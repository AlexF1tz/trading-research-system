from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from equity_research.market_data.calendar import UsEquityCalendar, market_local_to_utc
from equity_research.market_data.contracts import (
    Adjustment,
    Bar,
    Exchange,
    Instrument,
    Session,
    Timeframe,
)
from equity_research.market_data.features import FeatureConfig, compute_features


UTC = timezone.utc


def identity(
    security_id: str,
    ticker: str,
    security_type: str,
    sector: str | None = None,
) -> Instrument:
    available = datetime(2026, 1, 1, tzinfo=UTC)
    return Instrument(
        security_id=security_id,
        ticker=ticker,
        exchange=Exchange.NASDAQ if security_id == "X" else Exchange.OTHER,
        security_type=security_type,
        source="test",
        source_url="fixture://test",
        available_at=available,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        sector=sector,
        sector_available_at=available if sector else None,
        sector_status="test" if sector else "unavailable",
        shares_outstanding=2_000 if security_id == "X" else None,
        shares_outstanding_as_of=available if security_id == "X" else None,
        shares_outstanding_available_at=available if security_id == "X" else None,
        free_float=1_000 if security_id == "X" else None,
        free_float_as_of=available if security_id == "X" else None,
        free_float_available_at=available if security_id == "X" else None,
        free_float_status="test" if security_id == "X" else "unavailable",
    )


def intraday(
    security_id: str, timestamp: datetime, price: float, volume: int
) -> Bar:
    return Bar(
        security_id=security_id,
        timestamp=timestamp,
        timeframe=Timeframe.ONE_MINUTE,
        session=Session.REGULAR,
        open=price,
        high=price + 0.1,
        low=price - 0.1,
        close=price,
        volume=volume,
        available_at=timestamp + timedelta(minutes=1),
        source="test",
        source_url="fixture://test",
        feed="test",
        vwap=price,
        bid=price - 0.05,
        ask=price + 0.05,
    )


def daily(security_id: str, session_date: date, close: float) -> Bar:
    timestamp = market_local_to_utc(session_date, time(9, 30))
    return Bar(
        security_id=security_id,
        timestamp=timestamp,
        timeframe=Timeframe.ONE_DAY,
        session=Session.REGULAR,
        open=close,
        high=close + 0.2,
        low=close - 0.2,
        close=close,
        volume=10_000,
        available_at=market_local_to_utc(session_date, time(16, 1)),
        source="test",
        source_url="fixture://test",
        feed="test",
        adjustment=Adjustment.RAW,
    )


class FeatureTests(unittest.TestCase):
    def test_point_in_time_feature_calculations(self) -> None:
        dates = (date(2026, 7, 14), date(2026, 7, 15))
        bars: list[Bar] = []
        for day_index, session_date in enumerate(dates):
            start = market_local_to_utc(session_date, time(9, 30))
            for minute in range(10):
                bars.extend(
                    (
                        intraday(
                            "X",
                            start + timedelta(minutes=minute),
                            10 + day_index + minute * 0.1,
                            100 * (day_index + 1),
                        ),
                        intraday(
                            "S",
                            start + timedelta(minutes=minute),
                            20 + minute * 0.05,
                            500,
                        ),
                        intraday(
                            "I",
                            start + timedelta(minutes=minute),
                            30 + minute * 0.02,
                            1_000,
                        ),
                    )
                )
        daily_bars = tuple(
            daily(security_id, session_date, close)
            for security_id, closes in {
                "X": (9.8, 10.0),
                "S": (19.8, 20.0),
                "I": (29.8, 30.0),
            }.items()
            for session_date, close in zip(
                (date(2026, 7, 13), date(2026, 7, 14)), closes
            )
        )
        calendar = UsEquityCalendar(
            early_closes={value: time(9, 40) for value in dates}
        )
        rows = compute_features(
            tuple(bars),
            daily_bars,
            (
                identity("X", "X", "common_stock", "Technology"),
                identity("S", "S", "sector_benchmark"),
                identity("I", "I", "index_benchmark"),
            ),
            (),
            calendar,
            FeatureConfig(
                atr_period=2,
                realised_volatility_window=2,
                relative_return_window=5,
                index_benchmark_id="I",
                sector_benchmarks={"Technology": "S"},
            ),
        )
        day_two = [
            row
            for row in rows
            if row.security_id == "X"
            and calendar.local_date(row.timestamp) == date(2026, 7, 15)
        ]
        first = day_two[0]
        last = day_two[-1]
        self.assertAlmostEqual(first.gap_pct or 0, 10.0)
        self.assertAlmostEqual(first.relative_volume_tod or 0, 2.0)
        self.assertAlmostEqual(first.dollar_volume, 2_200.0)
        self.assertAlmostEqual(first.float_rotation or 0, 0.2)
        self.assertAlmostEqual(first.market_cap or 0, 22_000.0)
        self.assertAlmostEqual(first.vwap or 0, 11.0)
        self.assertAlmostEqual(
            last.momentum_5m_pct or 0, (11.9 / 11.4 - 1) * 100
        )
        self.assertIsNotNone(last.volume_acceleration)
        self.assertIsNotNone(last.atr_14)
        self.assertIsNotNone(last.realised_volatility_30m_pct)
        self.assertIsNotNone(last.sector_relative_return_5m_pct)
        self.assertIsNotNone(last.index_relative_return_5m_pct)

    def test_future_float_observation_is_not_used(self) -> None:
        session_date = date(2026, 7, 15)
        timestamp = market_local_to_utc(session_date, time(9, 30))
        future_identity = replace(
            identity("X", "X", "common_stock"),
            free_float_available_at=datetime(2026, 7, 20, tzinfo=UTC),
        )
        row = compute_features(
            (intraday("X", timestamp, 10.0, 100),),
            (),
            (future_identity,),
            (),
            UsEquityCalendar(early_closes={session_date: time(9, 31)}),
            FeatureConfig(),
        )[0]
        self.assertIsNone(row.float_rotation)


if __name__ == "__main__":
    unittest.main()

