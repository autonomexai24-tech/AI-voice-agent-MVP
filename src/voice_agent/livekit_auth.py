from __future__ import annotations

try:
    from livekit.api import AccessToken, VideoGrants
except ImportError:  # pragma: no cover - compatibility with alternate SDK exports.
    from livekit import api

    AccessToken = api.AccessToken
    VideoGrants = api.VideoGrants

from voice_agent.config import LiveKitConfig


def build_livekit_token(config: LiveKitConfig) -> str:
    return (
        AccessToken(config.api_key, config.api_secret)
        .with_identity(config.identity)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=config.room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .to_jwt()
    )
