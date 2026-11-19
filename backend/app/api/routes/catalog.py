"""Products, categories, warehouses, inventory records, purchase orders and global search (CSV-backed)."""
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user, require, store_dep
from app.core.security import Perm
from app.schemas.common import InventoryUpdateIn, Page, ProductIn, ProductOut, WarehouseIn
from app.services.activity import log_activity
from app.services.user_service import User
from app.storage.store import Store

router = APIRouter(prefix="/api", tags=["Products & master data"])


def _product_out(r: dict, sup_names: dict) -> ProductOut:
    sid = None if pd.isna(r["primary_supplier_id"]) else int(r["primary_supplier_id"])
    return ProductOut(id=int(r["id"]), sku=r["sku"], name=r["name"], category=r["category"], unit_cost=float(r["unit_cost"]),
                      selling_price=float(r["selling_price"]), ordering_cost=float(r["ordering_cost"]),
                      holding_cost_rate=float(r["holding_cost_rate"]), primary_supplier_id=sid,
                      primary_supplier=sup_names.get(sid), is_active=bool(r["is_active"]), is_simulated=bool(r["is_simulated"]))


def _sup_names(store: Store) -> dict:
    s = store.read("suppliers")
    return dict(zip(s["id"].astype(int), s["name"]))


@router.get("/products", response_model=Page[ProductOut])
def list_products(q: str | None = None, category: str | None = None, active: bool | None = None,
                  page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                  store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    df = store.read("products")
    if q:
        df = df[df["sku"].str.contains(q, case=False, regex=False) | df["name"].str.contains(q, case=False, regex=False)]
    if category:
        df = df[df["category"] == category]
    if active is not None:
        df = df[df["is_active"] == active]
    df = df.sort_values("sku")
    names = _sup_names(store)
    items = [_product_out(r, names) for r in df.iloc[(page - 1) * page_size: page * page_size].to_dict("records")]
    return Page(items=items, total=len(df), page=page, page_size=page_size)


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: int, store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    df = store.read("products")
    row = df[df["id"] == product_id]
    if row.empty:
        raise HTTPException(404, "Product not found")
    return _product_out(row.iloc[0].to_dict(), _sup_names(store))


def _check(store: Store, body: ProductIn):
    if body.primary_supplier_id and body.primary_supplier_id not in set(store.read("suppliers")["id"]):
        raise HTTPException(422, "Unknown primary_supplier_id")
    if body.selling_price < body.unit_cost:
        raise HTTPException(422, "selling_price must not be below unit_cost")


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(body: ProductIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.MANAGE_MASTER_DATA))):
    _check(store, body)
    with store.lock:
        df = store.read("products")
        if body.sku in set(df["sku"]):
            raise HTTPException(409, f"SKU {body.sku} already exists")
        row = {**body.model_dump(), "id": store.next_id("products"), "is_simulated": False}
        store.write("products", pd.concat([df, pd.DataFrame([row])], ignore_index=True))
    log_activity(store, user_id=user.id, action="create", entity_type="product", entity_id=row["id"], description=f"Product {body.sku} created")
    return get_product(row["id"], store, user)


@router.put("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: int, body: ProductIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.MANAGE_MASTER_DATA))):
    _check(store, body)
    with store.lock:
        df = store.read("products")
        if product_id not in set(df["id"]):
            raise HTTPException(404, "Product not found")
        if ((df["sku"] == body.sku) & (df["id"] != product_id)).any():
            raise HTTPException(409, f"SKU {body.sku} already exists")
        for k, v in body.model_dump().items():
            df.loc[df["id"] == product_id, k] = v
        store.write("products", df)
    log_activity(store, user_id=user.id, action="update", entity_type="product", entity_id=product_id, description=f"Product {body.sku} updated")
    return get_product(product_id, store, user)


@router.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, store: Store = Depends(store_dep), user: User = Depends(require(Perm.MANAGE_MASTER_DATA))):
    """Products with sales or purchase history cannot be deleted (409); deactivate them instead."""
    with store.lock:
        df = store.read("products")
        row = df[df["id"] == product_id]
        if row.empty:
            raise HTTPException(404, "Product not found")
        if (store.read("sales")["product_id"] == product_id).any() or (store.read("purchase_order_items")["product_id"] == product_id).any():
            raise HTTPException(409, "Product has sales or purchase history; set it inactive instead of deleting")
        store.write("products", df[df["id"] != product_id])
        for t in ("inventory", "supplier_products"):
            other = store.read(t)
            if (other["product_id"] == product_id).any():
                store.write(t, other[other["product_id"] != product_id])
    log_activity(store, user_id=user.id, action="delete", entity_type="product", entity_id=product_id,
                 description=f"Product {row.iloc[0]['sku']} deleted")


@router.get("/categories")
def categories(store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    counts = store.read("products")["category"].value_counts().sort_index()
    return [{"id": i + 1, "name": n, "product_count": int(c)} for i, (n, c) in enumerate(counts.items())]


@router.get("/warehouses")
def warehouses(store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    used = store.read("inventory").groupby("warehouse_id")["quantity_on_hand"].sum().to_dict()
    return [{"id": int(w.id), "code": w.code, "name": w.name, "city": w.city, "latitude": w.latitude, "longitude": w.longitude,
             "capacity_units": int(w.capacity_units), "units_on_hand": int(used.get(w.id, 0)),
             "utilization_pct": round(used.get(w.id, 0) / w.capacity_units * 100, 1), "is_simulated": bool(w.is_simulated)}
            for w in store.read("warehouses").sort_values("code").itertuples()]


@router.post("/warehouses", status_code=201)
def create_warehouse(body: WarehouseIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.MANAGE_MASTER_DATA))):
    with store.lock:
        df = store.read("warehouses")
        if body.code in set(df["code"]):
            raise HTTPException(409, "Warehouse code already exists")
        wid = store.next_id("warehouses")
        store.write("warehouses", pd.concat([df, pd.DataFrame([{**body.model_dump(), "id": wid, "is_simulated": False}])], ignore_index=True))
    log_activity(store, user_id=user.id, action="create", entity_type="warehouse", entity_id=wid, description=f"Warehouse {body.code} created")
    return {"id": wid, "code": body.code}


@router.put("/warehouses/{warehouse_id}")
def update_warehouse(warehouse_id: int, body: WarehouseIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.MANAGE_MASTER_DATA))):
    with store.lock:
        df = store.read("warehouses")
        if warehouse_id not in set(df["id"]):
            raise HTTPException(404, "Warehouse not found")
        if ((df["code"] == body.code) & (df["id"] != warehouse_id)).any():
            raise HTTPException(409, "Warehouse code already exists")
        for k, v in body.model_dump().items():
            df.loc[df["id"] == warehouse_id, k] = v
        store.write("warehouses", df)
    log_activity(store, user_id=user.id, action="update", entity_type="warehouse", entity_id=warehouse_id, description=f"Warehouse {body.code} updated")
    return {"id": warehouse_id, "code": body.code}


@router.put("/inventory/{product_id}/{warehouse_id}")
def update_inventory(product_id: int, warehouse_id: int, body: InventoryUpdateIn, store: Store = Depends(store_dep),
                     user: User = Depends(require(Perm.MANAGE_MASTER_DATA))):
    """Stock adjustment (e.g. after a physical count). Written to data/inventory.csv with a backup of the previous file."""
    with store.lock:
        df = store.read("inventory")
        mask = (df["product_id"] == product_id) & (df["warehouse_id"] == warehouse_id)
        if not mask.any():
            raise HTTPException(404, "Inventory record not found")
        old = int(df.loc[mask, "quantity_on_hand"].iloc[0])
        df.loc[mask, "quantity_on_hand"] = body.quantity_on_hand
        store.write("inventory", df)
    sku = store.read("products").set_index("id").loc[product_id, "sku"]
    wh = store.read("warehouses").set_index("id").loc[warehouse_id, "code"]
    log_activity(store, user_id=user.id, action="inventory_update", entity_type="inventory", entity_id=product_id,
                 description=f"Stock of {sku} at {wh}: {old} → {body.quantity_on_hand} ({body.reason})")
    return {"product_id": product_id, "warehouse_id": warehouse_id, "quantity_on_hand": body.quantity_on_hand, "previous": old}


@router.get("/purchase-orders")
def purchase_orders(status: str | None = None, supplier_id: int | None = None, page: int = Query(1, ge=1),
                    page_size: int = Query(25, ge=1, le=200), store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    po = store.read("purchase_orders")
    if status:
        po = po[po["status"] == status]
    if supplier_id:
        po = po[po["supplier_id"] == supplier_id]
    po = po.sort_values(["order_date", "id"], ascending=False)
    page_df = po.iloc[(page - 1) * page_size: page * page_size]
    items = store.read("purchase_order_items")
    items = items[items["purchase_order_id"].isin(page_df["id"])]
    skus = dict(zip(*[store.read("products")[c] for c in ("id", "sku")]))
    sup = dict(zip(*[store.read("suppliers")[c] for c in ("id", "name")]))
    whs = dict(zip(*[store.read("warehouses")[c] for c in ("id", "code")]))
    out = []
    for p in page_df.itertuples():
        lines = items[items["purchase_order_id"] == p.id]
        out.append({"id": int(p.id), "po_number": p.po_number, "supplier": sup.get(p.supplier_id), "supplier_id": int(p.supplier_id),
                    "warehouse": whs.get(p.warehouse_id), "order_date": p.order_date.isoformat(), "expected_date": p.expected_date.isoformat(),
                    "status": p.status, "source": p.source, "notes": p.notes or None,
                    "lines": [{"sku": skus.get(l.product_id), "quantity": int(l.quantity), "unit_cost": float(l.unit_cost)} for l in lines.itertuples()],
                    "total_cost": round(float((lines["quantity"] * lines["unit_cost"]).sum()), 2)})
    return {"total": len(po), "page": page, "page_size": page_size, "items": out}


@router.delete("/purchase-orders/{po_id}", status_code=204)
def delete_draft_po(po_id: int, store: Store = Depends(store_dep), user: User = Depends(require(Perm.OPTIMIZE))):
    with store.lock:
        po = store.read("purchase_orders")
        row = po[po["id"] == po_id]
        if row.empty:
            raise HTTPException(404, "Purchase order not found")
        if row.iloc[0]["status"] != "draft":
            raise HTTPException(409, "Only draft purchase orders can be deleted")
        store.write("purchase_orders", po[po["id"] != po_id])
        items = store.read("purchase_order_items")
        store.write("purchase_order_items", items[items["purchase_order_id"] != po_id])
    log_activity(store, user_id=user.id, action="delete", entity_type="purchase_order", entity_id=po_id,
                 description=f"Draft {row.iloc[0]['po_number']} deleted")


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=60), store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    def match(df, cols):
        m = pd.Series(False, index=df.index)
        for c in cols:
            m |= df[c].astype(str).str.contains(q, case=False, regex=False)
        return df[m]

    prods = match(store.read("products"), ["sku", "name"]).head(8)
    sups = match(store.read("suppliers"), ["name", "code", "city"]).head(5)
    whs = match(store.read("warehouses"), ["name", "code", "city"]).head(5)
    locs = match(store.read("locations"), ["name", "code", "city"]).head(5)
    return {"results": (
        [{"type": "product", "id": int(p.id), "label": f"{p.sku} · {p.name}", "path": f"/forecasting?product={p.id}"} for p in prods.itertuples()]
        + [{"type": "supplier", "id": int(s.id), "label": f"{s.code} · {s.name}", "path": f"/suppliers?supplier={s.id}"} for s in sups.itertuples()]
        + [{"type": "warehouse", "id": int(w.id), "label": f"{w.code} · {w.name}", "path": f"/inventory?warehouse={w.id}"} for w in whs.itertuples()]
        + [{"type": "location", "id": int(l.id), "label": f"{l.code} · {l.name}", "path": "/logistics"} for l in locs.itertuples()]
    )}
