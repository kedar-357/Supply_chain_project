"""Users (data/users.json) and per-user preferences (data/preferences.json)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.core.exceptions import ConflictError, NotFoundError, ValidationFailed
from app.core.security import RoleName, hash_password
from app.storage.store import Store, utcnow_iso

DOC = "users"
PREFS = "preferences"
ALLOWED_PREFERENCES = {"dashboard_range_days": int, "forecast_horizon_days": int, "default_warehouse_id": int}


@dataclass
class User:
    id: int
    email: str
    full_name: str
    role: str
    hashed_password: str
    is_active: bool = True
    created_at: str | None = None
    last_login_at: str | None = None


def _all(store: Store) -> list[dict]:
    return store.read_json(DOC, []) or []


def list_users(store: Store) -> list[User]:
    return [User(**u) for u in _all(store)]


def get_by_id(store: Store, user_id: int) -> User | None:
    return next((u for u in list_users(store) if u.id == user_id), None)


def get_by_email(store: Store, email: str) -> User | None:
    email = email.lower()
    return next((u for u in list_users(store) if u.email == email), None)


def create(store: Store, email: str, full_name: str, password: str, role: str = RoleName.ANALYST.value) -> User:
    if role not in {r.value for r in RoleName}:
        raise ValidationFailed("Unknown role")
    hashed = hash_password(password)

    def add(users):
        users = users or []
        if any(u["email"] == email.lower() for u in users):
            raise ConflictError("An account with this email already exists")
        uid = max((u["id"] for u in users), default=0) + 1
        users.append(asdict(User(id=uid, email=email.lower(), full_name=full_name, role=role, hashed_password=hashed,
                                 created_at=utcnow_iso())))
        return users

    store.update_json(DOC, [], add)
    return get_by_email(store, email)  # type: ignore[return-value]


def update(store: Store, user_id: int, **changes) -> User:
    def apply(users):
        for u in users:
            if u["id"] == user_id:
                u.update({k: v for k, v in changes.items() if v is not None})
                return users
        raise NotFoundError("User not found")

    store.update_json(DOC, [], apply)
    return get_by_id(store, user_id)  # type: ignore[return-value]


def get_preferences(store: Store, user_id: int) -> dict:
    return (store.read_json(PREFS, {}) or {}).get(str(user_id), {})


def set_preferences(store: Store, user_id: int, prefs: dict) -> dict:
    clean = {}
    for k, v in prefs.items():
        if k not in ALLOWED_PREFERENCES:
            raise ValidationFailed(f"Unknown preference '{k}'")
        if v is not None:
            try:
                clean[k] = ALLOWED_PREFERENCES[k](v)
            except (TypeError, ValueError):
                raise ValidationFailed(f"Invalid value for {k}")

    def apply(doc):
        doc = doc or {}
        doc[str(user_id)] = {**doc.get(str(user_id), {}), **clean}
        return doc

    return store.update_json(PREFS, {}, apply)[str(user_id)]
