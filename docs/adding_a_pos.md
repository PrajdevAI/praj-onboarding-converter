# Adding a new POS or accounting system

No Python. One YAML block per export file type, in `engine/mappings/<system>.yaml`.

```yaml
source_system: newpos
layout_status: reconstructed          # or documented
documents:
  - document_type: sales_items
    entity: sales_line                # canonical entity this file feeds
    detect:
      filename: "^NewPOS_Items_.*\\.csv$"       # optional
      required_headers: ["Ticket ID", "Item", "Qty", "Net"]   # all must be present
    filters:                          # optional, applied to the raw file first
      - {column: "Row Type", equals: "ITEM"}
    columns:                          # canonical: "Source Header"
      order_id: "Ticket ID"
      check_id: "Ticket ID"
      line_id: "Line ID"
      sent_at: "Time"
      sku: "SKU"
      item_name: "Item"
      category: "Category"
      quantity: "Qty"
      gross: "Gross"
      discount: "Discount"
      net: "Net"
      tax: "Tax"
      void: "Voided"
      dining_option: "Service"
      modifiers_text: "Modifiers"
    value_maps:                       # source value -> canonical value
      dining_option: {"EatIn": "Dine In", "ToGo": "Take Out"}
    constants: {}                     # fixed values, e.g. void: false
    types: {sent_at: datetime_iso, quantity: number, gross: money, discount: money, net: money, tax: money, void: bool}
    rules:
      - copy: {from: sent_at, to: opened_at}
      - business_date: {from: opened_at, closeout: "04:00"}
      - split_modifiers: {from: modifiers_text, delimiter: "; ", price_pattern: "\\(\\+([0-9]+\\.[0-9]{2})\\)"}
```

## Canonical entities and the fields the builder uses

| Entity | Required fields | Optional |
|---|---|---|
| sales_line | order_id, check_id, line_id, sent_at, opened_at, business_date, sku, item_name, category, quantity, gross, discount, net, tax, void | dining_option (else inherited from order_header), server |
| sales_line_modifier | parent_line_id, modifier, gross, discount, net, quantity, void, business_date | modifier_group, included_in_parent (true when the parent line already includes the modifier price) |
| order_header | order_id, opened_at, business_date, dining_option, net_sales, voided | check_id, customer_id, discount_reason, guests, tip, gratuity, total |
| payment | payment_id, order_id, paid_at, opened_at, business_date, tender_type (Credit/Cash/Gift Card/Other), amount (incl. tax, excl. tip) | dining_option, tip, gratuity, fees, status, refund_amount, refund_at, other_type |
| refund | order_id, amount, refund_at | reason |
| check_detail | check_id, customer_id, discount_reason | |
| settlement_card | business_date, payout_date, payout_id, card_sales, fees, net_deposit | tips, refunds |
| settlement_platform | payout_date, period_start, period_end, subtotal, tax, commission, marketing, amendments, net_payout | |
| purchase_line | invoice_id, invoice_date, vendor, description, quantity, unit, unit_cost, line_total, account, terms_days | due_date |
| accounting_txn | txn_date, txn_type, reference, payee, account, split, amount | memo, payment_method, qbo_class |
| payroll_line | batch, period_start, period_end, pay_date, employee_id, employee, role, pay_type, gross_earnings, bonus, overtime_earnings, employer_tax, employer_contributions, total_employer_cost | hourly_rate, annual_salary, tips, regular_hours, overtime_hours |
| employee | employee_id, first_name, last_name, role, pay_type | hourly_rate, annual_salary, benefits_per_period |
| item_cost, modifier_cost | sku / modifier, unit_cost | |
| feedback, event, weather | see other_sources.yaml | |

## Types
`money` ($1,234.56, -$7.31, (7.31)), `number`, `bool` (true/yes/1/voided), `datetime_toast` (10/3/25 12:25 PM),
`datetime_iso` (2025-10-03 12:16:05 or with offset), `datetime_dmy` (03/10/2025 12:16), `datetime_epoch_ms`, `date_us`, `date_iso`.

## Rules
`business_date`, `combine_datetime`, `expression` (pandas eval on canonical columns), `copy`, `shift_hours`, `synthetic_id`,
`tender_from_columns`, `split_modifiers`, `regex_extract`.

## Check it
1. Drop a real export in a folder and run `python engine/convert.py --input <folder> --input raw_uploads/shared --out out`.
2. `out/ingest_batches.csv` shows what each file classified as and any source header the YAML names that the file lacks.
3. `out/reconciliation.md` must say ACCEPTED.
