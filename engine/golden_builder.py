"""
Assemble the Golden Standard data sheets (Tony Pizza layout) from the canonical
entities produced by ingest.py, driven by a business-type profile.

Output: dict sheet_name -> DataFrame with the exact Golden column headers.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))

GOLDEN_COLUMNS = {
    "Products": ["SKU", "Product", "Category", "List Price", "Unit COGS", "Base Daily Units", "Gross Margin %"],
    "Employees": ["Employee ID", "Employee Name", "Role", "Pay Type", "Hourly Rate", "Annual Salary", "Avg Biweekly Hours", "Benefits / Period"],
    "Daily_Sales": ["Date", "Day Name", "Week Start", "Month Start", "Month Label", "ISO Week", "Weather", "Event", "Promotion", "Orders",
                    "Gross Sales", "Discounts", "Net Sales", "Sales Tax", "Refunds", "Total Collected", "COGS", "Gross Profit", "Avg Ticket",
                    "Dine-In Net Sales", "Takeout Net Sales", "Delivery App Net Sales", "Catering Net Sales"],
    "Sales_Item_Detail": ["Date", "ISO Week", "SKU", "Product", "Category", "Channel", "Weather", "Event / Promo", "Qty Sold", "Unit Price",
                          "Gross Sales", "Discounts", "Net Sales", "Sales Tax", "COGS", "Gross Profit", "Rating", "Customer ID"],
    "Cash_In_Detail": ["Transaction Date", "Settlement Date", "Channel", "Payment Method", "Gross Sales Basis", "Discounts", "Sales Tax",
                       "Deductions", "Net Cash Received", "Reference"],
    "Raw_Material_Purchases": ["Invoice ID", "Invoice Date", "Paid Date", "Month Label", "Vendor", "Expense Category", "Item Description", "Qty",
                               "Unit", "Unit Cost", "Line Total", "Payment Terms (Days)", "Status", "Notes"],
    "Payroll_Detail": ["Payroll Batch", "Period Start", "Period End", "Pay Date", "Month Label", "Employee ID", "Employee Name", "Role", "Pay Type",
                       "Hourly Rate", "Annual Salary", "Gross Pay", "Overtime Premium", "Bonus", "Employer Payroll Tax", "Benefits", "Total Cash Out"],
    "Bills_Fixed_Monthly": ["Reference", "Invoice Date", "Paid Date", "Month Label", "Department", "Expense Category", "Vendor", "Amount", "Status", "Notes"],
    "Cash_Out_Detail": ["Paid Date", "Month Label", "Department", "Expense Category", "Vendor / Payee", "Description", "Reference No", "Expense Type",
                        "Amount", "Payment Method", "Status", "Accounting Class", "Flow Type", "Notes"],
}


def load_profile(name: str) -> dict:
    with open(os.path.join(HERE, "profiles", f"{name}.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def mlabel(d) -> str:
    return pd.Timestamp(d).strftime("%b %Y")


def r2(s):
    return np.round(s.astype(float) + 1e-9, 2)


# --------------------------------------------------------------------------- #
class GoldenBuilder:
    def __init__(self, entities: dict, profile: dict, period_start: date, period_end: date, business_name: str):
        self.e = entities
        self.p = profile
        self.start, self.end = period_start, period_end
        self.business_name = business_name
        self.notes = []          # assumptions / derivations recorded for the report

    # ---- helpers ---------------------------------------------------------- #
    def ent(self, name: str) -> pd.DataFrame:
        df = self.e.get(name)
        if df is None:
            if name not in ("refund", "check_detail", "settlement_card", "settlement_platform"):
                self.notes.append(f"entity '{name}' not present in uploads")
            return pd.DataFrame()
        if name == "payment":
            df = self._payments_with_refunds(df)
        return df

    def _payments_with_refunds(self, pay: pd.DataFrame) -> pd.DataFrame:
        pay = pay.copy()
        if "status" not in pay.columns:
            pay["status"] = "CAPTURED"
        pay["status"] = pay["status"].fillna("CAPTURED")
        for c in ("refund_amount", "tip", "gratuity", "fees"):
            if c not in pay.columns:
                pay[c] = 0.0
            pay[c] = pay[c].fillna(0.0)
        if "refund_at" not in pay.columns:
            pay["refund_at"] = pd.NaT
        rf = self.e.get("refund")
        if rf is not None and len(rf):
            agg = rf.groupby("order_id").agg(ra=("amount", "sum"), rd=("refund_at", "max"))
            pay = pay.merge(agg, left_on="order_id", right_index=True, how="left")
            pay["refund_amount"] = np.where(pay.ra.notna(), pay.ra.abs(), pay.refund_amount)
            pay["refund_at"] = pay.refund_at.where(pay.rd.isna(), pay.rd)
            pay = pay.drop(columns=["ra", "rd"])
        return pay

    def channel(self, dining: pd.Series) -> pd.Series:
        return dining.map(self.p["channels"]["map"]).fillna(dining)

    def with_dining(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rows that carry no dining option of their own (Clover lines, SpotOn payments) inherit it from the order header."""
        if "dining_option" in df.columns and df.dining_option.notna().any():
            return df
        oh = self.e.get("order_header")
        if oh is None or "dining_option" not in oh.columns:
            df = df.copy(); df["dining_option"] = None
            return df
        lk = oh.drop_duplicates("order_id").set_index("order_id").dining_option
        df = df.copy()
        df["dining_option"] = df.order_id.map(lk)
        self.notes.append("dining option inherited from the order header for rows without one")
        return df

    # ---- enrichment lookups ---------------------------------------------- #
    def weather_by_date(self) -> dict:
        w = self.ent("weather")
        if w.empty:
            return {}
        t = self.p["weather"]

        def lab(r):
            if (r.snow or 0) >= t["snow_in"]:
                return "Snow"
            if (r.prcp or 0) >= t["rain_in"]:
                return "Rainy"
            if (r.tmax or 0) >= t["hot_f"]:
                return "Hot"
            if (r.tmax or 0) <= t["cold_f"]:
                return "Cold"
            return "Normal"
        return {r.weather_date: lab(r) for r in w.itertuples()}

    def events_by_date(self) -> dict:
        ev = self.ent("event")
        return {} if ev.empty else dict(zip(ev.event_date, ev.event))

    # ---- core frames ------------------------------------------------------ #
    def sales_lines_with_mods(self) -> pd.DataFrame:
        """Item lines with modifier revenue and modifier cost rolled into the parent line."""
        ln = self.with_dining(self.ent("sales_line"))
        md = self.ent("sales_line_modifier").copy()
        costs = self.ent("item_cost")
        mcosts = self.ent("modifier_cost")
        ln = ln[~ln.void]
        md = md[~md.void]
        unit_cost = dict(zip(costs.sku, costs.unit_cost)) if not costs.empty else {}
        mod_unit_cost = dict(zip(zip(mcosts.modifier_group, mcosts.modifier), mcosts.unit_cost)) if not mcosts.empty else {}
        mod_cost_by_name = dict(zip(mcosts.modifier, mcosts.unit_cost)) if not mcosts.empty else {}
        if not md.empty:
            if "included_in_parent" not in md.columns:
                md["included_in_parent"] = False
            md["included_in_parent"] = md["included_in_parent"].fillna(False).astype(bool)
            md["mod_cost"] = [ (mod_unit_cost.get((g, m)) if (g, m) in mod_unit_cost else mod_cost_by_name.get(m, 0.0)) * float(q)
                               for g, m, q in zip(md.modifier_group, md.modifier, md.quantity)]
            # when the POS already folded the modifier price into the parent line (Square, Clover ...) only the cost is added
            for c in ("gross", "discount", "net"):
                md[c] = np.where(md.included_in_parent, 0.0, md[c].astype(float))
            agg = md.groupby("parent_line_id").agg(mod_gross=("gross", "sum"), mod_discount=("discount", "sum"),
                                                   mod_net=("net", "sum"), mod_cost=("mod_cost", "sum"))
            ln = ln.merge(agg, left_on="line_id", right_index=True, how="left")
        for c in ["mod_gross", "mod_discount", "mod_net", "mod_cost"]:
            if c not in ln.columns:
                ln[c] = 0.0
            ln[c] = ln[c].fillna(0.0)
        ln["gross_all"] = r2(ln.gross + ln.mod_gross)
        ln["discount_all"] = r2(ln.discount + ln.mod_discount)
        ln["net_all"] = r2(ln.net + ln.mod_net)
        ln["cogs"] = r2(ln.sku.map(unit_cost).fillna(0.0) * ln.quantity + ln.mod_cost)
        if not costs.empty and ln.sku.map(unit_cost).isna().any():
            self.notes.append("some SKUs had no Unit COGS in owner_inputs/menu_costs.csv; COGS set to 0 for those lines")
        ln["channel"] = self.channel(ln.dining_option)
        return ln

    def operating_days(self) -> list:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    # ---- Products ---------------------------------------------------------- #
    def products(self, ln: pd.DataFrame) -> pd.DataFrame:
        costs = self.ent("item_cost")
        last_price = (ln.sort_values("sent_at").groupby("sku")
                      .apply(lambda g: (g.gross / g.quantity).iloc[-1], include_groups=False))
        days = ln.business_date.nunique()
        units = ln.groupby("sku").quantity.sum() / max(days, 1)
        names = ln.groupby("sku").item_name.last()
        cats = ln.groupby("sku").category.last()
        skus = sorted(set(ln.sku) | (set(costs.sku) if not costs.empty else set()))
        cost_map = dict(zip(costs.sku, costs.unit_cost)) if not costs.empty else {}
        name_map = dict(zip(costs.sku, costs.item_name)) if not costs.empty else {}
        cat_map = dict(zip(costs.sku, costs.category)) if not costs.empty else {}
        df = pd.DataFrame({
            "SKU": skus,
            "Product": [names.get(s, name_map.get(s)) for s in skus],
            "Category": [cats.get(s, cat_map.get(s)) for s in skus],
            "List Price": [round(float(last_price.get(s, np.nan)), 2) for s in skus],
            "Unit COGS": [cost_map.get(s, np.nan) for s in skus],
            "Base Daily Units": [int(round(units.get(s, 0))) for s in skus],
        })
        df["Gross Margin %"] = None   # formula written by the writer
        self.notes.append("Products.List Price = last observed unit price in POS sales (captures the April 2026 price change); "
                          "Base Daily Units = average units per operating day; Unit COGS from owner_inputs/menu_costs.csv")
        return df

    # ---- Employees --------------------------------------------------------- #
    def employees(self) -> pd.DataFrame:
        d = self.ent("employee")
        pr = self.ent("payroll_line")
        hrs = pr.groupby("employee_id").apply(lambda g: (g.regular_hours + g.overtime_hours).mean(), include_groups=False) if not pr.empty else {}
        df = pd.DataFrame({
            "Employee ID": d.employee_id,
            "Employee Name": d.first_name.astype(str) + " " + d.last_name.astype(str),
            "Role": d.role, "Pay Type": d.pay_type,
            "Hourly Rate": d.hourly_rate, "Annual Salary": d.annual_salary,
            "Avg Biweekly Hours": d.employee_id.map(lambda e: round(float(hrs.get(e, np.nan)), 1) if len(hrs) else np.nan),
            "Benefits / Period": d.benefits_per_period,
        })
        # Tony Pizza leaves Avg Biweekly Hours blank for salaried staff
        df.loc[df["Pay Type"].isin(self.p["payroll"]["salary_types"]), "Avg Biweekly Hours"] = np.nan
        return df

    # ---- Daily_Sales ------------------------------------------------------- #
    def daily_sales(self, ln: pd.DataFrame) -> pd.DataFrame:
        oh = self.ent("order_header")
        oh = oh[~oh.voided]
        pay = self.ent("payment")
        chk = self.ent("check_detail")
        weather, events = self.weather_by_date(), self.events_by_date()
        g = ln.groupby("business_date").agg(gross=("gross_all", "sum"), disc=("discount_all", "sum"), net=("net_all", "sum"),
                                            tax=("tax", "sum"), cogs=("cogs", "sum"))
        orders = oh.groupby("business_date").order_id.nunique()
        refunds = pay.groupby("business_date").refund_amount.sum()
        chan = ln.pivot_table(index="business_date", columns="channel", values="net_all", aggfunc="sum").fillna(0.0)
        # promotion of the day = most frequent discount reason on that day's checks
        promo = {}
        _, reason = self.check_attributes()
        if reason:
            c = pd.DataFrame({"check_id": list(reason.keys()), "discount_reason": list(reason.values())})
            c = c[c.discount_reason.notna() & (c.discount_reason.astype(str).str.strip() != "")]
            c = c[~c.discount_reason.isin(self.p.get("promotions", {}).get("exclude_reasons", []))]
            c = c.merge(ln[["check_id", "business_date"]].drop_duplicates("check_id"), on="check_id", how="inner")
            promo = c.groupby("business_date").discount_reason.agg(lambda s: s.value_counts().index[0]).to_dict()
        rows = []
        for d in self.operating_days():
            ts = pd.Timestamp(d)
            net = float(g.net.get(d, 0.0)); n_ord = int(orders.get(d, 0))
            ref = float(refunds.get(d, 0.0)); tax = float(g.tax.get(d, 0.0))
            rows.append({
                "Date": ts, "Day Name": ts.day_name(), "Week Start": ts - pd.Timedelta(days=ts.weekday()),
                "Month Start": ts.replace(day=1), "Month Label": mlabel(ts), "ISO Week": f"W{ts.isocalendar().week:02d}",
                "Weather": weather.get(d, "Normal"), "Event": events.get(d), "Promotion": promo.get(d),
                "Orders": n_ord, "Gross Sales": round(float(g.gross.get(d, 0.0)), 2), "Discounts": round(float(g.disc.get(d, 0.0)), 2),
                "Net Sales": round(net, 2), "Sales Tax": round(tax, 2), "Refunds": round(ref, 2),
                "Total Collected": round(net + tax - ref, 2), "COGS": round(float(g.cogs.get(d, 0.0)), 2),
                "Gross Profit": round(net - float(g.cogs.get(d, 0.0)), 2), "Avg Ticket": round(net / n_ord, 2) if n_ord else 0.0,
                **{f"{c} Net Sales": round(float(chan[c].get(d, 0.0)), 2) if c in chan.columns else 0.0 for c in self.p["channels"]["golden_order"]},
            })
        self.notes.append("Daily_Sales: one row per calendar day incl. closed days (zeros); Promotion = most frequent discount reason on that day's checks; "
                          "Weather from NOAA daily file; Event from owner events calendar; Refunds by original sale date")
        return pd.DataFrame(rows)

    def check_attributes(self):
        """customer id and discount reason per check: from CheckDetails when the POS has one, else from the order header."""
        chk = self.ent("check_detail")
        if not chk.empty:
            return dict(zip(chk.check_id, chk.customer_id)), dict(zip(chk.check_id, chk.discount_reason))
        oh = self.ent("order_header")
        key = oh.check_id if "check_id" in oh.columns and oh.check_id.notna().any() else oh.order_id
        cust = dict(zip(key, oh.customer_id)) if "customer_id" in oh.columns else {}
        reason = dict(zip(key, oh.discount_reason)) if "discount_reason" in oh.columns else {}
        return cust, reason

    # ---- Sales_Item_Detail ------------------------------------------------- #
    def sales_item_detail(self, ln: pd.DataFrame) -> pd.DataFrame:
        fb = self.ent("feedback")
        chk = self.ent("check_detail")
        weather, events = self.weather_by_date(), self.events_by_date()
        rating = dict(zip(fb.order_id, fb.rating)) if not fb.empty else {}
        cust, reason = self.check_attributes()
        df = pd.DataFrame({
            "Date": pd.to_datetime(ln.sent_at),
            "ISO Week": pd.to_datetime(ln.business_date).map(lambda t: f"W{t.isocalendar().week:02d}"),
            "SKU": ln.sku, "Product": ln.item_name, "Category": ln.category, "Channel": ln.channel,
            "Weather": ln.business_date.map(lambda d: weather.get(d, "Normal")),
            "Event / Promo": [ (events.get(d) or (reason.get(c) if str(reason.get(c, "")).strip() else None)) for d, c in zip(ln.business_date, ln.check_id)],
            "Qty Sold": ln.quantity.astype(int), "Unit Price": r2(ln.gross_all / ln.quantity),
            "Gross Sales": ln.gross_all, "Discounts": ln.discount_all, "Net Sales": ln.net_all, "Sales Tax": r2(ln.tax),
            "COGS": ln.cogs, "Gross Profit": r2(ln.net_all - ln.cogs),
            "Rating": ln.order_id.map(rating), "Customer ID": ln.check_id.map(cust),
        })
        self.notes.append("Sales_Item_Detail: voided lines excluded; modifier revenue and modifier cost rolled into the parent item line, "
                          "so Unit Price is the effective price including modifiers; Rating from Toast guest feedback; Customer ID from CheckDetails")
        return df.sort_values("Date").reset_index(drop=True)

    # ---- Cash_In_Detail ---------------------------------------------------- #
    def cash_in(self, ln: pd.DataFrame) -> pd.DataFrame:
        pay = self.with_dining(self.ent("payment"))
        pay = pay[pay.status.astype(str).str.upper() != "VOIDED"]
        tmap = self.p["tenders"]["map"]
        excl = set(self.p["tenders"]["exclude_from_cash_in"])
        pay = pay[~pay.tender_type.isin(excl)]
        pay["channel"] = self.channel(pay.dining_option)
        pay["method"] = pay.tender_type.map(tmap).fillna(pay.tender_type)
        chan_day = ln.groupby(["business_date", "channel"]).agg(gross=("gross_all", "sum"), disc=("discount_all", "sum"), tax=("tax", "sum"))
        g = pay.groupby(["business_date", "channel", "method"]).agg(received=("amount", "sum"), n=("payment_id", "count")).reset_index()
        # settlement dates
        cp = self.ent("settlement_card")
        card_settle = dict(zip(cp.business_date, cp.payout_date)) if not cp.empty else {}
        dp = self.ent("settlement_platform")
        plat_settle = {}
        if not dp.empty:
            for r in dp.itertuples():
                d = r.period_start
                while d <= r.period_end:
                    plat_settle[d] = r.payout_date
                    d += timedelta(days=1)
        lag = self.p["tenders"]["settlement_lag_days"]
        code = {"Dine-In": "DIN", "Takeout": "TOGO", "Delivery App": "DLV", "Catering": "CAT"}
        mcode = {"Cash Deposit": "CASH", "Card Settlement": "CARD", "Platform Transfer": "PLAT"}
        rows = []
        for r in g.itertuples():
            if r.method == "Card Settlement":
                sd = card_settle.get(r.business_date, r.business_date + timedelta(days=lag["Card Settlement"]))
            elif r.method == "Platform Transfer":
                sd = plat_settle.get(r.business_date, r.business_date + timedelta(days=7))
            else:
                sd = r.business_date + timedelta(days=lag.get(r.method, 0))
            basis = chan_day.loc[(r.business_date, r.channel)] if (r.business_date, r.channel) in chan_day.index else None
            rows.append({
                "Transaction Date": pd.Timestamp(r.business_date), "Settlement Date": pd.Timestamp(sd), "Channel": r.channel,
                "Payment Method": r.method,
                "Gross Sales Basis": round(float(basis.gross), 2) if basis is not None else 0.0,
                "Discounts": round(float(basis.disc), 2) if basis is not None else 0.0,
                "Sales Tax": round(float(basis.tax), 2) if basis is not None else 0.0,
                "Deductions": 0.0, "Net Cash Received": round(float(r.received), 2),
                "Reference": f"DAY-{r.business_date.strftime('%Y%m%d')}-{code.get(r.channel, 'OTH')}-{mcode.get(r.method, 'OTH')}",
            })
        self.notes.append("Cash_In_Detail: one row per business date x channel x tender; Net Cash Received = amount collected incl. tax, excluding tips "
                          "(tips are a pass-through liability); card settlement date from Toast payouts file, platform settlement date from DoorDash payout periods; "
                          "gift card redemptions excluded (not new cash); processing fees and commissions are Cash_Out rows, not deductions, as in Tony Pizza")
        return pd.DataFrame(rows).sort_values(["Transaction Date", "Channel", "Payment Method"]).reset_index(drop=True)

    # ---- Raw_Material_Purchases ------------------------------------------- #
    def purchases(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        pl = self.ent("purchase_line").copy()
        acct = self.p["qbo_accounts"]
        tx = self.ent("accounting_txn")
        bp = tx[tx.txn_type.isin(self.p["qbo_transaction_types"]["bill_payment"])]
        paid_date = {}
        paid_method = {}
        for r in bp.itertuples():
            ref = str(r.reference)
            inv = ref[3:] if ref.startswith("BP-") else ref
            paid_date[inv] = r.txn_date
            paid_method[inv] = r.payment_method
        pl["paid_date"] = pl.invoice_id.map(paid_date)
        pl["category"] = pl.account.map(lambda a: acct.get(a, {}).get("category", a))
        df = pd.DataFrame({
            "Invoice ID": pl.invoice_id, "Invoice Date": pd.to_datetime(pl.invoice_date), "Paid Date": pd.to_datetime(pl.paid_date),
            "Month Label": pd.to_datetime(pl.invoice_date).map(mlabel), "Vendor": pl.vendor, "Expense Category": pl.category,
            "Item Description": pl.description, "Qty": pl.quantity.astype(int), "Unit": pl.unit, "Unit Cost": pl.unit_cost,
            "Line Total": pl.line_total, "Payment Terms (Days)": pl.terms_days.astype(int),
            "Status": np.where(pl.paid_date.notna(), "Paid", "Open"),
            "Notes": pl.vendor.map(lambda v: f"Scheduled delivery - {v}"),
        })
        # invoice-level cash-out rows
        inv = pl[pl.paid_date.notna()].groupby(["invoice_id", "vendor", "paid_date", "category", "account"], as_index=False).agg(
            amount=("line_total", "sum"), n=("line_total", "count"))
        cash = pd.DataFrame({
            "Paid Date": pd.to_datetime(inv.paid_date), "Month Label": pd.to_datetime(inv.paid_date).map(mlabel),
            "Department": inv.account.map(lambda a: acct.get(a, {}).get("dept", "Inventory / Supplies")),
            "Expense Category": inv.category, "Vendor / Payee": inv.vendor,
            "Description": inv.apply(lambda r: f"Invoice {r.invoice_id} ({int(r.n)} lines)", axis=1),
            "Reference No": "BP-" + inv.invoice_id.astype(str), "Expense Type": "Variable", "Amount": r2(inv.amount),
            "Payment Method": inv.invoice_id.map(paid_method).fillna("Bank ACH"), "Status": "Paid",
            "Accounting Class": inv.account.map(lambda a: acct.get(a, {}).get("class", "Cost of Goods")), "Flow Type": "Cash Out",
            "Notes": "Vendor invoice payment",
        })
        self.notes.append("Raw_Material_Purchases from QBO Bills (line level); Paid Date and method from the matching QBO Bill Payment row")
        return df.sort_values(["Invoice Date", "Invoice ID"]).reset_index(drop=True), cash

    # ---- Bills_Fixed_Monthly + accounting cash-out ------------------------- #
    def fixed_bills_and_expenses(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        tx = self.ent("accounting_txn")
        acct = self.p["qbo_accounts"]
        tt = self.p["qbo_transaction_types"]
        bills = tx[tx.txn_type.isin(tt["recurring_bill"])].copy()
        pays = tx[tx.txn_type.isin(tt["bill_payment"])]
        pay_by_ref = {str(r.reference)[3:]: r for r in pays.itertuples() if str(r.reference).startswith("BP-")}
        rows, cash = [], []
        for r in bills.itertuples():
            m = acct.get(r.split, {"dept": "Operations", "category": r.split, "class": "Operating Expense"})
            p = pay_by_ref.get(str(r.reference))
            paid = p.txn_date if p is not None else None
            rows.append({"Reference": r.reference, "Invoice Date": pd.Timestamp(r.txn_date), "Paid Date": pd.Timestamp(paid) if paid else pd.NaT,
                         "Month Label": mlabel(r.txn_date), "Department": m["dept"], "Expense Category": m["category"], "Vendor": r.payee,
                         "Amount": round(float(r.amount), 2), "Status": "Paid" if paid else "Scheduled", "Notes": r.memo})
            if p is not None:
                cash.append({"Paid Date": pd.Timestamp(paid), "Month Label": mlabel(paid), "Department": m["dept"], "Expense Category": m["category"],
                             "Vendor / Payee": r.payee, "Description": r.memo, "Reference No": r.reference, "Expense Type": "Recurring",
                             "Amount": round(abs(float(p.amount)), 2), "Payment Method": p.payment_method or "Bank ACH", "Status": "Paid",
                             "Accounting Class": m["class"], "Flow Type": "Cash Out", "Notes": "Recurring bill payment"})
        for r in tx[tx.txn_type.isin(tt["expense"])].itertuples():
            m = acct.get(r.split, {"dept": "Operations", "category": r.split, "class": "Operating Expense"})
            cash.append({"Paid Date": pd.Timestamp(r.txn_date), "Month Label": mlabel(r.txn_date), "Department": m["dept"], "Expense Category": m["category"],
                         "Vendor / Payee": r.payee, "Description": r.memo, "Reference No": r.reference, "Expense Type": "Variable",
                         "Amount": round(abs(float(r.amount)), 2), "Payment Method": r.payment_method or "Business Card", "Status": "Paid",
                         "Accounting Class": m["class"], "Flow Type": "Cash Out", "Notes": "QBO expense"})
        for r in tx[tx.txn_type.isin(tt["sales_tax"])].itertuples():
            m = acct.get(r.split, {"dept": "Taxes", "category": "Sales Tax Remittance", "class": "Liability Payment"})
            cash.append({"Paid Date": pd.Timestamp(r.txn_date), "Month Label": mlabel(r.txn_date), "Department": m["dept"], "Expense Category": m["category"],
                         "Vendor / Payee": r.payee, "Description": r.memo, "Reference No": r.reference, "Expense Type": "Recurring",
                         "Amount": round(abs(float(r.amount)), 2), "Payment Method": r.payment_method or "Bank ACH", "Status": "Paid",
                         "Accounting Class": m["class"], "Flow Type": "Cash Out", "Notes": "Sales tax collected via POS, remitted monthly"})
        return pd.DataFrame(rows), pd.DataFrame(cash)

    # ---- Payroll ----------------------------------------------------------- #
    def payroll(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        pr = self.ent("payroll_line").copy()
        c = self.p["cash_out_sources"]["payroll"]
        gross_ex_bonus = r2(pr.gross_earnings - pr.bonus)
        ot_prem = r2(pr.overtime_earnings / 3.0)      # 1.5x pay -> the 0.5x premium portion
        df = pd.DataFrame({
            "Payroll Batch": pr.batch, "Period Start": pd.to_datetime(pr.period_start), "Period End": pd.to_datetime(pr.period_end),
            "Pay Date": pd.to_datetime(pr.pay_date), "Month Label": pd.to_datetime(pr.pay_date).map(mlabel), "Employee ID": pr.employee_id,
            "Employee Name": pr.employee, "Role": pr.role, "Pay Type": pr.pay_type, "Hourly Rate": pr.hourly_rate, "Annual Salary": pr.annual_salary,
            "Gross Pay": gross_ex_bonus, "Overtime Premium": ot_prem, "Bonus": pr.bonus, "Employer Payroll Tax": pr.employer_tax,
            "Benefits": pr.employer_contributions, "Total Cash Out": pr.total_employer_cost,
        })
        cash = pd.DataFrame({
            "Paid Date": df["Pay Date"], "Month Label": df["Month Label"], "Department": c["dept"],
            "Expense Category": np.where(df["Pay Type"].isin(self.p["payroll"]["salary_types"]), c["salary_category"], c["hourly_category"]),
            "Vendor / Payee": df["Employee Name"], "Description": df["Role"] + " - " + df["Payroll Batch"],
            "Reference No": df["Payroll Batch"] + "-" + df["Employee ID"],
            "Expense Type": np.where(df["Pay Type"].isin(self.p["payroll"]["salary_types"]), "Recurring", "Variable"),
            "Amount": df["Total Cash Out"], "Payment Method": c["method"], "Status": "Paid", "Accounting Class": c["class"],
            "Flow Type": "Cash Out", "Notes": "Gross + employer tax + benefits; tips paid through payroll excluded (pass-through)",
        })
        self.notes.append("Payroll_Detail from Gusto journal: Gross Pay excludes bonus and tips; Overtime Premium = overtime earnings / 3; "
                          "Total Cash Out = gross + bonus + employer tax + employer contributions")
        return df, cash

    # ---- fee / refund cash-out rows --------------------------------------- #
    def fee_and_refund_cash_out(self) -> pd.DataFrame:
        cs = self.p["cash_out_sources"]
        rows = []
        cp = self.ent("settlement_card")
        for r in cp.itertuples():
            if r.fees and r.fees > 0:
                c = cs["card_fees"]
                rows.append({"Paid Date": pd.Timestamp(r.payout_date), "Month Label": mlabel(r.payout_date), "Department": c["dept"], "Expense Category": c["category"],
                             "Vendor / Payee": c["payee"], "Description": f"Card processing fees for {r.business_date}", "Reference No": r.payout_id,
                             "Expense Type": "Variable", "Amount": round(float(r.fees), 2), "Payment Method": c["method"], "Status": "Paid",
                             "Accounting Class": c["class"], "Flow Type": "Cash Out", "Notes": "Netted from card payout"})
        dp = self.ent("settlement_platform")
        for r in dp.itertuples():
            c = cs["platform_fees"]
            fee = abs(float(r.commission)) + abs(float(r.marketing))
            rows.append({"Paid Date": pd.Timestamp(r.payout_date), "Month Label": mlabel(r.payout_date), "Department": c["dept"], "Expense Category": c["category"],
                         "Vendor / Payee": c["payee"], "Description": f"Commission + marketing, orders {r.period_start} to {r.period_end}", "Reference No": r.payout_id,
                         "Expense Type": "Variable", "Amount": round(fee, 2), "Payment Method": c["method"], "Status": "Paid",
                         "Accounting Class": c["class"], "Flow Type": "Cash Out", "Notes": "Netted from weekly payout"})
            if r.amendments and abs(float(r.amendments)) > 0:
                c = cs["platform_adj"]
                rows.append({"Paid Date": pd.Timestamp(r.payout_date), "Month Label": mlabel(r.payout_date), "Department": c["dept"], "Expense Category": c["category"],
                             "Vendor / Payee": c["payee"], "Description": "Error charges / adjustments", "Reference No": r.payout_id,
                             "Expense Type": "Variable", "Amount": round(abs(float(r.amendments)), 2), "Payment Method": c["method"], "Status": "Paid",
                             "Accounting Class": c["class"], "Flow Type": "Cash Out", "Notes": "Netted from weekly payout"})
        pay = self.ent("payment")
        rf = pay[pay.refund_amount > 0]
        c = cs["refunds"]
        for r in rf.itertuples():
            d = pd.Timestamp(r.refund_at).normalize() if pd.notna(r.refund_at) else pd.Timestamp(r.business_date)
            rows.append({"Paid Date": d, "Month Label": mlabel(d), "Department": c["dept"], "Expense Category": c["category"],
                         "Vendor / Payee": self.business_name, "Description": f"Partial refund on check {r.check_id[:8]}", "Reference No": f"RF-{r.payment_id[:8]}",
                         "Expense Type": "Variable", "Amount": round(float(r.refund_amount), 2), "Payment Method": "Card" if r.tender_type == "Credit" else "Cash",
                         "Status": "Paid", "Accounting Class": c["class"], "Flow Type": "Cash Out", "Notes": "Refund"})
        return pd.DataFrame(rows)

    # ---- assemble everything ---------------------------------------------- #
    def build(self) -> dict:
        ln = self.sales_lines_with_mods()
        sheets = {}
        sheets["Products"] = self.products(ln)
        sheets["Employees"] = self.employees()
        sheets["Daily_Sales"] = self.daily_sales(ln)
        sheets["Sales_Item_Detail"] = self.sales_item_detail(ln)
        sheets["Cash_In_Detail"] = self.cash_in(ln)
        purchases, cash_purch = self.purchases()
        sheets["Raw_Material_Purchases"] = purchases
        payroll, cash_pay = self.payroll()
        sheets["Payroll_Detail"] = payroll
        fixed, cash_acct = self.fixed_bills_and_expenses()
        sheets["Bills_Fixed_Monthly"] = fixed
        cash_fees = self.fee_and_refund_cash_out()
        cash = pd.concat([cash_purch, cash_acct, cash_pay, cash_fees], ignore_index=True)
        cash = cash[(cash["Paid Date"] >= pd.Timestamp(self.start)) & (cash["Paid Date"] <= pd.Timestamp(self.end))]
        sheets["Cash_Out_Detail"] = cash.sort_values(["Paid Date", "Department"]).reset_index(drop=True)
        for name, cols in GOLDEN_COLUMNS.items():
            sheets[name] = sheets[name].reindex(columns=cols)
        return sheets

    def build_sales_only(self) -> dict:
        """Sales-only subset for weekly top-ups.

        Builds just the sales sheets (Products, Daily_Sales, Sales_Item_Detail) — no
        payroll / purchases / fixed-bills / cash-out. Two reasons: (1) a POS-only week has
        no accounting/payroll entities, so the full build() would crash; (2) feeding only
        sales sheets to PRAJ_IQ's weekly ingest means a weekly upload can never overwrite
        the store's accounting rows. The onboarding path (build()) is untouched."""
        ln = self.sales_lines_with_mods()
        sheets = {
            "Products": self.products(ln),
            "Daily_Sales": self.daily_sales(ln),
            "Sales_Item_Detail": self.sales_item_detail(ln),
        }
        for name, df in list(sheets.items()):
            if name in GOLDEN_COLUMNS:
                sheets[name] = df.reindex(columns=GOLDEN_COLUMNS[name])
        return sheets
