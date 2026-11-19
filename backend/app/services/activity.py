"""Append-only activity log (data/activity.jsonl)."""
from app.storage.store import Store, utcnow_iso


def log_activity(store: Store, *, user_id: int | None, action: str, entity_type: str, description: str,
                 entity_id: int | None = None, details: dict | None = None) -> None:
    store.append_jsonl("activity", {
        "user_id": user_id, "action": action, "entity_type": entity_type, "entity_id": entity_id,
        "description": description, "details": details, "created_at": utcnow_iso(),
    })


def recent(store: Store, limit: int = 15) -> list[dict]:
    rows = store.read_jsonl("activity", last=limit)
    return list(reversed(rows))
