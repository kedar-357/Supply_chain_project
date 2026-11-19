"""Leakage-safe feature engineering for daily demand forecasting.

Every demand-derived feature for day t uses only y[t-1] and earlier (`shift(1)` before any
rolling window). Calendar and promotion features describe day t itself; promotions/discounts are
treated as *planned* inputs that are known in advance (the standard retail assumption). For future
days without a known plan we assume no promotion.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LAGS = (1, 7, 14, 28)
FEATURES = [
    "lag_1", "lag_7", "lag_14", "lag_28",
    "ma_7", "ma_30", "std_7",
    "dow", "month", "is_weekend", "doy_sin", "doy_cos",
    "discount_pct", "promotion",
]
MIN_HISTORY = 30


def calendar_features(idx: pd.DatetimeIndex) -> pd.DataFrame:
    doy = idx.dayofyear.to_numpy()
    return pd.DataFrame(
        {
            "dow": idx.dayofweek,
            "month": idx.month,
            "is_weekend": (idx.dayofweek >= 5).astype(int),
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25),
        },
        index=idx,
    )


def build_features(series: pd.DataFrame) -> pd.DataFrame:
    """series: DatetimeIndex (daily, no gaps) with columns units, discount_pct, promotion."""
    y = series["units"].astype(float)
    past = y.shift(1)  # information available at prediction time
    f = pd.DataFrame(index=series.index)
    for k in LAGS:
        f[f"lag_{k}"] = y.shift(k)
    f["ma_7"] = past.rolling(7).mean()
    f["ma_30"] = past.rolling(30).mean()
    f["std_7"] = past.rolling(7).std()
    f = f.join(calendar_features(series.index))
    f["discount_pct"] = series["discount_pct"].astype(float)
    f["promotion"] = series["promotion"].astype(int)
    f["target"] = y
    return f


def feature_row(history: np.ndarray, day: pd.Timestamp, discount: float = 0.0, promotion: int = 0) -> dict:
    """Features for a single future day given the (observed + already predicted) history."""
    h = history
    cal = calendar_features(pd.DatetimeIndex([day])).iloc[0].to_dict()
    row = {f"lag_{k}": float(h[-k]) for k in LAGS}
    row["ma_7"] = float(np.mean(h[-7:]))
    row["ma_30"] = float(np.mean(h[-30:]))
    row["std_7"] = float(np.std(h[-7:], ddof=1))
    row.update(cal)
    row["discount_pct"] = float(discount)
    row["promotion"] = int(promotion)
    return row


def daily_series(sales: pd.DataFrame, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Aggregate raw sales rows (date, units_sold, discount_pct, promotion) into a gap-free daily series."""
    g = sales.groupby("date").agg(
        units=("units_sold", "sum"), discount_pct=("discount_pct", "mean"), promotion=("promotion", "max")
    )
    g.index = pd.to_datetime(g.index)
    idx = pd.date_range(g.index.min(), end or g.index.max(), freq="D")
    g = g.reindex(idx)
    g["units"] = g["units"].fillna(0)  # no sales row = zero demand recorded
    g["discount_pct"] = g["discount_pct"].fillna(0)
    g["promotion"] = g["promotion"].fillna(False).astype(int)
    return g
