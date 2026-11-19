"""Logistics, scenarios, reports, data management and settings endpoints."""
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.api.deps import get_current_user, require, store_dep
from app.core.config import get_settings
from app.core.exceptions import ValidationFailed
from app.core.security import Perm
from app.schemas.common import ImportIn, RecommendationStatusIn, ScenarioCompareIn, ScenarioIn, ShortestRouteIn, VRPIn
from app.services import datasets, logistics_service, recommendation_service, report_service, scenario_service, settings_service
from app.services.activity import log_activity
from app.services.user_service import User
from app.storage.store import TABLES, Store, utcnow_iso

logistics = APIRouter(prefix="/api/logistics", tags=["Logistics & routes"])
scenarios = APIRouter(prefix="/api/scenarios", tags=["Scenario simulator"])
reports = APIRouter(prefix="/api/reports", tags=["Reports & insights"])
data = APIRouter(prefix="/api/data", tags=["Data management"])
settings_router = APIRouter(prefix="/api/settings", tags=["Settings"])

VIEW = Depends(require(Perm.VIEW_ANALYTICS))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


# ====================================================================== logistics
@logistics.get("/network")
def network(store: Store = Depends(store_dep), _: User = VIEW):
    return logistics_service.network(store)


@logistics.post("/shortest-route")
def shortest_route(body: ShortestRouteIn, store: Store = Depends(store_dep), user: User = VIEW):
    return logistics_service.shortest_route(store, body.origin_id, body.destination_id, body.optimize_for, user.id, body.cost_multiplier)


@logistics.post("/optimize")
def optimize(body: VRPIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.OPTIMIZE))):
    return logistics_service.optimize_vrp(store, body.depot_id, body.stop_ids, body.demands, body.vehicle_capacity,
                                          body.num_vehicles, user.id, body.cost_multiplier)


@logistics.get("/inefficient-routes")
def inefficient(store: Store = Depends(store_dep), _: User = VIEW):
    return logistics_service.inefficient(store)


@logistics.get("/cost-model")
def cost_model(cost_multiplier: float = Query(1.0, gt=0, le=5), store: Store = Depends(store_dep), _: User = VIEW):
    return logistics_service.distribution_cost_model(store, cost_multiplier)


# ====================================================================== scenarios
@scenarios.get("/types")
def scenario_types(_: User = VIEW):
    return [{"key": k, "label": v} for k, v in scenario_service.SCENARIO_TYPES.items()]


@scenarios.post("/simulate")
def simulate(body: ScenarioIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.SIMULATE))):
    result = scenario_service.simulate(store, body.scenario_type, body.config)
    if body.save:
        name = body.name or f"{result['scenario_label']} — {datetime.now():%d %b %H:%M}"
        sc = scenario_service.save(store, name, result, user.id)
        result["id"] = sc["id"]
        result["name"] = sc["name"]
    return result


@scenarios.get("")
def list_scenarios(store: Store = Depends(store_dep), _: User = VIEW):
    return [scenario_service.to_dict(x, full=False) for x in scenario_service.list_saved(store)]


@scenarios.get("/{scenario_id}")
def get_scenario(scenario_id: int, store: Store = Depends(store_dep), _: User = VIEW):
    sc = scenario_service.get(store, scenario_id)
    if sc is None:
        raise HTTPException(404, "Scenario not found")
    return scenario_service.to_dict(sc)


@scenarios.delete("/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: int, store: Store = Depends(store_dep), user: User = Depends(require(Perm.SIMULATE))):
    sc = scenario_service.delete(store, scenario_id)
    log_activity(store, user_id=user.id, action="delete", entity_type="scenario", entity_id=scenario_id,
                 description=f"Scenario '{sc['name']}' deleted")


@scenarios.post("/compare")
def compare(body: ScenarioCompareIn, store: Store = Depends(store_dep), _: User = VIEW):
    return scenario_service.compare(store, body.scenario_ids)


# ====================================================================== reports
@reports.get("")
def list_reports(_: User = VIEW):
    return [{"key": k, "title": v} for k, v in report_service.REPORTS.items()]


@reports.get("/export")
def export(report: str, format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"), start: date | None = None, end: date | None = None,
           store: Store = Depends(store_dep), user: User = Depends(require(Perm.GENERATE_REPORTS))):
    content, media, filename = report_service.export(store, report, format, start, end)
    log_activity(store, user_id=user.id, action="export", entity_type="report", description=f"Exported {report} report as {format.upper()}")
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@reports.get("/recommendations")
def recommendations(category: str | None = None, priority: str | None = None, store: Store = Depends(store_dep), _: User = VIEW):
    return recommendation_service.list_recommendations(store, category, priority)


@reports.post("/recommendations/generate")
def generate_recommendations(store: Store = Depends(store_dep), user: User = Depends(require(Perm.GENERATE_REPORTS))):
    return recommendation_service.generate(store, user.id)


@reports.patch("/recommendations/{rec_id}", status_code=204)
def recommendation_status(rec_id: int, body: RecommendationStatusIn, store: Store = Depends(store_dep),
                          _: User = Depends(require(Perm.GENERATE_REPORTS))):
    recommendation_service.set_status(store, rec_id, body.status)


@reports.get("/{report}")
def get_report(report: str, start: date | None = None, end: date | None = None, store: Store = Depends(store_dep), _: User = VIEW):
    return report_service.build(store, report, start, end)


# ====================================================================== data management
@data.get("/templates")
def templates(_: User = Depends(get_current_user)):
    return [{"dataset": s.key, "label": s.label, "description": s.description, "unique_key": list(s.unique_key),
             "columns": [{"name": c.name, "type": c.kind, "required": c.required, "choices": c.choices, "example": c.example}
                         for c in s.columns], "import_order": datasets.IMPORT_ORDER.index(s.key) + 1}
            for s in datasets.SPECS.values()]


@data.get("/templates/{dataset}")
def template_file(dataset: str, _: User = Depends(get_current_user)):
    if dataset not in datasets.SPECS:
        raise HTTPException(404, "Unknown dataset")
    return Response(datasets.template_csv(dataset), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="template_{dataset}.csv"'})


UPLOADS_DOC = "uploads"


def _upload(store: Store, upload_id: int) -> dict:
    rec = next((u for u in store.read_json(UPLOADS_DOC, []) or [] if u["id"] == upload_id), None)
    if rec is None:
        raise HTTPException(404, "Upload not found")
    return rec


def _set_upload(store: Store, upload_id: int, **fields) -> None:
    def apply(items):
        for u in items:
            if u["id"] == upload_id:
                u.update(fields)
        return items

    store.update_json(UPLOADS_DOC, [], apply)


@data.post("/upload", status_code=201)
async def upload(dataset: str = Form(...), file: UploadFile = File(...), store: Store = Depends(store_dep),
                 user: User = Depends(require(Perm.UPLOAD_DATA))):
    """Steps 1–7 of the workflow: store the file, detect columns, preview, validate types, missing values,
    duplicates and references. Nothing is written to the data tables until `/api/data/import` is called."""
    if dataset not in datasets.SPECS:
        raise ValidationFailed(f"Unknown dataset '{dataset}'")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationFailed("File exceeds the 25 MB limit")
    if not content:
        raise ValidationFailed("The uploaded file is empty")
    df = datasets.read_table(content, file.filename or "upload.csv")
    if df.empty:
        raise ValidationFailed("The file has a header but no data rows")
    report, _ = datasets.validate(df, dataset, store)
    suffix = Path(file.filename or "x.csv").suffix.lower()
    stored = get_settings().uploads_dir / f"{uuid4().hex}{suffix}"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(content)
    holder: dict = {}

    def add(items):
        items = items or []
        rec = {"id": max((u["id"] for u in items), default=0) + 1, "dataset_type": dataset,
               "original_filename": file.filename or "upload", "stored_path": str(stored),
               "status": "validated" if report.error_count == 0 else "has_errors", "row_count": report.row_count,
               "error_count": report.error_count, "imported_count": 0, "uploaded_by_id": user.id,
               "created_at": utcnow_iso(), "imported_at": None}
        holder["rec"] = rec
        return items + [rec]

    store.update_json(UPLOADS_DOC, [], add)
    rec = holder["rec"]
    log_activity(store, user_id=user.id, action="upload", entity_type="data_upload", entity_id=rec["id"],
                 description=f"Uploaded {rec['original_filename']} ({dataset}): {report.row_count} rows, {report.error_count} errors")
    return {"upload_id": rec["id"], "status": rec["status"], **report.as_dict()}


@data.get("/preview")
def preview(upload_id: int, store: Store = Depends(store_dep), _: User = Depends(require(Perm.UPLOAD_DATA))):
    rec = _upload(store, upload_id)
    df = datasets.read_table(Path(rec["stored_path"]).read_bytes(), rec["original_filename"])
    report, _ = datasets.validate(df, rec["dataset_type"], store)  # re-validate against current data
    return {"upload_id": rec["id"], "status": rec["status"], **report.as_dict()}


@data.post("/import")
def import_upload(body: ImportIn, store: Store = Depends(store_dep), user: User = Depends(require(Perm.UPLOAD_DATA))):
    """Steps 8–9: confirm and store. Refused when validation errors exist unless `skip_invalid` is explicitly
    set, in which case only valid rows are imported and the skipped count is reported and logged. Every
    affected CSV is backed up before it is rewritten."""
    rec = _upload(store, body.upload_id)
    if rec["status"] == "imported":
        raise HTTPException(409, "This upload has already been imported")
    df = datasets.read_table(Path(rec["stored_path"]).read_bytes(), rec["original_filename"])
    report, typed = datasets.validate(df, rec["dataset_type"], store)
    if report.missing_required_columns:
        raise ValidationFailed("Required columns are missing; fix the file and upload again", details=report.errors[:20])
    if report.error_count and not body.skip_invalid:
        raise ValidationFailed(f"{report.invalid_row_count} rows have validation errors. Fix them, or confirm importing only the "
                               f"{report.valid_row_count} valid rows.", details=report.errors[:20])
    n = datasets.import_valid_rows(store, rec["dataset_type"], typed)
    _set_upload(store, rec["id"], status="imported", imported_count=n, imported_at=utcnow_iso())
    log_activity(store, user_id=user.id, action="import", entity_type="data_upload", entity_id=rec["id"],
                 description=f"Imported {n} {rec['dataset_type']} rows from {rec['original_filename']}"
                             + (f" ({report.invalid_row_count} invalid rows skipped)" if report.invalid_row_count else ""))
    return {"upload_id": rec["id"], "imported": n, "skipped": report.invalid_row_count, "dataset": rec["dataset_type"]}


@data.get("/uploads")
def uploads(store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    items = sorted(store.read_json(UPLOADS_DOC, []) or [], key=lambda u: u["created_at"], reverse=True)[:50]
    return [{"id": u["id"], "dataset": u["dataset_type"], "filename": u["original_filename"], "status": u["status"],
             "rows": u["row_count"], "errors": u["error_count"], "imported": u["imported_count"], "created_at": u["created_at"],
             "imported_at": u["imported_at"]} for u in items]


@data.get("/summary")
def data_summary(store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    s = get_settings()
    out = []
    for name in ("products", "suppliers", "warehouses", "inventory", "supplier_products", "sales", "purchase_orders",
                 "purchase_order_items", "deliveries", "locations", "routes"):
        df = store.read(name)
        sim = int(df["is_simulated"].sum()) if "is_simulated" in df.columns else None
        path = store.table_path(name)
        out.append({"table": name, "file": f"data/{path.name}", "rows": len(df), "simulated_rows": sim,
                    "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0})
    sales = store.read("sales")
    backups = s.data_dir / "backups"
    return {"tables": out,
            "sales_range": {"from": min(sales["date"]).isoformat() if len(sales) else None,
                            "to": max(sales["date"]).isoformat() if len(sales) else None},
            "storage": {"data_dir": str(s.data_dir), "sample_dir": str(s.sample_dir), "artifacts_dir": str(s.artifacts_dir),
                        "backup_files": sum(1 for _ in backups.rglob("*") if _.is_file()) if backups.exists() else 0,
                        "format": "CSV tables · JSON documents · joblib models"},
            "demo_mode": s.demo_mode, "known_tables": list(TABLES)}


# ====================================================================== settings
@settings_router.get("")
def get_settings_all(store: Store = Depends(store_dep), _: User = Depends(get_current_user)):
    return settings_service.get_all(store)


@settings_router.put("/{key}")
def update_settings(key: str, body: dict, store: Store = Depends(store_dep), user: User = Depends(require(Perm.CONFIGURE))):
    value = settings_service.update(store, key, body)
    log_activity(store, user_id=user.id, action="configure", entity_type="settings", description=f"Settings '{key}' updated", details=body)
    return value
