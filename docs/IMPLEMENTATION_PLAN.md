# Staged Implementation Plan

Planning date: 16 July 2026. Competition start: 27 July 2026. The schedule prioritizes a small, auditable baseline and shadow reliability over model complexity.

## Delivery principles

- Each stage has an acceptance gate and can be reviewed independently.
- Deterministic fixtures validate engineering but are never treated as market evidence.
- The first real historical run waits for a legitimate market-data credential and records provider/feed provenance.
- Gradient boosting starts only after rule and logistic baselines produce valid chronological calibration reports.
- Competition-period model changes are versioned and evaluated without contaminating the standing holdout.

## Stage 0 — architecture and source decisions (16 July)

Deliverables in this stage:

- repository and instruction audit;
- architecture and time/identity contracts;
- initial prediction schema and data dictionary;
- source, cost, licensing, and credentials matrix;
- staged schedule and acceptance gates.

Gate: stakeholders agree that the system is decision support only and select the historical/live market-data path before Stage 2. No credentials are needed to approve the architecture.

## Stage 1 — installable foundation and deterministic vertical slice (17–18 July)

Build:

- `pyproject.toml`, lock/pin policy, `.env.example`, structured configs, CLI skeleton, logging;
- validated schemas for instruments, source events, bars, features, predictions, and outcomes;
- immutable raw manifest and local Parquet/DuckDB catalogue;
- deterministic fixture containing synthetic instruments, an event, bars, a halt, spread changes, and known barrier paths;
- one end-to-end fixture command: ingest -> normalize -> classify -> feature -> score rules -> label -> report;
- unit/contract tests and CI-friendly offline test command.

Gate:

- clean environment installs successfully;
- offline tests pass;
- fixture pipeline is deterministic and its report is labelled `ENGINEERING FIXTURE — NOT EMPIRICAL EVIDENCE`;
- no module can submit an order.

## Stage 2 — real source ingestion and historical sample (18–21 July)

Build:

- SEC submissions/index/RSS client with declared user agent, cache, throttling, retry, and content hashes;
- Nasdaq symbol-directory snapshot and trade-halt RSS adapters;
- FINRA daily short-sale volume adapter with correct limitations;
- one credentialed market-data adapter, initially Alpaca or Massive, behind a normalized interface;
- effective-dated security identity resolution and source freshness checks;
- a small, dated historical sample of events and minute bars stored locally under provider terms.

Credential gate: a market-data API key is required here. A free Alpaca Basic key can support an initial historical sample older than 15 minutes; it is not sufficient evidence of full real-time market coverage. Do not request news or Reddit credentials yet unless those adapters are selected for this stage.

Gate:

- raw and normalized row counts reconcile;
- timestamps/feed/adjustment status and hashes are present;
- known halt/event samples pass hand checks;
- the real sample historical pipeline runs end to end;
- its report explicitly states universe, feed, news, quote, and float limitations.

## Stage 3 — labels, features, backtester, and baseline models (20–23 July)

Build:

- versioned strategy profiles, candidate triggers, horizons, and barrier pairs;
- point-in-time feature materialization with automated leakage assertions;
- path labelling for touches, first target/stop, MFE, MAE, and continuation/reversal;
- minute-bar execution approximation first, plus quote-aware execution when data permits;
- halts, spread/slippage, participation caps, ambiguous-bar policy, and missing-data abstention;
- deterministic rule scorecard;
- regularized logistic models and training-only MFE/MAE estimators;
- expanding walk-forward train/calibrate/test orchestration;
- calibration and coverage report.

Gate:

- no randomized split exists in code or tests;
- a test proves post-cutoff information cannot enter a feature row;
- prediction records precede outcome records;
- reliability metrics and plots accompany discrimination metrics;
- backtest report separates signal-path and executable-policy outcomes;
- no profitability claim is made from the development sample.

## Stage 4 — shadow predictions, dashboard, and journal (22–25 July)

Build:

- idempotent scheduled shadow cycle with atomic append-only prediction files;
- delayed outcome labeller that cannot mutate predictions;
- data freshness, latency, missingness, and duplicate monitors;
- read-only Streamlit candidate/model/source-health dashboard;
- manual simulated trade journal linked to prediction IDs;
- daily immutable run manifest, model version, config hash, and source watermark.

Gate:

- one live shadow prediction file is generated with `outcome_status=pending` before the horizon ends;
- dashboard reads that file locally and exposes no execution control;
- a later job appends its outcome without altering the prediction hash;
- stale or incomplete critical inputs cause abstention, not silent scoring.

## Stage 5 — boosting comparison and readiness review (24–26 July)

Build only if Stages 1–4 are green:

- one gradient-boosting classifier/regressor family using identical folds and features;
- chronological hyperparameter selection on development folds;
- calibration, stability, latency, interpretability, and coverage comparison against logistic;
- model-card and operational runbook;
- fresh-machine install/test/pipeline rehearsal and backup procedure.

Promotion rule: boosting replaces logistic only if it improves predeclared out-of-sample calibration/utility measures without unacceptable instability, latency, or coverage loss. Otherwise logistic remains primary.

Gate:

- all definition-of-done commands pass on a clean checkout;
- at least two shadow sessions complete without prediction mutation or data-freshness failure;
- unresolved licensing or feed-entitlement issues are visible blockers, not assumptions.

## Stage 6 — competition operation (27 July–27 September)

- Run prediction generation in shadow/decision-support mode only.
- Freeze model/config versions for declared evaluation blocks; log every change.
- Review source health and calibration drift daily, and evaluate outcomes only after horizons mature.
- Do not tune on the final holdout or repeatedly change thresholds in response to recent simulated P&L.
- Publish weekly calibration, coverage, and execution-assumption reports.
- Preserve every prediction, abstention, config hash, and manual journal decision.

## Initial definition-of-done command set

Exact command names will be finalized in Stage 1. The intended contract is:

```powershell
python -m pip install -e ".[dev]"
pytest
equity-research sample-pipeline --config config/sample.yaml
equity-research shadow-once --config config/shadow.yaml
equity-research dashboard --config config/dashboard.yaml
```

`sample-pipeline` must distinguish deterministic fixtures from real historical samples. `shadow-once` requires a configured live/delayed provider and writes a pending prediction even when the model abstains.

## Decisions needed, by stage

No decision blocks Stage 1. Before Stage 2, choose one market-data adapter:

1. **Alpaca-first:** lowest integration effort; free historical sample and IEX-only live baseline, with the currently published $99/month full-market tier as the likely competition upgrade.
2. **Massive-first:** stronger flat-file/trade/quote pathway and explicit personal tiers, but endpoint-level entitlement and team licensing must be confirmed before implementation.

Before Stage 4, decide whether the budget supports consolidated real-time SIP and a licensed company-news feed. Retail attention can remain absent in the first operational baseline; missing licensed data is preferable to brittle scraping.

## Deferred work

The following are intentionally outside the pre-competition critical path: deep learning/LLMs as primary classifiers, level-2 queue simulation, options flow, automated broker integration, cloud deployment, multi-user redistribution, and high-frequency tick backtests across the entire market.

