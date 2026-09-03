# Sleep apnea screening from a single ECG lead

A CNN-BiLSTM trained on the PhysioNet Apnea-ECG database estimates overnight
apnea burden from one ECG channel and indicates whether a sleep study is warranted.

**Screening tool, not a diagnosis. Research use only.**

---

## Setup in VS Code

**1. Open this folder** — File → Open Folder → select `apnea_app`.

**2. Add your trained model.** From Kaggle, download `/kaggle/working/model/`
and `/kaggle/working/samples/` (Output panel, right sidebar) and drop the files in
so the folder looks like:

```
apnea_app/
├── app.py
├── pipeline.py
├── requirements.txt
├── model/
│   ├── cnn_bilstm.pt
│   ├── stage2.pkl
│   └── config.json
└── samples/
    ├── index.json
    └── a04.hea, a04.dat, ...
```

**3. Create an environment.** In the VS Code terminal (Ctrl+`):

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

**4. Run.**

```bash
python app.py
```

Open http://127.0.0.1:7860. Ctrl+C to stop.

---

## A public link

For a temporary URL (72 hours, handy at a poster session), edit the last line
of `app.py`:

```python
demo.launch(share=True, **launch_kw)
```

For a permanent URL, create a Hugging Face Space with SDK **Gradio** and upload
this whole folder including `model/` and `samples/`.

---

## Troubleshooting

**"Missing [...] in model"** — the three files are not in `model/`.

**Slow first run** — PyTorch loads on first import; subsequent runs are faster.

**"Only N usable heartbeats"** — the uploaded signal is too noisy, or it is not
an ECG.

**Uploading your own recording** — select *both* the `.hea` and `.dat` files
together, or upload a CSV whose first numeric column is the ECG.

---

## How it works

1. Third-order Butterworth bandpass, 0.5–45 Hz
2. Pan-Tompkins R-peak detection
3. RR-interval and ECG-derived respiration series, with artefact rejection
4. PCHIP resampling to 4 Hz, robust per-recording normalisation
5. One-minute segments scored with ±5 minutes of context by the CNN-BiLSTM
6. Aggregation to an apnea burden, then a referral decision

`pipeline.py` is copied verbatim from the training notebook. Changing the signal
processing without retraining will invalidate the reported performance.
