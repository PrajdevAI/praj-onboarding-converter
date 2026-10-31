"""
Reconciliation: compare the Golden sheets against the raw source files
(via the canonical entities) and check column completeness.

Every check returns (name, expected, actual, passed, detail).  A batch is only
"accepted" when all hard checks pass.
"""
from __future__ import annotations

import pandas as pd


def _close(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(float(a) - float(b)) <= tol


def run_checks(sheets: dict, entities: dict, profile: dict, start=None, end=None) -> pd.DataFrame:
    checks = []

    def add(name, expected, actual, tol=0.05, hard=True, detail=""):
        ok = _close(expected, actual, tol)
        checks.append({"check": name, "expected (raw files)": round(float(expected), 2), "actual (golden)": round(float(actual), 2),
                       "difference": round(float(actual) - float(expected), 2), "pass": ok, "severity": "hard" if hard else "soft", "detail": detail})

    ln = entities["sales_line"]; md = entities.get("sales_line_modifier", pd.DataFrame(columns=["net", "gross", "void", "business_date"])); oh = entities["order_header"]; pay = entities["payment"]
    md = md.copy()
    if len(md):
        md["void"] = md["void"].astype(bool)
        if "included_in_parent" in md.columns:          # Square/Clover-style: modifier price already inside the parent line
            inc = md["included_in_parent"].fillna(False).astype(bool)
            md.loc[inc, ["net", "gross"]] = 0.0
    if "status" not in pay.columns:
        pay = pay.assign(status="CAPTURED")
    pay = pay.copy(); pay["status"] = pay["status"].fillna("CAPTURED")
    ds = sheets["Daily_Sales"]; sid = sheets["Sales_Item_Detail"]; ci = sheets["Cash_In_Detail"]; co = sheets["Cash_Out_Detail"]

    # --- sales: Toast item + modifier files (non-void) vs Golden
    raw_net = ln[~ln.void].net.sum() + md[~md.void].net.sum()
    raw_gross = ln[~ln.void].gross.sum() + md[~md.void].gross.sum()
    raw_tax = ln[~ln.void].tax.sum()
    add("Net sales: POS item+modifier rows (non-void) = Daily_Sales.Net Sales", raw_net, ds["Net Sales"].sum())
    add("Net sales: Daily_Sales = Sales_Item_Detail", ds["Net Sales"].sum(), sid["Net Sales"].sum())
    add("Gross sales: POS = Daily_Sales.Gross Sales", raw_gross, ds["Gross Sales"].sum())
    add("Sales tax: POS item tax = Daily_Sales.Sales Tax", raw_tax, ds["Sales Tax"].sum())
    add("Net sales: POS order headers net (non-voided) = Daily_Sales.Net Sales", oh[~oh.voided].net_sales.sum(), ds["Net Sales"].sum(), tol=1.0,
        detail="order header vs line level; rounding across modifiers")
    add("Orders: POS order headers (non-voided) = Daily_Sales.Orders", oh[~oh.voided].order_id.nunique(), ds["Orders"].sum(), tol=0)
    add("Lines: POS non-void item rows = Sales_Item_Detail rows", (~ln.void).sum(), len(sid), tol=0)
    # per-day: every business date matches
    day_raw = (ln[~ln.void].groupby("business_date").net.sum().add(md[~md.void].groupby("business_date").net.sum(), fill_value=0))
    day_gold = ds.set_index(ds["Date"].dt.date)["Net Sales"]
    bad_days = int(((day_raw - day_gold.reindex(day_raw.index).fillna(0)).abs() > 0.05).sum())
    add("Per-day net sales mismatches (count of days)", 0, bad_days, tol=0)

    # --- payments: Toast PaymentDetails vs Cash_In
    live = pay[pay.status.astype(str).str.upper() != "VOIDED"]
    excl = profile["tenders"]["exclude_from_cash_in"]
    add("Cash in: POS payments (excl. gift card redemptions) = Cash_In.Net Cash Received",
        live[~live.tender_type.isin(excl)].amount.sum(), ci["Net Cash Received"].sum())
    add("Cash in + gift card redemptions = POS total collected", live.amount.sum(),
        ci["Net Cash Received"].sum() + live[live.tender_type.isin(excl)].amount.sum(), detail="gift card redemptions are not new cash")
    add("Refunds: POS payments = Daily_Sales.Refunds", pay.refund_amount.sum(), ds["Refunds"].sum())
    add("Refunds: POS payments = Cash_Out Customer Refunds", pay.refund_amount.sum(), co[co["Expense Category"] == "Customer Refunds"].Amount.sum())

    # --- settlements
    cp = entities.get("settlement_card", pd.DataFrame())
    if not cp.empty and end is not None:
        cp = cp[(cp.payout_date >= start) & (cp.payout_date <= end)]      # cash leaving after the period is next period's cash-out
    if not cp.empty:
        add("Card fees: processor payouts = Cash_Out Merchant Processing Fees", cp.fees.sum(),
            co[co["Expense Category"] == "Merchant Processing Fees"].Amount.sum())
        cp_all = entities["settlement_card"]
        add("Card sales: processor payouts = Cash_In Card Settlement", cp_all.card_sales.sum(), ci[ci["Payment Method"] == "Card Settlement"]["Net Cash Received"].sum())
    dp = entities.get("settlement_platform", pd.DataFrame())
    if not dp.empty and end is not None:
        dp = dp[(dp.payout_date >= start) & (dp.payout_date <= end)]
    if not dp.empty:
        dp_all = entities["settlement_platform"]
        add("DoorDash subtotal+tax = Cash_In Platform Transfer", (dp_all.subtotal + dp_all.tax).sum(), ci[ci["Payment Method"] == "Platform Transfer"]["Net Cash Received"].sum())
        add("DoorDash commission+marketing = Cash_Out Delivery App Commission", (dp.commission.abs() + dp.marketing.abs()).sum(),
            co[co["Expense Category"] == "Delivery App Commission"].Amount.sum())

    # --- purchases and accounting
    pl = entities["purchase_line"]; tx = entities["accounting_txn"]
    rmp = sheets["Raw_Material_Purchases"]
    add("Purchases: QBO bill lines = Raw_Material_Purchases.Line Total", pl.line_total.sum(), rmp["Line Total"].sum())
    add("Purchase lines: QBO bill rows = Raw_Material_Purchases rows", len(pl), len(rmp), tol=0)
    bp = tx[tx.txn_type.isin(profile["qbo_transaction_types"]["bill_payment"])]
    bp_inv = bp[~bp.reference.astype(str).str.startswith("BP-BILL-")]
    add("Inventory payments: QBO Bill Payments (vendor invoices) = Cash_Out Cost of Goods", bp_inv.amount.abs().sum(),
        co[co["Accounting Class"] == "Cost of Goods"].Amount.sum(), tol=1.0, detail="payments dated inside the period only")
    fb = sheets["Bills_Fixed_Monthly"]
    bills = tx[tx.txn_type.isin(profile["qbo_transaction_types"]["recurring_bill"])]
    add("Fixed bills: QBO recurring Bills = Bills_Fixed_Monthly.Amount", bills.amount.sum(), fb.Amount.sum())
    exp = tx[tx.txn_type.isin(profile["qbo_transaction_types"]["expense"])]
    add("Expenses: QBO Expense rows = Cash_Out 'QBO expense' rows", exp.amount.abs().sum(), co[co.Notes == "QBO expense"].Amount.sum())
    st = tx[tx.txn_type.isin(profile["qbo_transaction_types"]["sales_tax"])]
    add("Sales tax remitted: QBO = Cash_Out Sales Tax Remittance", st.amount.abs().sum(), co[co["Expense Category"] == "Sales Tax Remittance"].Amount.sum())

    # --- payroll
    pr = entities["payroll_line"]; pdx = sheets["Payroll_Detail"]
    add("Payroll: Gusto Total employer cost = Payroll_Detail.Total Cash Out", pr.total_employer_cost.sum(), pdx["Total Cash Out"].sum())
    add("Payroll: Payroll_Detail.Total Cash Out = Cash_Out Payroll dept", pdx["Total Cash Out"].sum(), co[co.Department == "Payroll"].Amount.sum())
    add("Payroll identity: Gross + Bonus + Employer Tax + Benefits = Total Cash Out",
        (pdx["Gross Pay"] + pdx["Bonus"] + pdx["Employer Payroll Tax"] + pdx["Benefits"]).sum(), pdx["Total Cash Out"].sum(), tol=1.0)

    # --- internal identities
    add("Identity: Daily_Sales Gross - Discounts = Net", (ds["Gross Sales"] - ds["Discounts"]).sum(), ds["Net Sales"].sum())
    add("Identity: Daily_Sales channel columns sum to Net Sales",
        ds[["Dine-In Net Sales", "Takeout Net Sales", "Delivery App Net Sales", "Catering Net Sales"]].sum().sum(), ds["Net Sales"].sum())
    add("Identity: Sales_Item_Detail Net - COGS = Gross Profit", (sid["Net Sales"] - sid["COGS"]).sum(), sid["Gross Profit"].sum())
    add("Identity: Daily_Sales Net + Tax - Refunds = Total Collected", (ds["Net Sales"] + ds["Sales Tax"] - ds["Refunds"]).sum(), ds["Total Collected"].sum())
    return pd.DataFrame(checks)


def run_sales_checks(sheets: dict, entities: dict, start=None, end=None) -> pd.DataFrame:
    """Weekly top-up reconciliation: SALES DATA integrity only.

    A weekly POS upload carries no payout/accounting/payroll files, so settlement-timing
    checks (card payouts, DoorDash, bill payments) are out of scope here — they're deferred
    to the monthly accounting upload and surfaced as 'pending', never a hard failure. What
    we DO verify is that the sales numbers tie out (item+modifier rows, order headers, order
    and line counts, per-day net, and the internal sales identities). A failure here is a
    real data mismatch and should quarantine the batch."""
    checks = []

    def add(name, expected, actual, tol=0.05, hard=True, detail=""):
        ok = _close(expected, actual, tol)
        checks.append({"check": name, "expected (raw files)": round(float(expected), 2), "actual (golden)": round(float(actual), 2),
                       "difference": round(float(actual) - float(expected), 2), "pass": ok, "severity": "hard" if hard else "soft", "detail": detail})

    ln = entities["sales_line"]
    md = entities.get("sales_line_modifier", pd.DataFrame(columns=["net", "gross", "void", "business_date"])).copy()
    oh = entities.get("order_header", pd.DataFrame())
    if len(md):
        md["void"] = md["void"].astype(bool)
        if "included_in_parent" in md.columns:
            inc = md["included_in_parent"].fillna(False).astype(bool)
            md.loc[inc, ["net", "gross"]] = 0.0
    ds = sheets["Daily_Sales"]; sid = sheets["Sales_Item_Detail"]

    raw_net = ln[~ln.void].net.sum() + md[~md.void].net.sum()
    raw_gross = ln[~ln.void].gross.sum() + md[~md.void].gross.sum()
    raw_tax = ln[~ln.void].tax.sum()
    add("Net sales: POS item+modifier rows (non-void) = Daily_Sales.Net Sales", raw_net, ds["Net Sales"].sum())
    add("Net sales: Daily_Sales = Sales_Item_Detail", ds["Net Sales"].sum(), sid["Net Sales"].sum())
    add("Gross sales: POS = Daily_Sales.Gross Sales", raw_gross, ds["Gross Sales"].sum())
    add("Sales tax: POS item tax = Daily_Sales.Sales Tax", raw_tax, ds["Sales Tax"].sum())
    add("Lines: POS non-void item rows = Sales_Item_Detail rows", (~ln.void).sum(), len(sid), tol=0)
    if len(oh):
        add("Net sales: POS order headers net (non-voided) = Daily_Sales.Net Sales", oh[~oh.voided].net_sales.sum(), ds["Net Sales"].sum(), tol=1.0,
            detail="order header vs line level; rounding across modifiers")
        add("Orders: POS order headers (non-voided) = Daily_Sales.Orders", oh[~oh.voided].order_id.nunique(), ds["Orders"].sum(), tol=0)
    day_raw = (ln[~ln.void].groupby("business_date").net.sum().add(md[~md.void].groupby("business_date").net.sum(), fill_value=0))
    day_gold = ds.set_index(ds["Date"].dt.date)["Net Sales"]
    bad_days = int(((day_raw - day_gold.reindex(day_raw.index).fillna(0)).abs() > 0.05).sum())
    add("Per-day net sales mismatches (count of days)", 0, bad_days, tol=0)

    # internal sales identities (no accounting/cash involved)
    add("Identity: Daily_Sales Gross - Discounts = Net", (ds["Gross Sales"] - ds["Discounts"]).sum(), ds["Net Sales"].sum())
    add("Identity: Sales_Item_Detail Net - COGS = Gross Profit", (sid["Net Sales"] - sid["COGS"]).sum(), sid["Gross Profit"].sum())
    return pd.DataFrame(checks)


FORMULA_COLUMNS = {("Products", "Gross Margin %")}      # written as Excel formulas by the writer


def column_completeness(sheets: dict) -> pd.DataFrame:
    rows = []
    for name, df in sheets.items():
        for c in df.columns:
            if (name, c) in FORMULA_COLUMNS:
                rows.append({"sheet": name, "column": c, "rows": len(df), "blank": 0, "blank_pct": 0.0, "note": "Excel formula"})
                continue
            s = df[c]
            blank = int(s.isna().sum() + (s.astype(str).str.strip() == "").sum()) if len(s) else 0
            rows.append({"sheet": name, "column": c, "rows": len(df), "blank": blank, "blank_pct": round(100 * blank / len(df), 1) if len(df) else 0.0})
    return pd.DataFrame(rows)
