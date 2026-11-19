"""Management recommendation engine.

Converts calculated analytics into actions using transparent business rules. Every recommendation
records the rule that fired, the supporting numbers, the reason and the expected impact; nothing is
generated without a measured trigger. Priority score = estimated financial impact (INR) scaled by
urgency; level: high ≥ 50k or urgent, medium ≥ 10k, else low.
"""
from __future__ import annotations


import pandas as pd

from app.storage.store import Store, utcnow_iso
from app.services import inventory_service as inv
from app.services import logistics_service, supplier_service
from app.services.activity import log_activity


DOC = "recommendations"


def _level(score: float, urgent: bool = False) -> str:
    if urgent or score >= 50_000:
        return "high"
    return "medium" if score >= 10_000 else "low"


def generate(store: Store, user_id: int | None) -> list[dict]:
    ctx = inv.load_context(store)
    snap = inv.compute_snapshot(ctx)
    plan, _ = inv.optimize_orders(ctx, snap, budget=None)
    recs: list[dict] = []
    # Rule 1 — replenish items below reorder point / projected to stock out
    for r in plan[plan["recommended_qty"] > 0].itertuples(index=False) if not plan.empty else []:
        s = snap[(snap.product_id == r.product_id) & (snap.warehouse_id == r.warehouse_id)].iloc[0]
        urgent = r.status in ("critical", "out_of_stock")
        protected = max(s.unmet_units_before_replenishment, 0) * s.selling_price + r.need_units * (s.selling_price - s.unit_cost) * 0.1
        recs.append(dict(
            category="replenishment", product_id=r.product_id, warehouse_id=r.warehouse_id, supplier_id=r.supplier_id,
            title=f"Order {r.recommended_qty:,} units of {r.sku} ({r.name}) for {r.warehouse_code} from {r.supplier_name}",
            reason=r.reason,
            expected_impact=(f"Restores stock to the target level of {r.target_level:,.0f} units; protects about "
                             f"₹{protected:,.0f} of sales at a purchase cost of ₹{r.order_cost:,.0f}."),
            supporting_data={"current_stock": r.current_stock, "incoming_units": r.incoming_units, "reorder_point": r.reorder_point,
                             "eoq": r.eoq, "forecast_daily_demand": round(s.forecast_daily_demand, 2),
                             "stockout_in_days": s.stockout_in_days, "lead_time_days": round(s.lead_time_days, 1),
                             "rule": "inventory position below ROP + review-period demand"},
            score=protected * (2 if urgent else 1), urgent=urgent,
        ))

    # Rule 2 — demand increase: forecast next 30 days ≥ 15% above last 30 days (product level)
    prod = snap.groupby(["product_id", "sku", "name"]).agg(fc=("predicted_demand_30d", "sum"), last=("units_last_30d", "sum"),
                                                          stock=("current_stock", "sum"), incoming=("incoming_units", "sum"),
                                                          price=("selling_price", "first"), cost=("unit_cost", "first")).reset_index()
    for r in prod.itertuples(index=False):
        if r.last > 0 and (r.fc - r.last) / r.last >= 0.15:
            pct = (r.fc - r.last) / r.last * 100
            extra = r.fc - r.last
            recs.append(dict(
                category="demand", product_id=r.product_id,
                title=f"Increase the reorder quantity of {r.sku} ({r.name}): demand forecast +{pct:.0f}%",
                reason=f"Forecast demand for the next 30 days ({r.fc:,.0f} units) exceeds the last 30 days ({r.last:,.0f}).",
                expected_impact=f"Covers ≈{extra:,.0f} additional units of demand (≈₹{extra * (r.price - r.cost):,.0f} gross margin).",
                supporting_data={"forecast_30d": round(r.fc, 1), "last_30d": round(r.last, 1), "stock_all_warehouses": int(r.stock),
                                 "incoming": int(r.incoming), "rule": "forecast change ≥ +15%"},
                score=extra * (r.price - r.cost), urgent=r.fc > r.stock + r.incoming,
            ))

    # Rule 3 — redistribute overstock to warehouses where the same product is short
    for pid, g in snap.groupby("product_id"):
        over = g[g["status"] == "overstock"]
        short = g[g["status"].isin(["low", "critical", "out_of_stock"]) | (g["days_of_cover"].fillna(0) < g["lead_time_days"])]
        for o in over.itertuples(index=False):
            for s in short.itertuples(index=False):
                need = max(s.reorder_point - s.inventory_position, s.forecast_daily_demand * s.lead_time_days - s.current_stock, 0)
                qty = int(min(o.excess_units, need))
                if qty <= 0:
                    continue
                saved = qty * s.unit_cost  # purchase avoided
                recs.append(dict(
                    category="redistribution", product_id=pid, warehouse_id=s.warehouse_id,
                    title=f"Transfer {qty:,} units of {o.sku} from {o.warehouse_code} to {s.warehouse_code}",
                    reason=(f"{o.warehouse_code} holds {o.excess_units:,.0f} excess units ({o.days_of_cover:.0f} days of cover) while "
                            f"{s.warehouse_code} is {s.status.replace('_', ' ')} ({s.current_stock} on hand, ROP {s.reorder_point:.0f})."),
                    expected_impact=f"Avoids ≈₹{saved:,.0f} of new purchasing and reduces excess stock at {o.warehouse_code}.",
                    supporting_data={"from_excess_units": round(o.excess_units, 0), "to_current_stock": s.current_stock,
                                     "to_reorder_point": round(s.reorder_point, 1), "rule": "overstock in one warehouse, shortage in another"},
                    score=saved, urgent=s.status in ("critical", "out_of_stock"),
                ))

    # Rule 4 — overstock with > 90 days of cover: reduce purchasing / markdown
    for o in snap[(snap["status"] == "overstock") & (snap["days_of_cover"] > 90)].itertuples(index=False):
        excess_days = o.excess_units / o.forecast_daily_demand if o.forecast_daily_demand else 0
        holding = o.excess_units * o.holding_cost_per_unit_year * excess_days / 365
        recs.append(dict(
            category="overstock", product_id=o.product_id, warehouse_id=o.warehouse_id,
            title=f"Pause purchasing of {o.sku} for {o.warehouse_code} and consider a promotion to clear {o.excess_units:,.0f} excess units",
            reason=f"{o.days_of_cover:.0f} days of cover versus a threshold of {o.overstock_threshold / o.forecast_daily_demand:.0f} days.",
            expected_impact=f"Releases ≈₹{o.excess_units * o.unit_cost:,.0f} of working capital and avoids ≈₹{holding:,.0f} holding cost.",
            supporting_data={"current_stock": o.current_stock, "days_of_cover": round(o.days_of_cover, 1),
                             "excess_units": round(o.excess_units, 0), "rule": "days of cover > 90 and above overstock threshold"},
            score=o.excess_units * o.unit_cost * 0.2, urgent=False,
        ))

    # Rule 5 — supplier reliability: switch volume to a better alternative supplier
    perf = supplier_service.performance(store)["suppliers"]
    by_id = {s["supplier_id"]: s for s in perf}
    sp = store.read("supplier_products")[["supplier_id", "product_id", "unit_cost"]]
    primary = snap.drop_duplicates("product_id")[["product_id", "supplier_id", "category", "annual_units"]]
    for sid, grp in primary.groupby("supplier_id"):
        s = by_id.get(int(sid))
        if not s or (s["on_time_rate"] or 1) >= 0.6:
            continue
        alts = sp[sp["product_id"].isin(grp["product_id"]) & (sp["supplier_id"] != sid)]
        for alt_id, ag in alts.groupby("supplier_id"):
            a = by_id.get(int(alt_id))
            if not a or (a["on_time_rate"] or 0) < (s["on_time_rate"] or 0) + 0.15:
                continue
            cost_diff = []
            for pid in ag["product_id"]:
                c_alt = float(ag.loc[ag.product_id == pid, "unit_cost"].iloc[0])
                c_pri = float(sp.loc[(sp.product_id == pid) & (sp.supplier_id == sid), "unit_cost"].iloc[0])
                cost_diff.append((c_alt - c_pri) / c_pri * 100)
            avg_diff = sum(cost_diff) / len(cost_diff)
            late_avoided = (a["on_time_rate"] - s["on_time_rate"]) * 100
            recs.append(dict(
                category="supplier", supplier_id=int(alt_id),
                title=f"Consider {a['name']} for future orders of {', '.join(sorted(grp['category'].unique()))} currently sourced from {s['name']}",
                reason=(f"{s['name']} delivered on time {s['on_time_rate'] * 100:.0f}% of the time (avg delay {s['avg_delay_days']:.1f} d) versus "
                        f"{a['on_time_rate'] * 100:.0f}% for {a['name']}."),
                expected_impact=(f"≈{late_avoided:.0f} fewer late deliveries per 100 orders at a {avg_diff:+.1f}% unit-cost difference "
                                 f"for {len(ag)} products. Split volume rather than single-source."),
                supporting_data={"current_supplier_on_time": s["on_time_rate"], "alternative_on_time": a["on_time_rate"],
                                 "avg_cost_difference_pct": round(avg_diff, 2), "current_risk": s.get("risk_category"),
                                 "products": int(len(ag)), "rule": "primary on-time < 60% and alternative ≥ 15 pp better"},
                score=float(grp["annual_units"].sum()) * late_avoided / 100 * 5, urgent=s.get("risk_category") == "High",
            ))
        if s.get("trend") == "deteriorating":
            recs.append(dict(
                category="supplier", supplier_id=int(sid),
                title=f"Schedule a performance review with {s['name']}",
                reason=(f"On-time rate in the last 90 days is {s['recent_on_time_rate'] * 100:.0f}% versus "
                        f"{s['same_period_last_year_on_time_rate'] * 100:.0f}% in the same period last year."),
                expected_impact="Early escalation limits the impact of a deteriorating supplier on safety stock and service level.",
                supporting_data={"recent_on_time_rate": s["recent_on_time_rate"],
                                 "same_period_last_year": s["same_period_last_year_on_time_rate"], "rule": "YoY on-time drop > 10 pp"},
                score=20_000, urgent=False,
            ))

    # Rule 6 — logistics: inefficient links and under-utilised lanes
    for ie in logistics_service.inefficient(store)[:5]:
        if not ie["potential_saving"]:
            continue
        recs.append(dict(
            category="logistics",
            title=f"Avoid the direct link {ie['from']} → {ie['to']}; route via the cheaper alternative",
            reason="; ".join(ie["reasons"]),
            expected_impact=f"Saves ≈₹{ie['potential_saving']:,.0f} per trip (estimate).",
            supporting_data={"direct_cost": ie["cost"], "alternative_cost": ie["best_alternative_cost"],
                             "detour_ratio": ie["detour_ratio"], "rule": "direct cost > 1.15 × best alternative"},
            score=ie["potential_saving"] * 8, urgent=False,
        ))
    lanes = logistics_service.distribution_cost_model(store)
    low = [l for l in lanes["lanes"] if l["trips_per_cycle"] and l["load_per_trip"] / lanes["vehicle_capacity"] < 0.6]
    if len(low) >= 2:
        saving = sum(l["monthly_cost"] for l in low) * 0.25
        recs.append(dict(
            category="logistics",
            title=f"Consolidate deliveries to {', '.join(l['store'] for l in low)} into multi-stop routes",
            reason=f"These lanes run trucks below 60% utilisation (capacity {lanes['vehicle_capacity']:,} units).",
            expected_impact=f"Multi-stop routing typically saves ≈25% on these lanes: ≈₹{saving:,.0f}/month (estimate; "
                            "verify with the route optimizer).",
            supporting_data={"lanes": [{"store": l["store"], "load_per_trip": l["load_per_trip"]} for l in low],
                             "rule": "truck utilisation < 60% on ≥ 2 lanes"},
            score=saving, urgent=False,
        ))

    # persist (replace previously generated open recommendations; keep accepted/dismissed history)
    now = utcnow_iso()

    def replace(items):
        kept = [r for r in items or [] if r["status"] != "open"]
        nxt = max((r["id"] for r in items or []), default=0) + 1
        for i, r in enumerate(recs):
            kept.append({
                "id": nxt + i, "category": r["category"], "title": r["title"], "reason": r["reason"],
                "expected_impact": r["expected_impact"], "priority": _level(r["score"], r["urgent"]),
                # urgent items (projected stockout before replenishment) always rank first within a level
                "priority_score": round(float(r["score"]) * (10 if r["urgent"] else 1), 2),
                "supporting_data": _jsonable(r["supporting_data"]), "product_id": _int(r.get("product_id")),
                "supplier_id": _int(r.get("supplier_id")), "warehouse_id": _int(r.get("warehouse_id")),
                "status": "open", "generated_at": now,
            })
        return kept

    store.update_json(DOC, [], replace)
    log_activity(store, user_id=user_id, action="generate", entity_type="recommendation",
                 description=f"{len(recs)} management recommendations generated")
    return list_recommendations(store)


def _int(v):
    return None if v is None or pd.isna(v) else int(v)


def _jsonable(d: dict) -> dict:
    import numpy as np

    def conv(v):
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating, float)):
            return None if np.isnan(v) else round(float(v), 4)
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v
    return {k: conv(v) for k, v in d.items()}


def list_recommendations(store: Store, category: str | None = None, priority: str | None = None) -> list[dict]:
    recs = [r for r in store.read_json(DOC, []) or [] if r["status"] != "dismissed"
            and (not category or r["category"] == category) and (not priority or r["priority"] == priority)]
    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: (order[r["priority"]], -r["priority_score"]))
    return [{k: r[k] for k in ("id", "category", "title", "reason", "expected_impact", "priority", "priority_score",
                               "supporting_data", "status", "generated_at")} for r in recs]


def set_status(store: Store, rec_id: int, status: str) -> None:
    from app.core.exceptions import NotFoundError, ValidationFailed

    if status not in ("open", "accepted", "dismissed"):
        raise ValidationFailed("status must be open, accepted or dismissed")

    def apply(items):
        for r in items or []:
            if r["id"] == rec_id:
                r["status"] = status
                return items
        raise NotFoundError("Recommendation not found")

    store.update_json(DOC, [], apply)
