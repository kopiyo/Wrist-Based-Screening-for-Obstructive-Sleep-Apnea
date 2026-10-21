from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from apps.api import auth, jobs, main, models, storage, tombstones


class MemoryRedis:
    def __init__(self):
        self.values: dict[str, bytes] = {}
        self.expiries: dict[str, int] = {}
        self.counters: dict[str, int] = {}

    def ping(self):
        return True

    def setex(self, key, ttl, value):
        self.values[key] = value.encode() if isinstance(value, str) else value
        self.expiries[key] = ttl
        return True

    def get(self, key):
        return self.values.get(key)

    def getdel(self, key):
        return self.values.pop(key, None)

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, ttl):
        self.expiries[key] = ttl
        return True

    def ttl(self, key):
        return self.expiries.get(key, 300)

    def scan_iter(self, match=None):
        return iter([key.encode() for key in self.values if match is None or key.startswith(match.rstrip("*"))])

    def delete(self, key):
        return self.values.pop(key, None) is not None


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-only-jwt-secret-for-api-tests")
    monkeypatch.setenv("MASTER_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA")
    monkeypatch.setenv("MASTER_KEY_VERSION", "test-v1")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080")
    monkeypatch.setenv("TEMP_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setattr(tombstones, "TOMBSTONE_DIR", tmp_path / "tombstones")

    engine = create_engine(f"sqlite:///{tmp_path / 'phase2-test.sqlite'}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    models.Base.metadata.create_all(engine)
    monkeypatch.setattr(storage, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    redis = MemoryRedis()
    monkeypatch.setattr(auth, "redis_connection", lambda: redis)
    monkeypatch.setattr(jobs, "redis_connection", lambda: redis)
    monkeypatch.setattr(main, "redis_connection", lambda: redis)
    monkeypatch.setattr(main.pipeline, "load_artifacts", lambda: (object(), object(), {}))

    with TestClient(main.app) as client:
        yield client, redis

    engine.dispose()


def seed_user(email: str = "one@example.invalid", *, verified: bool = True):
    user_id = uuid.uuid4()
    with storage.db_session() as session:
        user = models.User(
            id=user_id, email=email,
            email_verified_at=datetime.now(timezone.utc) if verified else None,
            timezone="Africa/Nairobi",
        )
        session.add(user)
        session.flush()
        storage.create_user_dek(session, user_id)
    return user_id


def sign_in(client: TestClient, user_id: uuid.UUID, *, csrf: bool = True):
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": str(user_id), "iat": now,
                        "exp": now + timedelta(minutes=10), "typ": "access",
                        "level": "full"}, "test-only-jwt-secret-for-api-tests", algorithm="HS256")
    client.cookies.set("nw_access", token)
    if csrf:
        client.cookies.set("nw_csrf", "test-csrf-token")


def csrf_headers():
    return {"X-CSRF-Token": "test-csrf-token", "Origin": "http://localhost:8080"}


def create_consent(client: TestClient) -> str:
    disclosure = client.get("/v1/privacy/consent").json()
    response = client.post("/v1/privacy/consents", json={
        "text_version": disclosure["text_version"],
        "scope": "save_analysis_results", "affirmative": True,
    }, headers=csrf_headers())
    assert response.status_code == 201, response.text
    return response.json()["id"]


def seed_night(user_id: uuid.UUID, consent_id: str, *, offset: int = 0) -> uuid.UUID:
    night_id = uuid.uuid4()
    with storage.db_session() as session:
        session.add(models.Night(
            id=night_id, user_id=user_id, source="watch_rr", source_validated=False,
            started_at=datetime.now(timezone.utc) + timedelta(minutes=offset),
            duration_seconds=600, consent_id=uuid.UUID(consent_id),
        ))
    return night_id


def test_anonymous_analysis_is_ephemeral_and_persistence_requires_verified_consent(api, monkeypatch):
    client, _ = api
    scheduled = []

    def fake_schedule(payload, kind, request, persist_result, consent_id, device_id=None):
        user = main._persistence(request, persist_result, consent_id, device_id)
        scheduled.append((kind, persist_result, consent_id, str(user.id) if user else None))
        return {"job_id": str(uuid.uuid4()), "status": "queued", "job_capability": "opaque"}

    monkeypatch.setattr(main, "_schedule_blob", fake_schedule)
    timestamps = list(range(601))
    body = {"rr_ms": [1000] * len(timestamps), "ts": timestamps}

    anonymous = client.post("/v1/analyze/intervals", json=body)
    assert anonymous.status_code == 202
    assert anonymous.json()["job_capability"] == "opaque"
    assert scheduled[-1][1:3] == (False, None)
    with storage.db_session() as session:
        assert session.scalar(select(func.count()).select_from(models.Night)) == 0

    verified_id = seed_user()
    sign_in(client, verified_id)
    missing_consent = client.post("/v1/analyze/intervals", json=body | {"persist_result": True}, headers=csrf_headers())
    assert missing_consent.status_code == 403

    consent_id = create_consent(client)
    accepted = client.post("/v1/analyze/intervals", json=body | {
        "persist_result": True, "consent_id": consent_id,
    }, headers=csrf_headers())
    assert accepted.status_code == 202, accepted.text
    assert scheduled[-1][1:3] == (True, uuid.UUID(consent_id))
    with storage.db_session() as session:
        assert session.scalar(select(func.count()).select_from(models.Night)) == 0

    unverified_id = seed_user("pending@example.invalid", verified=False)
    sign_in(client, unverified_id)
    unverified_consent = create_consent(client)
    denied = client.post("/v1/analyze/intervals", json=body | {
        "persist_result": True, "consent_id": unverified_consent,
    }, headers=csrf_headers())
    assert denied.status_code == 403


def test_history_is_owner_only_and_individual_delete_requires_csrf(api):
    client, _ = api
    assert client.get("/v1/nights").status_code == 401

    owner = seed_user()
    sign_in(client, owner)
    consent_id = create_consent(client)
    night_id = seed_night(owner, consent_id)

    other = seed_user("other@example.invalid")
    sign_in(client, other)
    assert client.get(f"/v1/nights/{night_id}").status_code == 404
    assert client.request("DELETE", f"/v1/nights/{night_id}", json={}, headers=csrf_headers()).status_code == 404

    sign_in(client, owner, csrf=False)
    assert client.delete(f"/v1/nights/{night_id}").status_code == 403
    client.cookies.set("nw_csrf", "test-csrf-token")
    removed = client.delete(f"/v1/nights/{night_id}", headers=csrf_headers())
    assert removed.status_code == 204
    assert client.get(f"/v1/nights/{night_id}").status_code == 404


def test_consent_revocation_can_delete_saved_nights_or_retain_them(api):
    client, _ = api
    user_id = seed_user()
    sign_in(client, user_id)
    delete_consent = create_consent(client)
    keep_consent = create_consent(client)
    delete_id = seed_night(user_id, delete_consent)
    keep_id = seed_night(user_id, keep_consent, offset=1)

    deleted = client.request("DELETE", f"/v1/privacy/consents/{delete_consent}",
                             json={"delete_saved_nights": True}, headers=csrf_headers())
    retained = client.request("DELETE", f"/v1/privacy/consents/{keep_consent}",
                              json={"delete_saved_nights": False}, headers=csrf_headers())

    assert deleted.status_code == retained.status_code == 204
    with storage.db_session() as session:
        assert session.get(models.Night, delete_id) is None
        assert session.get(models.Night, keep_id) is not None
        assert session.get(models.Consent, uuid.UUID(keep_consent)).revoked_at is not None


def test_sensitive_export_and_delete_require_purpose_bound_one_use_reauth(api):
    client, redis = api
    user_id = seed_user()
    sign_in(client, user_id)

    denied = client.post("/v1/me/data/export", headers=csrf_headers())
    assert denied.status_code == 403

    def authorize(purpose: str) -> str:
        token = f"fresh-{purpose}-token"
        import hashlib
        key = f"reauth-token:{hashlib.sha256(token.encode()).hexdigest()}"
        redis.setex(key, 300, __import__("json").dumps({"user_id": str(user_id), "purpose": purpose}))
        return token

    wrong_purpose = authorize("delete")
    response = client.post("/v1/me/data/export", headers=csrf_headers() | {"X-Reauth-Token": wrong_purpose})
    assert response.status_code == 403

    export_token = authorize("export")
    exported = client.post("/v1/me/data/export", headers=csrf_headers() | {"X-Reauth-Token": export_token})
    assert exported.status_code == 200, exported.text
    replay = client.post("/v1/me/data/export", headers=csrf_headers() | {"X-Reauth-Token": export_token})
    assert replay.status_code == 403

    delete_token = authorize("delete")
    deleted = client.request("DELETE", "/v1/me/data", json={"confirmation": "DELETE MY DATA"},
                             headers=csrf_headers() | {"X-Reauth-Token": delete_token})
    assert deleted.status_code == 204
    with storage.db_session() as session:
        assert session.get(models.User, user_id) is None
        assert session.scalar(select(func.count()).select_from(models.AuditEvent).where(
            models.AuditEvent.kind == "delete", models.AuditEvent.user_id.is_(None))) == 1
    tombstone_file = tombstones.TOMBSTONE_DIR / tombstones.TOMBSTONE_FILE
    assert tombstone_file.exists()
    assert str(user_id).encode() not in tombstone_file.read_bytes()


def test_registration_duplicate_and_login_options_are_non_enumerating(api):
    client, _ = api
    known_id = seed_user("one@example.com")
    duplicate = client.post("/v1/auth/register/options", json={"email": "ONE@example.com"})
    assert duplicate.status_code == 409

    known = client.post("/v1/auth/login/options", json={"email": "one@example.com"})
    unknown = client.post("/v1/auth/login/options", json={"email": "missing@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json().keys() == unknown.json().keys()
    assert known.json()["message"] == unknown.json()["message"]
    assert known.json()["public_key_options"].keys() == unknown.json()["public_key_options"].keys()

    no_session = client.post("/v1/auth/passkeys/options")
    assert no_session.status_code == 401
    assert known_id


def test_recovery_code_is_single_use_and_passkey_upgrade_restores_refresh_session(api, monkeypatch):
    client, _ = api
    user_id = seed_user("recovery@example.com")
    raw_code = "RECOVERY-ONCE-1234"
    with storage.db_session() as session:
        session.add(models.RecoveryCode(user_id=user_id,
                                        code_hash=auth._hash_recovery_code(raw_code)))
    client.cookies.set("nw_csrf", "test-csrf-token")

    recovered = client.post("/v1/auth/recovery", json={"email": "recovery@example.com", "code": raw_code})
    assert recovered.status_code == 200, recovered.text
    restricted = client.get("/v1/auth/me")
    assert restricted.status_code == 403

    options = client.post("/v1/auth/passkeys/options", headers=csrf_headers())
    assert options.status_code == 200, options.text
    monkeypatch.setattr(auth, "_verify_registration", lambda proof, context: (b"new-credential", b"test-public-key", 0))
    added = client.post("/v1/auth/passkeys", json={
        "challenge_id": options.json()["challenge_id"],
        "credential": {"id": "new", "rawId": "bmV3LWNyZWRlbnRpYWw="},
        "label": "Recovery device",
    }, headers=csrf_headers())
    assert added.status_code == 201, added.text
    assert client.get("/v1/auth/me").status_code == 200

    replay = client.post("/v1/auth/recovery", json={"email": "recovery@example.com", "code": raw_code})
    assert replay.status_code == 401
    refresh = client.post("/v1/auth/refresh", headers=csrf_headers())
    assert refresh.status_code == 200, refresh.text
    assert "nw_refresh" in client.cookies


def test_alert_rules_require_account_and_keep_device_silent_disabled(api):
    client, _ = api
    assert client.get("/v1/alerts/rules").status_code == 401
    user_id = seed_user()
    sign_in(client, user_id)

    defaults = client.get("/v1/alerts/rules")
    assert defaults.status_code == 200
    silent = next(rule for rule in defaults.json()["rules"]
                  if rule["kind"] == "device_silent_overnight")
    assert silent["enabled"] is False
    assert silent["availability"] == "needs_live_device"

    refused = client.put("/v1/alerts/rules", json={
        "rules": [{"kind": "device_silent_overnight", "enabled": True,
                   "channels": ["web_push"]}],
        "quiet_hours": {"start": "22:00", "end": "07:00"},
    }, headers=csrf_headers())
    assert refused.status_code == 409

    saved = client.put("/v1/alerts/rules", json={
        "rules": [{"kind": "screening_result", "enabled": True,
                   "channels": ["web_push"]}],
        "quiet_hours": {"start": "22:30", "end": "06:30"},
    }, headers=csrf_headers())
    assert saved.status_code == 200, saved.text
    assert saved.json()["quiet_hours"]["timezone"] == "Africa/Nairobi"
    assert saved.json()["rules"][0]["enabled"] is True


def test_alert_history_is_owner_scoped_and_delivery_log_hides_payloads(api):
    client, _ = api
    owner = seed_user()
    other = seed_user("other@example.invalid")
    alert_id = uuid.uuid4()
    with storage.db_session() as session:
        session.add(models.Alert(id=alert_id, user_id=owner, rule_kind="screening_result",
                                 idempotency_key="screening_result:test-night",
                                 encrypted_detail=b"secret health payload", detail_nonce=b"n" * 12,
                                 key_version="test-v1", status="sent"))
        session.flush()
        session.add(models.DeliveryLog(alert_id=alert_id, channel="email", status="sent",
                                       provider_error_code=None))

    sign_in(client, other)
    assert client.get("/v1/alerts").json()["items"] == []
    assert client.get(f"/v1/alerts/{alert_id}/deliveries").status_code == 404

    sign_in(client, owner)
    listed = client.get("/v1/alerts")
    deliveries = client.get(f"/v1/alerts/{alert_id}/deliveries")
    assert listed.status_code == deliveries.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert "secret health payload" not in listed.text
    assert "destination" not in deliveries.text
    assert "payload" not in deliveries.text


def test_web_push_subscription_is_encrypted_and_removable(api):
    client, _ = api
    user_id = seed_user()
    sign_in(client, user_id)
    endpoint = "https://push.example.invalid/subscription-secret"
    response = client.post("/v1/alerts/web-push/subscriptions", json={
        "endpoint": endpoint, "keys": {"p256dh": "client-public-secret", "auth": "client-auth-secret"},
    }, headers=csrf_headers())
    assert response.status_code == 201, response.text
    subscription_id = uuid.UUID(response.json()["id"])
    with storage.db_session() as session:
        row = session.get(models.PushSubscription, subscription_id)
        assert endpoint.encode() not in row.encrypted_subscription
        assert b"client-public-secret" not in row.encrypted_subscription

    removed = client.delete(f"/v1/alerts/web-push/subscriptions/{subscription_id}", headers=csrf_headers())
    assert removed.status_code == 204


def test_deleting_any_source_night_purges_derived_trend_alert(api):
    client, _ = api
    user_id = seed_user()
    sign_in(client, user_id)
    consent_id = create_consent(client)
    night_ids = [seed_night(user_id, consent_id, offset=index) for index in range(6)]
    alert_id = uuid.uuid4()
    with storage.db_session() as session:
        alert = models.Alert(
            id=alert_id, user_id=user_id, night_id=night_ids[-1],
            rule_kind="worsening_trend", idempotency_key="worsening_trend:night-5",
            encrypted_detail=b"derived from all six nights", detail_nonce=b"d" * 12,
            key_version="test-v1", status="sent",
        )
        session.add(alert)
        session.flush()
        session.add_all([models.AlertSourceNight(alert_id=alert_id, night_id=night_id)
                         for night_id in night_ids])

    removed = client.delete(f"/v1/nights/{night_ids[0]}", headers=csrf_headers())

    assert removed.status_code == 204
    assert all(item["kind"] != "worsening_trend" for item in client.get("/v1/alerts").json()["items"])
    with storage.db_session() as session:
        assert session.get(models.Alert, alert_id) is None
        assert list(session.scalars(select(models.AlertSourceNight).where(
            models.AlertSourceNight.alert_id == alert_id))) == []


def test_lttb_chart_downsample_respects_requested_cap_and_preserves_endpoints():
    values = [((index * 17) % 101) / 100 for index in range(5000)]
    sampled = auth._lttb(values, 1000)
    assert len(sampled) <= 1000
    assert sampled[0] == values[0]
    assert sampled[-1] == values[-1]


def test_upload_rejects_unsupported_kind_even_when_files_are_valid(api, monkeypatch):
    client, _ = api
    sample_dir = Path(__file__).resolve().parents[3] / "samples"
    calls = []
    monkeypatch.setattr(main, "_schedule_blob", lambda *args, **kwargs: calls.append(args) or {"job_id": "unexpected"})

    response = client.post("/v1/analyze/file", data={"kind": "unsupported"}, files=[
        ("files", ("a10.hea", (sample_dir / "a10.hea").read_bytes(), "application/octet-stream")),
        ("files", ("a10.dat", (sample_dir / "a10.dat").read_bytes(), "application/octet-stream")),
    ])

    assert response.status_code == 422
    assert calls == []


def test_interval_size_limit_applies_without_content_length_header(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 64)
    payload = __import__("json").dumps({
        "rr_ms": [1000] * 601, "ts": list(range(601)),
    }).encode()

    class ChunkedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield payload[: len(payload) // 2]
            yield payload[len(payload) // 2 :]

    response = client.post("/v1/analyze/intervals", content=ChunkedBody(),
                           headers={"Content-Type": "application/json"})

    assert "content-length" not in response.request.headers
    assert response.request.headers.get("transfer-encoding") == "chunked"
    assert response.status_code == 413, response.text


def test_history_rejects_malformed_filters_with_422(api):
    client, _ = api
    sign_in(client, seed_user())
    for query in ("from=not-a-date", "to=2026-13-40", "cursor=not-a-uuid"):
        assert client.get(f"/v1/nights?{query}").status_code == 422


def test_saving_with_a_foreign_device_is_rejected(api, monkeypatch):
    client, _ = api
    owner = seed_user()
    sign_in(client, owner)
    device = client.post("/v1/devices", json={"source": "watch_rr", "label": "Mine"}, headers=csrf_headers())
    assert device.status_code == 201
    consent_id = create_consent(client)
    monkeypatch.setattr(main, "write_blob", lambda *a, **k: "blob")
    monkeypatch.setattr(main, "enqueue_blob", lambda *a, **k: str(uuid.uuid4()))
    body = {"rr_ms": [800.0] * 800, "ts": [i * 0.8 for i in range(800)],
            "persist_result": True, "consent_id": consent_id}

    sign_in(client, seed_user("other@example.invalid"))
    other_consent = create_consent(client)
    foreign = client.post("/v1/analyze/intervals", headers=csrf_headers(),
                          json=body | {"consent_id": other_consent, "device_id": device.json()["id"]})
    assert foreign.status_code == 422
    assert foreign.json()["detail"] == "Unknown device."


def test_rr_payload_has_an_item_limit(api):
    client, _ = api
    too_many = main.MAX_INTERVALS + 1
    response = client.post("/v1/analyze/intervals", json={"rr_ms": [800.0] * too_many,
                                                          "ts": list(range(too_many))})
    assert response.status_code == 422
