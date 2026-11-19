"""Dashboard, forecasting, inventory and supplier analytics endpoints."""
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.api.deps import get_current_user, require, store_dep
from app.core.security import Perm
from app.schemas.common import (
    DelayPredictIn,
    DraftPOIn,
    ForecastPredictIn,
    ForecastTrainIn,
    OptimizeIn,
    SupplierCompareIn,
    SupplierIn,
)
from app.services import dashboard_service, forecasting_service, inventory_service, supplier_service
from app.services.activity import log_activity
from app.services.user_service import User
from app.storage.store import Store, get_store

dashboard = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])
forecast = APIRouter(prefix="/api/forecast", tags=["Demand forecasting"])
inventory = APIRouter(prefix="/api/inventory", tags=["Inventory optimization"])
suppliers = APIRouter(prefix="/api/suppliers", tags=["Supplier analytics"])

VIEW = Depends(require(Perm.VIEW_ANALYTICS))


# ====================================================================== dashboard
def _range(store, days, start, end):
    return dashboard_service.resolve_range(store, days, start, end)


@dashboard.get("/summary")
def summary(days: int | None = Query(30, ge=1, le=730), start: date | None = None, end: date | None = None,
            store: Store = Depends(store_dep), _: User = VIEW):
    return dashboard_service.summary(store, *_range(store, days, start, end))


@dashboard.get("/trends")
def trends(days: int | None = Query(180, ge=7, le=730), start: date | None = None, end: date | None = None,
           store: Store = Depends(store_dep), _: User = VIEW):
    return dashboard_service.trends(store, *_range(store, days, start, end))


@dashboard.get("/alerts")
def alerts(limit: int = Query(20, ge=1, le=100), store: Store = Depends(store_dep), _: User = VIEW):
    return dashboard_service.alerts(store, limit)


@dashboard.get("/distribution")
def distribution(store: Store = Depends(store_dep), _: User = VIEW):
    return dashboard_service.distribution(store)


@dashboard.get("/activity")
def activity(store: Store = Depends(store_dep), _: User = VIEW):
    return dashboard_service.recent_activity(store)


# ====================================================================== forecasting
def _train_in_background(run_id: int) -> None:
    forecasting_service.execute_run(get_store(), run_id)


@forecast.post("/train", status_code=202)
def train(body: ForecastTrainIn, background: BackgroundTasks, store: Store = Depends(store_dep),
          user: User = Depends(require(Perm.TRAIN_MODELS))):
    """Starts a training run in the background; poll `/api/forecast/status`."""
    run = forecasting_service.start_run(store, user.id, body.product_ids)
    background.add_task(_train_in_background, run["id"])
    return forecasting_service.run_summary(run)


@forecast.get("/status")
def status(store: Store = Depends(store_dep), _: User = VIEW):
    return {"latest": forecasting_service.run_summary(forecasting_service.latest_run(store, status=None)),
            "completed": forecasting_service.run_summary(forecasting_service.latest_run(store))}


@forecast.post("/predict")
def predict(body: ForecastPredictIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.RUN_PREDICTIONS))):
    return forecasting_service.predict(store, body.product_id, body.horizon_days, body.model, user.id)


@forecast.get("/history")
def history(product_id: int | None = None, limit: int = Query(50, ge=1, le=500), store: Store = Depends(store_dep), _: User = VIEW):
    return forecasting_service.history(store, product_id, limit)


@forecast.get("/metrics")
def metrics(store: Store = Depends(store_dep), _: User = VIEW):
    return forecasting_service.metrics(store)


@forecast.get("/seasonality")
def seasonality(category: str | None = None, store: Store = Depends(store_dep), _: User = VIEW):
    return forecasting_service.seasonal_trends(store, category)


@forecast.get("/comparison")
def comparison(days: int = Query(30, ge=7, le=90), store: Store = Depends(store_dep), _: User = VIEW):
    return forecasting_service.product_comparison(store, days)


# ====================================================================== inventory
@inventory.get("")
def list_inventory(status: str | None = None, warehouse_id: int | None = None, abc_class: str | None = None,
                   q: str | None = None, sort: str = "sku", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                   service_level: float | None = Query(None, ge=0.5, lt=1), ordering_cost: float | None = Query(None, ge=0),
                   holding_cost_rate: float | None = Query(None, gt=0, le=1), lead_time_days: float | None = Query(None, gt=0, le=180),
                   store: Store = Depends(store_dep), _: User = VIEW):
    """Per product × warehouse: demand, lead time, safety stock, ROP, EOQ, ABC class and status.
    The optional parameters let managers recalculate with different ordering/holding cost, lead time or service level."""
    ctx, snap = inventory_service.snapshot(store, service_level=service_level, ordering_cost=ordering_cost,
                                           holding_cost_rate=holding_cost_rate, lead_time_override=lead_time_days)
    counts = snap["status"].value_counts().to_dict()
    abc = snap.drop_duplicates("product_id")["abc_class"].value_counts().to_dict()
    df = snap
    if status:
        df = df[df["status"].isin(status.split(","))]
    if warehouse_id:
        df = df[df["warehouse_id"] == warehouse_id]
    if abc_class:
        df = df[df["abc_class"] == abc_class]
    if q:
        df = df[df["sku"].str.contains(q, case=False) | df["name"].str.contains(q, case=False)]
    desc = sort.startswith("-")
    key = sort.lstrip("-")
    if key in df.columns:
        df = df.sort_values(key, ascending=not desc, na_position="last")
    total = len(df)
    page_df = df.iloc[(page - 1) * page_size: page * page_size]
    return {"items": inventory_service.records(page_df), "total": total, "page": page, "page_size": page_size,
            "status_counts": counts, "abc_counts": abc, "as_of": ctx.as_of.isoformat(), "forecast_source": ctx.forecast_source,
            "parameters": {**ctx.params, **{k: v for k, v in {"service_level": service_level, "ordering_cost": ordering_cost,
                                                              "holding_cost_rate": holding_cost_rate, "lead_time_days": lead_time_days}.items() if v is not None}}}


@inventory.get("/abc")
def abc(store: Store = Depends(store_dep), _: User = VIEW):
    _, snap = inventory_service.snapshot(store)
    p = snap.drop_duplicates("product_id").sort_values("annual_consumption_value", ascending=False)
    total = p["annual_consumption_value"].sum()
    p = p.assign(cumulative_share=(p["annual_consumption_value"].cumsum() / total * 100).round(2),
                 value_share=(p["annual_consumption_value"] / total * 100).round(2))
    return {"items": inventory_service.records(p[["product_id", "sku", "name", "category", "annual_units", "product_unit_cost",
                                                   "annual_consumption_value", "value_share", "cumulative_share", "abc_class"]]),
            "method": "Annual consumption value = units sold in the last 365 days × unit cost; A ≤ 80% cumulative value, B ≤ 95%, C rest."}


@inventory.get("/alerts")
def inventory_alerts(store: Store = Depends(store_dep), _: User = VIEW):
    _, snap = inventory_service.snapshot(store)
    return inventory_service.alerts(snap, limit=200)


@inventory.post("/optimize")
def optimize(body: OptimizeIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.OPTIMIZE))):
    return inventory_service.run_optimization(store, budget=body.budget, user_id=user.id, service_level=body.service_level,
                                              ordering_cost=body.ordering_cost, holding_cost_rate=body.holding_cost_rate,
                                              lead_time_override=body.lead_time_days)


@inventory.get("/recommendations")
def recommendations(store: Store = Depends(store_dep), _: User = VIEW):
    return inventory_service.latest_optimization(store) or {"optimization_id": None, "items": []}


@inventory.post("/draft-purchase-orders", status_code=201)
def draft_pos(body: DraftPOIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.OPTIMIZE))):
    """Creates DRAFT purchase orders only. No order is sent to any supplier."""
    return inventory_service.create_draft_purchase_orders(store, body.optimization_id, body.item_ids, user.id)


# ====================================================================== suppliers
@suppliers.get("")
def list_suppliers(store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    df = store.read("suppliers").sort_values("code")
    return [{"id": int(r.id), "code": r.code, "name": r.name, "city": r.city, "region": r.region, "country": r.country,
             "latitude": r.latitude, "longitude": r.longitude, "quality_rating": r.quality_rating,
             "contact_email": r.contact_email or None, "is_active": bool(r.is_active), "is_simulated": bool(r.is_simulated)}
            for r in df.itertuples()]


@suppliers.post("", status_code=201)
def create_supplier(body: SupplierIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.MANAGE_MASTER_DATA))):
    import pandas as pd

    with store.lock:
        df = store.read("suppliers")
        if body.code in set(df["code"]):
            raise HTTPException(409, "Supplier code already exists")
        sid = store.next_id("suppliers")
        store.write("suppliers", pd.concat([df, pd.DataFrame([{**body.model_dump(), "id": sid, "is_simulated": False}])], ignore_index=True))
    log_activity(store, user_id=user.id, action="create", entity_type="supplier", entity_id=sid, description=f"Supplier {body.name} created")
    return {"id": sid, "code": body.code}


@suppliers.put("/{supplier_id}")
def update_supplier(supplier_id: int, body: SupplierIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.MANAGE_MASTER_DATA))):
    with store.lock:
        df = store.read("suppliers")
        if supplier_id not in set(df["id"]):
            raise HTTPException(404, "Supplier not found")
        for k, v in body.model_dump().items():
            df.loc[df["id"] == supplier_id, k] = v
        store.write("suppliers", df)
    log_activity(store, user_id=user.id, action="update", entity_type="supplier", entity_id=supplier_id, description=f"Supplier {body.name} updated")
    return {"id": supplier_id, "code": body.code}


@suppliers.get("/performance")
def performance(on_time: float | None = Query(None, ge=0), quality: float | None = Query(None, ge=0),
                cost: float | None = Query(None, ge=0), lead_time_consistency: float | None = Query(None, ge=0),
                store: Store = Depends(store_dep), _: User = VIEW):
    """Historical (descriptive) weighted score. Pass all four weights to override the configured ones."""
    weights = None
    if None not in (on_time, quality, cost, lead_time_consistency):
        weights = {"on_time": on_time, "quality": quality, "cost": cost, "lead_time_consistency": lead_time_consistency}
    return supplier_service.performance(store, weights)


@suppliers.get("/delivery-history")
def delivery_history(supplier_id: int | None = None, store: Store = Depends(store_dep), _: User = VIEW):
    return supplier_service.delivery_history(store, supplier_id)


@suppliers.post("/compare")
def compare(body: SupplierCompareIn, store: Store = Depends(store_dep), _: User = VIEW):
    return supplier_service.compare(store, body.supplier_ids, body.product_id)


@suppliers.post("/predict-delay")
def predict_delay(body: DelayPredictIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.RUN_PREDICTIONS))):
    """ML prediction (not historical score): probability that a new order will arrive late."""
    return supplier_service.predict_delay(store, supplier_id=body.supplier_id, warehouse_id=body.warehouse_id, quantity=body.quantity,
                                          product_id=body.product_id, order_date=body.order_date, user_id=user.id)


@suppliers.post("/train-delay-model")
def train_delay(store: Store = Depends(store_dep), user: User = Depends(require(Perm.TRAIN_MODELS))):
    run = supplier_service.train_delay_model(store, user.id)
    if run["status"] != "completed":
        raise HTTPException(500, f"Training failed: {(run.get('error') or 'unknown error').splitlines()[0]}")
    return supplier_service.delay_model_metrics(store)


@suppliers.get("/delay-model")
def delay_model(store: Store = Depends(store_dep), _: User = VIEW):
    return supplier_service.delay_model_metrics(store)


@suppliers.get("/open-order-risk")
def open_order_risk(store: Store = Depends(store_dep), _: User = VIEW):
    return supplier_service.open_order_risk(store)
