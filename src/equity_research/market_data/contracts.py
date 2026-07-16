"""Normalized, provider-independent market-data contracts.

The contracts intentionally retain availability and source metadata.  They do
not silently infer float, sector, consolidated quotes, or delisting history.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


class Exchange(str, Enum):
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    OTHER = "OTHER"


class Session(str, Enum):
    PREMARKET = "premarket"
    REGULAR = "regular"
    OUTSIDE = "outside"


class Timeframe(str, Enum):
    ONE_MINUTE = "1m"
    ONE_DAY = "1d"


class Adjustment(str, Enum):
    RAW = "raw"
    SPLIT_ADJUSTED = "split_adjusted"


class ActionType(str, Enum):
    SPLIT = "split"
    CASH_DIVIDEND = "cash_dividend"
    SYMBOL_CHANGE = "symbol_change"
    DELISTING = "delisting"


@dataclass(frozen=True, slots=True)
class Instrument:
    security_id: str
    ticker: str
    exchange: Exchange
    security_type: str
    source: str
    source_url: str
    available_at: datetime
    effective_from: datetime
    effective_to: datetime | None = None
    sector: str | None = None
    sector_available_at: datetime | None = None
    sector_status: str = "unavailable"
    shares_outstanding: int | None = None
    shares_outstanding_as_of: datetime | None = None
    shares_outstanding_available_at: datetime | None = None
    shares_outstanding_status: str = "unavailable"
    free_float: int | None = None
    free_float_as_of: datetime | None = None
    free_float_available_at: datetime | None = None
    free_float_status: str = "not_reliably_available_from_free_sources"
    reported_market_cap: float | None = None
    market_cap_as_of: datetime | None = None
    market_cap_status: str = "derived_only"
    is_delisted: bool = False
    delisted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Bar:
    security_id: str
    timestamp: datetime
    timeframe: Timeframe
    session: Session
    open: float
    high: float
    low: float
    close: float
    volume: int
    available_at: datetime
    source: str
    source_url: str
    feed: str
    adjustment: Adjustment = Adjustment.RAW
    vwap: float | None = None
    bid: float | None = None
    ask: float | None = None
    trade_count: int | None = None


@dataclass(frozen=True, slots=True)
class CorporateAction:
    security_id: str
    action_type: ActionType
    effective_at: datetime
    announced_at: datetime | None
    available_at: datetime
    source: str
    source_url: str
    split_ratio: float | None = None
    cash_amount: float | None = None
    old_ticker: str | None = None
    new_ticker: str | None = None


@dataclass(frozen=True, slots=True)
class Halt:
    security_id: str
    started_at: datetime
    resumed_at: datetime | None
    reason: str
    available_at: datetime
    source: str
    source_url: str


@dataclass(frozen=True, slots=True)
class CoverageManifest:
    provider: str
    dataset_kind: str
    retrieved_at: datetime
    minute_dates: tuple[date, ...]
    included_sessions: tuple[Session, ...]
    expected_security_ids: tuple[str, ...]
    historical_universe_complete: bool
    consolidated_quotes: bool
    sector_classification_available: bool
    free_float_reliability: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderDataset:
    instruments: tuple[Instrument, ...]
    one_minute_bars: tuple[Bar, ...]
    daily_bars: tuple[Bar, ...]
    corporate_actions: tuple[CorporateAction, ...]
    halts: tuple[Halt, ...]
    coverage: CoverageManifest


@dataclass(frozen=True, slots=True)
class FeatureRow:
    security_id: str
    ticker: str
    timestamp: datetime
    available_at: datetime
    session: Session
    gap_pct: float | None
    relative_volume_tod: float | None
    dollar_volume: float
    cumulative_dollar_volume: float
    float_rotation: float | None
    market_cap: float | None
    vwap: float | None
    distance_from_vwap_pct: float | None
    momentum_1m_pct: float | None
    momentum_5m_pct: float | None
    momentum_15m_pct: float | None
    momentum_30m_pct: float | None
    momentum_60m_pct: float | None
    volume_acceleration: float | None
    atr_14: float | None
    realised_volatility_30m_pct: float | None
    sector_relative_return_5m_pct: float | None
    index_relative_return_5m_pct: float | None
    bid: float | None
    ask: float | None
    spread_pct: float | None
    source_feed: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat().replace("+00:00", "Z")
        result["available_at"] = self.available_at.isoformat().replace("+00:00", "Z")
        result["session"] = self.session.value
        return result

