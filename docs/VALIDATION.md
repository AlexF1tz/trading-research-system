# Independent Model Validation

Status: automated red-team validation of the synthetic chronological modelling fixture. The current disposition is `REJECT_ALL_MODELS_FOR_PROMOTION_OR_PERFORMANCE_CLAIMS`.

## Independence boundary

The validator treats model reports and predictions as untrusted inputs. Its metric implementation does not import `equity_research.modelling.evaluation`; it independently implements labels, Brier score, log loss, equal-width calibration and ECE, top-1/3/5/10 rankings, event-clustered bootstrap intervals, regression errors, and all eight target-before-stop breakdowns. It uses shared data contracts only so observations and predictions can be aligned.

The 2,247 matched checks cover final-test classification/regression outputs, calibration bins, ranking metrics, clustered intervals, and selected-model subgroup breakdowns. Walk-forward selection, train-resubstitution errors, feature effects, and overfitting flags are **not independently reproducible from the current export**: fold membership, fold predictions, fitted-transform manifests, and model/effect snapshots are missing. The audit records that limitation as `MODEL_SELECTION_REPRODUCTION=not_verifiable` and requires those immutable artifacts before promotion.

Matching metrics proves that the report arithmetic is reproducible. It does not validate source timestamps, universe completeness, fills, costs, or predictive value.

## Audit coverage

The structured audit covers:

- feature/outcome availability and prediction training cutoffs;
- chronological partitions, purged expanding walk-forward folds, and an AST scan for common random split calls;
- inactive/delisted universe coverage;
- first-public announcement-time verifiability;
- original-versus-revised raw-data lineage;
- declared cost reconciliation and per-model 1.0x/1.5x/2.0x cost stress;
- a low-float-unfilled plus 2.5x-cost scenario;
- bid/ask, size, capacity, latency, same-barrier-touch, and fill-fidelity evidence;
- halt and reopening handling;
- candidate, abstention, rejection, and catalyst-discovery completeness;
- copied/coordinated social-post lineage;
- repeated final-test access;
- top-10 ticker concentration and leave-one-security-out Brier scores;
- synthetic-versus-empirical status.

Every model/target pair receives an explicit promotion decision. A failing cost scenario adds `COST_STRESS` to that pair's rejection reasons. All pairs are currently rejected by broader evidence blockers even when a synthetic diagnostic cost scenario is positive.

## Commands and outputs

```powershell
python -m pip install -e .
model-validation-sample --config config/validation.sample.json
python -m unittest tests.test_validation_red_team -v
```

The command writes ignored artifacts under `output/validation_sample/`:

- `validation_report.json`: machine-readable findings, stresses, concentration, and model decisions;
- `VALIDATION_REPORT.md`: generated human-readable report;
- `run_manifest.json`: disposition and reproduction summary.

The authored repository report is [VALIDATION_REPORT.md](../reports/VALIDATION_REPORT.md).

## Promotion gate

No model can advance to a shadow-performance claim until all blocker findings pass on real timestamp-valid data. At minimum this requires a survivorship-safe point-in-time universe, immutable original/revision lineage, independently corroborated first-public times, a full candidate and rejection ledger, halt-aware paths, quote/capacity-aware conservative fills, a registered hypothesis, and a newly locked final period.

The validator may approve engineering behavior while rejecting empirical promotion. It never authorizes execution: the entire system remains decision support only.
