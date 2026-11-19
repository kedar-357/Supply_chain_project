from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------- auth / users
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def strong(cls, v: str) -> str:
        if not any(c.isdigit() for c in v) or not any(c.isalpha() for c in v):
            raise ValueError("password must contain letters and digits")
        return v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    permissions: list[str] = []
    last_login_at: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class PreferencesIn(BaseModel):
    dashboard_range_days: int | None = Field(None, ge=1, le=730)
    forecast_horizon_days: int | None = Field(None, ge=7, le=90)
    default_warehouse_id: int | None = None


class UserUpdateIn(BaseModel):
    role: Literal["Administrator", "Supply Chain Manager", "Analyst"] | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------- master data
class ProductIn(BaseModel):
    sku: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=160)
    category: str = Field(min_length=2, max_length=80)
    unit_cost: float = Field(ge=0)
    selling_price: float = Field(ge=0)
    ordering_cost: float = Field(500, ge=0)
    holding_cost_rate: float = Field(0.22, ge=0, le=1)
    primary_supplier_id: int | None = None
    is_active: bool = True


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku: str
    name: str
    category: str
    unit_cost: float
    selling_price: float
    ordering_cost: float
    holding_cost_rate: float
    primary_supplier_id: int | None
    primary_supplier: str | None
    is_active: bool
    is_simulated: bool


class SupplierIn(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=2, max_length=120)
    city: str = Field(min_length=2, max_length=80)
    region: str = Field(min_length=2, max_length=40)
    country: str = "India"
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    quality_rating: float = Field(4.0, ge=1, le=5)
    contact_email: EmailStr | None = None
    is_active: bool = True


class WarehouseIn(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=2, max_length=120)
    city: str = Field(min_length=2, max_length=80)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    capacity_units: int = Field(gt=0)


class InventoryUpdateIn(BaseModel):
    quantity_on_hand: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=200)


# ---------------------------------------------------------------- analytics requests
class ForecastTrainIn(BaseModel):
    product_ids: list[int] | None = None


class ForecastPredictIn(BaseModel):
    product_id: int
    horizon_days: Literal[7, 30, 60, 90] = 30
    model: Literal["xgboost", "sarima", "seasonal_naive"] | None = None


class OptimizeIn(BaseModel):
    budget: float | None = Field(None, ge=0)
    service_level: float | None = Field(None, ge=0.5, lt=1)
    ordering_cost: float | None = Field(None, ge=0)
    holding_cost_rate: float | None = Field(None, gt=0, le=1)
    lead_time_days: float | None = Field(None, gt=0, le=180)


class DraftPOIn(BaseModel):
    optimization_id: int
    item_ids: list[int] | None = None


class DelayPredictIn(BaseModel):
    supplier_id: int
    warehouse_id: int
    quantity: int = Field(gt=0, le=1_000_000)
    product_id: int | None = None
    order_date: date | None = None


class SupplierCompareIn(BaseModel):
    supplier_ids: list[int] = Field(min_length=2, max_length=8)
    product_id: int | None = None


class ShortestRouteIn(BaseModel):
    origin_id: int
    destination_id: int
    optimize_for: Literal["distance", "time", "cost"] = "cost"
    cost_multiplier: float | None = Field(None, gt=0, le=5)


class VRPIn(BaseModel):
    depot_id: int
    stop_ids: list[int] = Field(min_length=1, max_length=25)
    demands: dict[int, int] | None = None
    vehicle_capacity: int | None = Field(None, gt=0)
    num_vehicles: int = Field(2, ge=1, le=20)
    cost_multiplier: float | None = Field(None, gt=0, le=5)


class ScenarioIn(BaseModel):
    scenario_type: Literal["supplier_delay", "demand_increase", "transport_cost", "budget_limit", "capacity_constraint"]
    config: dict = {}
    save: bool = False
    name: str | None = Field(None, max_length=120)


class ScenarioCompareIn(BaseModel):
    scenario_ids: list[int] = Field(min_length=2, max_length=6)


class ImportIn(BaseModel):
    upload_id: int
    skip_invalid: bool = False


class RecommendationStatusIn(BaseModel):
    status: Literal["open", "accepted", "dismissed"]
