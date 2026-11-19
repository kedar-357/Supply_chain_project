# SupplyChainX — Predictive Supply Chain & Inventory Optimization Platform

A two-layer decision-support application: a **React frontend** and a **FastAPI backend** that keeps all
data in **local files** (CSV tables, JSON documents, joblib models). **No database is required.**

It combines data science (demand forecasting, delivery-delay prediction), operations research (safety
stock, ROP, EOQ, ABC, MILP order optimisation, shortest paths, vehicle routing) and management decision
support (what-if scenarios, recommendations, reports).

| Layer | Technology |
|---|---|
| Frontend | React 18 · TypeScript · Vite · Tailwind CSS · shadcn/ui-style components (Radix) · Recharts · Axios · Leaflet map |
| Backend | Python 3.11 · FastAPI · Pandas · NumPy · scikit-learn · XGBoost · statsmodels · NetworkX · PuLP |
| Storage | CSV (operational data) · JSON (settings, users, preferences, scenarios, recommendations, model registry) · joblib (models) · generated CSV/XLSX/PDF reports |

Design and storage details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · measured model results:
[`docs/MODEL_EVALUATION.md`](docs/MODEL_EVALUATION.md).

## Quick start (local, no database)

Prerequisites: **Python 3.11** and **Node.js 20+**.

### 1. Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate                       # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                       # macOS/Linux: cp .env.example .env
```

Edit `backend/.env`: set `JWT_SECRET` (any random string of 32+ characters) and the three
`DEMO_*_PASSWORD` values. Then initialise the data and train the models:

```powershell
python -m app.seed.init_data --train         # ~10 s data + ~5 min model training
uvicorn app.main:app --reload --port 8000
```

API documentation (Swagger): <http://localhost:8000/docs> — click **Authorize** and sign in with a demo account.

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The Vite dev server proxies `/api` to `127.0.0.1:8000`; the backend also
allows CORS from `http://localhost:5173` (configure `CORS_ORIGINS` in `backend/.env`). To call the API
directly instead of through the proxy, set `VITE_API_URL=http://localhost:8000` in `frontend/.env`.

### Demo accounts

Created by the init script with the passwords you put in `backend/.env`:

| Email | Role | Can |
|---|---|---|
| admin@supplychainx.demo | Administrator | everything, incl. settings, users, master data, stock adjustments |
| manager@supplychainx.demo | Supply Chain Manager | analytics, training, optimisation, simulation, reports |
| analyst@supplychainx.demo | Analyst | view analytics, upload data, run predictions, reports |

Self-registered users get the Analyst role.

## Where the data lives

```
backend/
  datasets/sample/   original sample CSVs (generated once, never modified by the app)
  data/              working copy used by the API
    *.csv            products, suppliers, warehouses, supplier_products, inventory, sales,
                     purchase_orders, purchase_order_items, deliveries, locations, routes, demand_forecasts
    *.json           settings, users, preferences, scenarios, recommendations, optimizations,
                     uploads, model_registry
    *.jsonl          activity log, route optimisation runs
    backups/         previous version of every rewritten file (newest 10 each)
    uploads/         uploaded files awaiting confirmation
    reports/         every exported CSV / XLSX / PDF report
  artifacts/         trained models (joblib), one folder/file per model version
```

All writes are atomic (temp file + rename) and the previous version is backed up first.

Useful commands (from `backend/`):

```powershell
python -m app.seed.init_data                       # load sample data if data/ is empty (idempotent)
python -m app.seed.init_data --reset --train       # archive data/ to data_archive/ and start fresh
python -m app.seed.init_data --regenerate --reset  # new sample data ending yesterday
```

The sample data is **simulated** (deterministic, seed 42): 40 products in 6 categories, 8 suppliers,
4 warehouses, two years of daily sales (~117k rows), ~3.2k purchase orders and deliveries from a
day-by-day inventory simulation, and a 26-location road network. Every simulated row has
`is_simulated = true`. An adapter for the public UCI Online Retail dataset is included
(`python -m app.seed.prepare_uci_online_retail Online_Retail.xlsx`), producing CSVs you can upload
through Data Management.

## Modules

| Module | What it does |
|---|---|
| Overview Dashboard | 8 KPIs computed from the data files, actual vs backtest vs forecast trend, inventory status, supplier reliability, warehouse value, alerts, activity; date range remembered per user |
| Demand Forecasting | XGBoost vs SARIMA vs seasonal naive per product; 7/30/60/90-day horizons; 95 % intervals; holdout actual-vs-predicted; SHAP explanation; seasonality; product comparison; background training with status |
| Inventory Optimization | Safety stock, ROP, EOQ, ABC Pareto, stockout projection with incoming POs, overstock, adjustable parameters, PuLP optimiser (budget, capacity, MOQ), draft purchase orders, admin stock adjustments |
| Supplier Analytics | Adjustable weighted score, ranking, YoY trend; ML late-delivery classifier with explanations, calibration and open-PO risk; multi-supplier comparison with trade-offs; delivery history |
| Logistics & Routes | Map, Dijkstra (cost/distance/time), k-shortest alternatives, inefficient links, multi-stop vehicle routing (Clarke–Wright + 2-opt), distribution cost model — all labelled as estimates |
| Scenario Simulator | Supplier delay, demand increase, transport cost, procurement budget, warehouse capacity; baseline vs scenario; save & compare |
| Reports & Insights | 8 reports, date filters, CSV / Excel / PDF export; rule-based recommendations with reason, supporting data, impact, priority |
| Data Management | Upload CSV/XLSX → detect columns → preview → validate types, missing values, duplicates, references → confirm → write to CSV; templates; file overview |
| Settings | Business parameters, user roles, profile |

## Docker (optional)

```powershell
copy .env.example .env      # set JWT_SECRET and demo passwords
docker compose up --build
```

Two services: backend on <http://localhost:8000> (data and models on named volumes) and frontend on
<http://localhost:8080>. The first start initialises the data and trains the models.

## Tests

```powershell
cd backend;  python -m pytest     # 79 tests: storage, inventory maths, ML, routing, security, API integration
cd frontend; npm test             # 11 tests: validation, navigation, data loading, errors, scenario flow
```

Backend tests run against a temporary data folder created per session, never `backend/data`.

## Assumptions & limitations

- Travel times and transport costs are estimates from the network model, not live traffic.
- Overheads and logistics assumptions used in scenarios are configurable in Settings.
- Future promotions are unknown, so forecasts assume none unless a plan is supplied.
- Draft purchase orders are never sent to suppliers.
- File storage is designed for a single backend process (one uvicorn worker); it is not meant for
  multiple servers writing the same folder.
