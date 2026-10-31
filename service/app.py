"""
Converter microservice (FastAPI).

    uvicorn service.app:app --reload --port 8000

Endpoints
    GET  /health                     liveness
    GET  /formats                    dropdown catalog + per-format expected-file manifest
    POST /validate                   check an uploaded bundle against a declared format
    POST /convert                    transform the bundle into the Golden workbook

Both POST endpoints accept multipart/form-data with one or more `files` (CSV/XLSX, or a
.zip of them) plus form fields.  See service/README.md for the full contract.

PRAJ_IQ's flow: POST /validate  ->  (show result, let the user fix)  ->  POST /convert
->  take the returned Golden workbook and hand it to PRAJ_IQ's existing golden intake.
"""
from __future__ import annotations

import base64
import tempfile
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from service import core
from service.observability import logger

app = FastAPI(
    title="Praj.AI Onboarding Converter",
    version="0.1.0",
    description="Turns a customer's raw POS/accounting uploads into the Golden Standard workbook.",
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.middleware("http")
async def observe(request: Request, call_next):
    """Tag each request with a correlation id (honouring an inbound X-Request-ID) and log
    method, path, status and wall-clock duration. Domain specifics are logged in-handler."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.rid = rid
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("rid=%s %s %s -> 500 (%.0fms)",
                         rid, request.method, request.url.path, (time.perf_counter() - t0) * 1000)
        raise
    dur = (time.perf_counter() - t0) * 1000
    # /health is noisy; keep it at debug
    (logger.debug if request.url.path == "/health" else logger.info)(
        "rid=%s %s %s -> %s (%.0fms)", rid, request.method, request.url.path, response.status_code, dur)
    response.headers["X-Request-ID"] = rid
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/formats")
def formats():
    """Supported systems for the dropdown and the files each one is expected to include."""
    return core.supported_formats()


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    out = []
    for f in files:
        out.append((f.filename or "upload", await f.read()))
    return out


@app.post("/validate")
async def validate(
    request: Request,
    files: list[UploadFile] = File(..., description="Raw export file(s), or a .zip of them"),
    declared_format: str | None = Form(None, description="POS system the customer selected, e.g. 'square'"),
):
    """Classify the bundle by its headers and compare against the declared format. No conversion."""
    rid = getattr(request.state, "rid", "-")
    uploads = await _read_uploads(files)
    with tempfile.TemporaryDirectory(prefix="praj_validate_") as workdir:
        saved, _root = core.materialize(uploads, workdir)
        if not saved:
            raise HTTPException(status_code=400, detail="No CSV/XLSX data files found in the upload.")
        result = core.validate_bundle(saved, declared_format)
        logger.info("rid=%s validate declared=%s files=%d ok=%s missing=%s suggested=%s unclassified=%d",
                    rid, declared_format, len(saved), result["ok"],
                    result["missing_required_documents"] or "-", result["suggested_formats"] or "-",
                    len(result["unclassified_files"]))
        return result


@app.post("/convert")
async def convert(
    request: Request,
    files: list[UploadFile] = File(..., description="Raw export file(s), or a .zip of them"),
    business: str = Form("Harbor Grill"),
    start: str = Form("2025-10-01", description="period start, YYYY-MM-DD"),
    end: str = Form("2026-09-30", description="period end, YYYY-MM-DD"),
    profile: str = Form("restaurant"),
    mode: str = Form("full", description="'full' (onboarding, all sheets) or 'weekly' (sales-only top-up)"),
    download: bool = Query(False, description="true -> stream the .xlsx; false -> JSON with base64 workbook"),
):
    """
    Transform the bundle into the Golden workbook.

    mode='full' (default): the complete Golden workbook (onboarding).
    mode='weekly'        : sales-only workbook for a weekly top-up (no accounting/payroll).

    download=false (default): JSON { status, reconciliation, ingest_summary, workbook_base64, ... }
    download=true          : the .xlsx bytes, with the verdict in X-Reconciliation-Status header.
    """
    rid = getattr(request.state, "rid", "-")
    uploads = await _read_uploads(files)
    with tempfile.TemporaryDirectory(prefix="praj_convert_") as workdir:
        saved, root = core.materialize(uploads, workdir)
        if not saved:
            raise HTTPException(status_code=400, detail="No CSV/XLSX data files found in the upload.")
        t0 = time.perf_counter()
        try:
            result = core.convert_bundle(root, workdir, business=business, start=start, end=end,
                                         profile=profile, mode=mode)
        except Exception as exc:  # noqa: BLE001 - surface engine errors to the caller
            logger.exception("rid=%s convert FAILED business=%r files=%d: %s", rid, business, len(saved), exc)
            raise HTTPException(status_code=422, detail=f"Conversion failed: {exc}") from exc
        total_rows = sum(r["rows"] for r in result.ingest_summary)
        logger.info("rid=%s convert business=%r files=%d systems=%s status=%s hard_failures=%d "
                    "rows=%d unclassified=%d (%.0fms)",
                    rid, business, len(saved),
                    sorted({r["source_system"] for r in result.ingest_summary if r["source_system"]}),
                    result.status, result.hard_failures, total_rows,
                    len(result.unclassified_files), (time.perf_counter() - t0) * 1000)

        if download:
            return StreamingResponse(
                iter([result.workbook_bytes]),
                media_type=XLSX_MIME,
                headers={
                    "Content-Disposition": f'attachment; filename="{result.workbook_filename}"',
                    "X-Reconciliation-Status": result.status,
                    "X-Hard-Failures": str(result.hard_failures),
                },
            )

        return JSONResponse({
            "status": result.status,
            "business": result.business,
            "period": result.period,
            "hard_failures": result.hard_failures,
            "workbook_filename": result.workbook_filename,
            "workbook_base64": base64.b64encode(result.workbook_bytes).decode("ascii"),
            "reconciliation": result.reconciliation,
            "ingest_summary": result.ingest_summary,
            "unclassified_files": result.unclassified_files,
            "reconstructed_layouts": result.reconstructed_layouts,
            "notes": result.notes,
        })
