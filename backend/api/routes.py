"""HTTP endpoints (health + frontend config bootstrap)."""

from fastapi import APIRouter

from ..config import DEFAULT_VOICE

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "mtn-exec-copilot"}


@router.get("/api/config")
async def get_config():
    """Return default configuration to the frontend."""
    return {
        "voice": DEFAULT_VOICE,
    }
