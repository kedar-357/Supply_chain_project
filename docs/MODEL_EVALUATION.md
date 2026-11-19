# Model Evaluation

All numbers below were measured on **held-out data with chronological splits** on the sample dataset
(simulated, seed 42, daily history 20 Nov 2024 – 19 Nov 2026, 116,800 sales rows). Regenerating the
sample on another date (`python -m app.seed.init_data --reset --regenerate --train`) shifts the calendar,
so exact values differ slightly. The application always shows the metrics of the currently deployed
run (Demand Forecasting → *Model evaluation*, Supplier Analytics → *Delay prediction*); every run's
metrics are stored in `backend/data/model_registry.json` and its model files in `backend/artifacts/`.

## 1. Demand forecasting

**Protocol**

- Per product, daily units summed across warehouses (gap-free series; days without sales = 0).
- Test set = the **last 60 days**; every model is fitted on the preceding history only.
- Forecasts over the test window are **recursive multi-step** (horizon 1 → 60), exactly as in production —
  predicted values feed the lag/rolling features of later days. This is harder (and more honest) than
  one-step-ahead evaluation.
- Promotions/discounts are treated as planned inputs known in advance; future days in production assume
  no promotion unless a plan is supplied.
- Leakage control: every demand feature for day *t* is built from `y.shift(1)` or earlier; a unit test
  tampers with `y[t:]` and asserts the features up to *t* are unchanged, and another asserts the
  prediction-time feature builder reproduces the training features exactly.

**Models**

| Model | Description |
|---|---|
| XGBoost Regressor | lags 1/7/14/28, 7- and 30-day moving averages, 7-day volatility, day-of-week, month, weekend, yearly sin/cos, discount %, promotion flag |
| SARIMA (statistical baseline) | SARIMA(1,0,1)(1,0,1)[7] with constant, yearly Fourier terms and discount as exogenous regressors |
| Seasonal naive (naive baseline) | repeats the last observed week |

**Portfolio results (40 products × 60 days = 2,400 observations, errors pooled)**

| Model | MAE | RMSE | MAPE* | WAPE | Bias | Best model on |
|---|---:|---:|---:|---:|---:|---:|
| **XGBoost** | **12.49** | **19.32** | **30.1 %** | **23.5 %** | −2.68 | **22 products** |
| SARIMA | 16.10 | 26.34 | 31.4 % | 30.4 % | −11.58 | 16 products |
| Seasonal naive | 16.52 | 24.88 | 39.5 % | 31.1 % | −4.85 | 2 products |

Median per-product WAPE: XGBoost 23.8 %, SARIMA 28.5 %, seasonal naive 33.2 %.

\* MAPE is undefined when the actual value is 0; those observations (6 of 2,400) are excluded from MAPE
and the count is reported. WAPE (Σ|error| ÷ Σ actual) is robust to zeros and is the primary metric.

**Interpretation.** XGBoost reduces WAPE by ~24 % relative to the naive benchmark and ~22 % relative to
SARIMA. SARIMA is still the better model for 16 products (mostly smooth, low-promotion series), so the
platform does **not** assume one model wins everywhere: each product's *recommended model* is the one
with the lowest holdout MAE, and downstream inventory calculations use that model. SARIMA's larger
negative bias (under-forecasting) comes from the festive-season peak inside the holdout window, which
its weekly seasonal structure captures only through the Fourier terms.

**Prediction intervals.** SARIMA: model-based 95 % confidence interval. XGBoost: empirical 95 % band
(±1.96 σ) where σ is the standard deviation of holdout residuals around the same horizon, widened by
√(h/60) beyond the evaluated 60 days.

**Explainability.** Global XGBoost gain importance (averaged over products) is led by promotion
(25 %) and discount (17 %), then weekend, 7-day moving average and seasonal/calendar features. Each
individual XGBoost forecast shows SHAP contributions (`pred_contribs`) for the first forecast day.

## 2. Supplier delivery-delay classification

**Target:** a purchase order is *late* if `actual_date > promised_date` (44.8 % of test orders).

**Protocol:** orders sorted by order date; first 80 % train (20 Nov 2024 – 23 Jun 2026, 2,372 orders),
last 20 % test (23 Jun – 14 Nov 2026, 594 orders). Supplier-history features (prior late rate, prior
average delay, last delay, number of prior deliveries) use only deliveries that **arrived before the
order was placed**, verified by a unit test.

| Model | ROC-AUC | F1 | Precision | Recall | Accuracy |
|---|---:|---:|---:|---:|---:|
| Majority-class baseline | 0.500 | 0.000 | 0.000 | 0.000 | 0.552 |
| Logistic regression (baseline) | 0.833 | 0.720 | 0.730 | 0.711 | 0.753 |
| **Random Forest (deployed)** | **0.838** | 0.724 | 0.755 | 0.696 | **0.763** |
| XGBoost | 0.820 | 0.696 | 0.768 | 0.635 | 0.751 |

**Interpretation.** All learned models clearly beat the majority-class baseline (+0.34 ROC-AUC). The
logistic-regression baseline is close to the tree models on this dataset — the simulated delay process
is nearly additive (supplier reliability + season + distance + order size), which a linear model
captures well. Between the two models the specification asks for (Random Forest, XGBoost) the one with
the better test ROC-AUC is deployed; the full comparison is shown in the UI. (On an earlier regeneration
of the sample data the logistic baseline narrowly beat the Random Forest — the gap is within noise.)

**Calibration (deployed model, test set)**

| Predicted band | Mean predicted | Observed late rate | n |
|---|---:|---:|---:|
| 0.0 – 0.2 | 10.6 % | 9.4 % | 170 |
| 0.2 – 0.4 | 29.1 % | 31.1 % | 122 |
| 0.4 – 0.6 | 48.8 % | 52.5 % | 101 |
| 0.6 – 0.8 | 70.2 % | 77.5 % | 178 |
| 0.8 – 1.0 | 82.5 % | 91.3 % | 23 |

Probabilities are well calibrated at the low end and slightly conservative (under-predicting) above 0.6.

**Explainability.** For each prediction the API returns the top factors as occlusion effects: the
change in predicted probability when the factor is replaced by its typical (training median/mode)
value. Most influential features overall: shipping distance, supplier's historical late-delivery rate,
supplier's historical average delay, time of year.

**Historical score vs ML prediction.** The *supplier performance score* is descriptive (weighted,
min-max normalised history: on-time 35 %, quality 25 %, cost 20 %, lead-time consistency 20 %, weights
adjustable). The *delay probability / risk category* is an ML prediction for a typical next order.
They are labelled separately throughout the UI.

## 3. Reproducing

```bash
cd backend
python -m app.seed.init_data --reset --regenerate --train   # fresh sample data + retrain both models
python -m pytest tests/test_ml.py -v                          # leakage, split and accuracy tests
```
