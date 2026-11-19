"""Mixed-integer linear program for replenishment order quantities (PuLP + CBC).

For every candidate item i (product × warehouse whose inventory position is expected to fall
below its reorder point before the next review):

    need_i  = max(0, ROP_i + d_i·R − IP_i)            units required to restore the target level
    lot_i   = max(need_i, EOQ_i, MOQ_i)                preferred lot (EOQ-based cycle stock)

Decision variables
    x_i ∈ ℤ≥0   order quantity          y_i ∈ {0,1}   order placed
    a_i ∈ [0, need_i]                    portion of x_i that covers the need
    b_i ∈ [0, lot_i − need_i]           portion that completes the economic lot

Objective (minimise)
    Σ penalty_i · (need_i − a_i)         unmet need, valued at margin × ABC weight × urgency
  + Σ c_i · (lot_i − need_i − b_i)       small value for completing EOQ lots (fewer future orders)
  + Σ S_i · y_i                          fixed ordering cost

Constraints
    x_i = a_i + b_i
    MOQ_i · y_i ≤ x_i ≤ lot_i · y_i      supplier minimum order quantity
    Σ unit_cost_i · x_i ≤ budget         purchasing budget
    Σ_{i∈w} x_i ≤ free_capacity_w        warehouse capacity (incl. stock already in transit)
"""
from __future__ import annotations

from dataclasses import dataclass

import pulp

ABC_WEIGHT = {"A": 1.5, "B": 1.0, "C": 0.6}


@dataclass
class OrderCandidate:
    key: tuple[int, int]  # (product_id, warehouse_id)
    need: float
    lot: float
    moq: int
    unit_cost: float
    ordering_cost: float
    margin: float
    abc: str
    urgent: bool  # stockout projected before a new order could arrive


@dataclass
class OrderSolution:
    status: str
    quantities: dict[tuple[int, int], int]
    total_cost: float
    unmet_need: dict[tuple[int, int], float]
    objective: float


def penalty(c: OrderCandidate) -> float:
    return max(c.margin, 0.01 * c.unit_cost) * ABC_WEIGHT.get(c.abc, 1.0) * (2.0 if c.urgent else 1.0)


def solve(candidates: list[OrderCandidate], budget: float | None, free_capacity: dict[int, float],
          time_limit: int = 20) -> OrderSolution:
    if not candidates:
        return OrderSolution("Optimal", {}, 0.0, {}, 0.0)
    prob = pulp.LpProblem("replenishment", pulp.LpMinimize)
    idx = range(len(candidates))
    x = {i: pulp.LpVariable(f"x_{i}", lowBound=0, cat="Integer") for i in idx}
    y = {i: pulp.LpVariable(f"y_{i}", cat="Binary") for i in idx}
    a = {i: pulp.LpVariable(f"a_{i}", lowBound=0, upBound=candidates[i].need) for i in idx}
    b = {i: pulp.LpVariable(f"b_{i}", lowBound=0, upBound=max(candidates[i].lot - candidates[i].need, 0)) for i in idx}

    obj = []
    for i in idx:
        c = candidates[i]
        cycle_value = c.ordering_cost / max(c.lot, 1) * 0.5
        obj += [penalty(c) * (c.need - a[i]), cycle_value * (max(c.lot - c.need, 0) - b[i]), c.ordering_cost * y[i]]
        prob += x[i] == a[i] + b[i]
        prob += x[i] >= c.moq * y[i]
        prob += x[i] <= max(c.lot, c.moq) * y[i]
    prob += pulp.lpSum(obj)
    if budget is not None:
        prob += pulp.lpSum(candidates[i].unit_cost * x[i] for i in idx) <= budget, "budget"
    for wid, cap in free_capacity.items():
        members = [i for i in idx if candidates[i].key[1] == wid]
        if members:
            prob += pulp.lpSum(x[i] for i in members) <= max(cap, 0), f"capacity_{wid}"

    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
    status = pulp.LpStatus[prob.status]
    q = {candidates[i].key: int(round(x[i].value() or 0)) for i in idx}
    unmet = {candidates[i].key: max(candidates[i].need - (a[i].value() or 0), 0.0) for i in idx}
    total = sum(candidates[i].unit_cost * q[candidates[i].key] for i in idx)
    return OrderSolution(status, q, round(total, 2), unmet, float(pulp.value(prob.objective) or 0))
