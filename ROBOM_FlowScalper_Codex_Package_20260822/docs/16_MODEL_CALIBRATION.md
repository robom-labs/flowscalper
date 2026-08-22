# 16. Optional Local Model and Calibration

## 16.1 Default

The first release trades paper using deterministic rules only. No model probability is required for the application to function.

## 16.2 Data collection

Store outcomes for:

- executed candidates;
- qualified but rejected candidates;
- near-miss candidates;
- strategy, regime, venue and symbol liquidity bucket;
- candidate features at decision time;
- TP-first, SL-first, edge-decay and timeout/stale outcomes;
- MAE/MFE and costs.

This reduces selection bias from training only on executed trades.

## 16.3 Allowed local models

After sufficient data:

- logistic regression;
- gradient-boosted trees such as XGBoost/CatBoost;
- calibrated empirical bucket model.

No online GPT calls and no mandatory deep neural network.

## 16.4 Labels

Use triple-barrier-like outcome labeling with event-accurate ordering:

- target reached first;
- stop reached first;
- edge-decay exit;
- emergency/data-gap outcome.

The label generation must use only future events after the candidate timestamp and must be isolated from feature construction.

## 16.5 Splitting

Use time-ordered splits:

- training;
- validation;
- untouched evaluation;
- rolling walk-forward.

No random shuffle across time. Do not retune repeatedly against the untouched set.

## 16.6 Probability calibration

Required before displaying probability as a decision metric:

- adequate sample size;
- calibration curve/Brier score;
- out-of-sample reliability;
- venue/strategy/regime drift checks;
- conservative confidence bounds.

Before these pass, UI value is `CALIBRATING`.

## 16.7 Promotion

A model may become a Paper gate only when:

- it improves net out-of-sample expectancy after costs;
- it remains useful under fee/slippage/latency stress;
- no single symbol or short period explains most benefit;
- probability is calibrated;
- model artifact, feature version and training data range are recorded;
- fallback to rule-only mode is tested.

Models never deploy to real trading in this project version.

## 16.8 Drift

Monitor:

- feature distribution shift;
- calibration deterioration;
- expected versus realized slippage;
- rolling net expectancy;
- symbol-universe changes.

Drift can disable the model gate and return to rule-only Paper mode automatically.
