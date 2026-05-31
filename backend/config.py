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


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter())
    logging.basicConfig(level=level, handlers=[handler])


# Public env-derived defaults
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))
DEFAULT_VOICE = os.getenv("VOICELIVE_VOICE", "en-US-AvaMultilingualNeural")
DEFAULT_ENDPOINT = os.getenv("AZURE_VOICELIVE_ENDPOINT", "")
DEFAULT_API_KEY = os.getenv("AZURE_VOICELIVE_API_KEY", "")
AGENT_NAME = os.getenv("AGENT_NAME", "")
AGENT_PROJECT_NAME = os.getenv("AGENT_PROJECT_NAME", "")


# ---------------------------------------------------------------------------
# UI / behaviour configuration (UI_* env vars)
#
# The frontend used to expose ~30 toggles in a left sidebar. Issue #13 moves
# all of that to .env: every UI_* var below maps to one knob in the legacy
# sidebar. When UI_DEVELOPER_MODE=false (production), the sidebar is hidden,
# the session auto-connects on page load, and the backend ignores any
# client-supplied start_session.config. When UI_DEVELOPER_MODE=true, the
# sidebar comes back, devs can tweak values live, and per-session overrides
# are merged on top of the env defaults below.
# ---------------------------------------------------------------------------

_ui_logger = logging.getLogger(__name__)


def _get_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    _ui_logger.warning(
        "Invalid bool for %s=%r; using default %s", name, raw, default
    )
    return default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        _ui_logger.warning(
            "Invalid int for %s=%r; using default %d", name, raw, default
        )
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        _ui_logger.warning(
            "Invalid float for %s=%r; using default %s", name, raw, default
        )
        return default


def _get_enum(name: str, default: str, allowed: tuple) -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    if raw not in allowed:
        _ui_logger.warning(
            "Invalid value for %s=%r (allowed: %s); using default %r",
            name, raw, ", ".join(allowed), default,
        )
        return default
    return raw


_SR_MODELS = ("azure-speech", "mai-ears-1")
_TURN_DETECTION_TYPES = ("azure_semantic_vad", "server_vad")
_EOU_DETECTION_TYPES = ("semantic_detection_v1", "semantic_detection_v1_multilingual", "none")
_VOICE_TYPES = ("standard", "custom", "personal")
_AVATAR_OUTPUT_MODES = ("webrtc", "websocket")


def _derive_display_name(effective_avatar_name: str) -> str:
    """Best-effort humanized label for the avatar.

    e.g. "Lisa-casual-sitting" -> "Lisa", "Harry-business" -> "Harry",
    "Anika" -> "Anika". Returns empty string if input is empty.
    """
    if not effective_avatar_name:
        return ""
    head = effective_avatar_name.split("-", 1)[0].strip()
    if not head:
        return ""
    return head[:1].upper() + head[1:]


def get_ui_config() -> dict:
    """Resolve UI behaviour from UI_* env vars into the canonical
    camelCase dict the frontend + handler/builders consume.

    This is recomputed on every call so test harnesses or `azd env set`
    + restart flows pick up changes without a code reload.
    """
    voice_type = _get_enum("UI_VOICE_TYPE", "standard", _VOICE_TYPES)
    voice_name = _get_str("UI_VOICE_NAME", DEFAULT_VOICE)
    voice_temperature = _get_float("UI_VOICE_TEMPERATURE", 0.9)
    voice_speed_percent = _get_int("UI_VOICE_SPEED", 100)
    # Backend builders expect a multiplier (1.0 = normal). .env stores a
    # percentage to match the legacy sidebar slider (50-150).
    voice_speed = voice_speed_percent / 100.0

    avatar_enabled = _get_bool("UI_AVATAR_ENABLED", True)
    avatar_output_mode = _get_enum("UI_AVATAR_OUTPUT_MODE", "webrtc", _AVATAR_OUTPUT_MODES)
    is_photo_avatar = _get_bool("UI_IS_PHOTO_AVATAR", False)
    is_custom_avatar = _get_bool("UI_IS_CUSTOM_AVATAR", False)
    avatar_name_preset = _get_str("UI_AVATAR_NAME", "Lisa-casual-sitting")
    photo_avatar_name = _get_str("UI_PHOTO_AVATAR_NAME", "Anika")
    custom_avatar_name = _get_str("UI_CUSTOM_AVATAR_NAME", "")

    # Mirror frontend gatherConfig() precedence: custom > photo > standard.
    if is_custom_avatar:
        effective_avatar_name = custom_avatar_name or avatar_name_preset
    elif is_photo_avatar:
        effective_avatar_name = photo_avatar_name or avatar_name_preset
    else:
        effective_avatar_name = avatar_name_preset

    avatar_display_name = _get_str(
        "UI_AVATAR_DISPLAY_NAME",
        _derive_display_name(effective_avatar_name),
    )

    photo_scene = {
        "zoom": _get_int("UI_SCENE_ZOOM", 100),
        "positionX": _get_int("UI_SCENE_POSITION_X", 0),
        "positionY": _get_int("UI_SCENE_POSITION_Y", 0),
        "rotationX": _get_int("UI_SCENE_ROTATION_X", 0),
        "rotationY": _get_int("UI_SCENE_ROTATION_Y", 0),
        "rotationZ": _get_int("UI_SCENE_ROTATION_Z", 0),
        "amplitude": _get_int("UI_SCENE_AMPLITUDE", 100),
    }

    return {
        "srModel": _get_enum("UI_SR_MODEL", "azure-speech", _SR_MODELS),
        "recognitionLanguage": _get_str("UI_RECOGNITION_LANGUAGE", "auto"),
        "useNS": _get_bool("UI_USE_NS", True),
        "useEC": _get_bool("UI_USE_EC", True),
        "turnDetectionType": _get_enum(
            "UI_TURN_DETECTION_TYPE", "azure_semantic_vad", _TURN_DETECTION_TYPES
        ),
        "removeFillerWords": _get_bool("UI_REMOVE_FILLER_WORDS", False),
        "eouDetectionType": _get_enum(
            "UI_EOU_DETECTION_TYPE", "semantic_detection_v1", _EOU_DETECTION_TYPES
        ),
        "enableProactive": _get_bool("UI_ENABLE_PROACTIVE", True),
        "voiceType": voice_type,
        "voiceName": voice_name,
        "voiceTemperature": voice_temperature,
        "voiceSpeed": voice_speed,
        "voiceDeploymentId": _get_str("UI_CUSTOM_VOICE_DEPLOYMENT_ID", ""),
        "customVoiceName": _get_str("UI_CUSTOM_VOICE_NAME", ""),
        "personalVoiceName": _get_str("UI_PERSONAL_VOICE_NAME", ""),
        "personalVoiceModel": _get_str("UI_PERSONAL_VOICE_MODEL", "DragonLatestNeural"),
        "avatarEnabled": avatar_enabled,
        "avatarOutputMode": avatar_output_mode,
        "isPhotoAvatar": is_photo_avatar,
        "isCustomAvatar": is_custom_avatar,
        "avatarName": effective_avatar_name,
        "avatarDisplayName": avatar_display_name,
        "photoAvatarName": photo_avatar_name,
        "customAvatarName": custom_avatar_name,
        "avatarBackgroundImageUrl": _get_str("UI_AVATAR_BACKGROUND_IMAGE_URL", ""),
        "photoScene": photo_scene,
        "developerMode": _get_bool("UI_DEVELOPER_MODE", False),
    }
