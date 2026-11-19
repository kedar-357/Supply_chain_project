"""Graph-based route optimisation on the logistics network (NetworkX).

Edge attributes: distance_km, time_h, cost (= distance × cost_per_km × fuel multiplier + toll).
All times and costs are ESTIMATES from the network model, not live traffic information.
"""
from __future__ import annotations

import heapq
from itertools import islice

import networkx as nx

WEIGHTS = {"distance": "distance_km", "time": "time_h", "cost": "cost"}


def build_graph(locations: list[dict], routes: list[dict], cost_multiplier: float = 1.0) -> nx.DiGraph:
    g = nx.DiGraph()
    for loc in locations:
        g.add_node(loc["id"], **loc)
    for r in routes:
        g.add_edge(
            r["origin_id"], r["destination_id"], route_id=r["id"], distance_km=r["distance_km"],
            time_h=r["travel_time_hours"], cost_per_km=r["cost_per_km"], toll=r["toll_cost"],
            cost=r["distance_km"] * r["cost_per_km"] * cost_multiplier + r["toll_cost"],
        )
    return g


def dijkstra(g: nx.DiGraph, source: int, target: int, weight: str = "distance_km") -> tuple[float, list[int]]:
    """Textbook Dijkstra with a binary heap (implemented explicitly for transparency; results are
    cross-checked against networkx in the tests)."""
    if source not in g or target not in g:
        raise nx.NodeNotFound("Unknown location")
    dist = {source: 0.0}
    prev: dict[int, int] = {}
    heap = [(0.0, source)]
    visited = set()
    while heap:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        if u == target:
            break
        for v, attrs in g[u].items():
            nd = d + attrs[weight]
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    if target not in dist:
        raise nx.NetworkXNoPath(f"No path between {source} and {target}")
    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    return dist[target], path[::-1]


def path_summary(g: nx.DiGraph, path: list[int]) -> dict:
    legs = []
    for a, b in zip(path, path[1:]):
        e = g[a][b]
        legs.append({"from_id": a, "to_id": b, "from": g.nodes[a]["code"], "to": g.nodes[b]["code"],
                     "distance_km": round(e["distance_km"], 1), "time_h": round(e["time_h"], 2), "cost": round(e["cost"], 2)})
    return {
        "path": path,
        "codes": [g.nodes[n]["code"] for n in path],
        "names": [g.nodes[n]["name"] for n in path],
        "coordinates": [[g.nodes[n]["latitude"], g.nodes[n]["longitude"]] for n in path],
        "legs": legs,
        "total_distance_km": round(sum(l["distance_km"] for l in legs), 1),
        "total_time_h": round(sum(l["time_h"] for l in legs), 2),
        "total_cost": round(sum(l["cost"] for l in legs), 2),
        "stops": len(path) - 2,
    }


def alternatives(g: nx.DiGraph, source: int, target: int, weight: str, k: int = 4) -> list[dict]:
    """k loop-free shortest paths (Yen's algorithm via networkx.shortest_simple_paths)."""
    paths = islice(nx.shortest_simple_paths(g, source, target, weight=weight), k)
    return [path_summary(g, p) for p in paths]


def inefficient_routes(g: nx.DiGraph, threshold: float = 1.15) -> list[dict]:
    """Direct links whose cost exceeds the cheapest multi-hop alternative by more than `threshold`×,
    or whose road distance is unusually long relative to straight-line distance (detour ratio)."""
    from app.utils.geo import haversine_km

    out = []
    seen = set()
    for u, v, e in g.edges(data=True):
        if (v, u) in seen:
            continue
        seen.add((u, v))
        h = g.copy()
        h.remove_edge(u, v)
        if h.has_edge(v, u):
            h.remove_edge(v, u)
        try:
            best_alt, alt_path = nx.single_source_dijkstra(h, u, v, weight="cost")
        except nx.NetworkXNoPath:
            best_alt, alt_path = None, None
        straight = haversine_km(g.nodes[u]["latitude"], g.nodes[u]["longitude"], g.nodes[v]["latitude"], g.nodes[v]["longitude"])
        detour = e["distance_km"] / straight if straight > 0 else 1
        reasons = []
        if best_alt is not None and e["cost"] > best_alt * threshold:
            reasons.append(f"Direct link costs {e['cost'] / best_alt:.2f}× the cheapest alternative via "
                           f"{' → '.join(g.nodes[n]['code'] for n in alt_path)}")
        if detour > 1.5:
            reasons.append(f"Road distance is {detour:.2f}× the straight-line distance (poor road / detour)")
        if reasons:
            out.append({
                "route_id": e["route_id"], "from": g.nodes[u]["code"], "to": g.nodes[v]["code"],
                "from_name": g.nodes[u]["name"], "to_name": g.nodes[v]["name"],
                "distance_km": round(e["distance_km"], 1), "cost": round(e["cost"], 2), "time_h": round(e["time_h"], 2),
                "detour_ratio": round(detour, 2),
                "best_alternative_cost": None if best_alt is None else round(best_alt, 2),
                "potential_saving": None if best_alt is None else round(max(e["cost"] - best_alt, 0), 2),
                "reasons": reasons,
            })
    return sorted(out, key=lambda r: -(r["potential_saving"] or 0))
