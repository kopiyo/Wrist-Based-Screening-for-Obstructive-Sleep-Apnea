# Nightwise — wrist-based sleep apnea screening research prototype

**Screening tool, not a diagnosis. Research use only.**

This repository contains a two-stage CNN-BiLSTM + logistic regression model trained on single-lead ECG from the PhysioNet Apnea-ECG cohort. It is a research prototype, not a clinical product. Wrist/PPG-derived intervals are **unvalidated**; the reported ECG performance figures do not describe wearable performance.

## Start the Phase 1 app

Docker Compose builds the FastAPI API and responsive React app. Docker Engine and Compose are required. The model files in `model/` are included in the build context.

```bash
cp .env.example .env
docker compose up --build
```

Open the web app at <http://localhost:8080>. With the example configuration, the API and interactive OpenAPI documentation are at <http://localhost:8001/v1/health> and <http://localhost:8001/docs>. Set `API_HOST_PORT` and `VITE_API_URL` in `.env` together if you need another API host port.

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

The web development server is at <http://localhost:5173>. Set `VITE_API_URL` and `CORS_ORIGINS` from `.env.example` when using different ports or hosts. Compose starts Mailpit at <http://localhost:8025>; verification emails are captured there in development.

## Phase 1 capabilities

- **ECG file analysis:** WFDB `.hea` + `.dat` pair, or a CSV ECG signal (assumed 100 Hz).
- **RR interval analysis:** JSON `POST /v1/analyze/intervals`, or a CSV with `rr_ms` and `ts` columns. Timestamps are seconds and must be strictly increasing. JSON `ts` values are relative to `start_ts` when it is supplied. Minimum series duration is 10 minutes.
- **Phase 1 in-memory jobs:** `GET /v1/jobs/{id}`, `GET /v1/results/{id}`, and `GET /v1/jobs/{id}/events` (SSE). The Phase 2 deployment queues work with RQ and stores job metadata in Redis; upload data stays in encrypted temporary blobs.
- **Model card:** `GET /v1/model-card` uses values from `model/config.json`.
- The frontend presents screening results, minute probabilities, source quality, ECG model facts, empty states, and source validation status. A persistent disclaimer appears throughout the UI.

Phase 1 analysis remains available without an account and is ephemeral unless the user explicitly consents to save that analysis while signed in. Phase 2 saved data is encrypted at rest and available through authenticated history and privacy endpoints. The system remains a research prototype; do not treat results as a diagnosis or use the service as a substitute for clinical care.

## Phase 2 approved contract and privacy operations

The Phase 2 schema is documented in [`docs/phase-2-schema.md`](docs/phase-2-schema.md), and [`docs/phase-2-openapi.yaml`](docs/phase-2-openapi.yaml) is the API contract. Phase 2 adds account-backed storage, consent, alerts, history, and privacy operations to the app. Configure the secrets in `.env.example` before deploying; the local defaults are for development only.

Phase 2 allows anonymous analysis, which is ephemeral. Saving requires a verified account, an active consent record, and explicit `persist_result=true` on that analysis. The API enforces a 100 MB combined upload limit. RQ uses JSON serialization only (never pickle), while Redis is password-protected on a private network with disk persistence disabled. Upload bytes do not enter Redis: encrypted short-lived blobs live on a dedicated private volume, are referenced by opaque IDs, and are deleted on completion or after one hour.

### Encryption key hierarchy

`MASTER_KEY` must come from the deployment environment or a secret store and must not be committed. Each account has a random data-encryption key (DEK). Result and chart payloads are encrypted with the DEK; the DEK is itself wrapped by the master key. Database rows contain encrypted payloads and wrapped key material, never plaintext health values. Keep key versions so master-key rotation can rewrap DEKs in a resumable migration. Retain the old key until all rows are rewrapped and backups made with it have expired. Losing every recoverable version of the master key makes the associated encrypted results permanently unreadable; there is no plaintext fallback.

### Backups, deletion, logs, and browser security

Encrypted database backups are retained for 30 days. Hard deletion removes live rows and the user's wrapped DEK immediately. Before a database restore is opened to API or worker traffic, keep it quarantined and run `RESTORE_QUARANTINE=1 python -m apps.api.restore_tombstones` against the restored database; the separate tombstone volume is required for that replay. Backups and tombstones expire after 30 days. Redis has no disk persistence and the temporary blob volume is excluded from backups. Logs must never contain email addresses, uploads, result payloads, cookies/tokens, WebAuthn assertions, recovery codes, or push subscription values.

Production uses HTTPS, host-only `Secure; HttpOnly; SameSite=Lax` access and refresh cookies, a first-party CSRF cookie/header pair, and a one-use five-minute purpose-bound reauthentication token for export/delete. CORS allows only exact configured origins; wildcard origins are prohibited with credentials. Configure `CORS_ORIGINS` as a comma-separated allowlist and use Mailpit for local development email capture. Generate Web Push VAPID keys with `npx web-push generate-vapid-keys`; keep the private key in a secret store and provide the public key to the frontend. Email alerts require a verified address. Push/email notification bodies contain no score or health values and always include the screening disclaimer; RR/PPG notifications also say “Unvalidated source.”

Rate limits are specified in the OpenAPI contract: login 10/IP/15 minutes and 5/account/15 minutes, registration 5/IP/hour, verification email 3/account/hour and 10/IP/hour, export 3/account/hour, and alert delivery 20 attempts/account/rolling hour. Alert rules only evaluate saved nights: worsening trend compares the mean of the newest three saved nights against the immediately preceding three (minimum six nights in 30 days, increase ≥0.10); morning summaries run at 07:00 in the account's IANA timezone; quiet hours use that timezone. “Device silent overnight” remains disabled and labeled “needs live device” until Phase 3.

## Architecture and phases

```text
apps/web (React + Vite + TypeScript)
       │ versioned REST + SSE
apps/api (FastAPI, Pydantic v2, RQ worker for Phase 2)
       ├── adapters.py (file/interval adapter contract)
       └── pipeline.py (unchanged ECG pipeline + RR entry point)
model/ (fixed trained artifacts)      packages/shared (typed API client)
```

- **Phase 1 — file ingestion and screening:** ECG and timestamped RR CSV/JSON input; responsive analysis and result UI; no persistence by default.
- **Phase 2 — account and platform foundation:** explicit per-analysis storage consent, authentication, email verification, PostgreSQL/SQLAlchemy/Alembic, encrypted saved nights, Redis/RQ, privacy export/delete, device records, alert rules and delivery log. Web Push and email delivery require external credentials and service configuration. Live device pairing and overnight presence are Phase 3 work.
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
- This remains a research demonstration. Screening outputs are not diagnoses and wearable-source performance is unvalidated.

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
