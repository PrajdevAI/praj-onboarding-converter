# Converter Microservice

Wraps the raw→Golden engine behind an HTTP API so **PRAJ_IQ** can let a customer upload
their *raw* POS/accounting exports, pick the system they came from, and get back the
**Golden Standard** workbook — which PRAJ_IQ then feeds into its existing golden intake.

No engine code is POS-specific and none of it changed; this service only adds (1) uploads
→ a temp folder (expanding `.zip`, so Toast's nested folders survive) and (2) a *declared
format* check before converting.

## Run

```
pip install -r requirements.txt            # engine deps (root)
pip install -r service/requirements.txt    # fastapi, uvicorn, python-multipart
uvicorn service.app:app --reload --port 8100
```

Open http://localhost:8100/docs for the interactive Swagger UI.
Port 8100 is the canonical port — PRAJ_IQ's own backend owns 8000, so the two run side by side.
PRAJ_IQ reaches this service via the `CONVERTER_SERVICE_URL` env var (default `http://localhost:8100`).

## The flow PRAJ_IQ implements

```
customer picks system + drops raw files (or a .zip)
        │
        ├─ POST /validate   → "valid Toast upload" / "looks like Square, not Toast" / "missing payments"
        │                     (let the customer fix the selection or add files)
        │
        └─ POST /convert     → Golden workbook (.xlsx) + reconciliation verdict
                              → hand the workbook to PRAJ_IQ's existing golden intake
```

## Run with Docker

Standalone (converter only), from the project root:
```
docker compose up -d --build
curl http://localhost:8100/health
```
The image is Python 3.11-slim with only `engine/` + `service/` and their deps; it runs
`uvicorn service.app:app` on 8100 with a `/health` HEALTHCHECK. Tune verbosity with
`CONVERTER_LOG_LEVEL` (default INFO).

Together with the PRAJ_IQ stack — use PRAJ_IQ's override (keeps its prod compose untouched):
```
# from the PRAJ_IQ repo:
docker compose -f docker-compose.yml -f docker-compose.converter.yml up -d --build
```
That adds a `converter` service and sets the backend's `CONVERTER_SERVICE_URL=http://converter:8100`,
so the backend reaches it in-cluster by service name (the browser never does).

## Endpoints

### `GET /health`
Liveness probe → `{"status":"ok"}`.

### `GET /formats`
Dropdown catalog. Returns `pos_systems` (customer-selectable) and `accounting_systems`
(auto-recognised), each with the **expected documents** for that system — `document_type`,
whether it's `required`, a `filename_hint`, and the headers used to detect it. Use this to
render the format dropdown and the per-format "we'll need these files" checklist.

### `POST /validate`  — multipart/form-data
| field | type | notes |
|---|---|---|
| `files` | file(s) | one or more CSV/XLSX, or a single `.zip` of them |
| `declared_format` | text | the POS system the customer picked, e.g. `square` |

Classifies every file by its **headers** (not filename) and compares to `declared_format`.
Does **not** convert. Response:

```jsonc
{
  "declared_format": "square",
  "declared_format_detected": true,
  "ok": true,
  "message": "Looks like a valid Square upload.",
  "expected_documents": [{"document_type":"items_detail","required":true,"present":true}, ...],
  "missing_required_documents": [],
  "suggested_formats": [],          // e.g. ["square"] when they picked Toast by mistake
  "accounting_detected": ["quickbooks_online","gusto"],
  "unclassified_files": [],
  "files": [{"file":"items-2025-10.csv","status":"recognized","recognized_as":[...]}]
}
```

### `POST /convert`  — multipart/form-data
| field | type | default |
|---|---|---|
| `files` | file(s) | — (CSV/XLSX or `.zip`) |
| `business` | text | `Harbor Grill` |
| `start` | text | `2025-10-01` (YYYY-MM-DD) |
| `end` | text | `2026-09-30` |
| `profile` | text | `restaurant` |
| `download` | query bool | `false` |

- `download=false` (default) → JSON:

```jsonc
{
  "status": "ACCEPTED",            // or "QUARANTINED" if any hard check fails
  "hard_failures": 0,
  "workbook_filename": "harbor_grill_square_golden.xlsx",
  "workbook_base64": "UEsDBB...",  // the .xlsx; decode and forward to PRAJ_IQ
  "reconciliation": [{"check":"net_sales","expected_raw":..., "actual_golden":..., "pass":true}, ...],
  "ingest_summary": [...],
  "unclassified_files": [],
  "reconstructed_layouts": ["clover.orders", ...],
  "notes": [...]
}
```

- `download=true` → the raw `.xlsx` stream, with `X-Reconciliation-Status` and
  `X-Hard-Failures` response headers.

## Notes / current limits (POC)

- **Bundles, not single files.** Most systems need several files (Toast = a folder of
  nightly exports; Square = monthly items+transactions; everyone also needs the shared
  QuickBooks/Gusto/DoorDash files). Send them all in one request; a `.zip` is simplest for
  Toast. `/formats` tells the UI exactly what to ask for.
- **Validate vs convert.** `/validate` confirms "is this the format you said, and what did
  we find". The real quality gate is `/convert`'s reconciliation: `ACCEPTED` only when every
  hard check passes, otherwise `QUARANTINED` (workbook still returned for inspection).
- **Stateless.** Uploads go to a per-request temp dir that is deleted when the request ends.
  A production build would stream large workbooks to object storage instead of base64 in JSON.
- Verified against the generated `raw_uploads/`: Square + shared → `ACCEPTED`, 29/29 checks.
