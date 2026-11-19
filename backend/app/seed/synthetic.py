"""Deterministic synthetic supply-chain data generator (SIMULATED DATA).

Produces internally consistent CSVs for every dataset in `app.services.datasets.SPECS`:

* Demand: per product × warehouse daily demand = base × trend × weekly × yearly seasonality ×
  festival bump × promotion lift, drawn from a gamma-Poisson (negative binomial) distribution.
* Replenishment: a day-by-day inventory simulation with an (s, Q) policy. Order lines placed on
  the same day to the same supplier/warehouse are consolidated into one purchase order.
* Deliveries: delay = supplier base delay + monsoon / festive-season sensitivity + order-size and
  distance effects + supplier-specific noise (+ a deterioration trend for one supplier). The delay
  classifier therefore learns real, explainable structure — it is not given labels by chance.
* Sales are capped by available stock, so sales, inventory and receipts reconcile exactly.

Everything is generated from a fixed seed, so the demo is reproducible. All rows carry
`is_simulated=true`.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
HISTORY_DAYS = 730


@dataclass(frozen=True)
class SupplierDef:
    code: str
    name: str
    city: str
    region: str
    lat: float
    lon: float
    quality: float
    cost_mult: float
    lead_days: int
    base_delay: float
    delay_sd: float
    monsoon_sens: float = 0.0
    festive_sens: float = 0.0
    deteriorates: bool = False


SUPPLIERS = [
    SupplierDef("SUP-01", "Apex Components Pvt Ltd", "Pune", "West", 18.52, 73.86, 4.6, 1.06, 7, -0.6, 1.0, 0.5),
    SupplierDef("SUP-02", "Bharat Electronics Supply", "Bengaluru", "South", 12.97, 77.59, 4.2, 0.97, 10, 0.6, 1.6, 1.0, 1.0),
    SupplierDef("SUP-03", "Coastal Traders & Co", "Chennai", "South", 13.08, 80.27, 3.8, 0.90, 12, 1.2, 2.2, 3.5, 0.5),
    SupplierDef("SUP-04", "Delta Home Essentials", "Ahmedabad", "West", 23.02, 72.57, 4.4, 1.00, 8, -0.2, 1.2, 0.8, 0.8),
    SupplierDef("SUP-05", "Eastern Agro Foods", "Kolkata", "East", 22.57, 88.36, 4.0, 0.93, 6, 0.4, 1.8, 3.0, 0.5),
    SupplierDef("SUP-06", "FabWeave Textiles", "Tiruppur", "South", 11.11, 77.34, 4.3, 0.95, 14, 0.4, 1.8, 1.0, 2.5),
    SupplierDef("SUP-07", "Gulf Imports & Logistics", "Navi Mumbai", "West", 19.03, 73.03, 3.6, 0.85, 21, 1.6, 2.6, 1.5, 1.5, True),
    SupplierDef("SUP-08", "Himalaya Personal Care Mfg", "Haridwar", "North", 29.95, 78.16, 4.7, 1.08, 9, -0.4, 1.1, 1.0),
]

WAREHOUSES = [
    # code, name, city, lat, lon, demand share
    ("WH-MUM", "Mumbai Fulfilment Centre (Bhiwandi)", "Mumbai", 19.30, 73.06, 0.32),
    ("WH-DEL", "Delhi NCR Fulfilment Centre (Gurugram)", "Gurugram", 28.46, 77.03, 0.28),
    ("WH-BLR", "Bengaluru Fulfilment Centre (Hoskote)", "Bengaluru", 13.08, 77.80, 0.24),
    ("WH-KOL", "Kolkata Fulfilment Centre (Howrah)", "Kolkata", 22.59, 88.26, 0.16),
]

# category -> (primary supplier, alternate supplier, season profile)
CATEGORIES = {
    "Electronics": ("SUP-02", "SUP-07", "festive"),
    "Home & Kitchen": ("SUP-04", "SUP-01", "festive_light"),
    "Personal Care": ("SUP-08", "SUP-03", "summer"),
    "Groceries & Staples": ("SUP-05", "SUP-03", "flat"),
    "Apparel": ("SUP-06", "SUP-07", "winter"),
    "Office Supplies": ("SUP-01", "SUP-04", "school"),
}

# name, category, unit_cost (INR), markup, base total daily demand
PRODUCTS = [
    ("Wireless Optical Mouse", "Electronics", 320, 1.65, 46),
    ("USB-C Fast Charger 25W", "Electronics", 410, 1.70, 58),
    ("Bluetooth Earbuds Pro", "Electronics", 1350, 1.55, 22),
    ("Smart LED Bulb 9W", "Electronics", 240, 1.80, 64),
    ("Portable Power Bank 10000mAh", "Electronics", 780, 1.60, 30),
    ("HDMI Cable 2m", "Electronics", 150, 2.10, 25),
    ("Mechanical Keyboard", "Electronics", 2100, 1.45, 7),
    ("Stainless Steel Water Bottle 1L", "Home & Kitchen", 210, 1.90, 52),
    ("Non-stick Frying Pan 26cm", "Home & Kitchen", 640, 1.70, 18),
    ("Glass Storage Containers (Set of 3)", "Home & Kitchen", 480, 1.75, 21),
    ("Electric Kettle 1.5L", "Home & Kitchen", 890, 1.55, 14),
    ("Cotton Bath Towel", "Home & Kitchen", 260, 1.95, 33),
    ("Pressure Cooker 5L", "Home & Kitchen", 1450, 1.45, 6),
    ("Herbal Shampoo 340ml", "Personal Care", 145, 1.85, 88),
    ("Sunscreen SPF50 100ml", "Personal Care", 260, 1.90, 41),
    ("Aloe Vera Face Wash", "Personal Care", 120, 2.00, 72),
    ("Electric Trimmer", "Personal Care", 980, 1.50, 9),
    ("Moisturising Body Lotion 400ml", "Personal Care", 210, 1.85, 37),
    ("Basmati Rice 5kg", "Groceries & Staples", 520, 1.22, 95),
    ("Cold-pressed Groundnut Oil 1L", "Groceries & Staples", 215, 1.25, 110),
    ("Organic Toor Dal 1kg", "Groceries & Staples", 140, 1.28, 125),
    ("Assam Tea 500g", "Groceries & Staples", 190, 1.35, 70),
    ("Whole Wheat Atta 10kg", "Groceries & Staples", 380, 1.20, 80),
    ("Instant Coffee 200g", "Groceries & Staples", 330, 1.40, 34),
    ("Men's Cotton T-shirt", "Apparel", 240, 2.20, 44),
    ("Women's Woollen Shawl", "Apparel", 560, 1.95, 15),
    ("Unisex Hooded Sweatshirt", "Apparel", 690, 1.90, 18),
    ("Denim Jeans Slim Fit", "Apparel", 720, 1.85, 24),
    ("Sports Socks (Pack of 3)", "Apparel", 110, 2.30, 60),
    ("Rain Jacket", "Apparel", 640, 1.80, 8),
    ("A4 Copier Paper (500 sheets)", "Office Supplies", 260, 1.35, 55),
    ("Gel Pens (Pack of 10)", "Office Supplies", 85, 2.00, 70),
    ("Spiral Notebook A5", "Office Supplies", 45, 2.20, 95),
    ("Desk Organizer", "Office Supplies", 310, 1.75, 9),
    ("Whiteboard Markers (Set of 4)", "Office Supplies", 95, 1.95, 28),
    ("Ergonomic Office Chair", "Office Supplies", 4200, 1.40, 3),
    ("Stapler Heavy Duty", "Office Supplies", 180, 1.90, 12),
    ("Laptop Backpack", "Electronics", 950, 1.60, 16),
    ("Mosquito Repellent Refill", "Personal Care", 65, 1.90, 64),
    ("Kitchen Knife Set", "Home & Kitchen", 530, 1.80, 3),
]

# Products with a demand surge in the last ~45 days (low stock risk) and bulk overstock buys
SURGE_SKUS = {"SKU-104", "SKU-115", "SKU-127"}
OVERSTOCK_SKUS = {"SKU-109": "WH-DEL", "SKU-122": "WH-MUM", "SKU-134": "WH-BLR"}
WEEKLY = np.array([0.93, 0.95, 0.97, 1.00, 1.07, 1.16, 1.12])  # Mon..Sun

NETWORK_DCS = [
    ("DC-NAG", "Nagpur Distribution Centre", "Nagpur", 21.15, 79.09),
    ("DC-HYD", "Hyderabad Distribution Centre", "Hyderabad", 17.39, 78.49),
    ("DC-LKO", "Lucknow Distribution Centre", "Lucknow", 26.85, 80.95),
]
NETWORK_STORES = [
    ("ST-PUN", "Pune Retail Hub", "Pune", 18.56, 73.81, 640),
    ("ST-SUR", "Surat Retail Hub", "Surat", 21.17, 72.83, 420),
    ("ST-JAI", "Jaipur Retail Hub", "Jaipur", 26.91, 75.79, 380),
    ("ST-CHD", "Chandigarh Retail Hub", "Chandigarh", 30.73, 76.78, 300),
    ("ST-CHN", "Chennai Retail Hub", "Chennai", 13.05, 80.21, 610),
    ("ST-KOC", "Kochi Retail Hub", "Kochi", 9.98, 76.28, 280),
    ("ST-IND", "Indore Retail Hub", "Indore", 22.72, 75.86, 330),
    ("ST-BBS", "Bhubaneswar Retail Hub", "Bhubaneswar", 20.30, 85.82, 240),
    ("ST-VTZ", "Visakhapatnam Retail Hub", "Visakhapatnam", 17.69, 83.22, 220),
    ("ST-PAT", "Patna Retail Hub", "Patna", 25.59, 85.14, 260),
    ("ST-GOA", "Goa Retail Hub", "Panaji", 15.49, 73.83, 180),
]


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _season_factor(profile: str, doy: np.ndarray) -> np.ndarray:
    def bump(center, width, amp):
        d = np.minimum(np.abs(doy - center), 365 - np.abs(doy - center))
        return amp * np.exp(-0.5 * (d / width) ** 2)

    if profile == "festive":
        return 1 + 0.12 * np.cos(2 * np.pi * (doy - 300) / 365) + bump(300, 14, 0.55)
    if profile == "festive_light":
        return 1 + 0.08 * np.cos(2 * np.pi * (doy - 300) / 365) + bump(300, 16, 0.30)
    if profile == "summer":
        return 1 + 0.30 * np.cos(2 * np.pi * (doy - 130) / 365)
    if profile == "winter":
        return 1 + 0.38 * np.cos(2 * np.pi * (doy - 10) / 365) + bump(300, 12, 0.25)
    if profile == "school":
        return 1 + bump(160, 12, 0.45) + bump(15, 10, 0.20) - 0.05
    return 1 + 0.05 * np.cos(2 * np.pi * (doy - 60) / 365)


def generate(out_dir: Path, end_date: date | None = None) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    end = end_date or (date.today() - timedelta(days=1))
    start = end - timedelta(days=HISTORY_DAYS - 1)
    dates = pd.date_range(start, end, freq="D")
    n = len(dates)
    doy = dates.dayofyear.to_numpy()
    dow = dates.dayofweek.to_numpy()
    t = np.arange(n)
    month = dates.month.to_numpy()
    sup_by_code = {s.code: s for s in SUPPLIERS}

    # ------------------------------------------------------------------ master data
    suppliers_df = pd.DataFrame(
        [
            {
                "code": s.code, "name": s.name, "city": s.city, "region": s.region, "country": "India",
                "latitude": s.lat, "longitude": s.lon, "quality_rating": s.quality,
                "contact_email": f"orders@{s.code.lower()}.example", "is_simulated": True,
            }
            for s in SUPPLIERS
        ]
    )

    prod_rows, sp_rows, pinfo = [], [], []
    for i, (name, cat, cost, markup, base) in enumerate(PRODUCTS):
        sku = f"SKU-{101 + i}"
        primary, alt, season = CATEGORIES[cat]
        if cat == "Electronics" and i % 3 == 2:
            primary, alt = alt, primary  # some electronics primarily imported
        price = round(cost * markup / 5) * 5 - 1
        prod_rows.append({
            "sku": sku, "name": name, "category": cat, "unit_cost": float(cost), "selling_price": float(price),
            "ordering_cost": float(350 + 50 * int(rng.integers(0, 8))),
            "holding_cost_rate": round(float(rng.uniform(0.18, 0.28)), 3),
            "primary_supplier_code": primary, "is_simulated": True,
        })
        moq = int(max(10, round(base * 4 / 10) * 10))
        for code in (primary, alt):
            s = sup_by_code[code]
            sp_rows.append({
                "supplier_code": code, "sku": sku,
                "unit_cost": round(cost * s.cost_mult * float(rng.uniform(0.98, 1.02)), 2),
                "lead_time_days": s.lead_days, "min_order_qty": moq if code == primary else moq * 2,
            })
        pinfo.append({
            "sku": sku, "base": base, "season": season, "primary": primary, "alt": alt, "price": price,
            "growth": float(rng.uniform(-0.06, 0.22)), "elasticity": float(rng.uniform(1.5, 3.5)),
            "moq": moq, "cost": cost,
        })

    # ------------------------------------------------------------------ demand
    demand = {}  # (sku, wh) -> np.array
    promo_disc = {}  # sku -> np.array of discount fraction
    for p in pinfo:
        disc = np.zeros(n)
        for _ in range(8):
            s0 = int(rng.integers(0, n - 8))
            disc[s0 : s0 + int(rng.integers(4, 8))] = rng.choice([0.10, 0.15, 0.20, 0.25])
        promo_disc[p["sku"]] = disc
        trend = (1 + p["growth"]) ** (t / 365)
        lam = p["base"] * trend * WEEKLY[dow] * _season_factor(p["season"], doy) * (1 + p["elasticity"] * disc)
        if p["sku"] in SURGE_SKUS:
            lam = lam * np.where(t > n - 45, 1 + 0.40 * np.clip((t - (n - 45)) / 20, 0, 1), 1.0)
        for (code, _, _, _, _, share) in WAREHOUSES:
            local_share = share * float(rng.uniform(0.85, 1.15))
            mu = np.maximum(lam * local_share, 0.05)
            k = 6.0
            demand[(p["sku"], code)] = rng.poisson(rng.gamma(k, mu / k))

    # ------------------------------------------------------------------ inventory simulation (s, Q)
    lead = {p["sku"]: sup_by_code[p["primary"]].lead_days for p in pinfo}
    inv = {}
    for p in pinfo:
        for code, *_rest in WAREHOUSES:
            inv[(p["sku"], code)] = int(demand[(p["sku"], code)][:30].mean() * (lead[p["sku"]] + 20))
    pipeline = defaultdict(int)  # (sku, wh) -> units on order
    arrivals = defaultdict(list)  # day index -> list of (sku, wh, qty)
    sales_units = {k: np.zeros(n, dtype=int) for k in demand}
    po_rows, del_rows = [], []
    po_seq = 0
    wh_by_code = {w[0]: w for w in WAREHOUSES}
    pinfo_by_sku = {p["sku"]: p for p in pinfo}
    sp_cost = {(r["supplier_code"], r["sku"]): r["unit_cost"] for r in sp_rows}

    def delay_for(sup: SupplierDef, day_idx: int, qty: int, dist: float) -> int:
        m = month[day_idx]
        monsoon = 1.0 if m in (6, 7, 8, 9) else 0.0
        festive = 1.0 if m in (10, 11) else 0.0
        det = 2.2 * max(0.0, (day_idx - (n - 200)) / 200) if sup.deteriorates else 0.0
        mu = (
            sup.base_delay + sup.monsoon_sens * monsoon + sup.festive_sens * festive
            + 0.55 * math.log1p(qty / 400) + 0.0012 * dist + det - 2.9
        )
        return int(np.clip(round(mu + rng.normal(0, sup.delay_sd)), -3, 30))

    for d in range(n):
        for sku, code, qty in arrivals.pop(d, []):
            inv[(sku, code)] += qty
            pipeline[(sku, code)] -= qty
        orders = defaultdict(list)  # (supplier, wh) -> [(sku, qty)]
        for (sku, code), dem in demand.items():
            sold = min(int(dem[d]), inv[(sku, code)])
            sales_units[(sku, code)][d] = sold
            inv[(sku, code)] -= sold
            p = pinfo_by_sku[sku]
            recent = dem[max(0, d - 28) : d + 1].mean()
            s_level = recent * (lead[sku] + 9)
            if inv[(sku, code)] + pipeline[(sku, code)] <= s_level and d < n - 1:
                q = max(p["moq"], int(math.ceil(recent * 35 / 10) * 10))
                supplier = p["primary"] if rng.random() < 0.8 else p["alt"]
                orders[(supplier, code)].append((sku, q))
        if d == n - 50:  # one-off bulk "deal" purchases that create overstock
            for sku, code in OVERSTOCK_SKUS.items():
                p = pinfo_by_sku[sku]
                q = int(math.ceil(demand[(sku, code)][d - 60 : d].mean() * 150 / 10) * 10)
                orders[(p["primary"], code)].append((sku, q))
        for (scode, code), lines in orders.items():
            sup = sup_by_code[scode]
            wh = wh_by_code[code]
            po_seq += 1
            order_day = dates[d].date()
            po_number = f"PO-{order_day.year}-{po_seq:05d}"
            promised = order_day + timedelta(days=sup.lead_days)
            total_qty = sum(q for _, q in lines)
            dist = haversine_km(sup.lat, sup.lon, wh[3], wh[4]) * 1.25
            delay = delay_for(sup, d, total_qty, dist)
            actual_idx = d + sup.lead_days + delay
            fill = 1.0 if rng.random() < 0.9 else float(rng.uniform(0.9, 0.99))
            delivered = actual_idx <= n - 1
            status = "received" if delivered else ("in_transit" if (n - 1 - d) >= 2 else "placed")
            for sku, q in lines:
                po_rows.append({
                    "po_number": po_number, "supplier_code": scode, "warehouse_code": code,
                    "order_date": order_day, "expected_date": promised, "status": status, "sku": sku,
                    "quantity": q, "unit_cost": sp_cost[(scode, sku)], "is_simulated": True,
                })
                pipeline[(sku, code)] += q
                if delivered:
                    arrivals[actual_idx].append((sku, code, int(q * fill)))
                    pipeline[(sku, code)] -= q - int(q * fill)  # short-shipped units never arrive
            if delivered:
                qrating = 100 - (5 - sup.quality) * 4
                del_rows.append({
                    "po_number": po_number, "actual_date": promised + timedelta(days=delay),
                    "quantity_received": int(sum(int(q * fill) for _, q in lines)),
                    "quality_score": round(float(np.clip(rng.normal(qrating, 1.6), 70, 100)), 1),
                    "distance_km": round(dist, 1), "is_simulated": True,
                })

    # ------------------------------------------------------------------ assemble tables
    sales_rows = []
    for (sku, code), units in sales_units.items():
        p = pinfo_by_sku[sku]
        disc = promo_disc[sku]
        frame = pd.DataFrame({
            "date": dates.date, "sku": sku, "warehouse_code": code, "units_sold": units,
            "unit_price": np.round(p["price"] * (1 - disc), 2), "discount_pct": np.round(disc * 100, 1),
            "promotion": disc > 0, "is_simulated": True,
        })
        sales_rows.append(frame)
    sales_df = pd.concat(sales_rows, ignore_index=True)

    inv_df = pd.DataFrame([
        {"sku": sku, "warehouse_code": code, "quantity_on_hand": int(q), "as_of_date": end}
        for (sku, code), q in inv.items()
    ])
    # capacity such that current utilisation is ~70% (plus room for in-transit stock)
    occ = inv_df.groupby("warehouse_code")["quantity_on_hand"].sum()
    warehouses_df = pd.DataFrame([
        {"code": c, "name": nm, "city": city, "latitude": la, "longitude": lo,
         "capacity_units": int(math.ceil(occ[c] / 0.70 / 1000) * 1000), "is_simulated": True}
        for c, nm, city, la, lo, _ in WAREHOUSES
    ])

    locations, routes = _network(rng)

    tables = {
        "suppliers": suppliers_df,
        "warehouses": warehouses_df,
        "products": pd.DataFrame(prod_rows),
        "supplier_products": pd.DataFrame(sp_rows),
        "inventory": inv_df,
        "sales": sales_df,
        "purchase_orders": pd.DataFrame(po_rows),
        "deliveries": pd.DataFrame(del_rows),
        "locations": locations,
        "routes": routes,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out_dir / f"{name}.csv", index=False)
    return tables


def _network(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    nodes = []
    for code, name, city, la, lo, _ in WAREHOUSES:
        nodes.append({"code": code, "name": name, "location_type": "warehouse", "city": city, "latitude": la,
                      "longitude": lo, "daily_demand_units": 0, "warehouse_code": code, "supplier_code": None})
    for code, name, city, la, lo in NETWORK_DCS:
        nodes.append({"code": code, "name": name, "location_type": "distribution_center", "city": city,
                      "latitude": la, "longitude": lo, "daily_demand_units": 0, "warehouse_code": None, "supplier_code": None})
    for code, name, city, la, lo, dem in NETWORK_STORES:
        nodes.append({"code": code, "name": name, "location_type": "retail_store", "city": city, "latitude": la,
                      "longitude": lo, "daily_demand_units": dem, "warehouse_code": None, "supplier_code": None})
    for s in SUPPLIERS:
        nodes.append({"code": f"N-{s.code}", "name": s.name, "location_type": "supplier", "city": s.city,
                      "latitude": s.lat, "longitude": s.lon, "daily_demand_units": 0, "warehouse_code": None,
                      "supplier_code": s.code})
    loc = pd.DataFrame(nodes)
    loc["is_simulated"] = True

    # Road links: every pair closer than 700 km plus each node's 3 nearest neighbours.
    coords = loc[["latitude", "longitude"]].to_numpy()
    m = len(loc)
    dist = np.array([[haversine_km(*coords[i], *coords[j]) for j in range(m)] for i in range(m)])
    pairs = set()
    for i in range(m):
        order = np.argsort(dist[i])
        for j in order[1:4]:
            pairs.add(tuple(sorted((i, int(j)))))
        for j in range(m):
            if i != j and dist[i, j] < 700:
                pairs.add(tuple(sorted((i, j))))
    pairs = sorted(pairs)
    poor_roads = {pairs[k] for k in rng.choice(len(pairs), size=6, replace=False)}
    rows = []
    for i, j in pairs:
        poor = (i, j) in poor_roads
        factor = float(rng.uniform(1.62, 1.85)) if poor else float(rng.uniform(1.18, 1.32))
        road_km = round(dist[i, j] * factor, 1)
        speed = float(rng.uniform(32, 40)) if poor else float(rng.uniform(46, 58))
        cpk = round(float(rng.uniform(34, 40)) if poor else float(rng.uniform(27, 33)), 2)
        toll = 0.0 if poor else round(road_km * float(rng.uniform(1.2, 2.0)), 0)
        for a, b in ((i, j), (j, i)):
            rows.append({
                "origin_code": loc.at[a, "code"], "destination_code": loc.at[b, "code"], "distance_km": road_km,
                "travel_time_hours": round(road_km / speed, 2), "cost_per_km": cpk, "toll_cost": toll,
                "is_simulated": True,
            })
    return loc, pd.DataFrame(rows)


if __name__ == "__main__":  # pragma: no cover
    out = Path(__file__).resolve().parents[2] / "datasets" / "synthetic"
    tabs = generate(out)
    for k, v in tabs.items():
        print(f"{k:18s} {len(v):>8,d} rows")
