# Alpaca Historical-Quality Audit — 17 July 2026

## Scope and conclusion

This audit covers the immutable run `output/alpaca_historical_quality/run-20260717T091500894506Z`. The fetch and normalization succeeded: 24,735 raw minute bars became 24,230 configured regular-session bars plus 505 explicitly excluded extended-hours bars; 123 raw daily bars became 123 normalized daily bars. All counts reconcile. The sample contains three configured instruments and no quote, halt, float, corporate-action, sector, or survivorship-safe universe data.

The original report contains 303 errors and five warnings. All 303 errors are `MISSING_BARS` ranges for JPM. They represent 340 one-minute intervals. AAPL and MSFT each contain the complete 8,190 expected regular-session bars; JPM contains 7,850. The exact 303 range-start timestamps remain in the original immutable `quality_report.json`.

Alpaca documents that stock bars are trade aggregates and that no bar is generated when an interval has no price-forming eligible trade. IEX is a single venue. Therefore an absent IEX bar is not proof that Alpaca lost data. It is still an unobserved price-path interval: bars alone cannot distinguish no eligible trade from coverage loss, a halt, or another omission. These intervals remain hard errors and block modelling.

Primary provider references:

- [Alpaca Market Data FAQ: IEX versus SIP and bar aggregation](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca historical multi-symbol bars](https://docs.alpaca.markets/us/reference/stockbars)

## Grouping by code and ticker

| Original severity/code | Ticker | Error ranges | Unobserved minutes | Classification |
| --- | --- | ---: | ---: | --- |
| Error / `MISSING_BARS` | JPM | 303 | 340 | Genuine IEX/trade-aggregate limitation plus a validator terminology defect; severity remains error |
| Warning / `FLOAT_UNAVAILABLE` | AAPL | 1 | — | Genuine endpoint/source limitation |
| Warning / `FLOAT_UNAVAILABLE` | MSFT | 1 | — | Genuine endpoint/source limitation |
| Warning / `FLOAT_UNAVAILABLE` | JPM | 1 | — | Genuine endpoint/source limitation |
| Warning / `HISTORICAL_UNIVERSE_INCOMPLETE` | Universe | 1 | — | Genuine configured-sample/survivorship limitation |
| Warning / `QUOTE_COVERAGE_NOT_CONSOLIDATED` | Universe | 1 | — | Genuine IEX/bars-only limitation |

No other error code or ticker appears.

## Grouping by timestamp

All error timestamps are unique range starts inside the regular session. No error is outside 13:30–20:00 UTC. The 303 ranges comprise 272 one-minute ranges, 27 two-minute ranges, two three-minute ranges, and two four-minute ranges.

| Session date | Error ranges | Unobserved minutes | First range start UTC | Last range end UTC |
| --- | ---: | ---: | --- | --- |
| 2026-06-01 | 27 | 30 | 13:50 | 17:33 |
| 2026-06-02 | 2 | 2 | 15:41 | 18:57 |
| 2026-06-03 | 49 | 57 | 13:36 | 19:21 |
| 2026-06-04 | 6 | 6 | 13:53 | 16:43 |
| 2026-06-05 | 3 | 3 | 16:09 | 19:07 |
| 2026-06-08 | 31 | 35 | 13:43 | 19:17 |
| 2026-06-09 | 12 | 12 | 13:37 | 18:33 |
| 2026-06-10 | 13 | 14 | 13:50 | 18:52 |
| 2026-06-11 | 27 | 31 | 14:39 | 18:40 |
| 2026-06-12 | 18 | 19 | 13:47 | 18:38 |
| 2026-06-15 | 6 | 7 | 14:40 | 18:46 |
| 2026-06-16 | 5 | 5 | 15:58 | 18:45 |
| 2026-06-18 | 3 | 3 | 15:41 | 17:35 |
| 2026-06-22 | 10 | 10 | 14:44 | 18:36 |
| 2026-06-23 | 30 | 37 | 14:02 | 18:47 |
| 2026-06-24 | 20 | 21 | 14:34 | 18:43 |
| 2026-06-26 | 3 | 3 | 17:07 | 18:53 |
| 2026-06-29 | 10 | 10 | 16:01 | 19:13 |
| 2026-06-30 | 28 | 35 | 13:39 | 19:22 |
| **Total** | **303** | **340** | **13:36 earliest** | **19:22 latest** |

June 17 and June 25 have no error range. Grouped by individual missing-minute UTC hour: 13:00 — 14, 14:00 — 38, 15:00 — 59, 16:00 — 91, 17:00 — 88, 18:00 — 40, and 19:00 — 10.

## Classification and fixes

### Genuine Alpaca/IEX limitations

- IEX is a single venue, so JPM can have intervals with no eligible IEX trade even while the consolidated market trades.
- The bar endpoint emits no bar for an interval without a price-forming eligible trade. The saved bars cannot prove the exact reason for each absence.
- No trades, quotes, halt records, or consolidated SIP bars are present to resolve the 340 intervals.
- Float, NBBO quotes, inactive/delisted securities, and a point-in-time universe remain absent exactly as the five warnings state.

### Session-calendar assessment

No reported issue is a session-calendar defect in this run. All issue timestamps fall within the correct daylight-saving regular-session interval. The 21 declared June sessions explicitly exclude the 19 June holiday, and June 2026 contains no configured early-close date in the sample. The repository calendar is still not authoritative and remains a blocker for broader or differently dated samples.

### Demonstrable validator defects fixed

1. The generic `MISSING_BARS` message asserted a complete interval grid for a provider that documents sparse trade-aggregate emission. Alpaca runs now emit `UNOBSERVED_TRADE_BAR_INTERVALS` with error severity and explicitly state the missing evidence needed to resolve each interval. No error was removed or downgraded.
2. A cached validation unnecessarily required credentials and could fall back to the network. `--cache-only` now skips dotenv/credential access and fails closed before transport if any exact cache object is absent, stale, corrupt, or mismatched.
3. Cached reruns used the provider retrieval timestamp as the run-directory key and therefore collided with the original immutable run. Provider `retrieved_at` is now preserved separately from `validated_at`, which keys each validation run.

## Cached reproduction

The cache-only rerun is `output/alpaca_historical_quality/run-20260717T092624838812Z`. It reports four cache hits, zero network requests, identical normalized bar hashes, 303 `UNOBSERVED_TRADE_BAR_INTERVALS` errors at the same security/timestamps, and the same five warnings. It exited with status 2 as required. It did not train a model, generate predictions, claim profitability, access broker endpoints, or permit network fallback.

## Blockers before modelling or live agents

- Resolve or adopt a predeclared conservative treatment for all 340 unobserved JPM minutes using entitled trade/quote/halt evidence; never forward-fill them silently.
- Obtain consolidated SIP minute bars and preferably NBBO/trade data for fill, spread, and path fidelity, subject to entitlement and licensing confirmation.
- Add authoritative historical exchange holidays, early closes, and halt/LULD records.
- Add point-in-time corporate actions, stable identities, inactive/delisted securities, shares/float, sector, and survivorship-safe universe construction.
- Establish timestamp-correct primary catalyst ingestion before event modelling.
- Keep modelling and live-agent implementation blocked until these inputs pass their own provenance and quality gates.
