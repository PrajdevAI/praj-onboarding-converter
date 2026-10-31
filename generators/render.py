"""
Render the simulated truth into the files a real customer would upload.

Layouts follow each vendor's published documentation:
  * Toast nightly data export: doc.toasttab.com/doc/platformguide/adminDataExportFieldReference.html
    (column headers verbatim, including Toast's own "Discnt" and "Sales Cateogry")
  * Gusto payroll journal / custom report fields: support.gusto.com (field names; header order reconstructed)
  * QuickBooks Online: Transaction Detail / Transaction List style Excel exports (reconstructed layout)
  * DoorDash merchant Payouts CSV: merchants.doordash.com payout terminology
  * NOAA GHCN-Daily CSV for weather enrichment
Values are synthetic.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pandas as pd

from .truth import Truth, month_label


def tdt(x) -> str:
    """Toast CSV date-time text, e.g. 10/1/25 11:42 AM"""
    if x is None or pd.isna(x):
        return ""
    x = pd.Timestamp(x)
    return f"{x.month}/{x.day}/{x.strftime('%y')} {x.strftime('%I:%M %p').lstrip('0')}"


def td(x) -> str:
    x = pd.Timestamp(x)
    return f"{x.month}/{x.day}/{x.year}"


def bl(b) -> str:
    return "true" if bool(b) else "false"


def money(x) -> str:
    return f"{float(x):.2f}"


def ensure(p):
    os.makedirs(p, exist_ok=True)
    return p


# --------------------------------------------------------------------------- #
# Toast nightly bundle, one folder per business day
# --------------------------------------------------------------------------- #
def render_toast(T: Truth, root: str):
    loc = T.spec["business"]["location_name"]
    root = ensure(os.path.join(root, "toast"))
    orders, lines, mods, pays, te = T.orders, T.lines, T.mods, T.payments, T.time_entries

    def service(ts):
        h = pd.Timestamp(ts).hour
        return "Lunch" if 10 <= h < 16 else "Dinner"

    for bd, o_day in orders.groupby("business_date"):
        folder = ensure(os.path.join(root, bd.strftime("%Y%m%d")))
        # --- OrderDetails.csv
        od = pd.DataFrame({
            "Location": loc, "Order Id": o_day.order_id, "Order #": o_day.order_num, "Checks": o_day.order_num,
            "Opened": o_day.opened.map(tdt), "# of Guests": o_day.guests, "Tab Names": o_day.tab,
            "Server": o_day.server, "Table": o_day.table, "Revenue Center": o_day.revenue_center,
            "Dining Area": o_day.area, "Service": o_day.opened.map(service), "Dining Options": o_day.dining,
            "Discount Amount": o_day.discount.map(money), "Amount": o_day.amount.map(money), "Tax": o_day.tax.map(money),
            "Tip": o_day.tip.map(money), "Gratuity": o_day.gratuity.map(money), "Total": o_day.total.map(money),
            "Voided": o_day.voided.map(bl), "Paid": o_day.paid.map(tdt), "Closed": o_day.closed.map(tdt),
            "Duration (Opened to Paid)": o_day.duration.map(lambda m: f"{m // 60}:{m % 60:02d}"),
            "Order Source": o_day.source,
        })
        od.to_csv(os.path.join(folder, "OrderDetails.csv"), index=False)

        # --- CheckDetails.csv
        l_day = lines[lines.business_date == bd]
        desc = l_day.groupby("order_id")["name"].agg(lambda s: ", ".join(s.tolist()))
        pay_day = pays[pays.business_date == bd].set_index("order_id")
        cd = pd.DataFrame({
            "Location Code": T.spec["business"]["toast_location_code"],
            "Opened Date": o_day.opened.map(td), "Opened Time": o_day.opened.map(lambda x: pd.Timestamp(x).strftime("%I:%M %p").lstrip("0")),
            "Item Description": o_day.order_id.map(desc).fillna(""), "Server": o_day.server, "Tax": o_day.tax.map(money),
            "Tender": o_day.order_id.map(lambda i: pay_day.loc[i, "type"] if i in pay_day.index else ""),
            "Check Id": o_day.check_id, "Check #": o_day.order_num, "Total": o_day.total.map(money),
            "Customer Family": "", "Table Size": o_day.guests, "Discount": o_day.discount.map(money),
            "Reason of Discount": o_day.promo.fillna(""), "Link": "", "Customer Id": o_day.customer_id,
            "Customer": o_day.tab, "Customer Phone": "", "Customer Email": "",
        })
        cd.to_csv(os.path.join(folder, "CheckDetails.csv"), index=False)

        # --- ItemSelectionDetails.csv
        isd = pd.DataFrame({
            "Location": loc, "Order Id": l_day.order_id, "Order #": l_day.order_num, "Sent Date": l_day.sent.map(tdt),
            "Order Date": l_day.opened.map(tdt), "Check Id": l_day.check_id, "Server": l_day.server, "Table": l_day.table,
            "Dining Area": l_day.area, "Service": l_day.opened.map(service), "Dining Option": l_day.dining,
            "Item Selection Id": l_day.selection_id, "Item Id": l_day.item_id, "Master Id": l_day.master_id,
            "SKU": l_day.sku, "Menu Item": l_day.name, "Menu Subgroup(s)": "", "Menu Group": l_day.menu_group,
            "Menu": l_day.menu, "Sales Category": l_day.category.map(lambda c: "Alcohol" if c == "Bar" else ("N/A Bev" if c == "Beverages" else "Food")),
            "Gross Price": l_day.gross.map(money), "Discnt": l_day.discount.map(money), "Net Price": l_day.net.map(money),
            "Qty": l_day.qty, "Tax": l_day.tax.map(money), "Void?": l_day.void.map(bl), "Deferred": "false",
            "Tax Exempt": "false", "Tax Inclusion Option": "Tax Not Included", "Dining Option Tax": "", "Tab Name": "",
        })
        isd.to_csv(os.path.join(folder, "ItemSelectionDetails.csv"), index=False)

        # --- ModifiersSelectionDetails.csv  (header typo "Sales Cateogry" is Toast's own)
        m_day = mods[mods.business_date == bd]
        msd = pd.DataFrame({
            "Location": loc, "Order Id": m_day.order_id, "Order #": m_day.order_num, "Sent Date": m_day.sent.map(tdt),
            "Order Date": m_day.opened.map(tdt), "Check Id": m_day.check_id, "Server": m_day.server, "Table": m_day.table,
            "Dining Area": m_day.area, "Service": m_day.opened.map(service), "Dining Option": m_day.dining,
            "Item Selection Id": m_day.selection_id, "Modifier Id": m_day.modifier_id, "Master Id": m_day.master_id,
            "Modifier SKU": "", "Modifier": m_day.modifier, "Option Group ID": m_day.group_id,
            "Option Group Name": m_day.group_name, "Parent Menu Selection Item ID": m_day.parent_item_id,
            "Parent Menu Selection": m_day.parent_name,
            "Sales Cateogry": m_day.category.map(lambda c: "Alcohol" if c == "Bar" else ("N/A Bev" if c == "Beverages" else "Food")),
            "Gross Price": m_day.gross.map(money), "Discnt": m_day.discount.map(money), "Net Price": m_day.net.map(money),
            "Qty": m_day.qty, "Void?": m_day.void.map(bl), "Void Reason ID": "", "Void Reason": "",
        })
        msd.to_csv(os.path.join(folder, "ModifiersSelectionDetails.csv"), index=False)

        # --- PaymentDetails.csv
        p_day = pays[pays.business_date == bd]
        pdd = pd.DataFrame({
            "Location": loc, "Payment Id": p_day.payment_id, "Order Id": p_day.order_id, "Order #": p_day.order_num,
            "Paid Date": p_day.paid.map(tdt), "Order Date": p_day.opened.map(tdt), "Check Id": p_day.check_id,
            "Check #": p_day.order_num, "Tab Name": p_day.tab, "Server": p_day.server, "Table": p_day.table,
            "Dining Area": p_day.area, "Service": p_day.opened.map(service), "Dining Option": p_day.dining,
            "House Acct #": "", "Amount": p_day.amount.map(money), "Tip": p_day.tip.map(money),
            "Gratuity": p_day.gratuity.map(money), "Total": p_day.total.map(money),
            "Swiped Card Amount": p_day.apply(lambda r: money(r.total) if r.type == "Credit" else "0.00", axis=1),
            "Keyed Card Amount": "0.00", "Amount Tendered": p_day.total.map(money),
            "Refunded": p_day.refunded.map(lambda b: "Partial" if b else "None"),
            "Refund Date": p_day.refund_date.map(tdt), "Refund Amount": p_day.refund_amount.map(money),
            "Refund Tip Amount": "0.00", "Void User": "", "Void Approver": "", "Void Date": "",
            "Status": p_day.voided.map(lambda v: "VOIDED" if v else "CAPTURED"), "Type": p_day.type,
            "Cash Drawer": p_day.type.map(lambda t: "Drawer 1" if t == "Cash" else ""), "Card Type": p_day.card_type,
            "Other Type": p_day.other_type, "Email": "", "Phone": "", "Last 4 Card Digits": p_day.last4,
            "Receipt": "", "V/MC/D Fees": p_day.fees.map(money),
        })
        pdd.to_csv(os.path.join(folder, "PaymentDetails.csv"), index=False)

        # --- AllItemsReport.csv (product mix for the day)
        g = l_day.groupby(["master_id", "item_id", "menu", "menu_group", "name"], as_index=False).agg(
            qty_all=("qty", "sum"), gross_all=("gross", "sum"),
            qty=("qty", lambda s: int(s[~l_day.loc[s.index, "void"]].sum())),
            gross=("gross", lambda s: float(s[~l_day.loc[s.index, "void"]].sum())),
            vq=("qty", lambda s: int(s[l_day.loc[s.index, "void"]].sum())),
            va=("gross", lambda s: float(s[l_day.loc[s.index, "void"]].sum())),
            disc=("discount", "sum"), net=("net", "sum"), orders=("order_id", "nunique"))
        tq, ta = g.qty_all.sum(), g.gross_all.sum()
        air = pd.DataFrame({
            "Master ID": g.master_id, "Item ID": g.item_id, "Parent ID": "", "Menu Name": g.menu, "Menu Group": g.menu_group,
            "Subgroup": "", "Menu Item": g.name, "Avg Price": (g.gross_all / g.qty_all).round(2),
            "Item Qty (incl voids)": g.qty_all, "% of Ttl Qty (incl voids)": (g.qty_all / tq * 100).round(2),
            "Gross Amount (incl voids)": g.gross_all.round(2), "% of Ttl Amt (incl voids)": (g.gross_all / ta * 100).round(2),
            "Item Qty": g.qty, "Gross Amount": g.gross.round(2), "Void Qty": g.vq, "Void Amount": g.va.round(2),
            "Discount Amount": g.disc.round(2), "Net Amount": g.net.round(2), "# Orders": g.orders,
        })
        air.to_csv(os.path.join(folder, "AllItemsReport.csv"), index=False)

        # --- TimeEntries.csv
        t_day = te[te.business_date == bd]
        ted = pd.DataFrame({
            "Location": loc, "Location Code": T.spec["business"]["toast_location_code"], "Id": t_day.entry_id,
            "GUID": t_day.entry_id.map(lambda i: f"te-{i}"), "Employee Id": t_day.employee_id.map(lambda e: int(e[1:])),
            "Employee GUID": t_day.employee_id.map(lambda e: f"emp-{e}"), "Employee External Id": t_day.employee_id,
            "Employee": t_day.employee, "Job Id": t_day.role.map(lambda r: abs(hash(r)) % 900 + 100), "Job GUID": "",
            "Job Code": t_day.role.map(lambda r: r[:3].upper()), "Job Title": t_day.role, "In Date": t_day["in"].map(tdt),
            "Out Date": t_day["out"].map(tdt), "Total Hours": t_day.total_hours, "Unpaid Break Time": t_day.unpaid_break,
            "Paid Break Time": 0.0, "Payable Hours": t_day.payable_hours, "Cash Tips Declared": t_day.cash_tips.map(money),
            "Non Cash Tips": t_day.non_cash_tips.map(money), "Total Gratuity": "0.00",
            "Total Tips": (t_day.cash_tips + t_day.non_cash_tips).map(money), "Tips Withheld": "0.00",
            "Wage": t_day.wage.map(money), "Regular Hours": t_day.payable_hours, "Overtime Hours": 0.0,
            "Regular Pay": (t_day.payable_hours * t_day.wage).map(money), "Overtime Pay": "0.00",
            "Total Pay": (t_day.payable_hours * t_day.wage).map(money),
        })
        ted.to_csv(os.path.join(folder, "TimeEntries.csv"), index=False)

        # --- CashEntries.csv (drawer events)
        cash_total = p_day[p_day.type == "Cash"].total.sum()
        ce = pd.DataFrame([
            {"Location": loc, "Entry Id": f"ce-{bd.strftime('%Y%m%d')}-1", "Created Date": tdt(datetime.combine(bd, datetime.min.time()) + timedelta(hours=10)),
             "Action": "CASH_IN", "Amount": "200.00", "Cash Drawer": "Drawer 1", "Payout Reason": "", "Comment": "Opening float",
             "Employee": "Daniel Okafor", "Employee 2": "", "Payout Reason ": ""},
            {"Location": loc, "Entry Id": f"ce-{bd.strftime('%Y%m%d')}-2", "Created Date": tdt(datetime.combine(bd, datetime.min.time()) + timedelta(hours=23, minutes=30)),
             "Action": "CASH_OUT", "Amount": money(cash_total + 200), "Cash Drawer": "Drawer 1", "Payout Reason": "", "Comment": "Close drawer / deposit",
             "Employee": "Daniel Okafor", "Employee 2": "", "Payout Reason ": ""},
        ])
        ce.to_csv(os.path.join(folder, "CashEntries.csv"), index=False)

    # --- MenuExport json (once)
    menu_json = {"entityType": "Menu", "name": "Main Menu", "guid": "menu-main", "groups": []}
    for cat, items in pd.DataFrame(T.spec["menu"]).groupby("category"):
        menu_json["groups"].append({"entityType": "MenuGroup", "name": cat, "guid": f"grp-{cat}", "items": [
            {"entityType": "MenuItem", "name": r.name, "guid": f"item-{r.sku}", "sku": r.sku, "plu": "",
             "price": float(T.menu_prices[r.sku]["end"]), "optionGroups": [T.spec["modifier_groups"][g]["name"] for g in (r.mods if isinstance(r.mods, list) else [])]}
            for r in items.itertuples()]})
    with open(os.path.join(root, "MenuExport_1.json"), "w") as f:
        json.dump(menu_json, f, indent=1)

    # --- Guest feedback (monthly report export)
    fb = T.feedback.copy()
    fb["month"] = fb.business_date.map(lambda d: d.strftime("%Y-%m"))
    fdir = ensure(os.path.join(root, "guest_feedback"))
    for m, g in fb.groupby("month"):
        pd.DataFrame({"Location": loc, "Submitted Date": g.submitted.map(tdt), "Business Date": g.business_date.map(td),
                      "Order Id": g.order_id, "Check Id": g.check_id, "Rating": g.rating, "Customer Id": g.customer_id,
                      "Comment": ""}).to_csv(os.path.join(fdir, f"GuestFeedback_{m}.csv"), index=False)

    # --- Toast Payments payouts (settlement report)
    cp = T.card_payouts
    pdir = ensure(os.path.join(root, "payouts"))
    pd.DataFrame({"Location": loc, "Business Date": cp.business_date.map(td), "Payout Date": cp.payout_date.map(td),
                  "Payout Id": cp.payout_id, "Card Sales": cp.card_sales.round(2), "Tips": cp.tips.round(2),
                  "Gratuity": cp.gratuity.round(2), "Refunds": cp.refunds.round(2), "Processing Fees": cp.fees.round(2),
                  "Transactions": cp.txns, "Net Deposit": cp.net_deposit.round(2),
                  "Bank Account": "Commerce Bank ****4417"}).to_csv(os.path.join(pdir, "Toast_Payouts_Oct2025-Sep2026.csv"), index=False)


# --------------------------------------------------------------------------- #
# DoorDash payouts
# --------------------------------------------------------------------------- #
def render_doordash(T: Truth, root: str):
    d = ensure(os.path.join(root, "doordash"))
    p = T.dd_payouts
    pd.DataFrame({"Payout date": p.payout_date.map(lambda x: x.isoformat()), "Payout ID": p.payout_id, "Status": "Paid",
                  "Store": T.spec["business"]["name"], "Period start": p.period_start.map(lambda x: x.isoformat()),
                  "Period end": p.period_end.map(lambda x: x.isoformat()), "Orders": p.orders,
                  "Subtotal": p.subtotal.round(2), "Tax (subtotal)": p.tax.round(2), "Sales": (p.subtotal + p.tax).round(2),
                  "DoorDash commission and fees": (-p.commission).round(2), "Marketing spend": (-p.marketing).round(2),
                  "Amendments": (-p.error_charges).round(2), "Net payout": p.net_payout.round(2)
                  }).to_csv(os.path.join(d, "DoorDash_Payouts_Oct2025-Sep2026.csv"), index=False)


# --------------------------------------------------------------------------- #
# QuickBooks Online exports
# --------------------------------------------------------------------------- #
QBO_ACCOUNT = {
    "Raw Materials": "Cost of Goods Sold:Food Purchases", "Beverages": "Cost of Goods Sold:Beverage Purchases",
    "Packaging": "Cost of Goods Sold:Packaging & Supplies", "Cleaning Supplies": "Cost of Goods Sold:Cleaning Supplies",
}
QBO_EXPENSE_ACCOUNT = {
    "Rent": "Occupancy:Rent", "CAM / Common Area Maintenance": "Occupancy:CAM", "Electricity": "Utilities:Electricity",
    "Gas": "Utilities:Gas", "Water & Sewer": "Utilities:Water & Sewer", "Internet": "Utilities:Internet", "Phone": "Utilities:Phone",
    "POS / Software Subscription": "Software & Subscriptions:POS", "Accounting Software": "Software & Subscriptions:Accounting",
    "Business Insurance": "Insurance:General Liability", "Equipment Lease": "Equipment Lease", "Music Subscription": "Operations:Music",
    "Linen Service": "Operations:Linen", "Pest Control": "Operations:Pest Control", "Waste Disposal": "Operations:Waste",
    "Local Marketing": "Marketing:Local", "Digital Advertising": "Marketing:Digital", "Bank Charges": "Bank Charges",
    "Equipment Maintenance": "Repairs & Maintenance", "Accounting / Bookkeeping": "Professional Fees", "Licenses & Permit Renewal": "Licenses & Permits",
    "Smallwares": "Operations:Smallwares",
}


def render_qbo(T: Truth, root: str):
    d = ensure(os.path.join(root, "quickbooks"))
    b = T.bills
    # Transaction Detail by Account style, Bills only, one row per line
    bills = pd.DataFrame({
        "Date": b.invoice_date.map(td), "Transaction Type": "Bill", "Num": b.invoice_id, "Name": b.vendor,
        "Memo/Description": b.description, "Account": b.category.map(QBO_ACCOUNT), "Qty": b.qty, "Unit": b.unit,
        "Rate": b.unit_cost, "Amount": b.line_total, "Due Date": b.due_date.map(td), "Terms": b.terms.map(lambda t: f"Net {t}"),
        "Open Balance": b.apply(lambda r: 0.0 if r.status == "Paid" else r.line_total, axis=1),
        "A/P Paid": b.status.map(lambda s: "Paid" if s == "Paid" else "Unpaid"),
    })
    bills.to_excel(os.path.join(d, "QBO_Bills_TransactionDetail_Oct2025-Sep2026.xlsx"), index=False, sheet_name="Transaction Detail")

    # Transaction List by Date: Bill Payments, Expenses, Sales Tax Payments, recurring bills + their payments
    rows = []
    paid_inv = b[b.status == "Paid"].groupby(["invoice_id", "vendor", "paid_date", "category"], as_index=False).line_total.sum()
    for r in paid_inv.itertuples():
        rows.append({"Date": td(r.paid_date), "Transaction Type": "Bill Payment (Check)", "Num": f"BP-{r.invoice_id}", "Name": r.vendor,
                     "Memo/Description": f"Payment for {r.invoice_id}", "Account": "Commerce Bank Checking",
                     "Split": "Accounts Payable (A/P)", "Payment Method": "Bank ACH" if r.vendor != "Fresh Harvest Produce" else "Check",
                     "Amount": -round(float(r.line_total), 2), "Class": "Cost of Goods"})
    for r in T.fixed_bills.itertuples():
        rows.append({"Date": td(r.invoice_date), "Transaction Type": "Bill", "Num": r.reference, "Name": r.vendor,
                     "Memo/Description": "Recurring monthly bill", "Account": "Accounts Payable (A/P)",
                     "Split": QBO_EXPENSE_ACCOUNT[r.category], "Payment Method": "", "Amount": round(float(r.amount), 2),
                     "Class": "Operating Expense", "Due Date": td(r.paid_date)})
        if r.status == "Paid":
            rows.append({"Date": td(r.paid_date), "Transaction Type": "Bill Payment (Check)", "Num": f"BP-{r.reference}", "Name": r.vendor,
                         "Memo/Description": f"Payment for {r.reference}", "Account": "Commerce Bank Checking",
                         "Split": "Accounts Payable (A/P)", "Payment Method": "Bank ACH", "Amount": -round(float(r.amount), 2),
                         "Class": "Operating Expense"})
    for r in T.misc_expenses.itertuples():
        rows.append({"Date": td(r.date), "Transaction Type": "Expense", "Num": r.reference, "Name": r.vendor,
                     "Memo/Description": r.notes, "Account": "Commerce Bank Checking" if r.payment_method != "Business Card" else "Business Credit Card",
                     "Split": QBO_EXPENSE_ACCOUNT[r.category], "Payment Method": r.payment_method, "Amount": -round(float(r.amount), 2),
                     "Class": "Operating Expense"})
    for r in T.sales_tax.itertuples():
        if r.status != "Paid":
            continue
        rows.append({"Date": td(r.paid_date), "Transaction Type": "Sales Tax Payment", "Num": r.reference, "Name": r.vendor,
                     "Memo/Description": f"Sales tax remittance period {r.period}", "Account": "Commerce Bank Checking",
                     "Split": "Sales Tax Payable", "Payment Method": "Bank ACH", "Amount": -round(float(r.amount), 2),
                     "Class": "Liability Payment"})
    tl = pd.DataFrame(rows)
    tl["_d"] = pd.to_datetime(tl.Date)
    tl = tl.sort_values(["_d", "Transaction Type"]).drop(columns="_d")
    tl.to_excel(os.path.join(d, "QBO_TransactionList_Oct2025-Sep2026.xlsx"), index=False, sheet_name="Transaction List")

    # Vendors list and chart of accounts
    vend = sorted(set(b.vendor) | set(T.fixed_bills.vendor) | set(T.misc_expenses.vendor))
    pd.DataFrame({"Vendor": vend, "Company": vend, "Phone": "", "Email": "", "Terms": "", "Account No.": ""}).to_excel(
        os.path.join(d, "QBO_Vendors.xlsx"), index=False)
    coa = sorted(set(QBO_ACCOUNT.values()) | set(QBO_EXPENSE_ACCOUNT.values()) | {"Commerce Bank Checking", "Accounts Payable (A/P)", "Sales Tax Payable", "Business Credit Card"})
    pd.DataFrame({"Account": coa, "Type": ["Cost of Goods Sold" if a.startswith("Cost of") else ("Bank" if "Checking" in a else ("Credit Card" if "Credit Card" in a else ("Other Current Liability" if a in ("Accounts Payable (A/P)", "Sales Tax Payable") else "Expense"))) for a in coa]}).to_excel(
        os.path.join(d, "QBO_ChartOfAccounts.xlsx"), index=False)


# --------------------------------------------------------------------------- #
# Gusto payroll journal
# --------------------------------------------------------------------------- #
def render_gusto(T: Truth, root: str):
    d = ensure(os.path.join(root, "gusto"))
    p = T.payroll
    j = pd.DataFrame({
        "Payroll ID": p.batch, "Pay period start": p.period_start.map(lambda x: x.isoformat()),
        "Pay period end": p.period_end.map(lambda x: x.isoformat()), "Check date": p.pay_date.map(lambda x: x.isoformat()),
        "Employee ID": p.employee_id, "Employee": p.employee, "Job title": p.role, "Compensation type": p.pay_type,
        "Hourly rate": p.rate, "Annual salary": p.salary, "Regular hours": p.regular_hours, "Overtime hours": p.overtime_hours,
        "Regular earnings": (p.gross - p.ot_premium * 3).where(p.pay_type == "Hourly", p.gross).round(2),
        "Overtime earnings": (p.ot_premium * 3).round(2), "Bonus": p.bonus, "Paycheck tips": p.tips,
        "Gross earnings": (p.gross + p.bonus).round(2), "Employee taxes": p.employee_tax,
        "Total employee deductions": p.deductions, "Net pay": p.net_pay, "Employer taxes": p.employer_tax,
        "Total employer contributions": p.benefits, "Total employer cost": p.total_employer_cost,
    })
    j.to_csv(os.path.join(d, "Gusto_PayrollJournal_Oct2025-Sep2026.csv"), index=False)
    emps = pd.DataFrame(T.spec["employees"])
    pd.DataFrame({"Employee ID": emps.id, "First name": emps.name.str.split().str[0], "Last name": emps.name.str.split().str[-1],
                  "Job title": emps.role, "Compensation type": emps.pay_type, "Hourly rate": emps.get("rate"),
                  "Annual salary": emps.get("salary"), "Status": "Active",
                  "Benefits per pay period (employer)": emps.benefits}).to_csv(os.path.join(d, "Gusto_EmployeeDirectory.csv"), index=False)


# --------------------------------------------------------------------------- #
# Owner inputs and external enrichment
# --------------------------------------------------------------------------- #
def render_owner_and_enrichment(T: Truth, root: str):
    d = ensure(os.path.join(root, "owner_inputs"))
    menu = pd.DataFrame(T.spec["menu"])
    pd.DataFrame({"SKU": menu.sku, "Menu Item": menu.name, "Category": menu.category, "Diet": menu.get("diet", "veg"),
                  "Unit COGS": menu.cost}).to_csv(os.path.join(d, "menu_costs.csv"), index=False)
    mrows = []
    for gkey, g in T.spec["modifier_groups"].items():
        for o in g["options"]:
            mrows.append({"Modifier Group": g["name"], "Modifier": o["name"], "Unit COGS": o["cost"]})
    pd.DataFrame(mrows).to_csv(os.path.join(d, "modifier_costs.csv"), index=False)
    ev = pd.DataFrame(T.spec["events"])
    pd.DataFrame({"Date": ev.date.map(lambda x: x.isoformat()), "Event": ev.name}).to_csv(os.path.join(d, "events_calendar.csv"), index=False)
    pr = pd.DataFrame(T.spec["promotions"])
    pd.DataFrame({"Promotion": pr.name}).to_csv(os.path.join(d, "promotions.csv"), index=False)

    e = ensure(os.path.join(root, "enrichment"))
    T.weather.to_csv(os.path.join(e, "NOAA_GHCND_KansasCity_Oct2025-Sep2026.csv"), index=False)


def render_all(T: Truth, root: str):
    render_toast(T, root)
    render_doordash(T, root)
    render_qbo(T, root)
    render_gusto(T, root)
    render_owner_and_enrichment(T, root)
