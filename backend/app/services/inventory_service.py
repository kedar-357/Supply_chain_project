"""Inventory analytics engine.

`load_context` reads everything needed from the database once; `compute_snapshot` is a pure
function over that context that returns one row per product × warehouse with demand, lead time,
safety stock, ROP, EOQ, ABC class, stock projection and status. The scenario simulator calls the
same function with overrides (demand multipliers, supplier delays, capacity cuts), so the what-if
results are produced by exactly the same calculations as the operational views.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.core.exceptions import NotFoundError, PreconditionFailed, ValidationFailed
from app.optimization.inventory import formulas as F
from app.optimization.inventory.order_lp import OrderCandidate, solve
from app.services import forecasting_service, settings_service
from app.services.activity import log_activity
from app.storage.store import Store, utcnow_iso

OPT_DOC = "optimizations"
OPT_RUNS_KEPT = 25

FORECAST_HORIZON = 90


@dataclass
class InventoryContext:
    as_of: date
    items: pd.DataFrame  # product × warehouse
    forecast_daily: dict[int, np.ndarray]  # product_id -> next-90-day forecast (all warehouses)
    forecast_source: str
    incoming: pd.DataFrame  # product_id, warehouse_id, supplier_id, qty, day_offset, po_number
    warehouses: pd.DataFrame
    supplier_lead: dict[int, tuple[float, float]]  # supplier_id -> (mean, std) actual lead time
    params: dict = field(default_factory=dict)


# ====================================================================== context
def load_context(store: Store) -> InventoryContext:
    params = settings_service.get(store, "inventory")
    end = forecasting_service.data_end_date(store)
    if end is None:
        raise PreconditionFailed("No sales data available. Import data or run the initialisation script first.")
    as_of = end + timedelta(days=1)
    window = int(params["demand_window_days"])

    products = store.read("products")
    products = products[products["is_active"]]
    whs = store.read("warehouses")
    inv = store.read("inventory").merge(
        products.rename(columns={"id": "product_id", "primary_supplier_id": "supplier_id"})[
            ["product_id", "sku", "name", "category", "unit_cost", "selling_price", "ordering_cost", "holding_cost_rate", "supplier_id"]],
        on="product_id",
    ).merge(whs.rename(columns={"id": "warehouse_id", "code": "warehouse_code", "name": "warehouse_name"})[
        ["warehouse_id", "warehouse_code", "warehouse_name"]], on="warehouse_id")
    inv = inv.rename(columns={"quantity_on_hand": "on_hand"}).drop(columns=["as_of_date"])
    if inv.empty:
        raise PreconditionFailed("No inventory records available.")
    inv["supplier_id"] = inv["supplier_id"].astype("float")  # nullable → float for NaN-safe maps

    # Demand statistics per product × warehouse over the window (zero-filled daily series)
    sales_all = store.read("sales")
    start = as_of - timedelta(days=window)
    sales = sales_all[sales_all["date"] >= start]
    g = sales.groupby(["product_id", "warehouse_id"])["units_sold"]
    stats = pd.DataFrame({"total": g.sum().astype(float), "sumsq": g.apply(lambda x: float((x.astype(float) ** 2).sum()))}).reset_index()
    stats["avg_daily_demand"] = stats["total"] / window
    stats["sd_daily_demand"] = np.sqrt(np.maximum(stats["sumsq"] / window - stats["avg_daily_demand"] ** 2, 0))
    last30 = sales[sales["date"] >= as_of - timedelta(days=30)].groupby(["product_id", "warehouse_id"])["units_sold"].sum()
    stats = stats.merge(last30.rename("units_last_30d").reset_index(), on=["product_id", "warehouse_id"], how="left")
    inv = inv.merge(stats[["product_id", "warehouse_id", "avg_daily_demand", "sd_daily_demand", "units_last_30d", "total"]],
                    on=["product_id", "warehouse_id"], how="left").fillna({"avg_daily_demand": 0, "sd_daily_demand": 0,
                                                                            "units_last_30d": 0, "total": 0})
    prod_total = inv.groupby("product_id")["total"].transform("sum")
    n_wh = inv.groupby("product_id")["warehouse_id"].transform("count")
    inv["warehouse_share"] = np.where(prod_total > 0, inv["total"] / prod_total.replace(0, 1), 1 / n_wh)

    # Annual consumption value (ABC) – product level, last 365 days
    annual = sales_all[sales_all["date"] >= as_of - timedelta(days=365)].groupby("product_id")["units_sold"].sum()
    inv["annual_units"] = inv["product_id"].map(annual).fillna(0).astype(float)

    # Primary supplier terms
    sp = store.read("supplier_products").rename(columns={"unit_cost": "supplier_unit_cost", "lead_time_days": "quoted_lead_days",
                                                         "min_order_qty": "moq"})
    sp["supplier_id"] = sp["supplier_id"].astype(float)
    inv = inv.merge(sp, on=["supplier_id", "product_id"], how="left")
    inv["supplier_unit_cost"] = inv["supplier_unit_cost"].fillna(inv["unit_cost"])
    inv["moq"] = inv["moq"].fillna(1).astype(int)
    inv["quoted_lead_days"] = inv["quoted_lead_days"].fillna(7)
    sup = store.read("suppliers")
    inv["supplier_name"] = inv["supplier_id"].map(dict(zip(sup["id"].astype(float), sup["name"])))

    # Actual supplier lead times (order → receipt), last 365 days
    po = store.read("purchase_orders")
    dl = store.read("deliveries")
    lt = dl[dl["actual_date"] >= as_of - timedelta(days=365)].merge(
        po[["id", "order_date"]].rename(columns={"id": "purchase_order_id"}), on="purchase_order_id")
    supplier_lead = {}
    if not lt.empty:
        lt["lt"] = (pd.to_datetime(lt["actual_date"]) - pd.to_datetime(lt["order_date"])).dt.days
        for sid, grp in lt.groupby("supplier_id"):
            if len(grp) >= 5:
                supplier_lead[int(sid)] = (float(grp["lt"].mean()), float(grp["lt"].std()))

    # Open purchase orders (incoming stock)
    open_po = po[po["status"].isin(["placed", "in_transit"])]
    inc = store.read("purchase_order_items").merge(
        open_po[["id", "warehouse_id", "supplier_id", "expected_date", "po_number"]].rename(columns={"id": "purchase_order_id"}),
        on="purchase_order_id").rename(columns={"quantity": "qty"})
    inc["day_offset"] = [max(0, (d - as_of).days) for d in inc["expected_date"]] if len(inc) else []

    # Forecasts (latest run, recommended model per product); fall back to historical average
    fc = forecasting_service.latest_forecast_frame(store, FORECAST_HORIZON)
    forecast_daily: dict[int, np.ndarray] = {}
    source = "historical average (no trained forecast model)"
    if not fc.empty:
        source = "ML forecast (recommended model per product)"
        for pid, grp in fc.groupby("product_id"):
            grp = grp[pd.to_datetime(grp["forecast_date"]) >= pd.Timestamp(as_of)]
            arr = grp["predicted_units"].to_numpy(float)
            if len(arr) < FORECAST_HORIZON:  # extend with the mean if the stored horizon is shorter
                arr = np.concatenate([arr, np.full(FORECAST_HORIZON - len(arr), arr.mean() if len(arr) else 0)])
            forecast_daily[int(pid)] = arr[:FORECAST_HORIZON]

    wh = whs.rename(columns={"id": "warehouse_id"})[["warehouse_id", "code", "name", "city", "capacity_units"]]
    return InventoryContext(as_of=as_of, items=inv, forecast_daily=forecast_daily, forecast_source=source,
                            incoming=inc, warehouses=wh, supplier_lead=supplier_lead, params=params)


# ====================================================================== snapshot
def compute_snapshot(ctx: InventoryContext, *, demand_multiplier: float | dict[int, float] = 1.0,
                     supplier_delay_days: dict[int, int] | None = None, service_level: float | None = None,
                     ordering_cost: float | None = None, holding_cost_rate: float | None = None,
                     lead_time_override: float | None = None) -> pd.DataFrame:
    p = ctx.params
    sl = service_level or float(p["service_level"])
    review = float(p["review_period_days"])
    over_mult = float(p["overstock_cover_multiplier"])
    delays = supplier_delay_days or {}
    rows = []
    inc_groups = {k: g for k, g in ctx.incoming.groupby(["product_id", "warehouse_id"])} if not ctx.incoming.empty else {}

    for r in ctx.items.itertuples(index=False):
        mult = demand_multiplier.get(r.product_id, 1.0) if isinstance(demand_multiplier, dict) else demand_multiplier
        extra_delay = delays.get(int(r.supplier_id), 0) if not pd.isna(r.supplier_id) else 0
        lt_mean, lt_sd = ctx.supplier_lead.get(int(r.supplier_id) if not pd.isna(r.supplier_id) else -1,
                                               (float(r.quoted_lead_days), 0.0))
        if lead_time_override is not None:
            lt_mean, lt_sd = float(lead_time_override), 0.0
        lead = lt_mean + extra_delay

        fc = ctx.forecast_daily.get(r.product_id)
        if fc is not None:
            daily = fc * r.warehouse_share * mult
        else:
            daily = np.full(FORECAST_HORIZON, r.avg_daily_demand * mult)
        horizon_days = max(1, int(math.ceil(lead + review)))
        d = float(np.mean(daily[:min(horizon_days, len(daily))]))  # planning demand rate
        sd = float(r.sd_daily_demand) * mult
        ss = F.safety_stock(sl, d, sd, lead, lt_sd)
        rop = F.reorder_point(d, lead, ss)
        hold_rate = holding_cost_rate if holding_cost_rate is not None else float(r.holding_cost_rate)
        h = float(r.supplier_unit_cost) * hold_rate
        s_cost = ordering_cost if ordering_cost is not None else float(r.ordering_cost)
        eoq = F.eoq(d * 365, s_cost, h) if h > 0 else 0.0

        arrivals: dict[int, float] = {}
        incoming_units = 0.0
        g = inc_groups.get((r.product_id, r.warehouse_id))
        if g is not None:
            for q, off, sid in zip(g["qty"], g["day_offset"], g["supplier_id"]):
                off = int(off) + delays.get(int(sid), 0)
                arrivals[off] = arrivals.get(off, 0) + float(q)
                incoming_units += float(q)
        levels, stockout_day = F.project_stock(r.on_hand, daily, arrivals)
        ip = r.on_hand + incoming_units
        lead_int = max(1, int(math.ceil(lead)))
        # unmet demand before a replenishment ordered today could arrive
        unmet_before_replenishment = max(0.0, -float(levels[:lead_int].min())) if len(levels) else 0.0
        cover_days = r.on_hand / d if d > 0 else float("inf")
        overstock_threshold = max(rop + eoq, over_mult * d * (lead + review))

        if r.on_hand <= 0:
            status = "out_of_stock"
        elif stockout_day is not None and stockout_day < lead:
            status = "critical"
        elif ip <= rop:
            status = "low"
        elif r.on_hand > overstock_threshold and d > 0:
            status = "overstock"
        else:
            status = "healthy"

        rows.append({
            "product_id": r.product_id, "warehouse_id": r.warehouse_id, "sku": r.sku, "name": r.name,
            "category": r.category, "warehouse_code": r.warehouse_code, "warehouse_name": r.warehouse_name,
            "supplier_id": None if pd.isna(r.supplier_id) else int(r.supplier_id), "supplier_name": r.supplier_name,
            "current_stock": int(r.on_hand), "incoming_units": int(incoming_units), "inventory_position": float(ip),
            "avg_daily_demand": float(r.avg_daily_demand) * mult, "sd_daily_demand": sd,
            "forecast_daily_demand": d, "predicted_demand_30d": float(daily[:30].sum()),
            "units_last_30d": float(r.units_last_30d),
            "lead_time_days": lead, "lead_time_sd_days": lt_sd, "safety_stock": ss, "reorder_point": rop,
            "eoq": eoq, "moq": int(r.moq), "unit_cost": float(r.supplier_unit_cost), "selling_price": float(r.selling_price),
            "ordering_cost": s_cost, "holding_cost_per_unit_year": h,
            "inventory_value": float(r.on_hand) * float(r.unit_cost),
            "days_of_cover": None if math.isinf(cover_days) else cover_days,
            "stockout_in_days": stockout_day, "unmet_units_before_replenishment": unmet_before_replenishment,
            "revenue_at_risk": unmet_before_replenishment * float(r.selling_price),
            "overstock_threshold": overstock_threshold,
            "excess_units": max(0.0, r.on_hand - overstock_threshold) if status == "overstock" else 0.0,
            "annual_units": float(r.annual_units), "product_unit_cost": float(r.unit_cost), "status": status,
            "predicted_stockout": stockout_day is not None and stockout_day <= lead + review,
            "demand_change_pct": ((float(daily[:30].sum()) - float(r.units_last_30d)) / float(r.units_last_30d) * 100)
                                 if r.units_last_30d else None,
        })
    snap = pd.DataFrame(rows)
    acv = snap.groupby("product_id").apply(lambda g: g["annual_units"].iloc[0] * g["product_unit_cost"].iloc[0],
                                           include_groups=False)
    abc = F.abc_classify(acv)
    snap["abc_class"] = snap["product_id"].map(abc)
    snap["annual_consumption_value"] = snap["product_id"].map(acv)
    return snap


# ====================================================================== optimisation
def build_candidates(snap: pd.DataFrame, review_days: float) -> list[OrderCandidate]:
    cands = []
    for r in snap.itertuples(index=False):
        target = r.reorder_point + r.forecast_daily_demand * review_days
        need = max(0.0, target - r.inventory_position)
        if need <= 0:
            continue
        lot = max(need, r.eoq, r.moq)
        cands.append(OrderCandidate(
            key=(r.product_id, r.warehouse_id), need=need, lot=float(math.ceil(lot)), moq=int(r.moq),
            unit_cost=r.unit_cost, ordering_cost=r.ordering_cost, margin=r.selling_price - r.unit_cost,
            abc=r.abc_class or "C", urgent=r.status in ("critical", "out_of_stock"),
        ))
    return cands


def free_capacity(snap: pd.DataFrame, warehouses: pd.DataFrame, capacity_factor: float = 1.0) -> dict[int, float]:
    used = snap.groupby("warehouse_id")[["current_stock", "incoming_units"]].sum().sum(axis=1)
    return {int(w.warehouse_id): float(w.capacity_units * capacity_factor - used.get(w.warehouse_id, 0))
            for w in warehouses.itertuples()}


def optimize_orders(ctx: InventoryContext, snap: pd.DataFrame, budget: float | None,
                    capacity_factor: float = 1.0, capacity_override: dict[int, float] | None = None) -> tuple[pd.DataFrame, dict]:
    review = float(ctx.params["review_period_days"])
    cands = build_candidates(snap, review)
    cap = capacity_override if capacity_override is not None else free_capacity(snap, ctx.warehouses, capacity_factor)
    sol = solve(cands, budget, cap)
    by_key = {(r.product_id, r.warehouse_id): r for r in snap.itertuples(index=False)}
    out = []
    for c in cands:
        r = by_key[c.key]
        qty = sol.quantities.get(c.key, 0)
        reasons = []
        if r.status in ("critical", "out_of_stock"):
            reasons.append(f"Stockout projected in {r.stockout_in_days} days, before a new order can arrive "
                           f"(lead time {r.lead_time_days:.1f} d)" if r.stockout_in_days is not None else "Out of stock")
        elif r.inventory_position <= r.reorder_point:
            reasons.append(f"Low stock: inventory position {r.inventory_position:.0f} ≤ reorder point {r.reorder_point:.0f}")
        else:
            reasons.append(f"Will reach reorder point within the {review:.0f}-day review period")
        if r.demand_change_pct is not None and r.demand_change_pct >= 10:
            reasons.append(f"Demand increase: next-30-day forecast +{r.demand_change_pct:.0f}% vs last 30 days")
        unmet = sol.unmet_need.get(c.key, 0)
        if qty == 0:
            reasons.append("Not funded: lower priority under the budget/capacity limits")
        elif unmet > 0.5:
            reasons.append(f"Partially funded ({unmet:.0f} units of need left uncovered by budget/capacity)")
        elif qty > c.need + 0.5:
            reasons.append(f"Rounded up to economic lot (EOQ {r.eoq:.0f}, MOQ {r.moq})")
        out.append({
            "product_id": c.key[0], "warehouse_id": c.key[1], "sku": r.sku, "name": r.name, "category": r.category,
            "warehouse_code": r.warehouse_code, "supplier_id": r.supplier_id, "supplier_name": r.supplier_name,
            "abc_class": r.abc_class, "status": r.status, "current_stock": r.current_stock,
            "incoming_units": r.incoming_units, "reorder_point": round(r.reorder_point, 1),
            "target_level": round(r.reorder_point + r.forecast_daily_demand * review, 1),
            "need_units": round(c.need, 1), "eoq": round(r.eoq, 1), "moq": r.moq, "recommended_qty": qty,
            "unit_cost": r.unit_cost, "order_cost": round(qty * r.unit_cost, 2), "unmet_units": round(unmet, 1),
            "reason": "; ".join(reasons),
        })
    df = pd.DataFrame(out)
    summary = {
        "solver_status": sol.status, "candidates": len(cands),
        "items_ordered": int((df["recommended_qty"] > 0).sum()) if not df.empty else 0,
        "total_units": int(df["recommended_qty"].sum()) if not df.empty else 0,
        "total_order_cost": sol.total_cost, "budget": budget,
        "unmet_need_units": round(float(df["unmet_units"].sum()), 1) if not df.empty else 0.0,
        "free_capacity": cap,
    }
    return df, summary


# ====================================================================== service functions
def snapshot(store: Store, **overrides) -> tuple[InventoryContext, pd.DataFrame]:
    ctx = load_context(store)
    return ctx, compute_snapshot(ctx, **overrides)


def records(df: pd.DataFrame) -> list[dict]:
    def clean(v):
        if isinstance(v, (np.floating, float)):
            return None if (np.isnan(v) or np.isinf(v)) else round(float(v), 2)
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.bool_):
            return bool(v)
        return v
    return [{k: clean(v) for k, v in r.items()} for r in df.to_dict(orient="records")]


def run_optimization(store: Store, *, budget: float | None, user_id: int | None, service_level: float | None = None,
                     ordering_cost: float | None = None, holding_cost_rate: float | None = None,
                     lead_time_override: float | None = None, capacity_factor: float = 1.0) -> dict:
    if budget is not None and budget < 0:
        raise ValidationFailed("budget must be non-negative")
    ctx = load_context(store)
    snap = compute_snapshot(ctx, service_level=service_level, ordering_cost=ordering_cost,
                            holding_cost_rate=holding_cost_rate, lead_time_override=lead_time_override)
    plan, summary = optimize_orders(ctx, snap, budget, capacity_factor)
    holder: dict = {}

    def add(runs):
        runs = runs or []
        opt_id = max((r["optimization_id"] for r in runs), default=0) + 1
        if not plan.empty:
            plan["item_id"] = range(1, len(plan) + 1)
        run = {
            "optimization_id": opt_id, "created_at": utcnow_iso(), "created_by_id": user_id,
            "params": {"budget": budget, "service_level": service_level or ctx.params["service_level"],
                       "ordering_cost": ordering_cost, "holding_cost_rate": holding_cost_rate,
                       "lead_time_override": lead_time_override, "capacity_factor": capacity_factor,
                       "forecast_source": ctx.forecast_source},
            "summary": {k: v for k, v in summary.items() if k != "free_capacity"},
            "items": records(plan.sort_values("order_cost", ascending=False)) if not plan.empty else [],
        }
        holder["run"] = run
        return (runs + [run])[-OPT_RUNS_KEPT:]

    store.update_json(OPT_DOC, [], add)
    run = holder["run"]
    log_activity(store, user_id=user_id, action="optimize", entity_type="inventory_optimization", entity_id=run["optimization_id"],
                 description=f"Inventory optimization: {summary['items_ordered']} items, "
                             f"₹{summary['total_order_cost']:,.0f} ({summary['solver_status']})")
    return {**run, "summary": summary, "forecast_source": ctx.forecast_source}


def latest_optimization(store: Store) -> dict | None:
    runs = store.read_json(OPT_DOC, []) or []
    return runs[-1] if runs else None


def create_draft_purchase_orders(store: Store, optimization_id: int, item_ids: list[int] | None, user_id: int | None) -> list[dict]:
    """Groups recommended lines by supplier × warehouse into DRAFT purchase orders (appended to
    purchase_orders.csv / purchase_order_items.csv). Nothing is sent to suppliers."""
    opt = next((r for r in (store.read_json(OPT_DOC, []) or []) if r["optimization_id"] == optimization_id), None)
    if opt is None:
        raise NotFoundError("Optimization run not found")
    items = [i for i in opt["items"] if i["recommended_qty"] > 0 and (not item_ids or i.get("item_id") in item_ids)]
    if not items:
        raise ValidationFailed("No recommended quantities to convert into purchase orders")
    as_of = (forecasting_service.data_end_date(store) or date.today()) + timedelta(days=1)
    sp = store.read("supplier_products")
    lead = sp.groupby("supplier_id")["lead_time_days"].max().to_dict()
    groups: dict[tuple[int, int], list[dict]] = {}
    for i in items:
        if i.get("supplier_id") is None:
            continue
        groups.setdefault((int(i["supplier_id"]), int(i["warehouse_id"])), []).append(i)
    created = []
    with store.lock:
        po = store.read("purchase_orders")
        lines_df = store.read("purchase_order_items")
        next_id = int(po["id"].max()) + 1 if len(po) else 1
        new_heads, new_lines = [], []
        for (sid, wid), lines in groups.items():
            number = f"DRAFT-{as_of:%Y%m%d}-{next_id:05d}"
            new_heads.append({"id": next_id, "po_number": number, "supplier_id": sid, "warehouse_id": wid, "order_date": as_of,
                              "expected_date": as_of + timedelta(days=int(lead.get(sid, 7))), "status": "draft",
                              "source": "optimizer", "created_by_id": user_id, "is_simulated": False,
                              "notes": f"Draft generated from optimization run #{optimization_id}. Review before placing."})
            new_lines += [{"purchase_order_id": next_id, "product_id": int(l["product_id"]), "quantity": int(l["recommended_qty"]),
                           "unit_cost": float(l["unit_cost"])} for l in lines]
            created.append({"po_id": next_id, "po_number": number, "supplier_id": sid, "warehouse_id": wid, "lines": len(lines),
                            "total_cost": round(sum(l["recommended_qty"] * l["unit_cost"] for l in lines), 2)})
            next_id += 1
        store.write("purchase_orders", pd.concat([po, pd.DataFrame(new_heads)], ignore_index=True))
        store.write("purchase_order_items", pd.concat([lines_df, pd.DataFrame(new_lines)], ignore_index=True))
    log_activity(store, user_id=user_id, action="create_draft_po", entity_type="purchase_order",
                 description=f"{len(created)} draft purchase orders created from optimization #{optimization_id}")
    return created


def _clean_num(x) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def alerts(snap: pd.DataFrame, limit: int = 50) -> list[dict]:
    out = []
    for r in snap.itertuples(index=False):
        if r.status in ("out_of_stock", "critical"):
            msg = (f"{r.sku} ({r.name}) at {r.warehouse_code} is out of stock" if r.status == "out_of_stock" else
                   f"{r.sku} ({r.name}) at {r.warehouse_code} is expected to run out in {r.stockout_in_days} days "
                   f"— before replenishment can arrive (lead time {r.lead_time_days:.0f} d)")
            out.append({"severity": "critical", "type": "stockout", "message": msg, "sku": r.sku,
                        "product_id": r.product_id, "warehouse_code": r.warehouse_code, "value": r.stockout_in_days})
        elif r.status == "low":
            out.append({"severity": "warning", "type": "low_stock", "sku": r.sku, "product_id": r.product_id,
                        "warehouse_code": r.warehouse_code, "value": r.current_stock,
                        "message": f"{r.sku} at {r.warehouse_code} is below its reorder point "
                                   f"(position {r.inventory_position:.0f} vs ROP {r.reorder_point:.0f})"})
        elif r.status == "overstock":
            out.append({"severity": "info", "type": "overstock", "sku": r.sku, "product_id": r.product_id,
                        "warehouse_code": r.warehouse_code, "value": r.excess_units,
                        "message": f"{r.sku} at {r.warehouse_code} has excess inventory: {r.excess_units:.0f} units "
                                   f"above threshold ({r.days_of_cover:.0f} days of cover)"})
    for a in out:
        a["value"] = _clean_num(a.get("value"))
        a["product_id"] = int(a["product_id"])
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda a: (order[a["severity"]], a["value"] if a["value"] is not None else -1))[:limit]
