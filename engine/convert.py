"""
Step 2 of the POC: convert a customer's raw uploads into the Golden Standard workbook.

    python -m engine.convert --input raw_uploads --profile restaurant --out output

Produces:
    output/<business>_golden.xlsx          the Golden workbook (Tony Pizza layout)
    output/ingest_batches.csv              every file found, how it was classified, rows read
    output/reconciliation.csv/.md          raw-vs-golden checks
    output/column_completeness.csv         blank counts per Golden column
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ingest import ingest_folders, load_mappings            # noqa: E402
from engine.golden_builder import GoldenBuilder, load_profile      # noqa: E402
from engine.golden_writer import write_golden                       # noqa: E402
from engine.reconcile import run_checks, column_completeness        # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", action="append", help="upload folder; repeat for several (e.g. the POS folder and the shared accounting folder)")
    ap.add_argument("--profile", default="restaurant")
    ap.add_argument("--business", default="Harbor Grill")
    ap.add_argument("--start", default="2025-10-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--out", default="output")
    args = ap.parse_args(argv)
    args.input = args.input or ["raw_uploads"]
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()

    print(f"[1/5] Classifying and reading files in {', '.join(args.input)} ...")
    res = ingest_folders(args.input, load_mappings())
    res.batches.to_csv(os.path.join(args.out, "ingest_batches.csv"), index=False)
    summary = res.batches.groupby(["source_system", "document_type", "entity"], dropna=False).agg(files=("file", "count"), rows=("rows", "sum")).reset_index()
    print(summary.to_string(index=False))
    if res.unclassified:
        print(f"  unclassified files: {len(res.unclassified)} (see ingest_batches.csv)")
    print(f"  ({time.time()-t0:.1f}s)")

    print("[2/5] Building Golden sheets ...")
    profile = load_profile(args.profile)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    gb = GoldenBuilder(res.entities, profile, start, end, args.business)
    sheets = gb.build()
    ents = dict(res.entities); ents["payment"] = gb.ent("payment")      # payments with separate refund rows merged
    for k, v in sheets.items():
        print(f"  {k:<24} {len(v):>7,} rows x {len(v.columns)} cols")
    print(f"  ({time.time()-t0:.1f}s)")

    print("[3/5] Reconciling against raw files ...")
    checks = run_checks(sheets, ents, profile, start, end)
    checks.to_csv(os.path.join(args.out, "reconciliation.csv"), index=False)
    comp = column_completeness(sheets)
    comp.to_csv(os.path.join(args.out, "column_completeness.csv"), index=False)
    n_fail = int((~checks["pass"] & (checks.severity == "hard")).sum())
    for _, r in checks.iterrows():
        flag = "PASS" if bool(r["pass"]) else "FAIL"
        print(f"  {flag}  {r['check']}: raw {r['expected (raw files)']:,.2f} vs golden {r['actual (golden)']:,.2f} (diff {r['difference']:,.2f})")
    blanks = comp[comp.blank_pct == 100.0]
    print(f"  fully blank columns: {len(blanks)}" + (f" -> {blanks[['sheet','column']].values.tolist()}" if len(blanks) else ""))

    with open(os.path.join(args.out, "reconciliation.md"), "w", encoding="utf-8") as f:
        f.write(f"# Reconciliation report - {args.business}\n\n")
        f.write(f"Period {start} to {end}. Hard-check failures: **{n_fail}**. Status: **{'ACCEPTED' if n_fail == 0 else 'QUARANTINED'}**\n\n")
        f.write("| Check | Raw files | Golden | Diff | Result |\n|---|---:|---:|---:|---|\n")
        for r in checks.itertuples(index=False):
            f.write(f"| {r[0]} | {r[1]:,.2f} | {r[2]:,.2f} | {r[3]:,.2f} | {'PASS' if r[4] else 'FAIL'} |\n")
        f.write("\n## Column completeness (blank % per Golden column)\n\n| Sheet | Column | Rows | Blank | Blank % |\n|---|---|---:|---:|---:|\n")
        for r in comp.itertuples(index=False):
            f.write(f"| {r[0]} | {r[1]} | {r[2]:,} | {r[3]:,} | {r[4]} |\n")
        f.write("\n## Conversion notes\n\n" + "\n".join(f"- {n}" for n in gb.notes) + "\n")

    if n_fail:
        print(f"[4/5] {n_fail} hard checks FAILED -> batch quarantined; workbook still written for inspection")
    else:
        print("[4/5] All hard checks passed -> batch accepted")

    pos = [x for x in res.source_systems if x not in ("quickbooks_online", "gusto", "doordash")]
    tag = ("_" + "_".join(pos)) if pos else ""
    out_path = os.path.join(args.out, f"{args.business.lower().replace(' ', '_')}{tag}_golden.xlsx")
    recon = res.batches[res.batches.layout_status == "reconstructed"]
    if len(recon):
        print(f"  note: {recon.document_type.nunique()} document layouts are RECONSTRUCTED from vendor field docs (not verified against a real export): "
              + ", ".join(sorted(set(recon.source_system + "." + recon.document_type))))
    print(f"[5/5] Writing {out_path} ...")
    sources = sorted({f"{s}:{d}" for s, d in zip(res.batches.source_system.dropna(), res.batches.document_type.dropna())})
    write_golden(out_path, sheets, args.business, start, end, sources, gb.notes)
    print(f"Done in {time.time()-t0:.1f}s")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
