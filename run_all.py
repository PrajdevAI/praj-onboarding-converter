"""
One command, end to end:

    python run_all.py                       generate uploads for all 5 POS systems, convert each, compare
    python run_all.py --pos square          convert only one POS (after a first full run)
    python run_all.py --skip-generate       reuse existing raw_uploads/

Layout produced:
    raw_uploads/<pos>/     one POS export set per system (toast, square, clover, lightspeed, spoton)
    raw_uploads/shared/    accounting, payroll, marketplace payouts, owner inputs, weather (POS-independent)
    output/<pos>/          Golden workbook + reconciliation report per POS
    output/cross_pos_comparison.md
"""
import argparse
import glob
import os
import subprocess
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
POS = ["toast", "square", "clover", "lightspeed", "spoton"]


def run(cmd):
    print("\n$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode not in (0, 1):
        sys.exit(r.returncode)
    return r.returncode


def compare(out_root: str, systems: list[str]):
    rows = []
    for pos in systems:
        files = glob.glob(os.path.join(out_root, pos, "*_golden.xlsx"))
        if not files:
            continue
        f = files[0]
        ds = pd.read_excel(f, sheet_name="Daily_Sales"); ci = pd.read_excel(f, sheet_name="Cash_In_Detail")
        co = pd.read_excel(f, sheet_name="Cash_Out_Detail"); sid = pd.read_excel(f, sheet_name="Sales_Item_Detail")
        rows.append({"POS": pos, "Orders": int(ds.Orders.sum()), "Item rows": len(sid), "Net Sales": round(ds["Net Sales"].sum(), 2),
                     "Gross Profit": round(ds["Gross Profit"].sum(), 2), "Dine-In": round(ds["Dine-In Net Sales"].sum(), 2),
                     "Takeout": round(ds["Takeout Net Sales"].sum(), 2), "Delivery App": round(ds["Delivery App Net Sales"].sum(), 2),
                     "Cash In": round(ci["Net Cash Received"].sum(), 2), "Cash Out": round(co.Amount.sum(), 2),
                     "Refunds": round(ds.Refunds.sum(), 2)})
    df = pd.DataFrame(rows)
    if df.empty:
        return
    same = all(df[c].nunique() == 1 for c in df.columns if c != "POS")
    print("\n" + df.to_string(index=False))
    print("\nIdentical Golden totals across POS systems:", same)
    df.to_csv(os.path.join(out_root, "cross_pos_comparison.csv"), index=False)
    with open(os.path.join(out_root, "cross_pos_comparison.md"), "w", encoding="utf-8") as fh:
        fh.write("# Same restaurant, five POS systems\n\nHarbor Grill's year exported in each vendor's format and converted by the same engine.\n\n")
        fh.write("| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns) + "\n")
        for r in df.itertuples(index=False):
            fh.write("| " + " | ".join(f"{v:,.2f}" if isinstance(v, float) else str(v) for v in r) + " |\n")
        fh.write(f"\nIdentical across POS systems: **{same}**\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-generate", action="store_true")
    ap.add_argument("--pos", choices=POS + ["all"], default="all")
    ap.add_argument("--spec", default="spec/restaurant.yaml")
    ap.add_argument("--input", default="raw_uploads")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()
    py = sys.executable
    if not args.skip_generate:
        run([py, os.path.join("generators", "generate_raw_uploads.py"), "--spec", args.spec, "--out", args.input])
    systems = POS if args.pos == "all" else [args.pos]
    results = {}
    for pos in systems:
        rc = run([py, os.path.join("engine", "convert.py"), "--input", os.path.join(args.input, pos), "--input", os.path.join(args.input, "shared"),
                  "--profile", "restaurant", "--out", os.path.join(args.out, pos)])
        results[pos] = "ACCEPTED" if rc == 0 else "QUARANTINED"
    print("\nResults:")
    for pos, r in results.items():
        print(f"  {pos:<11} {r}   -> {os.path.join(args.out, pos)}")
    compare(args.out, systems)


if __name__ == "__main__":
    main()
