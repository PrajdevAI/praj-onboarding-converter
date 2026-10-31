"""
Core logic for the converter microservice.

Everything here is a thin, stateless wrapper over engine/.  It deals with the two
things a web request adds on top of the CLI:

  1. uploaded files instead of folders  -> materialize() writes the bundle to a temp
     folder (expanding any .zip, which matters for Toast's nested folder layout) so the
     existing folder-based engine can read it unchanged.
  2. a *declared* source system          -> validate_bundle() checks what the customer
     said against what the headers actually say, before we spend time converting.

The actual raw->Golden transform (convert_bundle) is convert.py's pipeline, returning
structured data + the workbook bytes instead of printing and writing to a fixed path.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

# Make the engine importable regardless of the process CWD.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.ingest import classify, ingest_folders, load_mappings      # noqa: E402
from engine.golden_builder import GoldenBuilder, load_profile          # noqa: E402
from engine.golden_writer import write_golden                          # noqa: E402
from engine.reconcile import run_checks, run_sales_checks, column_completeness   # noqa: E402


# --------------------------------------------------------------------------- #
# format catalog (derived from the mapping packs, so it never drifts from them)
# --------------------------------------------------------------------------- #
# The systems a customer can pick from the dropdown.  Everything else a bundle may
# contain (quickbooks_online, gusto, doordash, owner_input, noaa) is shared/accounting
# and is recognised automatically — the customer does not "declare" it.
POS_SYSTEMS = ["toast", "square", "clover", "lightspeed", "spoton"]
ACCOUNTING_SYSTEMS = ["quickbooks_online", "gusto", "doordash"]
INTERNAL_SYSTEMS = {"owner_input", "noaa", "mixed"}

FRIENDLY = {
    "toast": "Toast",
    "square": "Square",
    "clover": "Clover",
    "lightspeed": "Lightspeed Restaurant",
    "spoton": "SpotOn",
    "quickbooks_online": "QuickBooks Online",
    "gusto": "Gusto",
    "doordash": "DoorDash",
}

# Document types that are nice-to-have but not required for a usable conversion.
# (Everything else for a POS system is treated as expected.)
OPTIONAL_DOCS = {
    "guest_feedback", "transfers", "deposits", "payouts", "all_items_report",
    "cash_entries", "voided_line_items", "catalog", "employee_directory",
}


def _mappings_by_system() -> dict[str, list]:
    out: dict[str, list] = {}
    for m in load_mappings():
        out.setdefault(m.source_system, []).append(m)
    return out


def supported_formats() -> dict:
    """Format catalog for the frontend dropdown + per-format expected-file manifest."""
    by_sys = _mappings_by_system()

    def manifest(system: str) -> list[dict]:
        docs = []
        for m in by_sys.get(system, []):
            det = m.detect or {}
            docs.append({
                "document_type": m.document_type,
                "entity": m.entity,
                "required": m.document_type not in OPTIONAL_DOCS,
                "filename_hint": det.get("filename"),
                "required_headers": det.get("required_headers", []),
                "layout_status": m.layout_status,
            })
        return sorted(docs, key=lambda d: (not d["required"], d["document_type"]))

    return {
        "pos_systems": [
            {"id": s, "label": FRIENDLY.get(s, s), "documents": manifest(s)}
            for s in POS_SYSTEMS if s in by_sys
        ],
        "accounting_systems": [
            {"id": s, "label": FRIENDLY.get(s, s), "documents": manifest(s)}
            for s in ACCOUNTING_SYSTEMS if s in by_sys
        ],
    }


# --------------------------------------------------------------------------- #
# materialize uploads into a folder the engine can read
# --------------------------------------------------------------------------- #
@dataclass
class SavedFile:
    original_name: str
    path: str                     # absolute path on disk inside the work dir


def materialize(uploads: list[tuple[str, bytes]], workdir: str) -> tuple[list[SavedFile], str]:
    """
    Write each (filename, bytes) into <workdir>/uploads/, expanding .zip archives so a
    Toast nightly-export folder tree is preserved.  Returns the data files we actually
    saved (.csv/.xlsx); non-data files inside a zip are ignored.
    """
    root = os.path.join(workdir, "uploads")
    os.makedirs(root, exist_ok=True)
    saved: list[SavedFile] = []

    def _keep(name: str) -> bool:
        return name.lower().endswith((".csv", ".xlsx", ".xlsm"))

    for name, data in uploads:
        base = os.path.basename(name) or "upload"
        if base.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir() or not _keep(info.filename):
                        continue
                    # preserve the archive's relative path (safely)
                    rel = info.filename.replace("\\", "/").lstrip("/")
                    rel = os.path.normpath(rel).replace("..", "_")
                    dest = os.path.join(root, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as fh:
                        fh.write(zf.read(info))
                    saved.append(SavedFile(info.filename, dest))
        elif _keep(base):
            dest = os.path.join(root, base)
            # avoid collisions when two files share a name
            i = 1
            while os.path.exists(dest):
                stem, ext = os.path.splitext(base)
                dest = os.path.join(root, f"{stem}__{i}{ext}")
                i += 1
            with open(dest, "wb") as fh:
                fh.write(data)
            saved.append(SavedFile(name, dest))
    return saved, root


# --------------------------------------------------------------------------- #
# validate: does the bundle match the format the customer declared?
# --------------------------------------------------------------------------- #
def validate_bundle(saved: list[SavedFile], declared_format: str | None) -> dict:
    """
    Classify every saved file by its headers and compare against `declared_format`.
    Reports recognised files, missing expected documents, files that look like a
    *different* POS system (so the UI can suggest a correction), and unrecognised files.
    Does not convert anything.
    """
    mappings = load_mappings()
    by_sys = _mappings_by_system()

    files_report = []
    systems_seen: set[str] = set()
    docs_seen_for_declared: set[str] = set()

    for sf in saved:
        matches = classify(sf.path, mappings)
        recognised = [{"source_system": m.source_system, "document_type": m.document_type,
                       "entity": m.entity, "layout_status": m.layout_status} for m in matches]
        for m in matches:
            if m.source_system not in INTERNAL_SYSTEMS:
                systems_seen.add(m.source_system)
            if declared_format and m.source_system == declared_format:
                docs_seen_for_declared.add(m.document_type)
        files_report.append({
            "file": sf.original_name,
            "recognized_as": recognised,
            "status": "recognized" if recognised else "unclassified",
        })

    # expected vs present for the declared POS system
    expected = []
    missing_required = []
    if declared_format and declared_format in by_sys:
        for m in by_sys[declared_format]:
            present = m.document_type in docs_seen_for_declared
            required = m.document_type not in OPTIONAL_DOCS
            expected.append({"document_type": m.document_type, "required": required, "present": present})
            if required and not present:
                missing_required.append(m.document_type)

    # mismatch detection: a POS system was recognised that isn't what they declared
    other_pos = sorted((systems_seen & set(POS_SYSTEMS)) - ({declared_format} if declared_format else set()))
    declared_detected = (declared_format in systems_seen) if declared_format else None

    accounting_seen = sorted(systems_seen & set(ACCOUNTING_SYSTEMS))
    unclassified = [f["file"] for f in files_report if f["status"] == "unclassified"]

    ok = bool(declared_detected) and not missing_required and not other_pos
    if declared_detected is False and other_pos:
        message = (f"These files look like {', '.join(FRIENDLY.get(s, s) for s in other_pos)}, "
                   f"not {FRIENDLY.get(declared_format, declared_format)}. Did you pick the wrong system?")
    elif declared_detected is False:
        message = (f"We could not recognise any {FRIENDLY.get(declared_format, declared_format)} files "
                   f"in this upload. Please check the file(s) and the selected system.")
    elif missing_required:
        message = f"Recognised as {FRIENDLY.get(declared_format, declared_format)}, but missing: {', '.join(missing_required)}."
    else:
        message = f"Looks like a valid {FRIENDLY.get(declared_format, declared_format)} upload."

    return {
        "declared_format": declared_format,
        "declared_format_detected": declared_detected,
        "ok": ok,
        "message": message,
        "expected_documents": expected,
        "missing_required_documents": missing_required,
        "suggested_formats": other_pos,
        "accounting_detected": accounting_seen,
        "unclassified_files": unclassified,
        "files": files_report,
    }


# --------------------------------------------------------------------------- #
# convert: run the full pipeline and return the workbook + reconciliation
# --------------------------------------------------------------------------- #
@dataclass
class ConvertResult:
    status: str                        # "ACCEPTED" | "QUARANTINED"
    business: str
    period: dict
    workbook_filename: str
    workbook_bytes: bytes
    reconciliation: list = field(default_factory=list)
    hard_failures: int = 0
    ingest_summary: list = field(default_factory=list)
    unclassified_files: list = field(default_factory=list)
    reconstructed_layouts: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def convert_bundle(upload_root: str, workdir: str, *, business: str, start: str, end: str,
                   profile: str = "restaurant", mode: str = "full") -> ConvertResult:
    """Convert a raw bundle to Golden and reconcile it.

    mode="full"   : onboarding — the complete Golden workbook (all sheets, formulas,
                    dashboard) reconciled against every raw file. Unchanged.
    mode="weekly" : a weekly top-up — SALES sheets only (Products, Daily_Sales,
                    Sales_Item_Detail) written as a plain workbook, reconciled with the
                    sales-only checks. Works on a POS-only week (no accounting needed) and,
                    because it carries no cash/accounting/payroll sheets, can never
                    overwrite the store's accounting when fed to the weekly ingest.
    """
    start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)

    res = ingest_folders([upload_root], load_mappings(), verbose=False)

    profile_obj = load_profile(profile)
    gb = GoldenBuilder(res.entities, profile_obj, start_d, end_d, business)

    tag_pos = [s for s in res.source_systems if s not in ("quickbooks_online", "gusto", "doordash")]
    tag = ("_" + "_".join(tag_pos)) if tag_pos else ""

    if mode == "weekly":
        sheets = gb.build_sales_only()
        ents = dict(res.entities)
        checks = run_sales_checks(sheets, ents, start_d, end_d)
        hard_fail = int((~checks["pass"] & (checks.severity == "hard")).sum())
        fname = f"{business.lower().replace(' ', '_')}{tag}_weekly_{start}_{end}.xlsx"
        out_path = os.path.join(workdir, fname)
        # plain data workbook — no formulas/dashboard needed for ingest
        with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
            for name, df in sheets.items():
                df.to_excel(xw, sheet_name=name, index=False)
    else:
        sheets = gb.build()
        ents = dict(res.entities)
        ents["payment"] = gb.ent("payment")
        checks = run_checks(sheets, ents, profile_obj, start_d, end_d)
        hard_fail = int((~checks["pass"] & (checks.severity == "hard")).sum())
        fname = f"{business.lower().replace(' ', '_')}{tag}_golden.xlsx"
        out_path = os.path.join(workdir, fname)
        sources = sorted({f"{s}:{d}" for s, d in zip(res.batches.source_system.dropna(),
                                                     res.batches.document_type.dropna())})
        write_golden(out_path, sheets, business, start_d, end_d, sources, gb.notes)

    with open(out_path, "rb") as fh:
        wb_bytes = fh.read()

    recon_rows = [{
        "check": r["check"],
        "expected_raw": round(float(r["expected (raw files)"]), 2),
        "actual_golden": round(float(r["actual (golden)"]), 2),
        "difference": round(float(r["difference"]), 2),
        "severity": r["severity"],
        "pass": bool(r["pass"]),
    } for _, r in checks.iterrows()]

    summary = (res.batches.groupby(["source_system", "document_type", "entity"], dropna=False)
               .agg(files=("file", "count"), rows=("rows", "sum")).reset_index())
    ingest_summary = [
        {"source_system": r.source_system, "document_type": r.document_type,
         "entity": r.entity, "files": int(r.files), "rows": int(r.rows)}
        for r in summary.itertuples(index=False)
    ]

    recon_layouts = sorted(set(
        res.batches.loc[res.batches.layout_status == "reconstructed", "source_system"].dropna()
        + "." + res.batches.loc[res.batches.layout_status == "reconstructed", "document_type"].dropna()
    ))

    return ConvertResult(
        status="ACCEPTED" if hard_fail == 0 else "QUARANTINED",
        business=business,
        period={"start": start, "end": end},
        workbook_filename=fname,
        workbook_bytes=wb_bytes,
        reconciliation=recon_rows,
        hard_failures=hard_fail,
        ingest_summary=ingest_summary,
        unclassified_files=[os.path.relpath(p, upload_root) for p in res.unclassified],
        reconstructed_layouts=recon_layouts,
        notes=list(gb.notes),
    )
