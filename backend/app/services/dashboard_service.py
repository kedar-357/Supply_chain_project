"""Overview dashboard: KPIs, trends, distributions, alerts and recent activity — all computed from the data files."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.services import activity, forecasting_service, supplier_service, user_service
from app.services import inventory_service as inv
from app.storage.store import Store


def resolve_range(store: Store, days: int | None, start: date | None, end: date | None) -> tuple[date, date]:
    data_end = forecasting_service.data_end_date(store) or date.today()
    if start and end:
        if start > end:
            start, end = end, start
        return start, end
    d = days or 30
    return data_end - timedelta(days=d - 1), data_end


def summary(store: Store, start: date, end: date) -> dict:
    ctx, snap = inv.snapshot(store)
    span = (end - start).days + 1
    sales = store.read("sales")
    period = sales[(sales["date"] >= start) & (sales["date"] <= end)]
    prev = sales[(sales["date"] >= start - timedelta(days=span)) & (sales["date"] < start)]
    units = float(period["units_sold"].sum())
    revenue = float((period["units_sold"] * period["unit_price"]).sum())
    prev_units = float(prev["units_sold"].sum())
    po = store.read("purchase_orders")
    dl = store.read("deliveries").merge(po[["id", "order_date"]].rename(columns={"id": "purchase_order_id"}), on="purchase_order_id")
    dl = dl[(dl["actual_date"] >= start) & (dl["actual_date"] <= end)]
    products = store.read("products")
    data_end = max(sales["date"])
    year = sales[sales["date"] > data_end - timedelta(days=365)]
    cogs_year = float((year["units_sold"] * year["product_id"].map(dict(zip(products["id"], products["unit_cost"])))).sum())
    inv_value = float(snap["inventory_value"].sum())
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": span},
        "as_of": ctx.as_of.isoformat(),
        "total_inventory_value": round(inv_value, 2),
        "total_products": int(products["is_active"].sum()),
        "below_reorder_level": int((snap["inventory_position"] <= snap["reorder_point"]).sum()),
        "predicted_stockouts": int(snap["predicted_stockout"].sum()),
        "avg_supplier_delivery_days": round(float((pd.to_datetime(dl["actual_date"]) - pd.to_datetime(dl["order_date"])).dt.days.mean()), 1)
        if not dl.empty else None,
        "on_time_delivery_rate": round(float((dl["delay_days"] <= 0).mean()) * 100, 1) if not dl.empty else None,
        "deliveries_in_period": int(len(dl)),
        "monthly_demand_units": round(units * 30 / span, 0),
        "demand_change_pct": round((units - prev_units) / prev_units * 100, 1) if prev_units else None,
        "revenue_in_period": round(revenue, 2),
        "inventory_turnover": round(cogs_year / inv_value, 2) if inv_value else None,
        "turnover_note": "Annual COGS (last 365 days) ÷ current inventory value",
        "forecast_source": ctx.forecast_source,
    }


def trends(store: Store, start: date, end: date) -> dict:
    """Weekly actual sales in range, backtest predictions on the holdout window, and the forward forecast."""
    sales = store.read("sales")
    sales = sales[(sales["date"] >= start) & (sales["date"] <= end)]
    df = sales.groupby("date")["units_sold"].sum().rename("actual").reset_index()
    freq = "D" if (end - start).days <= 45 else "W-MON"
    points: dict[str, dict] = {}

    def full_bins(series: pd.Series) -> pd.Series:
        """Sum per bin, dropping partial weeks at the edges (they would show artificial dips)."""
        r = series.resample(freq, label="left", closed="left")
        sums, counts = r.sum(), r.count()
        return sums[counts >= (7 if freq != "D" else 1)]

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        s = full_bins(df.set_index("date")["actual"].astype(float))
        for d, v in s.items():
            points.setdefault(d.date().isoformat(), {})["actual"] = round(v, 0)
    run = forecasting_service.latest_run(store)
    if run and run.get("artifact_path") and run.get("metrics"):
        from pathlib import Path

        from app.ml.forecasting.pipeline import ProductModelBundle

        bt = None
        for pid in run["metrics"]["per_product"]:
            p = Path(run["artifact_path"]) / f"product_{pid}.joblib"
            if not p.exists():
                continue
            b = ProductModelBundle.load(p)
            ser = pd.Series(b.holdout[b.best_model], index=pd.to_datetime(b.holdout["dates"]))
            bt = ser if bt is None else bt.add(ser, fill_value=0)
        if bt is not None:
            bt = bt[(bt.index >= pd.Timestamp(start)) & (bt.index <= pd.Timestamp(end))]
            if not bt.empty:
                for d, v in full_bins(bt).items():
                    points.setdefault(d.date().isoformat(), {})["backtest"] = round(v, 0)
    fc = forecasting_service.latest_forecast_frame(store, 90)
    if not fc.empty:
        f = fc.groupby("forecast_date")["predicted_units"].sum()
        f.index = pd.to_datetime(f.index)
        horizon = min(90, max(14, (end - start).days // 2))
        f = f.iloc[:horizon]
        for d, v in full_bins(f).items():
            points.setdefault(d.date().isoformat(), {})["forecast"] = round(v, 0)
    series = [{"date": k, **v} for k, v in sorted(points.items())]
    return {"granularity": "daily" if freq == "D" else "weekly", "series": series,
            "note": "backtest = model predictions on the chronological holdout (never seen in training)"}


def distribution(store: Store) -> dict:
    ctx, snap = inv.snapshot(store)
    status = snap["status"].value_counts().to_dict()
    inv_dist = [
        {"name": "Healthy", "key": "healthy", "value": int(status.get("healthy", 0))},
        {"name": "Low stock", "key": "low", "value": int(status.get("low", 0) + status.get("critical", 0) + status.get("out_of_stock", 0))},
        {"name": "Overstocked", "key": "overstock", "value": int(status.get("overstock", 0))},
    ]
    wh = snap.groupby(["warehouse_id", "warehouse_code", "warehouse_name"]).agg(
        inventory_value=("inventory_value", "sum"), units=("current_stock", "sum"), incoming=("incoming_units", "sum")).reset_index()
    caps = dict(zip(ctx.warehouses["warehouse_id"], ctx.warehouses["capacity_units"]))
    wh["capacity_units"] = wh["warehouse_id"].map(caps)
    wh["utilization_pct"] = (wh["units"] / wh["capacity_units"] * 100).round(1)
    perf = supplier_service.performance(store)["suppliers"]
    return {
        "inventory_status": inv_dist,
        "abc": snap.drop_duplicates("product_id")["abc_class"].value_counts().sort_index().to_dict(),
        "warehouses": inv.records(wh),
        "suppliers": [{"name": s["name"], "code": s["code"], "on_time_rate": round((s["on_time_rate"] or 0) * 100, 1),
                       "performance_score": round(s["performance_score"], 1), "risk_category": s["risk_category"]} for s in perf],
    }


def alerts(store: Store, limit: int = 20) -> list[dict]:
    ctx, snap = inv.snapshot(store)
    out = inv.alerts(snap, limit=100)
    for s in supplier_service.performance(store)["suppliers"]:
        late = 1 - (s["recent_on_time_rate"] if s["recent_on_time_rate"] is not None else s["on_time_rate"] or 1)
        if late >= 0.4:
            out.append({"severity": "warning", "type": "supplier_delay", "supplier_id": s["supplier_id"], "value": late,
                        "message": f"Supplier {s['name']} has a delivery delay rate of {late * 100:.0f}% over the last 90 days"})
    prod = snap.groupby(["sku", "name"]).agg(fc=("predicted_demand_30d", "sum"), last=("units_last_30d", "sum")).reset_index()
    for r in prod.itertuples():
        if r.last and (r.fc - r.last) / r.last >= 0.15:
            out.append({"severity": "info", "type": "demand_increase", "sku": r.sku, "value": (r.fc - r.last) / r.last,
                        "message": f"Demand for {r.sku} ({r.name}) is expected to increase by {(r.fc - r.last) / r.last * 100:.0f}% "
                                   f"over the next 30 days"})
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda a: order[a["severity"]])[:limit]


def recent_activity(store: Store, limit: int = 15) -> dict:
    names = {u.id: u.full_name for u in user_service.list_users(store)}
    acts = activity.recent(store, limit)
    po = store.read("purchase_orders").sort_values(["order_date", "id"], ascending=False).head(8)
    items = store.read("purchase_order_items")
    value = (items["quantity"] * items["unit_cost"]).groupby(items["purchase_order_id"]).sum()
    sup = store.read("suppliers")
    whs = store.read("warehouses")
    sup_name, wh_code = dict(zip(sup["id"], sup["name"])), dict(zip(whs["id"], whs["code"]))
    return {
        "activities": [{"id": i, "action": a["action"], "entity_type": a["entity_type"], "description": a["description"],
                        "user": names.get(a.get("user_id"), "System"), "created_at": a["created_at"]} for i, a in enumerate(acts)],
        "purchase_orders": [{"po_number": p.po_number, "supplier": sup_name.get(p.supplier_id), "warehouse": wh_code.get(p.warehouse_id),
                             "order_date": p.order_date.isoformat(), "expected_date": p.expected_date.isoformat(),
                             "status": p.status, "source": p.source, "total_cost": round(float(value.get(p.id, 0.0)), 2)}
                            for p in po.itertuples()],
    }
