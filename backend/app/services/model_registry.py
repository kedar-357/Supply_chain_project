"""Versioned model-run registry (data/model_registry.json). Model files themselves are joblib in artifacts/."""
from __future__ import annotations

from app.core.exceptions import PreconditionFailed
from app.storage.store import Store, utcnow_iso

DOC = "model_registry"


def runs(store: Store, model_type: str) -> list[dict]:
    return [r for r in (store.read_json(DOC, []) or []) if r["model_type"] == model_type]


def latest(store: Store, model_type: str, status: str | None = "completed") -> dict | None:
    rs = [r for r in runs(store, model_type) if status is None or r["status"] == status]
    return max(rs, key=lambda r: r["version"]) if rs else None


def get(store: Store, run_id: int) -> dict | None:
    return next((r for r in (store.read_json(DOC, []) or []) if r["id"] == run_id), None)


def start(store: Store, model_type: str, params: dict, user_id: int | None, exclusive: bool = False) -> dict:
    holder: dict = {}

    def add(all_runs):
        all_runs = all_runs or []
        if exclusive:
            busy = [r for r in all_runs if r["model_type"] == model_type and r["status"] == "running"]
            if busy:
                raise PreconditionFailed(f"Training run v{busy[0]['version']} is already in progress")
        version = max((r["version"] for r in all_runs if r["model_type"] == model_type), default=0) + 1
        run = {"id": max((r["id"] for r in all_runs), default=0) + 1, "model_type": model_type, "version": version,
               "status": "running", "params": params, "metrics": None, "artifact_path": None, "error": None,
               "triggered_by_id": user_id, "started_at": utcnow_iso(), "finished_at": None}
        all_runs.append(run)
        holder["run"] = run
        return all_runs

    store.update_json(DOC, [], add)
    return holder["run"]


def finish(store: Store, run_id: int, **fields) -> dict:
    holder: dict = {}

    def apply(all_runs):
        for r in all_runs:
            if r["id"] == run_id:
                r.update(fields)
                r["finished_at"] = utcnow_iso()
                holder["run"] = r
        return all_runs

    store.update_json(DOC, [], apply)
    return holder["run"]


def mark_stale_running_as_failed(store: Store) -> None:
    """On startup, a 'running' run can only be left over from a crashed/stopped process."""
    def apply(all_runs):
        for r in all_runs or []:
            if r["status"] == "running":
                r["status"] = "failed"
                r["error"] = "Interrupted (server restarted during training)"
                r["finished_at"] = utcnow_iso()
        return all_runs or []

    if any(r["status"] == "running" for r in (store.read_json(DOC, []) or [])):
        store.update_json(DOC, [], apply)
