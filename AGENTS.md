# Project Rules

These instructions apply to the entire repository.

## Purpose

- This repository is a research and decision-support tool for US-listed equities. It must not automatically place or manage trades.
- Do not add broker order submission, order routing, auto-trading, unattended execution, or automatic position-management code.
- Adapters may read market data and account-independent reference data. Do not introduce trading credentials or broker account mutation.

## Data integrity

- Never fabricate market, float, borrow, short-interest, quote, news, or social-media data. Represent unavailable values explicitly and lower the data-quality score.
- Preserve source URLs, source-native identifiers, original timestamps, and immutable raw values.
- Preserve `event_at`, `published_at`, `first_seen_at`, `ingested_at`, and `available_at` where applicable. `available_at` must conservatively represent when information first became publicly available to the system.
- Store timestamps in UTC internally; derive exchange sessions with `America/New_York`.
- All feature joins must be as-of joins using `available_at <= prediction_as_of`.
- Never silently forward-fill catalyst or social-attention data. Missing observations remain missing and receive explicit coverage/quality flags.
- Never randomly shuffle time-series observations. Use expanding or rolling walk-forward splits with a separate chronological calibration interval and an embargo when outcome windows overlap.
- Keep raw values and corporate-action factors. Do not allow a later split adjustment, revised filing, renamed ticker, or backfilled article to leak into an earlier feature row.
- Use stable security identifiers internally. Tickers are time-varying labels.
- Historical universe construction must include inactive and delisted securities. If the selected data cannot provide them, the run must fail or be explicitly labelled a limited sample and cannot support survivorship-bias-safe claims.

## Modelling

- Establish transparent rule-based and simple statistical baselines before complex models.
- Use chronological train, validation/calibration, and test sets. Keep an untouched final test period.
- Fit imputers, encoders, scalers, feature selectors, calibrators, and models on the permitted training/calibration windows only.
- Evaluate Brier score, log loss, calibration error/reliability, discrimination, uncertainty, coverage, and abstention. Accuracy alone is insufficient.
- Reject models whose apparent advantage disappears after documented spread, slippage, fill, and capacity assumptions.
- Prefer the simpler model when performance differences are statistically or practically insignificant.
- No result may be described as profitable without untouched out-of-sample evidence after documented costs and uncertainty.

## Execution assumptions

- Backtests must account for the available quote or bar resolution, bid-ask spread, estimated slippage, halts, unfilled orders, and ambiguous same-barrier touches.
- Do not assume stop orders fill at their trigger price, especially through halts or gaps.
- Do not assume the full requested position can be filled. Apply volume/quote-size participation limits and stricter assumptions to low-float securities.
- If fill fidelity is unavailable, flag the observation and conservatively handle or exclude it under a versioned policy.

## Live testing

- Save all predictions and abstentions in immutable timestamped files before their outcomes are known.
- Never edit a prediction after its outcome is known. Outcomes are append-only records that reference prediction IDs.
- Record every signal, not only successful signals. Record rejected signals with machine-readable rejection reasons.
- The dashboard and journal may display or annotate predictions but must not mutate their original records.

## Code quality

- Use type hints for application code and public interfaces.
- Provider-specific logic belongs behind adapters; normalized schemas and model code must not depend on vendor response shapes.
- Raw ingestions are immutable and content-addressed. Keep source URL/identifier, retrieval time, response hash, and adapter version.
- Add deterministic tests for important calculations, time boundaries, label ordering, fills, costs, calibration, and leakage controls. Unit tests must not require network access; network integration tests are opt-in and credential-gated.
- Validate configuration and schema versions at process boundaries. Fail loudly and closed when critical data is missing, stale, partial, future-dated, or internally inconsistent.
- Keep data ingestion separate from feature engineering and modelling.
- Keep the local dashboard read-only with respect to predictions and market data. Journal entries are manual user actions.
- Document each external source, its fields, retrieval behavior, cost, retention/display constraints, and licence restrictions. Update the source documentation and data dictionary whenever a field or provider is added.
- Never commit credentials, API keys, secrets, vendor raw data, licensed article text, or API responses whose terms prohibit storage or redistribution.
