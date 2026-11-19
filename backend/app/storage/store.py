"""File-based storage: CSV tables, JSON documents and JSON-lines logs.

Design
------
* Operational tables (products, sales, purchase orders, ...) are CSV files in ``DATA_DIR``.
* Configuration, users, preferences, saved scenarios, model registry etc. are JSON documents.
* Append-only logs (activity, route runs) are JSON-lines files.
* Every write is **atomic** (write to a temp file in the same folder, then ``os.replace``) and the
  previous version of the file is copied to ``DATA_DIR/backups/<name>/<timestamp>`` first, keeping the
  newest ``BACKUPS_KEPT`` copies. Original sample datasets live in ``datasets/sample`` and are never
  modified by the application — the working copy in ``DATA_DIR`` is.
* A process-wide re-entrant lock serialises read-modify-write sequences (``with store.lock:``).
* Reads are cached per file and invalidated by modification time, so large tables such as ``sales``
  are parsed once, not on every request. Callers always receive a copy.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKUPS_KEPT = 10


@dataclass(frozen=True)
class TableSchema:
    columns: tuple[str, ...]
    ints: tuple[str, ...] = ()
    nullable_ints: tuple[str, ...] = ()
    floats: tuple[str, ...] = ()
    bools: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    key: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)


TABLES: dict[str, TableSchema] = {
    "suppliers": TableSchema(
        ("id", "code", "name", "city", "region", "country", "latitude", "longitude", "quality_rating", "contact_email", "is_active", "is_simulated"),
        ints=("id",), floats=("latitude", "longitude", "quality_rating"), bools=("is_active", "is_simulated"), key=("id",),
        defaults={"country": "India", "is_active": True, "is_simulated": False},
    ),
    "warehouses": TableSchema(
        ("id", "code", "name", "city", "latitude", "longitude", "capacity_units", "is_simulated"),
        ints=("id", "capacity_units"), floats=("latitude", "longitude"), bools=("is_simulated",), key=("id",),
        defaults={"is_simulated": False},
    ),
    "products": TableSchema(
        ("id", "sku", "name", "category", "unit_cost", "selling_price", "ordering_cost", "holding_cost_rate", "primary_supplier_id", "is_active", "is_simulated"),
        ints=("id",), nullable_ints=("primary_supplier_id",), floats=("unit_cost", "selling_price", "ordering_cost", "holding_cost_rate"),
        bools=("is_active", "is_simulated"), key=("id",), defaults={"ordering_cost": 500.0, "holding_cost_rate": 0.22, "is_active": True, "is_simulated": False},
    ),
    "supplier_products": TableSchema(
        ("supplier_id", "product_id", "unit_cost", "lead_time_days", "min_order_qty"),
        ints=("supplier_id", "product_id", "lead_time_days", "min_order_qty"), floats=("unit_cost",), key=("supplier_id", "product_id"),
        defaults={"min_order_qty": 1},
    ),
    "inventory": TableSchema(
        ("product_id", "warehouse_id", "quantity_on_hand", "as_of_date"),
        ints=("product_id", "warehouse_id", "quantity_on_hand"), dates=("as_of_date",), key=("product_id", "warehouse_id"),
    ),
    "sales": TableSchema(
        ("date", "product_id", "warehouse_id", "units_sold", "unit_price", "discount_pct", "promotion", "is_simulated"),
        ints=("product_id", "warehouse_id", "units_sold"), floats=("unit_price", "discount_pct"), bools=("promotion", "is_simulated"),
        dates=("date",), key=("date", "product_id", "warehouse_id"), defaults={"discount_pct": 0.0, "promotion": False, "is_simulated": False},
    ),
    "purchase_orders": TableSchema(
        ("id", "po_number", "supplier_id", "warehouse_id", "order_date", "expected_date", "status", "source", "notes", "created_by_id", "is_simulated"),
        ints=("id", "supplier_id", "warehouse_id"), nullable_ints=("created_by_id",), bools=("is_simulated",),
        dates=("order_date", "expected_date"), key=("id",), defaults={"source": "manual", "notes": "", "is_simulated": False},
    ),
    "purchase_order_items": TableSchema(
        ("purchase_order_id", "product_id", "quantity", "unit_cost"),
        ints=("purchase_order_id", "product_id", "quantity"), floats=("unit_cost",), key=("purchase_order_id", "product_id"),
    ),
    "deliveries": TableSchema(
        ("purchase_order_id", "supplier_id", "promised_date", "actual_date", "delay_days", "quantity_received", "quality_score", "distance_km", "is_simulated"),
        ints=("purchase_order_id", "supplier_id", "delay_days", "quantity_received"), floats=("quality_score", "distance_km"),
        bools=("is_simulated",), dates=("promised_date", "actual_date"), key=("purchase_order_id",), defaults={"is_simulated": False},
    ),
    "locations": TableSchema(
        ("id", "code", "name", "location_type", "city", "latitude", "longitude", "daily_demand_units", "warehouse_id", "supplier_id", "is_simulated"),
        ints=("id", "daily_demand_units"), nullable_ints=("warehouse_id", "supplier_id"), floats=("latitude", "longitude"),
        bools=("is_simulated",), key=("id",), defaults={"daily_demand_units": 0, "is_simulated": False},
    ),
    "routes": TableSchema(
        ("id", "origin_id", "destination_id", "distance_km", "travel_time_hours", "cost_per_km", "toll_cost", "is_simulated"),
        ints=("id", "origin_id", "destination_id"), floats=("distance_km", "travel_time_hours", "cost_per_km", "toll_cost"),
        bools=("is_simulated",), key=("id",), defaults={"toll_cost": 0.0, "is_simulated": False},
    ),
    "demand_forecasts": TableSchema(
        ("run_id", "product_id", "model_name", "forecast_date", "predicted_units", "lower_bound", "upper_bound", "generated_at"),
        ints=("run_id", "product_id"), floats=("predicted_units", "lower_bound", "upper_bound"), dates=("forecast_date",),
        key=("run_id", "product_id", "model_name", "forecast_date"),
    ),
}

_TRUE = {"true", "1", "yes", "y", "t"}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_default(o: Any):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"Not JSON serialisable: {type(o)}")


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.lock = threading.RLock()
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        for sub in ("", "backups", "uploads", "reports"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths & backups
    def table_path(self, name: str) -> Path:
        return self.root / f"{name}.csv"

    def doc_path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def _backup(self, path: Path) -> None:
        if not path.exists():
            return
        bdir = self.root / "backups" / path.stem
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        shutil.copy2(path, bdir / f"{stamp}{path.suffix}")
        old = sorted(bdir.iterdir())
        for f in old[:-BACKUPS_KEPT]:
            f.unlink(missing_ok=True)

    def _atomic_write(self, path: Path, text: str, backup: bool) -> None:
        with self.lock:
            if backup:
                self._backup(path)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8", newline="")
            os.replace(tmp, path)

    # ------------------------------------------------------------------ tables
    def exists(self, name: str) -> bool:
        return self.table_path(name).exists()

    def empty_table(self, name: str) -> pd.DataFrame:
        return self._typed(pd.DataFrame(columns=list(TABLES[name].columns)), name)

    def read(self, name: str) -> pd.DataFrame:
        """Typed copy of a table (empty, correctly typed frame if the file does not exist yet)."""
        path = self.table_path(name)
        if not path.exists():
            return self.empty_table(name)
        mtime = path.stat().st_mtime_ns
        cached = self._cache.get(name)
        if cached is None or cached[0] != mtime:
            raw = pd.read_csv(path, dtype=str, keep_default_na=False)
            df = self._typed(raw, name)
            self._cache[name] = (mtime, df)
            cached = self._cache[name]
        return cached[1].copy()

    def _typed(self, df: pd.DataFrame, name: str) -> pd.DataFrame:
        s = TABLES[name]
        df = df.copy()
        for c in s.columns:
            if c not in df.columns:
                df[c] = s.defaults.get(c, None)
        df = df[list(s.columns)]
        def blank(col: str) -> pd.Series:  # empty CSV cells → missing
            return df[col].mask(df[col].astype(str).str.strip() == "") if df[col].dtype == object else df[col]
        for c in s.ints:
            df[c] = pd.to_numeric(blank(c), errors="raise").fillna(s.defaults.get(c, 0)).astype("int64")
        for c in s.nullable_ints:
            df[c] = pd.to_numeric(blank(c), errors="raise").astype("Int64")
        for c in s.floats:
            df[c] = pd.to_numeric(blank(c), errors="raise").astype("float64")
        for c in s.bools:
            col = df[c]
            if col.dtype != bool:
                col = col.map(lambda v: v if isinstance(v, (bool, np.bool_)) else str(v).strip().lower() in _TRUE)
            df[c] = col.astype(bool)
        for c in s.dates:
            df[c] = pd.to_datetime(blank(c)).dt.date
        return df

    def write(self, name: str, df: pd.DataFrame, backup: bool = True) -> None:
        s = TABLES[name]
        typed = self._typed(df, name)
        if s.key and typed.duplicated(list(s.key)).any():
            raise ValueError(f"{name}: duplicate key {s.key}")
        out = typed.copy()
        for c in s.dates:
            out[c] = out[c].map(lambda d: d.isoformat() if isinstance(d, date) else "")
        text = out.to_csv(index=False, lineterminator="\n")
        self._atomic_write(self.table_path(name), text, backup)
        self._cache.pop(name, None)

    def upsert(self, name: str, rows: pd.DataFrame, backup: bool = True) -> int:
        """Insert or update rows by the table key. Returns number of rows written."""
        key = list(TABLES[name].key)
        with self.lock:
            cur = self.read(name)
            new = self._typed(rows, name)
            merged = pd.concat([cur, new], ignore_index=True).drop_duplicates(subset=key, keep="last")
            self.write(name, merged, backup=backup)
        return len(new)

    def next_id(self, name: str) -> int:
        df = self.read(name)
        return int(df["id"].max()) + 1 if len(df) else 1

    # ------------------------------------------------------------------ JSON documents
    def read_json(self, name: str, default: Any = None) -> Any:
        p = self.doc_path(name)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def write_json(self, name: str, obj: Any, backup: bool = True) -> None:
        self._atomic_write(self.doc_path(name), json.dumps(obj, indent=2, default=_json_default), backup)

    def update_json(self, name: str, default: Any, fn) -> Any:
        """Read-modify-write a JSON document under the store lock; fn returns the new object."""
        with self.lock:
            new = fn(self.read_json(name, default))
            self.write_json(name, new)
            return new

    # ------------------------------------------------------------------ JSON-lines logs
    def append_jsonl(self, name: str, record: dict) -> None:
        with self.lock:
            with (self.root / f"{name}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=_json_default) + "\n")

    def read_jsonl(self, name: str, last: int | None = None) -> list[dict]:
        p = self.root / f"{name}.jsonl"
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()
        if last:
            lines = lines[-last:]
        return [json.loads(line) for line in lines if line.strip()]


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        from app.core.config import get_settings

        _store = Store(get_settings().data_dir)
    return _store


def reset_store_for_tests(root: Path) -> Store:
    global _store
    _store = Store(root)
    return _store
