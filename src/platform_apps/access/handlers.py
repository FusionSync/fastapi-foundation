from __future__ import annotations

from core.events import EventEnvelope, get_current_event_dispatch_session
from core.exceptions import AppError
from core.permissions import PolicyProjector


async def handle_role_grant_changed(envelope: EventEnvelope) -> None:
    session = get_current_event_dispatch_session()
    if session is None:
        raise AppError(
            "SYSTEM_ERROR",
            "Role grant projection handler requires an outbox dispatch session",
            status_code=500,
        )
    await PolicyProjector(session).handle_role_grant_changed(envelope)
