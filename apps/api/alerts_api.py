"""Authenticated endpoints for rule configuration and alert history."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from . import auth
from .alerts.engine import AlertRuleEngine, AlertRuleError, RuleUnavailable, default_rules
from .alerts.models import AlertRule, DeliveryChannel, RuleKind, RuleUpdate
from .alerts_repository import SQLAlertRepository
from .auth import current_user
from .models import Alert, DeliveryLog, PushSubscription, User
from .storage import db_session, encrypt_for_user

router = APIRouter(prefix="/v1")
_engine = AlertRuleEngine(SQLAlertRepository())


class RuleEntry(BaseModel):
    kind: RuleKind
    enabled: bool
    # Emptiness is checked by the engine so the client gets a readable message.
    channels: list[DeliveryChannel]


class QuietHours(BaseModel):
    start: str
    end: str


class RuleUpdateBody(BaseModel):
    rules: list[RuleEntry]
    quiet_hours: QuietHours


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionBody(BaseModel):
    endpoint: str
    keys: PushKeys


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "Quiet hours must use HH:MM local time.") from exc


def _rule_response(rule: AlertRule) -> dict[str, Any]:
    unavailable = rule.kind == RuleKind.DEVICE_SILENT_OVERNIGHT
    return {"kind": rule.kind.value, "enabled": False if unavailable else rule.enabled,
            "channels": [channel.value for channel in rule.channels],
            "availability": "needs_live_device" if unavailable else "available",
            "disabled_reason": "needs live device until Phase 3" if unavailable else None}


def _rules_payload(rules: list[AlertRule] | tuple[AlertRule, ...], timezone: str) -> dict[str, Any]:
    first = rules[0] if rules else None
    return {"rules": [_rule_response(rule) for rule in rules],
            "quiet_hours": {"start": first.quiet_start.strftime("%H:%M") if first else "22:00",
                            "end": first.quiet_end.strftime("%H:%M") if first else "07:00",
                            "timezone": timezone}}


@router.get("/alerts/rules")
def get_rules(user: User = Depends(current_user)) -> dict[str, Any]:
    rules = _engine.repository.rules_for_user(str(user.id))
    if not rules:
        rules = list(default_rules(str(user.id)))
    return _rules_payload(rules, user.timezone)


@router.put("/alerts/rules")
def put_rules(body: RuleUpdateBody, request: Request,
              user: User = Depends(current_user)) -> dict[str, Any]:
    auth._check_csrf(request)
    start, end = _parse_time(body.quiet_hours.start), _parse_time(body.quiet_hours.end)
    incoming = tuple(AlertRule(user_id=str(user.id), kind=item.kind, enabled=item.enabled,
                               channels=tuple(item.channels), quiet_start=start, quiet_end=end)
                     for item in body.rules)
    try:
        saved = _engine.update_rules(str(user.id), RuleUpdate(rules=incoming,
                                                              quiet_start=start, quiet_end=end))
    except RuleUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except AlertRuleError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return _rules_payload(saved, user.timezone)


@router.get("/alerts")
def list_alerts(cursor: str | None = None, user: User = Depends(current_user)) -> dict[str, Any]:
    with db_session() as session:
        stmt = select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc()).limit(51)
        if cursor:
            try:
                cursor_id = uuid.UUID(cursor)
            except ValueError as exc:
                raise HTTPException(422, "Invalid cursor.") from exc
            cursor_row = session.scalar(select(Alert).where(Alert.id == cursor_id, Alert.user_id == user.id))
            if not cursor_row:
                raise HTTPException(404, "Alert not found.")
            stmt = stmt.where(Alert.created_at < cursor_row.created_at)
        rows = list(session.scalars(stmt))
        page, more = rows[:50], len(rows) > 50
        items = []
        for row in page:
            item: dict[str, Any] = {"id": str(row.id), "kind": row.rule_kind,
                                    "created_at": row.created_at.isoformat(),
                                    "status": row.status, "night_id": str(row.night_id) if row.night_id else None,
                                    "source_validated": None, "details_available": False}
            from .alerts_repository import SQLAlertDetailRepository
            try:
                authenticated = SQLAlertDetailRepository().authenticated_detail(str(user.id), str(row.id))
                item["source_validated"] = authenticated.get("source_validated")
                item["sources"] = authenticated.get("sources", [])
                item["source_nights"] = authenticated.get("source_nights", [])
                item["detail"] = authenticated.get("detail")
                item["details_available"] = item["detail"] is not None
            except KeyError:
                item["detail"] = None
            items.append(item)
        return {"items": items, "next_cursor": str(page[-1].id) if more and page else None}


@router.get("/alerts/{alert_id}/deliveries")
def list_deliveries(alert_id: uuid.UUID, user: User = Depends(current_user)) -> dict[str, Any]:
    with db_session() as session:
        alert = session.scalar(select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id))
        if not alert:
            raise HTTPException(404, "Alert not found.")
        rows = list(session.scalars(select(DeliveryLog).where(
            DeliveryLog.alert_id == alert_id).order_by(DeliveryLog.attempted_at.desc())))
        return {"items": [{"id": str(row.id), "channel": row.channel,
                           "attempted_at": row.attempted_at.isoformat(),
                           "due_at": row.due_at.isoformat() if row.due_at else None,
                           "status": row.status,
                           "provider_error_code": row.provider_error_code}
                          for row in rows]}


@router.post("/alerts/web-push/subscriptions", status_code=201)
def add_push_subscription(body: PushSubscriptionBody, request: Request,
                          user: User = Depends(current_user)) -> dict[str, str]:
    auth._check_csrf(request)
    serialized = json.dumps({"endpoint": body.endpoint, "keys": body.keys.model_dump()},
                            separators=(",", ":")).encode()
    with db_session() as session:
        encrypted, nonce, key_version = encrypt_for_user(session, user.id, serialized)
        row = PushSubscription(user_id=user.id, encrypted_subscription=encrypted,
                               nonce=nonce, key_version=key_version)
        session.add(row)
        session.flush()
        return {"id": str(row.id)}


@router.delete("/alerts/web-push/subscriptions/{subscription_id}", status_code=204)
def remove_push_subscription(subscription_id: uuid.UUID, request: Request,
                              user: User = Depends(current_user)) -> Response:
    auth._check_csrf(request)
    with db_session() as session:
        row = session.scalar(select(PushSubscription).where(
            PushSubscription.id == subscription_id, PushSubscription.user_id == user.id,
            PushSubscription.revoked_at.is_(None)))
        if not row:
            raise HTTPException(404, "Subscription not found.")
        row.revoked_at = datetime.now().astimezone()
        row.encrypted_subscription = b""
        row.nonce = b""
    return Response(status_code=204)
