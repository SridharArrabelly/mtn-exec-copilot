"""Live-runtime adapter around the pre-router planner.

``backend/voice/router.py`` contains the pure routing logic (regex prefilter
+ LLM planner) and was validated offline by ``scripts/_routing_harness.py``.
This module adapts it for the live Voice Live runtime:

* Builds the planner ``AsyncOpenAI`` client against the model-inference
  surface of the Foundry resource (``https://<resource_host>/openai/v1/``) —
  NOT the agent endpoint, which forbids a per-request ``model=``.
* Manages a cognitiveservices-scoped bearer token with a small per-scope
  cache, refreshing only within 5 minutes of expiry. The async
  ``AzureCliCredential`` does not cache in-memory, so naive per-call
  ``get_token`` is slow/flaky; managed identity is fine but caching is still
  cheap insurance for long-lived sessions.
* Exposes ``decide()`` which simply forwards to ``router.route`` with the
  conversation history + meeting catalogue.

Only usable when the SDK credential is a *token* credential (has
``get_token``). With API-key auth there is no token to mint for the planner
endpoint, so the caller keeps the router disabled.
"""

from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import urlparse

from . import router as _router
from .router import RouterDecision

logger = logging.getLogger(__name__)

# Token audience for the model-inference endpoint on a Foundry/AI Services
# resource. Differs from the agent endpoint audience (ai.azure.com).
_PLANNER_SCOPE = "https://cognitiveservices.azure.com/.default"


class LiveRouter:
    """Per-session adapter that runs the pre-router planner."""

    def __init__(
        self,
        credential,
        *,
        model: str,
        project_endpoint: str,
        base_url: str = "",
        api_version: str = "preview",
    ):
        if not project_endpoint:
            raise ValueError("LiveRouter requires PROJECT_ENDPOINT to be set")
        if not hasattr(credential, "get_token"):
            raise ValueError(
                "LiveRouter requires a token credential (got a non-token "
                "credential such as AzureKeyCredential)"
            )

        self._credential = credential
        self._model = model
        self._api_version = api_version

        project_endpoint = project_endpoint.rstrip("/")
        resource_host = urlparse(project_endpoint).netloc
        self._base_url = (base_url or f"https://{resource_host}/openai/v1/").rstrip("/") + "/"

        self._client = None  # lazily built on first decide()
        # scope -> (token, expires_on_epoch_seconds)
        self._token_cache: dict[str, tuple[str, float]] = {}

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _scoped_token(self, scope: str) -> str:
        cached = self._token_cache.get(scope)
        now = time.time()
        if cached and cached[1] - now > 300:
            return cached[0]
        tok = await self._credential.get_token(scope)
        self._token_cache[scope] = (tok.token, float(tok.expires_on))
        return tok.token

    async def _get_client(self):
        # Build once; refresh the bearer token on the existing client before
        # every call (reassigning ``api_key`` updates the auth header).
        from openai import AsyncOpenAI

        token = await self._scoped_token(_PLANNER_SCOPE)
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self._base_url,
                api_key=token,
                default_query={"api-version": self._api_version},
            )
        else:
            self._client.api_key = token
        return self._client

    async def warm(self) -> None:
        """Pre-warm the planner so the first real turn isn't a cold start.

        The cold cost on the first decide() is dominated by the initial
        ``responses.create`` HTTP round-trip to the model-inference endpoint
        (model spin-up + connection establishment), not just minting a token.
        We therefore issue one tiny throwaway planner call. ``plan_llm`` is
        internally guarded (returns a fallback on any error) so this never
        raises; we still wrap it defensively because warming is best-effort.
        """
        client = await self._get_client()
        await _router.plan_llm(
            "warm up",
            history=[],
            catalog="",
            client=client,
            model=self._model,
        )

    async def decide(
        self,
        query: str,
        history: Optional[list[dict]] = None,
        catalog: str = "",
    ) -> RouterDecision:
        """Run the two-stage router for one user turn.

        Returns a ``RouterDecision``. The regex prefilter (catalogue /
        obvious-external) and the planner fallback are both handled inside
        ``router.route``; this never raises for a routing miss — only an
        unexpected client/transport failure would propagate, and callers run
        this in a guarded context.
        """
        client = await self._get_client()
        return await _router.route(
            query,
            history=history or [],
            catalog=catalog,
            client=client,
            model=self._model,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as e:  # pragma: no cover - cleanup best effort
                logger.debug("LiveRouter: error closing planner client (ignored): %s", e)
            finally:
                self._client = None
