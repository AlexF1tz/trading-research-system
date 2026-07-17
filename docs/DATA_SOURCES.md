# Data Sources, Cost, Licensing, and Gaps

Reviewed against provider/regulator documentation on 17 July 2026. Prices, entitlements, and terms can change; verify them immediately before subscribing or redistributing anything. This document is an engineering assessment, not legal advice.

Implementation status: market data now includes one credential-gated, GET-only Alpaca historical-bars adapter for a bounded three-stock, one-month real sample. The first credentialed sample was retrieved on 17 July 2026. It loads credentials from the process environment or ignored `.env`, preserves content-addressed raw responses, uses a hash-verified append-only request cache, normalizes minute/daily bars, and runs quality checks without modelling. A cache-only validation path reads no credential file and forbids network fallback. The IEX sample remains blocked by unobserved JPM intervals and all documented quote, halt, float, reference, and universe gaps. Catalyst and retail-attention modules still make no network calls; all modules retain synthetic/offline adapters for tests only. No premium entitlement or provider coverage is assumed.

The retail-attention module follows the same offline boundary. It has an explicit synthetic provider and a strict JSONL directory reader for already-authorized exports, but no live social/trend collector. Schema support for a source is not a representation that official automated access, retention, display, or model-training rights have been obtained.

### Retail-attention source status

| Potential source | Adapter status | Credential/cost assumption | Requirement before enablement |
| --- | --- | --- | --- |
| Reddit | Not implemented | No credential or free tier assumed by this module | Reconfirm current Data API terms, OAuth approval, rate policy, university/team use, retention, deletion, display, and derived-feature rights |
| Stocktwits | Not implemented | Unknown | Confirm an official approved API/export and its current terms; do not scrape |
| Public X posts | Not implemented | Unknown/plan-dependent | Use only current official/approved access with explicit query, retention, and derived-data rights |
| YouTube titles/transcripts | Not implemented | Unknown/plan-dependent | Confirm official API quota and whether each transcript may be retrieved, analyzed, and retained |
| Public TikTok pages | Not implemented | Unknown | Confirm official research/API access and permitted fields; public visibility alone is insufficient |
| Public trading forums | Not implemented | Site-specific | Obtain site-specific automated-access permission or use a lawful manual/licensed export |
| Google Trends or similar | Not implemented | Unknown | Select an approved export/API path and document sampling, normalization, quota, and retention semantics |

Free data currently supports the engineering fixture, permission/quality gates, and normalized manual exports whose collection rights the user has independently confirmed. It does not yet support broad, continuous, multi-platform monitoring or historical attention backtesting.

The quantitative-modelling module adds no external source or credential. Its bundled sample is generated engineering data. A real run depends on the selected market/reference/news sources supplying timestamp-valid historical rows and cannot be called survivorship-safe until inactive/delisted coverage is present. The normalized modelling JSONL adapter does not grant or infer rights to train on vendor data; model-training and derived-data rights must be confirmed in the upstream source contract.

The independent-validation module also adds no external source, credential, or licence. It can reproduce the supplied report and test code-level controls offline. It cannot independently verify vendor announcement clocks, original-versus-revised payloads, universe completeness, halt coverage, quote-derived costs, fills, or social-post completeness until the corresponding immutable source records and lawful access rights are supplied.

## Source matrix

| Need | Candidate source | Current access/cost | Credentials | What it can support | Material limitations/licensing actions |
| --- | --- | --- | --- | --- | --- |
| SEC filings and structured facts | SEC EDGAR submissions, indexes/RSS, XBRL APIs | Free | No API key; declared contact user agent required | Catalyst events, filing text/exhibits, shares outstanding facts, point-in-time filing metadata | Respect SEC fair access; keep below 10 requests/second and cache. Ticker mapping is not guaranteed complete. |
| Current universe/reference | Nasdaq Trader symbol directory plus SEC ticker/CIK file | Free/current snapshot | None | Current listed issues, exchange/category/test/status fields, daily snapshot archive going forward | Current state is not a survivorship-free historical master. Nasdaq pages distinguish non-commercial/internal-use data and events data; review terms before broader display. |
| Halts | Nasdaq Trader trade-halt RSS | Free | None | Nasdaq- and other-exchange-listed halt/pause status and historical date queries | Updates once a minute; do not poll more often. RSS is not a historical tick/LULD feed. |
| Off-exchange short-sale volume | FINRA daily short-sale volume | Free for non-commercial use | None for files; API may have its own access method | Delayed daily aggregate context | It is not short interest, excludes non-publicly disseminated activity, and is not consolidated with exchange short-sale files. |
| Historical/live equities | Alpaca Market Data | Bounded historical adapter implemented; plan pricing/entitlements must be rechecked | `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` in process environment or ignored `.env` | One-month minute bars and covering daily bars from the historical bars endpoint; sample defaults to IEX | IEX is single venue; SIP is entitlement-dependent. No quotes/reference/halts/float/delisting from this adapter. Raw/cache retention and university/team rights must be confirmed; no redistribution. |
| Historical/live equities alternative | Massive Stocks | Personal tiers currently $0, $29, $79, $199/month; business/exchange licensing separate | API key | Aggregates, reference/corporate actions, and at entitled tiers trades, quotes, flat files, snapshots, LULD | Confirm the exact plan for historical depth, real-time trades/NBBO, flat files, and team use. Never infer entitlement from endpoint existence. |
| Broad company news/sentiment | Alpha Vantage | Free majority endpoints at 25 requests/day; premium plans available | API key | Small research samples and news/sentiment prototype | 25/day is insufficient for broad live monitoring. Real-time/delayed US data is premium/personal; commercial use requires sales contact. Verify news-history and storage rights. |
| Low-latency company news | Benzinga APIs | Quote/contact licensing | API token | Real-time REST/TCP news metadata/content at licensed tier | Cost and redistribution/storage rights require written quote/terms. Do not implement against assumed access. |
| Issuer press releases | Allowlisted issuer IR RSS/Atom or pages | Usually no API charge | Usually none | First-party headline/URL/event time for selected issuers | No uniform schema, uptime, history, or licence. Respect per-site terms/robots and avoid full-text redistribution unless permitted. |
| Retail attention | Reddit Data API | Free within published rate limits; separate agreements for some uses | OAuth app credentials | Aggregate mention velocity/source breadth for selected communities | Published free limit is 100 queries/minute per OAuth client. Separate agreement is required for commercial use, excess limits, or uses outside standard terms. Minimize user data. |
| Stocktwits/other social | Not selected | Unknown until official access is confirmed | Likely | Potential attention breadth | No adapter will be promised or scraped without current official API access and terms. |
| Current tradable float/dilution | SEC filings plus optional specialist vendor | SEC component free; robust vendor data usually paid/quoted | Depends | Shares outstanding, annual public-float disclosure, offering/dilution evidence | SEC has no reliable real-time `tradable_float` field. Values may be stale, definition-dependent, and changed by offerings, warrants, or reverse splits. |
| Point-in-time universe/delistings/corporate actions | Market-data/reference vendor | Usually paid or plan-dependent | API key/contract | Survivorship-aware research and symbol/action history | Must verify inactive/delisted coverage and effective timestamps. Current lists alone are inadequate. |
| Historical news and social archive | Specialist provider | Usually paid/contract | API key/contract | Backtesting catalyst/attention features | History, timestamp fidelity, article corrections, storage, derived-data, and model-training rights must be explicit. |

## What can be built with free data

Without any secret credential:

- project schemas, configuration, deterministic engineering fixtures, tests, and local dashboard;
- SEC ingestion and rule-based classification of filing catalysts;
- current symbol/reference snapshots archived from implementation day forward;
- current/historical-date Nasdaq halt RSS ingestion;
- delayed FINRA off-exchange daily short-sale-volume features;
- risk/data-quality logic that abstains when quotes, float, news, or attention are absent;
- an append-only prediction and outcome registry using fixture inputs.

With a free API credential:

- a small real historical minute-bar pipeline using older Alpaca SIP history;
- an IEX-only real-time shadow baseline, clearly labelled as incomplete market coverage;
- limited Alpha Vantage news experiments under the 25-request/day allowance;
- Reddit aggregate attention within OAuth terms and rate limits, if an app is approved.

Free sources are enough to build and verify the engineering system and a narrow baseline. They are not enough for a high-fidelity low-float full-market scanner/backtest with point-in-time float, comprehensive historical news, consolidated live quotes, and unbiased delisting coverage.

## Genuinely required credentials and timing

- **Stage 2:** one market-data key for the real historical end-to-end sample. Alpaca Basic is the proposed minimum.
- **Stage 4:** a live market-data key. Full consolidated SIP entitlement is strongly recommended for competition shadow use; IEX-only outputs must be quality-penalized.
- **Optional after baseline:** news token, Reddit OAuth app, and/or specialist float/reference credentials only when their adapters are selected and their terms accepted.
- **SEC:** no secret credential, but configuration must include a real project/user-agent name and contact email before making requests.

No credential should be committed. `.env.example` will contain names only, and runtime validation will request a key only for the command that needs it.

## Cost scenarios

| Scenario | Monthly data cost before tax | Capability |
| --- | ---: | --- |
| Engineering/free baseline | $0 | SEC/Nasdaq/FINRA plus fixture pipeline; free-key historical sample; incomplete IEX live shadow |
| Consolidated market baseline | Currently about $99 using Alpaca Algo Trader Plus | All-US-exchange live market coverage plus historical access, subject to entitlement confirmation |
| Massive personal path | Currently $29–$199 depending on selected stocks tier | Plan-dependent aggregates/trades/quotes/flat files; exact mapping must be checked before purchase |
| News-enhanced | Quote/premium in addition to market data | Broader and potentially lower-latency company news/history |
| Research-grade reference/archives | Quote | Point-in-time inactive securities, float/short interest, deep news/social history, or business/team rights |

Storage and compute can remain local at $0 incremental service cost for the initial scope. Tick/quote history can become large enough to require additional disk; size must be measured from the selected sample before procurement.

## Stage 3 shadow-source enforcement

The Stage 3 endpoint policy accepts fixture URLs only in synthetic/replay mode. Network market data is restricted to `data.alpaca.markets`, SEC inputs to `sec.gov` subdomains, and news to an explicit configured domain allowlist. Trading, paper-trading, brokerage, account, and order hosts are prohibited. No live network source is enabled by the sample configuration; activating one requires a separately reviewed read-only adapter and applicable credentials/user-agent identification.

## Licensing controls to implement

- Tag every raw object and normalized dataset with provider and licensing class.
- Keep provider-response caches local, content-addressed, hash-verified, bounded by a documented reuse age, and subject to the same retention/redistribution restrictions as raw responses.
- Keep licensed vendor raw data and full news text out of Git and generated reports.
- Make the dashboard local-only until display/redistribution rights are confirmed.
- Store links and derived features instead of article bodies when full-text rights are unclear.
- Record whether use is personal, non-professional, university/non-commercial, team display, or business; providers do not necessarily treat those categories as equivalent.
- Obtain written confirmation before sharing raw/derived vendor data outside the competition team.
- Add per-adapter rate limits, cache rules, attribution, retention, and deletion policies.

## Source documentation

Primary references:

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
- [SEC automated-access rate control](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits)
- [Nasdaq Trader halt RSS](https://www.nasdaqtrader.com/Trader.aspx?id=TradeHaltRSS)
- [Nasdaq Trader symbol definitions](https://nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs)
- [Nasdaq Trader symbol lookup and usage notice](https://nasdaqtrader.com/Trader.aspx?id=symbollookup)
- [FINRA short-sale volume](https://www.finra.org/finra-data/browse-catalog/short-sale-volume)
- [Alpaca market-data plans](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api)
- [Alpaca market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca historical bars reference](https://docs.alpaca.markets/us/v1.4.2/reference/stockbars)
- [Alpaca historical stock feed descriptions](https://docs.alpaca.markets/us/v1.1/docs/historical-stock-data-1)
- [Alpaca customer agreement](https://files.alpaca.markets/disclosures/library/AcctAppMarginAndCustAgmt.pdf)
- [Massive stocks API overview and plan table](https://massive.com/docs/rest/stocks)
- [Massive stocks flat files](https://massive.com/docs/flat-files/stocks/overview)
- [Alpha Vantage premium limits](https://www.alphavantage.co/premium/)
- [Alpha Vantage API documentation](https://www.alphavantage.co/documentation/)
- [Benzinga API introduction/licensing contact](https://docs.benzinga.com/introduction/introduction)
- [Reddit Data API terms](https://redditinc.com/policies/data-api-terms)
- [Reddit published API rate-limit update](https://redditinc.com/news/apifacts)
