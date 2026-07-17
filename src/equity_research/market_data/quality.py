"""Data-quality checks for timestamped US-equity market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import isfinite

from .calendar import UsEquityCalendar
from .contracts import ActionType, Adjustment, Bar, ProviderDataset, Timeframe


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class BarGapPolicy(str, Enum):
    COMPLETE_INTERVAL_GRID = "complete_interval_grid"
    TRADE_AGGREGATE = "trade_aggregate"


@dataclass(frozen=True, slots=True)
class QualityIssue:
    code: str
    severity: Severity
    message: str
    security_id: str | None = None
    timestamp: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "security_id": self.security_id,
            "timestamp": (
                self.timestamp.isoformat().replace("+00:00", "Z")
                if self.timestamp is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class QualityConfig:
    run_as_of: datetime
    max_float_age_days: int = 120
    split_tolerance: float = 0.12
    bar_gap_policy: BarGapPolicy = BarGapPolicy.COMPLETE_INTERVAL_GRID


class DataQualityError(RuntimeError):
    def __init__(self, issues: tuple[QualityIssue, ...]) -> None:
        self.issues = issues
        super().__init__(f"market-data quality failed with {len(issues)} error(s)")


def _is_utc(value: datetime) -> bool:
    return (
        value.tzinfo is not None
        and value.utcoffset() is not None
        and value.utcoffset() == timedelta(0)
    )


def _is_halted(dataset: ProviderDataset, security_id: str, timestamp: datetime) -> bool:
    return any(
        halt.security_id == security_id
        and halt.started_at <= timestamp
        and (halt.resumed_at is None or timestamp < halt.resumed_at)
        for halt in dataset.halts
    )


def _missing_ranges(values: list[datetime]) -> list[tuple[datetime, datetime]]:
    if not values:
        return []
    ordered = sorted(values)
    result: list[tuple[datetime, datetime]] = []
    start = previous = ordered[0]
    for current in ordered[1:]:
        if current - previous != timedelta(minutes=1):
            result.append((start, previous))
            start = current
        previous = current
    result.append((start, previous))
    return result


def run_quality_checks(
    dataset: ProviderDataset,
    calendar: UsEquityCalendar,
    config: QualityConfig,
) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []
    all_bars = dataset.one_minute_bars + dataset.daily_bars

    if not _is_utc(dataset.coverage.retrieved_at):
        issues.append(
            QualityIssue(
                "TIMEZONE_NOT_UTC",
                Severity.ERROR,
                "coverage retrieved_at must be timezone-aware UTC",
            )
        )
    for instrument in dataset.instruments:
        for field_name, timestamp in (
            ("available_at", instrument.available_at),
            ("effective_from", instrument.effective_from),
            ("effective_to", instrument.effective_to),
            ("sector_available_at", instrument.sector_available_at),
            ("shares_outstanding_as_of", instrument.shares_outstanding_as_of),
            (
                "shares_outstanding_available_at",
                instrument.shares_outstanding_available_at,
            ),
            ("free_float_as_of", instrument.free_float_as_of),
            ("free_float_available_at", instrument.free_float_available_at),
            ("market_cap_as_of", instrument.market_cap_as_of),
            ("delisted_at", instrument.delisted_at),
        ):
            if timestamp is not None and not _is_utc(timestamp):
                issues.append(
                    QualityIssue(
                        "TIMEZONE_NOT_UTC",
                        Severity.ERROR,
                        f"instrument {field_name} must be timezone-aware UTC",
                        instrument.security_id,
                        timestamp,
                    )
                )
        if not instrument.source_url:
            issues.append(
                QualityIssue(
                    "MISSING_SOURCE_URL",
                    Severity.ERROR,
                    "instrument has no source URL or source identifier",
                    instrument.security_id,
                )
            )

    known_security_ids = {instrument.security_id for instrument in dataset.instruments}
    for bar in all_bars:
        if bar.security_id not in known_security_ids:
            issues.append(
                QualityIssue(
                    "BAR_SECURITY_UNKNOWN",
                    Severity.ERROR,
                    "bar references a security absent from the normalized instrument set",
                    bar.security_id,
                    bar.timestamp,
                )
            )

    for action in dataset.corporate_actions:
        for field_name, timestamp in (
            ("effective_at", action.effective_at),
            ("announced_at", action.announced_at),
            ("available_at", action.available_at),
        ):
            if timestamp is not None and not _is_utc(timestamp):
                issues.append(
                    QualityIssue(
                        "TIMEZONE_NOT_UTC",
                        Severity.ERROR,
                        f"corporate action {field_name} must be timezone-aware UTC",
                        action.security_id,
                        timestamp,
                    )
                )
        if not action.source_url:
            issues.append(
                QualityIssue(
                    "MISSING_SOURCE_URL",
                    Severity.ERROR,
                    "corporate action has no source URL or source identifier",
                    action.security_id,
                    action.effective_at,
                )
            )

    for halt in dataset.halts:
        for field_name, timestamp in (
            ("started_at", halt.started_at),
            ("resumed_at", halt.resumed_at),
            ("available_at", halt.available_at),
        ):
            if timestamp is not None and not _is_utc(timestamp):
                issues.append(
                    QualityIssue(
                        "TIMEZONE_NOT_UTC",
                        Severity.ERROR,
                        f"halt {field_name} must be timezone-aware UTC",
                        halt.security_id,
                        timestamp,
                    )
                )
        if halt.resumed_at is not None and halt.resumed_at < halt.started_at:
            issues.append(
                QualityIssue(
                    "INVALID_HALT_INTERVAL",
                    Severity.ERROR,
                    "halt resumed before it started",
                    halt.security_id,
                    halt.started_at,
                )
            )
        if not halt.source_url:
            issues.append(
                QualityIssue(
                    "MISSING_SOURCE_URL",
                    Severity.ERROR,
                    "halt has no source URL or source identifier",
                    halt.security_id,
                    halt.started_at,
                )
            )

    seen: set[tuple[str, Timeframe, datetime]] = set()
    for bar in all_bars:
        key = (bar.security_id, bar.timeframe, bar.timestamp)
        if key in seen:
            issues.append(
                QualityIssue(
                    "DUPLICATE_BAR",
                    Severity.ERROR,
                    f"duplicate {bar.timeframe.value} bar",
                    bar.security_id,
                    bar.timestamp,
                )
            )
        seen.add(key)

        for field_name, timestamp in (
            ("timestamp", bar.timestamp),
            ("available_at", bar.available_at),
        ):
            if not _is_utc(timestamp):
                issues.append(
                    QualityIssue(
                        "TIMEZONE_NOT_UTC",
                        Severity.ERROR,
                        f"{field_name} must be timezone-aware UTC",
                        bar.security_id,
                        bar.timestamp,
                    )
                )
        if bar.timestamp.second != 0 or bar.timestamp.microsecond != 0:
            issues.append(
                QualityIssue(
                    "BAR_NOT_MINUTE_ALIGNED",
                    Severity.ERROR,
                    "bar start is not minute-aligned",
                    bar.security_id,
                    bar.timestamp,
                )
            )

        if bar.timeframe is Timeframe.ONE_MINUTE:
            expected_session = calendar.classify(bar.timestamp)
            if expected_session is not bar.session:
                issues.append(
                    QualityIssue(
                        "INCORRECT_SESSION_BOUNDARY",
                        Severity.ERROR,
                        f"declared {bar.session.value}; calendar classified {expected_session.value}",
                        bar.security_id,
                        bar.timestamp,
                    )
                )
            if bar.available_at < bar.timestamp + timedelta(minutes=1):
                issues.append(
                    QualityIssue(
                        "BAR_AVAILABLE_TOO_EARLY",
                        Severity.ERROR,
                        "completed one-minute bar was available before its end",
                        bar.security_id,
                        bar.timestamp,
                    )
                )

        prices = (bar.open, bar.high, bar.low, bar.close)
        if not all(isfinite(value) and value > 0 for value in prices):
            issues.append(
                QualityIssue(
                    "IMPOSSIBLE_PRICE",
                    Severity.ERROR,
                    "OHLC prices must be finite and positive",
                    bar.security_id,
                    bar.timestamp,
                )
            )
        elif bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(
            bar.open, bar.close, bar.high
        ):
            issues.append(
                QualityIssue(
                    "IMPOSSIBLE_OHLC",
                    Severity.ERROR,
                    "high/low do not contain open and close",
                    bar.security_id,
                    bar.timestamp,
                )
            )
        if bar.volume < 0:
            issues.append(
                QualityIssue(
                    "IMPOSSIBLE_VOLUME",
                    Severity.ERROR,
                    "volume cannot be negative",
                    bar.security_id,
                    bar.timestamp,
                )
            )
        if bar.bid is not None and (not isfinite(bar.bid) or bar.bid <= 0):
            issues.append(
                QualityIssue(
                    "IMPOSSIBLE_BID",
                    Severity.ERROR,
                    "bid must be finite and positive",
                    bar.security_id,
                    bar.timestamp,
                )
            )
        if bar.ask is not None and (not isfinite(bar.ask) or bar.ask <= 0):
            issues.append(
                QualityIssue(
                    "IMPOSSIBLE_ASK",
                    Severity.ERROR,
                    "ask must be finite and positive",
                    bar.security_id,
                    bar.timestamp,
                )
            )
        if bar.bid is not None and bar.ask is not None and bar.ask < bar.bid:
            issues.append(
                QualityIssue(
                    "CROSSED_QUOTE",
                    Severity.ERROR,
                    "ask is below bid",
                    bar.security_id,
                    bar.timestamp,
                )
            )
        if not bar.source_url:
            issues.append(
                QualityIssue(
                    "MISSING_SOURCE_URL",
                    Severity.ERROR,
                    "bar has no source URL or source identifier",
                    bar.security_id,
                    bar.timestamp,
                )
            )

    actual = {
        (bar.security_id, bar.session, bar.timestamp)
        for bar in dataset.one_minute_bars
    }
    for security_id in dataset.coverage.expected_security_ids:
        for session_date in dataset.coverage.minute_dates:
            for session in dataset.coverage.included_sessions:
                missing = [
                    timestamp
                    for timestamp in calendar.expected_bar_starts(session_date, session)
                    if (security_id, session, timestamp) not in actual
                    and not _is_halted(dataset, security_id, timestamp)
                ]
                for start, end in _missing_ranges(missing):
                    if config.bar_gap_policy is BarGapPolicy.TRADE_AGGREGATE:
                        code = "UNOBSERVED_TRADE_BAR_INTERVALS"
                        message = (
                            f"trade-aggregate feed emitted no {session.value} bar "
                            f"from {start.isoformat()} through {end.isoformat()}; "
                            "without eligible-trade, quote, or halt evidence the "
                            "interval remains unobserved"
                        )
                    else:
                        code = "MISSING_BARS"
                        message = (
                            f"missing expected {session.value} bars from "
                            f"{start.isoformat()} through {end.isoformat()}"
                        )
                    issues.append(
                        QualityIssue(
                            code,
                            Severity.ERROR,
                            message,
                            security_id,
                            start,
                        )
                    )

    daily_by_security: dict[str, list[Bar]] = {}
    for bar in dataset.daily_bars:
        daily_by_security.setdefault(bar.security_id, []).append(bar)
    for security_id in dataset.coverage.expected_security_ids:
        if not daily_by_security.get(security_id):
            issues.append(
                QualityIssue(
                    "MISSING_DAILY_BARS",
                    Severity.ERROR,
                    "expected security has no daily bars in the requested range",
                    security_id,
                )
            )
    for action in dataset.corporate_actions:
        if action.action_type is not ActionType.SPLIT or not action.split_ratio:
            continue
        ordered = sorted(daily_by_security.get(action.security_id, []), key=lambda bar: bar.timestamp)
        before = [bar for bar in ordered if bar.timestamp < action.effective_at]
        after = [bar for bar in ordered if bar.timestamp >= action.effective_at]
        if not before or not after:
            continue
        prior, following = before[-1], after[0]
        observed_ratio = following.open / prior.close
        raw_error = abs(observed_ratio - 1.0 / action.split_ratio)
        adjusted_error = abs(observed_ratio - 1.0)
        if following.adjustment is Adjustment.RAW and adjusted_error + config.split_tolerance < raw_error:
            issues.append(
                QualityIssue(
                    "SPLIT_ADJUSTMENT_ERROR",
                    Severity.ERROR,
                    "bar is declared raw but behaves split-adjusted",
                    action.security_id,
                    following.timestamp,
                )
            )
        if (
            following.adjustment is Adjustment.SPLIT_ADJUSTED
            and raw_error + config.split_tolerance < adjusted_error
        ):
            issues.append(
                QualityIssue(
                    "SPLIT_ADJUSTMENT_ERROR",
                    Severity.ERROR,
                    "bar is declared split-adjusted but retains a raw split discontinuity",
                    action.security_id,
                    following.timestamp,
                )
            )

    for instrument in dataset.instruments:
        if instrument.security_type != "common_stock":
            continue
        if instrument.free_float is None:
            issues.append(
                QualityIssue(
                    "FLOAT_UNAVAILABLE",
                    Severity.WARNING,
                    instrument.free_float_status,
                    instrument.security_id,
                )
            )
        elif instrument.free_float_as_of is None:
            issues.append(
                QualityIssue(
                    "FLOAT_DATE_MISSING",
                    Severity.ERROR,
                    "free float has no as-of timestamp",
                    instrument.security_id,
                )
            )
        elif _is_utc(instrument.free_float_as_of) and (
            config.run_as_of - instrument.free_float_as_of
            > timedelta(days=config.max_float_age_days)
        ):
            issues.append(
                QualityIssue(
                    "STALE_FLOAT",
                    Severity.WARNING,
                    f"free float is older than {config.max_float_age_days} days",
                    instrument.security_id,
                    instrument.free_float_as_of,
                )
            )
        if (
            instrument.free_float is not None
            and instrument.shares_outstanding is not None
            and instrument.free_float > instrument.shares_outstanding
        ):
            issues.append(
                QualityIssue(
                    "FLOAT_EXCEEDS_SHARES",
                    Severity.ERROR,
                    "free float exceeds shares outstanding",
                    instrument.security_id,
                )
            )

    if not dataset.coverage.historical_universe_complete:
        issues.append(
            QualityIssue(
                "HISTORICAL_UNIVERSE_INCOMPLETE",
                Severity.WARNING,
                "provider cannot support survivorship-bias-safe universe claims",
            )
        )
    if not dataset.coverage.consolidated_quotes:
        issues.append(
            QualityIssue(
                "QUOTE_COVERAGE_NOT_CONSOLIDATED",
                Severity.WARNING,
                "bid/ask fields are not confirmed consolidated NBBO",
            )
        )
    return tuple(issues)
