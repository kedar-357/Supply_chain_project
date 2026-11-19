"""Data-preparation adapter: UCI "Online Retail" dataset → SupplyChainX import CSVs.

The UCI file (https://archive.ics.uci.edu/dataset/352/online+retail) is not redistributed with this
project. Download it yourself, then run:

    python -m app.seed.prepare_uci_online_retail path/to/Online_Retail.xlsx --top 40 --out datasets/uci

It writes products.csv and sales.csv in the import format (see Data Management → templates), which
can then be uploaded through the normal validation pipeline. Steps:

1. Drop cancellations (InvoiceNo starting with "C"), non-positive quantities/prices and
   non-product stock codes (POST, M, BANK CHARGES, ...).
2. Keep the `--top` stock codes by revenue.
3. Aggregate to daily units per product; map countries to warehouses (UK → WH-MUM stand-in, other
   countries → WH-DEL) because the app is warehouse-based. The mapping is configurable.
4. Unit cost is unknown in the source; it is estimated as `cost_ratio` × median price and flagged in
   the product name suffix "(UCI)". Document this assumption when using the data.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

NON_PRODUCT = {"POST", "D", "M", "DOT", "BANK CHARGES", "C2", "CRUK", "PADS", "AMAZONFEE", "S", "B"}


def transform(raw: pd.DataFrame, top: int = 40, cost_ratio: float = 0.6,
              warehouse_map: dict[str, str] | None = None, default_warehouse: str = "WH-DEL",
              category: str = "Home & Kitchen") -> dict[str, pd.DataFrame]:
    wmap = warehouse_map or {"United Kingdom": "WH-MUM"}
    df = raw.rename(columns=str.strip).copy()
    df = df[~df["InvoiceNo"].astype(str).str.startswith("C")]
    df = df[(df["Quantity"] > 0) & (df["UnitPrice"] > 0)]
    df["StockCode"] = df["StockCode"].astype(str).str.strip().str.upper()
    df = df[~df["StockCode"].isin(NON_PRODUCT)]
    df["revenue"] = df["Quantity"] * df["UnitPrice"]
    keep = df.groupby("StockCode")["revenue"].sum().nlargest(top).index
    df = df[df["StockCode"].isin(keep)].copy()
    df["date"] = pd.to_datetime(df["InvoiceDate"], format="mixed").dt.date
    df["warehouse_code"] = df["Country"].map(wmap).fillna(default_warehouse)
    df["sku"] = "UCI-" + df["StockCode"]

    names = df.groupby("sku")["Description"].agg(lambda s: s.dropna().mode().iloc[0] if s.notna().any() else "Unknown")
    price = df.groupby("sku")["UnitPrice"].median()
    products = pd.DataFrame({
        "sku": names.index,
        "name": [f"{str(n).title()[:140]} (UCI)" for n in names.values],
        "category": category,
        "unit_cost": (price * cost_ratio).round(2).values,
        "selling_price": price.round(2).values,
        "is_simulated": False,
    })
    sales = (df.groupby(["date", "sku", "warehouse_code"])
             .agg(units_sold=("Quantity", "sum"), unit_price=("UnitPrice", "median")).reset_index())
    sales["discount_pct"] = 0.0
    sales["promotion"] = False
    sales["is_simulated"] = False
    return {"products": products, "sales": sales}


def main() -> None:  # pragma: no cover
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--out", default="datasets/uci")
    a = p.parse_args()
    raw = pd.read_excel(a.path) if a.path.lower().endswith((".xlsx", ".xls")) else pd.read_csv(a.path, encoding="latin-1")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, frame in transform(raw, a.top).items():
        frame.to_csv(out / f"{name}.csv", index=False)
        print(f"{name}: {len(frame)} rows -> {out / f'{name}.csv'}")


if __name__ == "__main__":  # pragma: no cover
    main()
