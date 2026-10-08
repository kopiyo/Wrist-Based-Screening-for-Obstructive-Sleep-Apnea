"""FastAPI Phase 1 API. Jobs and uploaded data stay in process memory."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.formparsers import MultiPartParser

import pipeline
from .adapters import CsvIntervalAdapter, DerivedIntervals, ecg_from_uploads

DISCLAIMER = "Screening tool, not a diagnosis. Research use only."
VERSION = "apnea-ecg-1"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# Starlette uses this threshold for SpooledTemporaryFile rollover. Keep the
# whole upload in memory up to the enforced request-size limit below.
MultiPartParser.spool_max_size = MAX_UPLOAD_BYTES
executor = ThreadPoolExecutor(max_workers=2)
jobs: dict[str, dict[str, Any]] = {}
job_events: dict[str, asyncio.Event] = {}
artifact_bundle = None
artifact_error: str | None = None


class IntervalsRequest(BaseModel):
    source: Literal["watch_rr", "ppg_rr"] = "watch_rr"
    device: str | None = None
    start_ts: float | None = None
    rr_ms: list[float] = Field(min_length=4)
    ts: list[float] = Field(min_length=4)


app = FastAPI(title="Sleep Screening API", version="1.0.0",
              description=DISCLAIMER)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


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
    }


def _create_job(source: str) -> str:
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"id": job_id, "status": "queued", "source": source,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "disclaimer": DISCLAIMER}
    job_events[job_id] = asyncio.Event()
    return job_id


async def _finish_job(job_id: str, future: asyncio.Future) -> None:
    jobs[job_id]["status"] = "processing"
    try:
        result = await future
        jobs[job_id].update(status="complete", result_id=job_id)
        jobs[f"result:{job_id}"] = result
    except Exception as exc:
        jobs[job_id].update(status="failed", error=str(exc))
    finally:
        job_events[job_id].set()


def _schedule(source: str, fn, *args) -> str:
    job_id = _create_job(source)
    loop = asyncio.get_running_loop()
    asyncio.create_task(_finish_job(job_id, loop.run_in_executor(executor, fn, *args)))
    return job_id


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
async def analyze_intervals(request: IntervalsRequest) -> dict[str, str]:
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
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"job_id": _schedule("watch_rr", _run_intervals, series)}


@app.post("/v1/analyze/file", status_code=202)
async def analyze_file(files: list[UploadFile] = File(...),
                       kind: str = Form("ecg")) -> dict[str, str]:
    contents = {}
    total_size = 0
    for upload in files:
        name = Path(upload.filename or "upload").name
        payload = await upload.read(MAX_UPLOAD_BYTES - total_size + 1)
        total_size += len(payload)
        if total_size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Combined upload size must be at most 100 MB.")
        contents[name] = payload
    if kind == "rr_csv":
        csvs = [(name, data) for name, data in contents.items() if name.lower().endswith(".csv")]
        if not csvs:
            raise HTTPException(422, "Upload an RR interval CSV file.")
        try:
            series = CsvIntervalAdapter().parse(csvs[0][1])
            if series.ts[-1] - series.ts[0] < 600:
                raise ValueError("At least 10 minutes of interval data is required.")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"job_id": _schedule("watch_rr", _run_intervals, series)}

    try:
        signal, fs = ecg_from_uploads(list(contents.items()))
        return {"job_id": _schedule("ecg", _run_ecg, signal, fs)}
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    if job_id not in jobs:
        raise HTTPException(404, "Job not found or expired.")
    return {k: v for k, v in jobs[job_id].items() if k != "result_id"} | {
        "result_id": jobs[job_id].get("result_id")}


@app.get("/v1/results/{result_id}")
def get_result(result_id: str) -> dict[str, Any]:
    result = jobs.get(f"result:{result_id}")
    if result is None:
        raise HTTPException(404, "Result is not ready or has expired.")
    if result["source_validated"] is False:
        result = dict(result, source_badge="Unvalidated source")
    return result | {"disclaimer": DISCLAIMER}


@app.get("/v1/jobs/{job_id}/events")
async def job_events_sse(job_id: str):
    from fastapi.responses import StreamingResponse
    if job_id not in jobs:
        raise HTTPException(404, "Job not found or expired.")
    async def event_stream():
        event = job_events[job_id]
        while not event.is_set():
            yield f"event: status\ndata: {json.dumps({'status': jobs[job_id]['status']})}\n\n"
            try:
                await asyncio.wait_for(event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
        yield f"event: status\ndata: {json.dumps({'status': jobs[job_id]['status']})}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
