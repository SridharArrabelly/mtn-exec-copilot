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
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw


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


def _build_raw_ui_config() -> dict:
    """Read every UI_* env var into a raw camelCase dict.

    Defaults are baked in here so `.env` only needs to set what should differ
    from the production baseline. The returned dict has the same shape as
    :func:`get_ui_config` but **no resolver rules have been applied yet** —
    in particular, ``avatarName`` is the raw ``UI_AVATAR_NAME`` preset (not
    the effective custom > photo > standard precedence) and
    ``avatarDisplayName`` is the raw ``UI_AVATAR_DISPLAY_NAME`` (may be empty).

    Callers should normally use :func:`get_ui_config` instead; this helper is
    exposed only so resolver tests can exercise the rule layer on a known
    baseline.
    """
    voice_speed_percent = _get_int("UI_VOICE_SPEED", 100)
    # Backend builders expect a multiplier (1.0 = normal). .env stores a
    # percentage to match the legacy sidebar slider (50-150).
    voice_speed = voice_speed_percent / 100.0

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
        "voiceType": _get_enum("UI_VOICE_TYPE", "standard", _VOICE_TYPES),
        "voiceName": _get_str("UI_VOICE_NAME", "en-ZA-LeahNeural"),
        "voiceTemperature": _get_float("UI_VOICE_TEMPERATURE", 0.9),
        "voiceSpeed": voice_speed,
        "voiceDeploymentId": _get_str("UI_CUSTOM_VOICE_DEPLOYMENT_ID", ""),
        "customVoiceName": _get_str("UI_CUSTOM_VOICE_NAME", ""),
        "personalVoiceName": _get_str("UI_PERSONAL_VOICE_NAME", ""),
        "personalVoiceModel": _get_str("UI_PERSONAL_VOICE_MODEL", "DragonLatestNeural"),
        "avatarEnabled": _get_bool("UI_AVATAR_ENABLED", True),
        "avatarOutputMode": _get_enum("UI_AVATAR_OUTPUT_MODE", "webrtc", _AVATAR_OUTPUT_MODES),
        "isPhotoAvatar": _get_bool("UI_IS_PHOTO_AVATAR", True),
        "isCustomAvatar": _get_bool("UI_IS_CUSTOM_AVATAR", True),
        "avatarName": _get_str("UI_AVATAR_NAME", "Lisa-casual-sitting"),
        "avatarDisplayName": _get_str("UI_AVATAR_DISPLAY_NAME", ""),
        "photoAvatarName": _get_str("UI_PHOTO_AVATAR_NAME", "Anika"),
        "customAvatarName": _get_str("UI_CUSTOM_AVATAR_NAME", "Nuru"),
        "avatarBackgroundImageUrl": _get_str("UI_AVATAR_BACKGROUND_IMAGE_URL", ""),
        "photoScene": photo_scene,
        "developerMode": _get_bool("UI_DEVELOPER_MODE", False),
    }


def apply_resolver_rules(raw: dict) -> dict:
    """Apply server-side cascading visibility rules to a UI config dict.

    Pure, idempotent. Returns a NEW dict; never mutates the input. Called by
    :func:`get_ui_config` on the raw env dict, and by
    ``websocket._start_session`` on the merged ``{**env, **client}`` dict in
    developer mode (so dev sidebar overrides can't defeat the rules).

    Rules applied here:
      1. ``srModel == "mai-ears-1"`` → force ``recognitionLanguage = "auto"``.
         Matches ``handler.py:150-151`` which already enforces this at
         session-build time; surfacing it here makes ``/api/config`` honest.
      2. Voice type cascade — blank dependent string fields the chosen type
         doesn't use (``standard`` blanks custom + personal; ``custom`` blanks
         personal; ``personal`` blanks custom).
      6. ``isPhotoAvatar == false`` → ``photoScene = {}`` (scene transforms
         only apply to photo avatars).

    Rules NOT applied here (deliberate):
      3. ``avatarEnabled == false`` does NO avatar-field blanking.
         ``build_avatar_config`` already returns ``None`` when avatar is off,
         so all avatar/scene fields are ignored at runtime regardless.
         Blanking ``avatarOutputMode`` to ``""`` would survive a dev-mode
         re-enable because ``config.get("avatarOutputMode", "webrtc")``
         returns the empty string, not the default. Same problem for
         ``avatarName`` in ``builders.py:69``. The single behavioural effect
         of "avatar disabled" lives in rule #4 (validation is gated on
         ``avatarEnabled=true``).
      4. Validation — handled separately by :func:`validate_ui_config` at
         startup so per-request paths never raise.
      5. Effective avatar name precedence (custom > photo > standard) is
         applied in :func:`get_ui_config`'s post-processing step. Putting it
         in the rules would break idempotency on a merged dict where the
         input ``avatarName`` may already be the effective resolved value
         from the previous resolver pass. ``builders.py`` re-applies the
         precedence at runtime so dev-mode merges still produce the right
         SDK character.

    Boolean handling: bool fields stay bool (``True``/``False``); string
    fields blank to ``""``; ``photoScene`` empties to ``{}``. No mixed types
    in the resolved payload.
    """
    out = dict(raw)

    # Rule 1: SR model mai-ears-1 ignores any language override.
    if out.get("srModel") == "mai-ears-1":
        out["recognitionLanguage"] = "auto"

    # Rule 2: voice type cascade — blank dependent string fields.
    voice_type = out.get("voiceType", "standard")
    if voice_type == "standard":
        out["voiceDeploymentId"] = ""
        out["customVoiceName"] = ""
        out["personalVoiceName"] = ""
        out["personalVoiceModel"] = ""
    elif voice_type == "custom":
        out["personalVoiceName"] = ""
        out["personalVoiceModel"] = ""
    elif voice_type == "personal":
        out["voiceDeploymentId"] = ""
        out["customVoiceName"] = ""

    # Rule 6: scene transforms only meaningful for photo avatars.
    if not out.get("isPhotoAvatar"):
        out["photoScene"] = {}

    return out


def validate_ui_config(config: dict) -> None:
    """Validate the resolved UI config; raise ``RuntimeError`` on violations.

    Called from the FastAPI lifespan at startup so misconfigurations refuse
    to boot rather than silently degrading at session-start time. Lifespan
    failure → uvicorn refuses to start → Container App revision goes
    unhealthy → rollback to the previous good revision. Intentionally loud.

    Rule 4: ``isCustomAvatar=true`` requires a non-empty
    ``customAvatarName``. Gated on ``avatarEnabled=true`` — a user who has
    turned the avatar off entirely can leave the custom-name field empty
    without blocking startup.
    """
    errors = []
    if config.get("avatarEnabled") and config.get("isCustomAvatar"):
        custom_name = (config.get("customAvatarName") or "").strip()
        if not custom_name:
            errors.append(
                "UI_IS_CUSTOM_AVATAR=true requires UI_CUSTOM_AVATAR_NAME to be set "
                'to the trained custom-avatar character name (e.g. "Nuru"). '
                "Either populate the env var or set UI_IS_CUSTOM_AVATAR=false."
            )
    if errors:
        raise RuntimeError(
            "Invalid UI configuration:\n  - " + "\n  - ".join(errors)
        )


def get_ui_config() -> dict:
    """Resolve UI behaviour from ``UI_*`` env vars into the canonical
    camelCase dict the frontend + handler/builders consume.

    Build pipeline:
      1. :func:`_build_raw_ui_config` — read every env var with defaults.
      2. :func:`apply_resolver_rules` — cascading visibility rules.
      3. Post-process — compute effective ``avatarName`` (custom > photo >
         standard precedence) and derive ``avatarDisplayName`` if not
         explicitly set. ``builders.py`` re-applies precedence at runtime so
         dev-mode merges that touch only some of the avatar fields still
         produce the right SDK character.

    Recomputed on every call so test harnesses / ``azd env set`` + restart
    flows pick up changes without a code reload.
    """
    raw = _build_raw_ui_config()
    resolved = apply_resolver_rules(raw)

    # Effective avatar name (mirror frontend gatherConfig() precedence:
    # custom > photo > standard). This overwrites the raw preset in the
    # output so the frontend, the SDK builders, and the display caption all
    # see the same SDK-ready character name.
    is_custom = bool(resolved.get("isCustomAvatar"))
    is_photo = bool(resolved.get("isPhotoAvatar"))
    avatar_name_preset = resolved.get("avatarName", "") or "Lisa-casual-sitting"
    custom_name = resolved.get("customAvatarName", "") or ""
    photo_name = resolved.get("photoAvatarName", "") or ""
    if is_custom:
        effective_avatar_name = custom_name or avatar_name_preset
    elif is_photo:
        effective_avatar_name = photo_name or avatar_name_preset
    else:
        effective_avatar_name = avatar_name_preset
    resolved["avatarName"] = effective_avatar_name

    # Derive a humanized display caption when UI_AVATAR_DISPLAY_NAME is unset.
    display_raw = (resolved.get("avatarDisplayName") or "").strip()
    if not display_raw:
        resolved["avatarDisplayName"] = _derive_display_name(effective_avatar_name)

    return resolved
