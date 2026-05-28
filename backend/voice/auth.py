"""Credential factory for the Voice Live SDK."""

import logging
from threading import Lock

from azure.core.credentials import AzureKeyCredential

logger = logging.getLogger(__name__)

# Singleton DefaultAzureCredential. Token cache is per-instance, so reusing
# one instance across sessions avoids paying token acquisition cost (500-2000ms)
# on every new client connection.
_default_credential = None
_default_credential_lock = Lock()


def create_credential(api_key: str):
    """Return an SDK credential.

    If `api_key` is provided, returns AzureKeyCredential. Otherwise returns
    a cached DefaultAzureCredential singleton (az login / managed identity /
    SP env vars).

    NOTE: Voice Live agent-v2 sessions do not accept API key auth, so the
    WebSocket path in backend/api/websocket.py always calls this with an
    empty key and goes through DefaultAzureCredential. The api_key branch
    here is retained for raw realtime sessions and AI Search reuse.
    """
    if api_key:
        logger.info("Auth: using API key")
        return AzureKeyCredential(api_key)

    global _default_credential
    if _default_credential is None:
        with _default_credential_lock:
            if _default_credential is None:
                from azure.identity.aio import DefaultAzureCredential
                logger.info("Auth: creating DefaultAzureCredential singleton")
                _default_credential = DefaultAzureCredential()
    return _default_credential


async def close_credential() -> None:
    """Close the cached DefaultAzureCredential, if it was created.

    `azure.identity.aio.DefaultAzureCredential` (and its chained inner
    credentials like ManagedIdentityCredential) holds an internal
    `aiohttp.ClientSession` for IMDS probes and token requests. If we
    don't await its `close()` on shutdown, the asyncio loop logs:

        ERROR asyncio Unclosed client session
        client_session: <aiohttp.client.ClientSession object at 0x...>

    Call this from the FastAPI lifespan shutdown phase, AFTER any code
    paths that might still use the credential have completed.
    """
    global _default_credential
    if _default_credential is None:
        return
    cred = _default_credential
    _default_credential = None
    try:
        await cred.close()
        logger.info("Auth: DefaultAzureCredential closed")
    except Exception as e:
        # Don't let cleanup errors mask the real shutdown reason.
        logger.warning(f"Auth: error closing DefaultAzureCredential (ignored): {e}")
