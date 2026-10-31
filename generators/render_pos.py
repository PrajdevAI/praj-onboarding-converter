"""
Render the same simulated year as four more POS systems' exports.

Layout provenance:
  * Square      - Transactions CSV and Items Detail CSV headers as posted by Square sellers
                  (sellercommunity.com, 2025).  Modifiers are flattened into one "Modifiers Applied"
                  string; tips and fees exist only in the Transactions CSV.  DOCUMENTED (seller-posted).
  * Clover      - Orders / Line Items / Payments / Voided Line Items exports RECONSTRUCTED from Clover's
                  REST API field names (orders, lineItems, modifications, payments, voided_line_items).
  * Lightspeed  - Restaurant K-Series receipt / receipt-item exports RECONSTRUCTED (vendor publishes report
                  names, not columns).  Day-first dates, modifiers separated by " | ".
  * SpotOn      - Restaurant POS Export API objects (Orders, Order Items, Payments) RECONSTRUCTED as CSV;
                  ISO-8601 timestamps with offset.
Values are synthetic; every reconstructed layout must be confirmed against one real export.
"""
from __future__ import annotations

import os
from datetime import timedelta

import numpy as np
import pandas as pd

from .truth import Truth


def ensure(p):
    os.makedirs(p, exist_ok=True)
    return p


def money(x) -> str:
    return f"{float(x):.2f}"


def sq_money(x) -> str:
    v = float(x) + 0.0          # normalises -0.0
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def iso(x) -> str:
    return pd.Timestamp(x).strftime("%Y-%m-%d %H:%M:%S")


def iso_tz(x, offset="-05:00") -> str:
    return pd.Timestamp(x).strftime("%Y-%m-%dT%H:%M:%S") + offset


def dmy(x) -> str:
    return pd.Timestamp(x).strftime("%d/%m/%Y %H:%M")


def epoch_ms(x) -> int:          # Clover stores UTC epoch ms; store is UTC-5 in the demo
    return int((pd.Timestamp(x) + pd.Timedelta(hours=5)).timestamp() * 1000)


def mods_by_line(T: Truth) -> dict:
    """selection_id -> list of (modifier name, price for the line)"""
    out = {}
    for r in T.mods.itertuples():
        out.setdefault(r.selection_id, []).append((r.modifier, float(r.gross)))
    return out


def mod_disc_by_line(T: Truth) -> dict:
    return T.mods.groupby("selection_id").discount.sum().to_dict()


def month_groups(df: pd.DataFrame, col="business_date"):
    return df.groupby(df[col].map(lambda d: d.strftime("%Y-%m")))


def write_feedback(T: Truth, folder: str, name: str, order_col="Order Id"):
    fb = T.feedback
    fdir = ensure(os.path.join(folder, "feedback"))
    for m, g in month_groups(fb):
        pd.DataFrame({order_col: g.order_id, "Business Date": g.business_date.map(lambda d: d.isoformat()),
                      "Submitted": g.submitted.map(iso), "Rating": g.rating, "Customer Id": g.customer_id, "Comment": ""}
                     ).to_csv(os.path.join(fdir, f"{name}_GuestFeedback_{m}.csv"), index=False)


def write_card_payouts(T: Truth, folder: str, filename: str, cols: dict):
    cp = T.card_payouts
    base = {"business_date": cp.business_date.map(lambda d: d.isoformat()), "payout_date": cp.payout_date.map(lambda d: d.isoformat()),
            "payout_id": cp.payout_id, "card_sales": cp.card_sales.round(2), "tips": (cp.tips + cp.gratuity).round(2),
            "refunds": cp.refunds.round(2), "fees": cp.fees.round(2), "net": cp.net_deposit.round(2), "txns": cp.txns}
    pd.DataFrame({cols[k]: v for k, v in base.items() if k in cols}).to_csv(os.path.join(folder, filename), index=False)


# --------------------------------------------------------------------------- #
# Square
# --------------------------------------------------------------------------- #
SQ_SOURCE = {"In store": "Point of Sale", "Online": "Square Online"}
SQ_DINING = {"Dine In": "For Here", "Take Out": "To Go", "Delivery": "Delivery", "Catering": "Catering"}
TX_COLS = ["Date", "Time", "Time Zone", "Gross Sales", "Discounts", "Service Charges", "Net Sales", "Gift Card Sales", "Tax", "Tip", "Partial Refunds",
           "Total Collected", "Source", "Card", "Card Entry Methods", "Cash", "Square Gift Card", "Other Tender", "Other Tender Type", "Other Tender Note",
           "Fees", "Net Total", "Transaction ID", "Payment ID", "Card Brand", "PAN Suffix", "Device Name", "Staff Name", "Staff ID", "Details", "Description",
           "Event Type", "Location", "Dining Option", "Customer ID", "Customer Name", "Customer Reference ID", "Initial Fees", "Adjustment Fees",
           "Device Nickname", "Fee Plan", "Number of Adjustments", "Adjustment Date(s)", "Third Party Fees", "Deposit ID", "Deposit Date", "Deposit Details",
           "Fee Percentage Rate", "Fee Fixed Rate", "Refund Reason", "Discount Name", "Transaction Status", "Cash App", "Order Reference ID", "Fulfillment Note"]
IT_COLS = ["Date", "Time", "Time Zone", "Category", "Item", "Qty", "Price Point Name", "SKU", "Modifiers Applied", "Gross Sales", "Discounts", "Net Sales", "Tax",
           "Transaction ID", "Payment ID", "Device Name", "Notes", "Details", "Event Type", "Location", "Dining Option", "Customer ID", "Customer Name",
           "Customer Reference ID", "Unit", "Count", "GTIN", "Itemization Type", "Commission", "Employee", "Fulfillment Note"]


def render_square(T: Truth, root: str):
    d = ensure(os.path.join(root, "square"))
    loc = T.spec["business"]["location_name"]
    tz = "Central Time (US & Canada)"
    orders = T.orders.set_index("order_id")
    pays = T.payments[~T.payments.voided]
    ml = mods_by_line(T)
    mdl = mod_disc_by_line(T)
    cp = T.card_payouts.set_index("business_date")

    # Transactions CSV: one row per payment, plus one row per refund event
    for m, g in month_groups(pays):
        rows = []
        for r in g.itertuples():
            o = orders.loc[r.order_id]
            desc = ", ".join(T.lines[T.lines.order_id == r.order_id].name.tolist()[:6]) if False else ""
            card = r.type == "Credit"
            dep = cp.loc[r.business_date] if (card and r.business_date in cp.index) else None
            row = dict.fromkeys(TX_COLS, "")
            row.update({
                "Date": pd.Timestamp(r.paid).strftime("%Y-%m-%d"), "Time": pd.Timestamp(r.paid).strftime("%H:%M:%S"), "Time Zone": tz,
                "Gross Sales": sq_money(r.net_sales + o.discount), "Discounts": sq_money(-o.discount), "Service Charges": sq_money(r.gratuity),
                "Net Sales": sq_money(r.net_sales), "Gift Card Sales": "$0.00", "Tax": sq_money(r.tax), "Tip": sq_money(r.tip),
                "Partial Refunds": "$0.00", "Total Collected": sq_money(r.total),
                "Source": "DoorDash" if r.other_type == "DoorDash" else SQ_SOURCE[o.source],
                "Card": sq_money(r.total) if card else "$0.00", "Card Entry Methods": "Tapped" if card else "",
                "Cash": sq_money(r.total) if r.type == "Cash" else "$0.00", "Square Gift Card": sq_money(r.total) if r.type == "Gift Card" else "$0.00",
                "Other Tender": sq_money(r.total) if r.type == "Other" else "$0.00", "Other Tender Type": r.other_type if r.type == "Other" else "",
                "Fees": sq_money(-r.fees) if card else "$0.00", "Net Total": sq_money(r.total - r.fees),
                "Transaction ID": r.order_id, "Payment ID": r.payment_id, "Card Brand": r.card_type, "PAN Suffix": r.last4,
                "Device Name": "Register 1" if o.source == "In store" else "Square Online", "Staff Name": o.server, "Staff ID": "",
                "Details": "Details", "Description": desc, "Event Type": "Payment", "Location": loc, "Dining Option": SQ_DINING[o.dining],
                "Customer ID": o.customer_id, "Customer Name": o.tab, "Initial Fees": sq_money(-r.fees) if card else "$0.00",
                "Adjustment Fees": "$0.00", "Fee Plan": "Standard", "Number of Adjustments": "0",
                "Deposit ID": dep.payout_id if dep is not None else "", "Deposit Date": dep.payout_date.isoformat() if dep is not None else "",
                "Fee Percentage Rate": "2.65%" if card else "", "Fee Fixed Rate": "$0.10" if card else "",
                "Discount Name": o.promo or "", "Transaction Status": "Complete", "Cash App": "$0.00",
            })
            rows.append(row)
            if r.refunded:
                rr = dict.fromkeys(TX_COLS, "")
                rr.update({"Date": pd.Timestamp(r.refund_date).strftime("%Y-%m-%d"), "Time": pd.Timestamp(r.refund_date).strftime("%H:%M:%S"), "Time Zone": tz,
                           "Gross Sales": sq_money(-r.refund_amount), "Discounts": "$0.00", "Net Sales": sq_money(-r.refund_amount), "Tax": "$0.00",
                           "Tip": "$0.00", "Total Collected": sq_money(-r.refund_amount), "Source": SQ_SOURCE[o.source],
                           "Card": sq_money(-r.refund_amount) if card else "$0.00", "Cash": sq_money(-r.refund_amount) if r.type == "Cash" else "$0.00",
                           "Fees": "$0.00", "Net Total": sq_money(-r.refund_amount), "Transaction ID": r.order_id, "Payment ID": r.payment_id,
                           "Event Type": "Refund", "Location": loc, "Refund Reason": "Customer request", "Transaction Status": "Complete"})
                rows.append(rr)
        pd.DataFrame(rows, columns=TX_COLS).to_csv(os.path.join(d, f"transactions-{m}.csv"), index=False)

    # Items Detail CSV: one row per item line (Square does not export voided-before-payment items)
    lines = T.lines[~T.lines.void]
    for m, g in month_groups(lines):
        rows = []
        for r in g.itertuples():
            o = orders.loc[r.order_id]
            mods = ml.get(r.selection_id, [])
            mod_txt = ", ".join(f"{n} (${p:.2f})" if p else n for n, p in mods)
            mod_gross = sum(p for _, p in mods)
            mod_disc = float(mdl.get(r.selection_id, 0.0))
            row = dict.fromkeys(IT_COLS, "")
            row.update({"Date": pd.Timestamp(r.sent).strftime("%Y-%m-%d"), "Time": pd.Timestamp(r.sent).strftime("%H:%M:%S"), "Time Zone": tz,
                        "Category": r.category, "Item": r.name, "Qty": r.qty, "Price Point Name": "Regular", "SKU": r.sku, "Modifiers Applied": mod_txt,
                        "Gross Sales": sq_money(r.gross + mod_gross), "Discounts": sq_money(-(r.discount + mod_disc)),
                        "Net Sales": sq_money(r.net + mod_gross - mod_disc), "Tax": sq_money(r.tax), "Transaction ID": r.order_id,
                        "Payment ID": "", "Device Name": "Register 1", "Details": "Details", "Event Type": "Payment", "Location": loc,
                        "Dining Option": SQ_DINING[r.dining], "Customer ID": o.customer_id, "Customer Name": o.tab, "Count": r.qty,
                        "Itemization Type": "Item", "Employee": r.server})
            rows.append(row)
        pd.DataFrame(rows, columns=IT_COLS).to_csv(os.path.join(d, f"items-{m}.csv"), index=False)

    # Item library export (catalog)
    menu = pd.DataFrame(T.spec["menu"])
    pd.DataFrame({"Token": menu.sku.map(lambda s: "tok_" + s.replace("-", "").lower()), "Item Name": menu.name, "Variation Name": "Regular",
                  "SKU": menu.sku, "Description": "", "Categories": menu.category, "Reporting Category": menu.category,
                  "Price": menu.sku.map(lambda s: T.menu_prices[s]["end"]), "Item Type": "Prepared food and beverage",
                  "Tax - Sales (8.25%)": "Y"}).to_csv(os.path.join(d, "catalog-items.csv"), index=False)

    # Transfers (deposits) report - reconstructed layout
    write_card_payouts(T, d, "transfers-2025-10-01-2026-09-30.csv",
                       {"payout_date": "Transfer Date", "payout_id": "Transfer ID", "business_date": "Sales Date", "card_sales": "Collected",
                        "tips": "Tips", "refunds": "Refunds", "fees": "Fees", "net": "Transfer Amount", "txns": "Payments"})
    write_feedback(T, d, "Square", order_col="Transaction ID")


# --------------------------------------------------------------------------- #
# Clover (reconstructed from API field names)
# --------------------------------------------------------------------------- #
CL_TYPE = {"Dine In": "Dine In", "Take Out": "To Go", "Delivery": "DoorDash", "Catering": "Catering"}
CL_TENDER = {"Credit": "Credit Card", "Cash": "Cash", "Gift Card": "Gift Card", "Other": "External - DoorDash"}


def render_clover(T: Truth, root: str):
    d = ensure(os.path.join(root, "clover"))
    orders = T.orders
    ml = mods_by_line(T)
    mdl = mod_disc_by_line(T)
    for m, g in month_groups(orders):
        pd.DataFrame({"Order ID": g.order_id.map(lambda x: x.replace("-", "")[:13].upper()), "Order Number": g.order_num,
                      "Created Time": g.opened.map(iso), "Created Time (ms)": g.opened.map(epoch_ms), "Modified Time": g.closed.map(iso),
                      "Order Type": g.dining.map(CL_TYPE), "Employee": g.server, "Customer ID": g.customer_id, "Customer Name": g.tab,
                      "State": g.voided.map(lambda v: "voided" if v else "locked"), "Subtotal": (g.amount + g.discount).round(2),
                      "Discount Amount": g.discount.round(2), "Discount Name": g.promo.fillna(""), "Tax": g.tax.round(2),
                      "Service Charge": g.gratuity.round(2), "Tip": g.tip.round(2), "Total": g.total.round(2),
                      "Payment State": g.voided.map(lambda v: "OPEN" if v else "PAID"), "Guests": g.guests, "Note": ""}
                     ).to_csv(os.path.join(d, f"Clover_Orders_{m}.csv"), index=False)
    lines = T.lines
    for m, g in month_groups(lines):
        live, void = g[~g.void], g[g.void]
        def mtxt(sel):
            return "; ".join(f"{n}:{p:.2f}" for n, p in ml.get(sel, []))
        def mamt(sel):
            return round(sum(p for _, p in ml.get(sel, [])), 2)
        pd.DataFrame({"Line Item ID": live.selection_id.map(lambda x: x.replace("-", "")[:13].upper()),
                      "Order ID": live.order_id.map(lambda x: x.replace("-", "")[:13].upper()), "Created Time": live.sent.map(iso),
                      "Item ID": live.item_id, "Item Name": live.name, "Category": live.category, "SKU": live.sku, "Quantity": live.qty,
                      "Unit Price": live.unit_price.round(2), "Modifiers": live.selection_id.map(mtxt), "Modifier Amount": live.selection_id.map(mamt),
                      "Price": (live.gross + live.selection_id.map(mamt)).round(2),
                      "Discount Amount": (live.discount + live.selection_id.map(lambda s: float(mdl.get(s, 0.0)))).round(2),
                      "Tax Amount": live.tax.round(2), "Is Revenue": "true", "Refunded": "false", "Printed": "true", "Employee": live.server}
                     ).to_csv(os.path.join(d, f"Clover_LineItems_{m}.csv"), index=False)
        pd.DataFrame({"Line Item ID": void.selection_id.map(lambda x: x.replace("-", "")[:13].upper()),
                      "Order ID": void.order_id.map(lambda x: x.replace("-", "")[:13].upper()), "Item Name": void.name, "Quantity": void.qty,
                      "Price": void.gross.round(2), "Reason": "Customer changed mind", "Delete Type": "VOID", "Deleted Time": void.sent.map(iso),
                      "Approved By": "Daniel Okafor"}).to_csv(os.path.join(d, f"Clover_VoidedLineItems_{m}.csv"), index=False)
    pays = T.payments[~T.payments.voided]
    for m, g in month_groups(pays):
        pd.DataFrame({"Payment ID": g.payment_id.map(lambda x: x.replace("-", "")[:13].upper()), "Order ID": g.order_id.map(lambda x: x.replace("-", "")[:13].upper()),
                      "Created Time": g.paid.map(iso), "Tender": g.type.map(CL_TENDER), "Card Type": g.card_type, "Last 4": g.last4,
                      "Amount": g.amount.round(2), "Tip Amount": (g.tip + g.gratuity).round(2), "Tax Amount": g.tax.round(2),
                      "Total": g.total.round(2), "Result": "SUCCESS", "Refund Amount": g.refund_amount.round(2),
                      "Refund Time": g.refund_date.map(lambda x: iso(x) if pd.notna(x) else ""), "Employee": g.server}
                     ).to_csv(os.path.join(d, f"Clover_Payments_{m}.csv"), index=False)
    write_card_payouts(T, d, "Clover_Deposits_2025-10-01_2026-09-30.csv",
                       {"business_date": "Batch Date", "payout_date": "Deposit Date", "payout_id": "Batch ID", "card_sales": "Gross Sales",
                        "tips": "Tips", "refunds": "Refunds", "fees": "Processing Fees", "net": "Net Deposit", "txns": "Transaction Count"})
    write_feedback(T, d, "Clover", order_col="Order ID")
    # feedback order ids must match Clover's id format
    for f in os.listdir(os.path.join(d, "feedback")):
        p = os.path.join(d, "feedback", f)
        fb = pd.read_csv(p, dtype=str)
        fb["Order ID"] = fb["Order ID"].map(lambda x: x.replace("-", "")[:13].upper())
        fb.to_csv(p, index=False)


# --------------------------------------------------------------------------- #
# Lightspeed Restaurant K-Series (reconstructed)
# --------------------------------------------------------------------------- #
LS_TYPE = {"Dine In": "Eat In", "Take Out": "Takeaway", "Delivery": "Delivery", "Catering": "Event"}
LS_PAY = {"Credit": "Card", "Cash": "Cash", "Gift Card": "Voucher", "Other": "DoorDash"}


def render_lightspeed(T: Truth, root: str):
    d = ensure(os.path.join(root, "lightspeed"))
    orders = T.orders.set_index("order_id")
    pays = T.payments[~T.payments.voided].set_index("order_id")
    ml = mods_by_line(T)
    mdl = mod_disc_by_line(T)
    for m, g in month_groups(T.orders):
        g = g[~g.voided]
        p = pays.reindex(g.order_id)
        pd.DataFrame({"Receipt Number": g.order_num.astype(str).radd("R-" + m.replace("-", "") + "-"), "Receipt ID": g.order_id,
                      "Opened": g.opened.map(dmy), "Closed": g.closed.map(dmy), "Table": g.table, "Covers": g.guests, "Staff": g.server,
                      "Order Type": g.dining.map(LS_TYPE), "Customer": g.customer_id.values, "Discount Name": g.promo.fillna("").values,
                      "Subtotal": (g.amount + g.discount).round(2).values, "Discount": g.discount.round(2).values, "Net": g.amount.round(2).values,
                      "Tax": g.tax.round(2).values, "Gratuity": g.gratuity.round(2).values, "Tip": g.tip.round(2).values, "Total": g.total.round(2).values,
                      "Payment Method": p.type.map(LS_PAY).values, "Card Brand": p.card_type.values,
                      "Paid At": p.paid.map(dmy).values, "Refunded": p.refund_amount.round(2).values,
                      "Refund Date": p.refund_date.map(lambda x: dmy(x) if pd.notna(x) else "").values, "Status": "Closed"}
                     ).to_csv(os.path.join(d, f"Lightspeed_Receipts_{m}.csv"), index=False)
    for m, g in month_groups(T.lines):
        def mtxt(sel):
            return " | ".join(f"{n} (+{p:.2f})" if p else n for n, p in ml.get(sel, []))
        def mamt(sel):
            return round(sum(p for _, p in ml.get(sel, [])), 2)
        mdisc = g.selection_id.map(lambda s: float(mdl.get(s, 0.0)))
        pd.DataFrame({"Receipt ID": g.order_id, "Line ID": g.selection_id, "Time": g.sent.map(dmy), "SKU": g.sku, "Product": g.name,
                      "Accounting Group": g.category, "Quantity": g.qty, "Unit Price": g.unit_price.round(2), "Modifiers": g.selection_id.map(mtxt),
                      "Gross": (g.gross + g.selection_id.map(mamt)).round(2), "Discount": (g.discount + mdisc).round(2),
                      "Net": (g.net + g.selection_id.map(mamt) - mdisc).round(2), "Tax": g.tax.round(2),
                      "Status": g.void.map(lambda v: "Voided" if v else "Sold"), "Order Type": g.dining.map(LS_TYPE), "Staff": g.server}
                     ).to_csv(os.path.join(d, f"Lightspeed_ReceiptItems_{m}.csv"), index=False)
    write_card_payouts(T, d, "Lightspeed_Payments_Payouts_2025-10-01_2026-09-30.csv",
                       {"business_date": "Sales Day", "payout_date": "Payout Date", "payout_id": "Payout Reference", "card_sales": "Card Volume",
                        "tips": "Tips", "refunds": "Refunds", "fees": "Fees", "net": "Payout Amount", "txns": "Transactions"})
    write_feedback(T, d, "Lightspeed", order_col="Receipt ID")


# --------------------------------------------------------------------------- #
# SpotOn (reconstructed from Export API objects)
# --------------------------------------------------------------------------- #
SO_TYPE = {"Dine In": "DINE_IN", "Take Out": "TAKE_OUT", "Delivery": "THIRD_PARTY_DELIVERY", "Catering": "CATERING"}
SO_PAY = {"Credit": "CREDIT", "Cash": "CASH", "Gift Card": "GIFT_CARD", "Other": "THIRD_PARTY"}


def render_spoton(T: Truth, root: str):
    d = ensure(os.path.join(root, "spoton"))
    ml = mods_by_line(T)
    mdl = mod_disc_by_line(T)
    for m, g in month_groups(T.orders):
        pd.DataFrame({"Order Id": g.order_id, "Order Number": g.order_num, "Opened At": g.opened.map(iso_tz), "Closed At": g.closed.map(iso_tz),
                      "Order Type": g.dining.map(SO_TYPE), "Source": g.source.map({"In store": "POS", "Online": "ONLINE"}), "Server": g.server,
                      "Table": g.table, "Guest Count": g.guests, "Customer Id": g.customer_id, "Discount Names": g.promo.fillna(""),
                      "Subtotal": (g.amount + g.discount).round(2), "Discounts": g.discount.round(2), "Net Sales": g.amount.round(2),
                      "Surcharges": g.gratuity.round(2), "Tax": g.tax.round(2), "Tip": g.tip.round(2), "Total": g.total.round(2),
                      "Status": g.voided.map(lambda v: "VOIDED" if v else "CLOSED")}
                     ).to_csv(os.path.join(d, f"SpotOn_Orders_{m}.csv"), index=False)
    for m, g in month_groups(T.lines):
        def mtxt(sel):
            return "; ".join(f"{n} (+{p:.2f})" if p else n for n, p in ml.get(sel, []))
        def mamt(sel):
            return round(sum(p for _, p in ml.get(sel, [])), 2)
        mdisc = g.selection_id.map(lambda s: float(mdl.get(s, 0.0)))
        pd.DataFrame({"Order Item Id": g.selection_id, "Order Id": g.order_id, "Sent At": g.sent.map(iso_tz), "Menu Item Id": g.item_id,
                      "Menu Item": g.name, "Report Category": g.category, "SKU": g.sku, "Quantity": g.qty, "Base Price": g.unit_price.round(2),
                      "Modifiers": g.selection_id.map(mtxt), "Modifier Total": g.selection_id.map(mamt),
                      "Gross": (g.gross + g.selection_id.map(mamt)).round(2), "Discount": (g.discount + mdisc).round(2),
                      "Net": (g.net + g.selection_id.map(mamt) - mdisc).round(2), "Tax": g.tax.round(2), "Voided": g.void.map(lambda v: "true" if v else "false"),
                      "Order Type": g.dining.map(SO_TYPE)}).to_csv(os.path.join(d, f"SpotOn_OrderItems_{m}.csv"), index=False)
    pays = T.payments[~T.payments.voided]
    for m, g in month_groups(pays):
        pd.DataFrame({"Payment Id": g.payment_id, "Order Id": g.order_id, "Paid At": g.paid.map(iso_tz), "Payment Option": g.type.map(SO_PAY),
                      "Card Brand": g.card_type, "Last Four": g.last4, "Amount": g.amount.round(2), "Tip": g.tip.round(2), "Surcharge": g.gratuity.round(2),
                      "Total": g.total.round(2), "Status": "CAPTURED", "Refund Amount": g.refund_amount.round(2),
                      "Refunded At": g.refund_date.map(lambda x: iso_tz(x) if pd.notna(x) else "")}
                     ).to_csv(os.path.join(d, f"SpotOn_Payments_{m}.csv"), index=False)
    write_card_payouts(T, d, "SpotOn_Deposits_2025-10-01_2026-09-30.csv",
                       {"business_date": "Business Date", "payout_date": "Deposit Date", "payout_id": "Deposit Id", "card_sales": "Card Sales",
                        "tips": "Tips", "refunds": "Refunds", "fees": "Processing Fees", "net": "Net Deposit", "txns": "Transactions"})
    write_feedback(T, d, "SpotOn", order_col="Order Id")


def render_all_pos(T: Truth, root: str):
    render_square(T, root)
    render_clover(T, root)
    render_lightspeed(T, root)
    render_spoton(T, root)
