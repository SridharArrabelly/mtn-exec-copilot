"""Environment loading and logging configuration."""

import logging
import os

from dotenv import load_dotenv

load_dotenv(override=True)


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
# Voice Live REST/WebSocket API version. The set of accepted speech-recognition
# models is gated server-side by this version. NOTE: mai-transcribe-1.5 is
# currently only available via the separate LLM Speech (batch) API, NOT the
# Voice Live realtime API used here — bumping this does not unlock it yet.
VOICELIVE_API_VERSION = os.getenv("VOICELIVE_API_VERSION", "2026-01-01-preview")
DEVELOPER_MODE = os.getenv("DEVELOPER_MODE", "false").strip().lower() == "true"

# Verbatim opening line spoken by the avatar when proactive greeting is enabled.
# Client-specific persona/wording lives in the environment, keeping this code
# generic and reusable. Falls back to a neutral greeting when unset.
PROACTIVE_GREETING = os.getenv(
    "PROACTIVE_GREETING",
    "Hello! How can I help you today?",
)

PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT", "")

# ───────── Teams bot (issue #53, Phase 2a) ─────────
# The Foundry agent is resolved by ID when available (durable identifier), with
# AGENT_NAME as a dev-only fallback (fails fast on zero/multiple matches).
AGENT_ID = os.getenv("AGENT_ID", "")
# Teams app id used to build deep links back to the Phase 1 static tab (#28).
# Defaults to TEAMS_APP_ID if that is what the package was built with.
TEAMS_APP_ID = os.getenv("TEAMS_APP_ID", "")
# entityId of the personal static tab in teams/manifest.template.json.
TEAMS_TAB_ENTITY_ID = os.getenv("TEAMS_TAB_ENTITY_ID", "avatarForgeHome")
# Seconds to poll a Foundry run synchronously inside a Teams turn before
# returning a "still working" holding reply (proactive continuation is a later
# enhancement). Keep well under the Teams ~15s activity timeout.
BOT_RUN_TIMEOUT_S = float(os.getenv("BOT_RUN_TIMEOUT_S", "12"))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _str(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _list(name: str, default: list[str]) -> list[str]:
    """Parse a pipe-separated env var into a list of trimmed, non-empty strings.

    Returns ``default`` when the var is unset or empty so callers always get a
    usable list (e.g. SUGGESTED_PROMPTS="Ask me X | Ask me Y").
    """
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    items = [part.strip() for part in raw.split("|")]
    items = [part for part in items if part]
    return items or default


def get_ui_defaults() -> dict:
    """Settings sent to the frontend on /api/config.

    Each value overrides the matching HTML default. Used in production
    (DEVELOPER_MODE=false) where the side panel is hidden and the session
    auto-starts with whatever is configured here.
    """
    # The onboarding hint default is modality-aware: when the text composer is
    # enabled, invite typing too. An explicit ONBOARDING_HINT always wins.
    _text_input = _bool("ENABLE_TEXT_INPUT", True)
    _hint_default = (
        "Tap the mic or type to ask me anything"
        if _text_input
        else "Tap the mic and ask me anything"
    )
    return {
        # Conversation
        "srModel": _str("SR_MODEL", "mai-transcribe-1"),
        "recognitionLanguage": _str("RECOGNITION_LANGUAGE", "auto"),
        "useNS": _bool("USE_NOISE_SUPPRESSION", True),
        "useEC": _bool("USE_ECHO_CANCELLATION", True),
        "turnDetectionType": _str("TURN_DETECTION_TYPE", "azure_semantic_vad"),
        "turnDetectionSilenceMs": int(_str("TURN_DETECTION_SILENCE_MS", "500")),
        "enableBargeIn": _bool("ENABLE_BARGE_IN", True),
        "removeFillerWords": _bool("REMOVE_FILLER_WORDS", True),
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
        # Avatar identity tagline shown under the name (top-left of the stage).
        # Empty hides the tagline line.
        "avatarTagline": _str("AVATAR_TAGLINE", "Your MTN Digital Assistant"),
        # Avatar UX (additive, env-gated)
        "enableTextInput": _bool("ENABLE_TEXT_INPUT", True),
        "enableCaptions": _bool("ENABLE_CAPTIONS", False),
        "captionsShowUser": _bool("CAPTIONS_SHOW_USER", False),
        "enableSuggestedPrompts": _bool("ENABLE_SUGGESTED_PROMPTS", True),
        "onboardingHint": _str("ONBOARDING_HINT", _hint_default),
        "suggestedPrompts": _list(
            "SUGGESTED_PROMPTS",
            [
                "What can you help me with?",
                "Tell me about your services",
                "How do I get started?",
            ],
        ),
    }
