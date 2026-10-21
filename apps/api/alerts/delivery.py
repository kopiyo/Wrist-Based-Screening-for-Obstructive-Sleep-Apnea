"""Generic, privacy-preserving email/Web Push notification delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from .models import Alert, DeliveryChannel, RuleKind
from .ports import AlertRepository, NotificationProvider, PushDestinationRepository
from .time_rules import in_quiet_hours, next_quiet_hours_end

DISCLAIMER = "Screening tool, not a diagnosis. Research use only."
UNVALIDATED_SOURCE = "Unvalidated source"
MAX_ATTEMPTS_PER_ACCOUNT_HOUR = 20
ALERTS_URL = "/alerts"


@dataclass(frozen=True, slots=True)
class Notification:
    subject: str
    body: str
    url: str = ALERTS_URL


def build_notification(alert: Alert) -> Notification:
    """Build generic text only; never embed score, trend value, or health data."""
    subject = "Nightwise account notification"
    body = "You have a new screening notification. Sign in to review details."
    if alert.source_validated is False:
        body += f" {UNVALIDATED_SOURCE}."
    body += f" {DISCLAIMER}"
    return Notification(subject=subject, body=body)


class AlertDispatcher:
    """Send generic notices while enforcing consent, timezone, and rate limits.

    Repository delivery attempt counting must be atomic with the insert in the
    host adapter (for example a transaction/advisory lock), so concurrent RQ
    workers cannot exceed 20 actual attempts per account per rolling hour.
    """

    def __init__(
        self,
        repository: AlertRepository,
        providers: dict[DeliveryChannel, NotificationProvider],
        push_destinations: PushDestinationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.providers = providers
        self.push_destinations = push_destinations
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def deliver(self, alert: Alert, channels: Sequence[DeliveryChannel]) -> list[str]:
        now = self._now()
        rule = next((r for r in self.repository.rules_for_user(alert.user_id)
                     if r.kind == alert.kind and r.enabled), None)
        if rule is None:
            return []
        timezone_name = self.repository.user_timezone(alert.user_id)
        if in_quiet_hours(now, timezone_name, rule.quiet_start, rule.quiet_end):
            due_at = next_quiet_hours_end(
                now, timezone_name, rule.quiet_start, rule.quiet_end
            )
            for channel in dict.fromkeys(channels):
                self.repository.mark_delivery_deferred(
                    alert.id, channel, due_at,
                )
            return ["deferred"]

        allowed = [channel for channel in channels if channel in rule.channels]
        email, email_verified = self.repository.user_email(alert.user_id)
        destinations: list[tuple[DeliveryChannel, str]] = []
        for channel in allowed:
            if channel == DeliveryChannel.EMAIL:
                if email and email_verified:
                    destinations.append((channel, email))
            elif channel == DeliveryChannel.WEB_PUSH:
                destinations.extend(
                    (channel, endpoint)
                    for endpoint in self.push_destinations.active_push_destinations(alert.user_id)
                )

        outcomes: list[str] = []
        for channel, destination in destinations:
            attempt_at = self._now()
            attempt_id = self.repository.reserve_delivery_attempt(
                user_id=alert.user_id,
                alert_id=alert.id,
                channel=channel,
                attempted_at=attempt_at,
                max_attempts=MAX_ATTEMPTS_PER_ACCOUNT_HOUR,
                window=timedelta(hours=1),
            )
            if attempt_id is None:
                outcomes.append("rate_limited")
                break
            notification = build_notification(alert)
            try:
                provider = self.providers[channel]
                provider.send(
                    destination=destination,
                    subject=notification.subject,
                    body=notification.body,
                    url=notification.url,
                )
            except Exception as exc:
                # Persist only a stable, sanitized code. Never log exception
                # strings because providers may include addresses/tokens.
                self.repository.complete_delivery_attempt(
                    attempt_id,
                    status="failed",
                    provider_error_code=_safe_error_code(exc),
                )
                outcomes.append("failed")
            else:
                self.repository.complete_delivery_attempt(
                    attempt_id,
                    status="sent",
                )
                outcomes.append("sent")
        return outcomes

    def deliver_due(self, alert: Alert, channels: Sequence[DeliveryChannel]) -> list[str]:
        """Retry hook for a deferred alert; caller invokes at scheduled due time."""
        return self.deliver(alert, channels)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value.astimezone(timezone.utc)


def _safe_error_code(exc: Exception) -> str:
    # Do not serialize exception text. Provider adapters may map known errors
    # to safe enums upstream; unknown failures receive a fixed code.
    name = type(exc).__name__.lower()
    if name in {"timeout", "timeouterror"}:
        return "provider_timeout"
    if name in {"connectionerror", "connectionexception"}:
        return "provider_unavailable"
    return "provider_error"
