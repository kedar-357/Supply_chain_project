# SupplyChainX — Architecture

## 1. Two application layers, no database

```
┌──────────────────────────────┐   REST/JSON + JWT   ┌──────────────────────────────────────────────────┐
│  Frontend  (frontend/)       │ ──────────────────▶ │  Backend  (backend/app, FastAPI)                 │
│  React · TypeScript · Vite   │                     │  api/           HTTP routers (Swagger at /docs)  │
│  Tailwind · shadcn/ui        │ ◀────────────────── │  services/      business logic per module        │
│  Recharts · Axios · Leaflet  │                     │  ml/            forecasting, delay classifier    │
│                              │                     │  optimization/  inventory MILP (PuLP), routing   │
│  Displays data, charts,      │                     │                 (NetworkX: Dijkstra, Yen, CVRP)  │
│  forms; no business logic    │                     │  storage/       file store (CSV / JSON / joblib) │
└──────────────────────────────┘                     └───────────────────────┬──────────────────────────┘
                                                                             │ pandas read/write
                                    ┌────────────────────────────────────────▼──────────────────────────┐
                                    │ Local files (owned by the backend only)                           │
                                    │  datasets/sample/*.csv   original sample datasets (read-only)     │
                                    │  data/*.csv              working tables (products, sales, …)      │
                                    │  data/*.json             settings, users, preferences, scenarios, │
                                    │                          recommendations, optimizations, registry │
                                    │  data/*.jsonl            activity log, route runs                 │
                                    │  data/backups/<file>/    previous versions (newest 10 per file)   │
                                    │  data/uploads/           uploaded CSV/XLSX awaiting import        │
                                    │  data/reports/           generated CSV / XLSX / PDF reports       │
                                    │  artifacts/              versioned joblib model files             │
                                    └───────────────────────────────────────────────────────────────────┘
```

Layering inside the backend: `api → services → (ml | optimization) → storage`. ML and optimisation
modules take pandas DataFrames and never import FastAPI or the store, so they are unit-tested alone.

## 2. File storage design (`app/storage/store.py`)

| Concern | Approach |
|---|---|
| Schema | Each CSV table has a declared schema (columns, int/float/bool/date types, key, defaults). Reads return typed DataFrames; writes validate the key is unique. |
| Atomic writes | Write to `<file>.tmp` in the same folder, then `os.replace` — a crash never leaves a half-written file. |
| Backups | Before any overwrite the previous file is copied to `data/backups/<name>/<timestamp>`; the newest 10 are kept. |
| Originals | `datasets/sample/` is produced once by the init script and never written by the app; imports go into `data/`. `--reset` archives `data/` to `data_archive/` instead of deleting it. |
| Concurrency | A process-wide re-entrant lock guards every read-modify-write sequence (uploads, upserts, JSON updates). |
| Performance | Parsed tables are cached per file and invalidated by modification time, so the 117k-row sales table is parsed once, not per request. |
| Partial updates | Re-uploading a file that omits optional columns keeps the stored values for existing records. |
| Models | Trained models are persisted with joblib and registered (version, metrics, artifact path) in `model_registry.json`; predictions load the latest completed run — models are never retrained per request. |

## 3. Key analytical decisions

| Concern | Decision |
|---|---|
| Data | Deterministic synthetic generator (seed 42): 2 years of daily sales for 40 SKUs × 4 warehouses with trend, weekly/yearly seasonality and promotion lift; (s, Q) replenishment simulation produces POs and deliveries whose delays depend on supplier, season, distance and size. All rows `is_simulated = true`. |
| Forecasting | Per-product XGBoost on leakage-safe lag/rolling/calendar/promo features, recursive multi-step; baselines SARIMA and seasonal naive; chronological 60-day holdout; MAE, RMSE, MAPE (zeros excluded), WAPE. |
| Delay prediction | Chronological 80/20 split; logistic-regression and majority baselines vs Random Forest and XGBoost; supplier history from prior deliveries only; occlusion explanations. |
| Inventory | Safety stock `z·√(L·σd² + d²·σL²)`, ROP, EOQ, ABC (80/95 %), stockout projection with incoming POs, overstock detection. |
| Order optimisation | PuLP/CBC MILP with budget, warehouse capacity and supplier MOQ constraints. |
| Logistics | NetworkX graph; Dijkstra (explicit implementation, tested against NetworkX), Yen k-shortest alternatives, inefficiency detection, Clarke–Wright savings + 2-opt for capacitated multi-stop routes. |
| Scenarios | Re-run the same inventory / optimiser / logistics calculations with modified inputs — deterministic, saved to `scenarios.json`. |
| Auth | bcrypt hashes in `users.json`, JWT (HS256), role → permission matrix enforced by FastAPI dependencies. |

## 4. Checklist

- [x] Two-layer architecture (React frontend, FastAPI backend), no database
- [x] File store: typed CSV tables, JSON documents, JSON-lines logs, atomic writes, rotating backups, read cache
- [x] Reproducible initialisation script with sample datasets (`python -m app.seed.init_data --train`)
- [x] Upload → detect → preview → validate → confirm → import into CSV, templates, never silent acceptance
- [x] Forecasting (XGBoost / SARIMA / naive), delay classifier, persisted joblib models + registry
- [x] Inventory formulas, ABC, stockout/overstock, PuLP optimiser, draft POs
- [x] Supplier scoring, comparison, ML risk; routing (Dijkstra, alternatives, CVRP), inefficiency detection
- [x] Scenarios A–E with save & compare; recommendation engine; reports with CSV / Excel / PDF export
- [x] Persisted user modifications: settings, preferences, roles, stock adjustments, uploads, scenarios, draft POs, recommendation status
- [x] CORS, Swagger docs, RBAC, tests (backend unit/ML/storage/API, frontend), Docker Compose (2 services)
