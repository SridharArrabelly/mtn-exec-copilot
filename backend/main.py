"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import routes, websocket as ws
from .config import HOST, PORT, configure_logging

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("MTN Exec Copilot server starting...")
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
        return {"message": "MTN Exec Copilot — frontend/ directory not found."}


def run() -> None:
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True, log_level="info")


if __name__ == "__main__":
    run()
