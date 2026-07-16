from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone

from equity_research.market_data.calendar import (
    UsEquityCalendar,
    eastern_dst_dates,
    market_local_to_utc,
)
from equity_research.market_data.contracts import Session


class CalendarTests(unittest.TestCase):
    def test_2026_dst_dates(self) -> None:
        self.assertEqual(
            eastern_dst_dates(2026), (date(2026, 3, 8), date(2026, 11, 1))
        )

    def test_regular_open_changes_utc_across_dst(self) -> None:
        self.assertEqual(
            market_local_to_utc(date(2026, 1, 15), time(9, 30)),
            datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            market_local_to_utc(date(2026, 7, 15), time(9, 30)),
            datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc),
        )

    def test_session_boundaries_are_half_open(self) -> None:
        calendar = UsEquityCalendar()
        self.assertEqual(
            calendar.classify(
                datetime(2026, 7, 15, 13, 29, tzinfo=timezone.utc)
            ),
            Session.PREMARKET,
        )
        self.assertEqual(
            calendar.classify(
                datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc)
            ),
            Session.REGULAR,
        )
        self.assertEqual(
            calendar.classify(datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)),
            Session.OUTSIDE,
        )


if __name__ == "__main__":
    unittest.main()

