from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta

from equity_research.market_data.calendar import UsEquityCalendar, market_local_to_utc
from equity_research.market_data.contracts import (
    ActionType,
    Bar,
    CorporateAction,
    Session,
    Timeframe,
)
from equity_research.market_data.transforms import (
    aggregate_regular_daily_bars,
    apply_known_split_adjustments,
)


def bar(timestamp: datetime, price: float, volume: int, session: Session) -> Bar:
    return Bar(
        security_id="X",
        timestamp=timestamp,
        timeframe=Timeframe.ONE_MINUTE,
        session=session,
        open=price,
        high=price + 0.1,
        low=price - 0.1,
        close=price + 0.05,
        volume=volume,
        available_at=timestamp + timedelta(minutes=1),
        source="test",
        source_url="fixture://test",
        feed="test",
        vwap=price,
    )


class TransformTests(unittest.TestCase):
    def test_daily_aggregation_uses_regular_session_only(self) -> None:
        session_date = date(2026, 7, 15)
        premarket = bar(
            market_local_to_utc(session_date, time(9, 29)),
            9.0,
            1_000,
            Session.PREMARKET,
        )
        first = bar(
            market_local_to_utc(session_date, time(9, 30)),
            10.0,
            100,
            Session.REGULAR,
        )
        second = bar(
            market_local_to_utc(session_date, time(9, 31)),
            11.0,
            200,
            Session.REGULAR,
        )
        daily = aggregate_regular_daily_bars(
            (premarket, first, second), UsEquityCalendar()
        )[0]
        self.assertEqual(daily.open, first.open)
        self.assertEqual(daily.close, second.close)
        self.assertEqual(daily.volume, 300)

    def test_split_view_uses_only_actions_known_by_cutoff(self) -> None:
        original = bar(
            market_local_to_utc(date(2026, 6, 30), time(9, 30)),
            100.0,
            100,
            Session.REGULAR,
        )
        effective = market_local_to_utc(date(2026, 7, 1), time(9, 30))
        action = CorporateAction(
            security_id="X",
            action_type=ActionType.SPLIT,
            effective_at=effective,
            announced_at=effective - timedelta(days=5),
            available_at=effective - timedelta(days=5),
            source="test",
            source_url="fixture://test",
            split_ratio=2.0,
        )
        before_cutoff = apply_known_split_adjustments(
            (original,), (action,), effective - timedelta(days=6)
        )[0]
        after_cutoff = apply_known_split_adjustments(
            (original,), (action,), effective + timedelta(minutes=1)
        )[0]
        self.assertEqual(before_cutoff.open, original.open)
        self.assertEqual(before_cutoff.volume, original.volume)
        self.assertEqual(after_cutoff.open, original.open / 2.0)
        self.assertEqual(after_cutoff.volume, original.volume * 2)


if __name__ == "__main__":
    unittest.main()
