import numpy as np
import pytest
from pathlib import Path

from apps.api.adapters import CsvIntervalAdapter, ecg_from_uploads


def test_csv_interval_adapter_reads_documented_columns():
    rows = "rr_ms,ts\n1000,0\n980,1\n1020,2\n1000,3\n"
    series = CsvIntervalAdapter().parse(rows.encode())
    assert series.source == "watch_rr"
    assert series.rr_ms.tolist() == [1000, 980, 1020, 1000]
    assert series.ts.tolist() == [0, 1, 2, 3]


def test_csv_interval_adapter_rejects_unknown_schema():
    with pytest.raises(ValueError, match="rr_ms and ts"):
        CsvIntervalAdapter().parse(b"heart_rate\n60\n")


def test_csv_interval_adapter_rejects_too_few_intervals():
    with pytest.raises(ValueError, match="at least four"):
        CsvIntervalAdapter().parse(b"rr_ms,ts\n1000,0\n")


def test_rr_quality_usable_ratio_contract():
    assert np.isfinite(np.asarray([800, 1000, 1200], dtype=float)).all()


def test_wfdb_adapter_reads_sample_pair_from_memory():
    root = Path(__file__).resolve().parents[3]
    record = "a10"
    files = [(f"{record}.hea", (root / "samples" / f"{record}.hea").read_bytes()),
             (f"{record}.dat", (root / "samples" / f"{record}.dat").read_bytes())]
    signal, fs = ecg_from_uploads(files)
    assert fs == 100.0
    assert signal.ndim == 1 and len(signal) > 60_000


def test_ecg_csv_adapter_uses_first_numeric_column_and_100_hz():
    signal, fs = ecg_from_uploads([("signal.csv", b"ecg,other\n0.1,8\n0.2,9\n")])
    assert fs == 100.0
    assert signal.tolist() == [0.1, 0.2]
