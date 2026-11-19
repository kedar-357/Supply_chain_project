"""Feature engineering for delivery-delay classification.

One row per delivered purchase order. All supplier-history features are computed from deliveries
that *arrived before the order was placed* (strictly earlier actual_date than this order_date),
so the model never sees the outcome of the order it is predicting or of concurrent orders.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NUMERIC = [
    "log_quantity", "n_lines", "distance_km", "quoted_lead_days",
    "sup_prior_late_rate", "sup_prior_avg_delay", "sup_prior_count", "sup_last_delay",
    "is_monsoon", "is_festive", "month_sin", "month_cos",
]
CATEGORICAL = ["region", "category"]


def season_flags(order_date: pd.Series) -> pd.DataFrame:
    m = pd.to_datetime(order_date).dt.month
    return pd.DataFrame({
        "is_monsoon": m.isin([6, 7, 8, 9]).astype(int),
        "is_festive": m.isin([10, 11]).astype(int),
        "month_sin": np.sin(2 * np.pi * m / 12),
        "month_cos": np.cos(2 * np.pi * m / 12),
    }, index=order_date.index)


def supplier_history_features(orders: pd.DataFrame, deliveries: pd.DataFrame) -> pd.DataFrame:
    """For each order row (supplier_id, order_date) compute stats over the supplier's deliveries
    with actual_date < order_date."""
    out = np.zeros((len(orders), 4))
    dl = deliveries.sort_values("actual_date")
    by_sup = {s: g for s, g in dl.groupby("supplier_id")}
    for i, (sid, od) in enumerate(zip(orders["supplier_id"], pd.to_datetime(orders["order_date"]))):
        g = by_sup.get(sid)
        if g is None:
            out[i] = (np.nan, np.nan, 0, np.nan)
            continue
        prior = g[pd.to_datetime(g["actual_date"]) < od]
        if prior.empty:
            out[i] = (np.nan, np.nan, 0, np.nan)
        else:
            d = prior["delay_days"].to_numpy(float)
            out[i] = ((d > 0).mean(), d.mean(), len(d), d[-1])
    return pd.DataFrame(out, columns=["sup_prior_late_rate", "sup_prior_avg_delay", "sup_prior_count", "sup_last_delay"],
                        index=orders.index)


def build_frame(orders: pd.DataFrame, deliveries: pd.DataFrame) -> pd.DataFrame:
    """orders: po_id, supplier_id, region, category, order_date, total_quantity, n_lines, distance_km,
    quoted_lead_days. deliveries: supplier_id, actual_date, delay_days (history pool)."""
    f = orders.copy()
    f["log_quantity"] = np.log1p(f["total_quantity"].astype(float))
    f = f.join(season_flags(f["order_date"]))
    f = f.join(supplier_history_features(f, deliveries))
    # cold start: fall back to the global prior instead of leaking anything
    f["sup_prior_late_rate"] = f["sup_prior_late_rate"].fillna(0.35)
    f["sup_prior_avg_delay"] = f["sup_prior_avg_delay"].fillna(0.0)
    f["sup_last_delay"] = f["sup_last_delay"].fillna(0.0)
    f["distance_km"] = f["distance_km"].fillna(f["distance_km"].median() if f["distance_km"].notna().any() else 0)
    return f
