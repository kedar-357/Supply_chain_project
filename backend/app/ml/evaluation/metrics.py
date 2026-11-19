"""Regression and classification metrics with explicit handling of edge cases."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def regression_metrics(actual, predicted) -> dict:
    """MAE, RMSE, MAPE, WAPE, bias.

    MAPE is undefined when actual == 0, so those observations are excluded from MAPE (and the
    number excluded is reported). WAPE (= sum|e| / sum|actual|) is reported alongside because it
    is robust to zero-demand days.
    """
    a = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    err = a - p
    nonzero = a != 0
    mape = float(np.mean(np.abs(err[nonzero] / a[nonzero])) * 100) if nonzero.any() else None
    denom = np.abs(a).sum()
    return {
        "mae": round(float(np.mean(np.abs(err))), 4),
        "rmse": round(float(np.sqrt(np.mean(err**2))), 4),
        "mape": None if mape is None else round(mape, 3),
        "wape": None if denom == 0 else round(float(np.abs(err).sum() / denom * 100), 3),
        "bias": round(float(np.mean(p - a)), 4),
        "n": int(a.size),
        "mape_excluded_zero_actuals": int((~nonzero).sum()),
    }


def classification_metrics(y_true, proba, threshold: float = 0.5) -> dict:
    y = np.asarray(y_true).astype(int)
    pr = np.asarray(proba, dtype=float)
    pred = (pr >= threshold).astype(int)
    out = {
        "accuracy": round(float(accuracy_score(y, pred)), 4),
        "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
        "positive_rate": round(float(y.mean()), 4),
        "n": int(y.size),
    }
    out["roc_auc"] = round(float(roc_auc_score(y, pr)), 4) if len(np.unique(y)) == 2 else None
    return out
