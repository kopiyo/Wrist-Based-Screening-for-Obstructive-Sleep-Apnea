# Nightwise — wrist-based sleep apnea screening research prototype

**Screening tool, not a diagnosis. Research use only.**

This repository contains a two-stage CNN-BiLSTM + logistic regression model trained on single-lead ECG from the PhysioNet Apnea-ECG cohort. It is a research prototype, not a clinical product. Wrist/PPG-derived intervals are **unvalidated**; the reported ECG performance figures do not describe wearable performance.

## Start the Phase 1 app

Docker Compose builds the FastAPI API and responsive React app. Docker Engine and Compose are required. The model files in `model/` are included in the build context.

```bash
cp .env.example .env
docker compose up --build
```

Open the web app at <http://localhost:8080>. The API and interactive OpenAPI documentation are at <http://localhost:8000/v1/health> and <http://localhost:8000/docs>.

For local development, use Python 3.11 and Node.js 20:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --extra-index-url https://download.pytorch.org/whl/cpu -r apps/api/requirements.txt
uvicorn apps.api.main:app --reload
```

In another terminal:

```bash
cd apps/web
npm ci
npm run dev
```

The web development server is at <http://localhost:5173>. Set `VITE_API_URL` and `CORS_ORIGINS` from `.env.example` when using different ports or hosts.

## Phase 1 capabilities

- **ECG file analysis:** WFDB `.hea` + `.dat` pair, or a CSV ECG signal (assumed 100 Hz).
- **RR interval analysis:** JSON `POST /v1/analyze/intervals`, or a CSV with `rr_ms` and `ts` columns. Timestamps are seconds and must be strictly increasing. JSON `ts` values are relative to `start_ts` when it is supplied. Minimum series duration is 10 minutes.
- **In-memory jobs:** `GET /v1/jobs/{id}`, `GET /v1/results/{id}`, and `GET /v1/jobs/{id}/events` (SSE).
- **Model card:** `GET /v1/model-card` uses values from `model/config.json`.
- The frontend presents screening results, minute probabilities, source quality, ECG model facts, empty states, and source validation status. A persistent disclaimer appears throughout the UI.

The Phase 1 API holds jobs and results in process memory and stores no uploads or results on disk. Data disappears when the API process restarts. It does not yet include accounts, persistent consent, encryption-at-rest, or `DELETE /me/data`; those depend on the Phase 2 storage/auth implementation. Do not use this deployment for identifiable health data.

## Architecture and phases

```text
apps/web (React + Vite + TypeScript)
       │ versioned REST + SSE
apps/api (FastAPI, Pydantic v2, in-memory job runner)
       ├── adapters.py (file/interval adapter contract)
       └── pipeline.py (unchanged ECG pipeline + RR entry point)
model/ (fixed trained artifacts)      packages/shared (typed API client)
```

- **Phase 1 — file ingestion and screening:** ECG and timestamped RR CSV/JSON input; responsive analysis and result UI; no persistence by default.
- **Phase 2 — live ingestion and platform foundation:** Web Bluetooth Heart Rate Service support, local buffering and resumable batch upload; explicit storage consent, encryption, authentication, PostgreSQL/SQLAlchemy/Alembic, Redis/RQ, job deletion, device registration, alert rules and delivery log. Browser/device support varies; Bluetooth requires a compatible browser and device. Web Push/email/FCM/APNs require external credentials and service configuration.
- **Phase 3 — companion apps:** iOS HealthKit and Android Health Connect integration, with platform-specific background-delivery limits documented before claiming availability.

`apps/api/adapters.py` defines the adapter boundary. A device adapter must produce paired RR intervals and timestamps; the shared pipeline then rejects artefacts, resamples to the existing 4 Hz grid and uses the same normalization, minute windows, context, CNN-BiLSTM and stage-two classifier. The RR input path skips only ECG filtering and R-peak detection. Its source is always returned as unvalidated.

## Model facts

Values below come from `model/config.json`; rounding matches the UI.

| Measure | Value |
|---|---:|
| Stage-two AUC | 0.957 |
| Screening sensitivity | 97.6% |
| Screening specificity | 88.5% |
| Referral threshold | 0.3032 |
| Clinical endpoint | AHI ≥ 15 |
| Patients | 67 |

These figures describe the ECG model and its reported evaluation only. They must not be applied to watch or PPG-derived intervals. The model estimates a screening result; it does not diagnose apnea, and its per-minute burden estimate is not clinical AHI.

## Limitations

- The training/evaluation cohort is small (67 patients), historical, and drawn from a referred population. Results may not generalize to a community screening population.
- Each patient contributes limited recording data. Night-to-night biological variability and modern scoring criteria are not represented adequately.
- The model uses single-lead ECG at 100 Hz; consumer wearable intervals are a different signal source.
- **No external validation of RR/PPG/watch input is included.** RR results carry an “Unvalidated source” badge. ECG sensitivity, specificity, and AUC are not wearable performance measures.
- The current RR-only model configuration uses interval features; a wearable result remains experimental even if its pipeline returns a score.
- This Phase 1 service is a local research demonstration. It has no authentication or durable consent/storage boundary.

## Validation questions for the data scientist

1. What paired sleep-study cohort and endpoints should be used to validate PPG/watch intervals against ECG and polysomnography?
2. Should validation compare interval timing, minute-level labels, patient-level referral, and calibration separately by device and population?
3. What minimum usable-beat ratio and gap policy should cause an RR session to be rejected rather than scored?
4. Is the current stage-two input (`pred_index` plus missing demographics) appropriate for interval-only input, or does the wearable pathway require a separately trained/calibrated head?

## Development commands

```bash
make api                 # FastAPI reload server
make web                 # Vite server
make build-web           # TypeScript check + production bundle
make test-api            # adapter tests and model/cache golden test
make up                  # Docker Compose
```

The `.npz` cache files contain notebook-preprocessed minute features and labels, not saved prediction outputs. The golden inference test uses a fixed cached record and an expected output fingerprint. Model artifacts and preprocessing constants must not be changed without retraining and new validation.
