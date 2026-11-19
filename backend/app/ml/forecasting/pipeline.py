"""Training / evaluation / persistence / prediction pipeline for demand forecasting.

Evaluation protocol (per product):
  1. Chronological split — the last `holdout_days` days are the test set; never random.
  2. Each model is fitted on the training window only and produces a *recursive* multi-step
     forecast across the whole holdout (i.e. horizon 1..holdout_days), exactly as in production.
  3. MAE / RMSE / MAPE / WAPE are computed on the holdout; the model with the lowest MAE is
     marked as the product's recommended model.
  4. Holdout residuals of XGBoost calibrate its empirical prediction intervals.
  5. Every model is then refitted on the full history and persisted for prediction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.ml.evaluation.metrics import regression_metrics
from app.ml.forecasting.models import (
    ForecastResult,
    SarimaForecaster,
    SeasonalNaiveForecaster,
    XGBForecaster,
)

HOLDOUT_DAYS = 60


@dataclass
class ProductModelBundle:
    product_id: int
    sku: str
    trained_through: pd.Timestamp
    models: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    holdout: dict = field(default_factory=dict)
    best_model: str = "xgboost"
    feature_importance: dict = field(default_factory=dict)

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"product_{self.product_id}.joblib"
        joblib.dump(self, path, compress=3)
        return path

    @staticmethod
    def load(path: Path) -> "ProductModelBundle":
        return joblib.load(path)


def future_frame(start: pd.Timestamp, days: int, plan: pd.DataFrame | None = None) -> pd.DataFrame:
    idx = pd.date_range(start, periods=days, freq="D")
    f = pd.DataFrame({"discount_pct": 0.0, "promotion": 0}, index=idx)
    if plan is not None and not plan.empty:
        f.update(plan.reindex(idx).dropna(how="all"))
    return f


def split(series: pd.DataFrame, holdout_days: int = HOLDOUT_DAYS) -> tuple[pd.DataFrame, pd.DataFrame]:
    if len(series) < holdout_days + 120:
        raise ValueError(f"Need at least {holdout_days + 120} days of history, got {len(series)}")
    return series.iloc[:-holdout_days], series.iloc[-holdout_days:]


def train_product(product_id: int, sku: str, series: pd.DataFrame, holdout_days: int = HOLDOUT_DAYS) -> ProductModelBundle:
    train, test = split(series, holdout_days)
    exog_test = test[["discount_pct", "promotion"]]
    bundle = ProductModelBundle(product_id=product_id, sku=sku, trained_through=series.index[-1])
    bundle.holdout = {"dates": [d.date().isoformat() for d in test.index], "actual": test["units"].tolist()}

    for cls in (XGBForecaster, SarimaForecaster, SeasonalNaiveForecaster):
        model = cls().fit(train)
        res: ForecastResult = model.forecast(train, exog_test)
        bundle.metrics[cls.name] = regression_metrics(test["units"], res.point)
        bundle.holdout[cls.name] = np.round(res.point, 3).tolist()
        if isinstance(model, XGBForecaster):
            residuals = test["units"].to_numpy(float) - res.point
            final = XGBForecaster().fit(series)
            final.calibrate(residuals)
            bundle.feature_importance = final.feature_importance()
            bundle.models[cls.name] = final
        else:
            bundle.models[cls.name] = cls().fit(series)

    bundle.best_model = min(bundle.metrics, key=lambda m: bundle.metrics[m]["mae"])
    return bundle


def predict(bundle: ProductModelBundle, series: pd.DataFrame, horizon: int, model_name: str,
            plan: pd.DataFrame | None = None) -> ForecastResult:
    model = bundle.models[model_name]
    exog = future_frame(series.index[-1] + pd.Timedelta(days=1), horizon, plan)
    return model.forecast(series, exog)


def aggregate_metrics(bundles: list[ProductModelBundle]) -> dict:
    """Portfolio-level metrics: holdout errors pooled across all products (not averaged ratios)."""
    out = {}
    for name in ("xgboost", "sarima", "seasonal_naive"):
        actual = np.concatenate([b.holdout["actual"] for b in bundles])
        pred = np.concatenate([b.holdout[name] for b in bundles])
        out[name] = regression_metrics(actual, pred)
        out[name]["products_won"] = sum(1 for b in bundles if b.best_model == name)
    return out
