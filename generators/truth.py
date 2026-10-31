"""
Simulate one year of operations for the restaurant described in spec/restaurant.yaml.

The output is a set of pandas DataFrames ("truth") that the renderers turn into
vendor-shaped files (Toast CSV bundle, QuickBooks exports, Gusto payroll journal,
payout statements).  The converter never sees these frames; it only reads the
rendered files.  Reconciliation compares the converter's output to the rendered
files, not to the truth.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import yaml


def load_spec(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def guid(*parts) -> str:
    h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def r2(x: float) -> float:
    return float(round(x + 1e-9, 2))


def month_label(d: date) -> str:
    return d.strftime("%b %Y")


@dataclass
class Truth:
    spec: dict
    orders: pd.DataFrame = None
    lines: pd.DataFrame = None
    mods: pd.DataFrame = None
    payments: pd.DataFrame = None
    feedback: pd.DataFrame = None
    time_entries: pd.DataFrame = None
    payroll: pd.DataFrame = None
    bills: pd.DataFrame = None            # purchase invoice lines
    fixed_bills: pd.DataFrame = None
    misc_expenses: pd.DataFrame = None
    sales_tax: pd.DataFrame = None
    card_payouts: pd.DataFrame = None
    dd_payouts: pd.DataFrame = None
    weather: pd.DataFrame = None
    calendar: pd.DataFrame = None
    menu_prices: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def weighted_choice(rng, items, weights, size=None):
    w = np.asarray(weights, dtype=float)
    w = w / w.sum()
    idx = rng.choice(len(items), size=size, p=w)
    if size is None:
        return items[idx]
    return [items[i] for i in idx]


def pick_time(rng, bd: date, open_t: str, close_t: str) -> datetime:
    """Pick an order time with lunch and dinner peaks inside opening hours."""
    oh, om = map(int, open_t.split(":"))
    ch, cm = map(int, close_t.split(":"))
    start = datetime.combine(bd, time(oh, om))
    end = datetime.combine(bd, time(ch, cm))
    if end <= start:               # closes after midnight
        end += timedelta(days=1)
    total_min = int((end - start).total_seconds() // 60)
    # mixture: 35% lunch peak ~12:30, 65% dinner peak ~19:00
    if rng.random() < 0.35:
        center = datetime.combine(bd, time(12, 30))
        m = rng.normal(0, 55)
    else:
        center = datetime.combine(bd, time(19, 0))
        m = rng.normal(0, 85)
    t = center + timedelta(minutes=float(m))
    if t < start:
        t = start + timedelta(minutes=float(rng.integers(0, 40)))
    if t > end - timedelta(minutes=5):
        t = end - timedelta(minutes=float(rng.integers(5, 45)))
    return t.replace(second=int(rng.integers(0, 60)), microsecond=0)


# --------------------------------------------------------------------------- #
# weather + calendar enrichment (external to the POS)
# --------------------------------------------------------------------------- #
def build_weather(rng, start: date, end: date) -> pd.DataFrame:
    rows = []
    for d in daterange(start, end):
        doy = d.timetuple().tm_yday
        tmax = 62 + 28 * math.sin((doy - 105) / 365 * 2 * math.pi) + rng.normal(0, 7)
        tmin = tmax - 18 + rng.normal(0, 3)
        prcp = max(0.0, rng.gamma(0.5, 0.35)) if rng.random() < 0.30 else 0.0
        snow = max(0.0, rng.gamma(0.8, 1.6)) if (tmax < 36 and rng.random() < 0.25) else 0.0
        rows.append({"STATION": "USW00003947", "NAME": "KANSAS CITY DOWNTOWN AIRPORT, MO US",
                     "DATE": d.isoformat(), "PRCP": round(prcp, 2), "SNOW": round(snow, 1),
                     "TMAX": int(round(tmax)), "TMIN": int(round(tmin))})
    return pd.DataFrame(rows)


def weather_label(row) -> str:
    if row["SNOW"] >= 1.0:
        return "Snow"
    if row["PRCP"] >= 0.25:
        return "Rainy"
    if row["TMAX"] >= 92:
        return "Hot"
    if row["TMAX"] <= 28:
        return "Cold"
    return "Normal"


def weather_mult(label: str) -> float:
    return {"Snow": 0.72, "Rainy": 0.90, "Hot": 0.96, "Cold": 0.93, "Normal": 1.0}[label]


# --------------------------------------------------------------------------- #
# main simulation
# --------------------------------------------------------------------------- #
def simulate(spec: dict) -> Truth:
    rng = np.random.default_rng(spec["business"]["seed"])
    biz = spec["business"]
    start, end = biz["period_start"], biz["period_end"]
    tax_rate = biz["sales_tax_rate"]
    vol = spec["volume"]
    menu = spec["menu"]
    groups = spec["modifier_groups"]
    promos = spec["promotions"]
    events = {e["date"]: e for e in spec["events"]}
    emps = spec["employees"]
    servers = [e["name"] for e in emps if e["role"] in ("Server",)]
    bartenders = [e["name"] for e in emps if e["role"] == "Bartender"]

    T = Truth(spec=spec)
    T.weather = build_weather(rng, start, end)
    wlabel = {date.fromisoformat(r.DATE): weather_label(r._asdict()) for r in T.weather.itertuples()}

    # menu price by date (handles mid-year price change)
    base_price = {m["sku"]: m["price"] for m in menu}
    changes = spec.get("price_changes", [])

    def price_on(sku: str, d: date) -> float:
        p = base_price[sku]
        for c in changes:
            if sku in c["skus"] and d >= c["effective"]:
                p += c["increase"]
        return p

    T.menu_prices = {m["sku"]: {"start": base_price[m["sku"]], "end": price_on(m["sku"], end)} for m in menu}
    menu_by_sku = {m["sku"]: m for m in menu}
    skus = [m["sku"] for m in menu]
    weights = [m["w"] for m in menu]
    item_id = {m["sku"]: 300000 + i for i, m in enumerate(menu)}
    master_id = {m["sku"]: 200000 + i for i, m in enumerate(menu)}
    mod_id = {}
    k = 400000
    for gkey, g in groups.items():
        for o in g["options"]:
            mod_id[(gkey, o["name"])] = k
            k += 1
    group_id = {gkey: 6000 + i for i, gkey in enumerate(groups)}

    customers = [f"C{n:05d}" for n in range(1, 1401)]
    last_names = ["Rivera", "Nguyen", "Patel", "Smith", "Johnson", "Garcia", "Kim", "Brown", "Davis", "Lopez",
                  "Miller", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Martin", "Jackson", "White", "Harris"]

    orders, lines, mods, payments, feedback = [], [], [], [], []
    calendar_rows = []
    order_counter = 0

    for bd in daterange(start, end):
        wd = bd.weekday()
        ev = events.get(bd)
        ev_mult = ev["mult"] if ev else 1.0
        wl = wlabel[bd]
        promo_today = None
        for p in promos:
            if p.get("weekday") == wd:
                promo_today = p["name"]
        calendar_rows.append({"Date": bd, "Event": ev["name"] if ev else None, "Weather": wl,
                              "Promotion": promo_today})
        if ev_mult == 0.0:
            continue
        lam = (vol["base_orders_per_day"] * vol["weekday_multiplier"][wd] * vol["month_multiplier"][bd.month]
               * ev_mult * weather_mult(wl))
        n_orders = int(rng.poisson(lam))
        open_t, close_t = spec["hours"][wd]
        day_nums = list(range(1, n_orders + 1))
        rng.shuffle(day_nums)

        for i in range(n_orders):
            order_counter += 1
            dining = weighted_choice(rng, list(vol["channel_mix"].keys()), list(vol["channel_mix"].values()))
            opened = pick_time(rng, bd, open_t, close_t)
            if dining == "Catering":
                opened = datetime.combine(bd, time(int(rng.integers(10, 15)), int(rng.integers(0, 60))))
            order_id = guid("order", order_counter)
            check_id = guid("check", order_counter)
            order_num = int(day_nums[i])
            if dining == "Dine In":
                source, rc = "In store", ("Bar" if rng.random() < 0.18 else "Dining Room")
                server = rng.choice(bartenders) if rc == "Bar" else rng.choice(servers)
                table = f"{int(rng.integers(1, 28))}"
                guests = max(1, int(rng.poisson(vol["guests_per_dinein_order"] - 1) + 1))
                area = rc
            elif dining == "Take Out":
                online = rng.random() < vol["online_share_of_takeout"]
                source = "Online" if online else "In store"
                rc = "Online" if online else "Dining Room"
                server = "Online Ordering" if online else rng.choice(servers + ["Emily Chen"])
                table, guests, area = "", 1, "Counter"
            elif dining == "Delivery":
                source, rc, server, table, guests, area = "Online", "Online", "DoorDash", "", 1, "Online"
            else:  # Catering
                source, rc, server, table, guests, area = "In store", "Catering", "Daniel Okafor", "", int(rng.integers(12, 40)), "Catering"

            n_lines = max(1, int(rng.poisson(vol["lines_per_order"][dining])))
            chosen = weighted_choice(rng, skus, weights, size=n_lines)
            loyalty = rng.random() < promos[2]["prob"]
            comp_line = int(rng.integers(0, n_lines)) if rng.random() < promos[3]["prob"] else -1
            order_amount = order_disc = order_tax = 0.0
            order_promo = None
            cust = rng.choice(customers)
            tab = rng.choice(last_names)

            for li, sku in enumerate(chosen):
                m = menu_by_sku[sku]
                qty = 1
                if m["category"] in ("Beverages", "Bar") and rng.random() < 0.25:
                    qty = 2
                if dining == "Catering":
                    qty = int(rng.integers(4, 12))
                unit = price_on(sku, bd)
                gross = r2(unit * qty)
                sel_id = guid("sel", order_counter, li)
                sent = opened + timedelta(minutes=int(rng.integers(1, 12)))
                void = rng.random() < vol["void_rate"]
                # modifiers
                mod_gross_total = 0.0
                for gkey in m.get("mods", []):
                    g = groups[gkey]
                    if not g.get("required") and rng.random() > g.get("attach_prob", 0.0):
                        continue
                    n_pick = 1 if g.get("required") else int(rng.integers(1, g.get("max", 1) + 1))
                    opts = g["options"]
                    picks = weighted_choice(rng, opts, [o["w"] for o in opts], size=n_pick)
                    seen = set()
                    for mi, o in enumerate(picks):
                        if o["name"] in seen:
                            continue
                        seen.add(o["name"])
                        mg = r2(o["price"] * qty)
                        mod_gross_total += mg
                        mods.append({
                            "order_id": order_id, "order_num": order_num, "check_id": check_id,
                            "sent": sent, "opened": opened, "server": server, "table": table, "area": area,
                            "dining": dining, "selection_id": sel_id,
                            "modifier_id": mod_id[(gkey, o["name"])], "master_id": 500000 + mod_id[(gkey, o["name"])] % 1000,
                            "modifier": o["name"], "group_id": group_id[gkey], "group_name": g["name"],
                            "parent_item_id": item_id[sku], "parent_name": m["name"], "category": m["category"],
                            "gross": mg, "discount": 0.0, "net": mg, "qty": qty, "void": void,
                            "cost": r2(o["cost"] * qty), "business_date": bd,
                        })
                # discounts
                disc = 0.0
                reason = None
                item_gross_all = gross + mod_gross_total
                for p in promos:
                    if p.get("weekday") == wd and m["category"] in p.get("categories", []):
                        disc = r2(item_gross_all * p["pct"]); reason = p["name"]
                    if "hours" in p and wd in p.get("weekdays", []) and m["category"] in p["categories"]:
                        h0 = int(p["hours"][0][:2]); h1 = int(p["hours"][1][:2])
                        if h0 <= opened.hour < h1:
                            disc = r2(p["amount"] * qty); reason = p["name"]
                if loyalty and disc == 0:
                    disc = r2(item_gross_all * promos[2]["pct"]); reason = promos[2]["name"]
                if li == comp_line:
                    disc = r2(item_gross_all); reason = promos[3]["name"]
                if reason and not order_promo:
                    order_promo = reason
                # split discount between item and its modifiers proportionally (Toast allocates)
                item_disc = r2(disc * (gross / item_gross_all)) if item_gross_all else 0.0
                mod_disc = r2(disc - item_disc)
                if mod_disc and mod_gross_total:
                    # push onto the last modifier rows of this selection
                    remaining = mod_disc
                    for mrow in reversed(mods):
                        if mrow["selection_id"] != sel_id:
                            break
                        take = min(remaining, mrow["gross"])
                        mrow["discount"] = r2(take); mrow["net"] = r2(mrow["gross"] - take)
                        remaining = r2(remaining - take)
                        if remaining <= 0:
                            break
                net = r2(gross - item_disc)
                tax = r2((net + (mod_gross_total - mod_disc)) * tax_rate) if m["category"] != "Bar" else r2((net + (mod_gross_total - mod_disc)) * tax_rate)
                lines.append({
                    "order_id": order_id, "order_num": order_num, "check_id": check_id, "sent": sent, "opened": opened,
                    "server": server, "table": table, "area": area, "dining": dining, "selection_id": sel_id,
                    "item_id": item_id[sku], "master_id": master_id[sku], "sku": sku, "name": m["name"],
                    "category": m["category"], "menu_group": m["category"], "menu": "Main Menu" if source == "In store" else "Online Menu",
                    "gross": gross, "discount": item_disc, "net": net, "qty": qty, "tax": tax, "void": void,
                    "unit_price": unit, "cost": r2(m["cost"] * qty), "business_date": bd, "reason": reason,
                })
                if not void:
                    order_amount += net + (mod_gross_total - mod_disc)
                    order_disc += disc
                    order_tax += tax

            order_amount = r2(order_amount); order_tax = r2(order_tax); order_disc = r2(order_disc)
            gratuity = r2(order_amount * vol["auto_gratuity_rate"]) if (dining in ("Dine In", "Catering") and guests >= vol["auto_gratuity_party_size"]) else 0.0
            duration = int(rng.integers(8, 95)) if dining == "Dine In" else int(rng.integers(2, 25))
            paid = opened + timedelta(minutes=duration)
            closed = paid + timedelta(minutes=int(rng.integers(0, 4)))
            voided_order = order_amount == 0 and n_lines > 0 and all(l["void"] for l in lines[-n_lines:])

            # payments
            if dining == "Delivery":
                ptype, other, card = "Other", "DoorDash", ""
                tip = 0.0
            else:
                u = rng.random()
                if u < vol["gift_card_share"]:
                    ptype, other, card = "Gift Card", "", ""
                elif u < vol["gift_card_share"] + (1 - vol["card_share"]):
                    ptype, other, card = "Cash", "", ""
                else:
                    ptype, other = "Credit", ""
                    card = rng.choice(["Visa", "Mastercard", "Amex", "Discover"], p=[0.55, 0.3, 0.1, 0.05])
                tip = r2(order_amount * max(0.0, rng.normal(vol["tip_rate_card"], 0.05))) if ptype == "Credit" and gratuity == 0 else 0.0
                if ptype == "Cash" and rng.random() < 0.5:
                    tip = r2(order_amount * 0.12)
            amount = r2(order_amount + order_tax)
            total = r2(amount + tip + gratuity)
            fees = r2(total * spec["fees"]["card_processing_pct"] + spec["fees"]["card_processing_fixed"]) if ptype == "Credit" else 0.0
            refunded = (not voided_order) and ptype in ("Credit", "Cash") and rng.random() < vol["refund_rate"]
            refund_amount = r2(min(amount, (lines[-1]["net"] + lines[-1]["tax"]))) if refunded else 0.0
            refund_date = (paid + timedelta(days=1, hours=int(rng.integers(9, 18)))) if refunded else None
            payments.append({
                "payment_id": guid("pay", order_counter), "order_id": order_id, "order_num": order_num, "check_id": check_id,
                "paid": paid, "opened": opened, "server": server, "table": table, "area": area, "dining": dining,
                "amount": amount, "tip": tip, "gratuity": gratuity, "total": total, "type": ptype, "other_type": other,
                "card_type": card, "fees": fees, "refunded": refunded, "refund_date": refund_date, "refund_amount": refund_amount,
                "last4": f"{int(rng.integers(1000, 9999))}" if ptype == "Credit" else "", "business_date": bd,
                "tab": tab, "net_sales": order_amount, "tax": order_tax, "voided": voided_order,
            })
            orders.append({
                "order_id": order_id, "order_num": order_num, "check_id": check_id, "business_date": bd, "opened": opened,
                "paid": paid, "closed": closed, "duration": duration, "dining": dining, "source": source, "revenue_center": rc,
                "area": area, "server": server, "table": table, "guests": guests, "tab": tab, "customer_id": cust,
                "discount": order_disc, "amount": order_amount, "tax": order_tax, "tip": tip, "gratuity": gratuity,
                "total": total, "voided": voided_order, "promo": order_promo, "weather": wl, "event": ev["name"] if ev else None,
                "n_lines": n_lines,
            })
            if rng.random() < vol["feedback_rate"]:
                rating = float(np.clip(rng.normal(4.35, 0.5), 1, 5))
                feedback.append({"order_id": order_id, "check_id": check_id, "business_date": bd,
                                 "submitted": closed + timedelta(hours=int(rng.integers(1, 30))),
                                 "rating": round(rating * 2) / 2, "customer_id": cust})

    T.orders = pd.DataFrame(orders)
    T.lines = pd.DataFrame(lines)
    T.mods = pd.DataFrame(mods)
    T.payments = pd.DataFrame(payments)
    T.feedback = pd.DataFrame(feedback)
    T.calendar = pd.DataFrame(calendar_rows)

    _simulate_labor(T, rng)
    _simulate_purchases(T, rng)
    _simulate_fixed_and_misc(T, rng)
    _simulate_settlements(T, rng)
    return T


# --------------------------------------------------------------------------- #
def _simulate_labor(T: Truth, rng):
    spec = T.spec
    emps = spec["employees"]
    start, end = spec["business"]["period_start"], spec["business"]["period_end"]
    pr = spec["payroll"]
    closed_days = {e["date"] for e in spec["events"] if e["mult"] == 0}

    # daily card tips by business date (distributed to tipped staff on shift)
    pay = T.payments
    tips_by_day = pay.groupby("business_date")[["tip", "gratuity"]].sum().sum(axis=1).to_dict()

    entries = []
    eid = 7000000
    for d in daterange(start - timedelta(days=14), end):    # include days before period for first payroll
        if d in closed_days:
            continue
        on_shift = []
        for e in emps:
            if e["pay_type"] != "Hourly":
                continue
            days_per_week = 5 if e["hours_per_week"] >= 30 else 4
            if rng.random() > days_per_week / 7:
                continue
            hrs = e["hours_per_week"] / days_per_week + rng.normal(0, 0.7)
            hrs = float(np.clip(hrs, 3.5, 10.5))
            on_shift.append((e, hrs))
        tipped_hours = sum(h for e, h in on_shift if e.get("tipped"))
        for e, hrs in on_shift:
            eid += 1
            if e["role"] in ("Line Cook", "Prep Cook", "Dishwasher"):
                in_t = datetime.combine(d, time(int(rng.integers(9, 12)), int(rng.integers(0, 60))))
            else:
                in_t = datetime.combine(d, time(int(rng.integers(10, 16)), int(rng.integers(0, 60))))
            out_t = in_t + timedelta(hours=hrs)
            unpaid_break = 0.5 if hrs >= 6 else 0.0
            payable = r2(hrs - unpaid_break)
            tips = 0.0
            if e.get("tipped") and tipped_hours > 0:
                tips = r2(tips_by_day.get(d, 0.0) * hrs / tipped_hours)
            entries.append({
                "entry_id": eid, "employee_id": e["id"], "employee": e["name"], "role": e["role"], "wage": e["rate"],
                "in": in_t, "out": out_t, "total_hours": r2(hrs), "unpaid_break": unpaid_break, "payable_hours": payable,
                "cash_tips": r2(tips * 0.1), "non_cash_tips": r2(tips * 0.9), "business_date": d,
            })
    T.time_entries = pd.DataFrame(entries)

    # biweekly payroll
    runs = []
    pay_date = pr["first_pay_date"]
    batch_n = 0
    te = T.time_entries
    while pay_date <= end + timedelta(days=13):
        period_end = pay_date - timedelta(days=pr["period_lag_days"])
        period_start = period_end - timedelta(days=13)
        if pay_date > end:
            break
        batch_n += 1
        in_period = te[(te.business_date >= period_start) & (te.business_date <= period_end)]
        for e in emps:
            if e["pay_type"] == "Salary":
                gross = r2(e["salary"] / 26)
                ot_prem = 0.0
                reg_hours = 80.0
                ot_hours = 0.0
                tips = 0.0
            else:
                mine = in_period[in_period.employee_id == e["id"]]
                hours = float(mine.payable_hours.sum())
                # weekly overtime over 40h
                wk1 = float(mine[mine.business_date <= period_start + timedelta(days=6)].payable_hours.sum())
                wk2 = hours - wk1
                ot_hours = r2(max(0, wk1 - 40) + max(0, wk2 - 40))
                reg_hours = r2(hours - ot_hours)
                gross = r2(reg_hours * e["rate"] + ot_hours * e["rate"] * 1.5)
                ot_prem = r2(ot_hours * e["rate"] * 0.5)
                tips = r2(mine.cash_tips.sum() + mine.non_cash_tips.sum())
            bonus = float(pr["bonus"]["salaried_amount"]) if (e["pay_type"] == "Salary" and pay_date.month == pr["bonus"]["month"] and pay_date.day > 15) else 0.0
            employer_tax = r2((gross + bonus) * pr["employer_tax_rate"])
            benefits = float(e["benefits"])
            emp_tax = r2((gross + bonus + tips) * 0.165)
            deductions = r2(benefits * 0.4)
            runs.append({
                "batch": f"PP-{pay_date.strftime('%Y%m%d')}", "period_start": period_start, "period_end": period_end,
                "pay_date": pay_date, "employee_id": e["id"], "employee": e["name"], "role": e["role"],
                "pay_type": e["pay_type"], "rate": e.get("rate"), "salary": e.get("salary"),
                "regular_hours": reg_hours, "overtime_hours": ot_hours, "gross": gross, "ot_premium": ot_prem,
                "bonus": bonus, "tips": tips, "employer_tax": employer_tax, "benefits": benefits,
                "employee_tax": emp_tax, "deductions": deductions,
                "net_pay": r2(gross + bonus + tips - emp_tax - deductions),
                "total_employer_cost": r2(gross + bonus + employer_tax + benefits),
            })
        pay_date += timedelta(days=14)
    T.payroll = pd.DataFrame(runs)


# --------------------------------------------------------------------------- #
def _simulate_purchases(T: Truth, rng):
    spec = T.spec
    start, end = spec["business"]["period_start"], spec["business"]["period_end"]
    ln = T.lines[~T.lines.void]
    md = T.mods[~T.mods.void]
    net_by_month = ln.groupby(ln.business_date.map(lambda d: (d.year, d.month)))["net"].sum() \
        + md.groupby(md.business_date.map(lambda d: (d.year, d.month)))["net"].sum()
    target_pct = spec["cogs_target_pct"]
    rows = []
    inv_n = 1000
    for v in spec["vendors"]:
        deliveries = []
        week_i = 0
        for d in daterange(start, end):
            if d.weekday() == 0:
                week_i += 1
            if d.weekday() in v["cadence_days"] and (week_i % v.get("every_n_weeks", 1) == 0):
                deliveries.append(d)
        by_month = {}
        for d in deliveries:
            by_month.setdefault((d.year, d.month), []).append(d)
        for ym, ds in by_month.items():
            month_target = float(net_by_month.get(ym, 0.0)) * target_pct * v["share"]
            per_delivery = month_target / len(ds)
            for d in ds:
                inv_n += 1
                inv_id = f"INV-{d.strftime('%Y%m%d')}-{inv_n}" if v["name"] != "Sysco Kansas City" else f"SY-{inv_n:08d}"
                budget = per_delivery * float(rng.uniform(0.82, 1.18))
                n_items = int(rng.integers(3, min(8, len(v["items"])) + 1))
                picks = rng.choice(len(v["items"]), size=n_items, replace=False)
                picked = [v["items"][i] for i in picks]
                # allocate budget across picked items
                shares = rng.dirichlet(np.ones(n_items) * 2.0)
                paid = d + timedelta(days=v["terms"] + int(rng.integers(-2, 4)))
                paid = max(paid, d)
                status = "Paid" if paid <= end else "Open"
                for it, s in zip(picked, shares):
                    unit_cost = r2(it["cost"] * float(rng.uniform(0.97, 1.06)))
                    qty = max(1, int(round(budget * s / unit_cost)))
                    rows.append({
                        "invoice_id": inv_id, "invoice_date": d, "due_date": d + timedelta(days=v["terms"]),
                        "paid_date": paid if status == "Paid" else None, "vendor": v["name"],
                        "category": v["category"], "description": it["d"], "qty": qty, "unit": it["unit"],
                        "unit_cost": unit_cost, "line_total": r2(qty * unit_cost), "terms": v["terms"], "status": status,
                        "notes": f"Scheduled delivery - {v['name']}",
                    })
    T.bills = pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
def _simulate_fixed_and_misc(T: Truth, rng):
    spec = T.spec
    start, end = spec["business"]["period_start"], spec["business"]["period_end"]
    rows = []
    ref = 7000
    m = date(start.year, start.month, 1)
    while m <= end:
        for b in spec["fixed_bills"]:
            ref += 1
            amt = b["amount"] * b.get("seasonal", {}).get(m.month, 1.0)
            amt = r2(amt * float(rng.uniform(0.98, 1.03))) if b["category"] not in ("Rent", "CAM / Common Area Maintenance", "Equipment Lease", "Business Insurance", "POS / Software Subscription", "Accounting Software", "Music Subscription") else float(b["amount"])
            inv = date(m.year, m.month, min(b["day"], 28))
            paid = inv + timedelta(days=b["terms"] + int(rng.integers(0, 3)))
            rows.append({"reference": f"BILL-{m.strftime('%Y%m')}-{ref}", "invoice_date": inv, "paid_date": paid,
                         "dept": b["dept"], "category": b["category"], "vendor": b["vendor"], "amount": amt,
                         "status": "Paid" if paid <= end else "Scheduled", "notes": "Recurring monthly bill"})
        m = date(m.year + (m.month // 12), (m.month % 12) + 1, 1)
    T.fixed_bills = pd.DataFrame(rows)

    misc = []
    n = 9000
    for e in spec["misc_expenses"]:
        for _ in range(e["per_year"]):
            n += 1
            d = start + timedelta(days=int(rng.integers(0, (end - start).days + 1)))
            misc.append({"reference": f"EXP-{n}", "date": d, "dept": e["dept"], "category": e["category"],
                         "vendor": e["vendor"], "amount": r2(float(rng.uniform(*e["amount"]))),
                         "payment_method": rng.choice(["Business Card", "Bank ACH", "Check"], p=[0.6, 0.25, 0.15]),
                         "notes": e["category"]})
    T.misc_expenses = pd.DataFrame(misc).sort_values("date").reset_index(drop=True)

    # monthly sales tax remittance for prior month
    pay = T.payments[~T.payments.voided]
    tax_by_month = pay.groupby(pay.business_date.map(lambda d: (d.year, d.month)))["tax"].sum()
    rem = []
    for (y, mo), t in tax_by_month.items():
        due_m = date(y + (mo // 12), (mo % 12) + 1, spec["sales_tax_remittance_day"])
        if due_m > end + timedelta(days=25):
            continue
        rem.append({"period": f"{y}-{mo:02d}", "paid_date": due_m, "amount": r2(t),
                    "vendor": "Missouri Department of Revenue", "reference": f"MO-ST-{y}{mo:02d}",
                    "status": "Paid" if due_m <= end else "Scheduled"})
    T.sales_tax = pd.DataFrame(rem)


# --------------------------------------------------------------------------- #
def _simulate_settlements(T: Truth, rng):
    spec = T.spec
    fees = spec["fees"]
    pay = T.payments
    card = pay[pay.type == "Credit"]
    g = card.groupby("business_date").agg(card_sales=("amount", "sum"), tips=("tip", "sum"), gratuity=("gratuity", "sum"),
                                          fees=("fees", "sum"), refunds=("refund_amount", "sum"), txns=("payment_id", "count")).reset_index()
    g["payout_date"] = g.business_date.map(lambda d: d + timedelta(days=fees["card_settlement_lag_days"]))
    g["net_deposit"] = (g.card_sales + g.tips + g.gratuity - g.fees - g.refunds).round(2)
    g["payout_id"] = [f"TP-{d.strftime('%Y%m%d')}-{int(rng.integers(100, 999))}" for d in g.business_date]
    T.card_payouts = g

    dd = pay[pay.other_type == "DoorDash"]
    wk = dd.business_date.map(lambda d: d + timedelta(days=(fees["doordash_payout_weekday"] - d.weekday()) % 7 or 7))
    dd = dd.assign(payout_date=wk)
    p = dd.groupby("payout_date").agg(orders=("payment_id", "count"), subtotal=("net_sales", "sum"), tax=("tax", "sum"),
                                      period_start=("business_date", "min"), period_end=("business_date", "max")).reset_index()
    p["commission"] = (p.subtotal * fees["doordash_commission_pct"]).round(2)
    p["marketing"] = (p.subtotal * fees["doordash_marketing_pct"]).round(2)
    p["error_charges"] = [r2(float(rng.uniform(0, 18))) if rng.random() < 0.6 else 0.0 for _ in range(len(p))]
    p["net_payout"] = (p.subtotal + p.tax - p.commission - p.marketing - p.error_charges).round(2)
    p["payout_id"] = [f"po_{guid('dd', d)[:12]}" for d in p.payout_date]
    T.dd_payouts = p
