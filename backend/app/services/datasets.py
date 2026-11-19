"""Dataset specifications, validation and import pipeline (file-based storage).

Used by both the Data Management upload workflow and the seed script, so the demo data goes
through exactly the same validation as user uploads. Nothing malformed is ever imported silently:
an import with validation errors is refused unless the caller explicitly opts to skip invalid rows,
in which case the skipped row count is reported and logged.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from app.core.exceptions import ValidationFailed
from app.storage.store import Store

PO_STATUSES = ("draft", "placed", "in_transit", "received", "cancelled")

MAX_REPORTED_ERRORS = 200
TRUE_VALUES = {"true", "1", "yes", "y", "t"}
FALSE_VALUES = {"false", "0", "no", "n", "f"}


@dataclass(frozen=True)
class Col:
    name: str
    kind: str  # str|int|float|date|bool
    required: bool = True
    check: Callable[[pd.Series], pd.Series] | None = None  # returns boolean mask of VALID values
    check_msg: str = ""
    choices: tuple[str, ...] | None = None
    example: str = ""


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    label: str
    description: str
    columns: tuple[Col, ...]
    unique_key: tuple[str, ...]
    # (column, entity) foreign-key lookups resolved against DB + this file
    references: tuple[tuple[str, str], ...] = ()


def _nonneg(s: pd.Series) -> pd.Series:
    return s >= 0


def _positive(s: pd.Series) -> pd.Series:
    return s > 0


SPECS: dict[str, DatasetSpec] = {
    s.key: s
    for s in [
        DatasetSpec(
            "suppliers", "Suppliers", "Supplier master data",
            (
                Col("code", "str", example="SUP-01"),
                Col("name", "str", example="Apex Components Pvt Ltd"),
                Col("city", "str", example="Pune"),
                Col("region", "str", example="West"),
                Col("country", "str", required=False, example="India"),
                Col("latitude", "float", required=False, check=lambda s: s.between(-90, 90), check_msg="must be between -90 and 90", example="18.52"),
                Col("longitude", "float", required=False, check=lambda s: s.between(-180, 180), check_msg="must be between -180 and 180", example="73.85"),
                Col("quality_rating", "float", check=lambda s: s.between(1, 5), check_msg="must be between 1 and 5", example="4.2"),
                Col("contact_email", "str", required=False, example="orders@apex.example"),
                Col("is_simulated", "bool", required=False, example="false"),
            ),
            unique_key=("code",),
        ),
        DatasetSpec(
            "warehouses", "Warehouses", "Warehouse locations and storage capacity",
            (
                Col("code", "str", example="WH-MUM"),
                Col("name", "str", example="Mumbai Fulfilment Centre"),
                Col("city", "str", example="Mumbai"),
                Col("latitude", "float", required=False, check=lambda s: s.between(-90, 90), check_msg="must be between -90 and 90", example="19.29"),
                Col("longitude", "float", required=False, check=lambda s: s.between(-180, 180), check_msg="must be between -180 and 180", example="73.06"),
                Col("capacity_units", "int", check=_positive, check_msg="must be > 0", example="60000"),
                Col("is_simulated", "bool", required=False, example="false"),
            ),
            unique_key=("code",),
        ),
        DatasetSpec(
            "products", "Products", "Product catalogue with cost parameters",
            (
                Col("sku", "str", example="SKU-101"),
                Col("name", "str", example="Wireless Mouse"),
                Col("category", "str", example="Electronics"),
                Col("unit_cost", "float", check=_nonneg, check_msg="must be >= 0", example="320"),
                Col("selling_price", "float", check=_nonneg, check_msg="must be >= 0", example="549"),
                Col("ordering_cost", "float", required=False, check=_nonneg, check_msg="must be >= 0", example="500"),
                Col("holding_cost_rate", "float", required=False, check=lambda s: s.between(0, 1), check_msg="annual fraction between 0 and 1", example="0.22"),
                Col("primary_supplier_code", "str", required=False, example="SUP-01"),
                Col("is_simulated", "bool", required=False, example="false"),
            ),
            unique_key=("sku",),
            references=(("primary_supplier_code", "supplier"),),
        ),
        DatasetSpec(
            "supplier_products", "Supplier catalogue", "Which supplier sells which product, at what price and lead time",
            (
                Col("supplier_code", "str", example="SUP-01"),
                Col("sku", "str", example="SKU-101"),
                Col("unit_cost", "float", check=_nonneg, check_msg="must be >= 0", example="315"),
                Col("lead_time_days", "int", check=_positive, check_msg="must be > 0", example="9"),
                Col("min_order_qty", "int", required=False, check=_positive, check_msg="must be > 0", example="50"),
            ),
            unique_key=("supplier_code", "sku"),
            references=(("supplier_code", "supplier"), ("sku", "product")),
        ),
        DatasetSpec(
            "inventory", "Inventory", "Current stock on hand per product and warehouse",
            (
                Col("sku", "str", example="SKU-101"),
                Col("warehouse_code", "str", example="WH-MUM"),
                Col("quantity_on_hand", "int", check=_nonneg, check_msg="must be >= 0", example="840"),
                Col("as_of_date", "date", example="2026-11-18"),
            ),
            unique_key=("sku", "warehouse_code"),
            references=(("sku", "product"), ("warehouse_code", "warehouse")),
        ),
        DatasetSpec(
            "sales", "Historical sales", "Daily units sold per product and warehouse",
            (
                Col("date", "date", example="2026-11-18"),
                Col("sku", "str", example="SKU-101"),
                Col("warehouse_code", "str", example="WH-MUM"),
                Col("units_sold", "int", check=_nonneg, check_msg="must be >= 0", example="23"),
                Col("unit_price", "float", check=_nonneg, check_msg="must be >= 0", example="549"),
                Col("discount_pct", "float", required=False, check=lambda s: (s >= 0) & (s < 100), check_msg="must be in [0, 100)", example="0"),
                Col("promotion", "bool", required=False, example="false"),
                Col("is_simulated", "bool", required=False, example="false"),
            ),
            unique_key=("date", "sku", "warehouse_code"),
            references=(("sku", "product"), ("warehouse_code", "warehouse")),
        ),
        DatasetSpec(
            "purchase_orders", "Purchase orders", "One row per order line; rows sharing a po_number form one order",
            (
                Col("po_number", "str", example="PO-2026-00001"),
                Col("supplier_code", "str", example="SUP-01"),
                Col("warehouse_code", "str", example="WH-MUM"),
                Col("order_date", "date", example="2026-10-01"),
                Col("expected_date", "date", example="2026-10-10"),
                Col("status", "str", choices=PO_STATUSES, example="received"),
                Col("sku", "str", example="SKU-101"),
                Col("quantity", "int", check=_positive, check_msg="must be > 0", example="500"),
                Col("unit_cost", "float", check=_nonneg, check_msg="must be >= 0", example="315"),
                Col("is_simulated", "bool", required=False, example="false"),
            ),
            unique_key=("po_number", "sku"),
            references=(("supplier_code", "supplier"), ("warehouse_code", "warehouse"), ("sku", "product")),
        ),
        DatasetSpec(
            "deliveries", "Deliveries", "Goods receipts against purchase orders",
            (
                Col("po_number", "str", example="PO-2026-00001"),
                Col("actual_date", "date", example="2026-10-12"),
                Col("quantity_received", "int", check=_nonneg, check_msg="must be >= 0", example="500"),
                Col("quality_score", "float", check=lambda s: s.between(0, 100), check_msg="percent between 0 and 100", example="98.5"),
                Col("distance_km", "float", required=False, check=_nonneg, check_msg="must be >= 0", example="148"),
                Col("is_simulated", "bool", required=False, example="false"),
            ),
            unique_key=("po_number",),
            references=(("po_number", "purchase_order"),),
        ),
        DatasetSpec(
            "locations", "Network locations", "Nodes of the logistics network",
            (
                Col("code", "str", example="DC-NAG"),
                Col("name", "str", example="Nagpur Distribution Centre"),
                Col("location_type", "str", choices=("warehouse", "distribution_center", "retail_store", "supplier"), example="distribution_center"),
                Col("city", "str", example="Nagpur"),
                Col("latitude", "float", check=lambda s: s.between(-90, 90), check_msg="must be between -90 and 90", example="21.15"),
                Col("longitude", "float", check=lambda s: s.between(-180, 180), check_msg="must be between -180 and 180", example="79.09"),
                Col("daily_demand_units", "int", required=False, check=_nonneg, check_msg="must be >= 0", example="0"),
                Col("warehouse_code", "str", required=False, example="WH-MUM"),
                Col("supplier_code", "str", required=False, example=""),
                Col("is_simulated", "bool", required=False, example="false"),
            ),
            unique_key=("code",),
            references=(("warehouse_code", "warehouse"), ("supplier_code", "supplier")),
        ),
        DatasetSpec(
            "routes", "Transportation routes", "Directed road links between network locations",
            (
                Col("origin_code", "str", example="WH-MUM"),
                Col("destination_code", "str", example="ST-PUN"),
                Col("distance_km", "float", check=_positive, check_msg="must be > 0", example="150"),
                Col("travel_time_hours", "float", check=_positive, check_msg="must be > 0", example="3.4"),
                Col("cost_per_km", "float", check=_positive, check_msg="must be > 0", example="32"),
                Col("toll_cost", "float", required=False, check=_nonneg, check_msg="must be >= 0", example="420"),
                Col("is_simulated", "bool", required=False, example="false"),
            ),
            unique_key=("origin_code", "destination_code"),
            references=(("origin_code", "location"), ("destination_code", "location")),
        ),
    ]
}

# Import order that satisfies foreign keys.
IMPORT_ORDER = [
    "suppliers", "warehouses", "products", "supplier_products", "inventory",
    "sales", "purchase_orders", "deliveries", "locations", "routes",
]


@dataclass
class ValidationReport:
    dataset: str
    row_count: int
    detected_columns: list[str]
    missing_required_columns: list[str]
    unknown_columns: list[str]
    missing_values: dict[str, int]
    duplicate_rows: int
    errors: list[dict] = field(default_factory=list)
    error_count: int = 0
    invalid_row_count: int = 0
    preview: list[dict] = field(default_factory=list)
    column_types: dict[str, str] = field(default_factory=dict)

    @property
    def valid_row_count(self) -> int:
        return self.row_count - self.invalid_row_count

    def as_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "row_count": self.row_count,
            "valid_row_count": self.valid_row_count,
            "invalid_row_count": self.invalid_row_count,
            "detected_columns": self.detected_columns,
            "missing_required_columns": self.missing_required_columns,
            "unknown_columns": self.unknown_columns,
            "missing_values": self.missing_values,
            "duplicate_rows": self.duplicate_rows,
            "error_count": self.error_count,
            "errors": self.errors[:MAX_REPORTED_ERRORS],
            "errors_truncated": self.error_count > MAX_REPORTED_ERRORS,
            "preview": self.preview,
            "column_types": self.column_types,
        }


# ---------------------------------------------------------------- reading
def read_table(content: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
        else:
            raise ValidationFailed("Unsupported file type. Upload a .csv or .xlsx file.")
    except ValidationFailed:
        raise
    except Exception as exc:  # parser errors
        raise ValidationFailed(f"Could not parse file: {exc}") from exc
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    df = df.apply(lambda s: s.str.strip() if s.dtype == object else s)
    return df.replace({"": np.nan, "nan": np.nan, "NaN": np.nan, "null": np.nan, "NULL": np.nan})


# ---------------------------------------------------------------- reference lookups
_ENTITY = {
    "supplier": ("suppliers", "code"),
    "warehouse": ("warehouses", "code"),
    "product": ("products", "sku"),
    "purchase_order": ("purchase_orders", "po_number"),
    "location": ("locations", "code"),
}


def lookup(store: Store, entity: str) -> dict[str, int]:
    table, key = _ENTITY[entity]
    df = store.read(table)
    return dict(zip(df[key].astype(str), df["id"].astype(int)))


# ---------------------------------------------------------------- validation
def validate(df_raw: pd.DataFrame, dataset: str, store: Store) -> tuple[ValidationReport, pd.DataFrame]:
    """Returns the report and a typed dataframe with an `_valid` column."""
    if dataset not in SPECS:
        raise ValidationFailed(f"Unknown dataset '{dataset}'. Expected one of: {', '.join(SPECS)}")
    spec = SPECS[dataset]
    spec_names = [c.name for c in spec.columns]
    detected = list(df_raw.columns)
    missing_cols = [c.name for c in spec.columns if c.required and c.name not in detected]
    unknown = [c for c in detected if c not in spec_names]

    report = ValidationReport(
        dataset=dataset,
        row_count=len(df_raw),
        detected_columns=detected,
        missing_required_columns=missing_cols,
        unknown_columns=unknown,
        missing_values={c: int(df_raw[c].isna().sum()) for c in detected},
        duplicate_rows=0,
        column_types={c.name: c.kind for c in spec.columns},
    )
    df = df_raw.copy()
    df.insert(0, "_row", np.arange(2, len(df) + 2))  # spreadsheet row number (header = 1)
    invalid = pd.Series(False, index=df.index)

    def add_errors(mask: pd.Series, column: str, message: str) -> None:
        nonlocal invalid
        mask = mask.fillna(False).astype(bool)
        if not mask.any():
            return
        invalid |= mask
        report.error_count += int(mask.sum())
        room = MAX_REPORTED_ERRORS - len(report.errors)
        if room > 0:
            for _, r in df_raw.loc[mask].head(room).iterrows():
                val = r.get(column) if column in df_raw.columns else None
                report.errors.append({
                    "row": int(df.at[_, "_row"]),
                    "column": column,
                    "value": None if pd.isna(val) else str(val),
                    "message": message,
                })

    if missing_cols:
        report.error_count += len(missing_cols)
        report.errors.extend(
            {"row": None, "column": c, "value": None, "message": "required column is missing"} for c in missing_cols
        )
        report.invalid_row_count = len(df)
        report.preview = _preview(df_raw)
        return report, df.assign(_valid=False)

    for col in spec.columns:
        if col.name not in df.columns:
            df[col.name] = np.nan
            continue
        raw = df_raw[col.name]
        present = raw.notna()
        if col.required:
            add_errors(~present, col.name, "missing required value")
        if col.kind == "int":
            num = pd.to_numeric(raw, errors="coerce")
            bad = present & (num.isna() | (num.notna() & (num % 1 != 0)))
            add_errors(bad, col.name, "must be a whole number")
            df[col.name] = num.where(~bad).astype("Int64")
        elif col.kind == "float":
            num = pd.to_numeric(raw, errors="coerce")
            bad = present & num.isna()
            add_errors(bad, col.name, "must be a number")
            df[col.name] = num.where(~bad)
        elif col.kind == "date":
            dt = pd.to_datetime(raw, errors="coerce", format="ISO8601")
            bad = present & dt.isna()
            add_errors(bad, col.name, "must be a date in YYYY-MM-DD format")
            df[col.name] = dt.dt.date
        elif col.kind == "bool":
            low = raw.astype(str).str.lower()
            bad = present & ~low.isin(TRUE_VALUES | FALSE_VALUES)
            add_errors(bad, col.name, "must be true/false")
            df[col.name] = low.isin(TRUE_VALUES)
        else:
            df[col.name] = raw
        if col.choices is not None:
            add_errors(present & ~raw.isin(col.choices), col.name, f"must be one of: {', '.join(col.choices)}")
        if col.check is not None:
            values = df[col.name]
            ok = values.isna() | col.check(values.astype(float) if col.kind in ("int", "float") else values)
            add_errors(~ok.fillna(True).astype(bool), col.name, col.check_msg)

    # Row-level business rules
    if dataset == "purchase_orders":
        od, ed = pd.to_datetime(df["order_date"]), pd.to_datetime(df["expected_date"])
        add_errors(ed < od, "expected_date", "expected_date must not be before order_date")
        hdr = df.groupby("po_number")[["supplier_code", "warehouse_code", "order_date"]].nunique()
        inconsistent = hdr[(hdr > 1).any(axis=1)].index
        add_errors(df["po_number"].isin(inconsistent), "po_number", "rows of the same PO disagree on supplier/warehouse/order_date")
    if dataset == "products":
        add_errors(df["selling_price"] < df["unit_cost"], "selling_price", "selling_price is below unit_cost")
    if dataset == "routes":
        add_errors(df["origin_code"] == df["destination_code"], "destination_code", "origin and destination are the same")

    # Duplicates within the file
    dup = df.duplicated(subset=list(spec.unique_key), keep="first") & df[list(spec.unique_key)].notna().all(axis=1)
    report.duplicate_rows = int(dup.sum())
    add_errors(dup, spec.unique_key[0], f"duplicate of an earlier row (key: {', '.join(spec.unique_key)})")

    # Foreign-key references (DB + rows in this same file for self references)
    for column, entity in spec.references:
        known = set(lookup(store, entity))
        vals = df[column]
        add_errors(
            vals.notna() & ~vals.isin(known),
            column,
            f"unknown {entity.replace('_', ' ')} (import the {entity.replace('_', ' ')}s dataset first)",
        )

    report.invalid_row_count = int(invalid.sum())
    report.preview = _preview(df_raw)
    return report, df.assign(_valid=~invalid)


def _preview(df: pd.DataFrame, n: int = 15) -> list[dict]:
    return df.head(n).astype(object).where(df.head(n).notna(), None).to_dict(orient="records")


# ---------------------------------------------------------------- import
def _with_ids(store: Store, table: str, df: pd.DataFrame, natural_key: str) -> pd.DataFrame:
    """Assign existing ids for known natural keys, new sequential ids otherwise."""
    cur = store.read(table)
    known = dict(zip(cur[natural_key].astype(str), cur["id"].astype(int)))
    nxt = int(cur["id"].max()) + 1 if len(cur) else 1
    ids = []
    for k in df[natural_key].astype(str):
        if k not in known:
            known[k] = nxt
            nxt += 1
        ids.append(known[k])
    return df.assign(id=ids)


def _keep_existing(store: Store, table: str, df: pd.DataFrame, key: list[str], cols: list[str]) -> pd.DataFrame:
    """For rows that update an existing record, a blank optional value keeps the stored value
    (instead of wiping it); new rows get the defaults applied afterwards."""
    cur = store.read(table)
    if cur.empty:
        return df
    m = df.merge(cur[key + cols], on=key, how="left", suffixes=("", "__old"))
    for c in cols:
        m[c] = m[c].astype(object).where(m[c].notna(), m[f"{c}__old"].astype(object))
    m.index = df.index
    return m.drop(columns=[f"{c}__old" for c in cols])


def import_valid_rows(store: Store, dataset: str, df: pd.DataFrame) -> int:
    """Upserts the rows flagged valid into the working CSV tables (atomic, backed-up writes)."""
    df = df[df["_valid"]].copy()
    if df.empty:
        return 0
    if "is_simulated" not in df.columns:
        df["is_simulated"] = False
    df["is_simulated"] = df["is_simulated"].fillna(False).astype(bool)

    with store.lock:
        if dataset == "suppliers":
            df = _keep_existing(store, "suppliers", df, ["code"], ["country", "latitude", "longitude", "contact_email"])
            df["country"] = df["country"].fillna("India")
            df["is_active"] = True
            store.upsert("suppliers", _with_ids(store, "suppliers", df, "code"))
        elif dataset == "warehouses":
            df = _keep_existing(store, "warehouses", df, ["code"], ["latitude", "longitude"])
            store.upsert("warehouses", _with_ids(store, "warehouses", df, "code"))
        elif dataset == "products":
            df["primary_supplier_id"] = df["primary_supplier_code"].map(lookup(store, "supplier"))
            df = _keep_existing(store, "products", df, ["sku"], ["primary_supplier_id", "ordering_cost", "holding_cost_rate"])
            df["ordering_cost"] = df["ordering_cost"].fillna(500.0)
            df["holding_cost_rate"] = df["holding_cost_rate"].fillna(0.22)
            df["is_active"] = True
            store.upsert("products", _with_ids(store, "products", df, "sku"))
        elif dataset == "supplier_products":
            df["supplier_id"] = df["supplier_code"].map(lookup(store, "supplier"))
            df["product_id"] = df["sku"].map(lookup(store, "product"))
            df = _keep_existing(store, "supplier_products", df, ["supplier_id", "product_id"], ["min_order_qty"])
            df["min_order_qty"] = df["min_order_qty"].fillna(1)
            store.upsert("supplier_products", df)
        elif dataset == "inventory":
            df["product_id"] = df["sku"].map(lookup(store, "product"))
            df["warehouse_id"] = df["warehouse_code"].map(lookup(store, "warehouse"))
            store.upsert("inventory", df)
        elif dataset == "sales":
            df["product_id"] = df["sku"].map(lookup(store, "product"))
            df["warehouse_id"] = df["warehouse_code"].map(lookup(store, "warehouse"))
            df["discount_pct"] = df["discount_pct"].fillna(0.0)
            df["promotion"] = df["promotion"].fillna(False).astype(bool)
            store.upsert("sales", df)
        elif dataset == "purchase_orders":
            sup, wh, prod = lookup(store, "supplier"), lookup(store, "warehouse"), lookup(store, "product")
            heads = df.groupby("po_number").first().reset_index()
            heads["supplier_id"] = heads["supplier_code"].map(sup)
            heads["warehouse_id"] = heads["warehouse_code"].map(wh)
            heads["source"] = "import"
            heads = _with_ids(store, "purchase_orders", heads, "po_number")
            store.upsert("purchase_orders", heads)
            df["purchase_order_id"] = df["po_number"].map(dict(zip(heads["po_number"], heads["id"])))
            df["product_id"] = df["sku"].map(prod)
            store.upsert("purchase_order_items", df)
        elif dataset == "deliveries":
            pos = store.read("purchase_orders").set_index("po_number")
            df["purchase_order_id"] = df["po_number"].map(pos["id"])
            df["supplier_id"] = df["po_number"].map(pos["supplier_id"])
            df["promised_date"] = df["po_number"].map(pos["expected_date"])
            df["delay_days"] = [(a - p).days for a, p in zip(df["actual_date"], df["promised_date"])]
            store.upsert("deliveries", df)
            po = store.read("purchase_orders")
            po.loc[po["id"].isin(df["purchase_order_id"]), "status"] = "received"
            store.write("purchase_orders", po)
        elif dataset == "locations":
            df["warehouse_id"] = df["warehouse_code"].map(lookup(store, "warehouse"))
            df["supplier_id"] = df["supplier_code"].map(lookup(store, "supplier"))
            df = _keep_existing(store, "locations", df, ["code"], ["daily_demand_units", "warehouse_id", "supplier_id"])
            df["daily_demand_units"] = df["daily_demand_units"].fillna(0)
            store.upsert("locations", _with_ids(store, "locations", df, "code"))
        elif dataset == "routes":
            loc = lookup(store, "location")
            df["origin_id"] = df["origin_code"].map(loc)
            df["destination_id"] = df["destination_code"].map(loc)
            df["toll_cost"] = df["toll_cost"].fillna(0.0)
            cur = store.read("routes")
            pair_id = {(o, d): i for o, d, i in zip(cur["origin_id"], cur["destination_id"], cur["id"])}
            nxt = int(cur["id"].max()) + 1 if len(cur) else 1
            ids = []
            for o, d in zip(df["origin_id"], df["destination_id"]):
                if (o, d) not in pair_id:
                    pair_id[(o, d)] = nxt
                    nxt += 1
                ids.append(pair_id[(o, d)])
            store.upsert("routes", df.assign(id=ids))
        else:  # pragma: no cover
            raise ValidationFailed(f"Import not supported for {dataset}")
    return len(df)


def template_csv(dataset: str) -> str:
    spec = SPECS[dataset]
    header = ",".join(c.name for c in spec.columns)
    example = ",".join(c.example for c in spec.columns)
    return f"{header}\n{example}\n"


def load_csv_file(store: Store, dataset: str, path: Path) -> tuple[ValidationReport, int]:
    """Validate + import a CSV from disk (initialisation pipeline). Raises if any row is invalid."""
    df_raw = read_table(path.read_bytes(), path.name)
    report, typed = validate(df_raw, dataset, store)
    if report.error_count:
        raise ValidationFailed(f"{dataset}: {report.error_count} validation errors", details=report.errors[:20])
    return report, import_valid_rows(store, dataset, typed)
