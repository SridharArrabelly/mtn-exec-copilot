"""Voice Live session handler.

Owns one Voice Live SDK connection per browser client. Bridges browser
WebSocket messages into SDK calls and relays SDK events back via send_message.
"""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.models import (
    AudioInputTranscriptionOptions,
    ClientEventSessionAvatarConnect,
    InputAudioFormat,
    InputTextContentPart,
    Modality,
    OutputAudioFormat,
    RequestSession,
    ServerEventType,
    SystemMessageItem,
    UserMessageItem,
)

from ..config import (
    AGENT_NAME,
    AGENT_PROJECT_NAME,
    PROJECT_ENDPOINT,
    ROUTER_API_VERSION,
    ROUTER_BASE_URL,
    ROUTER_MODE,
    ROUTER_MODEL,
)
from .builders import build_avatar_config, build_turn_detection, build_voice_config
from .catalog import get_meeting_catalog
from .event_handlers import handle_event
from .routing import LiveRouter

logger = logging.getLogger(__name__)

# Max conversation turns (user + assistant) kept for the pre-router planner.
# The planner only needs recent context to resolve relative references
# ("the last meeting", "the 2019 one") and clarify follow-ups.
_ROUTER_HISTORY_MAX = 8


# Override applied to the proactive opening response only.
#
# Why this is so directive: immediately after SESSION_UPDATED, the most
# recent thing in model context is the MEETINGS LIST system message the
# backend just injected, AND the agent system prompt explicitly tells the
# model "read MEETINGS LIST BEFORE thinking about tools". With both of
# those pulling toward "enumerate the meetings", a soft override like
# "say a short greeting, don't mention the catalogue" is not reliable:
# the model occasionally reads the list aloud as the opening utterance.
#
# Solution: pin the opening to a verbatim string. This removes all model
# freedom on the first turn (good for a deterministic, branded opener)
# without touching the agent's behaviour on any subsequent turn.
# Applied via response.create(additional_instructions=...).
PROACTIVE_GREETING_INSTRUCTIONS = (
    "This is the opening of the session and the user has not spoken yet. "
    "Output EXACTLY the following text and absolutely nothing else — no "
    "additions, no list of meetings, no date, no internal data: "
    '"Hello, I am Nuru, your executive assistant. '
    'I hope you are doing well today. How can I help you?"'
)


class VoiceSessionHandler:
    """Single Voice Live session bridged to one browser WebSocket client."""

    def __init__(
        self,
        client_id: str,
        endpoint: str,
        credential: Any,
        send_message: Callable,
        config: dict,
        send_binary: Optional[Callable] = None,
    ):
        self.client_id = client_id
        self.endpoint = endpoint
        self.credential = credential
        self.send_message = send_message
        self.send_binary = send_binary
        self.config = config

        self.connection = None
        self.is_running = False
        self._event_task: Optional[asyncio.Task] = None
        self._pending_proactive = False
        self._stopping = False
        self._audio_chunk_count = 0
        self._video_chunk_count = 0

        # Pre-router (shadow mode) state. Resolved once at session setup and
        # then immutable for the session. ``_router_mode`` is the EFFECTIVE
        # mode after gating (e.g. forced to "off" if credential is api-key).
        self._catalog: str = ""
        self._router: Optional[LiveRouter] = None
        self._router_mode: str = "off"
        self._router_history: list[dict] = []
        self._router_turn_seq = 0
        # Strong refs to fire-and-forget background tasks (e.g. shadow router)
        # so they aren't garbage-collected before completion.
        self._bg_tasks: set = set()

    def _spawn_bg(self, coro) -> None:
        """Schedule a fire-and-forget coroutine, keeping a strong reference."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def start(self):
        """Start the Voice Live session."""
        try:
            self.is_running = True
            agent_name = AGENT_NAME
            project_name = AGENT_PROJECT_NAME
            if not agent_name or not project_name:
                raise ValueError("AGENT_NAME and AGENT_PROJECT_NAME must be set in the environment")

            connect_kwargs = {
                "endpoint": self.endpoint,
                "credential": self.credential,
                "agent_config": {
                    "agent_name": agent_name,
                    "project_name": project_name,
                },
            }
            logger.info(f"Connecting to Voice Live with agent: {agent_name} (project={project_name})")

            async with connect(**connect_kwargs) as connection:
                self.connection = connection

                # Configure session
                await self._setup_session(connection)

                # Process events
                await self._process_events(connection)

        except asyncio.CancelledError:
            logger.info(f"Session cancelled for client {self.client_id}")
        except Exception as e:
            logger.error(f"Voice session error for {self.client_id}: {e}")
            await self.send_message({
                "type": "session_error",
                "error": str(e),
            })
        finally:
            self.is_running = False
            self.connection = None

    async def _setup_session(self, connection):
        """Configure the Voice Live session with avatar, voice, and other settings."""
        config = self.config

        # Kick off meeting catalogue fetch in parallel with session setup. The
        # Voice Live handshake itself takes ~1-2s so this fetch (~50-100ms on
        # cache miss, ~0 on hit) is almost always free.
        catalog_task = asyncio.create_task(get_meeting_catalog())

        # Build voice configuration
        voice_config = build_voice_config(config)

        # Build avatar configuration
        avatar_config = build_avatar_config(config)

        # Build turn detection
        turn_detection = build_turn_detection(config)

        # Build modalities (avatar is NOT a modality - it's configured via the avatar field)
        modalities = [Modality.TEXT, Modality.AUDIO]

        # Build SR options
        sr_model = config.get("srModel", "azure-speech")
        recognition_language = config.get("recognitionLanguage", "auto")
        input_audio_transcription = AudioInputTranscriptionOptions(
            model=sr_model,
            language=None if (sr_model == "mai-transcribe-1" or recognition_language == "auto")
            else recognition_language,
        )

        # Build noise/echo settings
        noise_reduction = None
        echo_cancellation = None
        if config.get("useNS", False):
            noise_reduction = {"type": "azure_deep_noise_suppression"}
        if config.get("useEC", False):
            echo_cancellation = {"type": "server_echo_cancellation"}

        # Instructions, tools, and temperature are owned by the Foundry agent.
        session_config = RequestSession(
            modalities=modalities,
            voice=voice_config,
            avatar=avatar_config,
            input_audio_format=InputAudioFormat.PCM16,
            output_audio_format=OutputAudioFormat.PCM16,
            input_audio_transcription=input_audio_transcription,
            turn_detection=turn_detection,
            input_audio_noise_reduction=noise_reduction,
            input_audio_echo_cancellation=echo_cancellation,
            tool_choice="auto",
        )

        logger.debug("[SEND] session.update")
        await connection.session.update(session=session_config)

        # Wait for SESSION_UPDATED
        session_updated = await self._wait_for_event(
            connection, {ServerEventType.SESSION_UPDATED}
        )
        if session_updated is None:
            raise ValueError("SESSION_UPDATED event not received")

        logger.info(f"Session configured for client {self.client_id}")

        # Inject the live meeting catalogue as a SYSTEM message so the
        # Foundry agent can answer first/last/count/listing questions
        # without searching, and can phrase precise content searches with
        # exact meeting dates. Wrapped in try/except so a fetch failure
        # never breaks the session — the agent prompt has a fallback path.
        try:
            catalog = await catalog_task
            if catalog:
                self._catalog = catalog
                catalog_item = SystemMessageItem(
                    content=[InputTextContentPart(text=catalog)]
                )
                await connection.conversation.item.create(item=catalog_item)
                logger.info(
                    f"Injected meeting catalogue ({len(catalog)} chars) "
                    f"for client {self.client_id}"
                )
            else:
                logger.info(
                    f"No meeting catalogue available; agent will fall back "
                    f"to direct search for client {self.client_id}"
                )
        except Exception as e:
            logger.warning(
                f"Failed to inject meeting catalogue for {self.client_id}: {e}"
            )

        # Resolve the pre-router mode (shadow / off) for this session. Catalogue
        # is now available, so the planner can resolve relative references.
        self._init_router()

        avatar_output_mode = config.get("avatarOutputMode", "webrtc")

        # If avatar is enabled with WebRTC mode, relay ICE servers info to browser
        if config.get("avatarEnabled", False) and avatar_output_mode == "webrtc":
            if hasattr(session_updated, "session") and session_updated.session:
                session_data = session_updated.session
                if hasattr(session_data, "avatar") and session_data.avatar:
                    avatar_data = session_data.avatar
                    if hasattr(avatar_data, "ice_servers") and avatar_data.ice_servers:
                        ice_servers = []
                        for server in avatar_data.ice_servers:
                            ice_server = {"urls": server.urls}
                            if server.username:
                                ice_server["username"] = server.username
                            if server.credential:
                                ice_server["credential"] = server.credential
                            ice_servers.append(ice_server)

                        await self.send_message({
                            "type": "ice_servers",
                            "iceServers": ice_servers,
                        })
                        logger.info(f"Sent ICE servers to client {self.client_id}")

        # Extract session ID if available
        session_id = None
        if hasattr(session_updated, "session") and session_updated.session:
            session_id = getattr(session_updated.session, "id", None)
        logger.info(f"Session ID: {session_id}")

        # Notify client session is ready
        await self.send_message({
            "type": "session_started",
            "status": "success",
            "sessionId": session_id,
            "config": {
                "agentName": AGENT_NAME,
                "agentProjectName": AGENT_PROJECT_NAME,
                "avatarEnabled": config.get("avatarEnabled", False),
                "avatarOutputMode": avatar_output_mode,
            },
        })

        # Proactive greeting logic depends on avatar mode:
        # - No avatar: send immediately
        # - Avatar + websocket: send immediately (no WebRTC handshake needed)
        # - Avatar + webrtc: defer until SESSION_AVATAR_CONNECTING event
        if not config.get("avatarEnabled", False):
            if config.get("enableProactive", True):
                try:
                    logger.info("[SEND] response.create (proactive greeting, no avatar)")
                    await connection.response.create(
                        additional_instructions=PROACTIVE_GREETING_INSTRUCTIONS
                    )
                    logger.info("Proactive greeting sent")
                except Exception as e:
                    logger.error(f"Failed to send proactive greeting: {e}")
        elif avatar_output_mode == "websocket":
            # WebSocket avatar mode: no WebRTC handshake, send greeting immediately
            if config.get("enableProactive", True):
                try:
                    logger.info("[SEND] response.create (proactive greeting, websocket avatar)")
                    await connection.response.create(
                        additional_instructions=PROACTIVE_GREETING_INSTRUCTIONS
                    )
                    logger.info("Proactive greeting sent (websocket avatar)")
                except Exception as e:
                    logger.error(f"Failed to send proactive greeting: {e}")
        else:
            # WebRTC avatar: defer proactive greeting until avatar connect
            self._pending_proactive = config.get("enableProactive", True)

    # ------------------------------------------------------------------
    # Pre-router (shadow mode)
    # ------------------------------------------------------------------
    def _init_router(self) -> None:
        """Resolve and build the pre-router for this session (once).

        Sets ``self._router_mode`` to the EFFECTIVE mode and ``self._router``
        to a ``LiveRouter`` when enabled. Any misconfiguration disables the
        router (mode "off") rather than failing the session.
        """
        mode = ROUTER_MODE
        if mode not in ("off", "shadow", "active"):
            logger.warning("Router: unknown ROUTER_MODE=%r; treating as off", mode)
            mode = "off"

        if mode == "off":
            self._router_mode = "off"
            logger.info("Router: disabled (ROUTER_MODE=off) for client %s", self.client_id)
            return

        # Active mode is not wired into response creation yet — run it as
        # shadow so we still collect data without risking the live flow.
        if mode == "active":
            logger.warning(
                "Router: ROUTER_MODE=active not yet wired to response creation; "
                "running in shadow mode for client %s", self.client_id,
            )
            mode = "shadow"

        # The planner needs a token credential to mint a cognitiveservices
        # token for the model-inference endpoint. API-key auth can't.
        if not hasattr(self.credential, "get_token"):
            self._router_mode = "off"
            logger.warning(
                "Router: disabled — credential has no get_token (api-key auth) "
                "for client %s", self.client_id,
            )
            return

        try:
            self._router = LiveRouter(
                self.credential,
                model=ROUTER_MODEL,
                project_endpoint=PROJECT_ENDPOINT,
                base_url=ROUTER_BASE_URL,
                api_version=ROUTER_API_VERSION,
            )
        except Exception as e:
            self._router = None
            self._router_mode = "off"
            logger.warning("Router: disabled — failed to build LiveRouter: %s", e)
            return

        self._router_mode = mode
        logger.info(
            "Router: %s mode active for client %s (model=%s @ %s)",
            mode, self.client_id, ROUTER_MODEL, self._router.base_url,
        )

        # Pre-warm the planner so the first real turn doesn't pay the cold-start
        # cost (~12s) of the first responses.create round-trip. Fire-and-forget;
        # warming is best-effort and never blocks or fails the session.
        self._spawn_bg(self._prewarm_router())

    async def _prewarm_router(self) -> None:
        """Best-effort planner warm-up; safe to fail."""
        if self._router is None:
            return
        t0 = asyncio.get_event_loop().time()
        try:
            await self._router.warm()
            elapsed_ms = (asyncio.get_event_loop().time() - t0) * 1000.0
            logger.info(
                "Router: planner pre-warmed for client %s in %.0fms",
                self.client_id, elapsed_ms,
            )
        except Exception as e:
            logger.debug("Router: pre-warm failed (ignored): %s", e)

    def _record_router_turn(self, role: str, content: str) -> None:
        """Append a turn to the capped rolling history for the planner."""
        content = (content or "").strip()
        if not content or self._router_mode == "off":
            return
        self._router_history.append({"role": role, "content": content})
        if len(self._router_history) > _ROUTER_HISTORY_MAX:
            self._router_history = self._router_history[-_ROUTER_HISTORY_MAX:]

    async def on_user_turn(self, transcript: str, item_id: str) -> None:
        """SHADOW MODE: run the pre-router on a completed user transcript.

        Logs the routing decision + latency for analysis but does NOT change
        response creation — the agent still auto-responds exactly as before.
        Fully guarded so the router can never break or stall the live session
        (it is launched as a fire-and-forget background task by the caller).
        """
        if self._router is None or self._router_mode != "shadow":
            return
        transcript = (transcript or "").strip()
        if not transcript:
            return

        self._router_turn_seq += 1
        seq = self._router_turn_seq
        # Snapshot prior history for the planner (excludes this turn), then
        # record this user turn immediately so its ordering relative to the
        # assistant reply is correct even if decide() is slow (cold start).
        history = list(self._router_history)
        self._record_router_turn("user", transcript)
        t0 = asyncio.get_event_loop().time()
        try:
            decision = await self._router.decide(
                transcript, history=history, catalog=self._catalog
            )
        except Exception as e:
            logger.warning(
                "[ROUTER shadow] turn=%d item=%s decide() failed: %s",
                seq, item_id, e,
            )
            return

        elapsed_ms = (asyncio.get_event_loop().time() - t0) * 1000.0
        refined = (decision.refined_query or "")[:160]
        clarify = (decision.clarify_question or "")[:160]
        logger.info(
            "[ROUTER shadow] turn=%d item=%s action=%s intent=%s source=%s "
            "latency=%.0fms len=%d refined=%r clarify=%r",
            seq, item_id, decision.action, decision.intent, decision.source,
            elapsed_ms, len(transcript), refined, clarify,
        )

    async def _process_events(self, connection):
        """Process incoming events from Voice Live API.
        
        Uses manual recv() loop instead of 'async for' so that individual
        event parsing/handling errors don't kill the entire event loop.
        """
        while self.is_running:
            try:
                event = await connection.recv()
            except (ConnectionError, OSError) as e:
                # Parsing error from SDK — log details and continue listening
                logger.warning(f"[RECV] Event parsing error (continuing): {type(e).__name__}: {e}")
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Connection closed or fatal error
                if self._stopping:
                    logger.info(f"Voice Live connection closed for {self.client_id}")
                else:
                    logger.error(f"Connection error in event loop: {e}")
                break

            try:
                etype = getattr(event, 'type', 'unknown')
                if etype not in (ServerEventType.RESPONSE_AUDIO_DELTA,
                                 ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA,
                                 "response.video.delta"):
                    logger.debug(f"[RECV] {etype}")
                if etype == "response.video.delta":
                    self._video_chunk_count = getattr(self, '_video_chunk_count', 0) + 1
                    if self._video_chunk_count == 1:
                        logger.info(f"[RECV] first response.video.delta received")
                await handle_event(self, event, connection)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error handling event {getattr(event, 'type', 'unknown')}: {e}", exc_info=True)
                # Continue processing — don't let one bad event kill the loop

    async def send_audio(self, audio_base64: str):
        """Send base64-encoded audio data from browser to Voice Live (legacy path)."""
        if not self.connection:
            self._audio_chunk_count += 1
            if self._audio_chunk_count == 1 or self._audio_chunk_count % 500 == 0:
                logger.warning(f"[AUDIO] No connection — dropping audio chunk #{self._audio_chunk_count} (connection lost)")
            return
        if not self.is_running:
            logger.warning(f"[AUDIO] Session not running — dropping audio chunk")
            return
        try:
            self._audio_chunk_count += 1
            await self.connection.input_audio_buffer.append(audio=audio_base64)
        except Exception as e:
            logger.error(f"Error sending audio: {e}")

    async def send_audio_bytes(self, audio_bytes: bytes):
        """Send raw PCM16 audio bytes from browser to Voice Live.

        Encodes to base64 here (the SDK's append() accepts base64) but avoids
        the JSON parse + base64 encode-on-client cost on the wire.
        """
        if not self.connection:
            self._audio_chunk_count += 1
            if self._audio_chunk_count == 1 or self._audio_chunk_count % 500 == 0:
                logger.warning(f"[AUDIO] No connection — dropping audio chunk #{self._audio_chunk_count} (connection lost)")
            return
        if not self.is_running:
            return
        try:
            import base64
            self._audio_chunk_count += 1
            audio_base64 = base64.b64encode(audio_bytes).decode("ascii")
            await self.connection.input_audio_buffer.append(audio=audio_base64)
        except Exception as e:
            logger.error(f"Error sending audio bytes: {e}")

    async def send_text_message(self, text: str):
        """Send a text message to the conversation."""
        if self.connection:
            try:
                item = UserMessageItem(
                    content=[InputTextContentPart(text=text)]
                )
                await self.connection.conversation.item.create(item=item)
                await self.connection.response.create()
            except Exception as e:
                logger.error(f"Error sending text: {e}")

    async def send_avatar_sdp_offer(self, client_sdp: str):
        """Forward the browser's SDP offer to Voice Live for avatar WebRTC."""
        if self.connection:
            try:
                # Log diagnostic info about the SDP format
                sdp_preview = client_sdp[:60] if client_sdp else '(empty)'
                logger.debug(f"[SDP-CHECK] client_sdp len={len(client_sdp)} starts={sdp_preview}")

                avatar_connect = ClientEventSessionAvatarConnect(
                    client_sdp=client_sdp,
                )
                serialized = avatar_connect.as_dict() if hasattr(avatar_connect, 'as_dict') else str(avatar_connect)
                logger.debug("[SEND] session.avatar.connect")
                await self.connection.send(avatar_connect)
                logger.info("Sent avatar SDP offer to Voice Live")
            except Exception as e:
                logger.error(f"Error sending avatar SDP offer: {e}")

    async def interrupt(self):
        """Interrupt current response."""
        if self.connection:
            try:
                await self.connection.response.cancel()
                await self.send_message({
                    "type": "stop_playback",
                    "reason": "manual_interrupt",
                })
            except Exception as e:
                logger.error(f"Error interrupting: {e}")

    async def update_avatar_scene(self, avatar_data: dict):
        """Send a raw session.update with avatar scene config.
        
        Bypasses SDK serialization completely by writing raw JSON directly
        to the underlying websocket, matching the JS sample's sendRawEvent approach.
        
        Includes input/output audio format and turn detection in the update
        to prevent the server from resetting those fields to defaults.
        """
        if self.connection:
            try:
                # Build session payload with avatar + preserved audio config
                session_payload = {
                    "avatar": avatar_data,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                }

                # Preserve turn detection config
                td = build_turn_detection(self.config)
                if hasattr(td, 'as_dict'):
                    session_payload["turn_detection"] = td.as_dict()
                elif hasattr(td, '__dict__'):
                    session_payload["turn_detection"] = {k: v for k, v in td.__dict__.items() if not k.startswith('_')}

                raw_event = {
                    "type": "session.update",
                    "session": session_payload,
                }
                raw_json = json.dumps(raw_event)
                logger.debug("[SEND] raw session.update (scene)")
                await self.connection._connection.send_str(raw_json)
            except Exception as e:
                logger.error(f"Error updating avatar scene: {e}", exc_info=True)

    async def stop(self):
        """Stop the session."""
        self._stopping = True
        self.is_running = False
        conn = self.connection
        self.connection = None
        if conn is not None:
            try:
                await conn.close()
            except Exception as e:
                logger.debug(f"Error closing Voice Live connection for {self.client_id}: {e}")

    async def _wait_for_event(self, connection, wanted_types: set, timeout_s: float = 15.0):
        """Wait for specific event types."""
        logger.debug(f"[WAIT] Waiting for event types: {wanted_types}")
        async def _next():
            async for event in connection:
                etype = getattr(event, 'type', 'unknown')
                if etype != ServerEventType.RESPONSE_AUDIO_DELTA:
                    logger.debug(f"[RECV-WAIT] {etype}")
                if event.type in wanted_types:
                    return event
                # Continue handling other events while waiting
                await handle_event(self, event, connection)
            return None

        try:
            return await asyncio.wait_for(_next(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for {wanted_types}")
            raise
