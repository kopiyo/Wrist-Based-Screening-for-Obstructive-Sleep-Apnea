from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from apps.api.alerts.engine import AlertRuleEngine, MIN_TREND_NIGHTS
from apps.api.alerts.delivery import AlertDispatcher, build_notification
from apps.api.alerts.models import Alert, AlertRule, DeliveryChannel, Night, RuleKind, RuleUpdate
from apps.api.alerts.time_rules import in_quiet_hours, next_quiet_hours_end


class MemoryAlertRepository:
    def __init__(self, timezone_name: str = "UTC") -> None:
        self.timezone_name = timezone_name
        self.rules: list[AlertRule] = []
        self.nights: list[Night] = []
        self.alerts: list[Alert] = []

    def rules_for_user(self, user_id: str):
        return [rule for rule in self.rules if rule.user_id == user_id]

    def nights_since(self, user_id: str, since: datetime):
        return [night for night in self.nights
                if night.user_id == user_id and night.started_at >= since]

    def user_timezone(self, user_id: str) -> str:
        return self.timezone_name

    def has_alert(self, user_id: str, idempotency_key: str) -> bool:
        return any(alert.user_id == user_id and
                   alert.metadata.get("idempotency_key") == idempotency_key
                   for alert in self.alerts)

    def create_alert(self, alert: Alert) -> Alert:
        self.alerts.append(alert)
        return alert

    def replace_rules(self, user_id: str, rules):
        self.rules = [rule for rule in self.rules if rule.user_id != user_id]
        self.rules.extend(rules)


def make_night(index: int, score: float, *, saved: bool = True,
               source_validated: bool = False,
               started_at: datetime | None = None) -> Night:
    return Night(
        id=f"night-{index}", user_id="user-1",
        started_at=started_at or datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index),
        source="watch_rr", source_validated=source_validated,
        model_version="model-v1", score=score, saved_with_consent=saved,
    )


def test_worsening_trend_compares_newest_three_to_previous_three_and_is_idempotent():
    repo = MemoryAlertRepository()
    rule = AlertRule(user_id="user-1", kind=RuleKind.WORSENING_TREND,
                     enabled=True, channels=(DeliveryChannel.WEB_PUSH,))
    repo.rules = [rule]
    repo.nights = [make_night(i, 0.2 if i < 3 else 0.31) for i in range(6)]
    engine = AlertRuleEngine(repo)

    generated = engine.on_saved_night(repo.nights[-1], now=datetime(2026, 1, 7, tzinfo=timezone.utc))
    repeated = engine.on_saved_night(repo.nights[-1], now=datetime(2026, 1, 7, tzinfo=timezone.utc))

    assert len(generated) == 1
    assert generated[0].kind == RuleKind.WORSENING_TREND
    assert repeated == []
    assert len(repo.alerts) == 1


@pytest.mark.parametrize("scores", [
    [0.2, 0.2, 0.2, 0.3, 0.3, 0.299],  # below +0.10
    [0.2, 0.2, 0.2, 0.3, 0.3],         # fewer than six nights
])
def test_worsening_trend_requires_six_nights_and_at_least_ten_percentage_points(scores):
    repo = MemoryAlertRepository()
    repo.rules = [AlertRule(user_id="user-1", kind=RuleKind.WORSENING_TREND, enabled=True)]
    repo.nights = [make_night(i, score) for i, score in enumerate(scores)]

    generated = AlertRuleEngine(repo).on_saved_night(
        repo.nights[-1], now=datetime(2026, 2, 1, tzinfo=timezone.utc)
    )

    assert MIN_TREND_NIGHTS == 6
    assert generated == []


def test_worsening_trend_alerts_on_exact_ten_point_rise():
    repo = MemoryAlertRepository()
    repo.rules = [AlertRule(user_id="user-1", kind=RuleKind.WORSENING_TREND, enabled=True)]
    repo.nights = [make_night(i, 0.2 if i < 3 else 0.3) for i in range(6)]

    generated = AlertRuleEngine(repo).on_saved_night(
        repo.nights[-1], now=datetime(2026, 1, 7, tzinfo=timezone.utc)
    )

    assert [alert.kind for alert in generated] == [RuleKind.WORSENING_TREND]


def test_unsaved_analysis_never_creates_an_alert():
    repo = MemoryAlertRepository()
    repo.rules = [AlertRule(user_id="user-1", kind=RuleKind.SCREENING_RESULT, enabled=True)]
    unsaved = make_night(1, 0.8, saved=False)

    assert AlertRuleEngine(repo).on_saved_night(unsaved) == []
    assert repo.alerts == []


def test_morning_summary_uses_previous_local_calendar_date_and_waits_until_seven():
    repo = MemoryAlertRepository("Africa/Nairobi")
    repo.rules = [AlertRule(user_id="user-1", kind=RuleKind.MORNING_SUMMARY, enabled=True)]
    # 2026-01-02 22:00 Nairobi is on the prior local date (2026-01-02).
    night = make_night(1, 0.4, started_at=datetime(2026, 1, 2, 19, 0, tzinfo=timezone.utc))
    repo.nights = [night]
    repo.users_with_enabled_rule = lambda kind: ["user-1"]
    engine = AlertRuleEngine(repo)

    before_due = engine.evaluate_morning_summaries(datetime(2026, 1, 3, 3, 59, tzinfo=timezone.utc))
    due = engine.evaluate_morning_summaries(datetime(2026, 1, 3, 4, 0, tzinfo=timezone.utc))

    assert before_due == []
    assert len(due) == 1
    assert due[0].kind == RuleKind.MORNING_SUMMARY


def test_quiet_hours_use_user_timezone_and_handle_midnight_crossing():
    instant = datetime(2026, 7, 1, 19, 30, tzinfo=timezone.utc)  # 22:30 Nairobi
    assert in_quiet_hours(instant, "Africa/Nairobi", time(22), time(7))
    assert next_quiet_hours_end(instant, "Africa/Nairobi", time(22), time(7)) == datetime(
        2026, 7, 2, 4, 0, tzinfo=timezone.utc
    )
    assert not in_quiet_hours(instant, "Africa/Nairobi", time(7), time(7))


def test_device_silent_rule_cannot_be_enabled_in_phase_two():
    repo = MemoryAlertRepository()
    update = RuleUpdate(
        rules=(AlertRule(user_id="user-1", kind=RuleKind.DEVICE_SILENT_OVERNIGHT,
                         enabled=True),),
        quiet_start=time(22), quiet_end=time(7),
    )

    with pytest.raises(ValueError, match="needs live device until Phase 3"):
        AlertRuleEngine(repo).update_rules("user-1", update)


class DeliveryRepository(MemoryAlertRepository):
    def __init__(self, *, verified: bool = True, timezone_name: str = "UTC"):
        super().__init__(timezone_name)
        self.verified = verified
        self.attempts = []
        self.completed = []
        self.deferred = []

    def user_email(self, user_id: str):
        return "person@example.invalid", self.verified

    def reserve_delivery_attempt(self, **kwargs):
        self.attempts.append(kwargs)
        return f"attempt-{len(self.attempts)}"

    def complete_delivery_attempt(self, attempt_id: str, *, status: str,
                                  provider_error_code: str | None = None):
        self.completed.append((attempt_id, status, provider_error_code))

    def mark_delivery_deferred(self, alert_id, channel, due_at):
        self.deferred.append((alert_id, channel, due_at))


class RecordingProvider:
    def __init__(self):
        self.messages = []

    def send(self, **message):
        self.messages.append(message)


class EmptyPushDestinations:
    def active_push_destinations(self, user_id: str):
        return []


def test_notification_is_generic_includes_disclaimer_and_unvalidated_source():
    alert = Alert(
        id="alert-1", user_id="user-1", kind=RuleKind.SCREENING_RESULT,
        night_id="night-1", created_at=datetime.now(timezone.utc),
        source_validated=False, metadata={"score": 0.99},
    )

    notice = build_notification(alert)

    assert "Screening tool, not a diagnosis. Research use only." in notice.body
    assert "Unvalidated source" in notice.body
    assert "0.99" not in notice.body
    assert "score" not in notice.body.lower()


def test_email_delivery_requires_verified_email_and_sends_generic_content():
    repo = DeliveryRepository(verified=False)
    repo.rules = [AlertRule(user_id="user-1", kind=RuleKind.SCREENING_RESULT,
                            enabled=True, channels=(DeliveryChannel.EMAIL,))]
    alert = Alert("alert-1", "user-1", RuleKind.SCREENING_RESULT, "night-1",
                  datetime.now(timezone.utc), False)
    provider = RecordingProvider()
    dispatcher = AlertDispatcher(
        repo, {DeliveryChannel.EMAIL: provider}, EmptyPushDestinations(),
        clock=lambda: datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
    )

    outcome = dispatcher.deliver(alert, [DeliveryChannel.EMAIL])

    assert outcome == []
    assert provider.messages == []
    assert repo.attempts == []

    repo.verified = True
    assert dispatcher.deliver(alert, [DeliveryChannel.EMAIL]) == ["sent"]
    assert len(provider.messages) == 1
    body = provider.messages[0]["body"]
    assert "Screening tool, not a diagnosis. Research use only." in body
    assert "Unvalidated source" in body
    assert "score" not in body.lower()


def test_delivery_is_deferred_during_local_quiet_hours():
    repo = DeliveryRepository(verified=True, timezone_name="Africa/Nairobi")
    repo.rules = [AlertRule(user_id="user-1", kind=RuleKind.SCREENING_RESULT,
                            enabled=True, channels=(DeliveryChannel.EMAIL,),
                            quiet_start=time(22), quiet_end=time(7))]
    alert = Alert("alert-1", "user-1", RuleKind.SCREENING_RESULT, "night-1",
                  datetime.now(timezone.utc), True)
    provider = RecordingProvider()
    dispatcher = AlertDispatcher(
        repo, {DeliveryChannel.EMAIL: provider}, EmptyPushDestinations(),
        clock=lambda: datetime(2026, 7, 1, 19, 30, tzinfo=timezone.utc),
    )

    outcome = dispatcher.deliver(alert, [DeliveryChannel.EMAIL])

    assert outcome == ["deferred"]
    assert len(repo.deferred) == 1
    assert repo.attempts == []
    assert provider.messages == []
