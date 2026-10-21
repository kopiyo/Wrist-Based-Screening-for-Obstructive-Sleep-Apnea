"""Input adapters for Phase 1 file and RR-interval ingestion."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class DerivedIntervals:
    """Validated interval series passed from an input adapter to the model."""

    rr_ms: np.ndarray
    ts: np.ndarray
    source: str
    device: str | None = None


def interval_chart_projection(series: DerivedIntervals) -> tuple[list[dict], list[dict]]:
    """Project accepted/rejected RR points using the existing pipeline bounds/window."""
    rr = np.asarray(series.rr_ms, dtype=np.float64) / 1000.0
    times = np.asarray(series.ts, dtype=np.float64)
    physiologic = (rr >= 0.3) & (rr <= 2.0)
    accepted = np.zeros(len(rr), dtype=bool)
    candidate = np.flatnonzero(physiologic)
    if len(candidate):
        from pandas import Series
        candidate_rr = rr[candidate]
        median = Series(candidate_rr).rolling(5, center=True, min_periods=1).median().to_numpy()
        accepted[candidate] = np.abs(candidate_rr - median) <= 0.20 * median
    origin = times[0] if len(times) else 0.0
    accepted_points = [{"x": float(t - origin), "y": float(value * 1000), "flagged": False}
                       for t, value, keep in zip(times, rr, accepted) if keep]
    rejected_points = [{"x": float(t - origin), "y": float(value * 1000), "flagged": True}
                       for t, value, keep in zip(times, rr, accepted) if not keep]
    return accepted_points, rejected_points


def submitted_or_epoch_start(ts: float | None, submitted_at: datetime | None = None) -> str:
    """Only treat explicitly epoch-scale input as a calendar timestamp."""
    submitted_at = submitted_at or datetime.now(timezone.utc)
    if ts is not None and np.isfinite(ts) and ts >= 946684800:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    return submitted_at.astimezone(timezone.utc).isoformat()


class IntervalAdapter(Protocol):
    def parse(self, payload: bytes, *, device: str | None = None) -> DerivedIntervals:
        """Convert an input payload into intervals and timestamps in seconds."""


class CsvIntervalAdapter:
    """CSV adapter expecting ``rr_ms`` and ``ts`` columns."""

    def parse(self, payload: bytes, *, device: str | None = None) -> DerivedIntervals:
        import io
        import pandas as pd

        try:
            frame = pd.read_csv(io.BytesIO(payload))
        except Exception as exc:
            raise ValueError("Could not read the RR interval CSV.") from exc
        names = {str(name).strip().lower(): name for name in frame.columns}
        rr_key = names.get("rr_ms") or names.get("rr") or names.get("interval_ms")
        ts_key = names.get("ts") or names.get("timestamp") or names.get("time_s")
        if rr_key is None or ts_key is None:
            raise ValueError("RR CSV must include rr_ms and ts columns (timestamps in seconds).")
        rr = frame[rr_key].to_numpy(dtype=np.float64)
        ts = frame[ts_key].to_numpy(dtype=np.float64)
        if len(rr) < 4 or len(rr) != len(ts):
            raise ValueError("RR CSV must have at least four paired rr_ms and ts values.")
        return DerivedIntervals(rr_ms=rr, ts=ts, source="watch_rr", device=device)


def ecg_from_uploads(files: list[tuple[str, bytes]]) -> tuple[np.ndarray, float]:
    """Parse ECG CSV or a WFDB pair directly from upload bytes."""
    by_name = {name.lower(): (name, payload) for name, payload in files}
    csv_upload = next((item for name, item in by_name.items()
                       if name.endswith((".csv", ".txt"))), None)
    if csv_upload is not None:
        import io
        import pandas as pd

        try:
            frame = pd.read_csv(io.BytesIO(csv_upload[1]))
            numeric = frame.select_dtypes(include="number")
        except Exception as exc:
            raise ValueError("Could not read the ECG CSV file.") from exc
        if numeric.shape[1] == 0:
            raise ValueError("No numeric column found in the uploaded ECG CSV.")
        return numeric.iloc[:, 0].to_numpy(dtype=np.float64), 100.0

    hea = next((item for name, item in by_name.items() if name.endswith(".hea")), None)
    dat = next((item for name, item in by_name.items() if name.endswith(".dat")), None)
    if hea is None or dat is None:
        raise ValueError("Upload a WFDB .hea + .dat pair, or a CSV of ECG samples.")
    try:
        lines = [line.strip() for line in hea[1].decode("utf-8", errors="ignore").splitlines()
                 if line.strip() and not line.startswith("#")]
        p0 = lines[0].split()
        n_sig = int(p0[1])
        fs = float(p0[2]) if len(p0) > 2 else 250.0
        samples = int(p0[3]) if len(p0) > 3 else 0
        signals = []
        for line in lines[1:1 + n_sig]:
            p = line.split()
            fmt = int(re.match(r"(\d+)", p[1]).group(1))
            gain, baseline = 200.0, 0
            if len(p) > 2:
                match = re.match(r"([-\d.]+)(?:\((-?\d+)\))?", p[2])
                if match:
                    gain = float(match.group(1)) or 200.0
                    if match.group(2) is not None:
                        baseline = int(match.group(2))
            adc_zero = int(p[4]) if len(p) > 4 else 0
            if len(p) > 2 and "(" not in p[2]:
                baseline = adc_zero
            signals.append({"fmt": fmt, "gain": gain, "baseline": baseline})
        if n_sig < 1 or not signals or fs <= 0 or samples <= 0:
            raise ValueError("The WFDB header is missing signal metadata.")
        fmt = signals[0]["fmt"]
        raw_bytes = dat[1]
        if fmt == 16:
            raw = np.frombuffer(raw_bytes, dtype="<i2").astype(np.int32)
        elif fmt == 212:
            b = np.frombuffer(raw_bytes, dtype=np.uint8)
            n3 = len(b) // 3
            b = b[:n3 * 3].reshape(-1, 3).astype(np.int32)
            s1 = b[:, 0] | ((b[:, 1] & 0x0F) << 8)
            s2 = b[:, 2] | ((b[:, 1] >> 4) << 8)
            s1 = np.where(s1 > 2047, s1 - 4096, s1)
            s2 = np.where(s2 > 2047, s2 - 4096, s2)
            raw = np.empty(n3 * 2, dtype=np.int32)
            raw[0::2], raw[1::2] = s1, s2
        else:
            raise ValueError(f"WFDB format {fmt} is not supported.")
        raw = (raw[:len(raw) // n_sig * n_sig].reshape(-1, n_sig)
               if n_sig > 1 else raw.reshape(-1, 1))
        signal = (raw[:, 0].astype(np.float64) - signals[0]["baseline"]) / signals[0]["gain"]
    except (IndexError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"Could not parse the WFDB upload: {exc}") from exc
    if len(signal) == 0:
        raise ValueError("The WFDB data file does not contain samples.")
    return signal, fs
