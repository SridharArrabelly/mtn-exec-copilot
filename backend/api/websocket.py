"""WebSocket endpoint and per-client session orchestration."""

import asyncio
import json
import logging
import os
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import DEFAULT_ENDPOINT, DEFAULT_API_KEY
from ..voice import VoiceSessionHandler
from ..voice.auth import create_credential

logger = logging.getLogger(__name__)
router = APIRouter()

# Per-client session bookkeeping
active_sessions: Dict[str, VoiceSessionHandler] = {}
active_tasks: Dict[str, asyncio.Task] = {}


@router.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """Main WebSocket endpoint for voice session communication."""
    await websocket.accept()
    logger.info(f"Client {client_id} connected")

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            await _handle_message(client_id, message, websocket)
    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error for {client_id}: {e}")
    finally:
        await cleanup_client(client_id)


async def _handle_message(client_id: str, message: dict, websocket: WebSocket):
    """Route incoming WebSocket messages."""
    msg_type = message.get("type")

    if msg_type == "start_session":
        await _start_session(client_id, message.get("config", {}), websocket)
        return

    handler = active_sessions.get(client_id)

    if msg_type == "stop_session":
        await cleanup_client(client_id)
    elif msg_type == "audio_chunk" and handler:
        await handler.send_audio(message.get("data", ""))
    elif msg_type == "send_text" and handler:
        await handler.send_text_message(message.get("text", ""))
    elif msg_type == "avatar_sdp_offer" and handler:
        await handler.send_avatar_sdp_offer(message.get("clientSdp", ""))
    elif msg_type == "interrupt" and handler:
        await handler.interrupt()
    elif msg_type == "update_scene" and handler:
        await handler.update_avatar_scene(message.get("avatar", {}))
    else:
        logger.warning(f"Unknown or unhandled message type: {msg_type}")


async def _start_session(client_id: str, config: dict, websocket: WebSocket):
    """Start a new Voice Live session for a client."""
    await cleanup_client(client_id)

    endpoint = (DEFAULT_ENDPOINT or "").strip()
    if endpoint:
        endpoint = endpoint.rstrip("/")
        for suffix in ("/voice-live/realtime", "/voice-agent/realtime"):
            if endpoint.endswith(suffix):
                endpoint = endpoint[: -len(suffix)]
                break
    api_key = ""  # agent-v2 sessions require Entra ID; ignore any AZURE_VOICELIVE_API_KEY

    if not endpoint:
        await _send(websocket, {
            "type": "session_error",
            "error": "AZURE_VOICELIVE_ENDPOINT must be set in the environment.",
        })
        return

    try:
        credential = create_credential(api_key)
    except ImportError:
        await _send(websocket, {
            "type": "session_error",
            "error": "azure-identity is not installed. Run: uv add azure-identity",
        })
        return

    async def send_message(msg: dict):
        try:
            await websocket.send_text(json.dumps(msg))
        except Exception as e:
            logger.error(f"Error sending to {client_id}: {e}")

    handler = VoiceSessionHandler(
        client_id=client_id,
        endpoint=endpoint,
        credential=credential,
        send_message=send_message,
        config=config,
    )
    active_sessions[client_id] = handler
    active_tasks[client_id] = asyncio.create_task(handler.start())
    logger.info(f"Session started for {client_id}")


async def cleanup_client(client_id: str):
    """Clean up session and task for a client."""
    handler = active_sessions.pop(client_id, None)
    if handler:
        await handler.stop()

    task = active_tasks.pop(client_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def shutdown_all():
    """Stop all active sessions (called from app lifespan)."""
    for client_id in list(active_sessions.keys()):
        await cleanup_client(client_id)


async def _send(websocket: WebSocket, message: dict):
    try:
        await websocket.send_text(json.dumps(message))
    except Exception as e:
        logger.error(f"Error sending WebSocket message: {e}")
