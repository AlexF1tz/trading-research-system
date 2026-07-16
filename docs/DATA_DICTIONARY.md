# Initial Data Dictionary

This is the Stage 0 logical contract. Physical Arrow/Parquet types and validation models will be fixed in Stage 1. All timestamps are UTC unless explicitly described.

## Common provenance fields

| Field | Meaning |
| --- | --- |
| `source` | Stable provider/source identifier, not a display label |
| `source_record_id` | Source-native identifier when available |
| `event_at` | Time the represented event occurred |
| `published_at` | Source-declared publication time; nullable |
| `first_seen_at` | Earliest observation by this system |
| `ingested_at` | Persistence time for this version |
| `available_at` | Conservative earliest time allowed in features |
| `source_uri` | Canonical link or request identifier with secrets removed |
| `content_hash` | Hash of immutable raw content or normalized source payload |
| `adapter_version` | Version of parsing/normalization logic |
| `license_class` | Configured retention/display category |

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

## `market_quote` and `market_trade`

Quotes store bid/ask price and size, quote timestamp, exchange/conditions, feed, and availability. Trades store price, size, venue/conditions, sequence/native ID, feed, and availability. Invalid or non-regular conditions remain present with flags; filters decide eligibility.

## `market_halt`

Stores security, halt start, resumption time, reason/code, source, first-seen time, and whether the timestamp is exact or minute-resolution.

## `source_document`

Stores a filing, issuer release, or licensed news/social object: document/event ID, issuer/security links, form/type, headline/title, canonical URL, published/first-seen/available times, content hash, language, source reliability, storage mode (`metadata`, `extract`, or `full_text`), and parsing status.

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

## `attention_observation`

Aggregated by source, security, and fixed window. Stores mention count, prior-window count, velocity, source breadth, unique-author count only when permitted, aggregate sentiment, collection coverage, availability, and quality flags. It does not require user-level text or identities.

## `feature_vector`

Stores `feature_set_version`, `security_id`, `prediction_as_of`, strategy/profile, candidate/event ID, named feature values, missingness flags, feature-availability maximum, raw lineage hash, and transform-fit cutoff. A feature row is invalid if any contributing `available_at` exceeds `prediction_as_of`.

## `prediction`

The full contract is in [PREDICTION_SCHEMA.md](PREDICTION_SCHEMA.md). It is append-only and initially has a pending outcome.

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

## `journal_entry`

Manual record only: prediction ID, user decision, simulated order/fill notes, intended risk, thesis, timestamps, exit notes, and review tags. It cannot trigger an order and must distinguish user-entered fills from backtest fills.

