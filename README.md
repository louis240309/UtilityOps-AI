# UtilityOps

UtilityOps is a Flask and SQLite utility-bill intake application for a multi-location business. The current release stabilizes Phases 1–3: login, bill entry, bill history/details, document uploads, SQLite persistence, and optional n8n callback plumbing.

## Current Phase 1–3 features

- Admin and supervisor demo accounts with Werkzeug password hashes. Existing plaintext account values are upgraded to hashes on database initialization.
- SQLite-backed store and bill records, dashboard summary counts, bill list/details, and authenticated document downloads.
- Bill entry for Electricity, Water, and Gas with YYYY-MM billing periods, validated dates and non-negative numeric values.
- PDF, PNG, JPG, and JPEG uploads, limited to 16 MB and checked against file signatures.
- Duplicate prevention for one bill per store, utility, and billing period.
- Separate processing state and analysis status fields. Analysis remains `NOT_ANALYZED` unless an n8n callback reports a supported result.
- Optional n8n webhook dispatch and authenticated, request-ID-matched callback.

Demo accounts (for local development only): `admin` / `admin123` and `supervisor` / `super123`. They are not autofilled or shown in the login screen. Change these before exposing the app to other users.

## Requirements and setup

Python 3.10+ is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate             # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env                  # Windows: copy .env.example .env
```

Set `SECRET_KEY` in `.env` to a long random value for deployments. Local development can run with the documented development fallback. `DEVELOPMENT=true` permits that local fallback; set `DEVELOPMENT=false` in deployment, provide `SECRET_KEY`, and set `SESSION_COOKIE_SECURE=true` when served over HTTPS. `FLASK_DEBUG` defaults to false.

The SQLite schema and demo rows are initialized automatically when the app starts. To use a separate database, set `DATABASE_PATH` to its path; leaving it empty uses the project-local `utilityops.db`. Back up any existing database before testing schema changes.

Start the app:

```bash
python app.py
```

Open `http://127.0.0.1:5001`. The development server binds to localhost and does not enable the debugger by default.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session signing secret; required when `DEVELOPMENT=false`. |
| `DEVELOPMENT` | Allows the local-only fallback secret when true. |
| `FLASK_DEBUG` | Enables the debugger only when explicitly true and `DEVELOPMENT=true`. Keep false outside local development. |
| `SESSION_COOKIE_SECURE` | Set true when the app is served over HTTPS. |
| `PORT` | Local server port (default `5001`). |
| `DATABASE_PATH` | Optional SQLite database path. |
| `N8N_WEBHOOK_URL` | Optional n8n production webhook URL. |
| `N8N_WEBHOOK_SECRET` | Shared bearer secret for dispatch and callbacks. The legacy `N8N_SHARED_SECRET` name is still accepted. |
| `UTILITYOPS_CALLBACK_URL` | URL n8n can reach for `/api/automation/bill-result`. |
| `N8N_TIMEOUT_SECONDS` | Dispatch timeout, clamped to 1–8 seconds. |
| `AI_MODE`, `AI_PROVIDER`, `AI_API_KEY`, `AI_MODEL` | Retained for the disconnected extraction prototype; the app does not OCR or analyze bills with this module. |

Never commit real secrets or `.env` files.

## Optional n8n Phase 3 callback

Import [`n8n/phase3-bill-callback.json`](n8n/phase3-bill-callback.json) into n8n. Configure the n8n process with `N8N_WEBHOOK_SECRET` and allow environment access for the workflow Code node and expressions (n8n deployments may restrict this by default). Activate the workflow, then set the app's `N8N_WEBHOOK_URL` to the generated production webhook URL, use the same secret in the app, and set `UTILITYOPS_CALLBACK_URL` to the reachable callback endpoint.

The starter workflow authenticates and validates the event, then reports `VERIFY` with a manual-review explanation. It does not extract fields, declare a bill normal/anomalous, or write to Excel. The app saves the bill before attempting dispatch. An unconfigured or unreachable n8n service is displayed as unavailable; the saved bill remains available for review. Callback processing is authenticated, restricted to allowed fields, matched to the active request ID, and idempotent for completed requests.

For local use without n8n, leave the integration variables empty. A remote n8n instance cannot call a Flask server bound to `127.0.0.1`. Remote testing requires a deployed backend or a temporary development tunnel; do not expose the development server directly to the internet.

## Uploads and statuses

Supported uploads are PDF, PNG, JPG, and JPEG, up to 16 MB. Uploads are stored under the project `uploads/` folder and downloads require login. The app checks file signatures as well as extensions.

Processing states include `RECEIVED`, `PROCESSING`, `REVIEW`, `COMPLETED`, and `FAILED`. Analysis status is tracked separately as `NOT_ANALYZED`, `NORMAL`, or `ANOMALY`. In Phase 3, new bills begin in `RECEIVED` / `NOT_ANALYZED`; n8n's starter workflow moves them to review. No built-in anomaly detection exists.

## Current limitations and later phases

This is a hackathon foundation, not yet the complete utility-intelligence platform. Supervisor/store assignment authorization, the Admin → Supervisor → Store drill-down, separate role-specific dashboards, Excel synchronization, OCR, automatic bill extraction, historical anomaly detection, AI insights, and real-time updates are not implemented. The AI extraction module is disconnected from submission and must not be represented as active OCR. A later phase will add these features without replacing the current Flask/Jinja/SQLite foundation.
