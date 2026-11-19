"""Unit tests: graph algorithms, VRP, security helpers."""
import networkx as nx
import pytest

from app.core.security import Perm, RoleName, create_access_token, decode_access_token, hash_password, permissions_for, verify_password
from app.optimization.logistics import network as net
from app.optimization.logistics.vrp import solve_cvrp

LOCS = [
    {"id": i, "code": c, "name": c, "type": t, "latitude": la, "longitude": lo}
    for i, c, t, la, lo in [
        (1, "W", "warehouse", 19.0, 73.0), (2, "A", "retail_store", 19.5, 73.5), (3, "B", "retail_store", 20.0, 74.0),
        (4, "C", "retail_store", 18.5, 74.0), (5, "D", "retail_store", 18.0, 73.2),
    ]
]
EDGES = [(1, 2, 80), (2, 3, 80), (1, 3, 200), (1, 4, 120), (4, 3, 90), (1, 5, 110), (5, 4, 70)]


def graph():
    routes = []
    rid = 0
    for a, b, d in EDGES:
        for o, t in ((a, b), (b, a)):
            rid += 1
            routes.append({"id": rid, "origin_id": o, "destination_id": t, "distance_km": d, "travel_time_hours": d / 50,
                           "cost_per_km": 30, "toll_cost": 0})
    return net.build_graph(LOCS, routes)


def test_dijkstra_matches_networkx():
    g = graph()
    for s in g.nodes:
        for t in g.nodes:
            if s == t:
                continue
            d, p = net.dijkstra(g, s, t, "distance_km")
            assert d == pytest.approx(nx.dijkstra_path_length(g, s, t, weight="distance_km"))
            assert p[0] == s and p[-1] == t


def test_dijkstra_prefers_two_hop_when_cheaper():
    _, p = net.dijkstra(graph(), 1, 3, "distance_km")
    assert p == [1, 2, 3]  # 160 km < direct 200 km


def test_alternatives_sorted_and_first_is_shortest():
    alts = net.alternatives(graph(), 1, 3, "distance_km", k=3)
    dists = [a["total_distance_km"] for a in alts]
    assert dists == sorted(dists) and alts[0]["path"] == [1, 2, 3]


def test_inefficient_route_detected():
    flagged = {(r["from"], r["to"]) for r in net.inefficient_routes(graph())}
    assert ("W", "B") in flagged or ("B", "W") in flagged


def test_no_path_raises():
    g = graph()
    g.add_node(99, code="X", name="X", latitude=0, longitude=0)
    with pytest.raises(nx.NetworkXNoPath):
        net.dijkstra(g, 1, 99)


def test_vrp_respects_capacity_and_visits_all_stops():
    g = graph()
    res = solve_cvrp(g, 1, [2, 3, 4, 5], {2: 40, 3: 40, 4: 40, 5: 40}, vehicle_capacity=100, num_vehicles=3, time_limit_s=2)
    visited = [s for v in res["vehicles"] for s in v["stop_sequence"] if s != 1]
    assert sorted(visited) == [2, 3, 4, 5]
    assert all(v["load"] <= 100 for v in res["vehicles"])


def test_vrp_rejects_oversized_stop():
    with pytest.raises(ValueError):
        solve_cvrp(graph(), 1, [2], {2: 500}, vehicle_capacity=100, num_vehicles=1)


def test_password_hashing():
    h = hash_password("S3cret-pass")
    assert h != "S3cret-pass" and verify_password("S3cret-pass", h) and not verify_password("wrong", h)


def test_jwt_roundtrip_and_tamper():
    tok = create_access_token("42", "Analyst")
    assert decode_access_token(tok)["sub"] == "42"
    import jwt

    with pytest.raises(jwt.PyJWTError):
        decode_access_token(tok[:-2] + ("A" if tok[-1] != "A" else "B") + tok[-1])


def test_role_permission_matrix():
    assert permissions_for(RoleName.ADMIN.value) == set(Perm)
    mgr = permissions_for(RoleName.MANAGER.value)
    assert Perm.OPTIMIZE in mgr and Perm.UPLOAD_DATA not in mgr and Perm.CONFIGURE not in mgr
    an = permissions_for(RoleName.ANALYST.value)
    assert Perm.UPLOAD_DATA in an and Perm.OPTIMIZE not in an and Perm.SIMULATE not in an
    assert permissions_for("Unknown role") == set()
