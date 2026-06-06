/**
 * Avatar Forge - Client-side JavaScript
 * Handles audio capture (AudioWorklet 24kHz PCM16), WebSocket communication,
 * WebRTC avatar video, and UI state management.
 */

// ===== State =====
let ws = null;
let audioContext = null;
let workletNode = null;
let mediaStream = null;
let playbackContext = null;
let playbackBufferQueue = [];
let nextPlaybackTime = 0;
let isConnected = false;
let isConnecting = false;
let isRecording = false;
let audioChunksSent = 0;
let isDeveloperMode = false;
let avatarEnabled = false;
// True from the moment connectSession() starts until the avatar video is ready
// (or the session ends). Lets us show the avatar frame + loading placeholder
// immediately instead of waiting for session_started, smoothing the startup.
let avatarConnecting = false;
let pendingAvatarEnabled = false;
let avatarLoadingHideTimer = null;
let micRevealTimer = null;
// Avatar "thinking" indicator: shown while the agent works (response_created ->
// first token) so the user isn't staring at a silent face during the 2-3s
// grounding gap. Purely visual — never touches the audio/avatar pipeline.
let thinkingShowTimer = null;
let thinkingRotateTimer = null;
let thinkingSlowTimer = null;
let thinkingMaxTimer = null;
let thinkingActive = false;
let thinkingCaptionIndex = 0;
// Monotonic generation token. Bumped every time a new turn arms the indicator,
// so a late event from a previous (e.g. cancelled) response can't show or
// cancel the current turn's pill.
let thinkingGen = 0;
const THINKING_CAPTIONS = [
    'Looking through the records…',
    'Checking the latest information…',
    'Pulling the details together…',
];
const THINKING_SLOW_CAPTION = 'Just a moment — getting you a reliable answer…';
const THINKING_SHOW_DELAY_MS = 700;
const THINKING_ROTATE_MS = 2200;
const THINKING_SLOW_MS = 3500;
// Hard ceiling so the pill can never get stuck if response_done never arrives.
const THINKING_MAX_MS = 25000;
let peerConnection = null;
let avatarVideoElement = null;
let isSpeaking = false;
let avatarOutputMode = 'webrtc';
let cachedIceServers = null;
let peerConnectionQueue = [];

// Volume animation state
let analyserNode = null;
let analyserDataArray = null;
let micAnalyserNode = null;
let micAnalyserDataArray = null;
let recordAnimationFrameId = null;
let playChunkAnimationFrameId = null;

// WebSocket video playback (MediaSource Extensions)
let mediaSource = null;
let sourceBuffer = null;
let videoChunksQueue = [];
let pendingWsVideoElement = null;

const clientId = 'client-' + Math.random().toString(36).substr(2, 9);

// ===== DOM Ready =====
document.addEventListener('DOMContentLoaded', () => {
    setupUIBindings();
    updateConditionalFields();
    updateControlStates();
    fetchServerConfig();
    warmWebRTCEngine();
});

// Construct (and immediately close) a throwaway RTCPeerConnection at page load
// so the browser loads/initializes its WebRTC native code, codec backends, and
// JS-to-native bridges before the user clicks Connect. Real ICE candidates are
// NOT pre-gathered here (we don't have the Azure ICE servers yet, and using
// public STUN as a stand-in proved unreliable — see the note above
// preparePeerConnection). The only thing this saves is the first-time
// engine-warm cost, which is small but free.
function warmWebRTCEngine() {
    if (typeof RTCPeerConnection === 'undefined') return;
    try {
        const warm = new RTCPeerConnection({});
        // Closing immediately is fine — we just wanted the constructor to run.
        warm.close();
        console.log('[WebRTC] engine warmed at page load');
    } catch (e) {
        // Ignore — older browsers / restrictive policies. Not fatal.
    }
}

// ===== Server Config =====
function applyServerDefaults(defaults) {
    if (!defaults) return;
    for (const [id, val] of Object.entries(defaults)) {
        const el = document.getElementById(id);
        if (!el) continue;
        if (el.type === 'checkbox') {
            el.checked = !!val;
        } else {
            el.value = String(val);
        }
        // Notify listeners (range displays, conditional fields).
        el.dispatchEvent(new Event('change', { bubbles: true }));
        if (el.type === 'range') el.dispatchEvent(new Event('input', { bubbles: true }));
    }
}

async function fetchServerConfig() {
    try {
        const resp = await fetch('/api/config');
        const config = await resp.json();

        // Apply env-driven defaults to all matching controls.
        applyServerDefaults(config.defaults);

        // Back-compat: top-level voice (older /api/config shape).
        if (config.voice) {
            const v = document.getElementById('voiceName');
            if (v && !config.defaults?.voiceName) v.value = config.voice;
        }

        // Refresh any cascaded visibility now that values are in place.
        if (typeof updateConditionalFields === 'function') updateConditionalFields();

        // Sidebar is hidden by default in markup to avoid a flash on load.
        // Reveal it only when developer mode is on; otherwise leave it hidden
        // and tidy auxiliary dev-only controls, then auto-connect.
        if (config.developerMode === true) {
            const sidebar = document.getElementById('sidebar');
            if (sidebar) sidebar.hidden = false;
            const headerBar = document.getElementById('headerBar');
            if (headerBar) headerBar.hidden = false;
        } else {
            const devToggle = document.getElementById('developerMode');
            if (devToggle) {
                const label = devToggle.closest('label');
                if (label) label.style.display = 'none';
            }
            document.querySelectorAll('.mobile-menu').forEach(el => { el.style.display = 'none'; });
            const clearBtn = document.getElementById('clearChatBtn');
            if (clearBtn) clearBtn.style.display = 'none';
            try { await connectSession(); }
            catch (err) { console.error('Auto-connect failed', err); }
        }
    } catch (e) {
        console.log('No server config available, using defaults', e);
    }
}

// ===== UI Bindings =====
function setupUIBindings() {
    // Voice type change
    document.getElementById('voiceType').addEventListener('change', updateConditionalFields);
    // Voice name change
    document.getElementById('voiceName').addEventListener('change', updateConditionalFields);
    // Avatar enabled
    document.getElementById('avatarEnabled').addEventListener('change', updateConditionalFields);
    // Photo avatar
    document.getElementById('isPhotoAvatar').addEventListener('change', updateConditionalFields);
    // Custom avatar
    document.getElementById('isCustomAvatar').addEventListener('change', updateConditionalFields);
    // Developer mode
    document.getElementById('developerMode').addEventListener('change', (e) => {
        isDeveloperMode = e.target.checked;
        updateDeveloperModeLayout();
    });
    // Turn detection type
    document.getElementById('turnDetectionType').addEventListener('change', updateConditionalFields);
    // SR Model
    document.getElementById('srModel').addEventListener('change', updateConditionalFields);

    // Range sliders - display values
    setupRangeDisplay('temperature', 'tempValue', v => v);
    setupRangeDisplay('voiceTemperature', 'voiceTempValue', v => v);
    setupRangeDisplay('voiceSpeed', 'voiceSpeedValue', v => v + '%');
    setupRangeDisplay('sceneZoom', 'sceneZoomLabel', v => 'Zoom: ' + v + '%');
    setupRangeDisplay('scenePositionX', 'scenePositionXLabel', v => 'Position X: ' + v + '%');
    setupRangeDisplay('scenePositionY', 'scenePositionYLabel', v => 'Position Y: ' + v + '%');
    setupRangeDisplay('sceneRotationX', 'sceneRotationXLabel', v => 'Rotation X: ' + v + ' deg');
    setupRangeDisplay('sceneRotationY', 'sceneRotationYLabel', v => 'Rotation Y: ' + v + ' deg');
    setupRangeDisplay('sceneRotationZ', 'sceneRotationZLabel', v => 'Rotation Z: ' + v + ' deg');
    setupRangeDisplay('sceneAmplitude', 'sceneAmplitudeLabel', v => 'Amplitude: ' + v + '%');

    // Scene sliders: send real-time updates when connected
    const sceneSliders = ['sceneZoom', 'scenePositionX', 'scenePositionY',
        'sceneRotationX', 'sceneRotationY', 'sceneRotationZ', 'sceneAmplitude'];
    sceneSliders.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', throttledUpdateAvatarScene);
    });

    // Accordion behavior: only one settings group open at a time
    const settingsGroups = document.querySelectorAll('.sidebar .settings-group');
    settingsGroups.forEach(group => {
        group.addEventListener('toggle', () => {
            if (group.open) {
                settingsGroups.forEach(other => {
                    if (other !== group && other.open) {
                        other.removeAttribute('open');
                    }
                });
            }
        });
    });
}

function setupRangeDisplay(sliderId, displayId, formatter) {
    const slider = document.getElementById(sliderId);
    const display = document.getElementById(displayId);
    if (slider && display) {
        slider.addEventListener('input', () => {
            display.textContent = formatter(slider.value);
        });
    }
}

// ===== Photo Avatar Scene Update =====
let lastSceneUpdate = 0;
const SCENE_THROTTLE_MS = 50;

function throttledUpdateAvatarScene() {
    const now = Date.now();
    if (now - lastSceneUpdate < SCENE_THROTTLE_MS) return;
    lastSceneUpdate = now;
    updateAvatarScene();
}

function updateAvatarScene() {
    if (!isConnected || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (!document.getElementById('isPhotoAvatar')?.checked) return;
    if (!document.getElementById('avatarEnabled')?.checked) return;

    const isCustom = document.getElementById('isCustomAvatar')?.checked || false;
    const avatarName = isCustom
        ? document.getElementById('customAvatarName')?.value || ''
        : document.getElementById('photoAvatarName')?.value || 'Anika';
    const parts = avatarName.split('-');
    const character = parts[0].toLowerCase();
    const style = parts.slice(1).join('-') || undefined;

    const scene = {
        zoom: parseInt(document.getElementById('sceneZoom').value) / 100,
        position_x: parseInt(document.getElementById('scenePositionX').value) / 100,
        position_y: parseInt(document.getElementById('scenePositionY').value) / 100,
        rotation_x: parseInt(document.getElementById('sceneRotationX').value) * Math.PI / 180,
        rotation_y: parseInt(document.getElementById('sceneRotationY').value) * Math.PI / 180,
        rotation_z: parseInt(document.getElementById('sceneRotationZ').value) * Math.PI / 180,
        amplitude: parseInt(document.getElementById('sceneAmplitude').value) / 100,
    };

    const avatar = {
        type: 'photo-avatar',
        model: 'vasa-1',
        character: character,
        scene: scene,
    };
    if (isCustom) {
        avatar.customized = true;
    } else if (style) {
        avatar.style = style;
    }

    ws.send(JSON.stringify({
        type: 'update_scene',
        avatar: avatar,
    }));
}

// ===== Conditional Field Visibility =====
function updateConditionalFields() {
    const voiceType = document.getElementById('voiceType').value;
    const voiceName = document.getElementById('voiceName').value;
    const avatarEnabled = document.getElementById('avatarEnabled').checked;
    const isPhotoAvatar = document.getElementById('isPhotoAvatar').checked;
    const isCustomAvatar = document.getElementById('isCustomAvatar').checked;
    const turnDetectionType = document.getElementById('turnDetectionType').value;
    const srModel = document.getElementById('srModel').value;

    // Recognition language hidden for MAI Transcribe models (auto multilingual)
    show('recognitionLanguageField', !srModel.startsWith('mai-transcribe'));

    // Filler words (semantic VAD)
    show('fillerWordsField', turnDetectionType === 'azure_semantic_vad');

    // Voice type variants
    show('standardVoiceField', voiceType === 'standard');
    show('customVoiceFields', voiceType === 'custom');
    show('personalVoiceFields', voiceType === 'personal');

    // Voice temperature (DragonHD or personal voice)
    const isDragonHD = voiceName && voiceName.includes('DragonHD');
    const isPersonal = voiceType === 'personal';
    show('voiceTempField', isDragonHD || isPersonal);

    // Avatar settings
    show('avatarSettings', avatarEnabled);
    show('standardAvatarField', !isPhotoAvatar && !isCustomAvatar);
    show('photoAvatarField', isPhotoAvatar && !isCustomAvatar);
    show('customAvatarField', isCustomAvatar);
    show('photoAvatarSceneSettings', isPhotoAvatar);
}

function show(id, visible) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('hidden', !visible);
}

// ===== Sidebar Toggle (mobile) =====
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// ===== Chat =====
function addMessage(role, text, isDev = false) {
    if (isDev && !isDeveloperMode) return;
    const messagesEl = document.getElementById('messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${isDev ? 'dev' : role}`;

    if (!isDev) {
        const roleSpan = document.createElement('div');
        roleSpan.className = 'message-role';
        roleSpan.textContent = role === 'user' ? 'You' : role === 'assistant' ? 'Assistant' : 'System';
        msgDiv.appendChild(roleSpan);
    }

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = text;
    msgDiv.appendChild(contentDiv);

    messagesEl.appendChild(msgDiv);
    scrollChatToBottom();
    updateClearChatButton();

    if (role === 'system' && text) {
        let type = 'info';
        const lowerText = text.toLowerCase();
        if (lowerText.includes('error') || lowerText.includes('failed') || lowerText.includes('denied')) {
            type = 'error';
        } else if (lowerText.includes('dropped') || lowerText.includes('disconnected') || lowerText.includes('closed') || lowerText.includes('websocket error')) {
            type = 'warning';
        } else if (lowerText.includes('started') || lowerText.includes('success')) {
            type = 'success';
        }
        showToast(text, type);
    }

    return contentDiv;
}

function updateLastAssistantMessage(text) {
    const messages = document.querySelectorAll('.message.assistant .message-content');
    if (messages.length > 0) {
        messages[messages.length - 1].textContent = text;
        scrollChatToBottom();
    }
}

function scrollChatToBottom() {
    const chatArea = document.getElementById('chatArea');
    chatArea.scrollTop = chatArea.scrollHeight;
}

function clearChat() {
    const messages = document.getElementById('messages');
    if (messages.children.length === 0) return;
    messages.innerHTML = '';
    updateClearChatButton();
}

function updateClearChatButton() {
    const btn = document.getElementById('clearChatBtn');
    const messages = document.getElementById('messages');
    if (!btn || !messages) return;
    const hasMessages = messages.children.length > 0;
    btn.disabled = !hasMessages;
    btn.style.opacity = hasMessages ? '' : '0.5';
}

// ===== Gather Config =====
function gatherConfig() {
    const voiceType = document.getElementById('voiceType').value;
    const isPhotoAvatar = document.getElementById('isPhotoAvatar').checked;
    const isCustomAvatar = document.getElementById('isCustomAvatar').checked;

    const voiceSpeed = parseFloat(document.getElementById('voiceSpeed').value) / 100;

    const config = {
        voiceType: voiceType,
        voiceName: document.getElementById('voiceName').value,
        voiceSpeed: voiceSpeed,
        voiceTemperature: parseFloat(document.getElementById('voiceTemperature').value),
        voiceDeploymentId: document.getElementById('voiceDeploymentId').value,
        customVoiceName: document.getElementById('customVoiceName').value,
        personalVoiceName: document.getElementById('personalVoiceName').value,
        personalVoiceModel: document.getElementById('personalVoiceModel').value,
        avatarEnabled: document.getElementById('avatarEnabled').checked,
        isPhotoAvatar: isPhotoAvatar,
        isCustomAvatar: isCustomAvatar,
        avatarName: isCustomAvatar
            ? document.getElementById('customAvatarName').value
            : isPhotoAvatar
                ? document.getElementById('photoAvatarName').value
                : document.getElementById('avatarName').value,
        avatarOutputMode: document.getElementById('avatarOutputMode').value,
        avatarBackgroundImageUrl: document.getElementById('avatarBackgroundImageUrl').value,
        useNS: document.getElementById('useNS').checked,
        useEC: document.getElementById('useEC').checked,
        turnDetectionType: document.getElementById('turnDetectionType').value,
        turnDetectionSilenceMs: parseInt(document.getElementById('turnDetectionSilenceMs').value),
        enableBargeIn: document.getElementById('enableBargeIn').checked,
        removeFillerWords: document.getElementById('removeFillerWords').checked,
        srModel: document.getElementById('srModel').value,
        recognitionLanguage: document.getElementById('recognitionLanguage').value,
        eouDetectionType: document.getElementById('eouDetectionType').value,
        enableProactive: document.getElementById('enableProactive').checked,
    };

    // Photo avatar scene settings
    if (isPhotoAvatar) {
        config.photoScene = {
            zoom: parseInt(document.getElementById('sceneZoom').value),
            positionX: parseInt(document.getElementById('scenePositionX').value),
            positionY: parseInt(document.getElementById('scenePositionY').value),
            rotationX: parseInt(document.getElementById('sceneRotationX').value),
            rotationY: parseInt(document.getElementById('sceneRotationY').value),
            rotationZ: parseInt(document.getElementById('sceneRotationZ').value),
            amplitude: parseInt(document.getElementById('sceneAmplitude').value),
        };
    }

    return config;
}

// ===== Connection =====
async function toggleConnection() {
    if (isConnecting) return;
    if (isConnected) {
        await disconnect();
    } else {
        await connectSession();
    }
}

async function connectSession() {
    setConnecting(true);
    addMessage('system', 'Session started, click on the mic button to start conversation! debug id: connecting...');

    // Reveal the avatar frame + a branded loading placeholder right away (t=0)
    // so the final layout is in place immediately. Without this the screen stays
    // blank for ~3s (Voice Live handshake) and then the empty avatar box, name and
    // mic all pop in at once — the staggered reveal that feels awkward.
    const wantAvatar = document.getElementById('avatarEnabled')?.checked || false;
    avatarConnecting = wantAvatar;
    pendingAvatarEnabled = wantAvatar;
    if (wantAvatar) {
        // Pin the output mode now so the ice_servers handler (which arrives before
        // session_started) routes webrtc vs websocket correctly.
        avatarOutputMode = document.getElementById('avatarOutputMode')?.value || 'webrtc';
        const isPhotoAvatarSession = document.getElementById('isPhotoAvatar')?.checked || false;
        const avatarContainer = document.getElementById('avatarVideoContainer');
        if (avatarContainer) avatarContainer.classList.toggle('photo-avatar', isPhotoAvatarSession);
        showAvatarLoading('Connecting…');
        updateDeveloperModeLayout();
    }

    try {
        // Open WebSocket to Python backend
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${location.host}/ws/${clientId}`);
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
            const config = gatherConfig();
            ws.send(JSON.stringify({ type: 'start_session', config }));
        };

        ws.onmessage = (event) => {
            // Binary frames = raw PCM16 audio chunks (server→client hot path).
            if (event.data instanceof ArrayBuffer) {
                handleAudioBinary(event.data);
                return;
            }
            const msg = JSON.parse(event.data);
            handleServerMessage(msg);
        };

        ws.onerror = (err) => {
            console.error('WebSocket error', err);
            addMessage('system', 'WebSocket error');
            setConnecting(false);
        };

        ws.onclose = () => {
            console.log('WebSocket closed');
            if (isConnected) {
                addMessage('system', 'Disconnected');
            }
            handleDisconnect();
        };

    } catch (err) {
        console.error('Connection error', err);
        addMessage('system', 'Failed to connect: ' + err.message);
        setConnecting(false);
    }
}

async function disconnect() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'stop_session' }));
    }
    handleDisconnect();
}

function handleDisconnect() {
    isConnected = false;
    isConnecting = false;
    isRecording = false;
    audioChunksSent = 0;
    avatarEnabled = false;
    avatarConnecting = false;
    pendingAvatarEnabled = false;
    clearAvatarLoading();

    stopAudioCapture();
    stopAudioPlayback();
    cleanupWebRTC();
    cleanupWebSocketVideo();
    updateSoundWaveAnimation();

    // Prepare next peer connection for faster reconnection
    if (cachedIceServers) {
        preparePeerConnection(cachedIceServers);
    }

    if (ws) {
        try { ws.close(); } catch (e) {}
        ws = null;
    }

    const lbl = document.getElementById('avatarNameLabel');
    if (lbl) lbl.textContent = '';
    clearTimeout(micRevealTimer);
    micRevealTimer = null;
    stopThinking();
    document.getElementById('recordContainer')?.classList.add('hidden');

    updateConnectionUI();
    updateDeveloperModeLayout();
}

// ===== Handle Server Messages =====
function handleServerMessage(msg) {
    const type = msg.type;

    switch (type) {
        case 'session_started':
            onSessionStarted(msg);
            break;
        case 'session_error':
            addMessage('system', 'Error: ' + (msg.error || 'Unknown error'));
            setConnecting(false);
            avatarConnecting = false;
            pendingAvatarEnabled = false;
            clearAvatarLoading();
            updateDeveloperModeLayout();
            break;
        case 'ice_servers':
            // Only setup WebRTC when avatar output mode is webrtc
            if (avatarOutputMode === 'webrtc') {
                setupWebRTC(msg.iceServers);
            }
            break;
        case 'avatar_sdp_answer':
            handleAvatarSdpAnswer(msg.serverSdp);
            break;
        case 'audio_data':
            handleAudioDelta(msg.data);
            break;
        case 'transcript_done':
            if (msg.role === 'user') {
                // Update existing placeholder by itemId, or add new message
                const itemId = msg.itemId;
                if (itemId) {
                    const existing = document.querySelector(`.message.user[data-item-id="${itemId}"] .message-content`);
                    if (existing) {
                        existing.textContent = msg.transcript;
                        scrollChatToBottom();
                        break;
                    }
                }
                addMessage('user', msg.transcript);
            } else if (msg.role === 'assistant') {
                // Finalize the streaming assistant message (don't create a new one)
                if (msg.transcript) {
                    const assistantMsgs = document.querySelectorAll('.message.assistant .message-content');
                    if (assistantMsgs.length > 0) {
                        assistantMsgs[assistantMsgs.length - 1].textContent = msg.transcript;
                    }
                    pendingAssistantText = '';
                }
            }
            break;
        case 'transcript_delta':
            if (msg.role === 'assistant') {
                onAssistantDelta(msg.delta);
            }
            break;
        case 'text_delta':
            onAssistantDelta(msg.delta);
            break;
        case 'text_done':
            // Text response complete - already accumulated via deltas
            break;
        case 'speech_started':
            onSpeechStarted(msg.itemId);
            break;
        case 'speech_stopped':
            onSpeechStopped();
            break;
        case 'transcript_empty':
            onTranscriptEmpty(msg.itemId);
            break;
        case 'response_created':
            pendingAssistantText = '';
            currentAssistantContentEl = null;
            addMessage('assistant', '');
            isSpeaking = true;
            startThinking();
            break;
        case 'response_done':
            isSpeaking = false;
            stopThinking();
            // Don't stop play-chunk animation here - the animation loop
            // will self-terminate when all buffered audio finishes playing
            break;
        case 'session_closed':
            addMessage('system', 'Session closed');
            handleDisconnect();
            break;
        case 'avatar_connecting':
            addMessage('system', 'Avatar connecting...');
            break;
        case 'video_data':
            handleVideoChunk(msg.delta);
            break;
        default:
            // Log unknown events in dev mode
            if (isDeveloperMode) {
                console.log('Unhandled:', type, msg);
            }
    }
}

let pendingAssistantText = '';
let currentAssistantContentEl = null;

function onAssistantDelta(text) {
    // First token of the answer has arrived — tear down the thinking indicator.
    if (thinkingActive || thinkingShowTimer) stopThinking();
    pendingAssistantText += text;
    // Cache the DOM ref instead of running querySelectorAll on every delta token.
    if (!currentAssistantContentEl || !currentAssistantContentEl.isConnected) {
        const messages = document.querySelectorAll('.message.assistant .message-content');
        currentAssistantContentEl = messages.length > 0 ? messages[messages.length - 1] : null;
    }
    if (currentAssistantContentEl) {
        currentAssistantContentEl.textContent = pendingAssistantText;
        scrollChatToBottom();
    } else {
        addMessage('assistant', pendingAssistantText);
        const messages = document.querySelectorAll('.message.assistant .message-content');
        currentAssistantContentEl = messages.length > 0 ? messages[messages.length - 1] : null;
    }
}

async function onSessionStarted(msg) {
    isConnected = true;
    isConnecting = false;
    updateConnectionUI();

    // Update the "connecting..." status message with the real session ID
    const sessionId = msg.sessionId || '';
    const statusMessages = document.querySelectorAll('.message.system .message-content');
    for (const el of statusMessages) {
        if (el.textContent.includes('debug id: connecting...')) {
            el.textContent = `Session started, click on the mic button to start conversation! debug id: ${sessionId || 'unknown'}`;
            break;
        }
    }

    // Show appropriate content area
    avatarEnabled = msg.config?.avatarEnabled || false;
    avatarOutputMode = msg.config?.avatarOutputMode || 'webrtc';
    const isPhotoAvatarSession = document.getElementById('isPhotoAvatar')?.checked || false;
    const avatarContainer = document.getElementById('avatarVideoContainer');
    if (avatarContainer) {
        avatarContainer.classList.toggle('photo-avatar', isPhotoAvatarSession);
    }
    const labelEl = document.getElementById('avatarNameLabel');
    if (labelEl && !avatarEnabled) {
        // Avatar name is revealed together with the video (see revealAvatarVideo);
        // keep it empty here so it doesn't pop in during the loading spinner.
        labelEl.textContent = '';
    }
    updateDeveloperModeLayout();

    // If avatar is enabled with websocket mode, set up MediaSource video playback
    if (avatarEnabled && avatarOutputMode === 'websocket') {
        setupWebSocketVideoPlayback(isPhotoAvatarSession);
    }

    // Reveal the mic. With an avatar, wait until the avatar video is on screen so
    // the controls don't pop in before the face does. A fallback timer guarantees
    // the mic still appears even if the video frame never fires (so a stalled
    // avatar can never permanently block the conversation).
    if (avatarEnabled && avatarConnecting) {
        clearTimeout(micRevealTimer);
        micRevealTimer = setTimeout(showMicControls, 8000);
    } else {
        showMicControls();
    }

    // Start audio capture but leave mic off by default
    await startAudioCapture();
    isRecording = false;
    stopRecordAnimation();
    resetVolumeCircle();
    updateMicUI();
}

function showMicControls() {
    clearTimeout(micRevealTimer);
    micRevealTimer = null;
    document.getElementById('recordContainer').classList.remove('hidden');
}

// ===== UI State =====
function setConnecting(connecting) {
    isConnecting = connecting;
    updateConnectionUI();
}

function updateConnectionUI() {
    const btn = document.getElementById('connectBtn');
    const text = document.getElementById('connectBtnText');

    btn.classList.remove('connected', 'connecting');
    if (isConnected) {
        btn.classList.add('connected');
        text.textContent = 'Disconnect';
    } else if (isConnecting) {
        btn.classList.add('connecting');
        text.textContent = 'Connecting...';
    } else {
        text.textContent = 'Connect';
    }

    // Disable connect button while connecting
    btn.disabled = isConnecting;

    // Scene Settings title: show "(Live Adjustable)" when connected
    const sceneTitle = document.getElementById('sceneSettingsTitle');
    if (sceneTitle) {
        sceneTitle.textContent = isConnected ? 'Scene Settings (Live Adjustable)' : 'Scene Settings';
    }

    // Update all control disabled states
    updateControlStates();

    // Mic buttons
    updateMicUI();
}

// ===== Control Enable/Disable States =====
// Controls that should be disabled when connected (locked during session)
const SETTINGS_CONTROLS = [
    // Conversation Settings
    'srModel', 'recognitionLanguage',
    'useNS', 'useEC', 'turnDetectionType', 'turnDetectionSilenceMs', 'enableBargeIn', 'removeFillerWords',
    'eouDetectionType', 'enableProactive',
    'voiceTemperature', 'voiceSpeed',
    // Voice Configuration
    'voiceType', 'voiceDeploymentId', 'customVoiceName',
    'personalVoiceName', 'personalVoiceModel', 'voiceName',
    // Avatar Configuration
    'avatarEnabled', 'isPhotoAvatar', 'avatarOutputMode',
    'isCustomAvatar', 'avatarName', 'photoAvatarName',
    'customAvatarName', 'avatarBackgroundImageUrl',
];

// Controls that should be disabled when NOT connected (chat interaction)
const CHAT_CONTROLS = [
    'textInput',
];

function updateControlStates() {
    // Disable all settings controls when connected
    for (const id of SETTINGS_CONTROLS) {
        const el = document.getElementById(id);
        if (el) el.disabled = isConnected;
    }

    // Disable chat controls when NOT connected
    for (const id of CHAT_CONTROLS) {
        const el = document.getElementById(id);
        if (el) el.disabled = !isConnected;
    }

    // Mic button (developer mode) - disabled when not connected
    const micBtn = document.getElementById('micBtn');
    if (micBtn) micBtn.disabled = !isConnected;

    // Send button - disabled when not connected
    const sendBtns = document.querySelectorAll('.send-btn');
    sendBtns.forEach(btn => btn.disabled = !isConnected);

    // Record button (non-developer mode footer) - disabled when not connected
    const recordBtn = document.getElementById('recordBtn');
    if (recordBtn) recordBtn.disabled = !isConnected;
}

// Whether the avatar panel (video frame + loading placeholder) should be shown.
// Once connected we trust the negotiated avatarEnabled; while still connecting we
// use the snapshot taken at connect time so the frame can appear at t=0.
function shouldShowAvatarPanel() {
    return isConnected ? avatarEnabled : (avatarConnecting && pendingAvatarEnabled);
}

// Set the avatar name label from the current config inputs (known client-side at
// connect time, so we don't have to wait for session_started to show it).
function setAvatarNameLabelFromConfig() {
    const labelEl = document.getElementById('avatarNameLabel');
    if (!labelEl) return;
    const isCustomA = document.getElementById('isCustomAvatar')?.checked;
    const isPhotoA = document.getElementById('isPhotoAvatar')?.checked;
    const rawName = isCustomA
        ? (document.getElementById('customAvatarName')?.value || '')
        : isPhotoA
            ? (document.getElementById('photoAvatarName')?.value || '')
            : (document.getElementById('avatarName')?.value || '');
    // Strip suffixes like '-business', '-casual-sitting' for a friendlier label.
    labelEl.textContent = rawName ? rawName.split('-')[0] : '';
}

function showAvatarLoading(text) {
    if (avatarLoadingHideTimer) { clearTimeout(avatarLoadingHideTimer); avatarLoadingHideTimer = null; }
    const el = document.getElementById('avatarLoading');
    if (!el) return;
    const t = document.getElementById('avatarLoadingText');
    if (t && text) t.textContent = text;
    el.classList.remove('hidden', 'fade-out');
}

// Fade the placeholder out smoothly, then remove it from layout once the CSS
// transition has finished.
function hideAvatarLoading() {
    const el = document.getElementById('avatarLoading');
    if (!el) return;
    el.classList.add('fade-out');
    if (avatarLoadingHideTimer) clearTimeout(avatarLoadingHideTimer);
    avatarLoadingHideTimer = setTimeout(() => {
        el.classList.add('hidden');
        avatarLoadingHideTimer = null;
    }, 500);
}

// Hide the placeholder immediately (no fade) and reset it — used on disconnect/error
// so a later reconnect starts from a clean state.
function clearAvatarLoading() {
    if (avatarLoadingHideTimer) { clearTimeout(avatarLoadingHideTimer); avatarLoadingHideTimer = null; }
    const el = document.getElementById('avatarLoading');
    if (el) el.classList.add('hidden');
    if (el) el.classList.remove('fade-out');
}

// Fade the live avatar video in and the placeholder out. Called on the first
// 'playing' event from the WebRTC or websocket video element.
function revealAvatarVideo(mediaPlayer) {
    if (mediaPlayer) mediaPlayer.classList.add('avatar-video-ready');
    avatarConnecting = false;
    setAvatarNameLabelFromConfig();
    hideAvatarLoading();
    showMicControls();
}

// ===== Avatar "thinking" indicator =====
// Scheduled on response_created; only actually shown if the first answer token
// hasn't arrived within THINKING_SHOW_DELAY_MS, so fast turns never flash it.
function startThinking() {
    stopThinking();
    // Suppress while the avatar itself is still loading in (greeting turn) or
    // when there's no avatar on screen.
    if (!(isConnected && avatarEnabled) || avatarConnecting) return;
    const gen = ++thinkingGen;
    thinkingCaptionIndex = 0;
    thinkingShowTimer = setTimeout(() => showThinking(gen), THINKING_SHOW_DELAY_MS);
}

function showThinking(gen) {
    thinkingShowTimer = null;
    // A newer turn (or a teardown) superseded this scheduled show — abort.
    if (gen !== thinkingGen) return;
    if (!(isConnected && avatarEnabled) || avatarConnecting) return;
    const el = document.getElementById('avatarThinking');
    if (!el) return;
    thinkingActive = true;
    setThinkingCaption(THINKING_CAPTIONS[0]);
    el.classList.add('visible');
    thinkingRotateTimer = setInterval(() => {
        thinkingCaptionIndex = (thinkingCaptionIndex + 1) % THINKING_CAPTIONS.length;
        setThinkingCaption(THINKING_CAPTIONS[thinkingCaptionIndex]);
    }, THINKING_ROTATE_MS);
    thinkingSlowTimer = setTimeout(() => {
        if (thinkingRotateTimer) { clearInterval(thinkingRotateTimer); thinkingRotateTimer = null; }
        setThinkingCaption(THINKING_SLOW_CAPTION);
    }, THINKING_SLOW_MS);
    // Failsafe: force-clear if the answer/response_done never arrives.
    thinkingMaxTimer = setTimeout(stopThinking, THINKING_MAX_MS);
}

function setThinkingCaption(text) {
    const t = document.getElementById('avatarThinkingText');
    if (t) t.textContent = text;
}

function stopThinking() {
    // Invalidate any in-flight scheduled show so it can't fire after teardown.
    thinkingGen++;
    if (thinkingShowTimer) { clearTimeout(thinkingShowTimer); thinkingShowTimer = null; }
    if (thinkingRotateTimer) { clearInterval(thinkingRotateTimer); thinkingRotateTimer = null; }
    if (thinkingSlowTimer) { clearTimeout(thinkingSlowTimer); thinkingSlowTimer = null; }
    if (thinkingMaxTimer) { clearTimeout(thinkingMaxTimer); thinkingMaxTimer = null; }
    thinkingActive = false;
    const el = document.getElementById('avatarThinking');
    if (el) el.classList.remove('visible');
}

function updateDeveloperModeLayout() {
    const contentArea = document.getElementById('contentArea');
    const avatarVideoContainer = document.getElementById('avatarVideoContainer');
    const volumeAnimation = document.getElementById('volumeAnimation');
    const chatArea = document.getElementById('chatArea');
    const inputArea = document.getElementById('inputArea');
    const footerArea = document.getElementById('footerArea');

    const hide = (el, h) => el.classList.toggle('hidden', h);
    const showAvatar = shouldShowAvatarPanel();

    if (isDeveloperMode) {
        // Developer mode: show input area, hide footer
        hide(inputArea, false);
        hide(footerArea, true);

        if (showAvatar) {
            // Avatar (connecting or connected) + developer: side-by-side (avatar + chat)
            contentArea.classList.add('developer-layout');
            hide(avatarVideoContainer, false);
            hide(chatArea, false);
            hide(volumeAnimation, true);
        } else if (isConnected) {
            // No avatar + developer: side-by-side layout (robot + chat)
            contentArea.classList.add('developer-layout');
            hide(avatarVideoContainer, true);
            hide(chatArea, false);
            hide(volumeAnimation, false);
        } else {
            // Not connected: just show chat
            contentArea.classList.remove('developer-layout');
            hide(avatarVideoContainer, true);
            hide(chatArea, false);
            hide(volumeAnimation, true);
        }
    } else {
        // Normal mode: show footer, hide input area
        hide(inputArea, true);
        hide(footerArea, false);
        contentArea.classList.remove('developer-layout');

        if (showAvatar) {
            // Avatar (connecting or connected) + normal: only avatar video, no chat
            hide(avatarVideoContainer, false);
            hide(chatArea, true);
            hide(volumeAnimation, true);
        } else if (isConnected) {
            // No avatar + normal: only robot, no chat
            hide(avatarVideoContainer, true);
            hide(chatArea, true);
            hide(volumeAnimation, false);
        } else {
            // Not connected: show chat history
            hide(avatarVideoContainer, true);
            hide(chatArea, false);
            hide(volumeAnimation, true);
        }
    }
}

let soundWaveIntervalId = null;

function updateSoundWaveAnimation() {
    const leftWave = document.getElementById('soundWaveLeft');
    const rightWave = document.getElementById('soundWaveRight');

    if (isConnected && avatarEnabled && isRecording && !isDeveloperMode) {
        // Create sound wave bars if not already present
        if (leftWave && leftWave.children.length === 0) {
            for (let i = 0; i < 10; i++) {
                const bar = document.createElement('div');
                bar.className = 'bar';
                bar.id = `item-${i}`;
                bar.style.height = '2px';
                leftWave.appendChild(bar);
            }
        }
        if (rightWave && rightWave.children.length === 0) {
            for (let i = 10; i < 20; i++) {
                const bar = document.createElement('div');
                bar.className = 'bar';
                bar.id = `item-${i}`;
                bar.style.height = '2px';
                rightWave.appendChild(bar);
            }
        }
        // Start animation
        if (!soundWaveIntervalId) {
            soundWaveIntervalId = setInterval(() => {
                if (micAnalyserNode && micAnalyserDataArray) {
                    micAnalyserNode.getByteFrequencyData(micAnalyserDataArray);
                    for (let i = 0; i < 20; i++) {
                        const ele = document.getElementById(`item-${i}`);
                        if (ele) {
                            // Map the 20 bars to different frequency bins for a real spectral visualizer
                            const binIndex = Math.floor((micAnalyserDataArray.length / 40) * (i + 2)); // focus on speech frequencies
                            const binValue = micAnalyserDataArray[binIndex] || 0;
                            // Scale value (0-255) to height (2px - 50px)
                            const height = 2 + (binValue / 255) * 48;
                            ele.style.transition = 'height 0.08s ease';
                            ele.style.height = `${height}px`;
                        }
                    }
                } else {
                    for (let i = 0; i < 20; i++) {
                        const ele = document.getElementById(`item-${i}`);
                        if (ele) {
                            ele.style.height = '2px';
                        }
                    }
                }
            }, 80); // faster update rate for responsive spectral visualizer
        }
        if (leftWave) leftWave.style.display = '';
        if (rightWave) rightWave.style.display = '';
    } else {
        // Stop animation, hide waves
        if (soundWaveIntervalId) {
            clearInterval(soundWaveIntervalId);
            soundWaveIntervalId = null;
        }
        if (leftWave) leftWave.style.display = 'none';
        if (rightWave) rightWave.style.display = 'none';
    }
}
function updateMicUI() {
    const micBtn = document.getElementById('micBtn');
    const recordBtn = document.getElementById('recordBtn');

    // Toggle recording class
    if (micBtn) micBtn.classList.toggle('recording', isRecording);
    if (recordBtn) recordBtn.classList.toggle('recording', isRecording);

    // Toggle icon visibility: show off-icon when not recording, on-icon when recording
    document.querySelectorAll('.mic-off-icon').forEach(el => {
        el.classList.toggle('hidden', isRecording);
    });
    document.querySelectorAll('.mic-on-icon').forEach(el => {
        el.classList.toggle('hidden', !isRecording);
    });

    // Update label text
    const label = document.querySelector('.microphone-label');
    if (label) {
        label.textContent = isRecording ? 'Turn off microphone' : 'Turn on microphone';
    }

    // Update sound wave visibility
    updateSoundWaveAnimation();
}

// ===== Audio Capture (24kHz PCM16 via AudioWorklet) =====
async function startAudioCapture() {
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                sampleRate: 24000,
                echoCancellation: true,
                noiseSuppression: true,
            }
        });
        audioContext = new AudioContext({ sampleRate: 24000 });
        console.log('[Audio] AudioContext created, actual sampleRate:', audioContext.sampleRate);

        // Register AudioWorklet processor inline via Blob
        const processorCode = `
class PCM16Processor extends AudioWorkletProcessor {
    constructor() {
        super();
        // 40ms at 24kHz = 960 samples. Smaller buffers give tighter
        // barge-in/interruption latency than the previous 100ms.
        this.bufferSize = 960;
        this.buffer = new Float32Array(this.bufferSize);
        this.offset = 0;
    }
    process(inputs) {
        const input = inputs[0];
        if (!input || !input[0]) return true;
        const data = input[0];
        for (let i = 0; i < data.length; i++) {
            this.buffer[this.offset++] = data[i];
            if (this.offset >= this.bufferSize) {
                const pcm16 = new Int16Array(this.bufferSize);
                for (let j = 0; j < this.bufferSize; j++) {
                    const s = Math.max(-1, Math.min(1, this.buffer[j]));
                    pcm16[j] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
                this.buffer = new Float32Array(this.bufferSize);
                this.offset = 0;
            }
        }
        return true;
    }
}
registerProcessor('pcm16-processor', PCM16Processor);
`;
        const blob = new Blob([processorCode], { type: 'application/javascript' });
        const url = URL.createObjectURL(blob);
        await audioContext.audioWorklet.addModule(url);
        URL.revokeObjectURL(url);

        const source = audioContext.createMediaStreamSource(mediaStream);
        workletNode = new AudioWorkletNode(audioContext, 'pcm16-processor');

        // Create analyser for mic volume visualization
        const micAnalyser = audioContext.createAnalyser();
        micAnalyser.fftSize = 2048;
        micAnalyser.smoothingTimeConstant = 0.85;
        const micDataArray = new Uint8Array(micAnalyser.frequencyBinCount);

        workletNode.port.onmessage = (e) => {
            if (!isConnected || !isRecording || !ws || ws.readyState !== WebSocket.OPEN) return;

            // Stream mic audio continuously. We intentionally do NOT gate the
            // mic on playback state: doing so is fragile (a single missed
            // response_done leaves the gate stuck and drops the mic permanently,
            // breaking every turn after the first). Echo-driven false turns are
            // instead prevented by keeping client barge-in (stopAudioPlayback on
            // speech_started) and the server's interrupt_response in lock-step,
            // plus the browser's echo cancellation on the mic capture.
            audioChunksSent++;
            if (audioChunksSent <= 3 || audioChunksSent % 100 === 0) {
                console.log(`[Audio] Sending chunk #${audioChunksSent}, bytes=${e.data.byteLength}`);
            }
            // Send raw PCM16 bytes as a binary WebSocket frame — avoids
            // base64 + JSON wrap overhead (~45%) on every audio chunk.
            ws.send(e.data);
        };

        source.connect(workletNode);
        source.connect(micAnalyser);
        workletNode.connect(audioContext.destination);

        // Store mic analyser so volume animation can use it
        micAnalyserNode = micAnalyser;
        micAnalyserDataArray = micDataArray;
        analyserNode = micAnalyser;
        analyserDataArray = micDataArray;
        startVolumeAnimation('record');

        console.log('[Audio] Capture started (24kHz PCM16)');
    } catch (err) {
        console.error('Audio capture error', err);
        addMessage('system', 'Microphone access denied or not available');
    }
}

function stopAudioCapture() {
    stopRecordAnimation();
    micAnalyserNode = null;
    micAnalyserDataArray = null;
    if (workletNode) { try { workletNode.disconnect(); } catch (e) {} workletNode = null; }
    if (audioContext) { try { audioContext.close(); } catch (e) {} audioContext = null; }
    if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
    resetVolumeCircle();
}

// ===== Audio Playback (24kHz PCM16) =====
function handleAudioBinary(arrayBuffer) {
    // Fast path for raw binary audio frames (no base64 decode needed).
    _playAudioPCM16(arrayBuffer);
}

function handleAudioDelta(base64Data) {
    // Legacy base64-in-JSON path (kept for backward compat).
    if (!base64Data) return;
    _playAudioPCM16(base64ToArrayBuffer(base64Data));
}

function _playAudioPCM16(arrayBuffer) {
    if (!playbackContext) {
        playbackContext = new AudioContext({ sampleRate: 24000 });
        // Create analyser for volume visualization
        analyserNode = playbackContext.createAnalyser();
        analyserNode.fftSize = 2048;
        analyserNode.smoothingTimeConstant = 0.85;
        analyserDataArray = new Uint8Array(analyserNode.frequencyBinCount);
        analyserNode.connect(playbackContext.destination);
        nextPlaybackTime = 0;
    }
    const int16 = new Int16Array(arrayBuffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 32768;
    }
    const buffer = playbackContext.createBuffer(1, float32.length, 24000);
    buffer.getChannelData(0).set(float32);
    const source = playbackContext.createBufferSource();
    source.buffer = buffer;
    source.connect(analyserNode);

    const now = playbackContext.currentTime;
    if (nextPlaybackTime < now) nextPlaybackTime = now;
    source.start(nextPlaybackTime);
    nextPlaybackTime += buffer.duration;

    // Start playback volume animation (only if not already running)
    if (!playChunkAnimationFrameId) {
        startVolumeAnimation('play-chunk');
    }
}

function stopAudioPlayback() {
    stopPlayChunkAnimation();
    if (playbackContext) { try { playbackContext.close(); } catch (e) {} playbackContext = null; }
    playbackBufferQueue = [];
    nextPlaybackTime = 0;
    // Switch back to mic analyser if mic is on
    if (isRecording && micAnalyserNode) {
        analyserNode = micAnalyserNode;
        analyserDataArray = micAnalyserDataArray;
        startVolumeAnimation('record');
    } else {
        analyserNode = null;
        analyserDataArray = null;
        resetVolumeCircle();
    }
}

// ===== Volume Animation =====
function startVolumeAnimation(animationType) {
    if (animationType === 'record') {
        stopPlayChunkAnimation();
    } else {
        stopPlayChunkAnimation();
        stopRecordAnimation();
    }
    const isRecord = animationType === 'record';
    const calculateVolume = () => {
        if (analyserNode && analyserDataArray) {
            analyserNode.getByteFrequencyData(analyserDataArray);
            const volume = Array.from(analyserDataArray).reduce((acc, v) => acc + v, 0) / analyserDataArray.length;
            updateVolumeCircle(volume, animationType);
        }

        if (isRecord) {
            // Stop record animation if mic was turned off
            if (!isRecording) {
                recordAnimationFrameId = null;
                resetVolumeCircle();
                return;
            }
            recordAnimationFrameId = requestAnimationFrame(calculateVolume);
        } else {
            // For playback: self-terminate when response is done AND audio finished
            if (!isSpeaking && (!playbackContext || playbackContext.currentTime >= nextPlaybackTime + 0.3)) {
                playChunkAnimationFrameId = null;
                // Switch back to mic animation or reset
                if (isRecording && micAnalyserNode) {
                    analyserNode = micAnalyserNode;
                    analyserDataArray = micAnalyserDataArray;
                    startVolumeAnimation('record');
                } else {
                    analyserNode = null;
                    analyserDataArray = null;
                    resetVolumeCircle();
                }
                return;
            }
            playChunkAnimationFrameId = requestAnimationFrame(calculateVolume);
        }
    };
    calculateVolume();
}

function stopRecordAnimation() {
    if (recordAnimationFrameId) {
        cancelAnimationFrame(recordAnimationFrameId);
        recordAnimationFrameId = null;
    }
}

function stopPlayChunkAnimation() {
    if (playChunkAnimationFrameId) {
        cancelAnimationFrame(playChunkAnimationFrameId);
        playChunkAnimationFrameId = null;
    }
}

function stopVolumeAnimation() {
    stopRecordAnimation();
    stopPlayChunkAnimation();
}

function updateVolumeCircle(volume, animationType) {
    const circle = document.getElementById('volumeCircle');
    if (!circle) return;
    const minSize = 160;
    const size = minSize + volume;
    circle.style.backgroundColor = animationType === 'record' ? 'lightgray' : 'lightblue';
    circle.style.width = size + 'px';
    circle.style.height = size + 'px';
}

function resetVolumeCircle() {
    const circle = document.getElementById('volumeCircle');
    if (!circle) return;
    circle.style.width = '';
    circle.style.height = '';
    circle.style.backgroundColor = '';
}

// ===== WebSocket Video Playback (MediaSource Extensions) =====
function setupWebSocketVideoPlayback(isPhotoAvatar) {
    // Clean any existing video
    cleanupWebSocketVideo();
    const container = document.getElementById('avatarVideo');
    if (container) container.innerHTML = '';

    // Create video element
    const videoElement = document.createElement('video');
    videoElement.id = 'ws-video';
    videoElement.autoplay = true;
    videoElement.playsInline = true;

    if (isPhotoAvatar) {
        videoElement.style.borderRadius = '10%';
    }
    videoElement.style.width = 'auto';
    videoElement.style.height = isDeveloperMode ? 'auto' : '';
    videoElement.style.objectFit = 'cover';
    videoElement.style.display = 'block';

    videoElement.addEventListener('canplay', () => {
        videoElement.play().catch(e => console.error('Play error:', e));
    });
    videoElement.addEventListener('playing', () => revealAvatarVideo(videoElement));

    // fMP4 codec: H.264 video + AAC audio
    const FMP4_MIME_CODEC = 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"';

    if (!MediaSource.isTypeSupported(FMP4_MIME_CODEC)) {
        console.error('MediaSource fMP4 codec not supported');
        addMessage('system', 'WebSocket video playback not supported in this browser. Please use WebRTC mode.');
        return;
    }

    mediaSource = new MediaSource();
    videoElement.src = URL.createObjectURL(mediaSource);

    mediaSource.addEventListener('sourceopen', () => {
        try {
            if (mediaSource.readyState === 'open') {
                sourceBuffer = mediaSource.addSourceBuffer(FMP4_MIME_CODEC);
                sourceBuffer.addEventListener('updateend', () => {
                    processVideoChunkQueue();
                });
            }
        } catch (e) {
            console.error('Error creating SourceBuffer:', e);
        }
    });

    // Append to container
    if (container) {
        container.appendChild(videoElement);
    } else {
        pendingWsVideoElement = videoElement;
    }
}

let videoChunkCount = 0;

function handleVideoChunk(base64Data) {
    if (!base64Data) return;
    videoChunkCount++;
    if (videoChunkCount <= 5 || videoChunkCount % 100 === 0) {
        console.log(`[VIDEO] chunk #${videoChunkCount}, length=${base64Data.length}, mediaSource=${mediaSource?.readyState}, sourceBuffer=${!!sourceBuffer}`);
    }
    try {
        const binaryString = atob(base64Data);
        const arrayBuffer = new ArrayBuffer(binaryString.length);
        const bytes = new Uint8Array(arrayBuffer);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        videoChunksQueue.push(arrayBuffer);
        processVideoChunkQueue();
    } catch (e) {
        console.error('Error handling video chunk:', e);
    }
}

function processVideoChunkQueue() {
    if (!sourceBuffer || sourceBuffer.updating || !mediaSource || mediaSource.readyState !== 'open') {
        return;
    }
    const next = videoChunksQueue.shift();
    if (!next) return;
    try {
        sourceBuffer.appendBuffer(next);
    } catch (e) {
        console.error('Error appending video chunk:', e);
    }
}

function cleanupWebSocketVideo() {
    videoChunksQueue = [];
    if (sourceBuffer && mediaSource) {
        try {
            if (mediaSource.readyState === 'open' && !sourceBuffer.updating) {
                mediaSource.endOfStream();
            }
        } catch (e) {
            console.error('Error ending MediaSource stream:', e);
        }
    }
    sourceBuffer = null;
    mediaSource = null;
    pendingWsVideoElement = null;
}

// ===== WebRTC for Avatar =====

// Prepare a peer connection ahead of time so ICE candidates are pre-gathered.
// This avoids the ICE gathering delay when the user starts a new session.
function preparePeerConnection(iceServers) {
    const iceConfig = iceServers.map(s => ({
        urls: s.urls,
        username: s.username || undefined,
        credential: s.credential || undefined,
    }));

    // iceCandidatePoolSize pre-allocates ICE candidates against each STUN/TURN server
    // at PC construction time, so by the time we createOffer() the candidates are
    // mostly gathered. Cuts cold-start gathering latency.
    const pc = new RTCPeerConnection({ iceServers: iceConfig, iceCandidatePoolSize: 4 });
    attachAvatarConnectionMonitor(pc);
    let iceGatheringDone = false;

    // Handle incoming tracks (video and audio)
    pc.ontrack = (event) => {
        const container = document.getElementById('avatarVideo');
        const mediaPlayer = document.createElement(event.track.kind);
        mediaPlayer.id = event.track.kind;
        mediaPlayer.srcObject = event.streams[0];
        // autoplay=true starts the stream as soon as the first frame decodes (no
        // loadeddata round-trip). playsInline keeps it inline on iOS Safari instead
        // of forcing fullscreen.
        mediaPlayer.autoplay = true;
        mediaPlayer.playsInline = true;
        if (container) container.appendChild(mediaPlayer);
        if (event.track.kind === 'video') {
            avatarVideoElement = mediaPlayer;
            mediaPlayer.onplaying = () => revealAvatarVideo(mediaPlayer);
        }
    };

    pc.onicegatheringstatechange = () => {
        if (pc.iceGatheringState === 'complete') {
            // ICE gathering complete
        }
    };

    pc.onicecandidate = (event) => {
        if (!event.candidate && !iceGatheringDone) {
            iceGatheringDone = true;
            peerConnectionQueue.push(pc);
            console.log('[' + new Date().toISOString() + '] ICE gathering done, new peer connection prepared.');
            // Keep only the latest prepared connection
            if (peerConnectionQueue.length > 1) {
                const old = peerConnectionQueue.shift();
                try { old.close(); } catch (e) {}
            }
        }
    };

    // Add transceivers for video and audio
    pc.addTransceiver('video', { direction: 'sendrecv' });
    pc.addTransceiver('audio', { direction: 'sendrecv' });

    // Listen for data channel events
    pc.addEventListener('datachannel', (event) => {
        const dataChannel = event.channel;
        dataChannel.onmessage = (e) => {
            console.log('[' + new Date().toISOString() + '] WebRTC event received: ' + e.data);
        };
        dataChannel.onclose = () => {
            console.log('Data channel closed');
        };
    });
    pc.createDataChannel('eventChannel');

    pc.createOffer().then(offer => {
        return pc.setLocalDescription(offer);
    }).then(() => {
        // Timeout fallback: if ICE gathering hasn't completed after 1.5 seconds,
        // push anyway. This is the disconnect-time prewarm; same logic as the
        // first-connect path — see setupWebRTC() for rationale.
        setTimeout(() => {
            if (!iceGatheringDone) {
                iceGatheringDone = true;
                peerConnectionQueue.push(pc);
                console.log('[' + new Date().toISOString() + '] ICE gathering timed out, peer connection prepared with available candidates.');
                if (peerConnectionQueue.length > 1) {
                    const old = peerConnectionQueue.shift();
                    try { old.close(); } catch (e) {}
                }
            }
        }, 1500);
    }).catch(err => {
        console.error('preparePeerConnection offer error', err);
    });
}

function setupWebRTC(iceServers) {
    if (peerConnection) cleanupWebRTC();

    // Cache ICE servers for future peer connection preparation
    cachedIceServers = iceServers;

    // Clear existing video container
    const container = document.getElementById('avatarVideo');
    if (container) container.innerHTML = '';

    if (peerConnectionQueue.length > 0) {
        // Use cached peer connection with pre-gathered ICE candidates
        peerConnection = peerConnectionQueue.shift();
        console.log('[' + new Date().toISOString() + '] Using cached peer connection with pre-gathered ICE candidates.');

        // Send SDP offer immediately (no need to wait for ICE gathering)
        const sdpJson = JSON.stringify(peerConnection.localDescription);
        const sdpBase64 = btoa(sdpJson);
        console.log('[SDP] Sending cached base64 SDP, starts with:', sdpBase64.substring(0, 40));
        ws.send(JSON.stringify({ type: 'avatar_sdp_offer', clientSdp: sdpBase64 }));
        console.log('[WebRTC] Cached SDP offer sent (base64)');

        // Prepare next peer connection for future use
        preparePeerConnection(iceServers);
        return;
    }

    // No cached peer connection available (first connection), create one from scratch
    const iceConfig = iceServers.map(s => ({
        urls: s.urls,
        username: s.username || undefined,
        credential: s.credential || undefined,
    }));

    // See preparePeerConnection() for why iceCandidatePoolSize is set.
    peerConnection = new RTCPeerConnection({ iceServers: iceConfig, iceCandidatePoolSize: 4 });
    attachAvatarConnectionMonitor(peerConnection);

    // Handle incoming tracks (video and audio)
    peerConnection.ontrack = (event) => {
        const mediaPlayer = document.createElement(event.track.kind);
        mediaPlayer.id = event.track.kind;
        mediaPlayer.srcObject = event.streams[0];
        // autoplay=true starts the stream as soon as the first frame decodes (no
        // loadeddata round-trip). playsInline keeps it inline on iOS Safari instead
        // of forcing fullscreen.
        mediaPlayer.autoplay = true;
        mediaPlayer.playsInline = true;
        if (container) container.appendChild(mediaPlayer);
        if (event.track.kind === 'video') {
            avatarVideoElement = mediaPlayer;
            mediaPlayer.onplaying = () => revealAvatarVideo(mediaPlayer);
        }
    };

    peerConnection.onicegatheringstatechange = () => {
        if (peerConnection.iceGatheringState === 'complete') {
            // ICE gathering complete
        }
    };

    let iceGatheringDone = false;
    peerConnection.onicecandidate = (event) => {
        if (!event.candidate && !iceGatheringDone) {
            iceGatheringDone = true;
            // ICE gathering complete, send SDP offer now
            const sdpJson = JSON.stringify(peerConnection.localDescription);
            const sdpBase64 = btoa(sdpJson);
            console.log('[SDP] Sending base64 SDP, starts with:', sdpBase64.substring(0, 40));
            ws.send(JSON.stringify({ type: 'avatar_sdp_offer', clientSdp: sdpBase64 }));
            console.log('[WebRTC] SDP offer sent (base64)');
        }
    };

    // Add transceivers for video and audio
    peerConnection.addTransceiver('video', { direction: 'sendrecv' });
    peerConnection.addTransceiver('audio', { direction: 'sendrecv' });

    // Listen for data channel events
    peerConnection.addEventListener('datachannel', (event) => {
        const dataChannel = event.channel;
        dataChannel.onmessage = (e) => {
            console.log('[' + new Date().toISOString() + '] WebRTC event received: ' + e.data);
        };
        dataChannel.onclose = () => {
            console.log('Data channel closed');
        };
    });
    peerConnection.createDataChannel('eventChannel');

    peerConnection.createOffer().then(offer => {
        return peerConnection.setLocalDescription(offer);
    }).then(() => {
        // Timeout fallback: send SDP after 1.5s if ICE gathering hasn't completed.
        // We send whatever candidates we have rather than waiting longer — if 1.5s
        // wasn't enough to reach any TURN server, 2.5s usually isn't either and we
        // were just adding visible Connect latency.
        setTimeout(() => {
            if (!iceGatheringDone) {
                iceGatheringDone = true;
                const sdpJson = JSON.stringify(peerConnection.localDescription);
                const sdpBase64 = btoa(sdpJson);
                console.log('[SDP] Sending base64 SDP (timeout), starts with:', sdpBase64.substring(0, 40));
                ws.send(JSON.stringify({ type: 'avatar_sdp_offer', clientSdp: sdpBase64 }));
                console.log('[WebRTC] SDP offer sent after timeout (base64)');
            }
        }, 1500);
    }).catch(err => {
        console.error('WebRTC offer error', err);
        addMessage('system', 'WebRTC setup failed');
    });

    // Prepare a peer connection for future use
    preparePeerConnection(iceServers);
}

function handleAvatarSdpAnswer(serverSdpBase64) {
    if (!peerConnection || !serverSdpBase64) return;
    try {
        // Server SDP is base64-encoded JSON: {"type":"answer","sdp":"..."}
        const serverSdpJson = atob(serverSdpBase64);
        const serverSdpObj = JSON.parse(serverSdpJson);
        peerConnection.setRemoteDescription(new RTCSessionDescription(serverSdpObj)).then(() => {
            console.log('[WebRTC] Remote SDP set');
        }).catch(err => {
            // A failed setRemoteDescription leaves the avatar permanently frozen
            // with no video. Surface it instead of swallowing it silently.
            console.error('SDP answer error', err);
            addMessage('system', 'The avatar could not start (connection negotiation failed). You can keep talking with audio, or reconnect to retry the avatar.');
        });
    } catch (e) {
        console.error('Failed to parse server SDP', e);
        addMessage('system', 'The avatar could not start (invalid connection answer). You can keep talking with audio, or reconnect to retry the avatar.');
    }
}

// Watch the peer connection and surface a terminal WebRTC failure to the user.
// Without this, a dropped/failed ICE negotiation just leaves the avatar frozen
// with no feedback ("sometimes unresponsive"). Attached to every PC we build.
function attachAvatarConnectionMonitor(pc) {
    if (!pc || pc._avatarMonitorAttached) return;
    pc._avatarMonitorAttached = true;
    let notified = false;
    const onFail = (label) => {
        if (notified) return;
        notified = true;
        console.error('[WebRTC] avatar connection ' + label);
        addMessage('system', 'The avatar connection dropped. You can keep talking with audio, or reconnect to retry the avatar.');
    };
    pc.addEventListener('iceconnectionstatechange', () => {
        if (pc.iceConnectionState === 'failed') onFail('iceConnectionState=failed');
    });
    pc.addEventListener('connectionstatechange', () => {
        if (pc.connectionState === 'failed') onFail('connectionState=failed');
    });
}

function cleanupWebRTC() {
    if (peerConnection) {
        try { peerConnection.close(); } catch (e) {}
        peerConnection = null;
    }
    if (avatarVideoElement) {
        avatarVideoElement.srcObject = null;
        avatarVideoElement = null;
    }
    const container = document.getElementById('avatarVideo');
    if (container) container.innerHTML = '';
}

// ===== Mic Toggle =====
function toggleMicrophone() {
    if (!isConnected) return;
    isRecording = !isRecording;
    updateMicUI();
    // Start/stop volume animation based on mic state
    if (isRecording && micAnalyserNode) {
        analyserNode = micAnalyserNode;
        analyserDataArray = micAnalyserDataArray;
        startVolumeAnimation('record');
    } else if (!isRecording) {
        stopRecordAnimation();
        resetVolumeCircle();
    }
}

// ===== Send Text =====
function sendTextMessage() {
    const input = document.getElementById('textInput');
    const text = input.value.trim();
    if (!text || !isConnected || !ws) return;

    addMessage('user', text);
    ws.send(JSON.stringify({ type: 'send_text', text }));
    input.value = '';
}

// ===== Speech Events (sound wave animation) =====
function onSpeechStarted(itemId) {
    isSpeaking = true;
    console.log(`[Turn] speech_started item=${itemId} | mic chunks sent so far=${audioChunksSent} | isRecording=${isRecording}`);
    // NOTE: deliberately do NOT tear down the thinking pill here. A stray VAD
    // speech_started during the grounding gap would otherwise cancel a
    // legitimate pill. Real barge-in is handled by response_done (status
    // CANCELLED), which calls stopThinking().
    // Stop assistant audio playback (barge-in) in speech-only mode if barge-in is enabled
    const bargeInEnabled = document.getElementById('enableBargeIn')?.checked || false;
    if (bargeInEnabled) {
        stopAudioPlayback();
    }
    // Add user placeholder message (will be updated when transcription completes)
    if (itemId) {
        const contentDiv = addMessage('user', '...');
        if (contentDiv) {
            contentDiv.closest('.message').setAttribute('data-item-id', itemId);
        }
    }
}

function onSpeechStopped() {
    console.log(`[Turn] speech_stopped | mic chunks sent so far=${audioChunksSent}`);
    pendingAssistantText = '';
    isSpeaking = false;
}

// A speech segment produced no recognized words (empty transcript). Remove the
// dangling "..." placeholder so the user doesn't perceive it as the avatar
// going silent. Surface a brief toast so the failure is visible, not mysterious.
function onTranscriptEmpty(itemId) {
    console.warn(`[Turn] transcript EMPTY item=${itemId} | mic chunks sent so far=${audioChunksSent}`);
    if (itemId) {
        const msg = document.querySelector(`.message.user[data-item-id="${itemId}"]`);
        if (msg) msg.remove();
    }
    showToast("Didn't catch that — please try again.", 'warning', 2500);
}

// ===== Utilities =====
function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}

function base64ToArrayBuffer(base64) {
    const binary = atob(base64);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
}

// ===== Toast Notification System =====
function showToast(message, type = 'info', duration = 5000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    // Icon selection
    let icon = '';
    if (type === 'success') icon = '✅';
    else if (type === 'error') icon = '❌';
    else if (type === 'warning') icon = '⚠️';
    else icon = 'ℹ️';

    toast.innerHTML = `<span style="flex-shrink: 0;">${icon}</span> <span style="line-height: 1.4;">${message}</span>`;
    container.appendChild(toast);

    // Force reflow and add visible class
    setTimeout(() => toast.classList.add('visible'), 10);

    // Auto remove
    setTimeout(() => {
        toast.classList.remove('visible');
        setTimeout(() => toast.remove(), 400);
    }, duration);
}

// Note: we used to pre-warm an RTCPeerConnection against Google's public STUN
// at page-load time, on the theory that having ICE candidates pre-gathered
// would speed up the first Connect. In practice that hurt as often as it helped:
// the cached PC's local candidates are gathered against `stun.l.google.com`,
// but the real ICE servers returned by Voice Live usually include Azure TURN
// credentials the prewarm PC never saw — on restrictive NATs the cached PC
// then fails to find a usable path and we fall back to a longer ICE timeout.
//
// The reconnect path (cleanupWebRTC -> preparePeerConnection(cachedIceServers))
// still pre-warms a PC against the REAL Azure ICE servers right after disconnect,
// which is a real win and is kept.
