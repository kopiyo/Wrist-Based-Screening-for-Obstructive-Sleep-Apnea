"""Small storage-neutral alert domain objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum
from typing import Any


class RuleKind(StrEnum):
    SCREENING_RESULT = "screening_result"
    WORSENING_TREND = "worsening_trend"
    MORNING_SUMMARY = "morning_summary"
    DEVICE_SILENT_OVERNIGHT = "device_silent_overnight"


class DeliveryChannel(StrEnum):
    EMAIL = "email"
    WEB_PUSH = "web_push"


@dataclass(frozen=True, slots=True)
class Night:
    """Persisted night metadata used for rule evaluation.

    ``score`` is used only inside the authenticated rule engine and must never
    be copied into an :class:`Alert` notification payload.
    """

    id: str
    user_id: str
    started_at: datetime
    source: str
    source_validated: bool
    model_version: str
    score: float
    saved_with_consent: bool


@dataclass(frozen=True, slots=True)
class AlertRule:
    user_id: str
    kind: RuleKind
    enabled: bool = False
    channels: tuple[DeliveryChannel, ...] = (DeliveryChannel.WEB_PUSH,)
    # Local wall-clock quiet hours. Equal start/end means no quiet period.
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)


@dataclass(frozen=True, slots=True)
class RuleUpdate:
    rules: tuple[AlertRule, ...]
    quiet_start: time
    quiet_end: time


@dataclass(frozen=True, slots=True)
class Alert:
    id: str
    user_id: str
    kind: RuleKind
    night_id: str | None
    created_at: datetime
    source_validated: bool | None
    # Only non-sensitive routing metadata; detail is stored separately and is
    # fetched through authenticated history/alert-detail endpoints.
    detail_ref: str | None = None
    delivery_state: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
