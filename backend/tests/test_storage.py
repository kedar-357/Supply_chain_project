"""File-storage tests: typed round-trips, atomic writes, backups, upserts, JSON documents, sample safety."""
from datetime import date

import pandas as pd
import pytest

from app.storage.store import BACKUPS_KEPT, Store


@pytest.fixture()
def fs(tmp_path):
    return Store(tmp_path / "data")


def test_table_roundtrip_preserves_types(fs):
    df = pd.DataFrame({"date": [date(2026, 1, 2)], "product_id": [1], "warehouse_id": [2], "units_sold": [5],
                       "unit_price": [9.5], "discount_pct": [0.0], "promotion": [True], "is_simulated": [False]})
    fs.write("sales", df)
    back = fs.read("sales")
    assert back.loc[0, "date"] == date(2026, 1, 2)
    assert back["units_sold"].dtype == "int64" and back["promotion"].dtype == bool
    assert bool(back.loc[0, "promotion"]) is True


def test_missing_table_reads_as_empty_typed_frame(fs):
    df = fs.read("products")
    assert df.empty and list(df.columns)[:3] == ["id", "sku", "name"]


def test_write_is_atomic_and_backed_up(fs):
    a = pd.DataFrame({"id": [1], "code": ["W1"], "name": ["A"], "city": ["X"], "latitude": [1.0], "longitude": [2.0], "capacity_units": [10]})
    fs.write("warehouses", a)
    fs.write("warehouses", a.assign(name="B"))
    backups = list((fs.root / "backups" / "warehouses").iterdir())
    assert len(backups) == 1 and "A" in backups[0].read_text()
    assert fs.read("warehouses").loc[0, "name"] == "B"
    assert not list(fs.root.glob("*.tmp"))  # temp file replaced atomically


def test_backups_are_rotated(fs):
    a = pd.DataFrame({"id": [1], "code": ["W1"], "name": ["A"], "city": ["X"], "capacity_units": [10]})
    for i in range(BACKUPS_KEPT + 5):
        fs.write("warehouses", a.assign(capacity_units=10 + i))
    assert len(list((fs.root / "backups" / "warehouses").iterdir())) == BACKUPS_KEPT


def test_duplicate_keys_are_rejected(fs):
    a = pd.DataFrame({"id": [1, 1], "code": ["W1", "W2"], "name": ["A", "B"], "city": ["X", "Y"], "capacity_units": [1, 2]})
    with pytest.raises(ValueError):
        fs.write("warehouses", a)


def test_upsert_updates_and_inserts(fs):
    base = pd.DataFrame({"product_id": [1, 2], "warehouse_id": [1, 1], "quantity_on_hand": [5, 6], "as_of_date": [date(2026, 1, 1)] * 2})
    fs.write("inventory", base)
    fs.upsert("inventory", pd.DataFrame({"product_id": [2, 3], "warehouse_id": [1, 1], "quantity_on_hand": [60, 7],
                                         "as_of_date": [date(2026, 1, 2)] * 2}))
    got = fs.read("inventory").set_index("product_id")["quantity_on_hand"].to_dict()
    assert got == {1: 5, 2: 60, 3: 7}


def test_json_documents_and_jsonl(fs):
    assert fs.read_json("settings", {"x": 1}) == {"x": 1}
    fs.update_json("settings", {}, lambda d: {**d, "a": 1})
    fs.update_json("settings", {}, lambda d: {**d, "b": 2})
    assert fs.read_json("settings") == {"a": 1, "b": 2}
    fs.append_jsonl("activity", {"n": 1})
    fs.append_jsonl("activity", {"n": 2})
    assert [r["n"] for r in fs.read_jsonl("activity", last=1)] == [2]


def test_cached_reads_return_copies(fs):
    a = pd.DataFrame({"id": [1], "code": ["W1"], "name": ["A"], "city": ["X"], "capacity_units": [10]})
    fs.write("warehouses", a)
    first = fs.read("warehouses")
    first.loc[0, "name"] = "mutated"
    assert fs.read("warehouses").loc[0, "name"] == "A"


def test_sample_datasets_are_never_modified_by_imports(store, client, analyst):
    from app.core.config import get_settings

    sample = get_settings().sample_dir / "products.csv"
    before = sample.read_bytes()
    csv = b"sku,name,category,unit_cost,selling_price\nSKU-101,Renamed via upload,Electronics,320,549\n"
    up = client.post("/api/data/upload", data={"dataset": "products"}, files={"file": ("p.csv", csv, "text/csv")}, headers=analyst).json()
    assert client.post("/api/data/import", json={"upload_id": up["upload_id"]}, headers=analyst).status_code == 200
    assert sample.read_bytes() == before                       # original untouched
    prods = store.read("products")
    assert prods.loc[prods["sku"] == "SKU-101", "name"].iloc[0] == "Renamed via upload"  # working copy updated
    assert any((store.root / "backups" / "products").iterdir())  # previous version backed up


def test_partial_upload_keeps_existing_optional_fields(store, client, analyst):
    before = store.read("products").set_index("sku").loc["SKU-102"]
    csv = b"sku,name,category,unit_cost,selling_price\nSKU-102,USB-C Fast Charger 25W,Electronics,410,699\n"
    up = client.post("/api/data/upload", data={"dataset": "products"}, files={"file": ("p.csv", csv, "text/csv")}, headers=analyst).json()
    client.post("/api/data/import", json={"upload_id": up["upload_id"]}, headers=analyst)
    after = store.read("products").set_index("sku").loc["SKU-102"]
    assert after["selling_price"] == 699
    assert after["primary_supplier_id"] == before["primary_supplier_id"]  # not wiped by the omitted column
    assert after["ordering_cost"] == before["ordering_cost"]
