"""Logistics network service: graph construction, shortest routes, VRP, network cost model."""
from __future__ import annotations

import math

import pandas as pd

import networkx as nx

from app.core.exceptions import NotFoundError, ValidationFailed
from app.storage.store import Store, utcnow_iso
from app.optimization.logistics import network as net
from app.optimization.logistics.vrp import solve_cvrp
from app.services import settings_service
from app.services.activity import log_activity

ESTIMATE_NOTE = ("Distances, travel times and costs are estimates from the simulated road network "
                 "(average speeds and per-km tariffs), not live traffic information.")


def _load(store: Store) -> tuple[list[dict], list[dict]]:
    loc = store.read("locations").sort_values("code")
    locs = [{"id": int(r.id), "code": r.code, "name": r.name, "type": r.location_type, "city": r.city,
             "latitude": float(r.latitude), "longitude": float(r.longitude), "daily_demand_units": int(r.daily_demand_units),
             "warehouse_id": None if pd.isna(r.warehouse_id) else int(r.warehouse_id),
             "supplier_id": None if pd.isna(r.supplier_id) else int(r.supplier_id), "is_simulated": bool(r.is_simulated)}
            for r in loc.itertuples()]
    routes = [{"id": int(r.id), "origin_id": int(r.origin_id), "destination_id": int(r.destination_id),
               "distance_km": float(r.distance_km), "travel_time_hours": float(r.travel_time_hours),
               "cost_per_km": float(r.cost_per_km), "toll_cost": float(r.toll_cost)}
              for r in store.read("routes").itertuples()]
    return locs, routes


def graph(store: Store, cost_multiplier: float | None = None) -> nx.DiGraph:
    locs, routes = _load(store)
    if not locs:
        raise NotFoundError("The logistics network is empty. Import locations and routes first.")
    mult = cost_multiplier if cost_multiplier is not None else float(settings_service.get(store, "logistics")["fuel_cost_multiplier"])
    return net.build_graph(locs, routes, mult)


def network(store: Store) -> dict:
    locs, routes = _load(store)
    g = net.build_graph(locs, routes) if locs else nx.DiGraph()
    seen, edges = set(), []
    for r in routes:
        key = tuple(sorted((r["origin_id"], r["destination_id"])))
        if key in seen:
            continue
        seen.add(key)
        edges.append({**r, "cost": round(g[r["origin_id"]][r["destination_id"]]["cost"], 2)})
    return {"locations": locs, "routes": edges, "note": ESTIMATE_NOTE,
            "stats": {"nodes": len(locs), "links": len(edges), "connected": nx.is_weakly_connected(g) if locs else False}}


def _loc(store: Store, loc_id: int) -> dict:
    loc = store.read("locations")
    r = loc[loc["id"] == loc_id]
    if r.empty:
        raise NotFoundError(f"Location {loc_id} not found")
    return r.iloc[0].to_dict()


def shortest_route(store: Store, origin_id: int, destination_id: int, optimize_for: str, user_id: int | None,
                   cost_multiplier: float | None = None) -> dict:
    if optimize_for not in net.WEIGHTS:
        raise ValidationFailed(f"optimize_for must be one of {list(net.WEIGHTS)}")
    if origin_id == destination_id:
        raise ValidationFailed("Origin and destination must differ")
    _loc(store, origin_id), _loc(store, destination_id)
    g = graph(store, cost_multiplier)
    weight = net.WEIGHTS[optimize_for]
    try:
        _, path = net.dijkstra(g, origin_id, destination_id, weight)
    except nx.NetworkXNoPath:
        raise ValidationFailed("No route exists between these locations")
    best = net.path_summary(g, path)
    alts = net.alternatives(g, origin_id, destination_id, weight, k=4)
    for a in alts:
        a["extra_distance_km"] = round(a["total_distance_km"] - best["total_distance_km"], 1)
        a["extra_cost"] = round(a["total_cost"] - best["total_cost"], 2)
        a["extra_time_h"] = round(a["total_time_h"] - best["total_time_h"], 2)
        a["is_selected"] = a["path"] == best["path"]
    result = {"optimize_for": optimize_for, "algorithm": "Dijkstra (binary heap); alternatives via Yen's k-shortest paths",
              "selected": best, "alternatives": alts, "note": ESTIMATE_NOTE}
    store.append_jsonl("route_runs", {"run_type": "shortest_path", "params": {"origin_id": origin_id, "destination_id": destination_id,
                                "optimize_for": optimize_for, "cost_multiplier": cost_multiplier}, "total_distance_km": best["total_distance_km"],
                                     "total_cost": best["total_cost"], "result": {"codes": best["codes"]}, "created_by_id": user_id, "created_at": utcnow_iso()})
    log_activity(store, user_id=user_id, action="route", entity_type="route_optimization",
                 description=f"Shortest route {best['codes'][0]} → {best['codes'][-1]} by {optimize_for}: "
                             f"{best['total_distance_km']} km")
    return result


def optimize_vrp(store: Store, depot_id: int, stop_ids: list[int], demands: dict[int, int] | None,
                 vehicle_capacity: int | None, num_vehicles: int, user_id: int | None,
                 cost_multiplier: float | None = None) -> dict:
    if not stop_ids:
        raise ValidationFailed("Select at least one destination")
    if depot_id in stop_ids:
        raise ValidationFailed("The starting warehouse cannot also be a destination")
    if num_vehicles < 1 or num_vehicles > 20:
        raise ValidationFailed("num_vehicles must be between 1 and 20")
    cap = vehicle_capacity or int(settings_service.get(store, "logistics")["vehicle_capacity_units"])
    g = graph(store, cost_multiplier)
    for s in [depot_id, *stop_ids]:
        if s not in g:
            raise NotFoundError(f"Location {s} not found")
    dem = {s: int((demands or {}).get(s) or g.nodes[s]["daily_demand_units"] or 0) for s in stop_ids}
    total = sum(dem.values())
    if total > cap * num_vehicles:
        raise ValidationFailed(f"Total demand {total} exceeds fleet capacity {cap * num_vehicles}. "
                               f"Add vehicles (need at least {math.ceil(total / cap)}).")
    try:
        sol = solve_cvrp(g, depot_id, stop_ids, dem, cap, num_vehicles)
    except ValueError as exc:
        raise ValidationFailed(str(exc))
    vehicles = []
    for v in sol["vehicles"]:
        summ = net.path_summary(g, v["road_path"])
        vehicles.append({"vehicle": v["vehicle"], "load": v["load"], "utilization": round(v["load"] / cap, 3),
                         "stop_codes": [g.nodes[n]["code"] for n in v["stop_sequence"]], **summ})
    # Baseline for comparison: one dedicated round trip per stop
    naive = 0.0
    for s in stop_ids:
        naive += nx.dijkstra_path_length(g, depot_id, s, weight="cost") + nx.dijkstra_path_length(g, s, depot_id, weight="cost")
    total_cost = round(sum(v["total_cost"] for v in vehicles), 2)
    result = {
        "algorithm": "Capacitated VRP: Clarke–Wright savings + 2-opt on the shortest-path cost matrix",
        "depot": g.nodes[depot_id]["code"], "vehicle_capacity": cap, "vehicles": vehicles,
        "total_distance_km": round(sum(v["total_distance_km"] for v in vehicles), 1),
        "total_time_h": round(sum(v["total_time_h"] for v in vehicles), 2), "total_cost": total_cost,
        "total_demand": total, "direct_trips_cost": round(naive, 2),
        "saving_vs_direct_trips": round(naive - total_cost, 2), "note": ESTIMATE_NOTE,
    }
    store.append_jsonl("route_runs", {"run_type": "vrp", "params": {"depot_id": depot_id, "stop_ids": stop_ids, "vehicle_capacity": cap,
                                "num_vehicles": num_vehicles, "cost_multiplier": cost_multiplier}, "total_distance_km": result["total_distance_km"],
                                     "total_cost": total_cost, "result": {"vehicles": [v["stop_codes"] for v in vehicles]}, "created_by_id": user_id, "created_at": utcnow_iso()})
    log_activity(store, user_id=user_id, action="route", entity_type="route_optimization",
                 description=f"Vehicle routing from {result['depot']}: {len(stop_ids)} stops, {len(vehicles)} vehicles, "
                             f"₹{total_cost:,.0f}")
    return result


def inefficient(store: Store) -> list[dict]:
    return net.inefficient_routes(graph(store))


def distribution_cost_model(store: Store, cost_multiplier: float = 1.0, vehicle_capacity: int | None = None) -> dict:
    """Estimated monthly cost of serving each retail store from its cheapest warehouse.

    Stores are replenished every `store_replenishment_days` days with full-truck loads:
    trips per cycle = ceil(daily demand × cycle days / vehicle capacity);
    monthly cost = trips per cycle × (30 / cycle days) × round-trip shortest-path cost.
    Used by the transportation report and the transport-cost scenario."""
    lset = settings_service.get(store, "logistics")
    cap = vehicle_capacity or int(lset["vehicle_capacity_units"])
    cycle = float(lset.get("store_replenishment_days", 7))
    base_mult = float(lset["fuel_cost_multiplier"])
    g = graph(store, base_mult * cost_multiplier)
    whs = [n for n, d in g.nodes(data=True) if d["type"] == "warehouse"]
    stores = [n for n, d in g.nodes(data=True) if d["type"] == "retail_store"]
    lanes = []
    for s in stores:
        best = None
        for w in whs:
            try:
                c_out, p_out = nx.single_source_dijkstra(g, w, s, weight="cost")
                c_back = nx.dijkstra_path_length(g, s, w, weight="cost")
            except nx.NetworkXNoPath:
                continue
            if best is None or c_out + c_back < best[0]:
                best = (c_out + c_back, w, p_out)
        if best is None:
            continue
        dd = g.nodes[s]["daily_demand_units"] or 0
        trips_cycle = math.ceil(dd * cycle / cap) if dd else 0
        trips_month = trips_cycle * 30 / cycle
        summ = net.path_summary(g, best[2])
        lanes.append({
            "store": g.nodes[s]["code"], "store_name": g.nodes[s]["name"], "serving_warehouse": g.nodes[best[1]]["code"],
            "route": summ["codes"], "one_way_distance_km": summ["total_distance_km"], "one_way_time_h": summ["total_time_h"],
            "round_trip_cost": round(best[0], 2), "daily_demand_units": g.nodes[s]["daily_demand_units"],
            "trips_per_cycle": trips_cycle, "trips_per_month": round(trips_month, 1),
            "load_per_trip": round(dd * cycle / trips_cycle, 0) if trips_cycle else 0,
            "monthly_cost": round(best[0] * trips_month, 2),
            "cost_per_unit": round(best[0] * trips_month / (dd * 30), 2) if dd else None,
        })
    return {"vehicle_capacity": cap, "cost_multiplier": cost_multiplier, "replenishment_cycle_days": cycle, "lanes": sorted(lanes, key=lambda l: -l["monthly_cost"]),
            "total_monthly_cost": round(sum(l["monthly_cost"] for l in lanes), 2), "note": ESTIMATE_NOTE}
