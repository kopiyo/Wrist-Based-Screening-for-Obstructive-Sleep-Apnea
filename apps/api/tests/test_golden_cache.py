"""Golden inference check over the notebook's cached, preprocessed minutes.

The cache stores per-minute model inputs and labels, not prior prediction
outputs. This test fingerprints the exported model's probability and class
outputs on a fixed cache record so changes to the extracted inference path are
detected. The cache was emitted by the training notebook's preprocessing code.
"""
import hashlib

import numpy as np
import pytest

torch = pytest.importorskip("torch")

import pipeline


GOLDEN_SHA256 = "795224f1768af11b504604a2b92ed97e48b44a107b50f774c44fd74e372a9618"


def cached_prediction_fingerprint():
    torch.set_num_threads(1)
    model, _, cfg = pipeline.load_artifacts()
    cached = np.load(pipeline.MODEL_DIR.parent / "cache" / "a01.npz")
    segments = cached["X"].astype(np.float32)[:, :, cfg["channels"]]
    predictions, probabilities = [], []
    with torch.no_grad():
        for start in range(0, len(segments), 128):
            windows = []
            for j in range(start, min(start + 128, len(segments))):
                idx = np.clip(np.arange(j - cfg["context"], j + cfg["context"] + 1),
                              0, len(segments) - 1)
                windows.append(np.transpose(segments[idx], (0, 2, 1)))
            logits = model(torch.from_numpy(np.stack(windows)).to(pipeline.DEVICE))
            predictions.extend(logits.argmax(1).cpu().numpy().tolist())
            probabilities.extend(torch.softmax(logits, 1)[:, 1].cpu().numpy().tolist())
    payload = np.asarray(predictions, dtype=np.int64).tobytes()
    payload += np.asarray(probabilities, dtype=np.float32).round(6).tobytes()
    return hashlib.sha256(payload).hexdigest()


def test_cached_notebook_record_predictions_match_golden():
    assert GOLDEN_SHA256 != "TO_BE_GENERATED", "Generate the cache golden with apps/api/tests/generate_golden.py."
    assert cached_prediction_fingerprint() == GOLDEN_SHA256
