"""Supplier analytics: historical performance scoring (descriptive) and delay prediction (ML)."""
from __future__ import annotations

import logging
import traceback
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, PreconditionFailed, ValidationFailed
from app.ml.supplier_prediction import pipeline as delay_ml
from app.ml.supplier_prediction.features import build_frame
from app.services import model_registry as registry
from app.services import settings_service
from app.services.activity import log_activity
from app.storage.store import Store
from app.utils.geo import road_distance_km

log = logging.getLogger("supplychainx.suppliers")
MODEL_TYPE = "delivery_delay"


# ================================================================== data
def _orders_frame(store: Store, delivered_only: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    po = store.read("purchase_orders")
    po = po[~po["status"].isin(["cancelled", "draft"])].rename(columns={"id": "po_id"})[
        ["po_id", "po_number", "supplier_id", "warehouse_id", "order_date", "expected_date", "status"]]
    items = store.read("purchase_order_items").merge(
        store.read("products")[["id", "category"]].rename(columns={"id": "product_id"}), on="product_id"
    ).rename(columns={"purchase_order_id": "po_id"})[["po_id", "quantity", "category"]]
    dl = store.read("deliveries").rename(columns={"purchase_order_id": "po_id"})[
        ["po_id", "supplier_id", "actual_date", "delay_days", "distance_km"]]
    sup = store.read("suppliers").rename(columns={"id": "supplier_id", "latitude": "s_lat", "longitude": "s_lon"})[
        ["supplier_id", "region", "s_lat", "s_lon"]]
    wh = store.read("warehouses").rename(columns={"id": "warehouse_id", "latitude": "w_lat", "longitude": "w_lon"})[
        ["warehouse_id", "w_lat", "w_lon"]]
    if po.empty or items.empty:
        return pd.DataFrame(), dl
    agg = items.groupby("po_id").agg(total_quantity=("quantity", "sum"), n_lines=("quantity", "size"),
                                     category=("category", lambda x: x.mode().iloc[0]))
    f = po.merge(agg, left_on="po_id", right_index=True).merge(sup, on="supplier_id").merge(wh, on="warehouse_id")
    f = f.merge(dl[["po_id", "delay_days", "distance_km"]], on="po_id", how="left")
    est = [road_distance_km(a, b, c, d) for a, b, c, d in zip(f.s_lat, f.s_lon, f.w_lat, f.w_lon)]
    f["distance_km"] = f["distance_km"].fillna(pd.Series(est, index=f.index))
    f["quoted_lead_days"] = (pd.to_datetime(f["expected_date"]) - pd.to_datetime(f["order_date"])).dt.days
    if delivered_only:
        f = f[f["delay_days"].notna()]
    f["late"] = (f["delay_days"] > 0).astype(int)
    return f, dl


def _row(store: Store, table: str, id_: int) -> dict | None:
    df = store.read(table)
    r = df[df["id"] == id_]
    return None if r.empty else r.iloc[0].to_dict()


# ================================================================== training
def latest_delay_run(store: Store, status: str | None = "completed") -> dict | None:
    return registry.latest(store, MODEL_TYPE, status)


def train_delay_model(store: Store, user_id: int | None) -> dict:
    run = registry.start(store, MODEL_TYPE, {"split": "chronological 80/20 by order_date",
                                             "candidates": list(delay_ml.LABELS)}, user_id)
    try:
        orders, deliveries = _orders_frame(store)
        frame = build_frame(orders, deliveries)
        bundle = delay_ml.train(frame)
        path = get_settings().artifacts_dir / "supplier_delay" / f"v{run['version']}.joblib"
        bundle.save(path)
        run = registry.finish(store, run["id"], status="completed", artifact_path=str(path), metrics={
            "models": bundle.metrics, "deployed_model": bundle.model_name, "feature_importance": bundle.feature_importance,
            "calibration": bundle.calibration, **bundle.train_period, "labels": delay_ml.LABELS})
        log_activity(store, user_id=user_id, action="train", entity_type="model_run", entity_id=run["id"],
                     description=f"Delivery-delay classifier v{run['version']} trained ({delay_ml.LABELS[bundle.model_name]}, "
                                 f"test ROC-AUC {bundle.metrics[bundle.model_name]['roc_auc']})")
    except Exception as exc:
        run = registry.finish(store, run["id"], status="failed", error=f"{exc}\n{traceback.format_exc(limit=3)}")
        log.exception("Delay model training failed")
    return run


def _bundle(store: Store) -> tuple[dict, delay_ml.DelayModelBundle]:
    run = latest_delay_run(store)
    if run is None:
        raise PreconditionFailed("Delivery-delay model has not been trained yet.")
    return run, delay_ml.DelayModelBundle.load(Path(run["artifact_path"]))


def delay_model_metrics(store: Store) -> dict:
    run = latest_delay_run(store)
    current = latest_delay_run(store, status=None)
    if run is None:
        return {"trained": False, "status": current["status"] if current else None}
    out = {"trained": True, "version": run["version"], "finished_at": run["finished_at"],
           "status": current["status"] if current else run["status"], **run["metrics"]}
    out["feature_importance"] = dict(sorted(out.get("feature_importance", {}).items(), key=lambda kv: -kv[1]))
    return out


# ================================================================== prediction
def predict_delay(store: Store, *, supplier_id: int, warehouse_id: int, quantity: int, product_id: int | None,
                  order_date: date | None, user_id: int | None) -> dict:
    run, bundle = _bundle(store)
    sup = _row(store, "suppliers", supplier_id)
    wh = _row(store, "warehouses", warehouse_id)
    if sup is None or wh is None:
        raise NotFoundError("Supplier or warehouse not found")
    if quantity <= 0:
        raise ValidationFailed("quantity must be positive")
    od = order_date or date.today()
    sp = store.read("supplier_products")
    category, lead = "Unknown", None
    if product_id:
        prod = _row(store, "products", product_id)
        if prod is None:
            raise NotFoundError("Product not found")
        category = prod["category"]
        match = sp[(sp["supplier_id"] == supplier_id) & (sp["product_id"] == product_id)]
        lead = int(match["lead_time_days"].iloc[0]) if len(match) else None
    if lead is None:
        own = sp[sp["supplier_id"] == supplier_id]["lead_time_days"]
        lead = float(own.mean()) if len(own) else 10
    orders = pd.DataFrame([{
        "supplier_id": supplier_id, "region": sup["region"], "category": category, "order_date": od,
        "total_quantity": quantity, "n_lines": 1,
        "distance_km": road_distance_km(sup["latitude"], sup["longitude"], wh["latitude"], wh["longitude"]),
        "quoted_lead_days": int(lead),
    }])
    _, deliveries = _orders_frame(store)
    frame = build_frame(orders, deliveries)
    res = delay_ml.predict_with_explanation(bundle, frame)[0]
    hist = frame.iloc[0]
    log_activity(store, user_id=user_id, action="predict", entity_type="supplier", entity_id=supplier_id,
                 description=f"Delay risk predicted for {sup['name']}: {res['delay_probability']:.0%}")
    return {
        **res,
        "supplier": {"id": supplier_id, "name": sup["name"], "code": sup["code"]},
        "model": delay_ml.LABELS[bundle.model_name], "model_version": run["version"],
        "inputs": {"order_date": od.isoformat(), "quantity": quantity, "category": category,
                   "distance_km": round(float(hist["distance_km"]), 1), "quoted_lead_days": int(lead),
                   "supplier_history_late_rate": round(float(hist["sup_prior_late_rate"]), 3),
                   "supplier_history_deliveries": int(hist["sup_prior_count"])},
        "kind": "ml_prediction",
    }


def open_order_risk(store: Store) -> list[dict]:
    """ML delay risk for purchase orders that are placed / in transit."""
    run = latest_delay_run(store)
    if run is None:
        return []
    bundle = delay_ml.DelayModelBundle.load(Path(run["artifact_path"]))
    orders, deliveries = _orders_frame(store, delivered_only=False)
    if orders.empty:
        return []
    open_o = orders[orders["status"].isin(["placed", "in_transit"])].copy()
    if open_o.empty:
        return []
    frame = build_frame(open_o.reset_index(drop=True), deliveries)
    preds = delay_ml.predict_with_explanation(bundle, frame, top_k=2)
    names = dict(zip(*[store.read("suppliers")[c] for c in ("id", "name")]))
    out = []
    for (_, r), p in zip(frame.iterrows(), preds):
        out.append({"po_number": r["po_number"], "supplier": names.get(r["supplier_id"]), "supplier_id": int(r["supplier_id"]),
                    "order_date": str(r["order_date"]), "expected_date": str(r["expected_date"]),
                    "quantity": int(r["total_quantity"]), **p})
    return sorted(out, key=lambda x: -x["delay_probability"])


# ================================================================== historical performance (descriptive)
def performance(store: Store, weights: dict | None = None, since: date | None = None) -> dict:
    w = weights or settings_service.get(store, "supplier_weights")
    total_w = sum(w.values())
    if total_w <= 0:
        raise ValidationFailed("Weights must sum to a positive number")
    w = {k: float(v) / total_w for k, v in w.items()}

    orders, _ = _orders_frame(store, delivered_only=False)
    po = store.read("purchase_orders")
    dl = store.read("deliveries").merge(po[["id", "order_date"]].rename(columns={"id": "purchase_order_id"}), on="purchase_order_id")
    if since is not None and not dl.empty:
        dl = dl[pd.to_datetime(dl["actual_date"]) >= pd.Timestamp(since)]
    sups = list(store.read("suppliers").sort_values("code").itertuples())
    sp = store.read("supplier_products").rename(columns={"lead_time_days": "lead_time", "min_order_qty": "moq"})
    sp["cost_index"] = sp["unit_cost"] / sp.groupby("product_id")["unit_cost"].transform("min")
    items = store.read("purchase_order_items").merge(
        po[po["status"].isin(["placed", "in_transit", "received"])][["id", "supplier_id"]].rename(columns={"id": "purchase_order_id"}),
        on="purchase_order_id")
    spend_map = (items["quantity"] * items["unit_cost"]).groupby(items["supplier_id"]).sum().to_dict()

    end = pd.to_datetime(dl["actual_date"]).max() if not dl.empty else pd.Timestamp.today()
    rows = []
    for s in sups:
        d = dl[dl["supplier_id"] == s.id]
        lead_actual = (pd.to_datetime(d["actual_date"]) - pd.to_datetime(d["order_date"])).dt.days
        ad = pd.to_datetime(d["actual_date"])
        recent = d[ad > end - pd.Timedelta(days=90)]
        last_year = d[(ad > end - pd.Timedelta(days=455)) & (ad <= end - pd.Timedelta(days=365))]
        spi = sp[sp["supplier_id"] == s.id]
        n_po = int((orders["supplier_id"] == s.id).sum()) if not orders.empty else 0
        rows.append({
            "supplier_id": s.id, "code": s.code, "name": s.name, "city": s.city, "region": s.region,
            "latitude": s.latitude, "longitude": s.longitude, "is_simulated": s.is_simulated,
            "products_supplied": int(spi["product_id"].nunique()),
            "purchase_order_count": n_po, "deliveries": int(len(d)),
            "on_time_rate": float((d["delay_days"] <= 0).mean()) if len(d) else None,
            "avg_delay_days": float(d.loc[d["delay_days"] > 0, "delay_days"].mean()) if (d["delay_days"] > 0).any() else 0.0,
            "avg_lead_time_days": float(lead_actual.mean()) if len(d) else None,
            "lead_time_std_days": float(lead_actual.std()) if len(d) > 1 else None,
            "lead_time_cv": float(lead_actual.std() / lead_actual.mean()) if len(d) > 1 and lead_actual.mean() else None,
            "avg_quality_score": float(d["quality_score"].mean()) if len(d) else None,
            "quality_rating": s.quality_rating,
            "avg_unit_cost_index": float(spi["cost_index"].mean()) if len(spi) else None,
            "avg_quoted_lead_time": float(spi["lead_time"].mean()) if len(spi) else None,
            "total_spend": round(spend_map.get(s.id, 0.0), 2),
            "recent_on_time_rate": float((recent["delay_days"] <= 0).mean()) if len(recent) else None,
            "recent_deliveries": int(len(recent)),
            "same_period_last_year_on_time_rate": float((last_year["delay_days"] <= 0).mean()) if len(last_year) else None,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return {"weights": w, "suppliers": []}

    def norm(col: str, higher_is_better: bool) -> pd.Series:
        s = df[col].astype(float)
        lo, hi = s.min(), s.max()
        if pd.isna(lo) or hi == lo:
            return pd.Series(1.0, index=df.index)
        x = (s - lo) / (hi - lo)
        return (x if higher_is_better else 1 - x).fillna(0)

    df["score_on_time"] = norm("on_time_rate", True)
    df["score_quality"] = norm("avg_quality_score", True)
    df["score_cost"] = norm("avg_unit_cost_index", False)
    df["score_lead_time_consistency"] = norm("lead_time_cv", False)
    df["performance_score"] = 100 * (
        w["on_time"] * df["score_on_time"] + w["quality"] * df["score_quality"]
        + w["cost"] * df["score_cost"] + w["lead_time_consistency"] * df["score_lead_time_consistency"]
    )
    df["rank"] = df["performance_score"].rank(ascending=False, method="min").astype(int)

    # Risk (ML): mean predicted delay probability for a typical next order from each supplier
    ml = _typical_order_risk(store, df)
    df["ml_delay_probability"] = df["supplier_id"].map(ml)
    df["risk_category"] = df["ml_delay_probability"].map(lambda p: None if pd.isna(p) else delay_ml.risk_category(p))
    # Year-over-year comparison of the same 90-day window, so seasonal effects (monsoon, festive
    # season) are not mistaken for a change in supplier performance.
    df["trend"] = [
        None if pd.isna(r.recent_on_time_rate) or pd.isna(r.same_period_last_year_on_time_rate)
        else ("deteriorating" if r.recent_on_time_rate < r.same_period_last_year_on_time_rate - 0.10 else
              "improving" if r.recent_on_time_rate > r.same_period_last_year_on_time_rate + 0.10 else "stable")
        for r in df.itertuples()
    ]
    df = df.sort_values("rank")
    return {
        "weights": w,
        "methodology": "Each metric is min-max normalised across suppliers (0 = worst, 1 = best); "
                       "cost and lead-time variability are inverted so that lower is better. "
                       "Score = 100 × Σ weight × normalised metric. Scores are historical (descriptive); "
                       "delay probability is an ML prediction for a typical next order.",
        "suppliers": _clean(df.to_dict(orient="records")),
    }


def _typical_order_risk(store: Store, perf: pd.DataFrame) -> dict[int, float]:
    run = latest_delay_run(store)
    if run is None:
        return {}
    bundle = delay_ml.DelayModelBundle.load(Path(run["artifact_path"]))
    orders, deliveries = _orders_frame(store)
    if orders.empty:
        return {}
    out = {}
    today = date.today()
    for sid, g in orders.groupby("supplier_id"):
        recent = g.sort_values("order_date").tail(25).copy()
        recent["order_date"] = today  # same orders, placed now → uses all history to date
        frame = build_frame(recent.reset_index(drop=True), deliveries)
        out[int(sid)] = float(bundle.model.predict_proba(frame[delay_ml.NUMERIC + delay_ml.CATEGORICAL])[:, 1].mean())
    return out


def _clean(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        out.append({k: (None if isinstance(v, float) and np.isnan(v) else (round(v, 4) if isinstance(v, float) else v))
                    for k, v in r.items()})
    return out


def delivery_history(store: Store, supplier_id: int | None = None, months: int = 24) -> list[dict]:
    df = store.read("deliveries")[["supplier_id", "actual_date", "delay_days", "quality_score"]]
    if supplier_id:
        df = df[df["supplier_id"] == supplier_id]
    if df.empty:
        return []
    df["month"] = pd.to_datetime(df["actual_date"]).dt.to_period("M").astype(str)
    g = df.groupby("month").agg(deliveries=("delay_days", "size"), on_time_rate=("delay_days", lambda s: (s <= 0).mean()),
                                avg_delay=("delay_days", "mean"), quality=("quality_score", "mean")).reset_index()
    return g.tail(months).round(3).to_dict(orient="records")


def compare(store: Store, supplier_ids: list[int], product_id: int | None = None) -> dict:
    if len(supplier_ids) < 2:
        raise ValidationFailed("Select at least two suppliers to compare")
    perf = {s["supplier_id"]: s for s in performance(store)["suppliers"]}
    missing = [i for i in supplier_ids if i not in perf]
    if missing:
        raise NotFoundError(f"Unknown supplier id(s): {missing}")
    rows = [perf[i] for i in supplier_ids]
    if product_id:
        sp = store.read("supplier_products")
        sp = sp[sp["product_id"] == product_id]
        costs = dict(zip(sp["supplier_id"], sp["unit_cost"]))
        for r in rows:
            c = costs.get(r["supplier_id"])
            r["product_unit_cost"] = None if c is None else float(c)
    return {"suppliers": rows, "recommendation": _tradeoff_text(rows, product_id is not None)}


def _tradeoff_text(rows: list[dict], has_product: bool) -> dict:
    best = max(rows, key=lambda r: r["performance_score"])
    cost_key = "product_unit_cost" if has_product and all(r.get("product_unit_cost") for r in rows) else "avg_unit_cost_index"
    cheapest = min(rows, key=lambda r: r[cost_key] or 1e9)
    most_reliable = max(rows, key=lambda r: r["on_time_rate"] or 0)
    points = []
    if cheapest["supplier_id"] != best["supplier_id"]:
        diff = (best[cost_key] - cheapest[cost_key]) / cheapest[cost_key] * 100
        ot_gap = ((best["on_time_rate"] or 0) - (cheapest["on_time_rate"] or 0)) * 100
        points.append(
            f"{cheapest['name']} is the cheapest option ({diff:.1f}% lower cost than {best['name']}), but its on-time rate is "
            f"{ot_gap:.0f} percentage points lower and its late deliveries average {cheapest['avg_delay_days']:.1f} days. "
            "The saving must be weighed against higher safety stock and stockout risk."
        )
    if most_reliable["supplier_id"] != best["supplier_id"]:
        points.append(f"{most_reliable['name']} has the best delivery reliability "
                      f"({(most_reliable['on_time_rate'] or 0) * 100:.0f}% on time) — prefer it for A-class or time-critical items.")
    for r in rows:
        if r.get("risk_category") == "High":
            points.append(f"{r['name']} is classified High risk by the delay model "
                          f"({r['ml_delay_probability'] * 100:.0f}% predicted delay probability); avoid single-sourcing from it.")
        if r.get("trend") == "deteriorating":
            points.append(f"{r['name']}'s on-time rate in the last 90 days is lower than in the same period last year.")
    return {
        "recommended_supplier_id": best["supplier_id"],
        "summary": f"{best['name']} offers the best overall balance (score {best['performance_score']:.1f}/100) "
                   f"across reliability, quality, cost and lead-time consistency.",
        "trade_offs": points,
    }
