"""Passkey accounts, cookie sessions, consent, and privacy endpoints."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from webauthn import (generate_authentication_options, generate_registration_options,
                      options_to_json, verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, ResidentKeyRequirement,
                                      UserVerificationRequirement, PublicKeyCredentialDescriptor)

from . import storage
from .jobs import redis_connection
from .models import (Alert, AlertSourceNight, AuditEvent, Consent, Device, EmailVerificationToken, Night, Passkey,
                     RecoveryCode, RefreshSession, Result, User)
from .storage import db_session

router = APIRouter(prefix="/v1")
DISCLAIMER = "Screening tool, not a diagnosis. Research use only."
CONSENT_VERSION = os.getenv("CONSENT_TEXT_VERSION", "2026-10-v1")
CONSENT_TEXT = os.getenv("CONSENT_TEXT", "I agree to save results from analyses where I explicitly choose Save.")
ACCESS_COOKIE = "nw_access"
REFRESH_COOKIE = "nw_refresh"
CSRF_COOKIE = "nw_csrf"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rp() -> tuple[str, str, str]:
    origin = os.getenv("WEBAUTHN_ORIGIN", "http://localhost:8080")
    rp_id = os.getenv("WEBAUTHN_RP_ID", urlparse(origin).hostname or "localhost")
    return os.getenv("WEBAUTHN_RP_NAME", "Nightwise Screening"), rp_id, origin


def _secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if not secret:
        raise RuntimeError("JWT_SECRET is required.")
    return secret


def _cache_json(key: str, value: dict[str, Any], ttl: int = 600) -> None:
    redis_connection().setex(key, ttl, json.dumps(value, separators=(",", ":")))


def _read_json(key: str) -> dict[str, Any] | None:
    raw = redis_connection().get(key)
    return json.loads(raw) if raw else None


def _consume_json(key: str) -> dict[str, Any] | None:
    client = redis_connection()
    raw = client.getdel(key)
    return json.loads(raw) if raw else None


def _rate_limit(bucket: str, limit: int, seconds: int) -> None:
    client = redis_connection()
    key = f"rl:{bucket}"
    count = client.incr(key)
    if count == 1:
        client.expire(key, seconds)
    if count > limit:
        raise HTTPException(429, "Rate limit exceeded.", headers={"Retry-After": str(client.ttl(key))})


def _email_key(email: str) -> str:
    # Do not put email addresses in Redis keys or logs.
    return hashlib.sha256(email.strip().casefold().encode()).hexdigest()


def _valid_timezone(value: str | None) -> str:
    if not value:
        return "UTC"
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(422, "timezone must be a valid IANA timezone.") from exc
    return value


def _audit(session: Session, user_id: uuid.UUID | None, kind: str, outcome: str,
           request: Request | None = None) -> None:
    # Only a random request identifier is retained; no address, email or payload.
    session.add(AuditEvent(user_id=user_id, kind=kind, outcome=outcome,
                           request_id=str(uuid.uuid4())))


def _new_session(session: Session, user: User, response: Response, *, level: str = "full") -> None:
    now = _now()
    access = jwt.encode({"sub": str(user.id), "iat": now, "exp": now + timedelta(minutes=10), "typ": "access", "level": level}, _secret(), algorithm="HS256")
    raw_refresh = secrets.token_urlsafe(48)
    row = RefreshSession(user_id=user.id, family_id=uuid.uuid4(), token_hash=hashlib.sha256(raw_refresh.encode()).hexdigest(), expires_at=now + timedelta(days=30))
    session.add(row)
    secure = os.getenv("ENVIRONMENT", "development") == "production"
    response.set_cookie(ACCESS_COOKIE, access, httponly=True, secure=secure, samesite="lax", max_age=600, path="/")
    response.set_cookie(REFRESH_COOKIE, raw_refresh, httponly=True, secure=secure, samesite="lax", max_age=30 * 86400, path="/v1/auth")


def _decode_access(request: Request) -> uuid.UUID:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(401, "Authentication required.")
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
        if payload.get("typ") != "access":
            raise ValueError("wrong token type")
        request.state.auth_level = payload.get("level", "full")
        return uuid.UUID(payload["sub"])
    except Exception as exc:
        raise HTTPException(401, "Authentication required.") from exc


def _check_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("x-csrf-token")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(403, "CSRF validation failed.")
    origin = request.headers.get("origin")
    allowed = {x.strip().rstrip("/") for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",")}
    if origin and origin.rstrip("/") not in allowed:
        raise HTTPException(403, "Origin is not allowed.")


def current_user(request: Request) -> User:
    user_id = _decode_access(request)
    if getattr(request.state, "auth_level", "full") == "recovery" and request.url.path not in {
        "/v1/auth/passkeys/options", "/v1/auth/passkeys"
    }:
        raise HTTPException(403, "Recovery session must add a passkey before account access.")
    with db_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(401, "Authentication required.")
        # Return a detached snapshot; route handlers needing mutation open their own session.
        session.expunge(user)
        return user


class RegisterOptions(BaseModel):
    email: EmailStr
    timezone: str = "UTC"


class CredentialProof(BaseModel):
    challenge_id: uuid.UUID
    credential: dict[str, Any]
    label: str | None = Field(default=None, max_length=80)


class LoginOptions(BaseModel):
    email: EmailStr


class EmailVerificationRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class ConsentRequest(BaseModel):
    text_version: str
    scope: str
    affirmative: bool


class RecoveryRequest(BaseModel):
    email: EmailStr
    code: str


class ReauthRequest(BaseModel):
    purpose: str


class DeviceCreate(BaseModel):
    source: str
    label: str | None = Field(default=None, max_length=80)


def _passkey_descriptors(session: Session, user_id: uuid.UUID) -> list[PublicKeyCredentialDescriptor]:
    return [PublicKeyCredentialDescriptor(id=row.credential_id) for row in session.scalars(select(Passkey).where(Passkey.user_id == user_id))]


def _send_verification(email: str, token: str) -> None:
    smtp_url = os.getenv("SMTP_URL")
    if not smtp_url:
        # Do not log a link or token. In production absence of SMTP is a hard failure.
        if os.getenv("ENVIRONMENT", "development") == "production":
            raise RuntimeError("SMTP_URL is required to deliver email verification.")
        return
    parsed = urlparse(smtp_url)
    use_starttls = parsed.scheme == "smtp+starttls" or os.getenv("SMTP_STARTTLS") == "1"
    if os.getenv("ENVIRONMENT") == "production" and parsed.scheme != "smtps" and not use_starttls:
        raise RuntimeError("Production SMTP must use TLS.")
    message = EmailMessage()
    message["From"] = os.getenv("MAIL_FROM", "Nightwise <no-reply@localhost>")
    message["To"] = email
    message["Subject"] = "Verify your Nightwise email"
    frontend = os.getenv("WEB_PUBLIC_URL", os.getenv("WEBAUTHN_ORIGIN", "http://localhost:8080")).rstrip("/")
    message.set_content(f"Verify your address: {frontend}/#verify-email={token}\nThis link expires in 24 hours.")
    smtp_class = smtplib.SMTP_SSL if parsed.scheme == "smtps" else smtplib.SMTP
    context = ssl.create_default_context() if smtp_class is smtplib.SMTP_SSL else None
    with smtp_class(parsed.hostname or "localhost", parsed.port or (465 if context else 1025),
                    timeout=5, context=context) if context else smtp_class(
                        parsed.hostname or "localhost", parsed.port or 1025, timeout=5) as smtp:
        if not context and use_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if parsed.username:
            smtp.login(parsed.username, parsed.password or "")
        smtp.send_message(message)


@router.get("/auth/csrf")
def csrf(response: Response) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    secure = os.getenv("ENVIRONMENT", "development") == "production"
    response.set_cookie(CSRF_COOKIE, token, httponly=False, secure=secure, samesite="lax", max_age=3600, path="/")
    return {"csrf_token": token}


@router.post("/auth/register/options")
def register_options(body: RegisterOptions, request: Request) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    _rate_limit(f"register-ip:{hashlib.sha256(client_ip.encode()).hexdigest()}", 5, 3600)
    email = str(body.email).strip().casefold()
    timezone = _valid_timezone(body.timezone)
    with db_session() as session:
        if session.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(409, "An account already exists for this email.")
    rp_name, rp_id, _ = _rp()
    challenge_id = uuid.uuid4()
    options = generate_registration_options(
        rp_id=rp_id, rp_name=rp_name, user_id=secrets.token_bytes(32),
        user_name=email, user_display_name=email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED))
    _cache_json(f"webauthn:register:{challenge_id}", {
        "email": email, "timezone": timezone,
        "challenge": base64.urlsafe_b64encode(options.challenge).decode().rstrip("="),
        "user_id": base64.urlsafe_b64encode(options.user.id).decode().rstrip("="),
    }, 600)
    return {"challenge_id": str(challenge_id), "public_key_options": json.loads(options_to_json(options))}


def _verify_registration(proof: CredentialProof, context: dict[str, Any]) -> tuple[bytes, bytes, int]:
    _, rp_id, origin = _rp()
    challenge = base64.urlsafe_b64decode(context["challenge"] + "=" * (-len(context["challenge"]) % 4))
    credential = proof.credential
    verified = verify_registration_response(
        credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
        expected_origin=origin, require_user_verification=True)
    return verified.credential_id, verified.credential_public_key, verified.sign_count


@router.post("/auth/register/verify", status_code=201)
def register_verify(body: CredentialProof, request: Request, response: Response) -> dict[str, Any]:
    context = _consume_json(f"webauthn:register:{body.challenge_id}")
    if not context:
        raise HTTPException(422, "Registration challenge is invalid or expired.")
    try:
        credential_id, public_key, counter = _verify_registration(body, context)
    except Exception as exc:
        raise HTTPException(422, "Passkey registration could not be verified.") from exc
    email = context["email"]
    user = None
    with db_session() as session:
        if session.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(409, "An account already exists for this email.")
        user = User(email=email, timezone=context["timezone"])
        session.add(user)
        session.flush()
        session.add(Passkey(user_id=user.id, credential_id=credential_id,
                            public_key=public_key, sign_count=counter, label=body.label))
        storage.create_user_dek(session, user.id)
        raw_token = secrets.token_urlsafe(32)
        session.add(EmailVerificationToken(user_id=user.id,
                    token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                    expires_at=_now() + timedelta(hours=24)))
        _new_session(session, user, response)
        response_user = {"id": str(user.id), "email": user.email,
                         "email_verified_at": None, "timezone": user.timezone}
    try:
        _send_verification(email, raw_token)
    except Exception:
        # Account exists, but remains unverified; allow resend after SMTP is repaired.
        pass
    return response_user


@router.post("/auth/email/verify")
def verify_email(body: EmailVerificationRequest) -> dict[str, bool]:
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    with db_session() as session:
        consumed = session.execute(update(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.consumed_at.is_(None),
            EmailVerificationToken.expires_at > _now()
        ).values(consumed_at=_now()).returning(EmailVerificationToken.user_id))
        user_id = consumed.scalar_one_or_none()
        if user_id is None:
            raise HTTPException(400, "Verification link is invalid, expired or consumed.")
        user = session.get(User, user_id)
        if user:
            user.email_verified_at = _now()
    return {"email_verified": True}


@router.post("/auth/email/verification/resend", status_code=202)
def resend_verification(request: Request, user: User = Depends(current_user)) -> dict[str, str]:
    _check_csrf(request)
    with db_session() as session:
        actual = session.get(User, user.id)
        if actual and actual.email_verified_at is None:
            now = _now()
            _rate_limit(f"verify-account:{actual.id}", 3, 3600)
            client_ip = request.client.host if request.client else "unknown"
            _rate_limit(f"verify-ip:{hashlib.sha256(client_ip.encode()).hexdigest()}", 10, 3600)
            token = secrets.token_urlsafe(32)
            session.execute(delete(EmailVerificationToken).where(
                EmailVerificationToken.user_id == actual.id,
                EmailVerificationToken.consumed_at.is_(None)))
            session.add(EmailVerificationToken(user_id=actual.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=now + timedelta(hours=24)))
            try:
                _send_verification(actual.email, token)
            except Exception:
                pass
    return {"status": "accepted"}


@router.post("/auth/login/options")
def login_options(body: LoginOptions, request: Request) -> dict[str, Any]:
    email = str(body.email).strip().casefold()
    client_ip = request.client.host if request.client else "unknown"
    _rate_limit(f"login-ip:{hashlib.sha256(client_ip.encode()).hexdigest()}", 10, 900)
    _rate_limit(f"login-account:{_email_key(email)}", 5, 900)
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        # Discoverable credentials keep options shape independent of account existence.
        options = generate_authentication_options(
            rp_id=_rp()[1], user_verification=UserVerificationRequirement.REQUIRED)
        context: dict[str, Any] = {
            "email_hash": _email_key(email),
            "challenge": base64.urlsafe_b64encode(options.challenge).decode().rstrip("="),
        }
        # Avoid leaking whether the supplied email exists in challenge cache contents or response.
        if user:
            context["user_id"] = str(user.id)
    challenge_id = uuid.uuid4()
    _cache_json(f"webauthn:login:{challenge_id}", context, 300)
    return {"challenge_id": str(challenge_id), "public_key_options": json.loads(options_to_json(options)),
            "message": "If the account can be used, passkey options are ready."}


@router.post("/auth/login/verify")
def login_verify(body: CredentialProof, request: Request, response: Response) -> dict[str, Any]:
    context = _consume_json(f"webauthn:login:{body.challenge_id}")
    if not context:
        raise HTTPException(401, "Authentication failed.")
    credential = body.credential
    raw_id = credential.get("rawId")
    if not raw_id:
        raise HTTPException(401, "Authentication failed.")
    try:
        credential_id = base64.urlsafe_b64decode(raw_id + "=" * (-len(raw_id) % 4))
    except Exception as exc:
        raise HTTPException(401, "Authentication failed.") from exc
    with db_session() as session:
        passkey = session.scalar(select(Passkey).where(Passkey.credential_id == credential_id))
        user = session.get(User, passkey.user_id) if passkey else None
        expected_user = session.get(User, uuid.UUID(context["user_id"])) if context.get("user_id") else None
        # Same generic response for unknown email, missing credential and invalid assertion.
        if (not passkey or not user or not expected_user or passkey.user_id != expected_user.id
                or _email_key(expected_user.email) != context["email_hash"]):
            raise HTTPException(401, "Authentication failed.")
        _, rp_id, origin = _rp()
        challenge = base64.urlsafe_b64decode(context["challenge"] + "=" * (-len(context["challenge"]) % 4))
        try:
            verified = verify_authentication_response(
                credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
                expected_origin=origin, credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True)
        except Exception as exc:
            raise HTTPException(401, "Authentication failed.") from exc
        passkey.sign_count = verified.new_sign_count
        passkey.last_used_at = _now()
        _new_session(session, user, response)
        _audit(session, user.id, "login", "success", request)
        return {"id": str(user.id), "email": user.email,
                "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
                "timezone": user.timezone}


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"id": str(user.id), "email": user.email,
            "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
            "timezone": user.timezone}


class SettingsPatch(BaseModel):
    timezone: str


@router.patch("/settings")
def update_settings(body: SettingsPatch, request: Request,
                    user: User = Depends(current_user)) -> dict[str, Any]:
    _check_csrf(request)
    timezone = _valid_timezone(body.timezone)
    with db_session() as session:
        actual = session.get(User, user.id)
        actual.timezone = timezone
        return {"id": str(actual.id), "email": actual.email,
                "email_verified_at": actual.email_verified_at.isoformat() if actual.email_verified_at else None,
                "timezone": actual.timezone}


@router.post("/auth/passkeys/options")
def add_passkey_options(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    _check_csrf(request)
    rp_name, rp_id, _ = _rp()
    with db_session() as session:
        actual = session.get(User, user.id)
        options = generate_registration_options(
            rp_id=rp_id, rp_name=rp_name, user_id=actual.id.bytes,
            user_name=actual.email, user_display_name=actual.email,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED),
            exclude_credentials=_passkey_descriptors(session, actual.id))
    challenge_id = uuid.uuid4()
    _cache_json(f"webauthn:add:{challenge_id}", {
        "user_id": str(user.id),
        "challenge": base64.urlsafe_b64encode(options.challenge).decode().rstrip("="),
    }, 600)
    return {"challenge_id": str(challenge_id), "public_key_options": json.loads(options_to_json(options))}


@router.post("/auth/passkeys", status_code=201)
def add_passkey(body: CredentialProof, request: Request, response: Response,
               user: User = Depends(current_user)) -> dict[str, str]:
    _check_csrf(request)
    context = _consume_json(f"webauthn:add:{body.challenge_id}")
    if not context or context.get("user_id") != str(user.id):
        raise HTTPException(422, "Passkey challenge invalid or expired.")
    try:
        cred_id, pub_key, counter = _verify_registration(body, context)
    except Exception as exc:
        raise HTTPException(422, "Passkey registration could not be verified.") from exc
    with db_session() as session:
        session.add(Passkey(user_id=user.id, credential_id=cred_id, public_key=pub_key,
                            sign_count=counter, label=body.label))
        if getattr(request.state, "auth_level", "full") == "recovery":
            _new_session(session, user, response)
            return {"status": "added"}
    return {"status": "added"}


@router.post("/auth/refresh")
def refresh(request: Request, response: Response) -> dict[str, str]:
    _check_csrf(request)
    raw = request.cookies.get("nw_refresh")
    if not raw:
        raise HTTPException(401, "Authentication required.")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    reused = False
    with db_session() as session:
        now = _now()
        rotated = session.execute(update(RefreshSession).where(
            RefreshSession.token_hash == digest,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > now,
        ).values(revoked_at=now, last_used_at=now).returning(
            RefreshSession.id, RefreshSession.user_id, RefreshSession.family_id)).first()
        if rotated is None:
            old = session.scalar(select(RefreshSession).where(RefreshSession.token_hash == digest))
            if old:
                session.execute(update(RefreshSession).where(
                    RefreshSession.family_id == old.family_id).values(revoked_at=now))
            reused = True
        else:
            old_id, user_id, family_id = rotated
            user = session.get(User, user_id)
            if not user:
                raise HTTPException(401, "Authentication required.")
            new_raw = secrets.token_urlsafe(48)
            new_row = RefreshSession(user_id=user.id, family_id=family_id,
                                     token_hash=hashlib.sha256(new_raw.encode()).hexdigest(),
                                     expires_at=now + timedelta(days=30))
            session.add(new_row)
            session.flush()
            old = session.get(RefreshSession, old_id)
            old.replaced_by_id = new_row.id
            access = jwt.encode({"sub": str(user.id), "iat": now, "exp": now + timedelta(minutes=10), "typ": "access"}, _secret(), algorithm="HS256")
            secure = os.getenv("ENVIRONMENT", "development") == "production"
            response.set_cookie(ACCESS_COOKIE, access, httponly=True, secure=secure, samesite="lax", max_age=600, path="/")
            response.set_cookie(REFRESH_COOKIE, new_raw, httponly=True, secure=secure, samesite="lax", max_age=30 * 86400, path="/v1/auth")
    if reused:
        raise HTTPException(401, "Authentication required.")
    return {"status": "refreshed"}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, user: User = Depends(current_user)) -> Response:
    _check_csrf(request)
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with db_session() as session:
            row = session.scalar(select(RefreshSession).where(RefreshSession.token_hash == digest, RefreshSession.user_id == user.id))
            if row:
                session.execute(delete(RefreshSession).where(RefreshSession.family_id == row.family_id, RefreshSession.user_id == user.id))
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth")
    response.status_code = 204
    return response


@router.get("/privacy/consent")
def consent_text() -> dict[str, str]:
    return {"text_version": CONSENT_VERSION, "text": CONSENT_TEXT, "disclaimer": DISCLAIMER}


@router.get("/privacy/consents")
def list_consents(user: User = Depends(current_user)) -> dict[str, list[dict[str, Any]]]:
    with db_session() as session:
        rows = list(session.scalars(select(Consent).where(Consent.user_id == user.id).order_by(Consent.granted_at.desc())))
        return {"items": [{"id": str(row.id), "scope": row.scope,
                           "text_version": row.text_version,
                           "granted_at": row.granted_at.isoformat(),
                           "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None}
                          for row in rows]}


@router.post("/privacy/consents", status_code=201)
def grant_consent(body: ConsentRequest, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    _check_csrf(request)
    if body.text_version != CONSENT_VERSION:
        raise HTTPException(409, "Consent text version is stale.")
    if body.scope != "save_analysis_results" or body.affirmative is not True:
        raise HTTPException(422, "Explicit save-analysis consent is required.")
    with db_session() as session:
        consent = Consent(user_id=user.id, scope=body.scope, text_version=CONSENT_VERSION,
                          text_sha256=hashlib.sha256(CONSENT_TEXT.encode()).hexdigest())
        session.add(consent)
        session.flush()
        _audit(session, user.id, "consent_granted", "success", request)
        return {"id": str(consent.id), "text_version": consent.text_version,
                "granted_at": consent.granted_at.isoformat(), "scope": consent.scope}


class RevokeConsent(BaseModel):
    delete_saved_nights: bool


@router.delete("/privacy/consents/{consent_id}", status_code=204)
def revoke_consent(consent_id: uuid.UUID, body: RevokeConsent, request: Request,
                   user: User = Depends(current_user)) -> Response:
    _check_csrf(request)
    with db_session() as session:
        consent = session.scalar(select(Consent).where(Consent.id == consent_id, Consent.user_id == user.id))
        if not consent:
            raise HTTPException(404, "Consent not found.")
        if consent.revoked_at is None:
            consent.revoked_at = _now()
        if body.delete_saved_nights:
            ids = select(Night.id).where(Night.user_id == user.id, Night.consent_id == consent.id)
            linked_alerts = select(AlertSourceNight.alert_id).where(AlertSourceNight.night_id.in_(ids))
            session.execute(delete(Alert).where(Alert.user_id == user.id,
                                                (Alert.night_id.in_(ids)) | (Alert.id.in_(linked_alerts))))
            ids = select(Night.id).where(Night.user_id == user.id, Night.consent_id == consent.id)
            session.execute(delete(Night).where(Night.id.in_(ids)))
        _audit(session, user.id, "consent_revoked", "success", request)
    return Response(status_code=204)


@router.post("/auth/reauth/options")
def reauth_options(body: ReauthRequest, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    _check_csrf(request)
    if body.purpose not in {"export", "delete", "recovery_codes"}:
        raise HTTPException(422, "Unsupported reauthentication purpose.")
    with db_session() as session:
        descriptors = _passkey_descriptors(session, user.id)
        options = generate_authentication_options(
            rp_id=_rp()[1], allow_credentials=descriptors,
            user_verification=UserVerificationRequirement.REQUIRED)
    challenge_id = uuid.uuid4()
    _cache_json(f"webauthn:reauth:{challenge_id}", {
        "user_id": str(user.id), "purpose": body.purpose,
        "challenge": base64.urlsafe_b64encode(options.challenge).decode().rstrip("="),
    }, 300)
    return {"challenge_id": str(challenge_id), "public_key_options": json.loads(options_to_json(options))}


@router.post("/auth/reauth/verify")
def reauth_verify(body: CredentialProof, request: Request, user: User = Depends(current_user)) -> dict[str, str]:
    _check_csrf(request)
    context = _consume_json(f"webauthn:reauth:{body.challenge_id}")
    if not context or context.get("user_id") != str(user.id):
        raise HTTPException(401, "Passkey proof failed.")
    raw_id = body.credential.get("rawId")
    try:
        credential_id = base64.urlsafe_b64decode(raw_id + "=" * (-len(raw_id) % 4))
    except Exception as exc:
        raise HTTPException(401, "Passkey proof failed.") from exc
    with db_session() as session:
        passkey = session.scalar(select(Passkey).where(
            Passkey.user_id == user.id, Passkey.credential_id == credential_id))
        if not passkey:
            raise HTTPException(401, "Passkey proof failed.")
        challenge = base64.urlsafe_b64decode(context["challenge"] + "=" * (-len(context["challenge"]) % 4))
        _, rp_id, origin = _rp()
        try:
            verified = verify_authentication_response(
                credential=body.credential, expected_challenge=challenge, expected_rp_id=rp_id,
                expected_origin=origin, credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count,
                require_user_verification=True)
        except Exception as exc:
            raise HTTPException(401, "Passkey proof failed.") from exc
        passkey.sign_count = verified.new_sign_count
        passkey.last_used_at = _now()
    token = secrets.token_urlsafe(32)
    _cache_json(f"reauth-token:{hashlib.sha256(token.encode()).hexdigest()}", {
        "user_id": str(user.id), "purpose": context["purpose"]}, 300)
    return {"reauth_token": token}


def _consume_reauth(request: Request, user: User, purpose: str) -> None:
    token = request.headers.get("x-reauth-token", "")
    if not token:
        raise HTTPException(403, "Fresh passkey reauthentication required.")
    context = _consume_json(f"reauth-token:{hashlib.sha256(token.encode()).hexdigest()}")
    if not context or context.get("user_id") != str(user.id) or context.get("purpose") != purpose:
        raise HTTPException(403, "Fresh passkey reauthentication required.")


def _hash_recovery_code(code: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(code.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def _check_recovery_code(code: str, stored: str) -> bool:
    try:
        salt_text, digest_text = stored.split("$", 1)
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.scrypt(code.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@router.post("/auth/recovery-codes")
def create_recovery_codes(request: Request, user: User = Depends(current_user)) -> dict[str, list[str]]:
    _check_csrf(request)
    _consume_reauth(request, user, "recovery_codes")
    codes = [secrets.token_hex(5).upper() + "-" + secrets.token_hex(5).upper() for _ in range(10)]
    with db_session() as session:
        session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id, RecoveryCode.consumed_at.is_(None)))
        session.add_all([RecoveryCode(user_id=user.id, code_hash=_hash_recovery_code(code)) for code in codes])
    return {"codes": codes}


@router.post("/auth/recovery")
def recover_access(body: RecoveryRequest, response: Response, request: Request) -> dict[str, str]:
    email = str(body.email).strip().casefold()
    client_ip = request.client.host if request.client else "unknown"
    _rate_limit(f"recovery-ip:{hashlib.sha256(client_ip.encode()).hexdigest()}", 10, 900)
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        if not user:
            raise HTTPException(401, "Recovery failed.")
        codes = list(session.scalars(select(RecoveryCode).where(
            RecoveryCode.user_id == user.id, RecoveryCode.consumed_at.is_(None))))
        matched = next((item for item in codes if _check_recovery_code(body.code, item.code_hash)), None)
        if not matched:
            raise HTTPException(401, "Recovery failed.")
        consumed = session.execute(update(RecoveryCode).where(
            RecoveryCode.id == matched.id, RecoveryCode.consumed_at.is_(None)
        ).values(consumed_at=_now()).returning(RecoveryCode.id)).scalar_one_or_none()
        if consumed is None:
            raise HTTPException(401, "Recovery failed.")
        now = _now()
        access = jwt.encode({"sub": str(user.id), "iat": now, "exp": now + timedelta(minutes=10),
                             "typ": "access", "level": "recovery"}, _secret(), algorithm="HS256")
        secure = os.getenv("ENVIRONMENT", "development") == "production"
        response.set_cookie(ACCESS_COOKIE, access, httponly=True, secure=secure, samesite="lax", max_age=600, path="/")
    return {"status": "restricted_recovery_session"}


def _night_detail(session: Session, user_id: uuid.UUID, night: Night,
                  resolution: str = "downsampled", max_points: int = 1000) -> dict[str, Any]:
    row = session.scalar(select(Result).where(Result.night_id == night.id))
    if row is None:
        raise HTTPException(404, "Night not found.")
    try:
        payload = json.loads(storage.decrypt_for_user(session, user_id, row.encrypted_payload, row.nonce))
    except Exception as exc:
        raise HTTPException(500, "Saved result could not be decrypted.") from exc
    if resolution not in {"full", "downsampled"} or not 100 <= max_points <= 2000:
        raise HTTPException(422, "Invalid chart resolution settings.")
    # Result series are downsampled server-side with the Largest-Triangle-Three-Buckets method.
    chart_names = ("per_minute_probabilities", "interval_tachogram", "rejected_artifacts")
    for name in chart_names:
        values = payload.get(name)
        if values is None:
            continue
        if name == "per_minute_probabilities" and values and not isinstance(values[0], dict):
            values = [{"x": float(i * 60), "y": float(value), "flagged": False}
                      for i, value in enumerate(values)]
            payload[name] = values
        if resolution == "full":
            if len(values) > 50000:
                raise HTTPException(422, "Full series exceeds 50,000 points.")
        else:
            payload[name] = _lttb(values, max_points)
    return {
        "id": str(night.id), "started_at": night.started_at.isoformat(),
        "duration_seconds": night.duration_seconds, "source": night.source,
        "source_validated": night.source_validated, "model_version": row.model_version,
        "score": payload.get("score"), "apnea_burden_percent": payload.get("apnea_burden_percent"),
        "charts": {
            "probability_by_minute": payload.get("per_minute_probabilities"),
            "interval_tachogram": payload.get("interval_tachogram"),
            "rejected_artifacts": payload.get("rejected_artifacts"),
        },
        "chart_availability": payload.get("chart_availability", {
            "interval_tachogram": payload.get("interval_tachogram") is not None,
            "rejected_artifacts": payload.get("rejected_artifacts") is not None,
        }),
        "signal_quality": payload.get("signal_quality", {}), "disclaimer": DISCLAIMER,
    }


def _lttb(values: list[Any], threshold: int) -> list[Any]:
    """LTTB downsample preserving endpoints; accepts numeric values or {x,y} points."""
    n = len(values)
    if n <= threshold:
        return values
    if threshold < 3:
        return [values[0], values[-1]][:threshold]
    def point(i: int) -> tuple[float, float]:
        value = values[i]
        if isinstance(value, dict):
            return float(value.get("x", i)), float(value.get("y", 0))
        return float(i), float(value)
    sampled = [values[0]]
    every = (n - 2) / (threshold - 2)
    a = 0
    for bucket in range(threshold - 2):
        avg_start = int((bucket + 1) * every) + 1
        avg_end = min(int((bucket + 2) * every) + 1, n)
        if avg_start >= avg_end:
            avg_x, avg_y = point(n - 1)
        else:
            points = [point(i) for i in range(avg_start, avg_end)]
            avg_x = sum(x for x, _ in points) / len(points)
            avg_y = sum(y for _, y in points) / len(points)
        range_start = int(bucket * every) + 1
        range_end = min(int((bucket + 1) * every) + 1, n - 1)
        ax, ay = point(a)
        best_area, next_a = -1.0, range_start
        for i in range(range_start, max(range_start + 1, range_end)):
            ix, iy = point(i)
            area = abs((ax - avg_x) * (iy - ay) - (ax - ix) * (avg_y - ay))
            if area > best_area:
                best_area, next_a = area, i
        sampled.append(values[next_a])
        a = next_a
    sampled.append(values[-1])
    return sampled


@router.get("/nights")
def list_nights(request: Request, from_: str | None = Query(default=None, alias="from"), to: str | None = None,
                source: str | None = None, cursor: str | None = None,
                user: User = Depends(current_user)) -> dict[str, Any]:
    # Cursor is the UUID of the final included record; ordering is newest-first.
    with db_session() as session:
        stmt = select(Night).where(Night.user_id == user.id)
        if source:
            stmt = stmt.where(Night.source == source)
        try:
            start = datetime.fromisoformat(from_.replace("Z", "+00:00")) if from_ else None
            end = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else None
            cursor_id = uuid.UUID(cursor) if cursor else None
        except ValueError as exc:
            raise HTTPException(422, "Invalid from, to or cursor parameter.") from exc
        if start:
            stmt = stmt.where(Night.started_at >= start)
        if end:
            stmt = stmt.where(Night.started_at <= end)
        if cursor_id:
            cursor_night = session.get(Night, cursor_id)
            if cursor_night and cursor_night.user_id == user.id:
                stmt = stmt.where(Night.started_at < cursor_night.started_at)
        nights = list(session.scalars(stmt.order_by(Night.started_at.desc()).limit(51)))
        page, more = nights[:50], len(nights) > 50
        items = []
        for night in page:
            row = session.scalar(select(Result).where(Result.night_id == night.id))
            summary: dict[str, Any] = {"id": str(night.id), "started_at": night.started_at.isoformat(),
                                       "source": night.source, "source_validated": night.source_validated,
                                       "model_version": row.model_version if row else "unknown"}
            if row:
                data = json.loads(storage.decrypt_for_user(session, user.id, row.encrypted_payload, row.nonce))
                summary.update(score=data.get("score"), apnea_burden_percent=data.get("apnea_burden_percent"))
            items.append(summary)
        return {"items": items, "next_cursor": str(page[-1].id) if more and page else None}


@router.get("/nights/compare")
def compare_nights(request: Request, night_ids: str,
                   resolution: str = "downsampled", max_points: int = 1000,
                   user: User = Depends(current_user)) -> dict[str, Any]:
    try:
        ids = [uuid.UUID(item) for item in night_ids.split(",")]
    except ValueError as exc:
        raise HTTPException(422, "night_ids must be comma-separated UUIDs.") from exc
    if not 2 <= len(ids) <= 10 or len(set(ids)) != len(ids):
        raise HTTPException(422, "Provide 2 to 10 distinct night IDs.")
    with db_session() as session:
        nights = list(session.scalars(select(Night).where(Night.user_id == user.id, Night.id.in_(ids))))
        if len(nights) != len(ids):
            raise HTTPException(404, "Night not found.")
        indexed = {night.id: night for night in nights}
        return {"items": [_night_detail(session, user.id, indexed[item], resolution, max_points) for item in ids],
                "disclaimer": DISCLAIMER}


@router.get("/nights/{night_id}")
def get_night(night_id: uuid.UUID, resolution: str = "downsampled", max_points: int = 1000,
              user: User = Depends(current_user)) -> dict[str, Any]:
    with db_session() as session:
        night = session.scalar(select(Night).where(Night.id == night_id, Night.user_id == user.id))
        if not night:
            raise HTTPException(404, "Night not found.")
        return _night_detail(session, user.id, night, resolution, max_points)


@router.delete("/nights/{night_id}", status_code=204)
def delete_night(night_id: uuid.UUID, request: Request, user: User = Depends(current_user)) -> Response:
    _check_csrf(request)
    with db_session() as session:
        night = session.scalar(select(Night).where(Night.id == night_id, Night.user_id == user.id))
        if not night:
            raise HTTPException(404, "Night not found.")
        linked_alerts = select(AlertSourceNight.alert_id).where(AlertSourceNight.night_id == night.id)
        session.execute(delete(Alert).where(Alert.user_id == user.id,
                                            (Alert.night_id == night.id) | (Alert.id.in_(linked_alerts))))
        session.delete(night)
    return Response(status_code=204)


@router.get("/devices")
def list_devices(user: User = Depends(current_user)) -> dict[str, list[dict[str, Any]]]:
    with db_session() as session:
        devices = list(session.scalars(select(Device).where(Device.user_id == user.id, Device.revoked_at.is_(None))))
        return {"items": [{"id": str(d.id), "source": d.source, "label": d.label,
                           "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None}
                          for d in devices]}


@router.post("/devices", status_code=201)
def create_device(body: DeviceCreate, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    _check_csrf(request)
    if body.source not in {"ecg", "watch_rr", "ppg_rr"}:
        raise HTTPException(422, "Unsupported device source.")
    with db_session() as session:
        device = Device(user_id=user.id, source=body.source, label=body.label)
        session.add(device)
        session.flush()
        return {"id": str(device.id), "source": device.source, "label": device.label,
                "last_seen_at": None}


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(device_id: uuid.UUID, request: Request,
                  user: User = Depends(current_user)) -> Response:
    _check_csrf(request)
    with db_session() as session:
        device = session.scalar(select(Device).where(
            Device.id == device_id, Device.user_id == user.id, Device.revoked_at.is_(None)))
        if not device:
            raise HTTPException(404, "Device not found.")
        device.revoked_at = _now()
        device.encrypted_push_data = None
    return Response(status_code=204)


@router.post("/me/data/export")
def export_data(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    _check_csrf(request)
    _consume_reauth(request, user, "export")
    _rate_limit(f"export:{user.id}", 3, 3600)
    with db_session() as session:
        actual = session.get(User, user.id)
        nights = list(session.scalars(select(Night).where(Night.user_id == user.id).order_by(Night.started_at)))
        consents = list(session.scalars(select(Consent).where(Consent.user_id == user.id)))
        exported_nights = [_night_detail(session, user.id, night, "full", 1000) for night in nights]
        _audit(session, user.id, "export", "success", request)
        return {"exported_at": _now().isoformat(),
                "user": {"id": str(user.id), "email": actual.email,
                         "email_verified_at": actual.email_verified_at.isoformat() if actual.email_verified_at else None,
                         "timezone": actual.timezone},
                "consents": [{"id": str(c.id), "text_version": c.text_version,
                              "granted_at": c.granted_at.isoformat(), "scope": c.scope,
                              "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None} for c in consents],
                "nights": exported_nights}


class DeleteAccount(BaseModel):
    confirmation: str


@router.delete("/me/data", status_code=204)
def delete_account(body: DeleteAccount, request: Request, response: Response,
                   user: User = Depends(current_user)) -> Response:
    _check_csrf(request)
    _consume_reauth(request, user, "delete")
    if body.confirmation != "DELETE MY DATA":
        raise HTTPException(422, "Typed confirmation did not match.")
    from .jobs import cancel_user_jobs
    from .tombstones import record_deleted_account
    cancel_user_jobs(str(user.id))
    # Persist the restore purge marker before the account/key deletion commits.
    record_deleted_account(user.id)
    with db_session() as session:
        actual = session.get(User, user.id)
        _audit(session, user.id, "delete", "success", request)
        # Retain only an anonymous deletion event; the account-linked event cascades away.
        _audit(session, None, "delete", "success", request)
        # Account rows and the wrapped DEK cascade immediately; the restricted
        # pseudonymous tombstone was written before this transaction for restore replay.
        session.delete(actual)
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth")
    response.status_code = 204
    return response
