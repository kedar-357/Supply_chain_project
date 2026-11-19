"""Unit tests: classical inventory formulas and the order-quantity MILP (no database)."""
import math

import numpy as np
import pandas as pd
import pytest

from app.optimization.inventory import formulas as F
from app.optimization.inventory.order_lp import OrderCandidate, solve


def test_z_score_known_values():
    assert F.z_score(0.95) == pytest.approx(1.6449, abs=1e-3)
    assert F.z_score(0.99) == pytest.approx(2.3263, abs=1e-3)
    with pytest.raises(ValueError):
        F.z_score(1.0)


def test_safety_stock_demand_variability_only():
    # σL = 0 → z·σd·√L
    ss = F.safety_stock(0.95, avg_daily_demand=20, sd_daily_demand=5, lead_time_days=9)
    assert ss == pytest.approx(1.6449 * 5 * 3, rel=1e-3)


def test_safety_stock_includes_lead_time_variability():
    without = F.safety_stock(0.95, 20, 5, 9, 0)
    with_lt = F.safety_stock(0.95, 20, 5, 9, 2)
    assert with_lt == pytest.approx(1.6449 * math.sqrt(9 * 25 + 400 * 4), rel=1e-3)
    assert with_lt > without


def test_reorder_point():
    assert F.reorder_point(20, 9, 25) == 205


def test_eoq_textbook_example():
    # D=12,000/yr, S=₹500/order, H=₹30/unit/yr → EOQ = √(2·12000·500/30) = 632.46
    assert F.eoq(12_000, 500, 30) == pytest.approx(632.456, rel=1e-4)


def test_eoq_minimises_total_cost():
    q = F.eoq(12_000, 500, 30)
    best = F.eoq_total_cost(12_000, q, 500, 30)
    for other in (q * 0.7, q * 1.3):
        assert F.eoq_total_cost(12_000, other, 500, 30) > best


def test_eoq_edge_cases():
    assert F.eoq(0, 500, 30) == 0
    with pytest.raises(ValueError):
        F.eoq(100, 500, 0)


def test_abc_classification_keeps_input_order_and_handles_zero():
    values = pd.Series({"x": 10, "y": 990})
    # y alone is 99% of value (A); 99% of value precedes x, so x is C
    assert F.abc_classify(values).to_dict() == {"x": "C", "y": "A"}
    assert F.abc_classify(pd.Series({"x": 0, "y": 0})).tolist() == ["C", "C"]


def test_abc_classification_documented_rule():
    values = pd.Series([500, 300, 100, 60, 40], index=list("vwxyz"))  # shares 50,30,10,6,4
    cls = F.abc_classify(values)
    # before-shares: 0, 50, 80, 90, 96 → A, A, B, B, C
    assert cls.tolist() == ["A", "A", "B", "B", "C"]


def test_project_stock_detects_first_shortage_and_arrivals():
    levels, day = F.project_stock(10, np.array([4, 4, 4, 4]), {})
    assert day == 2 and levels[-1] == -6
    levels, day = F.project_stock(10, np.array([4, 4, 4, 4]), {2: 10})
    assert day is None and levels[-1] == 4


# ------------------------------------------------------------------ MILP
def cand(i, need, cost=100, moq=10, margin=50, abc="B", urgent=False, wh=1, lot=None):
    return OrderCandidate(key=(i, wh), need=need, lot=lot or need, moq=moq, unit_cost=cost, ordering_cost=200,
                          margin=margin, abc=abc, urgent=urgent)


def test_lp_unconstrained_covers_all_needs():
    sol = solve([cand(1, 100), cand(2, 50)], budget=None, free_capacity={1: 10_000})
    assert sol.status == "Optimal"
    assert sol.quantities[(1, 1)] >= 100 and sol.quantities[(2, 1)] >= 50
    assert sum(sol.unmet_need.values()) == pytest.approx(0, abs=1e-6)


def test_lp_respects_budget_and_prioritises_valuable_items():
    cands = [cand(1, 100, margin=10, abc="C"), cand(2, 100, margin=200, abc="A", urgent=True)]
    sol = solve(cands, budget=10_000, free_capacity={1: 10_000})
    spend = sum(sol.quantities[c.key] * c.unit_cost for c in cands)
    assert spend <= 10_000 + 1e-6
    assert sol.quantities[(2, 1)] == 100  # urgent high-margin A item funded first
    assert sol.quantities[(1, 1)] == 0


def test_lp_respects_minimum_order_quantity():
    sol = solve([cand(1, 5, moq=40)], budget=None, free_capacity={1: 1000})
    q = sol.quantities[(1, 1)]
    assert q == 0 or q >= 40


def test_lp_respects_warehouse_capacity():
    cands = [cand(1, 300, wh=1), cand(2, 300, wh=1), cand(3, 300, wh=2)]
    sol = solve(cands, budget=None, free_capacity={1: 400, 2: 1000})
    assert sol.quantities[(1, 1)] + sol.quantities[(2, 1)] <= 400
    assert sol.quantities[(3, 2)] >= 300
