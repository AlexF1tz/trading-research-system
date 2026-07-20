# US Equity Catalyst Research

Decision-support research for a simulated university trading competition running from 27 July through 27 September 2026. The system is intended to identify US-equity catalyst and momentum candidates and estimate probabilistic outcomes. It must never place or route an order.

## Current status

Architecture and staged implementation planning are complete. Stage 2 now uses a credential-gated Alpaca historical-bars adapter as the primary market-data path for a bounded real sample. The first three-stock, one-month IEX sample was retrieved on 17 July 2026 and passed raw/normalized reconciliation, but it remains blocked by 340 unobserved JPM minute intervals plus missing quote, halt, float, reference, and survivorship-safe universe data. Stage 3 adds a read-only shadow monitor in synthetic and cache/replay modes; it preserves that empirical-modelling block in every research alert and heartbeat. No model is promoted and no return claim, live platform coverage, or production readiness is asserted.

Run the offline Stage 3 operational rehearsal with `live-monitor --config config/shadow_monitor.sample.json`; it continues until Ctrl+C. See [Stage 3 shadow monitor](docs/SHADOW_MONITOR.md).

### SEC-first shadow monitor

The SEC provider is read-only and uses EDGAR submissions only. Set an identifying contact in a copied config (do not put credentials in the repository), then run:

```powershell
.\.venv\Scripts\Activate.ps1
live-monitor --config config\shadow_sec.sample.json
```

Or without changing `PATH`:

```powershell
.\.venv\Scripts\python.exe -m equity_research.shadow.cli --config config\shadow_sec.sample.json --max-cycles 2
```

SEC accession state is persisted at `data/shadow/sec_seen.json` so restarts do not re-alert old filings. For offline replay, supply a captured cache using `config\shadow_sec_replay.sample.json`:

```powershell
.\.venv\Scripts\python.exe -m equity_research.shadow.cli --config config\shadow_sec_replay.sample.json --max-cycles 1
```

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
- [Independent validation](docs/VALIDATION.md)
- [Current validation report](reports/VALIDATION_REPORT.md)
- [Alpaca historical Stage 2 adapter](docs/ALPACA_HISTORICAL.md)

## Offline market-data regression fixture

This test-only fixture requires no API key or third-party runtime dependency. Its values are deterministic and must never be presented as historical market observations or used to train a production candidate model.

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

## Independent validation sample

The validation command reruns the modelling fixture, independently recomputes covered final-test evaluation values without importing the modelling metric implementation, stress-tests costs and security concentration, scans for random time-series splitting, and applies fail-closed promotion gates. It separately flags walk-forward selection and instability outputs as not independently verifiable until fold artifacts are exported.

```powershell
model-validation-sample --config config/validation.sample.json
```

The current run reproduces 2,247 metric values exactly within tolerance but still rejects all 36 model/target combinations. Matching arithmetic is not evidence of a tradable edge. See [Independent validation](docs/VALIDATION.md) and the [written report](reports/VALIDATION_REPORT.md).

## Real historical data quality check — Stage 2

The Stage 2 command uses Alpaca's historical **market-data** endpoint only. It performs credentialed GET requests for a bounded three-stock sample, preserves each raw response and request manifest under ignored `data/raw/`, normalizes minute and daily bars into the existing provider-neutral schema, and writes an immutable timestamped quality run. Successful responses are cached for 24 hours by default; every cache hit is tied to the exact request URL and verified against the preserved response hash before reuse. It does not call account, order, position, or broker endpoints; it does not train a model or generate predictions.

The sample configuration uses the IEX feed for AAPL, MSFT, and JPM, all 21 regular trading-session dates from 1–30 June 2026, and a covering daily-bar window from 1 May through 30 June. Juneteenth (19 June) is excluded explicitly. IEX is single-venue data, so the result is deliberately marked incomplete and cannot support full-market volume, spread, liquidity, survivorship, or profitability claims. The repository's zero-dependency calendar is not authoritative; the explicit session list must be reviewed before changing the date range.

Exact setup from the repository root in Git Bash on Windows:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m unittest discover -s tests
cp .env.example .env
chmod 600 .env
notepad .env

alpaca-historical-quality \
  --config config/alpaca_historical.sample.json \
  --output-dir output/alpaca_historical_quality \
  --repo-root . \
  --env-file .env
```

In `.env`, fill only `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`; do not add broker-account credentials. Process-environment values override `.env` values. The loader does not interpolate variables, does not mutate the process environment, and never writes credential values to artifacts.

The command exits with status `2` after writing available diagnostics when authentication, retrieval, normalization, reconciliation, or quality checks fail. It never silently converts a failed check into a successful run, and no modelling command consumes this output automatically. Review [Alpaca historical Stage 2 adapter](docs/ALPACA_HISTORICAL.md) before using or retaining provider data.

Revalidate exact cached responses without reading `.env` or allowing network fallback:

```bash
alpaca-historical-quality \
  --cache-only \
  --config config/alpaca_historical.sample.json \
  --output-dir output/alpaca_historical_quality \
  --repo-root .
```

The first real-data audit and its remaining blockers are documented in [the 17 July Alpaca quality audit](reports/ALPACA_HISTORICAL_QUALITY_AUDIT_20260717.md).

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

No credentials are required for planning, deterministic engineering fixtures, or exact cache-only revalidation. A legitimate market-data credential is required for a new historical retrieval. A consolidated real-time SIP entitlement is strongly recommended before competition shadow operation; free IEX-only live data is incomplete for a Nasdaq/NYSE low-float scanner.
