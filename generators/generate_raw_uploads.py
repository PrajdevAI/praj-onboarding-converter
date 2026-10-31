"""
Step 1 of the POC: render the dummy customer's raw uploads from spec/restaurant.yaml.

    python -m generators.generate_raw_uploads --spec spec/restaurant.yaml --out raw_uploads
"""
import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.truth import load_spec, simulate      # noqa: E402
from generators.render import render_toast, render_doordash, render_qbo, render_gusto, render_owner_and_enrichment   # noqa: E402
from generators.render_pos import render_all_pos                   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="spec/restaurant.yaml")
    ap.add_argument("--out", default="raw_uploads")
    args = ap.parse_args()

    t0 = time.time()
    spec = load_spec(args.spec)
    print(f"Simulating {spec['business']['name']} {spec['business']['period_start']} to {spec['business']['period_end']} ...")
    T = simulate(spec)
    print(f"  orders={len(T.orders):,} lines={len(T.lines):,} modifiers={len(T.mods):,} payments={len(T.payments):,} "
          f"purchase lines={len(T.bills):,} payroll rows={len(T.payroll):,}  ({time.time()-t0:.1f}s)")
    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    # POS exports: one folder per system (a customer uploads exactly one of these)
    render_toast(T, args.out)
    render_all_pos(T, args.out)
    # shared, POS-independent uploads: accounting, payroll, marketplace payouts, owner inputs, weather
    shared = os.path.join(args.out, "shared")
    render_doordash(T, shared)
    render_qbo(T, shared)
    render_gusto(T, shared)
    render_owner_and_enrichment(T, shared)
    n_files = sum(len(f) for _, _, f in os.walk(args.out))
    print(f"Rendered {n_files:,} files into {args.out}/  ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
