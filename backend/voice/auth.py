"""Credential factory for the Voice Live SDK."""

import logging

from azure.core.credentials import AzureKeyCredential

logger = logging.getLogger(__name__)


def create_credential(api_key: str):
    """Return an SDK credential.

    If `api_key` is provided, returns AzureKeyCredential. Otherwise returns
    DefaultAzureCredential (az login / managed identity / SP env vars).

    NOTE: Voice Live agent-v2 sessions do not accept API key auth, so the
    WebSocket path in backend/api/websocket.py always calls this with an
    empty key and goes through DefaultAzureCredential. The api_key branch
    here is retained for raw realtime sessions and AI Search reuse.
    """
    if api_key:
        logger.info("Auth: using API key")
        return AzureKeyCredential(api_key)
    from azure.identity.aio import DefaultAzureCredential
    logger.info("Auth: using DefaultAzureCredential")
    return DefaultAzureCredential()
