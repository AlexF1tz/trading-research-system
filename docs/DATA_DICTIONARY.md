# Initial Data Dictionary

This is the Stage 0 logical contract. Physical Arrow/Parquet types and validation models will be fixed in Stage 1. All timestamps are UTC unless explicitly described.

The implemented standard-library market-data slice refines this contract in `src/equity_research/market_data/contracts.py`; see [MARKET_DATA_PIPELINE.md](MARKET_DATA_PIPELINE.md) for physical semantics and current limitations. Parquet remains a later storage adapter rather than an installed dependency.

## Common provenance fields

| Field | Meaning |
| --- | --- |
| `source` | Stable provider/source identifier, not a display label |
| `source_record_id` | Source-native identifier when available |
| `event_at` | Time the represented event occurred |
| `published_at` | Source-declared publication time; nullable |
| `first_seen_at` | Earliest observation by this system |
| `ingested_at` | Persistence time for this version |
| `validated_at` | Time a quality policy was applied; distinct from provider retrieval time |
| `available_at` | Conservative earliest time allowed in features |
| `source_uri` | Canonical link or request identifier with secrets removed |
| `content_hash` | Hash of immutable raw content or normalized source payload |
| `adapter_version` | Version of parsing/normalization logic |
| `license_class` | Configured retention/display category |

### `raw_http_response` and `raw_request_manifest`

The Alpaca Stage 2 adapter stores the exact response bytes by SHA-256 under ignored local data storage. A separate immutable request manifest records provider, licence class, request URL without credentials, market-data adapter version, timeframe, attempt number, HTTP status, retrieval time, response hash/size, and allowlisted request/rate-limit response headers. Credential header names may be recorded; credential values never are. Retry/error responses are preserved as well as successful pages.

### `raw_request_cache_record` and `ingestion_audit`

Each successful cache record stores its schema/adapter version, provider, licence class, exact credential-free request URL, timeframe, original provider-retrieval time, response SHA-256, and repository-confined paths to the content-addressed response and raw manifest. Cache records are immutable; reuse is bounded by configured age and requires all hashes and identities to reconcile. Error responses are never cached.

The ingestion audit records total raw/cache artifact uses, actual network requests, cache hits, accepted pages, raw and normalized minute/daily row counts, explicitly session-filtered minute rows, reconciliation status, and every artifact reference. `cache_hit` is run-specific audit metadata; it never changes the original provider retrieval timestamp.

### `historical_quality_run`

The immutable run manifest stores provider `retrieved_at`, separate `validated_at`, `cache_only`, config and output hashes, network/cache counts, normalized row counts, reconciliation status, quality counts, and explicit flags confirming that no training, prediction, profitability claim, or trading endpoint was used. For sparse trade-derived bars, `UNOBSERVED_TRADE_BAR_INTERVALS` is an error range: it does not assert that the vendor lost data, and it cannot be resolved without eligible-trade, quote, or halt evidence.

## `security_master_scd`

Effective-dated identity table. Ticker is not the primary key.

| Field | Meaning |
| --- | --- |
| `security_id` | Internal stable security identifier |
| `issuer_id` | Internal issuer identifier; CIK-backed when possible |
| `cik` | Ten-digit SEC CIK; nullable |
| `ticker` | Ticker valid during the effective interval |
| `listing_exchange` | Primary listing market as known at that time |
| `security_type` | Common stock, ADR, ETF, warrant, right, etc. |
| `effective_from`, `effective_to` | Half-open validity interval |
| `active_flag` | Source state, not a deletion instruction |
| `status_flags` | Test/deficient/delinquent/bankrupt and similar source flags |

## `market_bar`

| Field | Meaning |
| --- | --- |
| `security_id`, `bar_start`, `bar_end` | Bar identity and interval |
| `open`, `high`, `low`, `close`, `volume`, `vwap`, `trade_count` | Source values; nullable only as documented |
| `feed` | IEX, SIP, delayed SIP, or vendor feed identifier |
| `adjustment` | Raw/split-adjusted/other explicit state |
| `session` | Premarket, regular, after-hours, overnight |
| `available_at` | When the completed bar could be used |

For Alpaca historical bars, provider fields map as `t/o/h/l/c/v/vw/n` to timestamp/OHLC/volume/VWAP/trade count. Minute availability is bar start plus one minute. Daily availability is conservatively 16:01 America/New_York. Retrieval time and raw response hashes remain separate provenance; neither replaces provider event time.

## `market_quote` and `market_trade`

Quotes store bid/ask price and size, quote timestamp, exchange/conditions, feed, and availability. Trades store price, size, venue/conditions, sequence/native ID, feed, and availability. Invalid or non-regular conditions remain present with flags; filters decide eligibility.

## `market_halt`

Stores security, halt start, resumption time, reason/code, source, first-seen time, and whether the timestamp is exact or minute-resolution.

## `source_document`

Stores a filing, issuer release, exchange/regulator announcement, or licensed secondary object: document/event ID, issuer/security links, form/type, headline/title, canonical URL, `published_at`, `first_public_at`, `first_seen_at`, `ingested_at`, conservative `available_at`, source-timestamp verification, source kind, content hash, language, storage mode (`metadata`, `extract`, or `full_text`), and parsing status. Full text is optional when storage rights do not permit it.

## `catalyst_event`

| Field | Meaning |
| --- | --- |
| `catalyst_id` | Stable derived-event ID |
| `document_ids` | Ordered evidence references |
| `security_ids` | Resolved affected securities |
| `labels` | Versioned multi-label catalyst taxonomy |
| `rule_version` | Deterministic classifier version |
| `classification_confidence` | Evidence/classifier confidence, distinct from price-model confidence |
| `evidence` | Field/span/rule references allowed by source licence |
| `available_at` | Maximum availability needed to know this classification |

The implemented catalyst slice additionally records source tier, verification status, direction, related categories, novelty/materiality heuristic scores, numerical details, dilution risk, expected catalyst date, bull/failure cases, stale/repeated/promotional flags, and an immutable `duplicate_of_event_id`.

## `attention_observation`

The implemented retail-attention slice refines this into source declarations, normalized mentions, and ticker signals.

### `attention_source_descriptor`

Stores source label, approved access method, authorization confirmation, terms URL/review time, enforced rate-limit policy, collection coverage interval, text-analysis permission, content-storage class, author-metric permission, and a coverage note. A missing or unconfirmed declaration is a hard error.

### `attention_mention`

| Field | Meaning |
| --- | --- |
| `mention_id`, `source_record_id` | Internal and source-native immutable identifiers |
| `security_id`, `ticker` | Stable identity plus effective display label |
| `source`, `source_url` | Declared platform/source and supporting link |
| `published_at`, `first_seen_at`, `ingested_at`, `available_at` | UTC point-in-time provenance |
| `content_hash` | SHA-256 of supplied analysis text or source content fingerprint |
| `text` | Nullable and permitted only under declared excerpt/full-text rights |
| `author_key` | Nullable source-scoped pseudonymous identity key |
| `is_repost`, `repost_of_source_record_id` | Nullable origin relationship |
| `outbound_urls`, `linked_catalyst_urls` | Preserved links, not confirmation by themselves |
| `engagement_snapshots` | Timestamped likes/replies/reposts/views where permitted |
| `account_quality_score`, `account_quality_basis` | Optional provider-normalized 0–1 indicator and mandatory basis |
| `affiliate_or_paid_promotion` | Nullable explicit source/collector flag |

### `attention_signal`

One row per monitored security and `as_of`, including zero-mention securities. It stores interval counts, raw/current/previous counts, complete-baseline count and mean, adjusted mention score, velocity, acceleration, unique-author and independent-author metrics, engagement velocity, sentiment, account-quality coverage, original/repost ratio, duplicate-language ratio, promotional score, source counts/concentration/diversity, first-observed time, primary catalyst links, supporting links, stage, flags, completeness warnings, and scoring version.

The adjusted score is null when collection coverage cannot support a comparable baseline. `quiet` and a measured zero are distinct from `insufficient_data`.

## `feature_vector`

Stores `feature_set_version`, `security_id`, `prediction_as_of`, strategy/profile, candidate/event ID, named feature values, missingness flags, feature-availability maximum, raw lineage hash, and transform-fit cutoff. A feature row is invalid if any contributing `available_at` exceeds `prediction_as_of`.

## `prediction`

The full contract is in [PREDICTION_SCHEMA.md](PREDICTION_SCHEMA.md). It is append-only and initially has a pending outcome.

## Stage 3 shadow records

- `raw_source_item`: source ID/family/URL, source timestamp, first-seen timestamp, processing timestamp, payload hash, immutable payload, and licence class.
- `market_observation`: stable security ID, ticker label, source/first-seen/processing timestamps, feed, bar completeness, close/volume, bid/ask, consolidated-coverage flag, halt status, point-in-time float, and explicit missing flags.
- `shadow_feature_record`: generated only from complete current and prior observations; records one-minute return, dollar volume, spread, bar float rotation, `as_of`, and `available_at`.
- `research_alert`: research-only catalyst observation with data-quality flags, stale sources, the mandatory Alpaca/IEX empirical-modelling block, null execution recommendation, and false profitability/execution fields.
- `heartbeat`: append-only source-health record with cycle, reconnect attempt, stale sources, counts, mandatory modelling block, and execution disabled.
- `shadow_outcome`: append-only horizon evaluation referencing the alert ID; records return/MFE/MAE or insufficient coverage and always has `used_for_training=false`.

## `prediction_outcome`

| Field | Meaning |
| --- | --- |
| `prediction_id`, `outcome_version`, `labelled_at` | Identity and append time |
| `horizon_complete` | Whether the full configured path is available |
| `reference_fill` | Actual simulated reference price/time/convention |
| `touch_up_05/10/20`, `touch_down_05/10` | Observed path labels |
| `barrier_results` | Target-first/stop-first/ambiguous for each pair |
| `mfe_pct`, `mae_pct` | Realized maximum excursions |
| `continuation_label` | Versioned continuation/reversal/ambiguous result |
| `fill_quality` | Quote/bar fidelity and estimated-cost flags |
| `path_hash` | Hash of inputs used to create the label |

## `model_training_row`

The implemented modelling slice consumes one matured, immutable row per historical prediction opportunity. It stores stable observation/event/security IDs, ticker as of prediction, UTC prediction/feature/outcome availability timestamps, source URL, exact named feature values with nulls preserved, all binary path labels, MFE/MAE, nullable continuation, fill status, gross return, spread/slippage costs, costed net return, data-quality score, label/fill policy versions, and the eight configured breakdown categories.

`features_available_at` must not exceed `prediction_as_of`; `outcome_available_at` must be later than prediction and no later than dataset fetch. A row is invalid when barrier labels contradict MFE/MAE or cost arithmetic does not reconcile.

## `model_evaluation_prediction`

A matured research artifact containing observation/event/security IDs, prediction time, target, model family, value, calibration flag, and `trained_through`. These rows evaluate the final historical period after outcomes are known. They are distinct from immutable live-shadow `prediction` records, which must be written before outcomes mature.

## `validation_report`

The independent validator writes a versioned JSON report containing audit time and scope, overall disposition, blocker IDs, status counts, independently reproduced metric counts and mismatches, per-model cost-stress scenarios, security-concentration diagnostics, structured findings, and one promotion decision per model/target pair. Every finding stores a stable check ID, pass/fail/warning/not-verifiable status, severity, evidence, impact, required action, and automated-test name.

The current report deliberately identifies missing fields that must be added before empirical promotion: immutable raw-response/revision/snapshot lineage, point-in-time candidate-universe and catalyst-discovery manifests, quote/size/participation/latency/fill-fidelity evidence, same-barrier-touch policy, and halt/reopening lineage. Their absence is not represented as a null success state; it is a promotion blocker.

## `journal_entry`

Manual record only: prediction ID, user decision, simulated order/fill notes, intended risk, thesis, timestamps, exit notes, and review tags. It cannot trigger an order and must distinguish user-entered fills from backtest fills.
