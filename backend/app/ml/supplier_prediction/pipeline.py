"""Train / evaluate / persist / predict the delivery-delay classifier.

Protocol: orders sorted by order_date; first 80% train, last 20% test (chronological). Candidates:
majority-class baseline, logistic regression baseline, Random Forest, XGBoost. The model with the
best test ROC-AUC (among RF / XGBoost) is refitted on all data and deployed.

Explanations are model-agnostic occlusion effects: for each factor, how much the predicted delay
probability changes when that factor is replaced by its typical (training median / mode) value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from app.ml.evaluation.metrics import classification_metrics
from app.ml.supplier_prediction.features import CATEGORICAL, NUMERIC

TEST_FRACTION = 0.2
FEATURE_LABELS = {
    "log_quantity": "Order quantity",
    "n_lines": "Number of order lines",
    "distance_km": "Shipping distance",
    "quoted_lead_days": "Quoted lead time",
    "sup_prior_late_rate": "Supplier's historical late-delivery rate",
    "sup_prior_avg_delay": "Supplier's historical average delay",
    "sup_prior_count": "Number of past deliveries",
    "sup_last_delay": "Delay of supplier's most recent delivery",
    "is_monsoon": "Monsoon season (Jun–Sep)",
    "is_festive": "Festive season (Oct–Nov)",
    "month_sin": "Time of year",
    "month_cos": "Time of year",
    "region": "Supplier region",
    "category": "Product category",
}


def _pre(scale: bool) -> ColumnTransformer:
    return ColumnTransformer([
        ("num", StandardScaler() if scale else "passthrough", NUMERIC),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
    ])


def candidates() -> dict[str, Pipeline]:
    return {
        "logistic_regression": Pipeline([("pre", _pre(True)), ("clf", LogisticRegression(max_iter=2000, C=1.0))]),
        "random_forest": Pipeline([("pre", _pre(False)), ("clf", RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=5, random_state=42, n_jobs=-1))]),
        "xgboost": Pipeline([("pre", _pre(False)), ("clf", XGBClassifier(
            n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9, colsample_bytree=0.9,
            random_state=42, n_jobs=4, eval_metric="logloss"))]),
    }


LABELS = {"majority_class": "Majority-class baseline", "logistic_regression": "Logistic Regression (baseline)",
          "random_forest": "Random Forest", "xgboost": "XGBoost"}


@dataclass
class DelayModelBundle:
    model_name: str
    model: Pipeline
    metrics: dict
    reference: dict  # typical values for occlusion explanations
    feature_importance: dict
    train_period: dict
    calibration: list = field(default_factory=list)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path, compress=3)
        return path

    @staticmethod
    def load(path: Path) -> "DelayModelBundle":
        return joblib.load(path)


def chronological_split(frame: pd.DataFrame, test_fraction: float = TEST_FRACTION):
    f = frame.sort_values("order_date").reset_index(drop=True)
    cut = int(len(f) * (1 - test_fraction))
    return f.iloc[:cut], f.iloc[cut:]


def train(frame: pd.DataFrame) -> DelayModelBundle:
    if len(frame) < 100:
        raise ValueError(f"Need at least 100 delivered orders to train, got {len(frame)}")
    train_df, test_df = chronological_split(frame)
    X_tr, y_tr = train_df[NUMERIC + CATEGORICAL], train_df["late"].astype(int)
    X_te, y_te = test_df[NUMERIC + CATEGORICAL], test_df["late"].astype(int)

    metrics = {"majority_class": classification_metrics(y_te, np.full(len(y_te), float(y_tr.mean() >= 0.5)))}
    fitted = {}
    for name, pipe in candidates().items():
        pipe.fit(X_tr, y_tr)
        metrics[name] = classification_metrics(y_te, pipe.predict_proba(X_te)[:, 1])
        fitted[name] = pipe
    best = max(("random_forest", "xgboost"), key=lambda n: metrics[n]["roc_auc"] or 0)

    # calibration table on the test set for the chosen model (predicted vs observed late rate)
    proba = fitted[best].predict_proba(X_te)[:, 1]
    bins = pd.cut(proba, [0, 0.2, 0.4, 0.6, 0.8, 1.0], include_lowest=True)
    calib = (pd.DataFrame({"bin": bins.astype(str), "p": proba, "y": y_te.to_numpy()})
             .groupby("bin", observed=True).agg(predicted=("p", "mean"), observed=("y", "mean"), n=("y", "size"))
             .reset_index().round(3).to_dict(orient="records"))

    final = candidates()[best].fit(frame[NUMERIC + CATEGORICAL], frame["late"].astype(int))
    reference = {c: float(frame[c].median()) for c in NUMERIC} | {c: str(frame[c].mode().iloc[0]) for c in CATEGORICAL}
    return DelayModelBundle(
        model_name=best, model=final, metrics=metrics, reference=reference,
        feature_importance=_importance(final), calibration=calib,
        train_period={
            "train_from": str(train_df["order_date"].min()), "train_to": str(train_df["order_date"].max()),
            "test_from": str(test_df["order_date"].min()), "test_to": str(test_df["order_date"].max()),
            "n_train": len(train_df), "n_test": len(test_df),
        },
    )


def _importance(pipe: Pipeline) -> dict[str, float]:
    names = pipe.named_steps["pre"].get_feature_names_out()
    imp = pipe.named_steps["clf"].feature_importances_
    agg: dict[str, float] = {}
    for n, v in zip(names, imp):
        base = n.split("__", 1)[1]
        key = next((c for c in CATEGORICAL if base.startswith(c + "_")), base)
        label = FEATURE_LABELS.get(key, key)
        agg[label] = agg.get(label, 0) + float(v)
    total = sum(agg.values()) or 1
    return {k: round(v / total, 4) for k, v in sorted(agg.items(), key=lambda kv: -kv[1])}


def risk_category(p: float) -> str:
    return "High" if p >= 0.6 else "Medium" if p >= 0.35 else "Low"


def predict_with_explanation(bundle: DelayModelBundle, rows: pd.DataFrame, top_k: int = 4) -> list[dict]:
    """Delay probability + occlusion explanation. All perturbed rows are scored in one batch."""
    X = rows[NUMERIC + CATEGORICAL].reset_index(drop=True)
    groups: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for col in NUMERIC + CATEGORICAL:
        if col in seen:
            continue
        cols = ["month_sin", "month_cos"] if col in ("month_sin", "month_cos") else [col]
        seen.update(cols)
        groups.append((FEATURE_LABELS.get(col, col), cols))
    perturbed = []
    for i in range(len(X)):
        for _, cols in groups:
            alt = X.iloc[[i]].copy()
            for c in cols:
                alt[c] = bundle.reference[c]
            perturbed.append(alt)
    proba = bundle.model.predict_proba(X)[:, 1]
    alt_p = bundle.model.predict_proba(pd.concat(perturbed, ignore_index=True))[:, 1] if perturbed else []
    results = []
    for i in range(len(X)):
        p = float(proba[i])
        effects = {label: p - float(alt_p[i * len(groups) + j]) for j, (label, _) in enumerate(groups)}
        top = sorted(effects.items(), key=lambda kv: -abs(kv[1]))[:top_k]
        results.append({
            "delay_probability": round(p, 4),
            "risk_category": risk_category(p),
            "contributing_factors": [
                {"factor": k, "effect": round(v, 4), "direction": "increases risk" if v > 0 else "reduces risk"}
                for k, v in top if abs(v) >= 0.005
            ],
        })
    return results
