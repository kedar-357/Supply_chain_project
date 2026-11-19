"""Capacitated Vehicle Routing (CVRP): Clarke–Wright savings construction + 2-opt improvement.

Pure Python on top of NetworkX. The cost matrix between stops is the *shortest-path* cost on the road
network, so a vehicle may pass through intermediate nodes. Every vehicle starts and ends at the depot.

1. Start with one route per stop: depot → i → depot.
2. Savings s(i, j) = c(0, i) + c(j, 0) − c(i, j). In decreasing order, merge the route ending at i with
   the route starting at j when both are route ends and the combined load fits the vehicle.
3. If more routes than vehicles remain, merge the cheapest-to-combine pairs that still fit capacity.
4. Improve each route's visiting order with 2-opt until no improving reversal exists.
"""
from __future__ import annotations

import networkx as nx


def _route_cost(route: list[int], c: dict[int, dict[int, float]], depot: int) -> float:
    seq = [depot, *route, depot]
    return sum(c[a][b] for a, b in zip(seq, seq[1:]))


def _two_opt(route: list[int], c: dict[int, dict[int, float]], depot: int) -> list[int]:
    best = route[:]
    best_cost = _route_cost(best, c, depot)
    improved = True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                cost = _route_cost(cand, c, depot)
                if cost < best_cost - 1e-9:
                    best, best_cost, improved = cand, cost, True
    return best


def solve_cvrp(g: nx.DiGraph, depot: int, stops: list[int], demands: dict[int, int], vehicle_capacity: int,
               num_vehicles: int, weight: str = "cost", time_limit_s: int | None = None) -> dict:
    nodes = [depot, *stops]
    sp = {n: nx.single_source_dijkstra(g, n, weight=weight) for n in nodes}
    for a in nodes:
        for b in nodes:
            if a != b and b not in sp[a][0]:
                raise ValueError(f"No road path between {g.nodes[a]['code']} and {g.nodes[b]['code']}")
    c = {a: {b: (0.0 if a == b else sp[a][0][b]) for b in nodes} for a in nodes}
    dem = {s: int(demands.get(s, 0)) for s in stops}
    if any(d > vehicle_capacity for d in dem.values()):
        raise ValueError("A single stop's demand exceeds vehicle capacity")

    routes: dict[int, list[int]] = {i: [s] for i, s in enumerate(stops)}
    load = {i: dem[s] for i, s in enumerate(stops)}
    where = {s: i for i, s in enumerate(stops)}
    savings = sorted(((c[depot][i] + c[j][depot] - c[i][j], i, j) for i in stops for j in stops if i != j), reverse=True)
    for s_ij, i, j in savings:
        if s_ij <= 0:
            break
        ri, rj = where[i], where[j]
        if ri == rj or routes[ri][-1] != i or routes[rj][0] != j or load[ri] + load[rj] > vehicle_capacity:
            continue
        routes[ri] += routes.pop(rj)
        load[ri] += load.pop(rj)
        for s in routes[ri]:
            where[s] = ri

    # Respect the fleet size: merge the cheapest compatible pairs while there are too many routes
    while len(routes) > num_vehicles:
        best = None
        keys = list(routes)
        for a in keys:
            for b in keys:
                if a == b or load[a] + load[b] > vehicle_capacity:
                    continue
                extra = _route_cost(routes[a] + routes[b], c, depot) - _route_cost(routes[a], c, depot) - _route_cost(routes[b], c, depot)
                if best is None or extra < best[0]:
                    best = (extra, a, b)
        if best is None:
            raise ValueError(f"Demand cannot be served by {num_vehicles} vehicle(s) of capacity {vehicle_capacity}")
        _, a, b = best
        routes[a] += routes.pop(b)
        load[a] += load.pop(b)

    vehicles = []
    for n, (rid, route) in enumerate(sorted(routes.items(), key=lambda kv: -load[kv[0]]), start=1):
        route = _two_opt(route, c, depot)
        seq = [depot, *route, depot]
        full = [depot]
        for a, b in zip(seq, seq[1:]):  # expand stop-to-stop hops into road paths
            full += sp[a][1][b][1:]
        vehicles.append({"vehicle": n, "stop_sequence": seq, "road_path": full, "load": load[rid]})
    return {"vehicles": vehicles, "objective": round(sum(_route_cost(v["stop_sequence"][1:-1], c, depot) for v in vehicles), 2)}
