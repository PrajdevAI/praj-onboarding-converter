# Praj.AI Onboarding Engine — POC (v2: five POS systems)

Converts a customer's raw POS and accounting exports into the Praj.AI **Golden Standard** workbook
(the Tony Pizza layout) with one config-driven engine, and proves the result reconciles to the source files.

Supported POS exports: **Toast, Square, Clover, Lightspeed Restaurant, SpotOn**. The customer uploads whichever
one they have; the engine recognises it from the file headers. Accounting side: **QuickBooks Online**, **Gusto**,
**DoorDash** and processor payout statements.

The dummy customer is **Harbor Grill**, a single-location full-service restaurant. Its year (Oct 2025 to Sep 2026,
the Tony Pizza window) is exported in all five POS formats, so the same restaurant can be converted five ways and
the five Golden workbooks compared: they are identical to the cent.

## Run it (Windows, macOS, Linux)

```
pip install -r requirements.txt
python run_all.py
```

About 6 to 8 minutes for all five systems. Then open `output/<pos>/harbor_grill_<pos>_golden.xlsx` and
`output/cross_pos_comparison.md`.

- `python run_all.py --pos square --skip-generate` converts one system on the existing `raw_uploads/`
- `pytest -q` runs 31 regression tests (classification, reconciliation, layout, cross-POS equality)

## Converting a real customer

```
python engine/convert.py --input C:\customers\acme\pos_export --input C:\customers\acme\accounting ^
    --profile restaurant --business "Acme Grill" --start 2025-10-01 --end 2026-09-30 --out C:\customers\acme\output
```

Folder names do not matter; each file is identified by its column headers. Files nobody recognises are listed as
`unclassified` in `output/ingest_batches.csv`, and the fix is a new block in `engine/mappings/` (see docs/adding_a_pos.md).

## What happens

```
spec/restaurant.yaml ──► generators/ ──► raw_uploads/ ──► engine/convert.py ──► output/harbor_grill_golden.xlsx
   (menu, vendors,         (simulate a        (what the            (classify, map,            + reconciliation.md
    staff, bills)           year, render       customer would        validate, assemble,       + ingest_batches.csv
                            vendor files)      upload)               reconcile, write)         + column_completeness.csv
```

### 1. Raw uploads (what a real customer sends)

| Folder | Source | Files | Shape |
|---|---|---|---|
| `raw_uploads/square/` | Square dashboard exports, monthly | transactions-YYYY-MM.csv (payments and refunds, tips, fees, deposit id), items-YYYY-MM.csv (one row per item, modifiers flattened into `Modifiers Applied`), catalog-items.csv, transfers | Headers as posted by Square sellers; transfers and feedback reconstructed |
| `raw_uploads/clover/` | Clover exports, monthly | Clover_Orders, Clover_LineItems (modifiers as `Name:price; ...`), Clover_VoidedLineItems, Clover_Payments, Clover_Deposits | RECONSTRUCTED from Clover API field names; merchant-defined Order Type strings; epoch-ms timestamps |
| `raw_uploads/lightspeed/` | Lightspeed Restaurant K-Series Back Office, monthly | Lightspeed_Receipts (payment on the receipt row), Lightspeed_ReceiptItems (modifiers `A | B (+2.00)`, Status Sold/Voided), payouts | RECONSTRUCTED; day-first dates |
| `raw_uploads/spoton/` | SpotOn export, monthly | SpotOn_Orders, SpotOn_OrderItems (Voided flag, modifiers `A; B (+2.00)`), SpotOn_Payments, SpotOn_Deposits | RECONSTRUCTED from Export API objects; ISO-8601 with offset |
| `raw_uploads/toast/<YYYYMMDD>/` | Toast nightly data export, one folder per business day | OrderDetails, CheckDetails, ItemSelectionDetails, ModifiersSelectionDetails, PaymentDetails, AllItemsReport, TimeEntries, CashEntries | Column headers verbatim from Toast's field reference, including Toast's own `Discnt` and `Sales Cateogry` |
| `raw_uploads/toast/payouts/` | Toast Payments settlement report | Toast_Payouts_*.csv | Card sales, tips, fees, net deposit per business date |
| `raw_uploads/toast/guest_feedback/` | Toast guest feedback export | GuestFeedback_YYYY-MM.csv | Rating per order |
| `raw_uploads/shared/quickbooks/` | QuickBooks Online report exports | QBO_Bills_TransactionDetail (one row per bill line), QBO_TransactionList (bill payments, expenses, recurring bills, sales tax payments), Vendors, Chart of Accounts | Excel |
| `raw_uploads/shared/gusto/` | Gusto | Payroll journal (per employee per pay run), employee directory | CSV |
| `raw_uploads/shared/doordash/` | DoorDash merchant portal | Weekly payouts | CSV |
| `raw_uploads/shared/owner_inputs/` | Collected at onboarding | menu_costs.csv (Unit COGS per SKU), modifier_costs.csv, events_calendar.csv, promotions.csv | CSV |
| `raw_uploads/shared/enrichment/` | NOAA GHCN-Daily | Daily weather for the store's station | CSV |

Values are synthetic. The layouts follow public vendor documentation; Toast's is fully documented, the QuickBooks
and Gusto layouts are reconstructed from documented field names and must be confirmed against one real export.

### 2. The engine (`engine/`)

| Piece | What it does | Knows about Toast/QBO? |
|---|---|---|
| `ingest.py` | Walks the upload folders, **classifies** each file by filename pattern + required headers (a file can feed several entities, e.g. Square's Transactions CSV gives payments, refunds and order headers), applies the **column map**, **value maps**, **constants** and **type coercions**, runs structural **rules**: business date with 04:00 closeout, combine date+time, arithmetic expressions, tender from money columns, synthetic line ids, and **split_modifiers** for modifiers packed into one text column | No — generic |
| `mappings/*.yaml` | One **provider mapping pack** per source system (toast, square, clover, lightspeed, spoton, quickbooks_online, other_sources): detection fingerprint, source column → canonical field, value maps, types, rules, `layout_status` documented or reconstructed | Yes — this is the only place |
| `profiles/restaurant.yaml` | **Business-type profile**: channel map, tender map, weather thresholds, QBO account → Golden Department/Category/Class, cash-out sources, payroll rules | Business rules, not file formats |
| `golden_builder.py` | Assembles the Golden data sheets from canonical entities (rolls modifiers into parent items, builds Daily_Sales by business date, Cash_In by channel × tender, Cash_Out from six sources) | No |
| `golden_writer.py` | Writes the workbook: data sheets, Monthly/Weekly/Product summaries with live formulas (whole-column ranges), Dashboard with KPIs and 3 charts, README | No |
| `reconcile.py` | 29 checks of Golden vs raw files + identities; column completeness | No |

Adding the four extra POS systems to the Toast-only v1 took four YAML files and three generic engine additions
(value maps, modifier-string splitting, inherit-dining-option-from-order-header). No POS-specific Python exists.

### 3. Golden workbook

Sheet order, headers, formats and formulas match Tony Pizza. Differences, all deliberate:

- Summary formulas use whole-column ranges (`Daily_Sales!$J:$J`) instead of fixed row spans, so a different row
  count never drops rows (Tony Pizza's Product_Summary excludes its last 123 rows).
- Dashboard monthly tables are live formulas into Monthly_Summary instead of pasted values.
- Products.Gross Margin % is a formula.

Conversion rules worth knowing (also written into the workbook's README sheet and `output/reconciliation.md`):

- Voided lines are excluded; modifier revenue and modifier cost are rolled into the parent item line, so
  Sales_Item_Detail.Unit Price is the effective price including modifiers.
- Business date: orders before 04:00 belong to the previous day (Toast closeout).
- Cash_In Net Cash Received excludes tips (pass-through liability) and gift card redemptions (not new cash).
  Processing fees and DoorDash commission are Cash_Out rows, as in Tony Pizza.
- Payroll Gross Pay excludes bonus and tips; Total Cash Out = gross + bonus + employer tax + benefits.
- Cash that leaves after the period end (last day's card fees, final DoorDash payout) is next period's Cash_Out.

### 4. Reconciliation (`output/reconciliation.md`)

Every total in the workbook is tied back to the raw files: net sales to Toast item + modifier rows and to
OrderDetails, orders and line counts, per-day net sales, cash in to PaymentDetails by tender, card sales and fees
to the payout file, DoorDash to its payouts, purchases to QBO bill lines, payments to QBO bill payments, fixed bills,
expenses, sales tax, payroll to Gusto, plus internal identities. A batch is **accepted** only when every hard check
passes; otherwise it is **quarantined** and the workbook is still written for inspection.

## Layout

```
spec/restaurant.yaml            the dummy customer definition (single source of truth for the generators)
generators/truth.py             simulate orders, lines, modifiers, payments, labor, purchases, bills, payouts
generators/render.py            render the simulation as vendor-shaped files
generators/generate_raw_uploads.py
engine/ingest.py                classify + map + type (generic)
engine/mappings/                toast.yaml, quickbooks_online.yaml, other_sources.yaml
engine/profiles/restaurant.yaml business-type rules
engine/golden_builder.py        canonical -> Golden sheets
engine/golden_writer.py         workbook, formulas, charts
engine/reconcile.py             checks
engine/convert.py               CLI
tests/test_poc.py               pytest
run_all.py                      end to end
```

## Layout status per source (what still needs one real file)

| Source | Status |
|---|---|
| Toast nightly export | documented by Toast; verify portion-modifier strings and delivery-fee placement |
| Square Transactions + Items Detail | headers from seller-posted copies; verify `Modifiers Applied` format and `Source` values |
| Clover, Lightspeed, SpotOn | reconstructed from API field names; confirm every column against one real export, then edit the YAML |
| QuickBooks Online, Gusto, DoorDash | field names documented; exact header order reconstructed |

## Next steps after this POC

1. Get one real export per system from pilot customers and re-run; fix the mapping YAML where a header differs.
2. Add the recipe table (product → ingredient quantities) and an Ingredient ID on purchases so the
   "sauce is selling, buy more tomatoes" prediction has its inputs.
4. Lift `engine/` into the Lambda functions behind Step Functions; the engine is already free of file-system
   assumptions beyond an input folder.


<!-- cd "C:\Documents\AIML\PRAJ AI\PRAJ Interface\Praj_onboarding_POC\praj_onboarding_poc_v2\praj_onboarding_poc"
python -m uvicorn service.app:app --reload --port 8100

Proper setup with a virtual environment (recommended)

cd "C:\Documents\AIML\PRAJ AI\PRAJ Interface\Praj_onboarding_POC\praj_onboarding_poc_v2\praj_onboarding_poc"

# 1) one-time: create a venv
python -m venv .venv

# 2) activate it
.\.venv\Scripts\Activate.ps1

# 3) one-time: install dependencies (engine + the microservice)
pip install -r requirements.txt
pip install -r service\requirements.txt

# 4) run the converter microservice
python -m uvicorn service.app:app --reload --port 8100

Once it's running you'll see Uvicorn running on http://127.0.0.1:8100. Verify in a browser:
- http://localhost:8100/docs — interactive Swagger UI
- http://localhost:8100/health — should return {"status":"ok"} -->