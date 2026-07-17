# Market-Data Pipeline

Stage 2 uses a bounded read-only Alpaca historical-bars adapter and quality-only command as its primary market-data path. See [ALPACA_HISTORICAL.md](ALPACA_HISTORICAL.md). The synthetic/CSV sample remains only as a deterministic offline regression and feature-engineering path; the real-data command intentionally stops after normalization and quality reporting.

Implemented 16 July 2026. This is an engineering vertical slice, not a profitable-strategy or market-coverage claim.

## What works

- Provider-neutral contracts for one-minute and daily OHLCV, premarket/regular sessions, optional bid/ask, instrument reference data, corporate actions, halts, and delisting state.
- A strict CSV-directory provider and a deterministic synthetic fixture provider implementing the same protocol.
- Point-in-time features: gap, time-of-day relative volume, dollar volume, float rotation, market capitalisation, session VWAP and distance, 1/5/15/30/60-minute momentum, volume acceleration, 14-period ATR, 30-minute realised volatility, and 5-minute sector/index-relative returns.
- Quality checks for duplicate/missing bars, session boundaries, split-adjustment state, impossible price/volume/quotes, stale or unavailable float, UTC/time alignment, source URLs, and bars available before completion.
- Halt-aware missing-bar checks: a declared halt interval does not become a false missing-data error.
- Raw bars remain immutable. `apply_known_split_adjustments` creates a new view using only actions effective and publicly available by an explicit knowledge timestamp.

There is no broker or order API.

## Provider contract

`MarketDataProvider.load()` returns one `ProviderDataset` containing:

- effective-dated `Instrument` records;
- `Bar` records separated into one-minute and daily collections;
- `CorporateAction` and `Halt` records;
- a `CoverageManifest` declaring dates, sessions, expected securities, quote breadth, historical-universe completeness, sector availability, float reliability, dataset kind, and notes.

Adapters must normalize source timestamps to UTC and retain `source`, `source_url`, `feed`, and `available_at`. Provider endpoint shapes do not enter feature or modelling code.

The included `CsvDirectoryProvider` expects:

```text
provider-export/
  metadata.json
  instruments.csv
  bars_1m.csv
  bars_1d.csv
  corporate_actions.csv   # optional
  halts.csv               # optional
```

`metadata.json` must at least declare `provider`, `dataset_kind`, `retrieved_at`, and `minute_dates`. A real export should also declare expected security IDs, sessions, quote consolidation, universe completeness, and field reliability. Missing required files or timezone offsets fail loudly.

## Feature definitions

| Feature | Implemented definition |
| --- | --- |
| Gap | Premarket: current close versus prior regular close. Regular: session open versus prior regular close. Known effective splits put the prior close on a comparable basis. |
| Relative volume | Cumulative volume in the current session divided by mean cumulative volume at the same wall-clock session minute on strictly prior dates. No future-day profile enters the denominator. |
| Dollar volume | Bar close multiplied by bar volume; cumulative day notional is also retained. |
| Float rotation | Cumulative premarket-plus-regular volume divided by free float. Null unless float and its availability timestamp are present before the bar. |
| Market capitalisation | Current close multiplied by point-in-time shares outstanding. Reported vendor market cap remains a separate reference field. |
| VWAP | Session-cumulative volume-weighted provider bar VWAP, or typical price when bar VWAP is absent. |
| Momentum | Return from the latest observation at or before the 1/5/15/30/60-minute wall-clock cutoff on the same market date. |
| Volume acceleration | Latest five consecutive one-minute volumes divided by the preceding five, minus one. Null across gaps or with insufficient history. |
| ATR | Mean true range over the latest 14 completed daily bars available at feature time; split-aware prior closes prevent mechanical split spikes. |
| Realised volatility | Square root of summed squared one-minute log returns across the latest 30 consecutive returns, expressed in percent and not annualised. |
| Relative returns | Security 5-minute momentum minus timestamp-aligned sector/index benchmark 5-minute momentum. |

Touching a later timestamp, current-day completed daily bar, revised share count, or future float observation is prohibited by the availability checks.

## Quality behaviour

Errors stop the pipeline by default. Warnings preserve the run while lowering its interpretable coverage.

Critical error examples:

- `DUPLICATE_BAR`, `MISSING_BARS` for a declared complete grid;
- `UNOBSERVED_TRADE_BAR_INTERVALS` for sparse trade aggregates whose path cannot be reconstructed from available evidence;
- `INCORRECT_SESSION_BOUNDARY`, `TIMEZONE_NOT_UTC`, `BAR_NOT_MINUTE_ALIGNED`;
- `BAR_AVAILABLE_TOO_EARLY`;
- `IMPOSSIBLE_PRICE`, `IMPOSSIBLE_OHLC`, `IMPOSSIBLE_VOLUME`, `CROSSED_QUOTE`;
- `SPLIT_ADJUSTMENT_ERROR`, `FLOAT_EXCEEDS_SHARES`, `FLOAT_DATE_MISSING`;
- `MISSING_SOURCE_URL`.

Coverage warnings include `FLOAT_UNAVAILABLE`, `STALE_FLOAT`, `HISTORICAL_UNIVERSE_INCOMPLETE`, and `QUOTE_COVERAGE_NOT_CONSOLIDATED`.

## Free-source availability and explicit gaps

| Field | Free-source status |
| --- | --- |
| Minute/daily OHLCV | A real Alpaca historical-bars adapter is bundled and requires a legitimate credential. The default IEX feed is single-venue and incomplete. |
| Premarket/regular session | Supported when the provider supplies extended-hours bars and a coverage manifest. |
| Bid/ask | Optional. Free live data may be single-venue rather than consolidated NBBO; feed status is mandatory. |
| Ticker/exchange | Current Nasdaq Trader/SEC mappings are free but are not a complete effective-dated historical master. |
| Sector | Not reliably available as a complete point-in-time field from the selected free regulator/exchange sources. Null until an entitled mapping is configured. |
| Shares outstanding | Can be sourced from SEC filings/XBRL but may be stale, amended, or inconsistently tagged; both fact date and public availability time are required. |
| Free float | No reliable current universal free field has been identified. It remains null unless a documented observation exists. |
| Market capitalisation | Safely derived only when point-in-time shares outstanding are available; otherwise null. |
| Corporate actions | Provider/reference dependent. The schema supports splits, dividends, symbol changes, and delistings without claiming free historical completeness. |
| Trading halts | Nasdaq Trader halt RSS can support a free adapter; the current module contains the normalized contract only. |
| Delisted securities | Current free symbol directories are insufficient for survivorship-safe history. The manifest must say `historical_universe_complete=false` unless proven otherwise. |

## Session-calendar limitation

The zero-dependency calendar implements standard US Eastern DST rules in force since 2007 and standard 04:00–09:30 premarket / 09:30–16:00 regular boundaries. A real adapter must inject authoritative holiday and early-close dates. The defaults must not be interpreted as a complete Nasdaq/NYSE trading calendar.

## Run the offline regression fixture

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
market-data-sample --config config/market_data.sample.json --output-dir output/market_data_sample
```

The sample currently creates 10,790 one-minute bars, 110 daily bars, six identities including one explicitly synthetic delisted identity, two corporate actions, one halt, and 10,790 feature rows. Its output is stamped `ENGINEERING_FIXTURE_NOT_EMPIRICAL_EVIDENCE`.

Expected warnings demonstrate limitations rather than filling them: one active common stock and the synthetic delisted identity have no free-float observation; the fixture is not a complete historical universe; and its bid/ask values are not consolidated NBBO.
