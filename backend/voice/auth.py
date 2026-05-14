"""Credential factory for the Voice Live SDK."""

import logging

from azure.core.credentials import AzureKeyCredential

logger = logging.getLogger(__name__)


def create_credential(api_key: str):
    """Return an SDK credential.

    If `api_key` is provided, returns AzureKeyCredential. Otherwise returns
    DefaultAzureCredential (uses az login / managed identity / env vars).
    """
    if api_key:
        logger.info("Auth: using API key")
        return AzureKeyCredential(api_key)
    from azure.identity.aio import DefaultAzureCredential
    logger.info("Auth: using DefaultAzureCredential")
    return DefaultAzureCredential()
