"""
Preprocessing and inference for the sleep apnea screening app.

Everything in this file is copied VERBATIM from the training notebook. That is
deliberate. If the app recomputed features even slightly differently from the
way the model was trained, its predictions would drift away from the reported
performance -- a failure mode called train/serve skew, and the most common way
a validated model produces nonsense in deployment.

Do not "improve" the signal processing here without retraining.
"""
import os, re, json, pickle
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from scipy.signal import butter, filtfilt, find_peaks
from scipy.interpolate import PchipInterpolator
from scipy.stats import iqr

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ---------------------------------------------------------------------------
# WFDB readers (no external dependency)
# ---------------------------------------------------------------------------
WFDB_ANN_SYMBOLS = {
    0: "", 1: "N", 2: "L", 3: "R", 4: "a", 5: "V", 6: "F", 7: "J", 8: "A",
    9: "S", 10: "E", 11: "j", 12: "/", 13: "Q", 14: "~", 16: "|", 18: "s",
    19: "T", 20: "*", 21: "D", 22: '"', 23: "=", 24: "p", 25: "B", 26: "^",
    27: "t", 28: "+", 29: "u", 30: "?", 31: "!", 32: "[", 33: "]", 34: "e",
    35: "n", 36: "@", 37: "x", 38: "f", 39: "(", 40: ")", 41: "r",
}
ANN_SKIP, ANN_NUM, ANN_SUB, ANN_CHN, ANN_AUX = 59, 60, 61, 62, 63


def read_header(base):
    """Parse a WFDB .hea header. `base` is the path without extension."""
    with open(base + ".hea", "r") as fh:
        lines = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    p0 = lines[0].split()
    hdr = dict(record_name=p0[0], n_sig=int(p0[1]),
               fs=float(p0[2]) if len(p0) > 2 else 250.0,
               n_samples=int(p0[3]) if len(p0) > 3 else 0, signals=[])
    for line in lines[1:1 + hdr["n_sig"]]:
        p = line.split()
        fmt = int(re.match(r"(\d+)", p[1]).group(1))
        gain, baseline = 200.0, 0
        if len(p) > 2:
            m = re.match(r"([-\d.]+)(?:\((-?\d+)\))?", p[2])
            if m:
                gain = float(m.group(1)) or 200.0
                if m.group(2) is not None:
                    baseline = int(m.group(2))
        adc_zero = int(p[4]) if len(p) > 4 else 0
        if len(p) > 2 and "(" not in p[2]:
            baseline = adc_zero
        hdr["signals"].append(dict(file_name=p[0], fmt=fmt, gain=gain,
                                   baseline=baseline, adc_zero=adc_zero))
    return hdr


def read_signal(base, hdr=None):
    """Read a WFDB .dat file, returning physical units shaped (n_samples, n_sig)."""
    hdr = hdr or read_header(base)
    dat = os.path.join(os.path.dirname(base), hdr["signals"][0]["file_name"])
    fmt, nsig = hdr["signals"][0]["fmt"], hdr["n_sig"]

    if fmt == 16:
        raw = np.fromfile(dat, dtype="<i2").astype(np.int32)
    elif fmt == 212:
        b = np.fromfile(dat, dtype=np.uint8)
        n3 = len(b) // 3
        b = b[:n3 * 3].reshape(-1, 3).astype(np.int32)
        s1 = b[:, 0] | ((b[:, 1] & 0x0F) << 8)
        s2 = b[:, 2] | ((b[:, 1] >> 4) << 8)
        s1 = np.where(s1 > 2047, s1 - 4096, s1)
        s2 = np.where(s2 > 2047, s2 - 4096, s2)
        raw = np.empty(n3 * 2, dtype=np.int32)
        raw[0::2], raw[1::2] = s1, s2
    else:
        raise NotImplementedError(f"WFDB format {fmt} not supported")

    raw = (raw[:len(raw) // nsig * nsig].reshape(-1, nsig) if nsig > 1
           else raw.reshape(-1, 1))
    out = np.empty(raw.shape, dtype=np.float64)
    for i, s in enumerate(hdr["signals"][:nsig]):
        out[:, i] = (raw[:, i] - s["baseline"]) / s["gain"]
    return out, hdr


def read_annotations(base, ext="apn"):
    """Read a WFDB annotation file -> (sample_indices, symbols)."""
    with open(base + "." + ext, "rb") as fh:
        data = np.frombuffer(fh.read(), dtype="<u2")
    samples, symbols, t, i = [], [], 0, 0
    while i < len(data):
        word = int(data[i]); code = word >> 10; interval = word & 0x03FF
        if code == 0 and interval == 0:
            break
        if code == ANN_SKIP:
            if i + 2 >= len(data):
                break
            t += (int(data[i + 1]) << 16) | int(data[i + 2]); i += 3; continue
        if code in (ANN_NUM, ANN_SUB, ANN_CHN):
            i += 2; continue
        if code == ANN_AUX:
            i += 1 + (interval + 1) // 2; continue
        t += interval
        samples.append(t); symbols.append(WFDB_ANN_SYMBOLS.get(code, "?"))
        i += 1
    return np.array(samples, dtype=np.int64), symbols


# ---------------------------------------------------------------------------
# Signal processing
# ---------------------------------------------------------------------------
def butter_bandpass(x, fs, lo, hi, order=3):
    """Zero-phase third-order Butterworth bandpass."""
    nyq = 0.5 * fs
    b, a = butter(order, [lo / nyq, min(hi / nyq, 0.99)], btype="band")
    if len(x) <= 3 * max(len(a), len(b)):
        return x
    return filtfilt(b, a, x)


def pchip_to_grid(times, values, grid):
    """Shape-preserving interpolation onto a uniform grid."""
    ok = np.isfinite(values)
    if ok.sum() < 4:
        return np.full(len(grid), np.nan)
    out = PchipInterpolator(times[ok], values[ok], extrapolate=False)(grid)
    if np.isnan(out).any():
        out = pd.Series(out).ffill().bfill().to_numpy()
    return out


def robust_z(x):
    """Robust per-recording normalisation, clipped to suppress artefacts."""
    x = np.asarray(x, dtype=np.float64)
    finite = x[np.isfinite(x)]
    if finite.size == 0:
        return np.zeros_like(x)
    med = np.median(finite)
    scale = iqr(finite)
    scale = scale if scale > 1e-9 else (np.std(finite) or 1.0)
    return np.clip((x - med) / scale, -10, 10)


def pan_tompkins(ecg, fs):
    """Pan-Tompkins R-peak detection."""
    x = butter_bandpass(ecg, fs, 5.0, min(15.0, 0.45 * fs), order=2)
    d = np.convolve(x, np.array([1, 2, 0, -2, -1]) * (fs / 8.0), mode="same")
    sq = d ** 2
    w = max(1, int(0.150 * fs))
    integrated = np.convolve(sq, np.ones(w) / w, mode="same")

    refractory = int(0.200 * fs)
    win = int(2.0 * fs)
    if len(integrated) > win:
        local = pd.Series(integrated).rolling(win, center=True,
                                              min_periods=1).median().to_numpy()
    else:
        local = np.full_like(integrated, np.median(integrated))
    thr = local + 0.5 * np.std(integrated)
    cand, _ = find_peaks(integrated, height=thr, distance=refractory)

    search = int(0.050 * fs)
    peaks = []
    for c in cand:
        lo, hi = max(0, c - search), min(len(ecg), c + search)
        if hi > lo:
            peaks.append(lo + int(np.argmax(np.abs(ecg[lo:hi]))))
    return np.unique(np.asarray(peaks, dtype=np.int64))


def rr_and_edr(ecg, peaks, fs):
    """Build RR-interval and ECG-derived-respiration series from R peaks."""
    if len(peaks) < 4:
        return (np.array([]),) * 4
    t = peaks / fs
    rr = np.diff(t)
    rr_t = t[1:]
    keep = (rr >= 0.3) & (rr <= 2.0)
    rr, rr_t = rr[keep], rr_t[keep]
    if len(rr) < 4:
        return (np.array([]),) * 4
    med = pd.Series(rr).rolling(5, center=True, min_periods=1).median().to_numpy()
    keep2 = np.abs(rr - med) <= 0.20 * med
    rr, rr_t = rr[keep2], rr_t[keep2]
    return rr_t, rr, peaks / fs, np.abs(ecg[peaks])


# ---------------------------------------------------------------------------
# Model — must match the notebook exactly or the state_dict will not load
# ---------------------------------------------------------------------------
class ConvEncoder(nn.Module):
    def __init__(self, in_ch, emb=128, p=0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(in_ch, 32, 7, padding=3), nn.BatchNorm1d(32), nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(32, 64, 5, padding=2), nn.BatchNorm1d(64), nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Dropout(p),
            nn.Conv1d(64, 128, 3, padding=1), nn.BatchNorm1d(128), nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(128, emb, 3, padding=1), nn.BatchNorm1d(emb), nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.emb = emb

    def forward(self, x):
        return self.net(x).squeeze(-1)


class CNNBiLSTM(nn.Module):
    def __init__(self, in_ch, emb=128, hidden=64, layers=2, p=0.3):
        super().__init__()
        self.encoder = ConvEncoder(in_ch, emb, p)
        self.lstm = nn.LSTM(emb, hidden, num_layers=layers, batch_first=True,
                            bidirectional=True, dropout=p if layers > 1 else 0.0)
        self.head = nn.Sequential(nn.Linear(hidden * 2, 64), nn.ReLU(),
                                  nn.Dropout(p), nn.Linear(64, 2))

    def forward(self, x):
        B, W, C, T = x.shape
        z = self.encoder(x.reshape(B * W, C, T)).reshape(B, W, -1)
        out, _ = self.lstm(z)
        return self.head(out[:, W // 2, :])


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------
MODEL_DIR = Path(__file__).parent / "model"


def load_artifacts(model_dir=MODEL_DIR):
    """Load the trained weights, the Stage 2 classifier, and the config."""
    model_dir = Path(model_dir)
    missing = [f for f in ["cnn_bilstm.pt", "stage2.pkl", "config.json"]
               if not (model_dir / f).exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing {missing} in {model_dir}.\n"
            f"Download them from /kaggle/working/model/ after running the "
            f"training notebook, and place them in this folder.")

    cfg = json.load(open(model_dir / "config.json"))
    model = CNNBiLSTM(len(cfg["channels"]))
    model.load_state_dict(torch.load(model_dir / "cnn_bilstm.pt",
                                     map_location=DEVICE))
    model.eval().to(DEVICE)
    with open(model_dir / "stage2.pkl", "rb") as fh:
        stage2 = pickle.load(fh)
    # scikit-learn 1.9 renamed the fitted imputer dtype field. Restore the
    # field expected by its transform method when loading older artifacts.
    imputer = getattr(stage2, "named_steps", {}).get("impute")
    if imputer is not None and not hasattr(imputer, "_fill_dtype"):
        imputer._fill_dtype = imputer._fit_dtype
    return model, stage2, cfg


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------
def screen(ecg, fs, model, stage2, cfg,
           age=None, sex=None, height_cm=None, weight_kg=None):
    """
    Score an overnight ECG and return a screening decision.

    Steps, identical to training:
      1. Butterworth bandpass for morphology
      2. Pan-Tompkins R-peak detection
      3. RR-interval and ECG-derived-respiration series, with artefact rejection
      4. PCHIP resampling onto the 4 Hz grid, robust per-recording normalisation
      5. One-minute segments, scored with +/-5 minutes of context
      6. Aggregate to an apnea burden, then Stage 2 for the referral decision
    """
    ecg = np.asarray(ecg, dtype=np.float64)
    dur = len(ecg) / fs
    if dur < 600:
        raise ValueError(
            f"Recording is only {dur/60:.1f} minutes. At least 10 minutes is "
            f"needed, and a full night is what the model was trained on.")

    ecg_f = butter_bandpass(ecg, fs, cfg["ecg_band"][0],
                            min(cfg["ecg_band"][1], 0.45 * fs),
                            cfg["butter_order"])
    peaks = pan_tompkins(ecg_f, fs)
    rr_t, rr, edr_t, edr = rr_and_edr(ecg_f, peaks, fs)
    if len(rr) < 50:
        raise ValueError(
            f"Only {len(rr)} usable heartbeats were recovered. The signal is "
            f"too noisy to score reliably.")

    grid = np.arange(0, dur, 1.0 / cfg["derived_fs"])
    rr_n = robust_z(pchip_to_grid(rr_t, rr, grid))
    edr_n = robust_z(pchip_to_grid(edr_t, edr, grid))

    L = cfg["derived_len"]
    n_min = min(int(dur // cfg["segment_sec"]), len(rr_n) // L)
    if n_min < 1:
        raise ValueError("Not enough data to form a single one-minute segment.")

    segs = np.stack([np.stack([rr_n[i*L:(i+1)*L], edr_n[i*L:(i+1)*L]], axis=1)
                     for i in range(n_min)]).astype(np.float32)
    segs = segs[:, :, cfg["channels"]]

    ctx = cfg["context"]
    preds, probs = [], []
    with torch.no_grad():
        for i in range(0, n_min, 128):
            win = []
            for j in range(i, min(i + 128, n_min)):
                w = np.clip(np.arange(j - ctx, j + ctx + 1), 0, n_min - 1)
                win.append(np.transpose(segs[w], (0, 2, 1)))
            lg = model(torch.from_numpy(np.stack(win)).to(DEVICE))
            probs.append(torch.softmax(lg, 1)[:, 1].cpu().numpy())
            preds.append(lg.argmax(1).cpu().numpy())
    preds = np.concatenate(preds)
    probs = np.concatenate(probs)

    apnea_min = int(preds.sum())
    apnea_index = 60.0 * apnea_min / max(n_min, 1)

    bmi = (weight_kg / (height_cm / 100.0) ** 2
           if (height_cm and weight_kg) else np.nan)
    feat = {
        "pred_index": apnea_index,
        "age": float(age) if age else np.nan,
        "BMI": bmi,
        "is_male": {"M": 1.0, "F": 0.0}.get(str(sex).upper()[:1], np.nan)
                   if sex else np.nan,
    }
    X = np.array([[feat.get(c, np.nan) for c in cfg["stage2_features"]]])
    risk = float(stage2.predict_proba(X)[0, 1])
    refer = bool(risk >= cfg["screening_threshold"])

    return dict(minutes=n_min, apnea_minutes=apnea_min, apnea_index=apnea_index,
                per_minute_pred=preds, per_minute_prob=probs,
                risk=risk, refer=refer, bmi=bmi, hours=dur / 3600,
                heart_rate=60.0 / np.mean(rr) if len(rr) else np.nan,
                n_beats=len(peaks))


def load_ecg_from_files(files):
    """
    Accept either a WFDB pair (.hea + .dat) or a CSV of samples.

    For CSV we take the first numeric column and assume the sampling rate in
    the config, since a bare CSV carries no header telling us otherwise.
    """
    paths = [str(f) for f in files]
    hea = next((p for p in paths if p.lower().endswith(".hea")), None)
    dat = next((p for p in paths if p.lower().endswith(".dat")), None)

    if hea and dat:
        base = hea[:-4]
        # WFDB expects the .dat beside the .hea with the name given in the
        # header; Gradio may rename uploads, so we copy it into place.
        hdr = read_header(base)
        want = os.path.join(os.path.dirname(base), hdr["signals"][0]["file_name"])
        if not os.path.exists(want):
            import shutil
            shutil.copy(dat, want)
        sig, hdr = read_signal(base)
        return sig[:, 0], hdr["fs"], os.path.basename(base)

    csv = next((p for p in paths if p.lower().endswith((".csv", ".txt"))), None)
    if csv:
        df = pd.read_csv(csv)
        num = df.select_dtypes("number")
        if num.shape[1] == 0:
            raise ValueError("No numeric column found in the uploaded file.")
        return num.iloc[:, 0].to_numpy(), None, os.path.basename(csv)

    raise ValueError(
        "Upload a WFDB pair (both the .hea and .dat files) or a CSV of samples.")
