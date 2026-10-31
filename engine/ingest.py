"""
Ingest: walk upload folders, classify every file by its headers against the
provider mapping packs, apply the column map, value maps, type coercions and
structural rules, and return one canonical DataFrame per entity.

This is the generic part of the engine.  Nothing in here knows what Toast,
Square or QuickBooks is; that knowledge lives in engine/mappings/*.yaml.

Mapping document keys:
  detect:      {filename: regex, required_headers: [...]}
  read:        {format: csv|xlsx, sheet: name}
  filters:     [{column, equals|not_equals}]          applied to the raw file first
  columns:     {canonical: "Source Header"}
  value_maps:  {canonical: {"source value": "canonical value"}}
  types:       {canonical: money|number|bool|datetime_toast|datetime_iso|datetime_dmy|datetime_epoch_ms|date_us|date_iso}
  rules:       ordered list of {rule_name: params}; see RULES
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# mapping registry
# --------------------------------------------------------------------------- #
@dataclass
class DocMapping:
    source_system: str
    document_type: str
    entity: str
    detect: dict
    columns: dict
    types: dict = field(default_factory=dict)
    rules: list = field(default_factory=list)
    filters: list = field(default_factory=list)
    read: dict = field(default_factory=dict)
    value_maps: dict = field(default_factory=dict)
    constants: dict = field(default_factory=dict)
    layout_status: str = "documented"

    @property
    def key(self):
        return f"{self.source_system}.{self.document_type}"


def load_mappings(folder: str | None = None) -> list[DocMapping]:
    folder = folder or os.path.join(HERE, "mappings")
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "*.yaml"))):
        with open(path, "r", encoding="utf-8") as f:
            pack = yaml.safe_load(f)
        for d in pack["documents"]:
            out.append(DocMapping(
                source_system=d.get("source_system", pack.get("source_system")),
                document_type=d["document_type"], entity=d["entity"], detect=d.get("detect", {}),
                columns=d.get("columns", {}), types=d.get("types", {}), rules=d.get("rules", []),
                filters=d.get("filters", []), read=d.get("read", {}), value_maps=d.get("value_maps", {}),
                constants=d.get("constants", {}), layout_status=d.get("layout_status", pack.get("layout_status", "documented")),
            ))
    return out


# --------------------------------------------------------------------------- #
# readers
# --------------------------------------------------------------------------- #
def read_headers(path: str) -> list[str]:
    if path.lower().endswith((".xlsx", ".xlsm")):
        return [str(c) for c in pd.read_excel(path, nrows=0).columns]
    if path.lower().endswith(".csv"):
        return [str(c) for c in pd.read_csv(path, nrows=0, dtype=str).columns]
    return []


_table_cache: dict[str, pd.DataFrame] = {}


def read_table(path: str, mapping: DocMapping) -> pd.DataFrame:
    key = f"{path}|{mapping.read.get('sheet', 0)}"
    if key in _table_cache:
        return _table_cache[key]
    if path.lower().endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(path, sheet_name=mapping.read.get("sheet", 0), dtype=str)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    _table_cache.clear()
    _table_cache[key] = df
    return df


# --------------------------------------------------------------------------- #
# classification by fingerprint (a file may feed more than one entity)
# --------------------------------------------------------------------------- #
def classify(path: str, mappings: list[DocMapping]) -> list[DocMapping]:
    """Deterministic fingerprint: filename pattern (if any) + all required headers present."""
    name = os.path.basename(path)
    try:
        headers = set(read_headers(path))
    except Exception:
        return []
    matches = []
    for m in mappings:
        det = m.detect
        if "filename" in det and not re.search(det["filename"], name):
            continue
        req = set(det.get("required_headers", []))
        if not req or not req.issubset(headers):
            continue
        matches.append(m)
    return matches


# --------------------------------------------------------------------------- #
# type coercions
# --------------------------------------------------------------------------- #
def _clean(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().replace({"": None, "nan": None, "None": None, "NaT": None})


def _money(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip()
    neg = t.str.startswith("(") & t.str.endswith(")") | t.str.startswith("-")
    t = t.str.replace(r"[\$,()\-]", "", regex=True).str.strip()
    v = pd.to_numeric(t.replace({"": None, "nan": None, "None": None}), errors="coerce")
    v = v.where(~neg, -v)
    return v.astype(float).round(2)


def _number(s: pd.Series) -> pd.Series:
    return pd.to_numeric(_clean(s), errors="coerce")


def _bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(["true", "yes", "y", "1", "t", "voided", "void"])


def _dt_toast(s: pd.Series) -> pd.Series:          # 10/3/25 12:25 PM
    return pd.to_datetime(_clean(s), format="%m/%d/%y %I:%M %p", errors="coerce")


def _dt_iso(s: pd.Series) -> pd.Series:            # 2025-10-03 12:16:05 or 2025-10-03T12:16:05-05:00
    v = pd.to_datetime(_clean(s), errors="coerce", utc=False, format="ISO8601")
    try:
        if getattr(v.dt, "tz", None) is not None:
            v = v.dt.tz_localize(None)
    except Exception:
        pass
    if v.dtype == object:  # mixed tz -> normalise each
        v = pd.to_datetime([x.tz_localize(None) if hasattr(x, "tz_localize") and x.tzinfo else x for x in v], errors="coerce")
    return v


def _dt_dmy(s: pd.Series) -> pd.Series:            # 03/10/2025 12:16  (day first)
    return pd.to_datetime(_clean(s), format="%d/%m/%Y %H:%M", errors="coerce")


def _dt_epoch_ms(s: pd.Series) -> pd.Series:       # 1759511765000 (UTC) -> naive local via rule shift if needed
    return pd.to_datetime(pd.to_numeric(_clean(s), errors="coerce"), unit="ms", errors="coerce")


def _date_us(s: pd.Series) -> pd.Series:
    return pd.to_datetime(_clean(s), format="%m/%d/%Y", errors="coerce").dt.date


def _date_iso(s: pd.Series) -> pd.Series:
    return pd.to_datetime(_clean(s), errors="coerce").dt.date


COERCE = {"money": _money, "number": _number, "bool": _bool, "datetime_toast": _dt_toast, "datetime_iso": _dt_iso,
          "datetime_dmy": _dt_dmy, "datetime_epoch_ms": _dt_epoch_ms, "date_us": _date_us, "date_iso": _date_iso}


# --------------------------------------------------------------------------- #
# rules (structural transforms parameterised from YAML)
# --------------------------------------------------------------------------- #
def rule_business_date(df, p):
    """Timestamps before the closeout hour belong to the previous business day."""
    hh, mm = map(int, p["closeout"].split(":"))
    ts = pd.to_datetime(df[p["from"]])
    df["business_date"] = (ts - pd.Timedelta(hours=hh, minutes=mm)).dt.date
    return df


def rule_regex_extract(df, p):
    ext = df[p["from"]].astype(str).str.extract(p["pattern"])[0]
    df[p["to"]] = pd.to_numeric(ext, errors="coerce") if p.get("as") == "number" else ext
    return df


def rule_combine_datetime(df, p):
    """Date and time in separate columns (Square) -> one timestamp."""
    txt = df[p["date"]].astype(str).str.strip() + " " + df[p["time"]].astype(str).str.strip()
    df[p["to"]] = pd.to_datetime(txt, format=p.get("format", "%Y-%m-%d %H:%M:%S"), errors="coerce")
    return df


def rule_expression(df, p):
    """Arithmetic between canonical columns, e.g. amount = net_sales + tax."""
    df[p["to"]] = df.eval(p["expr"]).astype(float).round(2)
    return df


def rule_copy(df, p):
    df[p["to"]] = df[p["from"]]
    return df


def rule_shift_hours(df, p):
    """UTC epoch timestamps -> store local time (Clover API style)."""
    df[p["column"]] = pd.to_datetime(df[p["column"]]) + pd.Timedelta(hours=float(p["hours"]))
    return df


def rule_synthetic_id(df, p):
    """Line id for exports that have none: <parent id>-<running number within parent>."""
    parent = df[p["from"]].astype(str)
    seq = df.groupby(parent).cumcount() + 1
    df[p["to"]] = parent + "-" + seq.astype(str)
    return df


def rule_tender_from_columns(df, p):
    """Square-style tender columns (Card, Cash, ...) -> one tender_type by the column that carries money."""
    out = pd.Series([None] * len(df), index=df.index, dtype=object)
    for col, tender in p["columns"].items():
        if col in df.columns:
            v = _money(df[col]).fillna(0.0)
            out = out.where(out.notna(), np.where(v != 0, tender, None))
    df[p["to"]] = out.fillna(p.get("default", "Other"))
    return df


def rule_split_modifiers(df, p):
    """
    Modifiers packed into one text column ("Add Bacon ($2.00), Medium" / "Medium:0.00; Add Bacon:2.00")
    -> extra entity sales_line_modifier, one row per modifier.  The modifier price is informational
    (included_in_parent = True) because the parent line's gross already includes it.
    """
    src = df[p["from"]].fillna("").astype(str)
    delim = p.get("delimiter", ", ")
    price_re = re.compile(p.get("price_pattern", r"\(\$?\+?([0-9]+\.[0-9]{2})\)"))
    rows = []
    for idx, text in src.items():
        if not text.strip():
            continue
        for part in [x.strip() for x in text.split(delim) if x.strip()]:
            m = price_re.search(part)
            price = float(m.group(1)) if m else 0.0
            name = price_re.sub("", part).strip(" :;|")
            rows.append({"order_id": df.at[idx, "order_id"], "check_id": df.at[idx, "check_id"],
                         "parent_line_id": df.at[idx, "line_id"], "modifier": name, "modifier_group": None,
                         "parent_item_name": df.at[idx, "item_name"], "gross": 0.0, "discount": 0.0, "net": 0.0,
                         "price_delta": price, "quantity": df.at[idx, "quantity"], "void": bool(df.at[idx, "void"]),
                         "business_date": df.at[idx, "business_date"], "opened_at": df.at[idx, "opened_at"],
                         "included_in_parent": True})
    extra = pd.DataFrame(rows)
    df.attrs.setdefault("extra_entities", {})["sales_line_modifier"] = extra
    return df


RULES = {"business_date": rule_business_date, "regex_extract": rule_regex_extract, "combine_datetime": rule_combine_datetime,
         "expression": rule_expression, "copy": rule_copy, "shift_hours": rule_shift_hours, "synthetic_id": rule_synthetic_id,
         "tender_from_columns": rule_tender_from_columns, "split_modifiers": rule_split_modifiers}


# --------------------------------------------------------------------------- #
def apply_mapping(raw: pd.DataFrame, m: DocMapping, source_file: str) -> pd.DataFrame:
    for flt in m.filters:
        col = raw[flt["column"]].astype(str).str.strip()
        if "equals" in flt:
            raw = raw[col == str(flt["equals"])]
        if "not_equals" in flt:
            raw = raw[col != str(flt["not_equals"])]
    out = pd.DataFrame(index=raw.index)
    missing = []
    for canon, src in m.columns.items():
        if src in raw.columns:
            out[canon] = raw[src]
        else:
            out[canon] = None
            missing.append(src)
    for canon, val in m.constants.items():
        out[canon] = val
    for canon, vm in m.value_maps.items():
        if canon in out.columns:
            out[canon] = out[canon].astype(str).str.strip().map(vm).fillna(out[canon])
    for canon, t in m.types.items():
        if canon in out.columns:
            out[canon] = COERCE[t](out[canon])
    extras = {}
    for rule in m.rules:
        (name, params), = rule.items()
        out = RULES[name](out, params)
        extras.update(out.attrs.get("extra_entities", {}))
    out["source_system"] = m.source_system
    out["source_document"] = m.document_type
    out["source_file"] = os.path.relpath(source_file)
    out["source_row"] = range(1, len(out) + 1)
    out = out.reset_index(drop=True)
    for k, v in extras.items():
        v["source_system"] = m.source_system
        v["source_document"] = m.document_type + "+" + k
        v["source_file"] = os.path.relpath(source_file)
    out.attrs["missing_columns"] = missing
    out.attrs["extra_entities"] = extras
    return out


@dataclass
class IngestResult:
    entities: dict                  # entity -> DataFrame
    batches: pd.DataFrame           # one row per file x mapping: classification + row count
    unclassified: list
    source_systems: list


def ingest_folders(roots: list[str], mappings: list[DocMapping] | None = None, verbose: bool = True) -> IngestResult:
    mappings = mappings or load_mappings()
    files = []
    for root in roots:
        files += sorted(p for p in glob.glob(os.path.join(root, "**", "*"), recursive=True)
                        if os.path.isfile(p) and p.lower().endswith((".csv", ".xlsx")))
    frames: dict[str, list] = {}
    batches, unclassified = [], []
    for i, path in enumerate(files):
        matches = classify(path, mappings)
        if not matches:
            unclassified.append(path)
            batches.append({"file": os.path.relpath(path), "source_system": None, "document_type": None, "entity": None,
                            "rows": 0, "status": "unclassified", "layout_status": None, "missing_columns": ""})
            continue
        for m in matches:
            raw = read_table(path, m)
            df = apply_mapping(raw, m, path)
            extras = df.attrs.get("extra_entities", {})
            missing = df.attrs.get("missing_columns", [])
            df.attrs = {}
            frames.setdefault(m.entity, []).append(df)
            for k, v in extras.items():
                if len(v):
                    v.attrs = {}
                    frames.setdefault(k, []).append(v)
            batches.append({"file": os.path.relpath(path), "source_system": m.source_system, "document_type": m.document_type,
                            "entity": m.entity, "rows": len(df), "status": "classified", "layout_status": m.layout_status,
                            "missing_columns": ";".join(missing)})
        if verbose and (i % 500 == 0):
            print(f"  ... {i:,}/{len(files):,} files")
    entities = {k: pd.concat(v, ignore_index=True) for k, v in frames.items()}
    b = pd.DataFrame(batches)
    systems = sorted(set(b.source_system.dropna()) - {"owner_input", "noaa", "mixed"}) if len(b) else []
    return IngestResult(entities=entities, batches=b, unclassified=unclassified, source_systems=systems)


def ingest_folder(root: str, mappings=None, verbose: bool = True) -> IngestResult:
    return ingest_folders([root], mappings, verbose)
