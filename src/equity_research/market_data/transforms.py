"""Corporate-action-aware, provenance-preserving market-data transforms."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from .calendar import UsEquityCalendar
from .contracts import ActionType, Adjustment, Bar, CorporateAction, Session, Timeframe


def aggregate_regular_daily_bars(
    bars: tuple[Bar, ...], calendar: UsEquityCalendar
) -> tuple[Bar, ...]:
    """Aggregate completed regular-session minute bars without adding future data."""

    grouped: dict[tuple[str, object], list[Bar]] = {}
    for bar in bars:
        if bar.timeframe is not Timeframe.ONE_MINUTE or bar.session is not Session.REGULAR:
            continue
        key = (bar.security_id, calendar.local_date(bar.timestamp))
        grouped.setdefault(key, []).append(bar)

    result: list[Bar] = []
    for (security_id, _), values in sorted(grouped.items(), key=lambda item: item[0]):
        ordered = sorted(values, key=lambda value: value.timestamp)
        volume = sum(value.volume for value in ordered)
        weighted = sum(
            (value.vwap if value.vwap is not None else (value.high + value.low + value.close) / 3.0)
            * value.volume
            for value in ordered
        )
        result.append(
            Bar(
                security_id=security_id,
                timestamp=ordered[0].timestamp,
                timeframe=Timeframe.ONE_DAY,
                session=Session.REGULAR,
                open=ordered[0].open,
                high=max(value.high for value in ordered),
                low=min(value.low for value in ordered),
                close=ordered[-1].close,
                volume=volume,
                available_at=max(value.available_at for value in ordered),
                source="derived_from_minute_bars",
                source_url=ordered[0].source_url,
                feed=ordered[0].feed,
                adjustment=ordered[0].adjustment,
                vwap=weighted / volume if volume else None,
                bid=ordered[-1].bid,
                ask=ordered[-1].ask,
                trade_count=sum(value.trade_count or 0 for value in ordered),
            )
        )
    return tuple(result)


def apply_known_split_adjustments(
    bars: tuple[Bar, ...],
    actions: tuple[CorporateAction, ...],
    knowledge_as_of: datetime,
) -> tuple[Bar, ...]:
    """Return a split-adjusted view using only actions knowable by `knowledge_as_of`.

    Raw bars are never mutated.  A split is eligible only after both its
    effective time and source availability time.
    """

    eligible = tuple(
        action
        for action in actions
        if action.action_type is ActionType.SPLIT
        and action.split_ratio is not None
        and action.split_ratio > 0
        and action.effective_at <= knowledge_as_of
        and action.available_at <= knowledge_as_of
    )
    result: list[Bar] = []
    for bar in bars:
        factor = 1.0
        for action in eligible:
            if action.security_id == bar.security_id and bar.timestamp < action.effective_at:
                factor *= action.split_ratio or 1.0
        if factor == 1.0:
            result.append(replace(bar, adjustment=Adjustment.SPLIT_ADJUSTED))
            continue
        result.append(
            replace(
                bar,
                open=bar.open / factor,
                high=bar.high / factor,
                low=bar.low / factor,
                close=bar.close / factor,
                volume=int(round(bar.volume * factor)),
                vwap=bar.vwap / factor if bar.vwap is not None else None,
                bid=bar.bid / factor if bar.bid is not None else None,
                ask=bar.ask / factor if bar.ask is not None else None,
                adjustment=Adjustment.SPLIT_ADJUSTED,
            )
        )
    return tuple(result)

