"""FastAPI application entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import routes, websocket as ws
from .config import HOST, PORT, configure_logging
from .voice.auth import close_credential, create_credential
from .voice.catalog import close_search_client, prewarm_catalog

configure_logging()
logger = logging.getLogger(__name__)


async def _prewarm_credential() -> None:
    """Acquire tokens at startup so the first user doesn't pay the cold-cost.

    DefaultAzureCredential's first token acquisition can take 1-6 seconds
    (resolves AzureCliCredential / managed identity / env-based chains, then
    shells out to ``az account get-access-token`` for the first scope).

    **Tokens are cached per-scope**, so we must warm every distinct Azure
    resource the backend actually talks to:
    - ``ai.azure.com`` — Voice Live SDK (primary path, every session)
    - ``search.azure.com`` — Azure AI Search (catalogue + agent tool calls)

    Scopes warmed in parallel. The Cognitive Services scope is NOT included
    because the backend has no direct AOAI / Cognitive Services callers
    (the Foundry agent and Voice Live SDK both use ``ai.azure.com``).
    Removing the unused scope shaves one ``az`` invocation (~1.3s) off
    startup.
    """
    scopes = (
        "https://ai.azure.com/.default",
        "https://search.azure.com/.default",
    )
    try:
        credential = create_credential("")
        await asyncio.gather(*(credential.get_token(s) for s in scopes))
        logger.info(f"Credential pre-warmed at startup (scopes: {', '.join(scopes)})")
    except Exception as e:
        # Don't fail startup if pre-warm fails — the per-session path will
        # surface a real error later.
        logger.warning(f"Credential pre-warm failed (will retry on first session): {e}")


async def _prewarm_startup() -> None:
    """Sequenced startup pre-warm: credential first, THEN catalogue.

    Sequencing matters — if these run in parallel, the catalogue's AI Search
    call asks the SDK for a ``search.azure.com`` token at the same moment
    ``_prewarm_credential`` is still acquiring it. The catalogue's request
    misses the cache and spawns ITS OWN ``az account get-access-token``
    call, duplicating ~1.3s of credential work and causing the catalogue
    fetch to be measured as ~7s instead of ~3-4s.

    Sequenced: catalogue fetch starts with a hot token cache; it only pays
    the AI Search round-trip cost.
    """
    await _prewarm_credential()
    await prewarm_catalog()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hook: pre-warms credentials, closes outstanding sessions on shutdown."""
    logger.info("Avatar Forge server starting...")
    # Fire-and-forget sequenced pre-warm so startup is not blocked but the
    # catalogue fetch benefits from a hot token cache.
    asyncio.create_task(_prewarm_startup())
    yield
    # Order matters: stop session handlers first (they may still use the
    # credential to refresh tokens during teardown), THEN close the
    # SearchClient (which uses the credential), THEN close the
    # credential's underlying aiohttp.ClientSession.
    await ws.shutdown_all()
    await close_search_client()
    await close_credential()
    logger.info("Avatar Forge server stopped.")


app = FastAPI(
    title="Avatar Forge",
    description="Python backend for Azure Voice Live with Avatar support",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Disable caching for static assets during development."""
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css", ".html")) or path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.include_router(routes.router)
app.include_router(ws.router)

# Mount frontend
_frontend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        """Fallback when frontend/ is missing."""
        return {"message": "Avatar Forge — frontend/ directory not found."}


def run() -> None:
    """Console-script entry point (see pyproject [project.scripts])."""
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True, log_level=os.getenv("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    run()
