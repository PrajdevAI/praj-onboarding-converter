"""
pytest tests, run for every POS system that has an upload folder:
classification of every raw file, reconciliation checks, Golden layout, and
cross-POS equality of the converted totals.

    pytest -q
"""
import os
import sys
from datetime import date

import openpyxl
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.ingest import ingest_folders, load_mappings              # noqa: E402
from engine.golden_builder import GoldenBuilder, load_profile, GOLDEN_COLUMNS   # noqa: E402
from engine.reconcile import run_checks, column_completeness          # noqa: E402

RAW = os.path.join(ROOT, "raw_uploads")
POS = [p for p in ["toast", "square", "clover", "lightspeed", "spoton"] if os.path.isdir(os.path.join(RAW, p))]
pytestmark = pytest.mark.skipif(not POS, reason="run python run_all.py first to generate raw_uploads/")

_cache = {}


def pipeline(pos):
    if pos not in _cache:
        res = ingest_folders([os.path.join(RAW, pos), os.path.join(RAW, "shared")], load_mappings(), verbose=False)
        profile = load_profile("restaurant")
        gb = GoldenBuilder(res.entities, profile, date(2025, 10, 1), date(2026, 9, 30), "Harbor Grill")
        sheets = gb.build()
        ents = dict(res.entities); ents["payment"] = gb.ent("payment")
        _cache[pos] = (res, profile, sheets, ents)
    return _cache[pos]


@pytest.mark.parametrize("pos", POS)
def test_every_file_is_classified(pos):
    res, _, _, _ = pipeline(pos)
    assert res.unclassified == [], f"unclassified files: {res.unclassified[:5]}"


@pytest.mark.parametrize("pos", POS)
def test_no_mapping_missed_a_column(pos):
    res, _, _, _ = pipeline(pos)
    missing = res.batches[res.batches.missing_columns.fillna("") != ""]
    assert missing.empty, missing.head().to_string()


@pytest.mark.parametrize("pos", POS)
def test_golden_sheets_have_exact_columns(pos):
    _, _, sheets, _ = pipeline(pos)
    for name, cols in GOLDEN_COLUMNS.items():
        assert list(sheets[name].columns) == cols, name
    assert len(sheets["Daily_Sales"]) == 365


@pytest.mark.parametrize("pos", POS)
def test_reconciliation_hard_checks_pass(pos):
    _, profile, sheets, ents = pipeline(pos)
    checks = run_checks(sheets, ents, profile, date(2025, 10, 1), date(2026, 9, 30))
    failed = checks[(~checks["pass"]) & (checks.severity == "hard")]
    assert failed.empty, failed.to_string()


@pytest.mark.parametrize("pos", POS)
def test_no_fully_blank_columns(pos):
    _, _, sheets, _ = pipeline(pos)
    comp = column_completeness(sheets)
    assert comp[comp.blank_pct == 100.0].empty


def test_same_restaurant_gives_same_golden_totals_from_every_pos():
    totals = {}
    for pos in POS:
        _, _, sheets, _ = pipeline(pos)
        ds = sheets["Daily_Sales"]
        totals[pos] = (round(ds["Net Sales"].sum(), 2), int(ds.Orders.sum()), len(sheets["Sales_Item_Detail"]),
                       round(sheets["Cash_In_Detail"]["Net Cash Received"].sum(), 2), round(sheets["Cash_Out_Detail"].Amount.sum(), 2))
    assert len(set(totals.values())) == 1, totals


@pytest.mark.parametrize("pos", POS)
def test_workbook_matches_tony_pizza_layout(pos):
    import glob
    files = glob.glob(os.path.join(ROOT, "output", pos, "*_golden.xlsx"))
    if not files:
        pytest.skip("workbook not built yet")
    wb = openpyxl.load_workbook(files[0], read_only=True)
    assert wb.sheetnames == ["README", "Products", "Employees", "Daily_Sales", "Sales_Item_Detail", "Cash_In_Detail", "Raw_Material_Purchases",
                             "Payroll_Detail", "Bills_Fixed_Monthly", "Cash_Out_Detail", "Monthly_Summary", "Weekly_Summary", "Product_Summary", "Dashboard"]
