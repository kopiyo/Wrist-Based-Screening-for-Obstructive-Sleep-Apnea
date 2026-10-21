"""RQ integration using JSON serialization and opaque encrypted blob IDs only."""
from __future__ import annotations

import json
import os
import uuid
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from redis import Redis
from rq import Queue
from rq.job import Job
from rq.exceptions import NoSuchJobError
from rq.serializers import JSONSerializer

from .temp_blobs import delete_blob, read_blob, write_blob

JOB_TTL_SECONDS = 86400


@lru_cache(maxsize=1)
def redis_connection() -> Redis:
    url = os.getenv("REDIS_URL", "redis://:nightwise-dev-only@redis:6379/0")
    client = Redis.from_url(url, decode_responses=False)
    client.ping()
    return client


def analysis_queue() -> Queue:
    return Queue("analysis", connection=redis_connection(), serializer=JSONSerializer,
                 default_timeout=1800)


def enqueue_blob(blob_id: str, kind: str, *, user_id: str | None = None,
                 persist_result: bool = False, consent_id: str | None = None) -> str:
    job_id = str(uuid.uuid4())
    analysis_queue().enqueue(
        process_blob, blob_id, kind, user_id, persist_result, consent_id,
        job_id=job_id, result_ttl=JOB_TTL_SECONDS, failure_ttl=JOB_TTL_SECONDS,
        ttl=JOB_TTL_SECONDS,
    )
    connection = redis_connection()
    metadata = {"owner_id": user_id, "persist_result": persist_result,
                "consent_id": consent_id, "capability_hash": None,
                "status": "queued", "created_at": int(time.time()),
                "expires_at": int(time.time()) + JOB_TTL_SECONDS}
    connection.setex(f"nightwise:jobmeta:{job_id}", JOB_TTL_SECONDS, json.dumps(metadata))
    return job_id


def process_blob(blob_id: str, kind: str, user_id: str | None,
                 persist_result: bool, consent_id: str | None) -> dict[str, Any]:
    """Worker entry point. Delete staging material in all exit paths."""
    try:
        data = json.loads(read_blob(blob_id))
        from . import main
        if kind == "intervals":
            from .adapters import DerivedIntervals, interval_chart_projection
            import numpy as np
            series = DerivedIntervals(np.asarray(data["rr_ms"], dtype=np.float64),
                                      np.asarray(data["ts"], dtype=np.float64),
                                      data.get("source", "watch_rr"), data.get("device"))
            result = main._run_intervals(series)
            accepted, rejected = interval_chart_projection(series)
            result["interval_tachogram"] = accepted
            result["rejected_artifacts"] = rejected
            result["chart_availability"] = {"interval_tachogram": True, "rejected_artifacts": True}
            result["duration_seconds"] = float(series.ts[-1] - series.ts[0])
        elif data.get("kind") == "rr_csv":
            import base64
            from .adapters import CsvIntervalAdapter, interval_chart_projection
            item = data["files"][0]
            series = CsvIntervalAdapter().parse(base64.b64decode(item["body"]))
            result = main._run_intervals(series)
            accepted, rejected = interval_chart_projection(series)
            result["interval_tachogram"] = accepted
            result["rejected_artifacts"] = rejected
            result["chart_availability"] = {"interval_tachogram": True, "rejected_artifacts": True}
            result["duration_seconds"] = float(series.ts[-1] - series.ts[0])
        else:
            import base64
            from .adapters import ecg_from_uploads
            import numpy as np
            files = [(entry["name"], base64.b64decode(entry["body"])) for entry in data["files"]]
            signal, fs = ecg_from_uploads(files)
            result = main._run_ecg(signal, fs)
            result["duration_seconds"] = len(signal) / fs
        result["disclaimer"] = main.DISCLAIMER
        result["created_at"] = data.get("analysis_started_at", datetime.now(timezone.utc).isoformat())
        result["started_at"] = data.get("started_at", result["created_at"])
        if persist_result and user_id and consent_id:
            persist_completed_result(user_id, consent_id, result)
        result_blob_id = write_blob(json.dumps(result, separators=(",", ":"), allow_nan=False).encode(),
                                    ttl_seconds=JOB_TTL_SECONDS)
        return {"result_blob_id": result_blob_id}
    finally:
        delete_blob(blob_id)


def persist_completed_result(user_id: str, consent_id: str,
                             result: dict[str, Any]) -> None:
    """Persist only after verifying an active consent and verified account."""
    import json
    import uuid
    from datetime import datetime, timezone
    from sqlalchemy import select
    from .models import Consent, Night, Result, User
    from .storage import db_session, encrypt_for_user

    owner = uuid.UUID(user_id)
    consent_key = uuid.UUID(consent_id)
    with db_session() as session:
        user = session.get(User, owner)
        consent = session.scalar(select(Consent).where(
            Consent.id == consent_key, Consent.user_id == owner,
            Consent.scope == "save_analysis_results", Consent.revoked_at.is_(None)))
        if user is None or user.email_verified_at is None or consent is None:
            raise PermissionError("Verified account and active analysis consent are required.")
        night_id = uuid.uuid4()
        payload = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
        ciphertext, nonce, key_version = encrypt_for_user(session, owner, payload)
        started = datetime.fromisoformat(result.get("started_at", result.get("created_at", datetime.now(timezone.utc).isoformat())))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        duration = max(0, int(result.get("duration_seconds", len(result.get("per_minute_probabilities", [])) * 60)))
        night = Night(id=night_id, user_id=owner, source=result["source"],
                      source_validated=bool(result["source_validated"]), started_at=started,
                      duration_seconds=duration, consent_id=consent_key)
        session.add(night)
        session.add(Result(night_id=night_id, encrypted_payload=ciphertext, nonce=nonce,
                           key_version=key_version, model_version=result["model_version"]))
    # Run alert rules only after the consented Night and encrypted Result commit.
    try:
        from .alerts.engine import AlertRuleEngine
        from .alerts.models import Night as DomainNight
        from .alert_runtime import dispatch_created
        domain_night = DomainNight(
            id=str(night_id), user_id=user_id, started_at=started, source=result["source"],
            source_validated=bool(result["source_validated"]),
            model_version=result["model_version"], score=float(result["score"]),
            saved_with_consent=True)
        generated = AlertRuleEngine(__import__("apps.api.alerts_repository", fromlist=["SQLAlertRepository"]).SQLAlertRepository()).on_saved_night(domain_night)
        dispatch_created(generated)
    except Exception:
        # Analysis persistence is complete; notification outages must not fail the result job.
        pass


def cancel_user_jobs(user_id: str) -> None:
    """Cancel pending jobs and remove their transient encrypted blobs/results."""
    from .temp_blobs import delete_blob
    connection = redis_connection()
    for key in connection.scan_iter(match="nightwise:jobmeta:*"):
        raw = connection.get(key)
        if not raw:
            continue
        try:
            metadata = json.loads(raw)
            if metadata.get("owner_id") != user_id:
                continue
            job_id = key.decode().rsplit(":", 1)[-1]
            try:
                job = Job.fetch(job_id, connection=connection, serializer=JSONSerializer)
                args = job.args or ()
                if args and isinstance(args[0], str):
                    delete_blob(args[0])
                if isinstance(job.result, dict) and job.result.get("result_blob_id"):
                    delete_blob(job.result["result_blob_id"])
                job.cancel()
                job.delete()
            except NoSuchJobError:
                pass
            connection.delete(key)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
