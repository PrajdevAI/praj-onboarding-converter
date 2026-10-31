"""
Write the Golden Standard workbook in the Tony Pizza layout:
README, Products, Employees, Daily_Sales, Sales_Item_Detail, Cash_In_Detail,
Raw_Material_Purchases, Payroll_Detail, Bills_Fixed_Monthly, Cash_Out_Detail,
Monthly_Summary, Weekly_Summary, Product_Summary, Dashboard.

Summary sheets use the same formulas as Tony Pizza, but with whole-column
ranges so the row count of a tenant's data never breaks the totals.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="C2410C")
HEADER_FONT = Font(bold=True, color="FFFFFF")
MONEY = '$#,##0.00'
INT = '#,##0'
PCT = '0.0%'
DATE = 'yyyy-mm-dd'
DT = 'yyyy-mm-dd hh:mm'

FORMATS = {
    "Products": {"List Price": MONEY, "Unit COGS": MONEY, "Base Daily Units": INT, "Gross Margin %": PCT},
    "Employees": {"Hourly Rate": MONEY, "Annual Salary": MONEY, "Benefits / Period": MONEY, "Avg Biweekly Hours": '0.0'},
    "Daily_Sales": {"Date": DATE, "Week Start": DATE, "Month Start": DATE, "Orders": INT, **{c: MONEY for c in [
        "Gross Sales", "Discounts", "Net Sales", "Sales Tax", "Refunds", "Total Collected", "COGS", "Gross Profit", "Avg Ticket",
        "Dine-In Net Sales", "Takeout Net Sales", "Delivery App Net Sales", "Catering Net Sales"]}},
    "Sales_Item_Detail": {"Date": DT, "Qty Sold": INT, **{c: MONEY for c in ["Unit Price", "Gross Sales", "Discounts", "Net Sales", "Sales Tax", "COGS", "Gross Profit"]}, "Rating": '0.0'},
    "Cash_In_Detail": {"Transaction Date": DATE, "Settlement Date": DATE, **{c: MONEY for c in ["Gross Sales Basis", "Discounts", "Sales Tax", "Deductions", "Net Cash Received"]}},
    "Raw_Material_Purchases": {"Invoice Date": DATE, "Paid Date": DATE, "Qty": INT, "Unit Cost": MONEY, "Line Total": MONEY, "Payment Terms (Days)": INT},
    "Payroll_Detail": {"Period Start": DATE, "Period End": DATE, "Pay Date": DATE, **{c: MONEY for c in [
        "Hourly Rate", "Annual Salary", "Gross Pay", "Overtime Premium", "Bonus", "Employer Payroll Tax", "Benefits", "Total Cash Out"]}},
    "Bills_Fixed_Monthly": {"Invoice Date": DATE, "Paid Date": DATE, "Amount": MONEY},
    "Cash_Out_Detail": {"Paid Date": DATE, "Amount": MONEY},
}

SHEET_ORDER = ["README", "Products", "Employees", "Daily_Sales", "Sales_Item_Detail", "Cash_In_Detail", "Raw_Material_Purchases",
               "Payroll_Detail", "Bills_Fixed_Monthly", "Cash_Out_Detail", "Monthly_Summary", "Weekly_Summary", "Product_Summary", "Dashboard"]


def _style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = 14
    ws.freeze_panes = "A2"


def write_data_sheet(wb: Workbook, name: str, df: pd.DataFrame):
    ws = wb.create_sheet(name)
    cols = list(df.columns)
    ws.append(cols)
    fmt = FORMATS.get(name, {})
    for row in df.itertuples(index=False):
        vals = []
        for v in row:
            if isinstance(v, pd.Timestamp):
                v = v.to_pydatetime()
            elif v is pd.NaT or (isinstance(v, float) and pd.isna(v)):
                v = None
            vals.append(v)
        ws.append(vals)
    _style_header(ws, len(cols))
    for j, c in enumerate(cols, start=1):
        if c in fmt:
            for r in range(2, len(df) + 2):
                ws.cell(row=r, column=j).number_format = fmt[c]
    return ws


def write_products_formulas(ws, n):
    for r in range(2, n + 2):
        ws.cell(row=r, column=7, value=f"=IFERROR((D{r}-E{r})/D{r},0)").number_format = PCT


def write_monthly_summary(wb: Workbook, start: date, end: date):
    ws = wb.create_sheet("Monthly_Summary")
    cols = ["Month Start", "Month End", "Month Label", "Orders", "Gross Sales", "Discounts", "Net Sales", "Sales Tax", "COGS (Accrued)", "Gross Profit",
            "Cash In", "Total Cash Out", "Net Cash Flow", "Payroll", "Occupancy", "Utilities", "Other Opex", "Avg Ticket",
            "Dine-In Sales", "Takeout Sales", "Delivery App Sales", "Catering Sales"]
    ws.append(cols)
    m = date(start.year, start.month, 1)
    r = 2
    while m <= end:
        nxt = date(m.year + (m.month // 12), (m.month % 12) + 1, 1)
        ds = lambda col: f'SUMIFS(Daily_Sales!${col}:${col},Daily_Sales!$A:$A,">="&A{r},Daily_Sales!$A:$A,"<"&B{r})'
        co = lambda extra="": f'SUMIFS(Cash_Out_Detail!$I:$I,Cash_Out_Detail!$A:$A,">="&A{r},Cash_Out_Detail!$A:$A,"<"&B{r}{extra})'
        row = [m, nxt, m.strftime("%b %Y"), "=" + ds("J"), "=" + ds("K"), "=" + ds("L"), "=" + ds("M"), "=" + ds("N"), "=" + ds("Q"), f"=G{r}-I{r}",
               f'=SUMIFS(Cash_In_Detail!$I:$I,Cash_In_Detail!$B:$B,">="&A{r},Cash_In_Detail!$B:$B,"<"&B{r})', "=" + co(), f"=K{r}-L{r}",
               "=" + co(',Cash_Out_Detail!$C:$C,"Payroll"'), "=" + co(',Cash_Out_Detail!$C:$C,"Occupancy"'), "=" + co(',Cash_Out_Detail!$C:$C,"Utilities"'),
               f"=L{r}-N{r}-O{r}-P{r}", f"=IFERROR(G{r}/D{r},0)", "=" + ds("T"), "=" + ds("U"), "=" + ds("V"), "=" + ds("W")]
        ws.append(row)
        r += 1
        m = nxt
    _style_header(ws, len(cols))
    for rr in range(2, r):
        ws.cell(row=rr, column=1).number_format = DATE
        ws.cell(row=rr, column=2).number_format = DATE
        ws.cell(row=rr, column=4).number_format = INT
        for c in list(range(5, 18)) + list(range(19, 23)):
            ws.cell(row=rr, column=c).number_format = MONEY
    return r - 2


def write_weekly_summary(wb: Workbook, start: date, end: date):
    ws = wb.create_sheet("Weekly_Summary")
    cols = ["Week Start", "Week End", "Week Label", "Orders", "Net Sales", "Cash In", "Cash Out", "Net Cash Flow", "Gross Profit", "Avg Ticket",
            "Dine-In", "Takeout", "Delivery App", "Catering"]
    ws.append(cols)
    w = start - timedelta(days=start.weekday())
    r = 2
    while w <= end:
        nxt = w + timedelta(days=7)
        ds = lambda col: f'SUMIFS(Daily_Sales!${col}:${col},Daily_Sales!$A:$A,">="&A{r},Daily_Sales!$A:$A,"<"&B{r})'
        iso = w.isocalendar()
        ws.append([w, nxt, f"W{iso.week:02d} {iso.year}", "=" + ds("J"), "=" + ds("M"),
                   f'=SUMIFS(Cash_In_Detail!$I:$I,Cash_In_Detail!$B:$B,">="&A{r},Cash_In_Detail!$B:$B,"<"&B{r})',
                   f'=SUMIFS(Cash_Out_Detail!$I:$I,Cash_Out_Detail!$A:$A,">="&A{r},Cash_Out_Detail!$A:$A,"<"&B{r})',
                   f"=F{r}-G{r}", "=" + ds("R"), f"=IFERROR(E{r}/D{r},0)", "=" + ds("T"), "=" + ds("U"), "=" + ds("V"), "=" + ds("W")])
        r += 1
        w = nxt
    _style_header(ws, len(cols))
    for rr in range(2, r):
        ws.cell(row=rr, column=1).number_format = DATE
        ws.cell(row=rr, column=2).number_format = DATE
        ws.cell(row=rr, column=4).number_format = INT
        for c in range(5, 15):
            ws.cell(row=rr, column=c).number_format = MONEY


def write_product_summary(wb: Workbook, products: pd.DataFrame):
    ws = wb.create_sheet("Product_Summary")
    cols = ["SKU", "Product", "Category", "Qty Sold", "Net Sales", "COGS", "Gross Profit", "Gross Margin %"]
    ws.append(cols)
    for i, p in enumerate(products.itertuples(index=False), start=2):
        ws.append([p.SKU, p.Product, p.Category,
                   f'=SUMIFS(Sales_Item_Detail!$I:$I,Sales_Item_Detail!$C:$C,A{i})',
                   f'=SUMIFS(Sales_Item_Detail!$M:$M,Sales_Item_Detail!$C:$C,A{i})',
                   f'=SUMIFS(Sales_Item_Detail!$O:$O,Sales_Item_Detail!$C:$C,A{i})',
                   f"=E{i}-F{i}", f"=IFERROR(G{i}/E{i},0)"])
    _style_header(ws, len(cols))
    n = len(products)
    for rr in range(2, n + 2):
        ws.cell(row=rr, column=4).number_format = INT
        for c in (5, 6, 7):
            ws.cell(row=rr, column=c).number_format = MONEY
        ws.cell(row=rr, column=8).number_format = PCT
    return n


def write_dashboard(wb: Workbook, business: str, n_months: int, n_products: int, top_products: pd.DataFrame):
    ws = wb.create_sheet("Dashboard")
    ws["A1"] = f"{business} Dashboard Dataset"
    ws["A1"].font = Font(bold=True, size=16)
    ws.merge_cells("A1:N1")
    ws["A2"] = "12 full months of operating data converted to the Golden Standard"
    ws.merge_cells("A2:N2")
    last = n_months + 1
    kpis = [("B4", "Annual Cash In", "B5", f"=SUM(Monthly_Summary!K2:K{last})"),
            ("F4", "Annual Cash Out", "F5", f"=SUM(Monthly_Summary!L2:L{last})"),
            ("J4", "Net Cash Flow", "J5", f"=SUM(Monthly_Summary!M2:M{last})"),
            ("B7", "Net Sales", "B8", f"=SUM(Monthly_Summary!G2:G{last})"),
            ("F7", "Gross Profit", "F8", f"=SUM(Monthly_Summary!J2:J{last})"),
            ("J7", "Avg Daily Orders", "J8", f"=AVERAGE(Monthly_Summary!D2:D{last})/30.4")]
    for lab_cell, lab, val_cell, f in kpis:
        ws[lab_cell] = lab
        ws[lab_cell].font = Font(bold=True)
        ws[val_cell] = f
        ws[val_cell].font = Font(bold=True, size=14)
        ws[val_cell].number_format = MONEY if "Orders" not in lab else '#,##0.0'
        col = lab_cell[0]
        c2 = chr(ord(col) + 2)
        ws.merge_cells(f"{lab_cell}:{c2}{lab_cell[1:]}")
        ws.merge_cells(f"{val_cell}:{c2}{int(val_cell[1:]) + 1}")
    # monthly tables (live formulas into Monthly_Summary)
    ws["A12"], ws["B12"], ws["C12"], ws["D12"] = "Month", "Cash In", "Cash Out", "Net Cash Flow"
    ws["F12"], ws["G12"], ws["H12"], ws["I12"], ws["J12"] = "Month", "Dine-In", "Takeout", "Delivery App", "Catering"
    for c in ["A12", "B12", "C12", "D12", "F12", "G12", "H12", "I12", "J12"]:
        ws[c].font = Font(bold=True)
    for i in range(n_months):
        r = 13 + i
        src = 2 + i
        ws[f"A{r}"] = f"=Monthly_Summary!C{src}"
        ws[f"B{r}"] = f"=Monthly_Summary!K{src}"
        ws[f"C{r}"] = f"=Monthly_Summary!L{src}"
        ws[f"D{r}"] = f"=Monthly_Summary!M{src}"
        ws[f"F{r}"] = f"=Monthly_Summary!C{src}"
        ws[f"G{r}"] = f"=Monthly_Summary!S{src}"
        ws[f"H{r}"] = f"=Monthly_Summary!T{src}"
        ws[f"I{r}"] = f"=Monthly_Summary!U{src}"
        ws[f"J{r}"] = f"=Monthly_Summary!V{src}"
        for c in "BCDGHIJ":
            ws[f"{c}{r}"].number_format = MONEY
    # top products (values) + quick insights
    ws["A27"], ws["B27"] = "Product", "Net Sales"
    ws["A27"].font = ws["B27"].font = Font(bold=True)
    for i, p in enumerate(top_products.itertuples(index=False)):
        ws[f"A{28 + i}"] = p.Product
        ws[f"B{28 + i}"] = float(p.net)
        ws[f"B{28 + i}"].number_format = MONEY
    ws["F27"] = "Quick Insight Formulas"
    ws["F27"].font = Font(bold=True)
    ws.merge_cells("F27:J27")
    ws["F28"], ws["G28"] = "Best Cash Month", f"=INDEX(Monthly_Summary!$C$2:$C${last},MATCH(MAX(Monthly_Summary!$M$2:$M${last}),Monthly_Summary!$M$2:$M${last},0))"
    ws["F29"], ws["G29"] = "Worst Cash Month", f"=INDEX(Monthly_Summary!$C$2:$C${last},MATCH(MIN(Monthly_Summary!$M$2:$M${last}),Monthly_Summary!$M$2:$M${last},0))"
    pl = n_products + 1
    ws["F30"], ws["G30"] = "Top Product", f"=INDEX(Product_Summary!$B$2:$B${pl},MATCH(MAX(Product_Summary!$E$2:$E${pl}),Product_Summary!$E$2:$E${pl},0))"
    ws["F31"], ws["G31"] = "Top Expense Dept", "Payroll"
    for c in ["A", "B", "C", "D", "F", "G", "H", "I", "J"]:
        ws.column_dimensions[c].width = 16
    # charts
    ch1 = BarChart(); ch1.title = "Cash In vs Cash Out by Month"; ch1.height, ch1.width = 7.5, 16
    ch1.add_data(Reference(ws, min_col=2, min_row=12, max_col=3, max_row=12 + n_months), titles_from_data=True)
    ch1.set_categories(Reference(ws, min_col=1, min_row=13, max_row=12 + n_months))
    ws.add_chart(ch1, "L4")
    ch2 = LineChart(); ch2.title = "Net Sales by Channel"; ch2.height, ch2.width = 7.5, 16
    ch2.add_data(Reference(ws, min_col=7, min_row=12, max_col=10, max_row=12 + n_months), titles_from_data=True)
    ch2.set_categories(Reference(ws, min_col=6, min_row=13, max_row=12 + n_months))
    ws.add_chart(ch2, "L20")
    ch3 = BarChart(); ch3.type = "bar"; ch3.title = "Top 10 Products by Net Sales"; ch3.height, ch3.width = 7.5, 16
    ch3.add_data(Reference(ws, min_col=2, min_row=27, max_row=27 + len(top_products)), titles_from_data=True)
    ch3.set_categories(Reference(ws, min_col=1, min_row=28, max_row=27 + len(top_products)))
    ws.add_chart(ch3, "L36")


def write_readme(wb: Workbook, business: str, start: date, end: date, n_days: int, sources: list[str], notes: list[str]):
    ws = wb.create_sheet("README")
    ws["A1"] = f"{business} - Golden Standard Operating Dataset (12 Full Months)"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:B1")
    rows = [
        ("Purpose", "Customer POS and accounting exports converted to the Praj.AI Golden Standard (Tony Pizza layout) by the onboarding engine."),
        ("Coverage", f"{start.isoformat()} to {end.isoformat()} ({n_days} daily operating records)."),
        ("Sheets", "Dashboard, Monthly_Summary, Weekly_Summary, Product_Summary, Daily_Sales, Sales_Item_Detail, Cash_In_Detail, Cash_Out_Detail, Raw_Material_Purchases, Payroll_Detail, Bills_Fixed_Monthly, Products, Employees."),
        ("Source systems", "; ".join(sources)),
        ("Key notes", "Delivery app fees and merchant fees are included as expense rows. Sales tax is collected in Daily_Sales and remitted later through Cash_Out_Detail. Tips are a pass-through and are excluded from Cash In and Payroll gross."),
        ("Suggested dashboard cuts", "Revenue by channel, sales by weekday, top products, cash in vs cash out, expense mix, payroll trend, raw material spend trend, gross margin, average ticket, promotion/event impact."),
        ("Conversion notes", " | ".join(notes)),
        ("Data caveat", "Synthetic demo customer (Harbor Grill) generated to exercise the onboarding engine; not real accounting data."),
    ]
    for i, (k, v) in enumerate(rows, start=3):
        ws[f"A{i}"] = k
        ws[f"A{i}"].font = Font(bold=True)
        ws[f"B{i}"] = v
        ws[f"B{i}"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 120


def write_golden(path: str, sheets: dict, business: str, start: date, end: date, sources: list[str], notes: list[str]):
    wb = Workbook()
    wb.remove(wb.active)
    write_readme(wb, business, start, end, len(sheets["Daily_Sales"]), sources, notes)
    for name in ["Products", "Employees", "Daily_Sales", "Sales_Item_Detail", "Cash_In_Detail", "Raw_Material_Purchases",
                 "Payroll_Detail", "Bills_Fixed_Monthly", "Cash_Out_Detail"]:
        ws = write_data_sheet(wb, name, sheets[name])
        if name == "Products":
            write_products_formulas(ws, len(sheets[name]))
    n_months = write_monthly_summary(wb, start, end)
    write_weekly_summary(wb, start, end)
    n_products = write_product_summary(wb, sheets["Products"])
    sid = sheets["Sales_Item_Detail"]
    top = sid.groupby("Product", as_index=False)["Net Sales"].sum().rename(columns={"Net Sales": "net"}).sort_values("net", ascending=False).head(10)
    write_dashboard(wb, business, n_months, n_products, top)
    wb._sheets = [wb[n] for n in SHEET_ORDER]
    wb.save(path)
