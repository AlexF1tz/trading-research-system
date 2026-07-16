# US Equity Catalyst Research

Decision-support research for a simulated university trading competition running from 27 July through 27 September 2026. The system is intended to identify US-equity catalyst and momentum candidates and estimate probabilistic outcomes. It must never place or route an order.

## Current status

Architecture and staged implementation planning are complete. Provider-neutral market-data, catalyst-intelligence, and retail-attention engineering slices are implemented with explicitly synthetic fixtures, replaceable normalized-input interfaces, and fail-closed quality checks. No modelling result, return claim, live platform coverage, or production readiness is asserted. The repository was empty when planning began on 16 July 2026.

The initial deliverables are:

- [Architecture](docs/ARCHITECTURE.md)
- [Staged implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Data sources, costs, and licensing](docs/DATA_SOURCES.md)
- [Initial data dictionary](docs/DATA_DICTIONARY.md)
- [Prediction contract](docs/PREDICTION_SCHEMA.md)
- [Market-data pipeline](docs/MARKET_DATA_PIPELINE.md)
- [Catalyst intelligence](docs/CATALYST_INTELLIGENCE.md)
- [Retail-attention research](docs/RETAIL_ATTENTION.md)
- [Quantitative modelling](docs/MODELLING.md)

## Market-data sample

The sample requires no API key and no third-party runtime dependency. Its values are deterministic synthetic fixtures and must never be presented as historical market observations.

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
market-data-sample --config config/market_data.sample.json
```

The command writes ignored JSONL features, a quality report, and a run manifest under `output/market_data_sample/`. Set `provider` to `csv_directory` and supply `provider_path` in a separate config to replace the fixture with a licensed provider export that follows the documented contract.

## Catalyst-intelligence sample

The catalyst sample likewise uses synthetic documents only. It covers every configured category, primary/secondary verification, numerical extraction, stale and repeated announcements, promotional wording, and an unverified social rumour that is never promoted to confirmed news.

```powershell
catalyst-sample --config config/catalyst.sample.json
```

See [Catalyst intelligence](docs/CATALYST_INTELLIGENCE.md) for timestamp, source-priority, classification, and licensing semantics.

## Retail-attention sample

The retail-attention sample uses synthetic mentions only and makes no network requests. It measures raw/baseline counts, velocity and acceleration, author independence, engagement, sentiment, repost/copy patterns, source concentration, catalyst links, promotional risk, and attention stage. It explicitly produces no trade recommendation.

```powershell
retail-attention-sample --config config/retail_attention.sample.json
```

See [Retail-attention research](docs/RETAIL_ATTENTION.md) for access declarations, timestamp semantics, null-on-incomplete coverage behavior, normalized export requirements, and unresolved platform rights.

## Quantitative-modelling sample

The modelling sample compares historical frequency, transparent rules, regularized logistic/linear models, and gradient-boosted decision stumps across explicit chronological train, calibration, embargo, and untouched final-test periods. All bundled rows are synthetic and cannot support a trading-performance claim.

```powershell
quant-modelling-sample --config config/modelling.sample.json
```

See [Quantitative modelling](docs/MODELLING.md) for target definitions, calibration, costed ranking metrics, clustered bootstrap intervals, subgroup breakdowns, instability diagnostics, and missing real-data requirements.

## Non-negotiable constraints

- Decision support only: no broker order endpoints and no automatic execution.
- Produce probabilities and excursion estimates, never one exact future price.
- Respect event time, first-seen time, and training cutoffs.
- Use chronological walk-forward evaluation; never randomly shuffle time-series observations.
- Include delisted and inactive securities when the selected data source supports them.
- Model spreads, slippage, halts, and fill uncertainty at the fidelity supported by the data.
- Persist each shadow prediction before its outcome is observable.
- Do not describe a strategy as profitable without genuinely held-out evidence after costs.

## Planned local stack

Python 3.12, Parquet plus DuckDB for local analytical storage, scikit-learn baselines, an optional gradient-boosting implementation selected after the baseline, pytest, and a read-only Streamlit dashboard. Exact dependencies will be pinned during Stage 1.

## Credentials

No credentials are required for the planning stage or deterministic engineering fixtures. A free market-data API credential becomes genuinely required in Stage 2 to run the first real historical minute-bar sample. A consolidated real-time SIP entitlement is strongly recommended before competition shadow operation; free IEX-only live data is incomplete for a Nasdaq/NYSE low-float scanner.
