/*
 * Browser joiner for the in-call avatar (issue #27, Phase 2b — D5 option A1).
 *
 * This is the one piece that cannot run server-side: ACS Call Automation has no
 * "join Teams meeting by URL" API, so a client-side ACS Calling SDK must join the
 * meeting first (as an anonymous interop guest — governed by the meeting lobby,
 * no Teams admin needed) and surface a ServerCallId. We then hand that id to the
 * Python server (POST /api/acs/call) which attaches the bidirectional audio
 * bridge via connect_call().
 *
 * SDK delivery (important): the ACS Calling SDK relies on web workers / wasm for
 * its media stack. Loaded as a plain ES module from a CDN (esm.sh) those workers
 * fail to initialise silently, leaving join() stuck in state "None" forever. The
 * Microsoft-supported no-bundler delivery is the UMD browser bundle, which inlines
 * its workers — so we vendor it locally (frontend/vendor/) and load it via a
 * <script> tag, keeping the repo's no-Node guardrail (no build step on the server).
 * Its two small externalised deps (@azure/communication-common, @azure/logger) are
 * loaded as ES modules and exposed as the globals the UMD factory expects.
 */
const CALLING_UMD = "/vendor/communication-calling-1.40.1.js";
const COMMON_ESM = "https://esm.sh/@azure/communication-common@2.3.1";
const LOGGER_ESM = "https://esm.sh/@azure/logger@1.1.4";

let _sdkPromise = null;
function loadCallingSdk() {
    if (_sdkPromise) return _sdkPromise;
    _sdkPromise = (async () => {
        const [common, logger] = await Promise.all([
            import(COMMON_ESM),
            import(LOGGER_ESM),
        ]);
        // The UMD factory reads these globals (l.communicationCommon, l.logger) at
        // evaluation time, so they must exist before the <script> runs.
        window.communicationCommon = common;
        window.logger = logger;
        await new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = CALLING_UMD;
            s.onload = resolve;
            s.onerror = () => reject(new Error(`failed to load ${CALLING_UMD}`));
            document.head.appendChild(s);
        });
        const sdk = window["azure-communication-calling"];
        if (!sdk || !sdk.CallClient) {
            throw new Error("ACS Calling SDK global not found after loading the UMD bundle");
        }
        console.log(`[acs-join] SDK loaded, apiVersion=${sdk.Call && sdk.Call.apiVersion ? sdk.Call.apiVersion : "?"}`);
        return {
            CallClient: sdk.CallClient,
            Features: sdk.Features,
            LocalAudioStream: sdk.LocalAudioStream,
            AzureCommunicationTokenCredential: common.AzureCommunicationTokenCredential,
        };
    })();
    return _sdkPromise;
}

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const joinBtn = $("joinBtn");
const leaveBtn = $("leaveBtn");
const linkEl = $("meetingLink");

let call = null;
let callAgent = null;

// ───────── browser-side media bridge (client-side audio path) ─────────
// Server-side Call Automation media streaming does not deliver Teams *meeting*
// audio, so we move the media through this browser leg instead: capture the
// meeting's remote audio -> our server WS -> Voice Live, and play Voice Live's
// spoken reply back out as this leg's outgoing call audio. 24 kHz mono PCM16
// end-to-end to match Voice Live (no resampling).
const MEDIA_SAMPLE_RATE = 24000;
let audioCtx = null;
let outboundDest = null;        // MediaStreamDestination -> the call's outgoing audio
let outboundLocalStream = null; // ACS LocalAudioStream wrapping outboundDest.stream
let mediaWs = null;
let captureNode = null;         // ScriptProcessor pulling remote audio -> WS
let captureSink = null;         // zero-gain sink so the capture node runs silently
const wiredRemoteTracks = new Set();
const primingAudioEls = [];     // muted <audio> elements priming remote tracks
let micStream = null;           // getUserMedia mic stream feeding the capture node
let captureFrames = 0;          // ScriptProcessor callbacks (diagnostics)
let captureMaxRms = 0;          // peak RMS since last stats report (diagnostics)
let playCursor = 0;             // scheduling cursor for outbound playback
let scheduledSources = [];      // active outbound buffer sources (for barge-in flush)

function setupOutboundAudio(LocalAudioStream) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: MEDIA_SAMPLE_RATE,
    });
    outboundDest = audioCtx.createMediaStreamDestination();
    // Nuru's synthesized speech is the call's *only* outgoing audio (no mic),
    // which also eliminates the laptop-mic echo we saw while testing.
    outboundLocalStream = new LocalAudioStream(outboundDest.stream);
    return outboundLocalStream;
}

function floatToPcm16(float32) {
    const out = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
        let s = Math.max(-1, Math.min(1, float32[i]));
        out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
}

function pcm16ToFloat(int16) {
    const out = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) out[i] = int16[i] / 0x8000;
    return out;
}

function openMediaSocket() {
    const wsUrl = `${location.origin.replace(/^http/, "ws")}/ws/acs/browser`;
    mediaWs = new WebSocket(wsUrl);
    mediaWs.binaryType = "arraybuffer";
    mediaWs.onopen = () => console.log("[acs-join] media WS open");
    mediaWs.onclose = () => console.log("[acs-join] media WS closed");
    mediaWs.onerror = (e) => console.warn("[acs-join] media WS error", e);
    mediaWs.onmessage = (ev) => {
        if (typeof ev.data === "string") {
            try {
                const msg = JSON.parse(ev.data);
                if (msg.type === "stop_playback") flushPlayback();
            } catch (_) { /* ignore */ }
            return;
        }
        playPcmChunk(new Int16Array(ev.data));
    };
}

// Voice Live PCM16 -> schedule into the outgoing call audio.
// A small jitter buffer (lead time) absorbs network/WS timing variance so the
// scheduled chunks play gap-free instead of underrunning into clicks/breakups.
const PLAYBACK_LEAD = 0.18; // seconds of cushion ahead of the play clock
function playPcmChunk(int16) {
    if (!audioCtx || !outboundDest) return;
    const f32 = pcm16ToFloat(int16);
    const buf = audioCtx.createBuffer(1, f32.length, MEDIA_SAMPLE_RATE);
    buf.copyToChannel(f32, 0);
    const node = audioCtx.createBufferSource();
    node.buffer = buf;
    node.connect(outboundDest);
    const now = audioCtx.currentTime;
    // If we've fallen behind (or this is the first chunk of a turn), rebuild the
    // cushion rather than scheduling right at "now", which would underrun.
    if (playCursor < now + 0.02) playCursor = now + PLAYBACK_LEAD;
    node.start(playCursor);
    playCursor += buf.duration;
    scheduledSources.push(node);
    node.onended = () => {
        const i = scheduledSources.indexOf(node);
        if (i >= 0) scheduledSources.splice(i, 1);
    };
}

// Barge-in: drop everything queued so Nuru stops mid-sentence when a human talks.
function flushPlayback() {
    for (const node of scheduledSources) {
        try { node.stop(); } catch (_) { /* already stopped */ }
    }
    scheduledSources = [];
    playCursor = audioCtx ? audioCtx.currentTime : 0;
}

// Capture the meeting's remote audio and stream PCM16 to the server.
function ensureCaptureNode() {
    if (captureNode) return;
    captureNode = audioCtx.createScriptProcessor(4096, 1, 1);
    captureNode.onaudioprocess = (e) => {
        if (!mediaWs || mediaWs.readyState !== WebSocket.OPEN) return;
        const samples = e.inputBuffer.getChannelData(0);
        // Track signal level so we can tell (from server logs) whether the
        // captured meeting audio is real or all-zero/silent.
        let sum = 0;
        for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
        const rms = Math.sqrt(sum / samples.length);
        if (rms > captureMaxRms) captureMaxRms = rms;
        captureFrames++;
        if (captureFrames % 25 === 0) {
            try {
                mediaWs.send(JSON.stringify({
                    type: "capture_stats",
                    frames: captureFrames,
                    maxRms: Number(captureMaxRms.toFixed(5)),
                    ctxRate: audioCtx ? audioCtx.sampleRate : 0,
                    remoteStreams: (call && call.remoteAudioStreams)
                        ? call.remoteAudioStreams.length : 0,
                    wiredTracks: wiredRemoteTracks.size,
                }));
            } catch (_) { /* ignore */ }
            captureMaxRms = 0;
        }
        const pcm = floatToPcm16(samples);
        mediaWs.send(pcm.buffer);
    };
    // A ScriptProcessor only runs while connected to the destination; route it
    // through a zero-gain node so it processes without playing remote audio
    // locally (ACS already renders the meeting audio for us).
    captureSink = audioCtx.createGain();
    captureSink.gain.value = 0;
    captureNode.connect(captureSink);
    captureSink.connect(audioCtx.destination);
}

function wireRemoteAudioStream(stream) {
    try {
        if (!stream || (stream.mediaStreamType && stream.mediaStreamType !== "Audio")) return;
        const track = typeof stream.getMediaStreamTrack === "function"
            ? stream.getMediaStreamTrack()
            : null;
        Promise.resolve(track).then((t) => {
            if (!t || wiredRemoteTracks.has(t.id)) return;
            wiredRemoteTracks.add(t.id);
            ensureCaptureNode();
            const ms = new MediaStream([t]);
            // Chrome/Edge only pull samples from a *remote* WebRTC MediaStream
            // through Web Audio if the stream is also consumed by a playing
            // HTMLMediaElement. Prime it with a muted <audio> element so the
            // ScriptProcessor actually receives the meeting audio.
            const el = new Audio();
            el.muted = true;
            el.srcObject = ms;
            el.play().catch(() => { /* autoplay may defer; track still primed */ });
            primingAudioEls.push(el);
            const src = audioCtx.createMediaStreamSource(ms);
            src.connect(captureNode);
            console.log("[acs-join] wired remote audio track", t.id);
            if (mediaWs && mediaWs.readyState === WebSocket.OPEN) {
                try { mediaWs.send(JSON.stringify({ type: "remote_wired", trackId: t.id })); } catch (_) {}
            }
        }).catch((e) => console.warn("[acs-join] getMediaStreamTrack failed", e));
    } catch (e) {
        console.warn("[acs-join] wireRemoteAudioStream failed", e);
    }
}

function startRemoteAudioCapture() {
    ensureCaptureNode();
    // PRIMARY capture path: the local microphone. ACS's Web SDK auto-renders
    // remote meeting audio and does NOT expose it as a raw MediaStreamTrack
    // (getMediaStreamTrack on RemoteAudioStream yields nothing — confirmed:
    // wiredTracks stayed 0, maxRms 0), so we cannot capture the mixed remote
    // audio in the browser. Instead we capture this device's mic — the
    // executive asking the question is at this laptop. echoCancellation strips
    // Nuru's own voice (played by the Teams client) from the captured signal.
    navigator.mediaDevices.getUserMedia({
        audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
        video: false,
    }).then((stream) => {
        micStream = stream;
        const src = audioCtx.createMediaStreamSource(stream);
        src.connect(captureNode);
        console.log("[acs-join] mic capture wired ->", stream.getAudioTracks().length, "track(s)");
        if (mediaWs && mediaWs.readyState === WebSocket.OPEN) {
            try { mediaWs.send(JSON.stringify({ type: "mic_wired", tracks: stream.getAudioTracks().length })); } catch (_) {}
        }
    }).catch((e) => {
        console.warn("[acs-join] mic capture failed", e);
        log(`Microphone capture failed: ${e.message || e}. Nuru can't hear questions.`);
    });
}

function teardownMedia() {
    try { if (mediaWs) mediaWs.close(); } catch (_) {}
    try { if (captureNode) captureNode.disconnect(); } catch (_) {}
    try { if (captureSink) captureSink.disconnect(); } catch (_) {}
    try { if (micStream) micStream.getTracks().forEach((t) => t.stop()); } catch (_) {}
    try { if (audioCtx) audioCtx.close(); } catch (_) {}
    for (const el of primingAudioEls) {
        try { el.pause(); el.srcObject = null; } catch (_) {}
    }
    primingAudioEls.length = 0;
    mediaWs = null; captureNode = null; captureSink = null; audioCtx = null; micStream = null;
    outboundDest = null; outboundLocalStream = null;
    wiredRemoteTracks.clear(); scheduledSources = []; playCursor = 0;
}

function log(msg) {
    console.log("[acs-join]", msg);
    statusEl.textContent = msg;
}

// Surface any error the SDK swallows inside its async media/connect pipeline —
// the "stuck at state None with no error" symptom is exactly what these catch.
window.addEventListener("error", (e) => {
    console.error("[acs-join] window error:", e.message, e.error || "");
});
window.addEventListener("unhandledrejection", (e) => {
    console.error("[acs-join] unhandled rejection:", e.reason);
});

async function ensureEnabled() {
    const res = await fetch("/api/acs/config");
    const cfg = await res.json();
    if (!cfg.enabled) {
        log("ACS in-call media is not enabled on this deployment (set ACS_CONNECTION_STRING / ACS_ENDPOINT and ENABLE_ACS=true).");
        joinBtn.disabled = true;
        return false;
    }
    return true;
}

// Teams exposes two join-link shapes and ACS needs a different locator for each:
//   - classic: https://teams.microsoft.com/l/meetup-join/19%3ameeting_...  -> { meetingLink }
//   - new:     https://teams.microsoft.com/meet/<id>?p=<passcode>          -> { meetingId, passcode }
// The new short links are NOT accepted by the meetingLink locator, so detect and
// translate them to a TeamsMeetingIdLocator. Returns the locator object to pass to join().
function buildMeetingLocator(raw) {
    const link = (raw || "").trim();
    if (link.includes("/l/meetup-join/")) {
        return { meetingLink: link };
    }
    // New "/meet/<id>" links (with the passcode in ?p=).
    const meetMatch = link.match(/\/meet\/([^/?#]+)/i);
    if (meetMatch) {
        const meetingId = decodeURIComponent(meetMatch[1]);
        let passcode = "";
        try { passcode = new URL(link).searchParams.get("p") || ""; } catch (e) { /* ignore */ }
        return passcode ? { meetingId, passcode } : { meetingId };
    }
    // Bare numeric meeting id (passcode would have to be appended as ?p=...).
    if (/^\d{6,}$/.test(link)) {
        return { meetingId: link };
    }
    // Last resort: hand it over as a meetingLink and let ACS validate it.
    return { meetingLink: link };
}

function describeEndReason(r) {
    // Common ACS interop subCodes seen when joining Teams meetings.
    const map = {
        5854: "The meeting link/id was rejected by the service (wrong format or expired).",
        5300: "Join was rejected — the tenant may block anonymous/interop participants (tenant policy).",
        5000: "Removed from the call.",
        10037: "Could not be admitted from the lobby (timed out or declined).",
    };
    if (r && map[r.subCode]) return map[r.subCode];
    if (r && r.code === 0) return "Normal hang up.";
    return "See the browser console for the full error.";
}

async function join() {
    const meetingLink = linkEl.value.trim();
    if (!meetingLink) { log("Paste a Teams meeting join link first."); return; }
    // Guard: only ever hand ACS something that actually looks like a Teams meeting
    // reference. (Defends against the input being polluted with status text or
    // other stray content, which otherwise produces a cryptic "Join failed".)
    const looksLikeMeeting =
        /teams\.microsoft\.com/i.test(meetingLink) ||
        /meetup-join/i.test(meetingLink) ||
        /\/meet\//i.test(meetingLink) ||
        /^\d{6,}$/.test(meetingLink);
    if (!looksLikeMeeting) {
        log("That doesn't look like a Teams meeting link. Clear the box and paste the full join link from the meeting invite (it contains \"teams.microsoft.com\").");
        return;
    }
    joinBtn.disabled = true;

    try {
        log("Requesting an ACS access token…");
        const tokRes = await fetch("/api/acs/token", { method: "POST" });
        if (!tokRes.ok) throw new Error(`token endpoint returned ${tokRes.status}`);
        const { token } = await tokRes.json();

        log("Loading the ACS Calling SDK…");
        const { CallClient, Features, LocalAudioStream, AzureCommunicationTokenCredential } = await loadCallingSdk();

        log("Initialising the ACS Calling SDK…");
        const callClient = new CallClient();
        const credential = new AzureCommunicationTokenCredential(token);

        // Build Nuru's outgoing audio from synthesized speech (not a mic), so she
        // can speak into the meeting and there is no laptop-mic echo. This is the
        // call's local audio stream, passed into join() below.
        const localAudio = setupOutboundAudio(LocalAudioStream);
        try { await audioCtx.resume(); } catch (_) { /* resumes on first gesture */ }

        // Initialise the device manager (some SDK builds require it before join).
        // Mic permission is best-effort only — we send synthesized audio, not mic.
        try {
            const deviceManager = await callClient.getDeviceManager();
            await deviceManager.askDevicePermission({ audio: true, video: false });
        } catch (permErr) {
            console.warn("[acs-join] device permission (non-fatal):", permErr);
        }

        callAgent = await callClient.createCallAgent(credential, {
            displayName: "Nuru (AI assistant)",
        });

        const locator = buildMeetingLocator(meetingLink);
        log(`Joining the Teams meeting via ${locator.meetingId ? "meeting id" : "meeting link"} (you may wait in the lobby until admitted)…`);
        console.log("[acs-join] locator", locator);
        // Join with our synthesized audio as the local stream (no mic). Empty
        // options otherwise — passing a populated videoOptions stalls the join.
        call = callAgent.join(locator, {
            audioOptions: { localAudioStreams: [localAudio], muted: false },
        });
        console.log(`[acs-join] join() returned, call.id=${call && call.id}, state=${call && call.state}`);

        // Network + media user-facing diagnostics — these reveal ICE/connectivity or
        // device problems that otherwise leave the call silently stuck at "None".
        try {
            const getFeature = (call.feature || call.api).bind(call);
            const diag = getFeature(Features.UserFacingDiagnostics);
            diag.network.on("diagnosticChanged", (d) =>
                console.warn(`[acs-join] network diag: ${d.diagnostic}=${d.value} (${d.valueType})`));
            diag.media.on("diagnosticChanged", (d) =>
                console.warn(`[acs-join] media diag: ${d.diagnostic}=${d.value} (${d.valueType})`));
        } catch (diagErr) {
            console.warn("[acs-join] could not attach UserFacingDiagnostics:", diagErr);
        }

        leaveBtn.disabled = false;
        // Poll the call state for ~30s so we can see whether it's stuck in
        // "Connecting"/"InLobby" vs never leaving "None" (diagnostics for the spike).
        let polls = 0;
        const poller = setInterval(() => {
            polls += 1;
            console.log(`[acs-join] poll#${polls} call.state=${call && call.state}`);
            if (!call || polls > 15 || call.state === "Connected" || call.state === "Disconnected") {
                clearInterval(poller);
            }
        }, 2000);
        call.on("stateChanged", async () => {
            log(`Call state: ${call.state}`);
            if (call.state === "Connected") {
                await startBrowserMedia();
            }
            if (call.state === "Disconnected") {
                const r = call.callEndReason || {};
                log(`Call ended (code ${r.code ?? "?"}, subCode ${r.subCode ?? "?"}). ${describeEndReason(r)}`);
                leaveBtn.disabled = true;
                joinBtn.disabled = false;
            }
        });
    } catch (e) {
        log(`Join failed: ${e.message || e}`);
        joinBtn.disabled = false;
    }
}

async function startBrowserMedia() {
    try {
        log("Connected. Bridging meeting audio to Nuru…");
        openMediaSocket();
        startRemoteAudioCapture();
        // Stop the SDK from rendering the meeting's incoming audio out the local
        // speaker. We capture it for Voice Live via Web Audio (muted <audio>
        // priming keeps the WebRTC track flowing), so local rendering is pure
        // echo when Nuru's leg shares a device with the user's Teams client.
        setTimeout(async () => {
            try {
                if (call && typeof call.muteIncomingAudio === "function") {
                    await call.muteIncomingAudio();
                    console.log("[acs-join] incoming audio muted (echo guard)");
                    if (mediaWs && mediaWs.readyState === WebSocket.OPEN) {
                        try { mediaWs.send(JSON.stringify({ type: "incoming_muted" })); } catch (_) {}
                    }
                }
            } catch (e) {
                console.warn("[acs-join] muteIncomingAudio failed", e);
            }
        }, 1500);
        log("Nuru is live in the call. Ask a question aloud and she'll answer.");
    } catch (e) {
        log(`Media bridge failed: ${e.message || e}`);
    }
}

async function leave() {
    leaveBtn.disabled = true;
    try {
        if (call) await call.hangUp();
    } catch (e) {
        console.warn("hangUp error", e);
    }
    teardownMedia();
    call = null;
    log("Left the meeting.");
    joinBtn.disabled = false;
}

joinBtn.addEventListener("click", join);
leaveBtn.addEventListener("click", leave);

// The Companion control panel (companion.html, opened in a separate window so the
// ACS Calling leg runs OUTSIDE the Teams meeting webview) hands the meeting link
// over via ?meeting=. Prefill it so the user does not paste twice.
try {
    const prefill = new URLSearchParams(window.location.search).get("meeting");
    if (prefill) linkEl.value = prefill;
} catch (e) { /* ignore */ }

ensureEnabled();
