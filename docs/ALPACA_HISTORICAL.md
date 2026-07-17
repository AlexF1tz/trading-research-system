# Alpaca Historical Market-Data Adapter

Status: implemented bounded Stage 2 ingestion and quality checking. A credentialed real-data run has not been executed in this repository because no credentials are present. This module is read-only research infrastructure, not a broker integration.

## Read-only boundary

The transport permits HTTP GET requests only to:

```text
https://data.alpaca.markets/v2/stocks/bars
```

It rejects trading/account hosts and every other path before opening a network connection. The repository contains no order submission, account mutation, position management, or broker execution code. Authentication uses `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` from the process environment; credential values are never accepted in JSON config, URLs, raw manifests, normalized output, or logs.

Official provider references:

- [Historical bars endpoint](https://docs.alpaca.markets/us/v1.4.2/reference/stockbars)
- [Market Data API authentication](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api)
- [Historical stock feed descriptions](https://docs.alpaca.markets/us/v1.1/docs/historical-stock-data-1)
- [Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca customer agreement](https://files.alpaca.markets/disclosures/library/AcctAppMarginAndCustAgmt.pdf)

## Bounded sample

`config/alpaca_historical.sample.json` contains three explicitly configured common-stock identities: AAPL and MSFT on Nasdaq, and JPM on NYSE. The minute request is limited to one historical date and the daily request to forty days. Runtime guards allow at most ten symbols, five calendar days of minute requests, ninety calendar days of daily requests, one hundred pages per timeframe, and an end time at least fifteen minutes behind the run clock.

The configured identity rows are not provider reference data and are not a historical listing master. `historical_universe_complete` is always false. Stable IDs, exchange labels, and common-stock type come from reviewed configuration and must not be used to claim delisted/inactive coverage.

The default `iex` feed is the free single-venue path. It represents only IEX-eligible activity and must not be interpreted as consolidated Nasdaq/NYSE price or volume. `sip` is accepted by configuration only when the user's current Alpaca entitlement permits it. The adapter never guesses entitlement or silently falls back between feeds.

## Request and timestamp behavior

Each request specifies symbols, `1Min` or `1Day`, exact RFC-3339 start/end times, feed, `raw` or `split` adjustment, ascending order, USD, page limit, and `asof=-`. Disabling symbol mapping prevents current rename mappings from silently backfilling earlier ticker history; historical symbol changes therefore remain an explicit missing-data limitation.

The adapter follows every `next_page_token` until null and fails on repeated tokens or configured page-limit exhaustion. It applies minimum request spacing and retries HTTP 429/500/502/503/504 responses. `Retry-After` and `X-RateLimit-Reset` take precedence over capped exponential backoff.

Alpaca's provider bar timestamp `t` is preserved as UTC `timestamp`. Minute `available_at` is conservatively the bar start plus one minute. Daily `available_at` is 16:01 America/New_York for the bar's market date; this is intentionally later than an early close rather than earlier. Retrieval timestamps are recorded separately in coverage and raw manifests.

## Raw preservation and normalization

Every HTTP response, including a retryable or terminal error response, is written once under:

```text
data/raw/alpaca/responses/<sha256-prefix>/<response-sha256>.json
data/raw/alpaca/manifests/<request-sha256>.json
```

Response bodies are content-addressed. Per-request manifests contain the URL, adapter version, timeframe, attempt, status, retrieval timestamp, content hash, byte count, and safe rate-limit/request headers. Authentication header names are recorded, but values are not. Existing paths are verified byte-for-byte and never overwritten. Raw provider data remains ignored by Git and must not be redistributed.

The provider maps `t/o/h/l/c/v/vw/n` into the existing `Bar` contract. It retains the exact source request URL, feed, adjustment state, provider timestamp, retrieval provenance, and explicit session classification. Bars outside configured premarket/regular sessions remain preserved in raw data but are counted as intentionally dropped during normalization. Raw count must equal normalized count plus this explicit drop count.

Normalized and audit artifacts are written once to a timestamped directory under `output/alpaca_historical_quality/`. They include instruments, one-minute bars, daily bars, coverage, ingestion audit, quality issues, hashes, and a run manifest.

## Quality-only command

```bash
alpaca-historical-quality \
  --config config/alpaca_historical.sample.json \
  --output-dir output/alpaca_historical_quality \
  --repo-root .
```

Checks include duplicate/missing bars, UTC and session boundaries, impossible OHLCV, daily-bar presence, unknown security IDs, raw/normalized reconciliation, unavailable float, incomplete historical universe, and absent consolidated quotes. A nonzero quality-error count returns exit status `2` after artifacts are saved. The command sets `training_performed=false`, `predictions_generated=false`, and `profitability_claimed=false` in every successful run manifest.

## Known gaps

The bars endpoint does not provide bid/ask quotes, quote size, trading halts, corporate actions as separate timestamped events, sector, shares outstanding, free float, market capitalization, delisting history, or a survivorship-safe universe. Raw bars may later be corrected by the provider; content-addressed retrievals preserve what this system received, but a single retrieval cannot prove the original publication version. These limitations block Stage 3 empirical promotion.

Before retaining or sharing data, the user must review the current Alpaca and exchange terms for non-professional, university/team, storage, display, derived-data, and redistribution rights. This documentation is an engineering control, not legal advice.
