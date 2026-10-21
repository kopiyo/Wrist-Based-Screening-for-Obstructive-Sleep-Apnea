"""Deterministic saved-night rule evaluation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from statistics import mean
from typing import Sequence
import uuid
from zoneinfo import ZoneInfo

from .models import Alert, AlertRule, DeliveryChannel, Night, RuleKind, RuleUpdate
from .ports import AlertRepository
from .time_rules import checked_zone, previous_local_date

MIN_TREND_NIGHTS = 6
TREND_WINDOW_DAYS = 30
TREND_DELTA = 0.10


class AlertRuleError(ValueError):
    """Invalid or unavailable alert rule update."""

    status_code = 422


class RuleUnavailable(AlertRuleError):
    status_code = 409


def default_rules(user_id: str) -> tuple[AlertRule, ...]:
    return tuple(
        AlertRule(
            user_id=user_id,
            kind=kind,
            enabled=False,
            channels=(DeliveryChannel.WEB_PUSH,),
        )
        for kind in RuleKind
    )


def validate_rule_update(update: RuleUpdate) -> None:
    kinds = [rule.kind for rule in update.rules]
    if len(kinds) != len(set(kinds)):
        raise AlertRuleError("Each alert rule may appear only once.")
    if any(rule.quiet_start != update.quiet_start or rule.quiet_end != update.quiet_end
           for rule in update.rules):
        raise AlertRuleError("Quiet hours are shared across the user's alert rules.")
    for rule in update.rules:
        if rule.kind == RuleKind.DEVICE_SILENT_OVERNIGHT and rule.enabled:
            raise RuleUnavailable(
                "device_silent_overnight is disabled: needs live device until Phase 3"
            )
        if not rule.channels:
            raise AlertRuleError("At least one delivery channel is required.")


class AlertRuleEngine:
    """Evaluate only persisted, consented nights and create idempotent alerts."""

    def __init__(self, repository: AlertRepository):
        self.repository = repository

    def update_rules(self, user_id: str, update: RuleUpdate) -> tuple[AlertRule, ...]:
        validate_rule_update(update)
        zone_name = self.repository.user_timezone(user_id)
        checked_zone(zone_name)
        normalized = tuple(
            replace(rule, user_id=user_id) for rule in update.rules
        )
        self.repository.replace_rules(user_id, normalized)
        return normalized

    def on_saved_night(self, night: Night, now: datetime | None = None) -> list[Alert]:
        """Run immediately after a consented night is committed to storage."""
        if not night.saved_with_consent:
            return []
        now = _aware_utc(now or datetime.now(timezone.utc))
        rules = {r.kind: r for r in self.repository.rules_for_user(night.user_id)}
        generated: list[Alert] = []
        screening = rules.get(RuleKind.SCREENING_RESULT)
        if screening and screening.enabled:
            generated.extend(self._create(
                night.user_id, screening, night, f"screening_result:{night.id}", now
            ))

        trend = rules.get(RuleKind.WORSENING_TREND)
        if trend and trend.enabled:
            nights = self._recent_saved_nights(night.user_id, now)
            if len(nights) >= MIN_TREND_NIGHTS and nights[-1].id == night.id and self._worsening(nights):
                generated.extend(self._create(
                    night.user_id, trend, night, f"worsening_trend:{night.id}", now
                ))
        return generated

    def evaluate_morning_summaries(self, now: datetime | None = None) -> list[Alert]:
        """Scheduler hook; call periodically, idempotency limits one/day/user."""
        now = _aware_utc(now or datetime.now(timezone.utc))
        # This port intentionally returns active users via an optional adapter
        # method, avoiding a hard ORM dependency in the alert package.
        users = getattr(self.repository, "users_with_enabled_rule", None)
        if users is None:
            return []
        results: list[Alert] = []
        for user_id in users(RuleKind.MORNING_SUMMARY):
            zone_name = self.repository.user_timezone(user_id)
            local_now = now.astimezone(checked_zone(zone_name))
            # The daily job runs at 07:00 local. If invoked early, leave the
            # previous date's summary for the next scheduler tick.
            if (local_now.hour, local_now.minute) < (7, 0):
                continue
            local_yesterday = previous_local_date(now, zone_name)
            rule = next((r for r in self.repository.rules_for_user(user_id)
                         if r.kind == RuleKind.MORNING_SUMMARY and r.enabled), None)
            if rule is None:
                continue
            day_nights = [n for n in self._recent_saved_nights(user_id, now)
                          if n.started_at.astimezone(checked_zone(zone_name)).date() == local_yesterday]
            if not day_nights:
                continue
            results.extend(self._create(
                user_id, rule, day_nights[-1],
                f"morning_summary:{local_yesterday.isoformat()}", now,
            ))
        return results

    def _recent_saved_nights(self, user_id: str, now: datetime) -> list[Night]:
        since = now - timedelta(days=TREND_WINDOW_DAYS)
        rows = [n for n in self.repository.nights_since(user_id, since)
                if n.user_id == user_id and n.saved_with_consent and _aware_utc(n.started_at) >= since]
        return sorted(rows, key=lambda n: (n.started_at, n.id))

    @staticmethod
    def _worsening(nights: Sequence[Night]) -> bool:
        # Exactly the newest six nights are split into chronological groups:
        # oldest three form baseline, newest three form comparison. Additional
        # earlier nights in the 30-day interval do not alter the comparison.
        six = nights[-MIN_TREND_NIGHTS:]
        baseline = mean(n.score for n in six[:3])
        newest = mean(n.score for n in six[3:])
        # Round to absorb float error so an exact +0.10 rise (0.2 -> 0.3) alerts.
        return round(newest - baseline, 10) >= TREND_DELTA

    def _create(
        self, user_id: str, rule: AlertRule, night: Night,
        idempotency_key: str, now: datetime,
    ) -> list[Alert]:
        if self.repository.has_alert(user_id, idempotency_key):
            return []
        alert = Alert(
            # Stable alert UUID makes repository insert-on-conflict idempotent
            # even if two worker executions race after has_alert().
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"nightwise:{user_id}:{idempotency_key}")),
            user_id=user_id,
            kind=rule.kind,
            night_id=night.id,
            created_at=now,
            source_validated=night.source_validated,
            detail_ref=night.id,
            metadata={"idempotency_key": idempotency_key},
        )
        return [self.repository.create_alert(alert)]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime values must be timezone-aware")
    return value.astimezone(timezone.utc)
