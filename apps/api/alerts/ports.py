"""Integration ports expected from the API persistence/provider layers."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, Sequence

from .models import Alert, AlertRule, DeliveryChannel, Night, RuleKind


class AlertRepository(Protocol):
    """Storage adapter. Implementations must scope every query to user_id."""

    def rules_for_user(self, user_id: str) -> Sequence[AlertRule]: ...

    def nights_since(self, user_id: str, since: datetime) -> Sequence[Night]:
        """Return persisted nights only; anonymous/ephemeral analyses excluded."""

    def user_timezone(self, user_id: str) -> str: ...

    def user_email(self, user_id: str) -> tuple[str | None, bool]:
        """Return email and email_verified status."""

    def create_alert(self, alert: Alert) -> Alert:
        """Persist an idempotent alert; duplicate idempotency keys are ignored."""

    def has_alert(self, user_id: str, idempotency_key: str) -> bool: ...

    def mark_delivery_deferred(
        self, alert_id: str, channel: DeliveryChannel, due_at: datetime
    ) -> None: ...

    def reserve_delivery_attempt(
        self,
        *,
        user_id: str,
        alert_id: str,
        channel: DeliveryChannel,
        attempted_at: datetime,
        max_attempts: int,
        window: timedelta,
    ) -> str | None:
        """Atomically reserve a rolling-hour slot and create a pending log row.

        Returns an opaque attempt ID, or None when the limit is exhausted.
        The insert counts the attempt before contacting the provider, avoiding
        concurrent workers exceeding the configured account limit.
        """

    def complete_delivery_attempt(
        self, attempt_id: str, *, status: str, provider_error_code: str | None = None
    ) -> None:
        """Complete a reserved attempt; store no destination or payload."""

    def replace_rules(self, user_id: str, rules: Sequence[AlertRule]) -> None: ...

    def users_with_enabled_rule(self, kind: RuleKind) -> Sequence[str]:
        """Return IDs only; scheduler must not fetch or log user email/health data."""


class NotificationProvider(Protocol):
    """Email or Web Push sender; implementations must not log payload/secrets."""

    def send(self, *, destination: str, subject: str, body: str, url: str) -> None:
        """Raise a provider exception on failure; never include health details."""


class ProviderRegistry(Protocol):
    def provider(self, channel: DeliveryChannel) -> NotificationProvider: ...


class PushDestinationRepository(Protocol):
    def active_push_destinations(self, user_id: str) -> Sequence[str]: ...


class AlertDetailRepository(Protocol):
    def authenticated_detail(self, user_id: str, alert_id: str) -> dict: ...
