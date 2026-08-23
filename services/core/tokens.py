"""LiveKit access tokens.

The patient's token is minted only after the assistant is already in the room,
so there is no window in which a patient sits alone in an empty room.
"""

from datetime import timedelta

from livekit import api

from services.core.config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET


def mint_token(room: str, identity: str, *, can_publish: bool) -> str:
    return (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_ttl(timedelta(hours=2))
        .with_grants(
            api.VideoGrants(
                room=room,
                room_join=True,
                can_publish=can_publish,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )
