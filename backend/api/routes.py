"""HTTP endpoints (health + frontend config bootstrap)."""

from fastapi import APIRouter

from ..config import DEFAULT_VOICE, get_ui_config

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe."""
    return {"status": "healthy", "service": "mtn-exec-copilot"}


@router.get("/api/config")
async def get_config():
    """Return the fully-resolved UI configuration to the frontend.

    The frontend reads this once on page load and uses it both to render
    the UI (or hide the sidebar in production) and to assemble the
    start_session payload. See backend/config.py::get_ui_config for the
    canonical shape and defaults.
    """
    return {**get_ui_config(), "voice": DEFAULT_VOICE}
