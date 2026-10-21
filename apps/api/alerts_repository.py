"""SQLAlchemy adapters for the storage-neutral alert engine ports."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from .alerts.models import Alert, AlertRule, DeliveryChannel, Night, RuleKind
from .models import (Alert as AlertRow, AlertRule as AlertRuleRow, AlertSourceNight, Consent, DeliveryLog,
                     Night as NightRow, PushSubscription, Result, User)
from .storage import db_session, decrypt_for_user, encrypt_for_user


def _as_domain_rule(row: AlertRuleRow) -> AlertRule:
    return AlertRule(user_id=str(row.user_id), kind=RuleKind(row.kind), enabled=row.enabled,
                     channels=tuple(DeliveryChannel(item) for item in (row.channels or [])),
                     quiet_start=datetime.strptime(row.quiet_start or "22:00", "%H:%M").time(),
                     quiet_end=datetime.strptime(row.quiet_end or "07:00", "%H:%M").time())


class SQLAlertRepository:
    def rules_for_user(self, user_id: str):
        with db_session() as session:
            rows = session.scalars(select(AlertRuleRow).where(AlertRuleRow.user_id == uuid.UUID(user_id)))
            return [_as_domain_rule(row) for row in rows]

    def nights_since(self, user_id: str, since: datetime):
        owner = uuid.UUID(user_id)
        with db_session() as session:
            rows = list(session.scalars(select(NightRow).where(
                NightRow.user_id == owner, NightRow.started_at >= since)))
            result = []
            for row in rows:
                stored = session.scalar(select(Result).where(Result.night_id == row.id))
                if not stored:
                    continue
                payload = json.loads(decrypt_for_user(session, owner, stored.encrypted_payload, stored.nonce))
                consent = session.get(Consent, row.consent_id)
                result.append(Night(
                    id=str(row.id), user_id=str(row.user_id), started_at=row.started_at,
                    source=row.source, source_validated=row.source_validated,
                    model_version=stored.model_version, score=float(payload.get("score", 0.0)),
                    saved_with_consent=consent is not None,
                ))
            return result

    def user_timezone(self, user_id: str) -> str:
        with db_session() as session:
            user = session.get(User, uuid.UUID(user_id))
            return user.timezone if user else "UTC"

    def user_email(self, user_id: str) -> tuple[str | None, bool]:
        with db_session() as session:
            user = session.get(User, uuid.UUID(user_id))
            return (user.email, user.email_verified_at is not None) if user else (None, False)

    def create_alert(self, alert: Alert) -> Alert:
        owner = uuid.UUID(alert.user_id)
        with db_session() as session:
            existing = session.get(AlertRow, uuid.UUID(alert.id))
            if existing:
                return alert
            row = AlertRow(id=uuid.UUID(alert.id), user_id=owner,
                           night_id=uuid.UUID(alert.night_id) if alert.night_id else None,
                           rule_kind=alert.kind.value,
                           idempotency_key=str(alert.metadata.get("idempotency_key", alert.id)),
                           status=alert.delivery_state)
            session.add(row)
            source_nights: list[uuid.UUID] = []
            if alert.night_id:
                source_nights = [uuid.UUID(alert.night_id)]
            idempotency = str(alert.metadata.get("idempotency_key", ""))
            if alert.kind == RuleKind.WORSENING_TREND:
                source_nights = list(session.scalars(select(NightRow.id).where(
                    NightRow.user_id == owner,
                    NightRow.started_at >= alert.created_at - timedelta(days=30))
                    .order_by(NightRow.started_at.desc()).limit(6)))
            elif alert.kind == RuleKind.MORNING_SUMMARY and ":" in idempotency:
                user = session.get(User, owner)
                local_day = datetime.fromisoformat(idempotency.split(":", 1)[1]).date()
                zone = ZoneInfo(user.timezone if user else "UTC")
                local_start = datetime.combine(local_day, datetime.min.time(), zone)
                local_end = datetime.combine(local_day + timedelta(days=1), datetime.min.time(), zone)
                source_nights = list(session.scalars(select(NightRow.id).where(
                    NightRow.user_id == owner,
                    NightRow.started_at >= local_start.astimezone(timezone.utc),
                    NightRow.started_at < local_end.astimezone(timezone.utc))))
            source_rows = list(session.scalars(select(NightRow).where(
                NightRow.id.in_(source_nights)))) if source_nights else []
            # A combined notice is conservatively unvalidated if any night that
            # contributed to it came from an unvalidated source.
            aggregate_validated = (all(night.source_validated for night in source_rows)
                                   if source_rows else alert.source_validated)
            row.source_validated = aggregate_validated
            session.add_all([AlertSourceNight(alert_id=row.id, night_id=night_id)
                             for night_id in dict.fromkeys(source_nights)])
            from dataclasses import replace
            return replace(alert, source_validated=aggregate_validated)

    def has_alert(self, user_id: str, idempotency_key: str) -> bool:
        with db_session() as session:
            return bool(session.scalar(select(AlertRow.id).where(
                AlertRow.user_id == uuid.UUID(user_id),
                AlertRow.idempotency_key == idempotency_key)))

    def mark_delivery_deferred(self, alert_id: str, channel: DeliveryChannel,
                               due_at: datetime) -> None:
        with db_session() as session:
            session.add(DeliveryLog(alert_id=uuid.UUID(alert_id), channel=channel.value,
                                    status="deferred", due_at=due_at))

    def reserve_delivery_attempt(self, *, user_id: str, alert_id: str,
                                 channel: DeliveryChannel, attempted_at: datetime,
                                 max_attempts: int, window: timedelta) -> str | None:
        owner = uuid.UUID(user_id)
        with db_session() as session:
            # PostgreSQL advisory lock makes rolling-hour count and reservation atomic.
            if session.bind and session.bind.dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                                {"lock_id": owner.int & 0x7fffffffffffffff})
            count = session.scalar(select(func.count(DeliveryLog.id)).join(
                AlertRow, AlertRow.id == DeliveryLog.alert_id).where(
                    AlertRow.user_id == owner,
                    DeliveryLog.attempted_at >= attempted_at - window,
                    DeliveryLog.status.in_(["pending", "sent", "failed"]))) or 0
            if count >= max_attempts:
                return None
            attempt = DeliveryLog(alert_id=uuid.UUID(alert_id), channel=channel.value,
                                  attempted_at=attempted_at, status="pending")
            session.add(attempt)
            session.flush()
            return str(attempt.id)

    def complete_delivery_attempt(self, attempt_id: str, *, status: str,
                                  provider_error_code: str | None = None) -> None:
        with db_session() as session:
            row = session.get(DeliveryLog, uuid.UUID(attempt_id))
            if row:
                row.status = status
                row.provider_error_code = provider_error_code

    def replace_rules(self, user_id: str, rules) -> None:
        owner = uuid.UUID(user_id)
        with db_session() as session:
            session.query(AlertRuleRow).filter(AlertRuleRow.user_id == owner).delete(synchronize_session=False)
            for rule in rules:
                session.add(AlertRuleRow(
                    user_id=owner, kind=rule.kind.value, enabled=rule.enabled,
                    channels=[channel.value for channel in rule.channels], config={},
                    quiet_start=rule.quiet_start.strftime("%H:%M"),
                    quiet_end=rule.quiet_end.strftime("%H:%M")))

    def users_with_enabled_rule(self, kind: RuleKind):
        with db_session() as session:
            return [str(value) for value in session.scalars(select(AlertRuleRow.user_id).where(
                AlertRuleRow.kind == kind.value, AlertRuleRow.enabled.is_(True)))]


class SQLPushDestinationRepository:
    def active_push_destinations(self, user_id: str):
        owner = uuid.UUID(user_id)
        with db_session() as session:
            rows = session.scalars(select(PushSubscription).where(
                PushSubscription.user_id == owner, PushSubscription.revoked_at.is_(None)))
            destinations = []
            for row in rows:
                payload = json.loads(decrypt_for_user(session, owner, row.encrypted_subscription, row.nonce))
                if payload.get("endpoint"):
                    destinations.append(json.dumps(payload, separators=(",", ":")))
            return destinations


class SQLAlertDetailRepository:
    def authenticated_detail(self, user_id: str, alert_id: str) -> dict:
        owner = uuid.UUID(user_id)
        with db_session() as session:
            alert = session.scalar(select(AlertRow).where(
                AlertRow.id == uuid.UUID(alert_id), AlertRow.user_id == owner))
            if not alert:
                raise KeyError("alert not found")
            detail: dict = {"id": str(alert.id), "kind": alert.rule_kind,
                            "created_at": alert.created_at.isoformat(),
                            "status": alert.status, "night_id": str(alert.night_id) if alert.night_id else None}
            source_rows = list(session.execute(select(NightRow).join(
                AlertSourceNight, AlertSourceNight.night_id == NightRow.id).where(
                    AlertSourceNight.alert_id == alert.id)).scalars())
            if not source_rows and alert.night_id:
                night = session.get(NightRow, alert.night_id)
                source_rows = [night] if night else []
            detail["source_validated"] = (all(n.source_validated for n in source_rows)
                                          if source_rows else alert.source_validated)
            detail["sources"] = sorted({n.source for n in source_rows})
            detail["source_nights"] = [{"night_id": str(n.id),
                                        "started_at": n.started_at.isoformat(),
                                        "source": n.source,
                                        "source_validated": n.source_validated}
                                       for n in source_rows]
            if alert.night_id:
                night = session.get(NightRow, alert.night_id)
                result = session.scalar(select(Result).where(Result.night_id == alert.night_id))
                if night and result:
                    payload = json.loads(decrypt_for_user(session, owner, result.encrypted_payload, result.nonce))
                    detail["detail"] = {"score": payload.get("score"),
                                        "apnea_burden_percent": payload.get("apnea_burden_percent"),
                                        "source": night.source,
                                        "source_validated": detail["source_validated"],
                                        "model_version": result.model_version}
            return detail
