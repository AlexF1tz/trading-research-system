from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from equity_research.market_data.calendar import UsEquityCalendar, market_local_to_utc
from equity_research.market_data.contracts import (
    ActionType,
    Adjustment,
    Bar,
    CorporateAction,
    CoverageManifest,
    Exchange,
    Instrument,
    ProviderDataset,
    Session,
    Timeframe,
)
from equity_research.market_data.quality import QualityConfig, run_quality_checks


UTC = timezone.utc
SESSION_DATE = date(2026, 7, 15)


def instrument(**changes: object) -> Instrument:
    value = Instrument(
        security_id="X",
        ticker="X",
        exchange=Exchange.NASDAQ,
        security_type="common_stock",
        source="test",
        source_url="fixture://test",
        available_at=datetime(2026, 1, 1, tzinfo=UTC),
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        shares_outstanding=2_000,
        shares_outstanding_as_of=datetime(2026, 7, 1, tzinfo=UTC),
        shares_outstanding_available_at=datetime(2026, 7, 2, tzinfo=UTC),
        free_float=1_000,
        free_float_as_of=datetime(2026, 7, 1, tzinfo=UTC),
        free_float_available_at=datetime(2026, 7, 2, tzinfo=UTC),
        free_float_status="test",
    )
    return replace(value, **changes)


def minute_bar(timestamp: datetime, **changes: object) -> Bar:
    value = Bar(
        security_id="X",
        timestamp=timestamp,
        timeframe=Timeframe.ONE_MINUTE,
        session=Session.REGULAR,
        open=10.0,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=100,
        available_at=timestamp + timedelta(minutes=1),
        source="test",
        source_url="fixture://test",
        feed="test",
        bid=10.0,
        ask=10.1,
    )
    return replace(value, **changes)


def dataset(
    bars: tuple[Bar, ...],
    *,
    instruments: tuple[Instrument, ...] | None = None,
    daily: tuple[Bar, ...] = (),
    actions: tuple[CorporateAction, ...] = (),
    expected: tuple[str, ...] = (),
    dates: tuple[date, ...] = (),
) -> ProviderDataset:
    return ProviderDataset(
        instruments=instruments or (instrument(),),
        one_minute_bars=bars,
        daily_bars=daily,
        corporate_actions=actions,
        halts=(),
        coverage=CoverageManifest(
            provider="test",
            dataset_kind="test",
            retrieved_at=datetime(2026, 7, 16, tzinfo=UTC),
            minute_dates=dates,
            included_sessions=(Session.REGULAR,),
            expected_security_ids=expected,
            historical_universe_complete=True,
            consolidated_quotes=True,
            sector_classification_available=True,
            free_float_reliability="test",
        ),
    )


class QualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = QualityConfig(datetime(2026, 7, 16, tzinfo=UTC))
        self.open = market_local_to_utc(SESSION_DATE, time(9, 30))

    def codes(
        self, value: ProviderDataset, calendar: UsEquityCalendar | None = None
    ) -> set[str]:
        return {
            issue.code
            for issue in run_quality_checks(
                value, calendar or UsEquityCalendar(), self.config
            )
        }

    def test_duplicate_bars(self) -> None:
        bar = minute_bar(self.open)
        self.assertIn("DUPLICATE_BAR", self.codes(dataset((bar, bar))))

    def test_missing_bar_range(self) -> None:
        calendar = UsEquityCalendar(early_closes={SESSION_DATE: time(9, 33)})
        bars = (minute_bar(self.open), minute_bar(self.open + timedelta(minutes=2)))
        value = dataset(bars, expected=("X",), dates=(SESSION_DATE,))
        self.assertIn("MISSING_BARS", self.codes(value, calendar))

    def test_timezone_price_and_volume_errors(self) -> None:
        non_utc = datetime(
            2026, 7, 15, 9, 30, tzinfo=timezone(timedelta(hours=-4))
        )
        bar = minute_bar(
            non_utc,
            high=9.0,
            low=9.5,
            volume=-1,
            available_at=non_utc + timedelta(minutes=1),
        )
        codes = self.codes(dataset((bar,)))
        self.assertIn("TIMEZONE_NOT_UTC", codes)
        self.assertIn("IMPOSSIBLE_OHLC", codes)
        self.assertIn("IMPOSSIBLE_VOLUME", codes)

    def test_incorrect_session_boundary(self) -> None:
        bar = minute_bar(self.open, session=Session.PREMARKET)
        self.assertIn("INCORRECT_SESSION_BOUNDARY", self.codes(dataset((bar,))))

    def test_early_availability_and_crossed_quote(self) -> None:
        bar = minute_bar(
            self.open,
            available_at=self.open + timedelta(seconds=30),
            bid=10.2,
            ask=10.1,
        )
        codes = self.codes(dataset((bar,)))
        self.assertIn("BAR_AVAILABLE_TOO_EARLY", codes)
        self.assertIn("CROSSED_QUOTE", codes)

    def test_split_adjustment_error(self) -> None:
        prior = Bar(
            security_id="X",
            timestamp=market_local_to_utc(date(2026, 6, 30), time(9, 30)),
            timeframe=Timeframe.ONE_DAY,
            session=Session.REGULAR,
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1_000,
            available_at=market_local_to_utc(date(2026, 6, 30), time(16, 1)),
            source="test",
            source_url="fixture://test",
            feed="test",
            adjustment=Adjustment.SPLIT_ADJUSTED,
        )
        following = replace(
            prior,
            timestamp=market_local_to_utc(date(2026, 7, 1), time(9, 30)),
            available_at=market_local_to_utc(date(2026, 7, 1), time(16, 1)),
            open=50,
            high=51,
            low=49,
            close=50,
        )
        action = CorporateAction(
            security_id="X",
            action_type=ActionType.SPLIT,
            effective_at=following.timestamp,
            announced_at=prior.timestamp,
            available_at=prior.available_at,
            source="test",
            source_url="fixture://test",
            split_ratio=2.0,
        )
        self.assertIn(
            "SPLIT_ADJUSTMENT_ERROR",
            self.codes(dataset((), daily=(prior, following), actions=(action,))),
        )

    def test_stale_float(self) -> None:
        stale = instrument(free_float_as_of=datetime(2025, 1, 1, tzinfo=UTC))
        self.assertIn("STALE_FLOAT", self.codes(dataset((), instruments=(stale,))))

    def test_instrument_timestamp_must_be_utc(self) -> None:
        eastern = timezone(timedelta(hours=-4))
        value = instrument(available_at=datetime(2026, 1, 1, tzinfo=eastern))
        self.assertIn(
            "TIMEZONE_NOT_UTC", self.codes(dataset((), instruments=(value,)))
        )


if __name__ == "__main__":
    unittest.main()
