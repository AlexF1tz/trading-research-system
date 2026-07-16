# Prediction Schema

The prediction record is written atomically before any configured outcome is known. It is immutable; labels are appended to `prediction_outcome`. JSONL is the first interchange format and Parquet is the analytical mirror.

## Core fields

| Group | Fields |
| --- | --- |
| Contract | `schema_version`, `prediction_id`, `created_at`, `prediction_as_of`, `outcome_status="pending"` |
| Identity | `security_id`, `issuer_id`, `cik`, `ticker_asof`, `listing_exchange` |
| Context | `strategy_profile`, `candidate_id`, `catalyst_id`, `catalyst_labels`, `source_event_ids`, `session` |
| Horizon | `horizon_id`, `horizon_end`, `label_policy_version`, `continuation_definition` |
| Reference | `reference_price`, `reference_price_at`, `reference_price_source`, `feed`, `entry_delay`, `entry_convention` |
| Model | `model_family`, `model_id`, `model_version`, `trained_through`, `calibrator_id`, `feature_set_version` |
| Lineage | `config_hash`, `feature_hash`, `raw_watermarks`, `universe_version`, `code_version` |
| Eligibility | `decision_support_status`, `risk_flags`, `liquidity_flags`, `abstention_reasons` |

## Required estimates

| Field | Range/units | Meaning |
| --- | --- | --- |
| `p_touch_up_05` | `[0,1]` | P(path touches +5% from reference within horizon) |
| `p_touch_up_10` | `[0,1]` | P(path touches +10%) |
| `p_touch_up_20` | `[0,1]` | P(path touches +20%) |
| `p_touch_down_05` | `[0,1]` | P(path touches -5%) |
| `p_touch_down_10` | `[0,1]` | P(path touches -10%) |
| `barrier_probabilities` | array | Configured `{target_pct, stop_pct, p_target_before_stop}` entries |
| `expected_mfe_pct` | percentage points | Training-estimated expected maximum favourable excursion |
| `expected_mae_pct` | percentage points, normally non-positive | Training-estimated expected maximum adverse excursion |
| `p_continuation` | `[0,1]` | P(versioned continuation label) |
| `p_reversal` | `[0,1]` | P(versioned reversal label) |
| `confidence_score` | `[0,100]` | Model-support/stability diagnostic, not a probability |
| `data_quality_score` | `[0,100]` | Timeliness/completeness/feed/identity diagnostic |

Touch probabilities need not sum to one. `p_continuation + p_reversal` sums to one only where the versioned label excludes an ambiguous class; otherwise explicit `p_ambiguous` is included.

## Barrier pairs

The initial configurable pairs are proposed as +5% before -5%, +10% before -5%, and +20% before -10%. The schema uses an array so research cannot silently substitute one stop definition for all targets.

## Abstention

A pending prediction record is still written when the system abstains. In that case:

- `decision_support_status` is `ABSTAIN` or `BLOCKED`;
- probability fields may be null only with a schema-valid reason;
- `abstention_reasons` lists machine-readable codes such as `STALE_MARKET_DATA`, `UNRESOLVED_SECURITY`, `HALTED`, `INSUFFICIENT_LIQUIDITY`, `FEED_COVERAGE_LOW`, or `MODEL_OUT_OF_DOMAIN`;
- the record remains available for coverage and operational-failure evaluation.

## Example shape

Illustrative values below demonstrate the contract only. They are not a forecast or a result.

```json
{
  "schema_version": "0.1.0",
  "prediction_id": "01900000-0000-7000-8000-000000000001",
  "created_at": "2026-07-20T13:35:01Z",
  "prediction_as_of": "2026-07-20T13:35:00Z",
  "outcome_status": "pending",
  "security_id": "example-security",
  "ticker_asof": "DEMO",
  "strategy_profile": "low_float_intraday",
  "horizon_id": "session_close",
  "horizon_end": "2026-07-20T20:00:00Z",
  "reference_price": 10.0,
  "reference_price_at": "2026-07-20T13:35:00Z",
  "reference_price_source": "illustrative_fixture",
  "feed": "fixture",
  "p_touch_up_05": 0.4,
  "p_touch_up_10": 0.2,
  "p_touch_up_20": 0.05,
  "p_touch_down_05": 0.3,
  "p_touch_down_10": 0.1,
  "barrier_probabilities": [
    {"target_pct": 5, "stop_pct": -5, "p_target_before_stop": 0.55}
  ],
  "expected_mfe_pct": 4.1,
  "expected_mae_pct": -2.8,
  "p_continuation": 0.58,
  "p_reversal": 0.42,
  "confidence_score": 60,
  "data_quality_score": 100,
  "decision_support_status": "WATCH",
  "risk_flags": [],
  "liquidity_flags": [],
  "abstention_reasons": [],
  "model_family": "illustrative_fixture",
  "trained_through": "2026-06-30T20:00:00Z",
  "feature_set_version": "0.1.0",
  "config_hash": "illustrative",
  "feature_hash": "illustrative"
}
```

Stage 1 validation will additionally enforce nested monotonic sanity checks where appropriate: for the same horizon and reference path, estimated +20% touch probability should not exceed +10%, which should not exceed +5%, unless the modelling representation is repaired or rejected. The analogous constraint applies to -10% versus -5%.

