"""What-if scenario simulator.

Every scenario recomputes the *same* inventory snapshot / optimizer / logistics cost model used by the
operational modules, once with baseline inputs and once with modified inputs, from current database
records (CSV data files) and the latest stored forecasts. No random numbers are involved: re-running a scenario on
unchanged data gives identical results.
"""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

from app.core.exceptions import NotFoundError, ValidationFailed
from app.storage.store import Store, utcnow_iso
from app.services import inventory_service as inv
from app.services import logistics_service, settings_service
from app.services.activity import log_activity

SCENARIO_TYPES = {
    "supplier_delay": "Supplier delay",
    "demand_increase": "Sudden demand increase",
    "transport_cost": "Transportation cost increase",
    "budget_limit": "Limited procurement budget",
    "capacity_constraint": "Warehouse capacity constraint",
}

KPI_DEFS = [  # metric, label, unit, higher_is_better
    ("items_at_risk", "Items at stockout risk", "items", False),
    ("unmet_units", "Unmet demand before replenishment", "units", False),
    ("revenue_at_risk", "Revenue at risk", "INR", False),
    ("expected_service_level", "Expected service level", "%", True),
    ("replenishment_cost", "Required replenishment spend", "INR", False),
    ("monthly_logistics_cost", "Monthly logistics cost (est.)", "INR", False),
    ("monthly_operational_cost", "Monthly operational cost (est.)", "INR", False),
    ("profit_margin", "Operating profit margin (est.)", "%", True),
    ("inventory_value", "Inventory value", "INR", False),
]


# ====================================================================== shared KPI model
def _monthly_financials(store: Store) -> dict:
    sales = store.read("sales")
    end = max(sales["date"])
    recent = sales[sales["date"] >= end - timedelta(days=29)]
    cost = recent["product_id"].map(dict(zip(*[store.read("products")[c] for c in ("id", "unit_cost")])))
    return {"revenue_30d": float((recent["units_sold"] * recent["unit_price"]).sum()),
            "cogs_30d": float((recent["units_sold"] * cost).sum())}


def kpis(snap: pd.DataFrame, plan: pd.DataFrame, plan_summary: dict, logistics_cost: float, fin: dict,
         overhead: float, demand_factor: float = 1.0) -> dict:
    """Standard KPI set, identical for baseline and every scenario.

    * unmet units = demand that current + incoming stock cannot cover before a replenishment ordered
      today could arrive, plus replenishment need the plan leaves uncovered (budget/capacity) beyond
      the safety-stock buffer.
    * service level = 1 − unmet / (lead-time demand + replenishment need).
    """
    lead_demand = float((snap["forecast_daily_demand"] * snap["lead_time_days"]).sum())
    unmet_snap = float(snap["unmet_units_before_replenishment"].sum())
    rev_risk = float(snap["revenue_at_risk"].sum())
    at_risk = set(map(tuple, snap.loc[snap["predicted_stockout"], ["product_id", "warehouse_id"]].to_numpy()))
    need_total, uncovered_lost = 0.0, 0.0
    if plan is not None and not plan.empty:
        info = snap.set_index(["product_id", "warehouse_id"])[["safety_stock", "selling_price"]]
        need_total = float(plan["need_units"].sum())
        for r in plan.itertuples(index=False):
            if r.unmet_units <= 0.5:
                continue
            ss, price = info.loc[(r.product_id, r.warehouse_id)]
            lost = max(0.0, r.unmet_units - ss)
            uncovered_lost += lost
            rev_risk += lost * price
            if r.status in ("low", "critical", "out_of_stock"):
                at_risk.add((r.product_id, r.warehouse_id))
    unmet = unmet_snap + uncovered_lost
    holding_month = float((snap["current_stock"] * snap["holding_cost_per_unit_year"]).sum() / 12)
    revenue = fin["revenue_30d"] * demand_factor
    cogs = fin["cogs_30d"] * demand_factor
    op_cost = cogs + logistics_cost + holding_month + overhead
    realised_revenue = max(revenue - rev_risk, 0)
    denom = lead_demand + need_total
    return {
        "items_at_risk": len(at_risk),
        "unmet_units": round(unmet, 1),
        "revenue_at_risk": round(rev_risk, 2),
        "expected_service_level": round(100 * (1 - unmet / denom), 2) if denom > 0 else 100.0,
        "replenishment_cost": round(float(plan_summary["total_order_cost"]), 2),
        "monthly_logistics_cost": round(logistics_cost, 2),
        "monthly_operational_cost": round(op_cost, 2),
        "profit_margin": round(100 * (realised_revenue - op_cost) / realised_revenue, 2) if realised_revenue else 0.0,
        "inventory_value": round(float(snap["inventory_value"].sum()), 2),
    }


def _metric_rows(base: dict, scen: dict) -> list[dict]:
    rows = []
    for m, label, unit, hib in KPI_DEFS:
        b, s = base[m], scen[m]
        delta = s - b
        rows.append({"metric": m, "label": label, "unit": unit, "baseline": b, "scenario": s,
                     "change": round(delta, 2), "change_pct": round(delta / b * 100, 2) if b else None,
                     "favourable": None if abs(delta) < 1e-9 else (delta > 0) == hib})
    return rows


def _at_risk_table(base: pd.DataFrame, mod: pd.DataFrame, limit: int = 30) -> list[dict]:
    m = mod.merge(base[["product_id", "warehouse_id", "status", "stockout_in_days", "unmet_units_before_replenishment"]],
                  on=["product_id", "warehouse_id"], suffixes=("", "_base"))
    m = m[m["predicted_stockout"]].copy()
    m["newly_at_risk"] = ~m["status_base"].isin(["critical", "out_of_stock"]) & m["status"].isin(["critical", "out_of_stock"])
    m["additional_shortage"] = m["unmet_units_before_replenishment"] - m["unmet_units_before_replenishment_base"]
    m = m.sort_values(["additional_shortage", "revenue_at_risk"], ascending=False).head(limit)
    cols = ["sku", "name", "warehouse_code", "supplier_name", "abc_class", "current_stock", "incoming_units",
            "forecast_daily_demand", "lead_time_days", "stockout_in_days", "stockout_in_days_base", "status", "status_base",
            "newly_at_risk", "unmet_units_before_replenishment", "additional_shortage", "revenue_at_risk"]
    return inv.records(m[cols])


# ====================================================================== simulation
def simulate(store: Store, scenario_type: str, config: dict) -> dict:
    if scenario_type not in SCENARIO_TYPES:
        raise ValidationFailed(f"Unknown scenario type. Expected one of: {', '.join(SCENARIO_TYPES)}")
    ctx = inv.load_context(store)
    fin = _monthly_financials(store)
    overhead = float(settings_service.get(store, "finance")["overhead_cost_per_month"])
    base_snap = inv.compute_snapshot(ctx)
    base_plan, base_sum = inv.optimize_orders(ctx, base_snap, budget=None)
    base_log = logistics_service.distribution_cost_model(store, 1.0)
    base_k = kpis(base_snap, base_plan, base_sum, base_log["total_monthly_cost"], fin, overhead)
    handler = {
        "supplier_delay": _supplier_delay, "demand_increase": _demand_increase, "transport_cost": _transport_cost,
        "budget_limit": _budget_limit, "capacity_constraint": _capacity,
    }[scenario_type]
    scen_k, details, narrative = handler(store, ctx, config, base_snap, base_plan, base_sum, base_log, fin, overhead)
    return {
        "scenario_type": scenario_type, "scenario_label": SCENARIO_TYPES[scenario_type], "config": config,
        "as_of": ctx.as_of.isoformat(), "forecast_source": ctx.forecast_source,
        "metrics": _metric_rows(base_k, scen_k), "details": details, "narrative": narrative,
    }


def _supplier_delay(store, ctx, cfg, base_snap, base_plan, base_sum, base_log, fin, overhead):
    days = int(cfg.get("delay_days", 7))
    if days <= 0 or days > 90:
        raise ValidationFailed("delay_days must be between 1 and 90")
    sid = cfg.get("supplier_id")
    all_sup = store.read("suppliers")
    sup_ids = [int(sid)] if sid else [int(x) for x in all_sup["id"]]
    if sid and int(sid) not in set(all_sup["id"]):
        raise NotFoundError("Supplier not found")
    delays = {s: days for s in sup_ids}
    mod = inv.compute_snapshot(ctx, supplier_delay_days=delays)
    plan, summ = inv.optimize_orders(ctx, mod, budget=None)
    k = kpis(mod, plan, summ, base_log["total_monthly_cost"], fin, overhead)
    at_risk = _at_risk_table(base_snap, mod)

    # Alternative supplier requirements for items newly short because of the delay
    sp = store.read("supplier_products").rename(columns={"min_order_qty": "moq"})[
        ["product_id", "supplier_id", "unit_cost", "lead_time_days", "moq"]]
    names = dict(zip(all_sup["id"], all_sup["name"]))
    alts = []
    affected = mod.merge(base_snap[["product_id", "warehouse_id", "unmet_units_before_replenishment"]],
                         on=["product_id", "warehouse_id"], suffixes=("", "_base"))
    affected = affected[(affected["unmet_units_before_replenishment"] > affected["unmet_units_before_replenishment_base"] + 0.5)
                        & affected["supplier_id"].isin(sup_ids)]
    for r in affected.itertuples(index=False):
        options = sp[(sp["product_id"] == r.product_id) & (~sp["supplier_id"].isin(sup_ids))]
        for o in options.itertuples(index=False):
            lead_mean = ctx.supplier_lead.get(int(o.supplier_id), (float(o.lead_time_days), 0))[0]
            covers = r.stockout_in_days is None or lead_mean <= (r.stockout_in_days or 0)
            qty = max(int(math.ceil(r.unmet_units_before_replenishment - r.unmet_units_before_replenishment_base)), int(o.moq))
            alts.append({"sku": r.sku, "warehouse_code": r.warehouse_code, "delayed_supplier": r.supplier_name,
                         "alternative_supplier": names.get(o.supplier_id), "alternative_lead_time_days": round(lead_mean, 1),
                         "arrives_before_stockout": bool(covers), "quantity_needed": qty, "unit_cost": float(o.unit_cost),
                         "extra_cost_vs_primary": round((float(o.unit_cost) - r.unit_cost) * qty, 2)})
    newly = sum(1 for a in at_risk if a["newly_at_risk"])
    narrative = [
        f"A {days}-day delay from {'all suppliers' if not sid else names.get(int(sid))} puts {k['items_at_risk']} item-locations at "
        f"stockout risk (baseline {base_sum and int(base_snap['predicted_stockout'].sum())}); {newly} become critical.",
        f"Additional unmet demand before replenishment: {k['unmet_units'] - float(base_snap['unmet_units_before_replenishment'].sum()):,.0f} units, "
        f"putting an extra ₹{k['revenue_at_risk'] - float(base_snap['revenue_at_risk'].sum()):,.0f} of revenue at risk.",
    ]
    if alts:
        ok = [a for a in alts if a["arrives_before_stockout"]]
        narrative.append(f"{len(ok)} of {len(alts)} alternative sourcing options can arrive before the projected stockout.")
    return k, {"at_risk_items": at_risk, "alternative_suppliers": alts[:40]}, narrative


def _demand_increase(store, ctx, cfg, base_snap, base_plan, base_sum, base_log, fin, overhead):
    pct = float(cfg.get("increase_pct", 20))
    if not -90 < pct <= 300:
        raise ValidationFailed("increase_pct must be between -90 and 300")
    cat = cfg.get("category")
    mult: float | dict = 1 + pct / 100
    factor = mult
    if cat:
        ids = base_snap.loc[base_snap["category"] == cat, "product_id"].unique()
        if len(ids) == 0:
            raise NotFoundError(f"Unknown category '{cat}'")
        mult = {int(i): 1 + pct / 100 for i in ids}
        share = base_snap.loc[base_snap["category"] == cat, "units_last_30d"].sum() / max(base_snap["units_last_30d"].sum(), 1)
        factor = 1 + pct / 100 * share
    mod = inv.compute_snapshot(ctx, demand_multiplier=mult)
    plan, summ = inv.optimize_orders(ctx, mod, budget=None)
    log_cost = logistics_service.distribution_cost_model(store, 1.0)["total_monthly_cost"] * factor
    k = kpis(mod, plan, summ, log_cost, fin, overhead, demand_factor=factor)
    req = (mod["reorder_point"] + mod["forecast_daily_demand"] * float(ctx.params["review_period_days"])).sum()
    req_base = (base_snap["reorder_point"] + base_snap["forecast_daily_demand"] * float(ctx.params["review_period_days"])).sum()
    ss_delta = float(mod["safety_stock"].sum() - base_snap["safety_stock"].sum())
    narrative = [
        f"Demand {'+' if pct >= 0 else ''}{pct:.0f}%{' for ' + cat if cat else ''} raises target inventory levels by "
        f"{req - req_base:,.0f} units (safety stock +{ss_delta:,.0f}).",
        f"Additional purchasing required now: ₹{summ['total_order_cost'] - base_sum['total_order_cost']:,.0f} "
        f"({summ['items_ordered']} items to replenish vs {base_sum['items_ordered']} in the baseline).",
        f"Item-locations at stockout risk: {k['items_at_risk']} (baseline {int(base_snap['predicted_stockout'].sum())}).",
    ]
    replen = plan[plan["recommended_qty"] > 0].sort_values("order_cost", ascending=False) if not plan.empty else plan
    return k, {"revised_requirements": {"target_inventory_units": round(float(req), 0), "baseline_target_units": round(float(req_base), 0),
                                        "safety_stock_change_units": round(ss_delta, 0)},
               "recommended_replenishment": inv.records(replen.head(40)) if not replen.empty else [],
               "at_risk_items": _at_risk_table(base_snap, mod)}, narrative


def _transport_cost(store, ctx, cfg, base_snap, base_plan, base_sum, base_log, fin, overhead):
    pct = float(cfg.get("increase_pct", 15))
    if not -50 < pct <= 300:
        raise ValidationFailed("increase_pct must be between -50 and 300")
    mod_log = logistics_service.distribution_cost_model(store, 1 + pct / 100)
    k = kpis(base_snap, base_plan, base_sum, mod_log["total_monthly_cost"], fin, overhead)
    base_lanes = {l["store"]: l for l in base_log["lanes"]}
    lanes = []
    for l in mod_log["lanes"]:
        b = base_lanes.get(l["store"], {})
        lanes.append({"store": l["store"], "store_name": l["store_name"], "serving_warehouse": l["serving_warehouse"],
                      "baseline_route": " → ".join(b.get("route", [])), "new_route": " → ".join(l["route"]),
                      "route_changed": b.get("route") != l["route"], "baseline_monthly_cost": b.get("monthly_cost"),
                      "new_monthly_cost": l["monthly_cost"], "trips_per_month": l["trips_per_month"],
                      "utilisation": round(l["load_per_trip"] / mod_log["vehicle_capacity"], 2)
                      if l["trips_per_cycle"] else None})
    suggestions = []
    for l in lanes:
        if l["route_changed"]:
            suggestions.append(f"Re-route {l['store']} via {l['new_route']} — cheaper under the new tariff.")
        if l["utilisation"] is not None and l["utilisation"] < 0.5:
            suggestions.append(f"Consolidate deliveries to {l['store']} (vehicle utilisation only {l['utilisation']:.0%}); "
                               "combine with nearby stores using the multi-stop route optimizer.")
    for ie in logistics_service.inefficient(store)[:3]:
        if ie["potential_saving"]:
            suggestions.append(f"Avoid direct link {ie['from']} → {ie['to']}: a multi-hop alternative saves "
                               f"₹{ie['potential_saving']:,.0f} per trip.")
    delta = mod_log["total_monthly_cost"] - base_log["total_monthly_cost"]
    narrative = [
        f"A {pct:+.0f}% change in per-km transport tariffs changes estimated monthly logistics cost by ₹{delta:,.0f} "
        f"(₹{base_log['total_monthly_cost']:,.0f} → ₹{mod_log['total_monthly_cost']:,.0f}). Tolls are unchanged.",
        f"Estimated operating margin moves from {kpis(base_snap, base_plan, base_sum, base_log['total_monthly_cost'], fin, overhead)['profit_margin']:.2f}% "
        f"to {k['profit_margin']:.2f}%.",
    ]
    return k, {"lanes": lanes, "suggestions": suggestions[:10]}, narrative


def _budget_limit(store, ctx, cfg, base_snap, base_plan, base_sum, base_log, fin, overhead):
    budget = cfg.get("budget")
    if budget is None or float(budget) < 0:
        raise ValidationFailed("budget (non-negative) is required")
    budget = float(budget)
    plan, summ = inv.optimize_orders(ctx, base_snap, budget=budget)
    k = kpis(base_snap, plan, summ, base_log["total_monthly_cost"], fin, overhead)
    need_total = float(plan["need_units"].sum()) if not plan.empty else 0.0
    unmet_after = float(plan["unmet_units"].sum()) if not plan.empty else 0.0
    unfunded = plan[(plan["recommended_qty"] == 0)] if not plan.empty else plan
    narrative = [
        f"With a budget of ₹{budget:,.0f} the optimizer funds {summ['items_ordered']} of {summ['candidates']} replenishment "
        f"needs for ₹{summ['total_order_cost']:,.0f} (unconstrained plan: ₹{base_sum['total_order_cost']:,.0f}).",
        f"{unmet_after:,.0f} of {need_total:,.0f} needed units remain uncovered; priority went to items with the highest "
        "stockout urgency × margin × ABC weight per rupee.",
    ]
    if budget >= base_sum["total_order_cost"]:
        narrative.append("The budget covers the full unconstrained plan.")
    plan_sorted = plan.sort_values(["recommended_qty", "order_cost"], ascending=False) if not plan.empty else plan
    return k, {"purchasing_plan": inv.records(plan_sorted), "unfunded_count": int(len(unfunded))}, narrative


def _capacity(store, ctx, cfg, base_snap, base_plan, base_sum, base_log, fin, overhead):
    pct = float(cfg.get("reduction_pct", 20))
    if not 0 < pct < 100:
        raise ValidationFailed("reduction_pct must be between 0 and 100")
    wid = cfg.get("warehouse_id")
    if wid and int(wid) not in set(ctx.warehouses["warehouse_id"]):
        raise NotFoundError("Warehouse not found")
    whs = ctx.warehouses.copy()
    affected_ids = [int(wid)] if wid else whs["warehouse_id"].astype(int).tolist()
    whs["new_capacity"] = [c * (1 - pct / 100) if w in affected_ids else c for w, c in zip(whs["warehouse_id"], whs["capacity_units"])]
    occ = base_snap.groupby("warehouse_id")[["current_stock", "incoming_units"]].sum().sum(axis=1)
    whs["occupancy"] = whs["warehouse_id"].map(occ).fillna(0)
    whs["overflow_units"] = np.maximum(whs["occupancy"] - whs["new_capacity"], 0)
    whs["free_after"] = np.maximum(whs["new_capacity"] - whs["occupancy"], 0)

    # Which stock cannot be stored: release lowest-priority stock first (overstock, C-class, highest cover)
    cannot_store, moves = [], []
    free = dict(zip(whs["warehouse_id"], whs["free_after"]))
    pr = {"overstock": 0, "healthy": 1, "low": 2, "critical": 3, "out_of_stock": 4}
    for w in whs[whs["overflow_units"] > 0].itertuples():
        overflow = w.overflow_units
        items = base_snap[base_snap["warehouse_id"] == w.warehouse_id].copy()
        items["prio"] = items["status"].map(pr)
        items["abc_rank"] = items["abc_class"].map({"C": 0, "B": 1, "A": 2})
        items = items.sort_values(["prio", "abc_rank", "days_of_cover"], ascending=[True, True, False])
        for r in items.itertuples(index=False):
            if overflow <= 0:
                break
            keep = r.reorder_point  # never push stock below its reorder point
            releasable = max(r.current_stock - keep, 0)
            q = min(releasable, overflow)
            if q <= 0:
                continue
            overflow -= q
            # redistribute to the warehouse with free space where this product has the lowest cover
            others = base_snap[(base_snap["product_id"] == r.product_id) & (base_snap["warehouse_id"] != w.warehouse_id)]
            others = others.assign(free=others["warehouse_id"].map(free)).sort_values("days_of_cover")
            moved = 0
            for o in others.itertuples(index=False):
                room = min(free.get(o.warehouse_id, 0), q - moved)
                if room <= 0:
                    continue
                free[o.warehouse_id] -= room
                moved += room
                moves.append({"sku": r.sku, "from": r.warehouse_code, "to": o.warehouse_code, "units": int(room),
                              "to_days_of_cover_before": None if o.days_of_cover is None else round(o.days_of_cover, 1)})
                if moved >= q:
                    break
            cannot_store.append({"sku": r.sku, "name": r.name, "warehouse_code": r.warehouse_code, "abc_class": r.abc_class,
                                 "status": r.status, "units_to_release": int(q), "units_redistributed": int(moved),
                                 "units_without_space": int(q - moved)})
    cap = {int(w.warehouse_id): float(w.new_capacity - w.occupancy) for w in whs.itertuples()}
    plan, summ = inv.optimize_orders(ctx, base_snap, budget=None, capacity_override=cap)
    k = kpis(base_snap, plan, summ, base_log["total_monthly_cost"], fin, overhead)
    unmet = summ["unmet_need_units"]
    narrative = [
        f"Reducing capacity by {pct:.0f}% at {'all warehouses' if not wid else whs.loc[whs.warehouse_id == wid, 'code'].iloc[0]} "
        f"leaves {whs['overflow_units'].sum():,.0f} units of current + in-transit stock without space.",
        f"{sum(m['units'] for m in moves):,.0f} units can be redistributed to warehouses with free space; "
        f"{sum(c['units_without_space'] for c in cannot_store):,.0f} units need external storage, markdown or returns.",
        f"Replenishment blocked by capacity: {unmet:,.0f} units of need cannot be ordered.",
    ]
    return k, {"warehouses": inv.records(whs[["code", "name", "capacity_units", "new_capacity", "occupancy", "overflow_units", "free_after"]]),
               "cannot_store": cannot_store[:50], "redistribution": moves[:50]}, narrative


# ====================================================================== persistence (data/scenarios.json)
DOC = "scenarios"


def save(store: Store, name: str, result: dict, user_id: int | None) -> dict:
    holder: dict = {}

    def add(items):
        items = items or []
        sc = {
            "id": max((x["id"] for x in items), default=0) + 1, "name": name, "scenario_type": result["scenario_type"],
            "config": result["config"], "created_by_id": user_id, "created_at": utcnow_iso(),
            "details": {"details": result["details"], "narrative": result["narrative"], "as_of": result["as_of"],
                        "forecast_source": result["forecast_source"]},
            "results": [{"metric": m["metric"], "label": m["label"], "unit": m["unit"], "baseline_value": float(m["baseline"]),
                         "scenario_value": float(m["scenario"])} for m in result["metrics"]],
        }
        holder["sc"] = sc
        return items + [sc]

    store.update_json(DOC, [], add)
    sc = holder["sc"]
    log_activity(store, user_id=user_id, action="simulate", entity_type="scenario", entity_id=sc["id"],
                 description=f"Scenario saved: {name} ({SCENARIO_TYPES[sc['scenario_type']]})")
    return sc


def list_saved(store: Store) -> list[dict]:
    return sorted(store.read_json(DOC, []) or [], key=lambda x: x["created_at"], reverse=True)


def get(store: Store, scenario_id: int) -> dict | None:
    return next((x for x in store.read_json(DOC, []) or [] if x["id"] == scenario_id), None)


def delete(store: Store, scenario_id: int) -> dict:
    holder: dict = {}

    def drop(items):
        keep = [x for x in items or [] if x["id"] != scenario_id]
        if len(keep) == len(items or []):
            raise NotFoundError("Scenario not found")
        holder["sc"] = next(x for x in items if x["id"] == scenario_id)
        return keep

    store.update_json(DOC, [], drop)
    return holder["sc"]


def to_dict(sc: dict, full: bool = True) -> dict:
    metrics = []
    hib = {m: h for m, _, _, h in KPI_DEFS}
    for r in sc["results"]:
        d = r["scenario_value"] - r["baseline_value"]
        metrics.append({"metric": r["metric"], "label": r["label"], "unit": r["unit"], "baseline": r["baseline_value"],
                        "scenario": r["scenario_value"], "change": round(d, 2),
                        "change_pct": round(d / r["baseline_value"] * 100, 2) if r["baseline_value"] else None,
                        "favourable": None if abs(d) < 1e-9 else (d > 0) == hib.get(r["metric"], False)})
    out = {"id": sc["id"], "name": sc["name"], "scenario_type": sc["scenario_type"],
           "scenario_label": SCENARIO_TYPES.get(sc["scenario_type"]), "config": sc["config"], "created_at": sc["created_at"],
           "metrics": metrics}
    if full and sc.get("details"):
        det = sc["details"]
        out.update({"details": det.get("details"), "narrative": det.get("narrative"), "as_of": det.get("as_of"),
                    "forecast_source": det.get("forecast_source")})
    return out


def compare(store: Store, ids: list[int]) -> dict:
    if len(ids) < 2:
        raise ValidationFailed("Select at least two scenarios to compare")
    found = {x["id"]: x for x in store.read_json(DOC, []) or [] if x["id"] in ids}
    if len(found) != len(set(ids)):
        raise NotFoundError("One or more scenarios were not found")
    scs = [found[i] for i in ids]
    table = []
    for m, label, unit, hib in KPI_DEFS:
        row = {"metric": m, "label": label, "unit": unit, "higher_is_better": hib}
        base_vals = []
        for sc in scs:
            r = next((x for x in sc["results"] if x["metric"] == m), None)
            row[str(sc["id"])] = None if r is None else r["scenario_value"]
            if r is not None:
                base_vals.append(r["baseline_value"])
        row["baseline"] = base_vals[0] if base_vals else None
        table.append(row)
    return {"scenarios": [to_dict(x, full=False) for x in scs], "table": table}
