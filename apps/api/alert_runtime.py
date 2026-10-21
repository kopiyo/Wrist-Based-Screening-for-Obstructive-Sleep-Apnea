"""Configured delivery adapters and scheduler tick for the isolated alert engine."""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import uuid
from email.message import EmailMessage

from .alerts.delivery import AlertDispatcher
from .alerts.models import DeliveryChannel
from .alerts_repository import SQLAlertRepository, SQLPushDestinationRepository


class SMTPProvider:
    def send(self, *, destination: str, subject: str, body: str, url: str) -> None:
        from urllib.parse import urlparse
        configured = os.getenv("SMTP_URL")
        if not configured:
            raise RuntimeError("email provider unavailable")
        parsed = urlparse(configured)
        use_starttls = parsed.scheme == "smtp+starttls" or os.getenv("SMTP_STARTTLS") == "1"
        if os.getenv("ENVIRONMENT") == "production" and parsed.scheme != "smtps" and not use_starttls:
            raise RuntimeError("email provider unavailable")
        message = EmailMessage()
        message["From"] = os.getenv("MAIL_FROM", "Nightwise <no-reply@localhost>")
        message["To"] = destination
        message["Subject"] = subject
        message.set_content(f"{body}\n\nSign in: {os.getenv('WEB_PUBLIC_URL', 'http://localhost:8080')}{url}")
        smtp_class = smtplib.SMTP_SSL if parsed.scheme == "smtps" else smtplib.SMTP
        if smtp_class is smtplib.SMTP_SSL:
            smtp = smtp_class(parsed.hostname or "localhost", parsed.port or 465,
                              timeout=8, context=ssl.create_default_context())
        else:
            smtp = smtp_class(parsed.hostname or "localhost", parsed.port or 1025, timeout=8)
        with smtp:
            if smtp_class is smtplib.SMTP and use_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if parsed.username:
                smtp.login(parsed.username, parsed.password or "")
            smtp.send_message(message)


class WebPushProvider:
    def send(self, *, destination: str, subject: str, body: str, url: str) -> None:
        from pywebpush import webpush
        private_key = os.getenv("VAPID_PRIVATE_KEY", "")
        subject_claim = os.getenv("VAPID_SUBJECT", "")
        if not private_key or not subject_claim:
            raise RuntimeError("web push provider unavailable")
        subscription = json.loads(destination)
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": subject, "body": body,
                             "url": os.getenv("WEB_PUBLIC_URL", "http://localhost:8080") + url},
                            separators=(",", ":")),
            vapid_private_key=private_key,
            vapid_claims={"sub": subject_claim},
        )


class FCMStub:
    """Provider seam reserved for a future self-hosted/configured FCM integration."""
    def send(self, **_: str) -> None:
        raise RuntimeError("fcm_not_configured")


class APNsStub:
    """Provider seam reserved for a future configured APNs integration."""
    def send(self, **_: str) -> None:
        raise RuntimeError("apns_not_configured")


def dispatcher() -> AlertDispatcher:
    providers = {
        DeliveryChannel.EMAIL: SMTPProvider(),
        DeliveryChannel.WEB_PUSH: WebPushProvider(),
    }
    return AlertDispatcher(SQLAlertRepository(), providers, SQLPushDestinationRepository())


def dispatch_created(alerts) -> None:
    repository = SQLAlertRepository()
    service = dispatcher()
    for alert in alerts:
        rule = next((item for item in repository.rules_for_user(alert.user_id)
                     if item.kind == alert.kind and item.enabled), None)
        if rule:
            outcomes = service.deliver(alert, rule.channels)
            _set_alert_status(alert.id, outcomes)


def _set_alert_status(alert_id: str, outcomes: list[str]) -> None:
    from sqlalchemy import update
    from .models import Alert as AlertRow
    from .storage import db_session

    status = ("sent" if "sent" in outcomes else "failed" if "failed" in outcomes
              else "deferred" if "deferred" in outcomes else
              "rate_limited" if "rate_limited" in outcomes else "suppressed")
    with db_session() as session:
        session.execute(update(AlertRow).where(AlertRow.id == uuid.UUID(str(alert_id))).values(status=status))


def scheduler_tick() -> None:
    """Idempotent tick for 07:00 local summaries and due quiet-hour retries."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import or_, select, update
    from .alerts.engine import AlertRuleEngine
    from .alerts.models import Alert as DomainAlert, RuleKind
    from .models import Alert, AlertRule, DeliveryLog
    from .storage import db_session

    repository = SQLAlertRepository()
    engine = AlertRuleEngine(repository)
    dispatch_created(engine.evaluate_morning_summaries(datetime.now(timezone.utc)))

    due: list[tuple[str, str, str, str]] = []
    now = datetime.now(timezone.utc)
    with db_session() as session:
        # Claim due rows with one conditional UPDATE. The five-minute lease
        # permits recovery after a worker dies while preventing two schedulers
        # from sending the same deferred notice concurrently.
        claim = update(DeliveryLog).where(
            DeliveryLog.due_at <= now,
            or_(DeliveryLog.status == "deferred", DeliveryLog.status == "processing"),
        ).values(status="processing", due_at=now + timedelta(minutes=5))
        claimed_ids = list(session.scalars(claim.returning(DeliveryLog.id)))
        if claimed_ids:
            rows = list(session.scalars(select(DeliveryLog).where(DeliveryLog.id.in_(claimed_ids))))
            for delivery in rows:
                alert = session.get(Alert, delivery.alert_id)
                if alert:
                    due.append((str(delivery.id), str(alert.id), alert.rule_kind, delivery.channel))
    for delivery_id, alert_id, kind, channel in due:
        with db_session() as session:
            row = session.get(Alert, alert_id)
            if not row:
                continue
            rule_row = session.scalar(select(AlertRule).where(
                AlertRule.user_id == row.user_id, AlertRule.kind == kind, AlertRule.enabled.is_(True)))
            if not rule_row:
                session.execute(update(DeliveryLog).where(DeliveryLog.id == delivery_id,
                                                          DeliveryLog.status == "processing")
                                .values(status="suppressed", due_at=None))
                continue
            domain = DomainAlert(id=str(row.id), user_id=str(row.user_id),
                                kind=RuleKind(row.rule_kind), night_id=str(row.night_id) if row.night_id else None,
                                created_at=row.created_at,
                                source_validated=row.source_validated)
        outcomes = dispatcher().deliver(domain, [DeliveryChannel(channel)])
        result = outcomes[0] if outcomes else "suppressed"
        final_status = {"sent": "sent", "failed": "failed", "deferred": "retried_deferred",
                        "rate_limited": "rate_limited"}.get(result, "suppressed")
        with db_session() as session:
            session.execute(update(DeliveryLog).where(
                DeliveryLog.id == delivery_id, DeliveryLog.status == "processing"
            ).values(status=final_status, due_at=None))
        _set_alert_status(alert_id, outcomes)
