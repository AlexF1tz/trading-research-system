# US Equity Catalyst Research

Decision-support research for a simulated university trading competition running from 27 July through 27 September 2026. The system is intended to identify US-equity catalyst and momentum candidates and estimate probabilistic outcomes. It must never place or route an order.

## Current status

Architecture and staged implementation planning are complete. No modelling result, return claim, or production readiness is asserted yet. The repository was empty when planning began on 16 July 2026.

The initial deliverables are:

- [Architecture](docs/ARCHITECTURE.md)
- [Staged implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Data sources, costs, and licensing](docs/DATA_SOURCES.md)
- [Initial data dictionary](docs/DATA_DICTIONARY.md)
- [Prediction contract](docs/PREDICTION_SCHEMA.md)

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

