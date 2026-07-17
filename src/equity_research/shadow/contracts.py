"""Immutable contracts for Stage 3 shadow collection and research alerts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from equity_research.catalyst_intelligence.contracts import SourceBatch


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("shadow timestamps must be timezone-aware")
    return value.isoformat().replace("+00:00", "Z")


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        json_safe(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return json_safe(value.to_dict())  # type: ignore[union-attr]
    if hasattr(value, "__dataclass_fields__"):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    return value


class MonitorMode(str, Enum):
    SYNTHETIC = "synthetic"
    REPLAY = "replay"
    LIVE = "live"


class SourceFamily(str, Enum):
    MARKET_DATA = "market_data"
    SEC = "sec"
    APPROVED_NEWS = "approved_news"


class HeartbeatStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


class AlertStatus(str, Enum):
    RESEARCH_ONLY = "research_only"
    BLOCKED_DATA = "blocked_data"


@dataclass(frozen=True, slots=True)
class RawSourceItem:
    source_id: str
    source_family: SourceFamily
    source_url: str
    source_timestamp: datetime
    first_seen_at: datetime
    processing_timestamp: datetime
    payload: dict[str, object]
    license_class: str
    provider_received_at: datetime | None = None

    @property
    def content_sha256(self) -> str:
        return canonical_hash(self.payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_family": self.source_family.value,
            "source_url": self.source_url,
            "source_timestamp": utc_text(self.source_timestamp),
            "first_seen_at": utc_text(self.first_seen_at),
            "processing_timestamp": utc_text(self.processing_timestamp),
            "provider_received_at": utc_text(self.provider_received_at) if self.provider_received_at else None,
            "payload_sha256": self.content_sha256,
            "payload": json_safe(self.payload),
            "license_class": self.license_class,
        }


@dataclass(frozen=True, slots=True)
class MarketObservation:
    observation_id: str
    security_id: str
    ticker: str
    source_url: str
    source_timestamp: datetime
    first_seen_at: datetime
    processing_timestamp: datetime
    feed: str
    bar_complete: bool
    close: float | None
    volume: int | None
    bid: float | None
    ask: float | None
    consolidated_coverage: bool
    halt_status: str | None
    free_float: int | None
    missing_flags: tuple[str, ...] = ()
    provider_received_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for name in (
            "source_timestamp",
            "first_seen_at",
            "processing_timestamp",
        ):
            result[name] = utc_text(getattr(self, name))
        result["missing_flags"] = list(self.missing_flags)
        result["provider_received_at"] = utc_text(self.provider_received_at) if self.provider_received_at else None
        return result


@dataclass(frozen=True, slots=True)
class ShadowInputBatch:
    provider: str
    mode: MonitorMode
    fetched_at: datetime
    raw_items: tuple[RawSourceItem, ...]
    market_observations: tuple[MarketObservation, ...]
    catalyst_batch: SourceBatch
    source_watermarks: tuple[tuple[SourceFamily, datetime], ...]


@dataclass(frozen=True, slots=True)
class ShadowFeatureRecord:
    feature_id: str
    security_id: str
    ticker: str
    as_of: datetime
    available_at: datetime
    return_1m_pct: float
    dollar_volume: float
    spread_pct: float
    float_rotation_bar: float
    feature_version: str = "shadow-market-features-v1"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["as_of"] = utc_text(self.as_of)
        result["available_at"] = utc_text(self.available_at)
        return result


@dataclass(frozen=True, slots=True)
class ResearchAlert:
    alert_id: str
    created_at: datetime
    alert_as_of: datetime
    horizon_end: datetime
    security_id: str
    ticker: str
    catalyst_event_id: str
    catalyst_category: str
    catalyst_source_url: str
    status: AlertStatus
    feature_id: str | None
    reference_price: float | None
    data_quality_flags: tuple[str, ...]
    stale_sources: tuple[str, ...]
    empirical_modelling_blocked: bool
    empirical_modelling_block_reason: str
    research_summary: str
    execution_recommendation: None = None
    profitability_claimed: bool = False
    order_execution_enabled: bool = False
    schema_version: str = "shadow-research-alert-v1"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for name in ("created_at", "alert_as_of", "horizon_end"):
            result[name] = utc_text(getattr(self, name))
        result["status"] = self.status.value
        result["data_quality_flags"] = list(self.data_quality_flags)
        result["stale_sources"] = list(self.stale_sources)
        return result


@dataclass(frozen=True, slots=True)
class ShadowOutcome:
    outcome_id: str
    alert_id: str
    evaluated_at: datetime
    horizon_end: datetime
    status: str
    return_pct: float | None
    maximum_favourable_excursion_pct: float | None
    maximum_adverse_excursion_pct: float | None
    observation_count: int
    used_for_training: bool = False
    schema_version: str = "shadow-outcome-v1"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["evaluated_at"] = utc_text(self.evaluated_at)
        result["horizon_end"] = utc_text(self.horizon_end)
        return result


@dataclass(frozen=True, slots=True)
class Heartbeat:
    heartbeat_id: str
    cycle_number: int
    processed_at: datetime
    provider: str
    mode: MonitorMode
    status: HeartbeatStatus
    stale_sources: tuple[str, ...]
    reconnect_attempt: int
    raw_items_seen: int
    market_observations_seen: int
    catalyst_documents_seen: int
    alerts_written: int
    outcomes_written: int
    empirical_modelling_blocked: bool = True
    order_execution_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["processed_at"] = utc_text(self.processed_at)
        result["mode"] = self.mode.value
        result["status"] = self.status.value
        result["stale_sources"] = list(self.stale_sources)
        return result
