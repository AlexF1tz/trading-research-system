"""Deterministic engineering fixture; never use as empirical market evidence."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import sin
from .calendar import UsEquityCalendar, market_local_to_utc
from .contracts import (
    ActionType,
    Adjustment,
    Bar,
    CorporateAction,
    CoverageManifest,
    Exchange,
    Halt,
    Instrument,
    ProviderDataset,
    Session,
    Timeframe,
)


UTC = timezone.utc
FIXTURE_SOURCE = "synthetic_fixture"
FIXTURE_URL = "fixture://market-data/v1"


def _local(session_date: date, clock: time) -> datetime:
    return market_local_to_utc(session_date, clock)


def _business_dates(start: date, end: date, holidays: frozenset[date]) -> list[date]:
    values: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            values.append(current)
        current += timedelta(days=1)
    return values


class SyntheticFixtureProvider:
    """Generate a complete, timestamp-correct fixture with known limitations."""

    @property
    def name(self) -> str:
        return FIXTURE_SOURCE

    def load(self) -> ProviderDataset:
        holidays = frozenset({date(2026, 7, 3)})
        calendar = UsEquityCalendar(holidays=holidays)
        retrieved_at = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
        metadata_available = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
        float_available = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

        instruments = (
            Instrument(
                security_id="DEMO.NQ",
                ticker="DMNQ",
                exchange=Exchange.NASDAQ,
                security_type="common_stock",
                source=FIXTURE_SOURCE,
                source_url=FIXTURE_URL,
                available_at=metadata_available,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                sector="Technology",
                sector_available_at=metadata_available,
                sector_status="synthetic_fixture_only",
                shares_outstanding=10_000_000,
                shares_outstanding_as_of=datetime(2026, 6, 30, tzinfo=UTC),
                shares_outstanding_available_at=float_available,
                shares_outstanding_status="synthetic_fixture_only",
                free_float=4_000_000,
                free_float_as_of=datetime(2026, 6, 30, tzinfo=UTC),
                free_float_available_at=float_available,
                free_float_status="synthetic_fixture_only",
            ),
            Instrument(
                security_id="DEMO.NY",
                ticker="DMNY",
                exchange=Exchange.NYSE,
                security_type="common_stock",
                source=FIXTURE_SOURCE,
                source_url=FIXTURE_URL,
                available_at=metadata_available,
                effective_from=datetime(2019, 1, 1, tzinfo=UTC),
                sector="Healthcare",
                sector_available_at=metadata_available,
                sector_status="synthetic_fixture_only",
                shares_outstanding=20_000_000,
                shares_outstanding_as_of=datetime(2026, 6, 30, tzinfo=UTC),
                shares_outstanding_available_at=float_available,
                shares_outstanding_status="synthetic_fixture_only",
                free_float=None,
                free_float_status="not_reliably_available_from_free_sources",
            ),
            Instrument(
                security_id="SECTOR.TECH",
                ticker="TECH-DEMO",
                exchange=Exchange.OTHER,
                security_type="sector_benchmark",
                source=FIXTURE_SOURCE,
                source_url=FIXTURE_URL,
                available_at=metadata_available,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            ),
            Instrument(
                security_id="SECTOR.HEALTH",
                ticker="HEALTH-DEMO",
                exchange=Exchange.OTHER,
                security_type="sector_benchmark",
                source=FIXTURE_SOURCE,
                source_url=FIXTURE_URL,
                available_at=metadata_available,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            ),
            Instrument(
                security_id="INDEX.SPY",
                ticker="INDEX-DEMO",
                exchange=Exchange.OTHER,
                security_type="index_benchmark",
                source=FIXTURE_SOURCE,
                source_url=FIXTURE_URL,
                available_at=metadata_available,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            ),
            Instrument(
                security_id="OLDCO.NQ",
                ticker="OLDQ",
                exchange=Exchange.NASDAQ,
                security_type="common_stock",
                source=FIXTURE_SOURCE,
                source_url=FIXTURE_URL,
                available_at=metadata_available,
                effective_from=datetime(2018, 1, 1, tzinfo=UTC),
                effective_to=datetime(2025, 12, 1, 14, 30, tzinfo=UTC),
                is_delisted=True,
                delisted_at=datetime(2025, 12, 1, 14, 30, tzinfo=UTC),
            ),
        )

        split_effective = _local(date(2026, 7, 1), time(9, 30))
        corporate_actions = (
            CorporateAction(
                security_id="DEMO.NY",
                action_type=ActionType.SPLIT,
                effective_at=split_effective,
                announced_at=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
                available_at=datetime(2026, 6, 24, 12, 0, 5, tzinfo=UTC),
                source=FIXTURE_SOURCE,
                source_url=FIXTURE_URL,
                split_ratio=2.0,
            ),
            CorporateAction(
                security_id="OLDCO.NQ",
                action_type=ActionType.DELISTING,
                effective_at=datetime(2025, 12, 1, 14, 30, tzinfo=UTC),
                announced_at=datetime(2025, 11, 28, 21, 0, tzinfo=UTC),
                available_at=datetime(2025, 11, 28, 21, 1, tzinfo=UTC),
                source=FIXTURE_SOURCE,
                source_url=FIXTURE_URL,
            ),
        )

        halt = Halt(
            security_id="DEMO.NQ",
            started_at=_local(date(2026, 7, 15), time(10, 0)),
            resumed_at=_local(date(2026, 7, 15), time(10, 10)),
            reason="synthetic volatility pause",
            available_at=_local(date(2026, 7, 15), time(10, 0)) + timedelta(seconds=30),
            source=FIXTURE_SOURCE,
            source_url=FIXTURE_URL,
        )
        halts = (halt,)

        daily_dates = _business_dates(date(2026, 6, 15), date(2026, 7, 15), holidays)
        daily_bars = self._daily_bars(daily_dates)
        minute_dates = (date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15))
        minute_security_ids = (
            "DEMO.NQ",
            "DEMO.NY",
            "SECTOR.TECH",
            "SECTOR.HEALTH",
            "INDEX.SPY",
        )
        minute_bars = self._minute_bars(
            calendar, minute_dates, minute_security_ids, halts
        )

        coverage = CoverageManifest(
            provider=FIXTURE_SOURCE,
            dataset_kind="synthetic_engineering_fixture_not_market_evidence",
            retrieved_at=retrieved_at,
            minute_dates=minute_dates,
            included_sessions=(Session.PREMARKET, Session.REGULAR),
            expected_security_ids=minute_security_ids,
            historical_universe_complete=False,
            consolidated_quotes=False,
            sector_classification_available=False,
            free_float_reliability="synthetic_only; real free float unavailable",
            notes=(
                "All values are deterministic synthetic fixtures.",
                "OLDCO.NQ demonstrates a delisted identity but is not real market history.",
                "Bid/ask values test the schema and do not represent consolidated NBBO.",
            ),
        )
        return ProviderDataset(
            instruments=instruments,
            one_minute_bars=minute_bars,
            daily_bars=daily_bars,
            corporate_actions=corporate_actions,
            halts=halts,
            coverage=coverage,
        )

    def _daily_bars(self, dates: list[date]) -> tuple[Bar, ...]:
        configs = {
            "DEMO.NQ": (9.0, 0.07, 80_000),
            "DEMO.NY": (80.0, 0.10, 120_000),
            "SECTOR.TECH": (200.0, 0.20, 500_000),
            "SECTOR.HEALTH": (150.0, 0.12, 450_000),
            "INDEX.SPY": (600.0, 0.25, 2_000_000),
        }
        bars: list[Bar] = []
        for day_index, session_date in enumerate(dates):
            for security_id, (base, drift, volume) in configs.items():
                price_base = base + day_index * drift
                if security_id == "DEMO.NY" and session_date >= date(2026, 7, 1):
                    price_base = (base + day_index * drift) / 2.0
                open_price = price_base
                close_price = price_base * (1.0 + ((day_index % 5) - 2) * 0.001)
                high = max(open_price, close_price) * 1.006
                low = min(open_price, close_price) * 0.994
                timestamp = _local(session_date, time(9, 30))
                bars.append(
                    Bar(
                        security_id=security_id,
                        timestamp=timestamp,
                        timeframe=Timeframe.ONE_DAY,
                        session=Session.REGULAR,
                        open=round(open_price, 6),
                        high=round(high, 6),
                        low=round(low, 6),
                        close=round(close_price, 6),
                        volume=volume + day_index * 1_000,
                        available_at=_local(session_date, time(16, 1)),
                        source=FIXTURE_SOURCE,
                        source_url=FIXTURE_URL,
                        feed="synthetic_daily",
                        adjustment=Adjustment.RAW,
                        vwap=round((open_price + high + low + close_price) / 4.0, 6),
                    )
                )
        return tuple(bars)

    def _minute_bars(
        self,
        calendar: UsEquityCalendar,
        dates: tuple[date, ...],
        security_ids: tuple[str, ...],
        halts: tuple[Halt, ...],
    ) -> tuple[Bar, ...]:
        base_prices = {
            "DEMO.NQ": 10.0,
            "DEMO.NY": 41.0,
            "SECTOR.TECH": 204.0,
            "SECTOR.HEALTH": 152.0,
            "INDEX.SPY": 604.0,
        }
        base_volumes = {
            "DEMO.NQ": 2_000,
            "DEMO.NY": 3_000,
            "SECTOR.TECH": 8_000,
            "SECTOR.HEALTH": 7_000,
            "INDEX.SPY": 20_000,
        }
        bars: list[Bar] = []
        for day_index, session_date in enumerate(dates):
            ordered_starts = (
                calendar.expected_bar_starts(session_date, Session.PREMARKET)
                + calendar.expected_bar_starts(session_date, Session.REGULAR)
            )
            for security_rank, security_id in enumerate(security_ids):
                for minute_index, timestamp in enumerate(ordered_starts):
                    if any(
                        halt.security_id == security_id
                        and halt.started_at <= timestamp
                        and (halt.resumed_at is None or timestamp < halt.resumed_at)
                        for halt in halts
                    ):
                        continue
                    session = calendar.classify(timestamp)
                    day_impulse = day_index * (0.12 if security_id == "DEMO.NQ" else 0.04)
                    intraday_drift = minute_index * (0.0008 + security_rank * 0.00005)
                    oscillation = sin((minute_index + security_rank * 3) / 17.0) * 0.004
                    open_price = base_prices[security_id] + day_impulse + intraday_drift + oscillation
                    close_price = open_price + 0.002 + security_rank * 0.0002
                    high = max(open_price, close_price) + 0.006
                    low = min(open_price, close_price) - 0.006
                    profile_boost = 2_000 if minute_index < 15 else 0
                    activity_multiplier = 2 if day_index == 2 and security_id == "DEMO.NQ" else 1
                    volume = (
                        base_volumes[security_id]
                        + profile_boost
                        + ((minute_index * 37 + day_index * 113) % 300)
                    ) * activity_multiplier
                    midpoint = (open_price + close_price) / 2.0
                    spread = max(0.01, midpoint * (0.0015 if security_id.startswith("DEMO") else 0.0002))
                    bars.append(
                        Bar(
                            security_id=security_id,
                            timestamp=timestamp,
                            timeframe=Timeframe.ONE_MINUTE,
                            session=session,
                            open=round(open_price, 6),
                            high=round(high, 6),
                            low=round(low, 6),
                            close=round(close_price, 6),
                            volume=volume,
                            available_at=timestamp + timedelta(minutes=1, seconds=2),
                            source=FIXTURE_SOURCE,
                            source_url=FIXTURE_URL,
                            feed="synthetic_non_consolidated",
                            adjustment=Adjustment.RAW,
                            vwap=round((high + low + close_price) / 3.0, 6),
                            bid=round(midpoint - spread / 2.0, 6),
                            ask=round(midpoint + spread / 2.0, 6),
                            trade_count=max(1, volume // 100),
                        )
                    )
        return tuple(bars)
