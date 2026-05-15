"""Pure builder functions that translate frontend config into SDK objects."""

import logging
import math
import os
from typing import Optional

from azure.ai.voicelive.models import (
    AvatarConfig,
    AzureCustomVoice,
    AzurePersonalVoice,
    AzureSemanticDetection,
    AzureSemanticDetectionEn,
    AzureSemanticDetectionMultilingual,
    AzureSemanticVad,
    AzureStandardVoice,
    Background,
    OpenAIVoice,
    ServerVad,
    VideoCrop,
    VideoParams,
)

logger = logging.getLogger(__name__)


def build_voice_config(config: dict):
    """Build voice configuration from client settings."""
    voice_type = config.get("voiceType", "standard")
    voice_name = config.get("voiceName", os.getenv("VOICELIVE_VOICE", "en-US-AvaMultilingualNeural"))
    voice_temperature = config.get("voiceTemperature", 0.9)
    voice_speed = config.get("voiceSpeed", 1.0)

    if voice_type == "custom":
        custom_voice_name = config.get("customVoiceName", "")
        deployment_id = config.get("voiceDeploymentId", "")
        return AzureCustomVoice(
            name=custom_voice_name,
            endpoint_id=deployment_id,
            rate=str(voice_speed),
        )
    elif voice_type == "personal":
        personal_voice_name = config.get("personalVoiceName", "")
        personal_model = config.get("personalVoiceModel", "DragonLatestNeural")
        return AzurePersonalVoice(
            name=personal_voice_name,
            model=personal_model,
            temperature=voice_temperature,
        )
    else:
        # Standard voice - check if Azure or OpenAI
        if "-" in voice_name:
            # Azure voice
            is_dragon = "Dragon" in voice_name
            return AzureStandardVoice(
                name=voice_name,
                temperature=voice_temperature if is_dragon else None,
                rate=str(voice_speed),
            )
        else:
            # OpenAI voice
            return OpenAIVoice(name=voice_name)

def build_avatar_config(config: dict) -> Optional[AvatarConfig]:
    """Build avatar configuration from client settings."""
    if not config.get("avatarEnabled", False):
        return None

    avatar_name = config.get("avatarName", "Lisa-casual-sitting")
    is_photo = config.get("isPhotoAvatar", False)
    is_custom = config.get("isCustomAvatar", False)
    background_url = config.get("avatarBackgroundImageUrl", "")

    # Parse character and style from avatar name
    if is_custom:
        character = avatar_name
        style = None
    elif is_photo:
        photo_name = config.get("avatarName") or config.get("photoAvatarName") or "Anika"
        parts = photo_name.split("-", 1)
        character = parts[0].lower() if parts else photo_name.lower()
        style = parts[1] if len(parts) > 1 else None
    else:
        parts = avatar_name.split("-", 1)
        character = parts[0].lower() if parts else avatar_name.lower()
        style = parts[1] if len(parts) > 1 else None

    # Build video params
    video_crop = None
    if not is_photo:
        # Centered crop matching JS sample: 800px wide centered in 1920
        video_crop = VideoCrop(top_left=[560, 0], bottom_right=[1360, 1080])

    background = None
    if background_url:
        background = Background(image_url=background_url)

    video = VideoParams(
        codec="h264",
        crop=video_crop,
        background=background,
    )

    # Build avatar config kwargs
    avatar_kwargs = {
        "character": character,
        "style": style,
        "video": video,
    }

    # Only set customized=True when actually custom (omit when False)
    if is_custom:
        avatar_kwargs["customized"] = True

    avatar_cfg = AvatarConfig(**avatar_kwargs)

    # Photo avatar: add type, model, and scene via bracket notation (not in SDK model)
    if is_photo:
        avatar_cfg["type"] = "photo-avatar"
        avatar_cfg["model"] = "vasa-1"
        photo_scene = config.get("photoScene", {})
        if photo_scene:
            import math
            avatar_cfg["scene"] = {
                "zoom": photo_scene.get("zoom", 100) / 100,
                "position_x": photo_scene.get("positionX", 0) / 100,
                "position_y": photo_scene.get("positionY", 0) / 100,
                "rotation_x": photo_scene.get("rotationX", 0) * math.pi / 180,
                "rotation_y": photo_scene.get("rotationY", 0) * math.pi / 180,
                "rotation_z": photo_scene.get("rotationZ", 0) * math.pi / 180,
                "amplitude": photo_scene.get("amplitude", 100) / 100,
            }

    # Add output_protocol (not in SDK model, inject as additional property)
    avatar_output_mode = config.get("avatarOutputMode", "webrtc")
    try:
        avatar_cfg["output_protocol"] = avatar_output_mode
    except Exception:
        try:
            avatar_cfg.output_protocol = avatar_output_mode
        except Exception:
            logger.warning("Could not set output_protocol on AvatarConfig")

    return avatar_cfg

def build_turn_detection(config: dict):
    """Build turn detection configuration."""
    td_type = config.get("turnDetectionType", "server_vad")
    eou_type = config.get("eouDetectionType", "none")
    remove_filler = config.get("removeFillerWords", False)

    if td_type == "azure_semantic_vad":
        eou_detection = None
        if eou_type == "semantic_detection_v1_multilingual":
            eou_detection = AzureSemanticDetectionMultilingual(
                threshold_level="default",
                timeout_ms=1000,
            )
        elif eou_type == "semantic_detection_v1":
            eou_detection = AzureSemanticDetectionEn(
                threshold_level="default",
                timeout_ms=1000,
            )
        return AzureSemanticVad(
            threshold=0.3,
            prefix_padding_ms=300,
            speech_duration_ms=80,
            silence_duration_ms=500,
            remove_filler_words=remove_filler,
            interrupt_response=True,
            end_of_utterance_detection=eou_detection,
        )
    else:
        return ServerVad(
            threshold=0.3,
            prefix_padding_ms=300,
            silence_duration_ms=500,
        )

