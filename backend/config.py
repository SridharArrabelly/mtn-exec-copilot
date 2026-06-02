"""Environment loading and logging configuration."""

import logging
import os

from dotenv import load_dotenv

load_dotenv()


class ColorFormatter(logging.Formatter):
    """Adds ANSI color codes to log output."""

    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
        logging.CRITICAL: "\033[1;31m",  # Bold Red
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    WHITE = "\033[97m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        timestamp = self.formatTime(record, self.datefmt)
        return (
            f"{self.DIM}{timestamp}{self.RESET} "
            f"{color}{self.BOLD}{record.levelname:<8}{self.RESET} "
            f"{self.DIM}{record.name}{self.RESET} "
            f"{self.WHITE}{record.getMessage()}{self.RESET}"
        )


def configure_logging(level: int | str | None = None) -> None:
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter())
    logging.basicConfig(level=level, handlers=[handler])


# Public env-derived defaults
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))
DEFAULT_VOICE = os.getenv("VOICELIVE_VOICE", "")
DEFAULT_ENDPOINT = os.getenv("AZURE_VOICELIVE_ENDPOINT", "")
DEFAULT_API_KEY = os.getenv("AZURE_VOICELIVE_API_KEY", "")
AGENT_NAME = os.getenv("AGENT_NAME", "")
AGENT_PROJECT_NAME = os.getenv("AGENT_PROJECT_NAME", "")
DEVELOPER_MODE = os.getenv("DEVELOPER_MODE", "false").strip().lower() == "true"

# Pre-router (tool-selection planner) settings.
#
# ROUTER_MODE controls how the validated pre-router (backend/voice/router.py)
# participates in the live Voice Live runtime:
#   * "off"    — disabled; the agent decides tools on its own (current prod).
#   * "shadow" — the router runs on every real user turn and its decision is
#                LOGGED, but response creation is NOT changed. Zero behavioural
#                regression risk; used to validate auth, latency, token refresh,
#                catalogue availability and event ordering against live traffic.
#   * "active" — (future) the router gates response creation and injects its
#                hint / clarification before the agent responds.
# The planner runs gpt-4.1-mini via the Responses API on the model-inference
# surface of the SAME Foundry resource (PROJECT_ENDPOINT host) — NOT the agent
# endpoint, which forbids a per-request model=.
ROUTER_MODE = os.getenv("ROUTER_MODE", "off").strip().lower()
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "gpt-4.1-mini")
ROUTER_BASE_URL = os.getenv("ROUTER_BASE_URL", "")
ROUTER_API_VERSION = os.getenv("ROUTER_API_VERSION", "preview")
PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT", "")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _str(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def get_ui_defaults() -> dict:
    """Settings sent to the frontend on /api/config.

    Each value overrides the matching HTML default. Used in production
    (DEVELOPER_MODE=false) where the side panel is hidden and the session
    auto-starts with whatever is configured here.
    """
    return {
        # Conversation
        "srModel": _str("SR_MODEL", "mai-transcribe-1"),
        "recognitionLanguage": _str("RECOGNITION_LANGUAGE", "auto"),
        "useNS": _bool("USE_NOISE_SUPPRESSION", True),
        "useEC": _bool("USE_ECHO_CANCELLATION", True),
        "turnDetectionType": _str("TURN_DETECTION_TYPE", "azure_semantic_vad"),
        "removeFillerWords": _bool("REMOVE_FILLER_WORDS", False),
        "eouDetectionType": _str("EOU_DETECTION_TYPE", "semantic_detection_v1"),
        "enableProactive": _bool("ENABLE_PROACTIVE", False),
        # Voice
        "voiceType": _str("VOICE_TYPE", "standard"),
        "voiceName": _str("VOICELIVE_VOICE", "en-US-AvaMultilingualNeural"),
        "voiceSpeed": int(_str("VOICE_SPEED", "100")),
        "voiceTemperature": float(_str("VOICE_TEMPERATURE", "0.9")),
        # Avatar
        "avatarEnabled": _bool("AVATAR_ENABLED", True),
        "avatarOutputMode": _str("AVATAR_OUTPUT_MODE", "webrtc"),
        "isPhotoAvatar": _bool("IS_PHOTO_AVATAR", False),
        "isCustomAvatar": _bool("IS_CUSTOM_AVATAR", False),
        "avatarName": _str("AVATAR_NAME", "Lisa-casual-sitting"),
        "customAvatarName": _str("CUSTOM_AVATAR_NAME", ""),
        "photoAvatarName": _str("PHOTO_AVATAR_NAME", "Anika"),
        "avatarBackgroundImageUrl": _str("AVATAR_BACKGROUND_IMAGE_URL", ""),
    }
