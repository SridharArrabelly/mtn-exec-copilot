"""Credential factory for the Voice Live SDK."""

import asyncio
import logging
import os
import time
from threading import Lock
from typing import Any, Optional

from azure.core.credentials import AccessToken, AccessTokenInfo, AzureKeyCredential

logger = logging.getLogger(__name__)

# Singleton credential (a _CachingCredentialWrapper around DefaultAzureCredential).
# Reusing one instance across sessions is necessary but NOT sufficient: the
# underlying AzureCliCredential performs NO in-memory token caching — every
# get_token / get_token_info shells out to `az account get-access-token`
# (~1s). The wrapper below caches one token per audience and reuses it until
# shortly before expiry, so each scope is acquired once, not once-per-call.
_default_credential = None
_default_credential_lock = Lock()


class _CachingCredentialWrapper:
    """In-memory per-scope token cache around an async token credential.

    Serves BOTH the ``get_token`` (``AsyncTokenCredential``) and
    ``get_token_info`` (``AsyncSupportsTokenInfo``) APIs from a single cache so
    that whichever SDK path asks — Voice Live uses ``get_token``, Azure AI
    Search uses ``get_token_info`` — reuses the same cached token.

    A cached entry is reused until ``REFRESH_BUFFER_SECONDS`` before it expires
    (or until ``refresh_on`` if the STS supplied one). Requests carrying
    ``claims`` (a CAE / conditional-access re-challenge) always bypass the
    cached value and fetch a fresh token, then refresh the cache.
    """

    # Refresh this many seconds before expiry so an in-flight request never
    # races a token that lapses mid-call.
    REFRESH_BUFFER_SECONDS = 300

    def __init__(self, inner: Any):
        self._inner = inner
        self._cache: dict[tuple, AccessTokenInfo] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}
        # Guards creation of per-key asyncio locks (cheap, non-awaited ops only).
        self._dict_lock = Lock()

    @staticmethod
    def _key(scopes, tenant_id, enable_cae) -> tuple:
        return (tuple(scopes), tenant_id, bool(enable_cae))

    def _lock_for(self, key: tuple) -> asyncio.Lock:
        with self._dict_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def _is_valid(self, info: AccessTokenInfo) -> bool:
        now = time.time()
        refresh_on = getattr(info, "refresh_on", None)
        deadline = refresh_on if refresh_on else (info.expires_on - self.REFRESH_BUFFER_SECONDS)
        return now < deadline

    async def _acquire_info(self, scopes, claims, tenant_id, enable_cae) -> AccessTokenInfo:
        """Fetch a fresh token from the inner credential as an AccessTokenInfo."""
        if hasattr(self._inner, "get_token_info"):
            options: dict[str, Any] = {}
            if claims is not None:
                options["claims"] = claims
            if tenant_id is not None:
                options["tenant_id"] = tenant_id
            if enable_cae:
                options["enable_cae"] = True
            return await self._inner.get_token_info(*scopes, options=options or None)
        tok = await self._inner.get_token(
            *scopes, claims=claims, tenant_id=tenant_id, enable_cae=enable_cae
        )
        return AccessTokenInfo(tok.token, tok.expires_on)

    async def _get_info(self, scopes, claims, tenant_id, enable_cae) -> AccessTokenInfo:
        key = self._key(scopes, tenant_id, enable_cae)
        # Fast path: serve a still-valid cached token without locking.
        if claims is None:
            cached = self._cache.get(key)
            if cached is not None and self._is_valid(cached):
                return cached
        # Slow path: single-flight acquisition guarded by a per-key lock so a
        # burst of concurrent sessions triggers exactly one `az` shell-out.
        lock = self._lock_for(key)
        async with lock:
            if claims is None:
                cached = self._cache.get(key)
                if cached is not None and self._is_valid(cached):
                    return cached
            info = await self._acquire_info(scopes, claims, tenant_id, enable_cae)
            self._cache[key] = info
            return info

    async def get_token(
        self, *scopes, claims=None, tenant_id=None, enable_cae=False, **kwargs
    ) -> AccessToken:
        info = await self._get_info(scopes, claims, tenant_id, enable_cae)
        return AccessToken(info.token, info.expires_on)

    async def get_token_info(self, *scopes, options=None) -> AccessTokenInfo:
        options = options or {}
        claims = options.get("claims")
        tenant_id = options.get("tenant_id")
        enable_cae = bool(options.get("enable_cae", False))
        return await self._get_info(scopes, claims, tenant_id, enable_cae)

    async def close(self) -> None:
        await self._inner.close()

    # The async-credential protocols the SDKs check against
    # (AsyncTokenCredential / AsyncSupportsTokenInfo) are runtime_checkable AND
    # extend AsyncContextManager, so isinstance() requires these two methods to
    # be present — without them the Voice Live SDK misclassifies this wrapper as
    # a *sync* credential and calls get_token() without awaiting it.
    #
    # __aexit__ deliberately does NOT close: this wrapper is a process-wide
    # shared singleton whose lifecycle is owned by close_credential() at app
    # shutdown. A transient `async with` by an SDK must not tear down the
    # credential other live sessions are still using.
    async def __aenter__(self) -> "_CachingCredentialWrapper":
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None


def _exclude_managed_identity() -> bool:
    """Return True if the IMDS probe should be skipped (dev laptops).

    On a developer machine, `DefaultAzureCredential` walks the chain in order:
    Env → ManagedIdentity (IMDS) → AzureCli. The IMDS probe at
    169.254.169.254 is unreachable off-Azure and takes ~5s to time out per
    parallel `get_token` call before falling through to `az login`. Setting
    `AUTH_EXCLUDE_MANAGED_IDENTITY=true` skips that probe entirely, cutting
    cold-start pre-warm from ~7s to ~1.5s. Leave UNSET in production —
    Container Apps / App Service use managed identity via IMDS.
    """
    return os.getenv("AUTH_EXCLUDE_MANAGED_IDENTITY", "").strip().lower() in (
        "true",
        "1",
        "yes",
    )


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
                if _exclude_managed_identity():
                    logger.info(
                        "Auth: creating DefaultAzureCredential singleton "
                        "(managed identity / IMDS excluded — dev mode)"
                    )
                    inner = DefaultAzureCredential(
                        exclude_managed_identity_credential=True
                    )
                else:
                    logger.info("Auth: creating DefaultAzureCredential singleton")
                    inner = DefaultAzureCredential()
                # Wrap in the in-memory token cache so each audience is acquired
                # once and reused (AzureCliCredential does not cache tokens).
                _default_credential = _CachingCredentialWrapper(inner)
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
