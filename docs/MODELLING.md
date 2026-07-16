# Quantitative Modelling Pipeline

Status: dependency-free engineering baseline using an explicit synthetic fixture. It is not empirical evidence of predictability, profitability, or production readiness.

## Scope

The pipeline compares four model families for every requested binary outcome:

- +10% target before -5% stop;
- touch +5%, +10%, and +20%;
- touch -5% and -10%;
- continuation versus reversal where the label is unambiguous.

Reversal probability is emitted as `1 - P(continuation)` on those non-ambiguous rows; a second contradictory binary model is not fitted.

For maximum favourable excursion (MFE) and maximum adverse excursion (MAE), it compares the corresponding historical mean, transparent rules, regularized linear regression, and gradient-boosted regression stumps. MFE predictions are constrained non-negative and MAE predictions non-positive.

The implementation contains no trading or broker interface.

## Required point-in-time row

Each normalized modelling row preserves:

- stable observation, event, and security IDs plus the ticker valid at prediction time;
- `prediction_as_of`, `features_available_at`, and `outcome_available_at` in UTC;
- exact numeric features, with missing values preserved;
- all path labels, excursions, and continuation ambiguity;
- gross return, spread cost, slippage cost, fill status, and net return after costs;
- label and fill-policy versions;
- source URL, data-quality score, and every requested breakdown category.

The pipeline fails closed when a feature is available after the prediction, an outcome is already available at prediction time, an outcome has not matured by dataset fetch time, barrier labels contradict MFE/MAE, cost arithmetic is inconsistent, timestamps are not UTC, or feature schemas differ.

The current barrier pair is explicitly +10% before -5%. Other pairs require new versioned labels rather than relabelling this field silently.

## Chronology and leakage controls

Configuration requires exact UTC boundaries for training, calibration, and final test. Gaps between periods are embargo intervals. Training outcomes must mature before calibration begins; calibration outcomes must mature before the final test begins.

Model selection uses expanding walk-forward folds inside the training period only. A fold purges any training row whose outcome overlaps the validation start. Rows are sorted chronologically and never randomly shuffled.

Median imputers, missing-value indicators, means, standard deviations, and every fitted model use training rows only. Platt calibration uses only the separate calibration period. The final test is opened solely for the final comparison and is never used to select model family, hyperparameters, thresholds, transformations, or calibrators.

## Models

### Historical baseline

Binary probabilities use a Laplace-smoothed training frequency. MFE and MAE use the training mean. These establish the base-rate benchmark.

### Transparent rules

A fixed, documented score combines gap, relative volume, five-minute momentum, catalyst materiality, retail-attention acceleration, independent-author score, promotion risk, dilution risk, spread, and volatility. Only the base log-odds or mean comes from training history; feature weights are not optimized on the fixture.

### Logistic and linear baselines

Binary targets use L2-regularized batch logistic regression. MFE and MAE use L2-regularized linear regression. Both consume training-fitted standardized features plus explicit missing indicators.

### Gradient-boosted trees

The dependency-free comparator uses deterministic gradient boosting with depth-one decision trees and fixed estimator count, learning rate, and candidate quantiles. It is deliberately modest. A later production comparison may use a mature library only after dependencies are approved and pinned, with the same point-in-time folds and final-test isolation.

## Evaluation

Every binary model report includes:

- sample count, positives, and base rate;
- Brier score and log loss;
- equal-width calibration curve and expected calibration error;
- precision at the top 1, 3, 5, and 10 ranked signals, always shown beside the base rate;
- target-before-stop rate for each ranked selection;
- expectancy after declared spread and slippage, with unfilled signals contributing zero;
- fill rate;
- 95% bootstrap intervals for base rate, Brier score, log loss, and top-10 costed expectancy.

Bootstrap samples are clustered by event ID with a fixed reproducibility seed. Bootstrap resampling is an uncertainty calculation, not a randomized time-series train/test split.

Regression reports include mean actual/prediction, MAE, RMSE, bias, and event-clustered bootstrap intervals.

The selected target-before-stop model receives breakdowns by catalyst category, float category, market-cap category, market regime, time of day, gap size, relative volume, and retail-attention stage. Groups with fewer than ten final-test observations are explicitly warned as small samples.

No accuracy metric is reported. No return is described as profitable merely because a synthetic or small-sample expectancy is positive.

## Stability and overfitting diagnostics

For every target/model pair, feature effects are saved across walk-forward folds. The report flags:

- coefficient sign changes;
- high fold-to-fold relative variation;
- tree features selected in only a minority of folds;
- large training-resubstitution versus walk-forward Brier gaps;
- large training-resubstitution versus walk-forward MFE/MAE error gaps;
- final-test degradation versus earlier walk-forward performance.

These are evidence flags, not formal proof of overfitting. The fixture intentionally includes `unstable_sentiment_proxy`, whose relationship changes inside the training period, to verify that instability appears in the report.

Separately fitted touch models can violate probability nesting. The report counts violations of `P(+20) <= P(+10) <= P(+5)` and `P(-10) <= P(-5)` for each family and for the selected-per-target combination. Any violation requires repair or rejection before shadow use; the pipeline does not silently rewrite evaluation probabilities.

## Normalized JSONL provider

Set `provider` to `jsonl_directory` and point `provider_path` to a directory containing:

- `metadata.json`: provider, dataset kind, fetch time, exact feature names, barrier pair, survivorship status, and notes;
- `rows.jsonl`: normalized matured feature/outcome rows matching `ModelRow`.

The adapter does not fetch market or news data. Source licensing, raw-response retention, and point-in-time joins remain responsibilities of upstream adapters and must be documented before a real run.

## Sample

```powershell
python -m pip install -e .
quant-modelling-sample --config config/modelling.sample.json
```

Ignored outputs under `output/modelling_sample/` are:

- `modelling_report.json`;
- `final_test_evaluation_predictions.jsonl`;
- `quality_report.json`;
- `run_manifest.json`.

The prediction file is a matured evaluation artifact generated after outcomes are known. It is not a live shadow prediction file and must not be substituted for the append-before-outcome prediction registry.

## Missing real data

No real labelled dataset exists in the repository. A defensible empirical run still requires:

- survivorship-aware historical Nasdaq/NYSE identities, including inactive and delisted securities;
- point-in-time minute bars and quotes, corporate actions, halts, and realistic fills;
- immutable catalyst/news timestamps and classifications;
- point-in-time float and dilution information with explicit missingness;
- optional lawfully sourced retail-attention history with known coverage;
- candidate-generation logs containing rejected signals, not only successful movers;
- outcome paths and costs produced by a versioned backtester.

Until those rows exist, the synthetic report validates software behavior only.
