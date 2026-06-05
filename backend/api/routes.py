"""HTTP endpoints (health + frontend config bootstrap)."""

from fastapi import APIRouter

from ..config import DEFAULT_VOICE, DEVELOPER_MODE, get_ui_defaults

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe."""
    return {"status": "healthy", "service": "avatar-forge"}


@router.get("/api/config")
async def get_config():
    """Return default configuration to the frontend."""
    return {
        "voice": DEFAULT_VOICE,
        "developerMode": DEVELOPER_MODE,
        "defaults": get_ui_defaults(),
    }
