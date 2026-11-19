"""SupplyChainX API entry point.  Run: uvicorn app.main:app --reload

Two-layer architecture: this FastAPI backend owns all data processing, ML, optimisation and file-based
persistence (CSV / JSON / joblib under backend/data and backend/artifacts); the React frontend only
renders what the API returns.
"""
import logging
import math
import time
from contextlib import asynccontextmanager
from datetime import date, datetime

import numpy as np
from fastapi import FastAPI, Request
from fastapi.encoders import ENCODERS_BY_TYPE
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import analytics, auth, catalog, operations
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.services import model_registry
from app.storage.store import get_store

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("supplychainx.api")

# Results are computed with pandas/NumPy: teach FastAPI's encoder about NumPy scalars.
ENCODERS_BY_TYPE.update({
    np.integer: int, np.int64: int, np.int32: int, np.floating: float, np.float64: float, np.float32: float,
    np.bool_: bool, np.ndarray: lambda a: a.tolist(),
})


def _finite(o):
    """Replace NaN/inf (not valid JSON) with null, recursively."""
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_finite(v) for v in o]
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return o


class SafeJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return super().render(_finite(content))


@asynccontextmanager
async def lifespan(_: FastAPI):
    store = get_store()
    model_registry.mark_stale_running_as_failed(store)
    if not store.exists("sales"):
        log.warning("No working data found in %s. Run `python -m app.seed.init_data --train` first.", settings.data_dir)
    yield


app = FastAPI(
    title="SupplyChainX API",
    version="2.0.0",
    description=(
        "Predictive supply chain & inventory optimization platform (file-based storage: CSV / JSON / joblib). "
        "Authenticate with **Authorize** (email + password) to try endpoints. Records flagged `is_simulated` "
        "come from the synthetic sample datasets."
    ),
    default_response_class=SafeJSONResponse,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
register_exception_handlers(app)


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code, (time.perf_counter() - start) * 1000)
    return response


for r in (
    auth.router, auth.users_router, auth.prefs_router, catalog.router, analytics.dashboard, analytics.forecast,
    analytics.inventory, analytics.suppliers, operations.logistics, operations.scenarios, operations.reports,
    operations.data, operations.settings_router,
):
    app.include_router(r)


@app.get("/api/health", tags=["System"])
def health():
    store = get_store()
    return {"status": "ok" if store.exists("sales") else "no_data", "app": settings.app_name, "storage": "files",
            "data_dir": str(settings.data_dir), "demo_mode": settings.demo_mode}
