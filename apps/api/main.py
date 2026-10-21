"""Nightwise screening API with ephemeral encrypted staging and JSON RQ jobs."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.formparsers import MultiPartParser

import pipeline
from .adapters import CsvIntervalAdapter, DerivedIntervals, ecg_from_uploads
from . import auth
from .jobs import enqueue_blob, redis_connection
from .temp_blobs import MAX_UPLOAD_BYTES, write_blob
from .alerts_api import router as alerts_router

DISCLAIMER = "Screening tool, not a diagnosis. Research use only."
VERSION = "apnea-ecg-1"
# Starlette uses this threshold for SpooledTemporaryFile rollover. Keep the
# whole upload in memory up to the enforced request-size limit below.
MultiPartParser.spool_max_size = MAX_UPLOAD_BYTES
executor = ThreadPoolExecutor(max_workers=2)
jobs: dict[str, dict[str, Any]] = {}
job_events: dict[str, asyncio.Event] = {}
artifact_bundle = None
artifact_error: str | None = None


class AnalysisBodyLimit:
    """Bound chunked as well as Content-Length analysis requests at ASGI ingress."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope.get("type") != "http" or path not in {"/v1/analyze/file", "/v1/analyze/intervals"}:
            return await self.app(scope, receive, send)
        cap = MAX_UPLOAD_BYTES + (1024 * 1024 if path.endswith("/file") else 0)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        length = headers.get(b"content-length")
        if length:
            try:
                if int(length) > cap:
                    return await self._reject(send)
            except ValueError:
                return await self._reject(send)
        total = 0
        started = False

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > cap:
                    # Raise the framework exception from receive so the inner
                    # exception middleware can return a proper 413 response.
                    raise HTTPException(413, "Input exceeds 100 MB.")
            return message
        await self.app(scope, bounded_receive, send)

    @staticmethod
    async def _reject(send):
        body = b'{"detail":"Request body exceeds the 100 MB analysis limit."}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class IntervalsRequest(BaseModel):
    source: Literal["watch_rr", "ppg_rr"] = "watch_rr"
    device: str | None = None
    start_ts: float | None = None
    rr_ms: list[float] = Field(min_length=4)
    ts: list[float] = Field(min_length=4)
    persist_result: bool = False
    consent_id: uuid.UUID | None = None


app = FastAPI(title="Sleep Screening API", version="1.0.0",
              description=DISCLAIMER)
_cors_origins = [item.strip() for item in os.getenv(
    "CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",") if item.strip()]
if not _cors_origins or "*" in _cors_origins:
    raise RuntimeError("CORS_ORIGINS must contain an exact, non-wildcard origin allowlist.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Reauth-Token", "X-Job-Capability"],
)
app.add_middleware(AnalysisBodyLimit)
app.include_router(auth.router)
app.include_router(alerts_router)


@app.on_event("startup")
def load_model() -> None:
    global artifact_bundle, artifact_error
    try:
        artifact_bundle = pipeline.load_artifacts()
        artifact_error = None
    except Exception as exc:
        artifact_error = f"Model unavailable: {exc}"


def _model() -> tuple[Any, Any, dict[str, Any]]:
    if artifact_bundle is None:
        raise RuntimeError(artifact_error or "Model artifacts are unavailable.")
    return artifact_bundle


def _run_intervals(series: DerivedIntervals) -> dict[str, Any]:
    model, stage2, cfg = _model()
    raw = pipeline.screen_intervals(series.rr_ms, series.ts, model, stage2, cfg)
    minutes = int(raw["minutes"])
    return {
        "score": float(raw["risk"]),
        "apnea_burden_percent": 100 * float(raw["apnea_minutes"]) / max(minutes, 1),
        "per_minute_probabilities": [float(x) for x in raw["per_minute_prob"]],
        "signal_quality": raw["signal_quality"],
        "referral_decision": "Sleep study recommended" if raw["refer"] else "Not indicated by this screening",
        "model_version": VERSION,
        "source_validated": False,
        "source": series.source,
        "device": series.device,
    }


def _run_ecg(signal: np.ndarray, fs: float) -> dict[str, Any]:
    model, stage2, cfg = _model()
    raw = pipeline.screen(signal, fs, model, stage2, cfg)
    minutes = int(raw["minutes"])
    return {
        "score": float(raw["risk"]),
        "apnea_burden_percent": 100 * float(raw["apnea_minutes"]) / max(minutes, 1),
        "per_minute_probabilities": [float(x) for x in raw["per_minute_prob"]],
        "signal_quality": {"usable_beat_ratio": None, "gaps": None,
                           "usable_beats": int(raw["n_beats"])},
        "referral_decision": "Sleep study recommended" if raw["refer"] else "Not indicated by this screening",
        "model_version": VERSION,
        "source_validated": True,
        "source": "ecg",
        "device": None,
        "interval_tachogram": None,
        "rejected_artifacts": None,
        "chart_availability": {"interval_tachogram": False, "rejected_artifacts": False},
    }


def _optional_user(request: Request):
    if not request.cookies.get(auth.ACCESS_COOKIE):
        return None
    return auth.current_user(request)


def _persistence(request: Request, persist_result: bool, consent_id: uuid.UUID | None):
    if not persist_result:
        return None
    auth._check_csrf(request)
    user = _optional_user(request)
    if user is None:
        raise HTTPException(403, "Sign in to save an analysis.")
    from sqlalchemy import select
    from .models import Consent, User
    from .storage import db_session
    with db_session() as session:
        actual = session.get(User, user.id)
        consent = session.scalar(select(Consent).where(
            Consent.id == consent_id, Consent.user_id == user.id,
            Consent.scope == "save_analysis_results", Consent.revoked_at.is_(None))) if consent_id else None
        if not actual or actual.email_verified_at is None or consent is None:
            raise HTTPException(403, "Persistence requires a verified account and active consent.")
    return user


def _schedule_blob(payload: bytes, kind: str, request: Request,
                   persist_result: bool, consent_id: uuid.UUID | None) -> dict[str, str]:
    user = _persistence(request, persist_result, consent_id)
    blob_id = write_blob(payload, max_bytes=140 * 1024 * 1024)
    job_id = enqueue_blob(blob_id, kind, user_id=str(user.id) if user else None,
                          persist_result=persist_result, consent_id=str(consent_id) if persist_result and consent_id else None)
    capability = None
    if user is None:
        capability = secrets.token_urlsafe(32)
        key = f"nightwise:jobmeta:{job_id}"
        meta = json.loads(redis_connection().get(key))
        meta["capability_hash"] = hashlib.sha256(capability.encode()).hexdigest()
        redis_connection().setex(key, 86400, json.dumps(meta, separators=(",", ":")))
    response = {"job_id": job_id, "status": "queued", "disclaimer": DISCLAIMER}
    if capability:
        response["job_capability"] = capability
    return response


def _job_and_authorize(job_id: str, request: Request):
    import hashlib
    from rq.job import Job
    from rq.exceptions import NoSuchJobError
    try:
        meta_raw = redis_connection().get(f"nightwise:jobmeta:{job_id}")
        if not meta_raw:
            raise HTTPException(404, "Job not found or expired.")
        meta = json.loads(meta_raw)
        owner = _optional_user(request) if request.cookies.get(auth.ACCESS_COOKIE) else None
        capability = request.headers.get("x-job-capability", "")
        is_owner = owner is not None and meta.get("owner_id") == str(owner.id)
        is_capability = bool(capability and meta.get("capability_hash") and
                             secrets.compare_digest(hashlib.sha256(capability.encode()).hexdigest(), meta["capability_hash"]))
        if not is_owner and not is_capability:
            raise HTTPException(403, "Job capability or owner authorization is invalid.")
        job = Job.fetch(job_id, connection=redis_connection(), serializer=__import__("rq.serializers", fromlist=["JSONSerializer"]).JSONSerializer)
        return job, meta
    except NoSuchJobError as exc:
        raise HTTPException(404, "Job not found or expired.") from exc


@app.get("/v1/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "model_ready": artifact_bundle is not None,
            "disclaimer": DISCLAIMER}


@app.get("/v1/model-card")
def model_card() -> dict[str, Any]:
    try:
        cfg = json.loads((pipeline.MODEL_DIR / "config.json").read_text())
    except Exception:
        cfg = {}
    return {"model": "CNN-BiLSTM + LogReg", "training_source": "single-lead ECG",
            "auc": cfg.get("stage2_auc"),
            "sensitivity": cfg.get("screening_sensitivity"),
            "specificity": cfg.get("screening_specificity"),
            "threshold": cfg.get("screening_threshold"),
            "ahi_threshold": cfg.get("ahi_threshold"),
            "patients": cfg.get("n_patients"),
            "disclaimer": DISCLAIMER}


@app.post("/v1/analyze/intervals", status_code=202)
async def analyze_intervals(request: IntervalsRequest, http_request: Request) -> dict[str, str]:
    content_length = http_request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Input exceeds 100 MB.")
    if len(request.rr_ms) != len(request.ts):
        raise HTTPException(422, "rr_ms and ts must have the same number of values.")
    try:
        # ``ts`` is seconds relative to ``start_ts`` when that value is given.
        interval_ts = np.asarray(request.ts, dtype=np.float64)
        if request.start_ts is not None:
            interval_ts = interval_ts + request.start_ts
        series = DerivedIntervals(np.asarray(request.rr_ms), interval_ts,
                                  request.source, request.device)
        # Validate immediately, before queueing work.
        if not np.isfinite(series.rr_ms).all() or not np.isfinite(series.ts).all():
            raise ValueError("Intervals and timestamps must be finite.")
        if np.any(np.diff(series.ts) <= 0):
            raise ValueError("Timestamps must be strictly increasing.")
        if series.ts[-1] - series.ts[0] < 600:
            raise ValueError("At least 10 minutes of interval data is required.")
        submitted = datetime.now(timezone.utc)
        started_at = (datetime.fromtimestamp(request.start_ts, timezone.utc).isoformat()
                      if request.start_ts is not None and request.start_ts >= 946684800
                      else submitted.isoformat())
        staged = json.dumps({"rr_ms": series.rr_ms.tolist(), "ts": series.ts.tolist(),
                             "source": series.source, "device": series.device,
                             "analysis_started_at": submitted.isoformat(), "started_at": started_at},
                            separators=(",", ":"), allow_nan=False).encode()
        if len(staged) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Input exceeds 100 MB.")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _schedule_blob(staged, "intervals", http_request,
                          request.persist_result, request.consent_id)


@app.post("/v1/analyze/file", status_code=202)
async def analyze_file(request: Request,
                       files: list[UploadFile] = File(...),
                       kind: str = Form("ecg"),
                       persist_result: bool = Form(False),
                       consent_id: uuid.UUID | None = Form(None)) -> dict[str, str]:
    if len(files) > 10:
        raise HTTPException(422, "At most 10 files can be uploaded at once.")
    if kind not in {"ecg", "rr_csv"}:
        raise HTTPException(422, "kind must be ecg or rr_csv.")
    contents = {}
    total_size = 0
    for upload in files:
        name = Path(upload.filename or "upload").name
        payload = await upload.read(MAX_UPLOAD_BYTES - total_size + 1)
        total_size += len(payload)
        if total_size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Combined upload size must be at most 100 MB.")
        contents[name] = payload
    submitted = datetime.now(timezone.utc).isoformat()
    staged: dict[str, Any] = {"kind": kind, "files": [
        {"name": name, "body": base64.b64encode(content).decode("ascii")}
        for name, content in contents.items()], "analysis_started_at": submitted,
        "started_at": submitted}
    if kind == "rr_csv":
        csvs = [(name, data) for name, data in contents.items() if name.lower().endswith(".csv")]
        if not csvs:
            raise HTTPException(422, "Upload an RR interval CSV file.")
        try:
            series = CsvIntervalAdapter().parse(csvs[0][1])
            if series.ts[-1] - series.ts[0] < 600:
                raise ValueError("At least 10 minutes of interval data is required.")
            if series.ts[0] >= 946684800:
                staged["started_at"] = datetime.fromtimestamp(float(series.ts[0]), timezone.utc).isoformat()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return _schedule_blob(json.dumps(staged, separators=(",", ":")).encode(),
                              "rr_csv", request, persist_result, consent_id)

    try:
        # Validate payload on the API before putting only an encrypted volume blob on the queue.
        ecg_from_uploads(list(contents.items()))
        return _schedule_blob(json.dumps(staged, separators=(",", ":")).encode(),
                              "ecg", request, persist_result, consent_id)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    job, meta = _job_and_authorize(job_id, request)
    status = job.get_status(refresh=True)
    if status == "queued":
        normalized = "queued"
    elif status in {"started", "deferred", "scheduled"}:
        normalized = "started"
    elif status == "finished":
        normalized = "finished"
    elif status == "failed":
        normalized = "failed"
    else:
        normalized = "expired"
    response: dict[str, Any] = {"job_id": job_id, "status": normalized,
                                "result": None, "error": "Analysis failed." if normalized == "failed" else None}
    if normalized == "finished":
        response["result_id"] = job_id
    return response


@app.get("/v1/results/{result_id}")
def get_result(result_id: str, request: Request,
               resolution: str = "downsampled", max_points: int = 1000) -> dict[str, Any]:
    job, _ = _job_and_authorize(result_id, request)
    if job.get_status(refresh=True) != "finished" or not isinstance(job.result, dict):
        raise HTTPException(404, "Result is not ready or has expired.")
    blob_id = job.result.get("result_blob_id")
    if not blob_id:
        raise HTTPException(404, "Result is not ready or has expired.")
    from .temp_blobs import read_blob
    try:
        result = json.loads(read_blob(blob_id))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(404, "Result is not ready or has expired.") from exc
    if result.get("source_validated") is False:
        result["source_badge"] = "Unvalidated source"
    if resolution not in {"full", "downsampled"} or not 100 <= max_points <= 2000:
        raise HTTPException(422, "Invalid chart resolution settings.")
    probabilities = result.get("per_minute_probabilities")
    probability_points = ([{"x": float(i * 60), "y": float(value), "flagged": False}
                           for i, value in enumerate(probabilities)] if probabilities is not None else None)
    chart_series = {
        "probability_by_minute": probability_points,
        "interval_tachogram": result.get("interval_tachogram"),
        "rejected_artifacts": result.get("rejected_artifacts"),
    }
    for name, values in chart_series.items():
        if values is None:
            continue
        if resolution == "full":
            if len(values) > 50000:
                raise HTTPException(422, "Full series exceeds 50,000 points.")
        else:
            chart_series[name] = auth._lttb(values, max_points)
    result["charts"] = chart_series
    result["chart_availability"] = result.get("chart_availability", {
        "interval_tachogram": result.get("interval_tachogram") is not None,
        "rejected_artifacts": result.get("rejected_artifacts") is not None,
    })
    for name in ("per_minute_probabilities", "interval_tachogram", "rejected_artifacts"):
        result.pop(name, None)
    return result | {"disclaimer": DISCLAIMER}


@app.get("/v1/jobs/{job_id}/events")
async def job_events_sse(job_id: str, request: Request):
    from fastapi.responses import StreamingResponse
    job, _ = _job_and_authorize(job_id, request)
    async def event_stream():
        last = None
        while True:
            status = job.get_status(refresh=True)
            normalized = "finished" if status == "finished" else "failed" if status == "failed" else "started" if status in {"started", "deferred", "scheduled"} else "queued"
            if normalized != last:
                yield f"event: status\ndata: {json.dumps({'status': normalized})}\n\n"
                last = normalized
            if normalized in {"finished", "failed"}:
                break
            await asyncio.sleep(1)
    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
