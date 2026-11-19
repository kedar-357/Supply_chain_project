"""Forecasting models: XGBoost (ML), SARIMA (statistical baseline), seasonal naive (naive baseline).

All models share the interface:
    fit(series)                          -> self
    forecast(series, future_exog)        -> ForecastResult (point + optional lower/upper)
where `series` is the observed daily history (units, discount_pct, promotion) and `future_exog`
is a DataFrame indexed by the future dates with discount_pct and promotion.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX
from xgboost import XGBRegressor

from app.ml.forecasting.features import FEATURES, MIN_HISTORY, build_features, feature_row

Z95 = 1.96


@dataclass
class ForecastResult:
    index: pd.DatetimeIndex
    point: np.ndarray
    lower: np.ndarray | None = None
    upper: np.ndarray | None = None


class XGBForecaster:
    name = "xgboost"
    label = "XGBoost Regressor"

    def __init__(self, **params):
        self.params = {
            "n_estimators": 350, "max_depth": 4, "learning_rate": 0.05, "subsample": 0.85,
            "colsample_bytree": 0.85, "min_child_weight": 3, "reg_lambda": 1.0,
            "objective": "reg:squarederror", "random_state": 42, "n_jobs": 4,
        } | params
        self.model: XGBRegressor | None = None
        # residual std by forecast horizon (1-based), estimated on the chronological holdout
        self.residual_sigma: np.ndarray | None = None

    def fit(self, series: pd.DataFrame) -> "XGBForecaster":
        f = build_features(series).iloc[MIN_HISTORY:].dropna()
        self.model = XGBRegressor(**self.params)
        self.model.fit(f[FEATURES], f["target"])
        return self

    def forecast(self, series: pd.DataFrame, future_exog: pd.DataFrame) -> ForecastResult:
        assert self.model is not None, "model not fitted"
        history = series["units"].to_numpy(dtype=float).tolist()
        preds = []
        for day, ex in future_exog.iterrows():  # recursive multi-step forecast
            row = feature_row(np.asarray(history), day, ex["discount_pct"], ex["promotion"])
            yhat = max(0.0, float(self.model.predict(pd.DataFrame([row])[FEATURES])[0]))
            preds.append(yhat)
            history.append(yhat)
        point = np.array(preds)
        lower = upper = None
        if self.residual_sigma is not None and len(self.residual_sigma):
            h = np.arange(1, len(point) + 1)
            sig = np.array([self._sigma_at(i) for i in h])
            lower, upper = np.maximum(point - Z95 * sig, 0), point + Z95 * sig
        return ForecastResult(future_exog.index, point, lower, upper)

    def _sigma_at(self, h: int) -> float:
        s = self.residual_sigma
        if h <= len(s):
            return float(s[h - 1])
        return float(s[-1] * np.sqrt(h / len(s)))  # widen beyond the evaluated horizon

    def calibrate(self, residuals: np.ndarray, window: int = 7) -> None:
        """Empirical interval: rolling std of holdout residuals around each horizon."""
        r = np.asarray(residuals, dtype=float)
        self.residual_sigma = np.array(
            [np.std(r[max(0, i - window) : i + window + 1], ddof=1) for i in range(len(r))]
        )

    def feature_importance(self) -> dict[str, float]:
        assert self.model is not None
        imp = self.model.get_booster().get_score(importance_type="gain")
        total = sum(imp.values()) or 1.0
        return {k: round(v / total, 4) for k, v in sorted(imp.items(), key=lambda kv: -kv[1])}

    def explain_first_step(self, series: pd.DataFrame, future_exog: pd.DataFrame) -> dict[str, float]:
        """SHAP contributions (XGBoost pred_contribs) for the first forecast day."""
        import xgboost as xgb

        day, ex = next(future_exog.iterrows())
        row = pd.DataFrame([feature_row(series["units"].to_numpy(float), day, ex["discount_pct"], ex["promotion"])])[FEATURES]
        contrib = self.model.get_booster().predict(xgb.DMatrix(row), pred_contribs=True)[0]
        out = {f: round(float(c), 3) for f, c in zip(FEATURES, contrib[:-1])}
        out["_bias"] = round(float(contrib[-1]), 3)
        return out


def _fourier(idx: pd.DatetimeIndex, discount: pd.Series) -> pd.DataFrame:
    doy = idx.dayofyear.to_numpy()
    return pd.DataFrame(
        {
            "sin1": np.sin(2 * np.pi * doy / 365.25), "cos1": np.cos(2 * np.pi * doy / 365.25),
            "sin2": np.sin(4 * np.pi * doy / 365.25), "cos2": np.cos(4 * np.pi * doy / 365.25),
            "discount_pct": discount.to_numpy(dtype=float),
        },
        index=idx,
    )


class SarimaForecaster:
    """SARIMA(1,0,1)(1,0,1)[7] with yearly Fourier terms and discount as exogenous regressors."""

    name = "sarima"
    label = "SARIMA (statistical baseline)"
    order = (1, 0, 1)
    seasonal_order = (1, 0, 1, 7)

    def __init__(self):
        self.params: np.ndarray | None = None

    def _model(self, series: pd.DataFrame) -> SARIMAX:
        return SARIMAX(
            series["units"].astype(float).to_numpy(),
            exog=_fourier(series.index, series["discount_pct"]).to_numpy(),
            order=self.order, seasonal_order=self.seasonal_order, trend="c",
            enforce_stationarity=False, enforce_invertibility=False,
        )

    def fit(self, series: pd.DataFrame) -> "SarimaForecaster":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = self._model(series).fit(disp=False, maxiter=60, method="lbfgs")
        self.params = res.params
        return self

    def forecast(self, series: pd.DataFrame, future_exog: pd.DataFrame) -> ForecastResult:
        assert self.params is not None, "model not fitted"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = self._model(series).filter(self.params)  # re-use fitted parameters, no re-estimation
            fc = res.get_forecast(steps=len(future_exog), exog=_fourier(future_exog.index, future_exog["discount_pct"]).to_numpy())
        ci = np.asarray(fc.conf_int(alpha=0.05))
        point = np.maximum(np.asarray(fc.predicted_mean), 0)
        return ForecastResult(future_exog.index, point, np.maximum(ci[:, 0], 0), np.maximum(ci[:, 1], 0))


class SeasonalNaiveForecaster:
    """y_hat(t+h) = y(t+h-7k): repeats the last observed week. Classic naive benchmark."""

    name = "seasonal_naive"
    label = "Seasonal naive (weekly)"

    def fit(self, series: pd.DataFrame) -> "SeasonalNaiveForecaster":
        return self

    def forecast(self, series: pd.DataFrame, future_exog: pd.DataFrame) -> ForecastResult:
        last_week = series["units"].to_numpy(dtype=float)[-7:]
        point = np.array([last_week[i % 7] for i in range(len(future_exog))])
        return ForecastResult(future_exog.index, point)


MODEL_CLASSES = {c.name: c for c in (XGBForecaster, SarimaForecaster, SeasonalNaiveForecaster)}
MODEL_LABELS = {c.name: c.label for c in (XGBForecaster, SarimaForecaster, SeasonalNaiveForecaster)}
