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
from .voice.auth import create_credential

configure_logging()
logger = logging.getLogger(__name__)


async def _prewarm_credential() -> None:
    """Acquire tokens at startup so the first user doesn't pay the cold-cost.

    DefaultAzureCredential's first token acquisition can take 1-6 seconds
    (resolves AzureCliCredential / managed identity / env-based chains, then
    shells out to ``az account get-access-token`` for the first scope).

    **Tokens are cached per-scope**, so we must warm the scope Voice Live
    actually requests (``https://ai.azure.com/.default``). Previously we only
    warmed ``https://cognitiveservices.azure.com/.default``, which left
    Voice Live to pay the full ~6s CLI spawn on the first user's Connect.

    Both scopes are warmed in parallel:
    - ``ai.azure.com`` — Voice Live SDK (primary path)
    - ``cognitiveservices.azure.com`` — direct AOAI embeddings / AI Search
      calls if the backend ever makes them (e.g. for diagnostic scripts)
    """
    scopes = (
        "https://ai.azure.com/.default",
        "https://cognitiveservices.azure.com/.default",
    )
    try:
        credential = create_credential("")
        await asyncio.gather(*(credential.get_token(s) for s in scopes))
        logger.info(f"Credential pre-warmed at startup (scopes: {', '.join(scopes)})")
    except Exception as e:
        # Don't fail startup if pre-warm fails — the per-session path will
        # surface a real error later.
        logger.warning(f"Credential pre-warm failed (will retry on first session): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hook: pre-warms credentials, closes outstanding sessions on shutdown."""
    logger.info("MTN Exec Copilot server starting...")
    # Fire-and-forget pre-warm so startup is not blocked.
    asyncio.create_task(_prewarm_credential())
    yield
    await ws.shutdown_all()
    logger.info("MTN Exec Copilot server stopped.")


app = FastAPI(
    title="MTN Exec Copilot",
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
        return {"message": "MTN Exec Copilot — frontend/ directory not found."}


def run() -> None:
    """Console-script entry point (see pyproject [project.scripts])."""
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True, log_level="info")


if __name__ == "__main__":
    run()
