"""Point-in-time market features computed without future observations."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import log, sqrt
from statistics import fmean
from types import MappingProxyType
from typing import Mapping

from .calendar import UsEquityCalendar
from .contracts import (
    ActionType,
    Bar,
    CorporateAction,
    FeatureRow,
    Instrument,
    Session,
    Timeframe,
)


MOMENTUM_WINDOWS = (1, 5, 15, 30, 60)


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    atr_period: int = 14
    realised_volatility_window: int = 30
    relative_return_window: int = 5
    index_benchmark_id: str = "INDEX.SPY"
    sector_benchmarks: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType(
            {"Technology": "SECTOR.TECH", "Healthcare": "SECTOR.HEALTH"}
        )
    )


def _group_intraday(
    bars: tuple[Bar, ...], calendar: UsEquityCalendar
) -> dict[tuple[str, date], list[Bar]]:
    grouped: dict[tuple[str, date], list[Bar]] = defaultdict(list)
    for bar in bars:
        if bar.timeframe is Timeframe.ONE_MINUTE:
            grouped[(bar.security_id, calendar.local_date(bar.timestamp))].append(bar)
    for values in grouped.values():
        values.sort(key=lambda value: value.timestamp)
    return grouped


def _momentum_lookup(
    grouped: dict[tuple[str, date], list[Bar]],
) -> dict[tuple[str, datetime, int], float | None]:
    result: dict[tuple[str, datetime, int], float | None] = {}
    for (security_id, _), values in grouped.items():
        prior_times: list[datetime] = []
        prior_closes: list[float] = []
        for bar in values:
            for window in MOMENTUM_WINDOWS:
                target = bar.timestamp - timedelta(minutes=window)
                index = bisect_right(prior_times, target) - 1
                result[(security_id, bar.timestamp, window)] = (
                    (bar.close / prior_closes[index] - 1.0) * 100.0
                    if index >= 0
                    else None
                )
            prior_times.append(bar.timestamp)
            prior_closes.append(bar.close)
    return result


def _split_factor_between(
    security_id: str,
    prior_timestamp: datetime,
    current_timestamp: datetime,
    as_of: datetime,
    actions: tuple[CorporateAction, ...],
) -> float:
    factor = 1.0
    for action in actions:
        if (
            action.security_id == security_id
            and action.action_type is ActionType.SPLIT
            and action.split_ratio is not None
            and prior_timestamp < action.effective_at <= current_timestamp
            and action.available_at <= as_of
        ):
            factor *= action.split_ratio
    return factor


def _daily_context(
    daily_bars: tuple[Bar, ...],
    actions: tuple[CorporateAction, ...],
    calendar: UsEquityCalendar,
) -> tuple[
    dict[str, list[Bar]],
    dict[str, list[datetime]],
    dict[str, list[float]],
]:
    by_security: dict[str, list[Bar]] = defaultdict(list)
    for bar in daily_bars:
        by_security[bar.security_id].append(bar)
    availability: dict[str, list[datetime]] = {}
    true_ranges: dict[str, list[float]] = {}
    for security_id, values in by_security.items():
        values.sort(key=lambda value: value.timestamp)
        availability[security_id] = [value.available_at for value in values]
        ranges: list[float] = []
        prior: Bar | None = None
        for value in values:
            if prior is None:
                comparable_prior_close = value.close
            else:
                split_factor = _split_factor_between(
                    security_id,
                    prior.timestamp,
                    value.timestamp,
                    value.available_at,
                    actions,
                )
                comparable_prior_close = prior.close / split_factor
            ranges.append(
                max(
                    value.high - value.low,
                    abs(value.high - comparable_prior_close),
                    abs(value.low - comparable_prior_close),
                )
            )
            prior = value
        true_ranges[security_id] = ranges
    return by_security, availability, true_ranges


def _atr_as_of(
    security_id: str,
    as_of: datetime,
    availability: dict[str, list[datetime]],
    true_ranges: dict[str, list[float]],
    period: int,
) -> float | None:
    times = availability.get(security_id, [])
    index = bisect_right(times, as_of) - 1
    if index + 1 < period:
        return None
    values = true_ranges[security_id][index - period + 1 : index + 1]
    return fmean(values)


def _previous_daily_bar(
    security_id: str,
    session_date: date,
    as_of: datetime,
    daily_by_security: dict[str, list[Bar]],
    calendar: UsEquityCalendar,
) -> Bar | None:
    eligible = [
        bar
        for bar in daily_by_security.get(security_id, [])
        if calendar.local_date(bar.timestamp) < session_date and bar.available_at <= as_of
    ]
    return eligible[-1] if eligible else None


def _volume_acceleration(values: list[Bar]) -> float | None:
    if len(values) < 10:
        return None
    recent = values[-10:]
    if any(
        right.timestamp - left.timestamp != timedelta(minutes=1)
        for left, right in zip(recent, recent[1:])
    ):
        return None
    previous_sum = sum(value.volume for value in recent[:5])
    latest_sum = sum(value.volume for value in recent[5:])
    return latest_sum / previous_sum - 1.0 if previous_sum else None


def _realised_volatility(
    returns: list[float], window: int
) -> float | None:
    if len(returns) < window:
        return None
    return sqrt(sum(value * value for value in returns[-window:])) * 100.0


def compute_features(
    one_minute_bars: tuple[Bar, ...],
    daily_bars: tuple[Bar, ...],
    instruments: tuple[Instrument, ...],
    actions: tuple[CorporateAction, ...],
    calendar: UsEquityCalendar,
    config: FeatureConfig,
) -> tuple[FeatureRow, ...]:
    """Compute point-in-time features for every one-minute bar.

    Relative volume is current cumulative session volume divided by the mean
    cumulative volume at the same session minute on prior fixture/provider
    dates only.  Float rotation uses cumulative day volume and is null unless a
    float observation was available by the bar's availability timestamp.
    """

    instrument_by_id = {value.security_id: value for value in instruments}
    grouped = _group_intraday(one_minute_bars, calendar)
    momentum = _momentum_lookup(grouped)
    daily_by_security, daily_availability, true_ranges = _daily_context(
        daily_bars, actions, calendar
    )
    regular_opens: dict[tuple[str, date], float] = {}
    for key, values in grouped.items():
        regular = [value for value in values if value.session is Session.REGULAR]
        if regular:
            regular_opens[key] = regular[0].open

    rows: list[FeatureRow] = []
    by_security_dates: dict[str, list[date]] = defaultdict(list)
    for security_id, session_date in grouped:
        by_security_dates[security_id].append(session_date)

    for security_id, dates in by_security_dates.items():
        instrument = instrument_by_id.get(security_id)
        if instrument is None:
            raise ValueError(f"no instrument identity for {security_id}")
        prior_volume_profiles: dict[tuple[Session, int], list[int]] = defaultdict(list)
        for session_date in sorted(set(dates)):
            values = grouped[(security_id, session_date)]
            session_cumulative_volume: dict[Session, int] = defaultdict(int)
            session_cumulative_notional: dict[Session, float] = defaultdict(float)
            session_cumulative_vwap_notional: dict[Session, float] = defaultdict(float)
            day_cumulative_volume = 0
            day_cumulative_notional = 0.0
            profile_for_day: dict[tuple[Session, int], int] = {}
            bars_in_session: dict[Session, list[Bar]] = defaultdict(list)
            realised_returns: list[float] = []
            previous_bar: Bar | None = None

            for bar in values:
                if instrument.available_at > bar.available_at:
                    raise ValueError(
                        f"instrument identity for {security_id} was unavailable at {bar.available_at}"
                    )
                session_start, _ = calendar.bounds(session_date, bar.session)
                minute_offset = int((bar.timestamp - session_start).total_seconds() // 60)
                session_cumulative_volume[bar.session] += bar.volume
                day_cumulative_volume += bar.volume
                bar_dollar_volume = bar.close * bar.volume
                session_cumulative_notional[bar.session] += bar_dollar_volume
                day_cumulative_notional += bar_dollar_volume
                bar_vwap = (
                    bar.vwap
                    if bar.vwap is not None
                    else (bar.high + bar.low + bar.close) / 3.0
                )
                session_cumulative_vwap_notional[bar.session] += bar_vwap * bar.volume
                cumulative_vwap = (
                    session_cumulative_vwap_notional[bar.session]
                    / session_cumulative_volume[bar.session]
                    if session_cumulative_volume[bar.session]
                    else None
                )
                profile_key = (bar.session, minute_offset)
                prior_profile_values = prior_volume_profiles.get(profile_key, [])
                relative_volume = (
                    session_cumulative_volume[bar.session] / fmean(prior_profile_values)
                    if prior_profile_values and fmean(prior_profile_values) > 0
                    else None
                )
                profile_for_day[profile_key] = session_cumulative_volume[bar.session]

                prior_daily = _previous_daily_bar(
                    security_id,
                    session_date,
                    bar.available_at,
                    daily_by_security,
                    calendar,
                )
                comparable_prior_close: float | None = None
                if prior_daily is not None:
                    factor = _split_factor_between(
                        security_id,
                        prior_daily.timestamp,
                        bar.timestamp,
                        bar.available_at,
                        actions,
                    )
                    comparable_prior_close = prior_daily.close / factor
                if comparable_prior_close is None:
                    gap_pct = None
                elif bar.session is Session.PREMARKET:
                    gap_pct = (bar.close / comparable_prior_close - 1.0) * 100.0
                else:
                    gap_pct = (
                        regular_opens[(security_id, session_date)]
                        / comparable_prior_close
                        - 1.0
                    ) * 100.0

                free_float = (
                    instrument.free_float
                    if instrument.free_float is not None
                    and instrument.free_float_available_at is not None
                    and instrument.free_float_available_at <= bar.available_at
                    else None
                )
                shares = (
                    instrument.shares_outstanding
                    if instrument.shares_outstanding is not None
                    and instrument.shares_outstanding_available_at is not None
                    and instrument.shares_outstanding_available_at <= bar.available_at
                    else None
                )

                bars_in_session[bar.session].append(bar)
                volume_acceleration = _volume_acceleration(bars_in_session[bar.session])
                if (
                    previous_bar is not None
                    and bar.timestamp - previous_bar.timestamp == timedelta(minutes=1)
                ):
                    realised_returns.append(log(bar.close / previous_bar.close))
                else:
                    realised_returns = []
                previous_bar = bar

                momentum_values = {
                    window: momentum[(security_id, bar.timestamp, window)]
                    for window in MOMENTUM_WINDOWS
                }
                sector_relative: float | None = None
                if (
                    instrument.sector is not None
                    and instrument.sector_available_at is not None
                    and instrument.sector_available_at <= bar.available_at
                ):
                    sector_id = config.sector_benchmarks.get(instrument.sector)
                    benchmark_return = (
                        momentum.get(
                            (sector_id, bar.timestamp, config.relative_return_window)
                        )
                        if sector_id is not None
                        else None
                    )
                    own_return = momentum_values[config.relative_return_window]
                    if own_return is not None and benchmark_return is not None:
                        sector_relative = own_return - benchmark_return
                index_return = momentum.get(
                    (
                        config.index_benchmark_id,
                        bar.timestamp,
                        config.relative_return_window,
                    )
                )
                own_relative_return = momentum_values[config.relative_return_window]
                index_relative = (
                    own_relative_return - index_return
                    if own_relative_return is not None and index_return is not None
                    else None
                )
                midpoint = (
                    (bar.bid + bar.ask) / 2.0
                    if bar.bid is not None and bar.ask is not None
                    else None
                )
                spread_pct = (
                    (bar.ask - bar.bid) / midpoint * 100.0
                    if midpoint is not None and midpoint > 0
                    else None
                )
                rows.append(
                    FeatureRow(
                        security_id=security_id,
                        ticker=instrument.ticker,
                        timestamp=bar.timestamp,
                        available_at=bar.available_at,
                        session=bar.session,
                        gap_pct=gap_pct,
                        relative_volume_tod=relative_volume,
                        dollar_volume=bar_dollar_volume,
                        cumulative_dollar_volume=day_cumulative_notional,
                        float_rotation=(
                            day_cumulative_volume / free_float
                            if free_float is not None and free_float > 0
                            else None
                        ),
                        market_cap=bar.close * shares if shares is not None else None,
                        vwap=cumulative_vwap,
                        distance_from_vwap_pct=(
                            (bar.close / cumulative_vwap - 1.0) * 100.0
                            if cumulative_vwap is not None and cumulative_vwap > 0
                            else None
                        ),
                        momentum_1m_pct=momentum_values[1],
                        momentum_5m_pct=momentum_values[5],
                        momentum_15m_pct=momentum_values[15],
                        momentum_30m_pct=momentum_values[30],
                        momentum_60m_pct=momentum_values[60],
                        volume_acceleration=volume_acceleration,
                        atr_14=_atr_as_of(
                            security_id,
                            bar.available_at,
                            daily_availability,
                            true_ranges,
                            config.atr_period,
                        ),
                        realised_volatility_30m_pct=_realised_volatility(
                            realised_returns, config.realised_volatility_window
                        ),
                        sector_relative_return_5m_pct=sector_relative,
                        index_relative_return_5m_pct=index_relative,
                        bid=bar.bid,
                        ask=bar.ask,
                        spread_pct=spread_pct,
                        source_feed=bar.feed,
                    )
                )

            for key, cumulative_volume in profile_for_day.items():
                prior_volume_profiles[key].append(cumulative_volume)

    rows.sort(key=lambda value: (value.timestamp, value.security_id))
    return tuple(rows)
