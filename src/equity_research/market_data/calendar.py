"""US-equity session boundaries with explicit holiday/early-close overrides."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from types import MappingProxyType
from typing import Iterable, Mapping

from .contracts import Session


UTC = timezone.utc


def _first_sunday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def eastern_dst_dates(year: int) -> tuple[date, date]:
    """US Eastern DST dates under the rules in force since 2007."""

    second_sunday_march = _first_sunday(year, 3) + timedelta(days=7)
    first_sunday_november = _first_sunday(year, 11)
    return second_sunday_march, first_sunday_november


def _is_dst_local(session_date: date) -> bool:
    start, end = eastern_dst_dates(session_date.year)
    return start <= session_date < end


def market_local_to_utc(session_date: date, clock: time) -> datetime:
    """Convert an exchange-hours local time to UTC.

    Premarket and regular-session boundaries do not occur in the ambiguous or
    missing 02:00 transition hour, so date-level DST selection is unambiguous.
    """

    offset_hours = -4 if _is_dst_local(session_date) else -5
    fixed_eastern = timezone(timedelta(hours=offset_hours))
    return datetime.combine(session_date, clock, fixed_eastern).astimezone(UTC)


def to_market_local(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    value = timestamp.astimezone(UTC)
    start_date, end_date = eastern_dst_dates(value.year)
    start_utc = datetime.combine(start_date, time(7, 0), UTC)
    end_utc = datetime.combine(end_date, time(6, 0), UTC)
    offset_hours = -4 if start_utc <= value < end_utc else -5
    return value.astimezone(timezone(timedelta(hours=offset_hours)))


@dataclass(frozen=True, slots=True)
class UsEquityCalendar:
    """Session calendar used by normalization and quality checks.

    Exchange holidays and early closes must come from a configured source in a
    real adapter.  Defaults cover standard hours only; they do not claim to be
    a complete exchange calendar.
    """

    holidays: frozenset[date] = frozenset()
    early_closes: Mapping[date, time] = field(
        default_factory=lambda: MappingProxyType({})
    )
    premarket_open: time = time(4, 0)
    regular_open: time = time(9, 30)
    regular_close: time = time(16, 0)

    def is_session_date(self, session_date: date) -> bool:
        return session_date.weekday() < 5 and session_date not in self.holidays

    def bounds(self, session_date: date, session: Session) -> tuple[datetime, datetime]:
        if not self.is_session_date(session_date):
            raise ValueError(f"{session_date} is not configured as a trading date")
        regular_close = self.early_closes.get(session_date, self.regular_close)
        if session is Session.PREMARKET:
            start_time, end_time = self.premarket_open, self.regular_open
        elif session is Session.REGULAR:
            start_time, end_time = self.regular_open, regular_close
        else:
            raise ValueError("OUTSIDE is not an expected trading session")
        return (
            market_local_to_utc(session_date, start_time),
            market_local_to_utc(session_date, end_time),
        )

    def classify(self, timestamp: datetime) -> Session:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return Session.OUTSIDE
        local = to_market_local(timestamp)
        if not self.is_session_date(local.date()):
            return Session.OUTSIDE
        close = self.early_closes.get(local.date(), self.regular_close)
        local_time = local.timetz().replace(tzinfo=None)
        if self.premarket_open <= local_time < self.regular_open:
            return Session.PREMARKET
        if self.regular_open <= local_time < close:
            return Session.REGULAR
        return Session.OUTSIDE

    def expected_bar_starts(
        self, session_date: date, session: Session
    ) -> tuple[datetime, ...]:
        start, end = self.bounds(session_date, session)
        values: list[datetime] = []
        current = start
        while current < end:
            values.append(current)
            current += timedelta(minutes=1)
        return tuple(values)

    @staticmethod
    def local_date(timestamp: datetime) -> date:
        return to_market_local(timestamp).date()


def utc_datetime(value: datetime) -> datetime:
    """Normalize an aware datetime to UTC and fail on ambiguous naive input."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def configured_calendar(
    holidays: Iterable[date] = (), early_closes: Mapping[date, time] | None = None
) -> UsEquityCalendar:
    return UsEquityCalendar(
        holidays=frozenset(holidays),
        early_closes=MappingProxyType(dict(early_closes or {})),
    )
