"""ML tests: leakage, chronological splits, metric edge cases, and measured accuracy vs baselines."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.ml.evaluation.metrics import classification_metrics, regression_metrics
from app.ml.forecasting.features import FEATURES, build_features, daily_series, feature_row
from app.ml.forecasting.pipeline import HOLDOUT_DAYS, split, train_product
from app.ml.supplier_prediction import pipeline as delay_ml
from app.ml.supplier_prediction.features import build_frame, supplier_history_features
from app.seed.synthetic import generate


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("syn"), end_date=date(2026, 6, 30))


def _series(tables, sku):
    s = tables["sales"]
    s = s[s["sku"] == sku].copy()
    s["date"] = pd.to_datetime(s["date"])
    return daily_series(s)


# ------------------------------------------------------------------ metrics
def test_mape_excludes_zero_actuals_and_reports_them():
    m = regression_metrics([0, 10, 20], [5, 8, 22])
    assert m["mape_excluded_zero_actuals"] == 1
    assert m["mape"] == pytest.approx((0.2 + 0.1) / 2 * 100)
    assert m["mae"] == pytest.approx(3.0)
    assert m["wape"] == pytest.approx(9 / 30 * 100)


def test_mape_all_zero_is_none():
    assert regression_metrics([0, 0], [1, 2])["mape"] is None


def test_classification_metrics_single_class_auc_none():
    assert classification_metrics([0, 0, 0], [0.1, 0.2, 0.3])["roc_auc"] is None


# ------------------------------------------------------------------ leakage & splits
def test_lag_features_do_not_use_current_or_future_target(synthetic):
    ser = _series(synthetic, "SKU-101")
    base = build_features(ser)
    t = 200
    tampered = ser.copy()
    tampered.iloc[t:, tampered.columns.get_loc("units")] += 10_000  # change today and the future
    f2 = build_features(tampered)
    demand_feats = [c for c in FEATURES if c.startswith(("lag_", "ma_", "std_"))]
    pd.testing.assert_frame_equal(base.iloc[: t + 1][demand_feats], f2.iloc[: t + 1][demand_feats])


def test_feature_row_matches_training_features(synthetic):
    """The recursive prediction path must build exactly the same features as training."""
    ser = _series(synthetic, "SKU-105")
    f = build_features(ser)
    t = 400
    row = feature_row(ser["units"].to_numpy(float)[:t], ser.index[t], ser["discount_pct"].iloc[t], ser["promotion"].iloc[t])
    for k in FEATURES:
        assert row[k] == pytest.approx(float(f.iloc[t][k]), rel=1e-9, abs=1e-9), k


def test_chronological_split_has_no_overlap(synthetic):
    ser = _series(synthetic, "SKU-101")
    train, test = split(ser)
    assert len(test) == HOLDOUT_DAYS
    assert train.index.max() < test.index.min()


# ------------------------------------------------------------------ accuracy (measured on held-out data)
@pytest.mark.parametrize("sku", ["SKU-101", "SKU-119", "SKU-114"])
def test_ml_forecast_beats_naive_baseline_on_holdout(synthetic, sku):
    ser = _series(synthetic, sku)
    b = train_product(1, sku, ser)
    best_ml = min(b.metrics["xgboost"]["wape"], b.metrics["sarima"]["wape"])
    assert best_ml < b.metrics["seasonal_naive"]["wape"], b.metrics
    assert len(b.holdout["actual"]) == HOLDOUT_DAYS


# ------------------------------------------------------------------ delay model
def _delay_frame(t):
    po = t["purchase_orders"].groupby("po_number").agg(
        supplier_code=("supplier_code", "first"), order_date=("order_date", "first"),
        expected_date=("expected_date", "first"), total_quantity=("quantity", "sum"), n_lines=("sku", "size")).reset_index()
    dl = t["deliveries"].merge(po, on="po_number")
    dl["delay_days"] = (pd.to_datetime(dl["actual_date"]) - pd.to_datetime(dl["expected_date"])).dt.days
    sup = t["suppliers"].set_index("code")
    dl["supplier_id"] = dl["supplier_code"]
    dl["region"] = dl["supplier_code"].map(sup["region"])
    dl["category"] = "x"
    dl["quoted_lead_days"] = (pd.to_datetime(dl["expected_date"]) - pd.to_datetime(dl["order_date"])).dt.days
    dl["order_date"] = pd.to_datetime(dl["order_date"]).dt.date
    dl["actual_date"] = pd.to_datetime(dl["actual_date"]).dt.date
    dl["late"] = (dl["delay_days"] > 0).astype(int)
    return dl


def test_supplier_history_uses_only_earlier_deliveries():
    orders = pd.DataFrame({"supplier_id": [1, 1], "order_date": [date(2026, 1, 10), date(2026, 1, 20)]})
    deliveries = pd.DataFrame({"supplier_id": [1, 1, 1], "actual_date": [date(2026, 1, 5), date(2026, 1, 15), date(2026, 1, 25)],
                               "delay_days": [2, -1, 9]})
    f = supplier_history_features(orders, deliveries)
    assert f.iloc[0]["sup_prior_count"] == 1 and f.iloc[0]["sup_prior_avg_delay"] == 2
    assert f.iloc[1]["sup_prior_count"] == 2 and f.iloc[1]["sup_prior_late_rate"] == 0.5  # 25 Jan delivery excluded


def test_delay_model_beats_baselines_on_chronological_test_set(synthetic):
    frame = build_frame(_delay_frame(synthetic), _delay_frame(synthetic)[["supplier_id", "actual_date", "delay_days"]])
    tr, te = delay_ml.chronological_split(frame)
    assert pd.to_datetime(tr["order_date"]).max() <= pd.to_datetime(te["order_date"]).min()
    b = delay_ml.train(frame)
    deployed = b.metrics[b.model_name]
    assert deployed["roc_auc"] >= 0.70, b.metrics
    assert deployed["roc_auc"] > b.metrics["majority_class"]["roc_auc"]
    preds = delay_ml.predict_with_explanation(b, frame.head(3))
    assert all(0 <= p["delay_probability"] <= 1 for p in preds)
    assert all(p["risk_category"] in ("Low", "Medium", "High") for p in preds)


def test_synthetic_generator_is_deterministic(tmp_path):
    a = generate(tmp_path / "a", end_date=date(2026, 3, 31))
    b = generate(tmp_path / "b", end_date=date(2026, 3, 31))
    pd.testing.assert_frame_equal(a["sales"], b["sales"])
    pd.testing.assert_frame_equal(a["deliveries"], b["deliveries"])


def test_synthetic_inventory_reconciles(synthetic):
    """Sales never exceed stock: end inventory is non-negative for every product × warehouse."""
    assert (synthetic["inventory"]["quantity_on_hand"] >= 0).all()
    assert (synthetic["sales"]["units_sold"] >= 0).all()
    assert np.isfinite(synthetic["sales"]["unit_price"]).all()


def test_uci_online_retail_adapter_cleans_and_aggregates():
    from app.seed.prepare_uci_online_retail import transform

    raw = pd.DataFrame({
        "InvoiceNo": ["536365", "536365", "C536379", "536380", "536381"],
        "StockCode": ["85123A", "85123A", "85123A", "POST", "71053"],
        "Description": ["WHITE HANGING HEART", "WHITE HANGING HEART", "WHITE HANGING HEART", "POSTAGE", "WHITE METAL LANTERN"],
        "Quantity": [6, 4, -2, 1, 3],
        "InvoiceDate": ["2010-12-01 08:26", "2010-12-01 09:00", "2010-12-01 10:00", "2010-12-01", "2010-12-02"],
        "UnitPrice": [2.55, 2.55, 2.55, 18.0, 3.39],
        "Country": ["United Kingdom", "United Kingdom", "United Kingdom", "France", "France"],
    })
    out = transform(raw, top=10)
    assert set(out["products"]["sku"]) == {"UCI-85123A", "UCI-71053"}  # cancellation + POST removed
    s = out["sales"].set_index("sku")
    assert s.loc["UCI-85123A", "units_sold"] == 10 and s.loc["UCI-85123A", "warehouse_code"] == "WH-MUM"
    assert s.loc["UCI-71053", "warehouse_code"] == "WH-DEL"
    assert (out["products"]["selling_price"] >= out["products"]["unit_cost"]).all()
