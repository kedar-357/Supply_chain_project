"""Configurable business parameters stored in data/settings.json."""
from app.core.exceptions import ValidationFailed
from app.storage.store import Store

DEFAULTS: dict[str, tuple[dict, str]] = {
    "inventory": (
        {
            "service_level": 0.95,
            "review_period_days": 7,
            "overstock_cover_multiplier": 3.0,
            "demand_window_days": 90,
            "default_ordering_cost": 500.0,
            "default_holding_cost_rate": 0.22,
        },
        "Inventory policy parameters (service level, review period, overstock threshold)",
    ),
    "supplier_weights": (
        {"on_time": 0.35, "quality": 0.25, "cost": 0.20, "lead_time_consistency": 0.20},
        "Weights of the supplier performance score (must sum to 1)",
    ),
    "procurement": ({"monthly_budget": 2_500_000.0, "currency": "INR"}, "Default procurement budget used by the optimizer"),
    "logistics": (
        {"vehicle_capacity_units": 4000, "store_replenishment_days": 7, "fuel_cost_multiplier": 1.0, "loading_time_hours": 0.5},
        "Logistics defaults for route planning",
    ),
    "finance": (
        {"lost_sale_margin_factor": 1.0, "overhead_cost_per_month": 1_200_000.0},
        "Financial assumptions for scenario impact (overheads are an estimate)",
    ),
}
DOC = "settings"


def get_all(store: Store) -> dict[str, dict]:
    stored = store.read_json(DOC, {}) or {}
    return {k: {**default, **stored.get(k, {})} for k, (default, _) in DEFAULTS.items()}


def get(store: Store, key: str) -> dict:
    return get_all(store)[key]


def ensure_defaults(store: Store) -> None:
    if store.read_json(DOC) is None:
        store.write_json(DOC, {k: v for k, (v, _) in DEFAULTS.items()}, backup=False)


def update(store: Store, key: str, value: dict) -> dict:
    if key not in DEFAULTS:
        raise ValidationFailed(f"Unknown settings group '{key}'")
    default, _ = DEFAULTS[key]
    unknown = set(value) - set(default)
    if unknown:
        raise ValidationFailed(f"Unknown settings: {', '.join(sorted(unknown))}")
    merged = {**get(store, key), **value}
    for k, v in merged.items():
        if k != "currency" and (not isinstance(v, (int, float)) or v < 0):
            raise ValidationFailed(f"{k} must be a non-negative number")
    if key == "supplier_weights":
        total = sum(float(v) for v in merged.values())
        if abs(total - 1) > 1e-6:
            raise ValidationFailed("Supplier weights must be non-negative and sum to 1.0")
    if key == "inventory" and not (0.5 <= float(merged["service_level"]) < 1):
        raise ValidationFailed("service_level must be in [0.5, 1)")

    def apply(doc):
        doc = doc or {}
        doc[key] = merged
        return doc

    store.update_json(DOC, {}, apply)
    return merged
