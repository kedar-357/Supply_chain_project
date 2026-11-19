"""Demand forecasting orchestration: CSV data ⇄ ML pipeline, model registry, joblib artifacts, stored forecasts."""
from __future__ import annotations

import logging
import shutil
import traceback
from datetime import date
from pathlib import Path

import pandas as pd

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, PreconditionFailed
from app.ml.forecasting.features import daily_series
from app.ml.forecasting.models import MODEL_LABELS
from app.ml.forecasting.pipeline import HOLDOUT_DAYS, ProductModelBundle, aggregate_metrics, future_frame, train_product
from app.ml.forecasting.pipeline import predict as ml_predict
from app.services import model_registry as registry
from app.services.activity import log_activity
from app.storage.store import Store, utcnow_iso

log = logging.getLogger("supplychainx.forecasting")
MODEL_TYPE = "demand_forecast"
DEFAULT_STORED_HORIZON = 90
RUNS_WITH_STORED_FORECASTS = 3  # older runs' forecast rows are pruned from demand_forecasts.csv


# ------------------------------------------------------------------ data access
def product_daily_sales(store: Store, product_ids: list[int] | None = None) -> pd.DataFrame:
    s = store.read("sales")
    if product_ids:
        s = s[s["product_id"].isin(product_ids)]
    if s.empty:
        return pd.DataFrame(columns=["product_id", "date", "units_sold", "discount_pct", "promotion"])
    g = (s.groupby(["product_id", "date"])
         .agg(units_sold=("units_sold", "sum"), discount_pct=("discount_pct", "mean"), promotion=("promotion", "max"))
         .reset_index().sort_values(["product_id", "date"]))
    g["units_sold"] = g["units_sold"].astype(float)
    return g


def data_end_date(store: Store) -> date | None:
    s = store.read("sales")
    return None if s.empty else max(s["date"])


def series_for(store: Store, product_id: int) -> pd.DataFrame:
    df = product_daily_sales(store, [product_id])
    if df.empty:
        raise NotFoundError("No sales history for this product")
    return daily_series(df, pd.Timestamp(data_end_date(store)))


# ------------------------------------------------------------------ model runs
def latest_run(store: Store, status: str | None = "completed") -> dict | None:
    return registry.latest(store, MODEL_TYPE, status)


def start_run(store: Store, user_id: int | None, product_ids: list[int] | None = None) -> dict:
    run = registry.start(store, MODEL_TYPE, {
        "holdout_days": HOLDOUT_DAYS, "models": list(MODEL_LABELS), "split": "chronological",
        "forecast_strategy": "recursive multi-step", "product_ids": product_ids,
    }, user_id, exclusive=True)
    log_activity(store, user_id=user_id, action="train", entity_type="model_run", entity_id=run["id"],
                 description=f"Demand forecasting training v{run['version']} started")
    return run


def execute_run(store: Store, run_id: int) -> dict:
    """Trains every product, persists joblib artifacts and stores default forecasts. Safe for background use."""
    run = registry.get(store, run_id)
    out_dir = get_settings().artifacts_dir / "forecasting" / f"v{run['version']}"
    try:
        products = store.read("products")
        products = products[products["is_active"]]
        sku = dict(zip(products["id"], products["sku"]))
        wanted = run["params"].get("product_ids") or list(sku)
        sales = product_daily_sales(store, wanted)
        end = pd.Timestamp(data_end_date(store))
        bundles, skipped, rows = [], [], []
        for pid, grp in sales.groupby("product_id"):
            if pid not in sku:
                continue
            series = daily_series(grp, end)
            try:
                b = train_product(int(pid), sku[pid], series)
            except ValueError as exc:
                skipped.append({"product_id": int(pid), "reason": str(exc)})
                continue
            b.save(out_dir)
            bundles.append(b)
            rows += _forecast_rows(run_id, b, series, DEFAULT_STORED_HORIZON)
        if not bundles:
            raise ValueError("No product had enough history to train")
        _save_forecast_rows(store, rows, replace_run=run_id)
        metrics = {
            "portfolio": aggregate_metrics(bundles),
            "per_product": {str(b.product_id): {"sku": b.sku, "best_model": b.best_model, **b.metrics} for b in bundles},
            "skipped": skipped,
            "trained_through": end.date().isoformat(),
            "holdout_days": HOLDOUT_DAYS,
            "global_feature_importance": _global_importance(bundles),
        }
        run = registry.finish(store, run_id, status="completed", metrics=metrics, artifact_path=str(out_dir))
        log_activity(store, user_id=run["triggered_by_id"], action="train", entity_type="model_run", entity_id=run_id,
                     description=f"Demand forecasting v{run['version']} completed for {len(bundles)} products")
        log.info("Forecast run v%s completed (%d products)", run["version"], len(bundles))
    except Exception as exc:  # record failure instead of leaving the run "running"
        shutil.rmtree(out_dir, ignore_errors=True)
        run = registry.finish(store, run_id, status="failed", error=f"{exc}\n{traceback.format_exc(limit=3)}")
        log.exception("Forecast run failed")
    return run


def train(store: Store, user_id: int | None, product_ids: list[int] | None = None) -> dict:
    run = start_run(store, user_id, product_ids)
    return execute_run(store, run["id"])


def _global_importance(bundles: list[ProductModelBundle]) -> dict[str, float]:
    acc: dict[str, float] = {}
    for b in bundles:
        for k, v in b.feature_importance.items():
            acc[k] = acc.get(k, 0) + v / len(bundles)
    return {k: round(v, 4) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])}


def _forecast_rows(run_id: int, bundle: ProductModelBundle, series: pd.DataFrame, horizon: int, models=None) -> list[dict]:
    now = utcnow_iso()
    rows = []
    for name in models or bundle.models:
        res = ml_predict(bundle, series, horizon, name)
        for i, d in enumerate(res.index):
            rows.append({"run_id": run_id, "product_id": bundle.product_id, "model_name": name, "forecast_date": d.date(),
                         "predicted_units": float(res.point[i]),
                         "lower_bound": None if res.lower is None else float(res.lower[i]),
                         "upper_bound": None if res.upper is None else float(res.upper[i]), "generated_at": now})
    return rows


def _save_forecast_rows(store: Store, rows: list[dict], replace_run: int | None = None) -> None:
    with store.lock:
        cur = store.read("demand_forecasts")
        if replace_run is not None:
            cur = cur[cur["run_id"] != replace_run]
            keep_runs = sorted(cur["run_id"].unique())[-(RUNS_WITH_STORED_FORECASTS - 1):] if len(cur) else []
            cur = cur[cur["run_id"].isin(keep_runs)]
        store.write("demand_forecasts", pd.concat([cur, pd.DataFrame(rows)], ignore_index=True)
                    .drop_duplicates(["run_id", "product_id", "model_name", "forecast_date"], keep="last"))


def load_bundle(run: dict, product_id: int) -> ProductModelBundle:
    path = Path(run["artifact_path"]) / f"product_{product_id}.joblib"
    if not path.exists():
        raise PreconditionFailed("No trained model for this product in the latest run. Train models first.")
    return ProductModelBundle.load(path)


def _product(store: Store, product_id: int) -> dict:
    p = store.read("products")
    row = p[p["id"] == product_id]
    if row.empty:
        raise NotFoundError("Product not found")
    return row.iloc[0].to_dict()


# ------------------------------------------------------------------ prediction & reads
def predict(store: Store, product_id: int, horizon: int, model_name: str | None, user_id: int | None) -> dict:
    run = latest_run(store)
    if run is None:
        raise PreconditionFailed("No trained forecasting model yet. Run training first.")
    product = _product(store, product_id)
    bundle = load_bundle(run, product_id)
    series = series_for(store, product_id)
    chosen = model_name or bundle.best_model
    if chosen not in bundle.models:
        raise NotFoundError(f"Unknown model '{chosen}'")
    res = ml_predict(bundle, series, horizon, chosen)
    _save_forecast_rows(store, _forecast_rows(run["id"], bundle, series, horizon, [chosen]))
    log_activity(store, user_id=user_id, action="predict", entity_type="forecast", entity_id=product_id,
                 description=f"{horizon}-day {MODEL_LABELS[chosen]} forecast for {product['sku']}")

    hist = series.iloc[-180:]
    explanation = None
    if chosen == "xgboost":
        explanation = bundle.models["xgboost"].explain_first_step(series, future_frame(res.index[0], horizon))
    return {
        "product": {"id": product_id, "sku": product["sku"], "name": product["name"], "category": product["category"]},
        "model": chosen,
        "model_label": MODEL_LABELS[chosen],
        "recommended_model": bundle.best_model,
        "run_version": run["version"],
        "trained_through": bundle.trained_through.date().isoformat(),
        "history": [{"date": d.date().isoformat(), "units": float(v)} for d, v in hist["units"].items()],
        "forecast": [
            {"date": d.date().isoformat(), "predicted": round(float(res.point[i]), 2),
             "lower": None if res.lower is None else round(float(res.lower[i]), 2),
             "upper": None if res.upper is None else round(float(res.upper[i]), 2)}
            for i, d in enumerate(res.index)
        ],
        "total_predicted": round(float(res.point.sum()), 1),
        "holdout": bundle.holdout,
        "metrics": bundle.metrics,
        "feature_importance": bundle.feature_importance,
        "explanation": explanation,
        "interval_method": {
            "xgboost": "Empirical 95% band from chronological-holdout residuals at the same horizon",
            "sarima": "Model-based 95% confidence interval (statsmodels)",
            "seasonal_naive": "No interval (naive benchmark)",
        }[chosen],
        "assumptions": "Future days assume no promotion and no discount unless a promotion plan is supplied.",
    }


def latest_forecast_frame(store: Store, horizon_days: int = 90, use_best: bool = True) -> pd.DataFrame:
    """Stored forecasts of the latest completed run; each product uses its recommended model."""
    cols = ["product_id", "forecast_date", "predicted_units", "lower_bound", "upper_bound", "model_name"]
    run = latest_run(store)
    if run is None:
        return pd.DataFrame(columns=cols)
    df = store.read("demand_forecasts")
    df = df[df["run_id"] == run["id"]][cols]
    if use_best and run.get("metrics"):
        best = {int(k): v["best_model"] for k, v in run["metrics"].get("per_product", {}).items()}
        df = df[df["model_name"] == df["product_id"].map(best)]
    df = df.sort_values(["product_id", "forecast_date"])
    return df.groupby("product_id").head(horizon_days)


def forecast_totals(store: Store, horizon_days: int) -> dict[int, float]:
    df = latest_forecast_frame(store, horizon_days)
    return df.groupby("product_id")["predicted_units"].sum().to_dict() if not df.empty else {}


def history(store: Store, product_id: int | None = None, limit: int = 50) -> list[dict]:
    df = store.read("demand_forecasts")
    if product_id:
        df = df[df["product_id"] == product_id]
    if df.empty:
        return []
    g = (df.groupby(["product_id", "model_name", "run_id"])
         .agg(start=("forecast_date", "min"), end=("forecast_date", "max"), total=("predicted_units", "sum"),
              generated_at=("generated_at", "max")).reset_index()
         .sort_values("generated_at", ascending=False).head(limit))
    skus = dict(zip(*[store.read("products")[c] for c in ("id", "sku")]))
    return [{"product_id": int(r.product_id), "sku": skus.get(r.product_id), "model": r.model_name, "run_id": int(r.run_id),
             "from": r.start.isoformat(), "to": r.end.isoformat(), "total_units": round(float(r.total), 1),
             "generated_at": r.generated_at} for r in g.itertuples()]


def run_summary(run: dict | None) -> dict | None:
    if run is None:
        return None
    return {k: run.get(k) for k in ("id", "version", "status", "model_type", "started_at", "finished_at", "params")} | {
        "error": (run.get("error") or "").split("\n")[0] or None}


def metrics(store: Store) -> dict:
    run = latest_run(store)
    current = latest_run(store, status=None)
    if run is None:
        return {"latest_run": run_summary(current), "trained": False}
    m = run["metrics"]
    return {
        "trained": True,
        "latest_run": run_summary(current),
        "completed_run": run_summary(run),
        "portfolio": m["portfolio"],
        "per_product": m["per_product"],
        "trained_through": m["trained_through"],
        "holdout_days": m["holdout_days"],
        "global_feature_importance": dict(sorted(m["global_feature_importance"].items(), key=lambda kv: -kv[1])),
        "model_labels": MODEL_LABELS,
        "runs": [run_summary(r) for r in sorted(registry.runs(store, MODEL_TYPE), key=lambda r: -r["version"])[:10]],
    }


# ------------------------------------------------------------------ EDA
def seasonal_trends(store: Store, category: str | None = None) -> dict:
    """Average daily demand by month and by weekday, per category (exploratory analysis)."""
    s = store.read("sales")
    p = store.read("products")[["id", "category"]].rename(columns={"id": "product_id"})
    df = s.merge(p, on="product_id")
    if category:
        df = df[df["category"] == category]
    if df.empty:
        return {"monthly": [], "weekday": [], "categories": []}
    daily = df.groupby(["category", "date"])["units_sold"].sum().reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["month"] = daily["date"].dt.month
    daily["dow"] = daily["date"].dt.dayofweek
    m = daily.groupby(["month", "category"])["units_sold"].mean().unstack().round(1)
    w = daily.groupby(["dow", "category"])["units_sold"].mean().unstack().round(1)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "categories": list(m.columns),
        "monthly": [{"month": months[i - 1], **row.to_dict()} for i, row in m.iterrows()],
        "weekday": [{"day": days[i], **row.to_dict()} for i, row in w.iterrows()],
    }


def product_comparison(store: Store, days: int = 90) -> list[dict]:
    end = data_end_date(store)
    if end is None:
        return []
    start = (pd.Timestamp(end) - pd.Timedelta(days=days - 1)).date()
    s = store.read("sales")
    units = s[s["date"] >= start].groupby("product_id")["units_sold"].sum()
    p = store.read("products")
    fc = forecast_totals(store, days)
    out = []
    for r in p.itertuples():
        if r.id not in units.index:
            continue
        last, nxt = float(units[r.id]), fc.get(r.id)
        out.append({"product_id": int(r.id), "sku": r.sku, "name": r.name, "category": r.category,
                    "last_period_units": int(last), "next_period_forecast": None if nxt is None else round(nxt, 1),
                    "change_pct": None if not nxt or not last else round((nxt - last) / last * 100, 1)})
    return sorted(out, key=lambda r: -r["last_period_units"])
