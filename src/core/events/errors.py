from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EventHandlerErrorKind = Literal["transient", "permanent"]


@dataclass(frozen=True, slots=True)
class EventHandlerErrorClassification:
    kind: EventHandlerErrorKind
    retryable: bool


class EventHandlerTransientError(Exception):
    """Handler failure that should follow the normal outbox retry policy."""


class EventHandlerPermanentError(Exception):
    """Handler failure that should move directly to dead letter."""


class EventPayloadValidationError(EventHandlerPermanentError):
    """Event payload does not satisfy the registered schema."""


class EventSchemaCompatibilityError(ValueError):
    """Event schema version compatibility declaration is invalid."""


def classify_event_handler_error(error: BaseException) -> EventHandlerErrorClassification:
    if isinstance(error, EventHandlerPermanentError):
        return EventHandlerErrorClassification(kind="permanent", retryable=False)
    return EventHandlerErrorClassification(kind="transient", retryable=True)
