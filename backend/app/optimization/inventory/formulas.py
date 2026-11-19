"""Classical inventory-management formulas (pure functions, unit-tested)."""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd


def z_score(service_level: float) -> float:
    """One-sided z for a cycle service level, e.g. 0.95 → 1.645."""
    if not 0 < service_level < 1:
        raise ValueError("service_level must be in (0, 1)")
    return NormalDist().inv_cdf(service_level)


def safety_stock(service_level: float, avg_daily_demand: float, sd_daily_demand: float,
                 lead_time_days: float, sd_lead_time_days: float = 0.0) -> float:
    """SS = z · sqrt(L·σd² + d²·σL²)  (demand and lead-time variability combined).

    With σL = 0 this reduces to the familiar z · σd · sqrt(L)."""
    if lead_time_days < 0 or avg_daily_demand < 0:
        raise ValueError("lead time and demand must be non-negative")
    var = lead_time_days * sd_daily_demand**2 + avg_daily_demand**2 * sd_lead_time_days**2
    return z_score(service_level) * math.sqrt(max(var, 0.0))


def reorder_point(avg_daily_demand: float, lead_time_days: float, safety_stock_units: float) -> float:
    """ROP = d · L + SS"""
    return avg_daily_demand * lead_time_days + safety_stock_units


def eoq(annual_demand: float, ordering_cost: float, annual_holding_cost_per_unit: float) -> float:
    """EOQ = sqrt(2 · D · S / H)"""
    if annual_holding_cost_per_unit <= 0:
        raise ValueError("holding cost must be positive")
    if annual_demand <= 0 or ordering_cost <= 0:
        return 0.0
    return math.sqrt(2 * annual_demand * ordering_cost / annual_holding_cost_per_unit)


def eoq_total_cost(annual_demand: float, order_qty: float, ordering_cost: float, holding_cost: float) -> float:
    """Annual ordering + holding cost of ordering `order_qty` each cycle."""
    if order_qty <= 0:
        return float("inf")
    return annual_demand / order_qty * ordering_cost + order_qty / 2 * holding_cost


def abc_classify(values: pd.Series, a_cut: float = 0.80, b_cut: float = 0.95) -> pd.Series:
    """ABC classes from annual consumption value: items making up the top 80% of cumulative value
    are A, the next 15% B, the rest C."""
    if values.empty:
        return pd.Series(dtype=str)
    order = values.sort_values(ascending=False)
    total = order.sum()
    if total <= 0:
        return pd.Series("C", index=values.index)
    cum_before = order.cumsum().shift(fill_value=0) / total  # share *before* adding the item
    cls = np.where(cum_before < a_cut, "A", np.where(cum_before < b_cut, "B", "C"))
    return pd.Series(cls, index=order.index).reindex(values.index)


def project_stock(on_hand: float, daily_demand: np.ndarray, arrivals: dict[int, float]) -> tuple[np.ndarray, int | None]:
    """Day-by-day stock projection. Returns (end-of-day stock array, first day index with unmet demand)."""
    stock = float(on_hand)
    levels, stockout_day = [], None
    for t, d in enumerate(daily_demand):
        stock += arrivals.get(t, 0.0)
        stock -= d
        if stock < 0 and stockout_day is None:
            stockout_day = t
        levels.append(stock)
    return np.array(levels), stockout_day
