"""Integration tests: FastAPI endpoints against the seeded test database."""
from datetime import timedelta

import pytest


# ------------------------------------------------------------------ auth & RBAC
def test_health(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["storage"] == "files"


def test_login_rejects_bad_password(client):
    r = client.post("/api/auth/login", json={"email": "admin@supplychainx.demo", "password": "nope"})
    assert r.status_code == 401


def test_protected_endpoint_requires_token(client):
    assert client.get("/api/dashboard/summary").status_code == 401
    assert client.get("/api/dashboard/summary", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_register_creates_analyst_and_validates(client):
    r = client.post("/api/auth/register", json={"email": "new.user@example.com", "full_name": "New User", "password": "weakpass"})
    assert r.status_code == 422  # no digit
    r = client.post("/api/auth/register", json={"email": "new.user@example.com", "full_name": "New User", "password": "Str0ngPass"})
    assert r.status_code == 201 and r.json()["role"] == "Analyst"
    assert client.post("/api/auth/register", json={"email": "new.user@example.com", "full_name": "X Y", "password": "Str0ngPass"}).status_code == 409
    tok = client.post("/api/auth/login", json={"email": "new.user@example.com", "password": "Str0ngPass"}).json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert "upload_data" in me["permissions"] and "optimize" not in me["permissions"]


def test_rbac_enforced(client, analyst, manager):
    assert client.post("/api/inventory/optimize", json={}, headers=analyst).status_code == 403
    assert client.post("/api/scenarios/simulate", json={"scenario_type": "transport_cost", "config": {}}, headers=analyst).status_code == 403
    assert client.put("/api/settings/inventory", json={"service_level": 0.9}, headers=manager).status_code == 403
    files = {"file": ("p.csv", b"sku\nX", "text/csv")}
    assert client.post("/api/data/upload", data={"dataset": "products"}, files=files, headers=manager).status_code == 403
    assert client.get("/api/users", headers=manager).status_code == 403


# ------------------------------------------------------------------ master data
def test_product_crud_and_conflicts(client, admin, store):
    body = {"sku": "TEST-001", "name": "Test product", "category": "Electronics", "unit_cost": 100, "selling_price": 150}
    r = client.post("/api/products", json=body, headers=admin)
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.post("/api/products", json=body, headers=admin).status_code == 409
    assert client.post("/api/products", json={**body, "sku": "TEST-002", "selling_price": 50}, headers=admin).status_code == 422
    r = client.put(f"/api/products/{pid}", json={**body, "name": "Renamed"}, headers=admin)
    assert r.json()["name"] == "Renamed"
    assert "TEST-001" in set(store.read("products")["sku"])  # persisted to products.csv
    assert client.delete(f"/api/products/{pid}", headers=admin).status_code == 204
    assert "TEST-001" not in set(store.read("products")["sku"])
    # products with history cannot be hard-deleted
    assert client.delete("/api/products/1", headers=admin).status_code == 409


def test_products_pagination(client, manager):
    r = client.get("/api/products", params={"page": 2, "page_size": 10}, headers=manager).json()
    assert r["page"] == 2 and len(r["items"]) == 10 and r["total"] >= 40


# ------------------------------------------------------------------ dashboard KPIs are database-backed
def test_dashboard_summary_matches_data_files(client, manager, store):
    s = client.get("/api/dashboard/summary", params={"days": 30}, headers=manager).json()
    sales = store.read("sales")
    end = max(sales["date"])
    assert s["monthly_demand_units"] == pytest.approx(sales[sales["date"] > end - timedelta(days=30)]["units_sold"].sum(), rel=1e-6)
    inv = store.read("inventory").merge(store.read("products")[["id", "unit_cost", "is_active"]].rename(columns={"id": "product_id"}))
    assert s["total_inventory_value"] == pytest.approx(float((inv["quantity_on_hand"] * inv["unit_cost"]).sum()), rel=1e-6)
    assert s["total_products"] == int(store.read("products")["is_active"].sum())


def test_inventory_adjustment_persists(client, admin, manager, store):
    before = store.read("inventory")
    row = before.iloc[0]
    pid, wid = int(row["product_id"]), int(row["warehouse_id"])
    r = client.put(f"/api/inventory/{pid}/{wid}", json={"quantity_on_hand": 123, "reason": "cycle count"}, headers=admin)
    assert r.status_code == 200 and r.json()["previous"] == int(row["quantity_on_hand"])
    after = store.read("inventory")
    assert int(after[(after.product_id == pid) & (after.warehouse_id == wid)]["quantity_on_hand"].iloc[0]) == 123
    assert client.put(f"/api/inventory/{pid}/{wid}", json={"quantity_on_hand": 5, "reason": "x"}, headers=manager).status_code == 403
    client.put(f"/api/inventory/{pid}/{wid}", json={"quantity_on_hand": int(row["quantity_on_hand"]), "reason": "restore"}, headers=admin)


def test_preferences_roundtrip(client, manager, analyst):
    assert client.put("/api/preferences", json={"dashboard_range_days": 90}, headers=manager).json()["dashboard_range_days"] == 90
    assert client.get("/api/preferences", headers=manager).json()["dashboard_range_days"] == 90
    assert client.get("/api/preferences", headers=analyst).json() == {}  # per user
    assert client.put("/api/preferences", json={"dashboard_range_days": 0}, headers=manager).status_code == 422


def test_dashboard_endpoints(client, manager):
    for path in ("/api/dashboard/trends", "/api/dashboard/alerts", "/api/dashboard/distribution", "/api/dashboard/activity"):
        assert client.get(path, headers=manager).status_code == 200


# ------------------------------------------------------------------ inventory
def test_inventory_rows_are_internally_consistent(client, manager):
    r = client.get("/api/inventory", params={"page_size": 500}, headers=manager).json()
    assert r["total"] == 160
    for it in r["items"]:
        assert it["reorder_point"] == pytest.approx(it["forecast_daily_demand"] * it["lead_time_days"] + it["safety_stock"], rel=1e-2, abs=0.5)  # values are rounded to 2 dp
        assert it["safety_stock"] >= 0 and it["eoq"] >= 0
        assert it["abc_class"] in ("A", "B", "C")


def test_inventory_parameter_overrides(client, manager):
    base = client.get("/api/inventory", params={"q": "SKU-101", "warehouse_id": 1}, headers=manager).json()["items"][0]
    hi = client.get("/api/inventory", params={"q": "SKU-101", "warehouse_id": 1, "service_level": 0.99}, headers=manager).json()["items"][0]
    assert hi["safety_stock"] > base["safety_stock"]
    cheap = client.get("/api/inventory", params={"q": "SKU-101", "warehouse_id": 1, "ordering_cost": 50}, headers=manager).json()["items"][0]
    assert cheap["eoq"] < base["eoq"]


def test_optimizer_respects_budget_and_creates_drafts(client, manager):
    r = client.post("/api/inventory/optimize", json={"budget": 50_000}, headers=manager).json()
    assert r["summary"]["total_order_cost"] <= 50_000 + 1e-6
    ordered = [i for i in r["items"] if i["recommended_qty"] > 0]
    if ordered:
        d = client.post("/api/inventory/draft-purchase-orders", json={"optimization_id": r["optimization_id"],
                        "item_ids": [i["item_id"] for i in ordered]}, headers=manager)
        assert d.status_code == 201
        drafts = client.get("/api/purchase-orders", params={"status": "draft"}, headers=manager).json()
        assert drafts["total"] >= 1


def test_optimizer_rejects_negative_budget(client, manager):
    assert client.post("/api/inventory/optimize", json={"budget": -1}, headers=manager).status_code == 422


# ------------------------------------------------------------------ forecasting & suppliers
def test_forecast_predict_and_metrics(client, manager):
    m = client.get("/api/forecast/metrics", headers=manager).json()
    assert m["trained"] and set(m["portfolio"]) == {"xgboost", "sarima", "seasonal_naive"}
    r = client.post("/api/forecast/predict", json={"product_id": 1, "horizon_days": 30, "model": "xgboost"}, headers=manager).json()
    assert len(r["forecast"]) == 30 and all(f["predicted"] >= 0 for f in r["forecast"])
    assert all(f["lower"] <= f["predicted"] <= f["upper"] for f in r["forecast"])
    assert client.post("/api/forecast/predict", json={"product_id": 1, "horizon_days": 45}, headers=manager).status_code == 422
    # product without a trained model in this (3-product) run
    assert client.post("/api/forecast/predict", json={"product_id": 20, "horizon_days": 7}, headers=manager).status_code == 409


def test_supplier_performance_and_weights(client, manager):
    p = client.get("/api/suppliers/performance", headers=manager).json()
    scores = [s["performance_score"] for s in p["suppliers"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 100 for s in scores)
    only_cost = client.get("/api/suppliers/performance", params={"on_time": 0, "quality": 0, "cost": 1, "lead_time_consistency": 0}, headers=manager).json()
    best = only_cost["suppliers"][0]
    assert best["avg_unit_cost_index"] == min(s["avg_unit_cost_index"] for s in only_cost["suppliers"])


def test_delay_prediction(client, manager):
    r = client.post("/api/suppliers/predict-delay", json={"supplier_id": 1, "warehouse_id": 1, "quantity": 500, "product_id": 1}, headers=manager)
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["delay_probability"] <= 1 and body["kind"] == "ml_prediction"
    cmp = client.post("/api/suppliers/compare", json={"supplier_ids": [1, 7]}, headers=manager).json()
    assert cmp["recommendation"]["summary"]


# ------------------------------------------------------------------ logistics
def test_shortest_route_and_vrp(client, manager):
    locs = client.get("/api/logistics/network", headers=manager).json()["locations"]
    ids = {l["code"]: l["id"] for l in locs}
    r = client.post("/api/logistics/shortest-route", json={"origin_id": ids["WH-MUM"], "destination_id": ids["ST-KOC"], "optimize_for": "distance"}, headers=manager).json()
    assert r["selected"]["codes"][0] == "WH-MUM" and r["selected"]["codes"][-1] == "ST-KOC"
    assert r["selected"]["total_distance_km"] == min(a["total_distance_km"] for a in r["alternatives"])
    v = client.post("/api/logistics/optimize", json={"depot_id": ids["WH-MUM"], "stop_ids": [ids["ST-PUN"], ids["ST-SUR"], ids["ST-GOA"]], "num_vehicles": 2}, headers=manager).json()
    assert v["saving_vs_direct_trips"] >= 0
    bad = client.post("/api/logistics/shortest-route", json={"origin_id": ids["WH-MUM"], "destination_id": ids["WH-MUM"]}, headers=manager)
    assert bad.status_code == 422


# ------------------------------------------------------------------ scenarios
@pytest.mark.parametrize("stype,cfg", [
    ("supplier_delay", {"supplier_id": 2, "delay_days": 15}),
    ("demand_increase", {"increase_pct": 30}),
    ("transport_cost", {"increase_pct": 20}),
    ("budget_limit", {"budget": 20_000}),
    ("capacity_constraint", {"reduction_pct": 30, "warehouse_id": 1}),
])
def test_scenarios_are_deterministic_and_directional(client, manager, stype, cfg):
    a = client.post("/api/scenarios/simulate", json={"scenario_type": stype, "config": cfg}, headers=manager).json()
    b = client.post("/api/scenarios/simulate", json={"scenario_type": stype, "config": cfg}, headers=manager).json()
    assert a["metrics"] == b["metrics"]  # no randomness
    m = {x["metric"]: x for x in a["metrics"]}
    if stype == "supplier_delay":
        assert m["unmet_units"]["scenario"] >= m["unmet_units"]["baseline"]
    if stype == "demand_increase":
        assert m["replenishment_cost"]["scenario"] >= m["replenishment_cost"]["baseline"]
    if stype == "transport_cost":
        assert m["monthly_logistics_cost"]["scenario"] == pytest.approx(m["monthly_logistics_cost"]["baseline"] * 1.2, rel=0.25)
    if stype == "budget_limit":
        assert m["replenishment_cost"]["scenario"] <= 20_000 + 1e-6
    if stype == "capacity_constraint":
        assert m["expected_service_level"]["scenario"] <= m["expected_service_level"]["baseline"] + 1e-9


def test_scenario_save_compare_delete(client, manager):
    ids = []
    for pct in (10, 30):
        r = client.post("/api/scenarios/simulate", json={"scenario_type": "demand_increase", "config": {"increase_pct": pct}, "save": True,
                                                        "name": f"Demand +{pct}"}, headers=manager).json()
        ids.append(r["id"])
    cmp = client.post("/api/scenarios/compare", json={"scenario_ids": ids}, headers=manager).json()
    row = next(t for t in cmp["table"] if t["metric"] == "replenishment_cost")
    assert row[str(ids[1])] >= row[str(ids[0])]
    assert client.get(f"/api/scenarios/{ids[0]}", headers=manager).json()["details"] is not None
    assert client.delete(f"/api/scenarios/{ids[0]}", headers=manager).status_code == 204
    assert client.post("/api/scenarios/simulate", json={"scenario_type": "budget_limit", "config": {"budget": -5}}, headers=manager).status_code == 422


# ------------------------------------------------------------------ data upload workflow
def test_upload_validation_blocks_malformed_rows(client, analyst):
    csv = (
        "sku,name,category,unit_cost,selling_price\n"
        "NEW-1,Good row,Electronics,100,150\n"
        "NEW-2,Bad cost,Electronics,abc,150\n"
        ",Missing sku,Electronics,10,20\n"
        "NEW-1,Duplicate,Electronics,100,150\n"
        "NEW-3,Price below cost,Electronics,100,50\n"
    ).encode()
    r = client.post("/api/data/upload", data={"dataset": "products"}, files={"file": ("p.csv", csv, "text/csv")}, headers=analyst)
    assert r.status_code == 201
    rep = r.json()
    assert rep["row_count"] == 5 and rep["invalid_row_count"] == 4 and rep["duplicate_rows"] == 1
    messages = " ".join(e["message"] for e in rep["errors"])
    assert "number" in messages and "missing" in messages and "duplicate" in messages and "below unit_cost" in messages
    # refused without explicit confirmation
    assert client.post("/api/data/import", json={"upload_id": rep["upload_id"]}, headers=analyst).status_code == 422
    ok = client.post("/api/data/import", json={"upload_id": rep["upload_id"], "skip_invalid": True}, headers=analyst).json()
    assert ok == {"upload_id": rep["upload_id"], "imported": 1, "skipped": 4, "dataset": "products"}
    assert client.post("/api/data/import", json={"upload_id": rep["upload_id"], "skip_invalid": True}, headers=analyst).status_code == 409


def test_upload_detects_missing_columns_and_unknown_references(client, analyst):
    r = client.post("/api/data/upload", data={"dataset": "sales"}, files={"file": ("s.csv", b"date,sku\n2026-01-01,SKU-101\n", "text/csv")}, headers=analyst).json()
    assert set(r["missing_required_columns"]) == {"warehouse_code", "units_sold", "unit_price"}
    csv = b"date,sku,warehouse_code,units_sold,unit_price\n2026-13-01,NOPE,WH-MUM,5,10\n"
    r = client.post("/api/data/upload", data={"dataset": "sales"}, files={"file": ("s.csv", csv, "text/csv")}, headers=analyst).json()
    msgs = " ".join(e["message"] for e in r["errors"])
    assert "date" in msgs and "unknown product" in msgs


def test_upload_rejects_bad_file_type(client, analyst):
    r = client.post("/api/data/upload", data={"dataset": "products"}, files={"file": ("p.txt", b"hello", "text/plain")}, headers=analyst)
    assert r.status_code == 422


def test_templates_and_reports_export(client, manager):
    t = client.get("/api/data/templates/sales", headers=manager)
    assert t.status_code == 200 and t.text.startswith("date,sku,warehouse_code")
    for fmt, magic in (("csv", b"\xef\xbb\xbf"), ("xlsx", b"PK"), ("pdf", b"%PDF")):
        r = client.get("/api/reports/export", params={"report": "inventory", "format": fmt}, headers=manager)
        assert r.status_code == 200 and r.content.startswith(magic), fmt
    assert client.get("/api/reports/unknown", headers=manager).status_code == 422


def test_recommendations_have_required_fields(client, manager):
    recs = client.post("/api/reports/recommendations/generate", headers=manager).json()
    assert recs, "expected at least one recommendation on the demo data"
    for r in recs:
        assert r["title"] and r["reason"] and r["expected_impact"] and r["priority"] in ("high", "medium", "low")
        assert isinstance(r["supporting_data"], dict) and "rule" in r["supporting_data"]


def test_settings_validation(client, admin):
    bad = client.put("/api/settings/supplier_weights", json={"on_time": 0.9, "quality": 0.9}, headers=admin)
    assert bad.status_code == 422
    ok = client.put("/api/settings/inventory", json={"service_level": 0.97}, headers=admin)
    assert ok.status_code == 200 and ok.json()["service_level"] == 0.97
    client.put("/api/settings/inventory", json={"service_level": 0.95}, headers=admin)
