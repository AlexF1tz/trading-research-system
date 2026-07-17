from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock

from equity_research.market_data.alpaca import (
    ALPACA_BARS_URL,
    AlpacaConfigurationError,
    AlpacaCredentials,
    AlpacaHistoricalConfig,
    AlpacaHistoricalProvider,
    ConfiguredSecurity,
    HttpResponse,
    UrllibReadOnlyTransport,
)
from equity_research.market_data.contracts import Exchange, Session
from equity_research.market_data.historical_quality import (
    main,
    run_historical_quality_check,
)


UTC = timezone.utc
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, url: str, headers, timeout_seconds: float) -> HttpResponse:
        self.calls.append((url, dict(headers), timeout_seconds))
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        return self.responses.pop(0)


def response(payload: object, status: int = 200, **headers: str) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers=headers,
        body=json.dumps(payload, sort_keys=True).encode("utf-8"),
    )


def bar(timestamp: str, close: float = 100.5) -> dict[str, object]:
    return {
        "t": timestamp,
        "o": 100.0,
        "h": 101.0,
        "l": 99.5,
        "c": close,
        "v": 1000,
        "n": 50,
        "vw": 100.4,
    }


def config(raw_root: Path) -> AlpacaHistoricalConfig:
    return AlpacaHistoricalConfig(
        universe=(
            ConfiguredSecurity("US:AAPL", "AAPL", Exchange.NASDAQ),
            ConfiguredSecurity("US:JPM", "JPM", Exchange.NYSE),
        ),
        minute_start=datetime(2026, 7, 15, 13, 30, tzinfo=UTC),
        minute_end=datetime(2026, 7, 15, 13, 32, tzinfo=UTC),
        daily_start=datetime(2026, 7, 1, tzinfo=UTC),
        daily_end=datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
        minute_session_dates=(date(2026, 7, 15),),
        included_sessions=(Session.REGULAR,),
        minimum_request_interval_seconds=0.0,
        raw_root=raw_root,
    )


def paginated_transport() -> FakeTransport:
    return FakeTransport(
        [
            response(
                {
                    "bars": {"AAPL": [bar("2026-07-15T13:30:00Z")]},
                    "next_page_token": "next-minute-page",
                },
                **{"X-RateLimit-Remaining": "199"},
            ),
            response(
                {
                    "bars": {
                        "AAPL": [bar("2026-07-15T13:31:00Z", 101.0)],
                        "JPM": [
                            bar("2026-07-15T13:30:00Z", 200.0),
                            bar("2026-07-15T13:31:00Z", 201.0),
                        ],
                    },
                    "next_page_token": None,
                }
            ),
            response(
                {
                    "bars": {
                        "AAPL": [bar("2026-07-15T04:00:00Z", 102.0)],
                        "JPM": [bar("2026-07-15T04:00:00Z", 202.0)],
                    },
                    "next_page_token": None,
                }
            ),
        ]
    )


class AlpacaHistoricalTests(unittest.TestCase):
    def test_credentials_are_environment_only_and_required(self) -> None:
        with self.assertRaises(AlpacaConfigurationError):
            AlpacaCredentials.from_environment({})
        value = AlpacaCredentials.from_environment(
            {
                "ALPACA_API_KEY_ID": "test-key",
                "ALPACA_API_SECRET_KEY": "test-secret",
            }
        )
        self.assertEqual(value.headers["APCA-API-KEY-ID"], "test-key")
        self.assertEqual(value.headers["APCA-API-SECRET-KEY"], "test-secret")

    def test_transport_refuses_non_market_data_or_non_bar_endpoint(self) -> None:
        transport = UrllibReadOnlyTransport()
        for url in (
            "https://api.alpaca.markets/v2/orders?status=open",
            "https://data.alpaca.markets/v2/stocks/trades?symbols=AAPL",
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing request"):
                transport.get(url, {}, 1.0)

    def test_pagination_normalization_and_raw_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = paginated_transport()
            provider = AlpacaHistoricalProvider(
                config(root / "raw"),
                AlpacaCredentials("test-key", "test-secret"),
                transport=transport,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            dataset = provider.load()
            self.assertEqual(len(dataset.one_minute_bars), 4)
            self.assertEqual(len(dataset.daily_bars), 2)
            self.assertTrue(provider.audit.reconciled)
            self.assertEqual(provider.audit.requests, 3)
            self.assertEqual(provider.audit.accepted_pages, 3)
            self.assertEqual(
                dataset.one_minute_bars[0].available_at,
                dataset.one_minute_bars[0].timestamp + timedelta(minutes=1),
            )
            self.assertEqual(dataset.daily_bars[0].available_at.hour, 20)
            self.assertEqual(dataset.daily_bars[0].available_at.minute, 1)
            second_query = parse_qs(urlparse(transport.calls[1][0]).query)
            self.assertEqual(second_query["page_token"], ["next-minute-page"])
            first_query = parse_qs(urlparse(transport.calls[0][0]).query)
            self.assertEqual(first_query["asof"], ["-"])
            self.assertEqual(first_query["feed"], ["iex"])
            self.assertEqual(first_query["timeframe"], ["1Min"])
            for url, headers, _ in transport.calls:
                self.assertTrue(url.startswith(f"{ALPACA_BARS_URL}?"))
                self.assertNotIn("api.alpaca.markets/v2/orders", url)
                self.assertEqual(headers["APCA-API-KEY-ID"], "test-key")
            for artifact in provider.audit.artifacts:
                self.assertTrue(Path(artifact.response_path).exists())
                manifest_text = Path(artifact.manifest_path).read_text(
                    encoding="utf-8"
                )
                self.assertNotIn("test-key", manifest_text)
                self.assertNotIn("test-secret", manifest_text)
                self.assertIn('"credential_values_persisted": false', manifest_text)

    def test_rate_limit_retry_honours_retry_after(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                [
                    response(
                        {"code": 429, "message": "rate limit"},
                        status=429,
                        **{"Retry-After": "2"},
                    ),
                    response(
                        {
                            "bars": {
                                "AAPL": [bar("2026-07-15T13:30:00Z")],
                                "JPM": [bar("2026-07-15T13:30:00Z")],
                            },
                            "next_page_token": None,
                        }
                    ),
                    response(
                        {
                            "bars": {
                                "AAPL": [bar("2026-07-15T04:00:00Z")],
                                "JPM": [bar("2026-07-15T04:00:00Z")],
                            },
                            "next_page_token": None,
                        }
                    ),
                ]
            )
            sleeps: list[float] = []
            provider = AlpacaHistoricalProvider(
                config(Path(directory) / "raw"),
                AlpacaCredentials("key", "secret"),
                transport=transport,
                clock=lambda: NOW,
                sleeper=sleeps.append,
            )
            provider.load()
            self.assertIn(2.0, sleeps)
            self.assertEqual(provider.audit.requests, 3)
            self.assertEqual(provider.audit.artifacts[0].status, 429)

    def test_config_rejects_unbounded_ranges_and_large_universe(self) -> None:
        value = config(Path("raw"))
        too_long = replace(
            value,
            minute_end=value.minute_start + timedelta(days=6),
        )
        with self.assertRaisesRegex(AlpacaConfigurationError, "five days"):
            too_long.validate(NOW)
        too_many = replace(
            value,
            universe=tuple(
                ConfiguredSecurity(f"US:T{index}", f"T{index}", Exchange.NASDAQ)
                for index in range(11)
            ),
        )
        with self.assertRaisesRegex(AlpacaConfigurationError, "one to ten"):
            too_many.validate(NOW)

    def test_quality_command_writes_no_model_or_profit_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "alpaca.json"
            config_path.write_text(
                json.dumps(
                    {
                        "provider": "alpaca_historical_bars",
                        "universe": [
                            {
                                "security_id": "US:AAPL",
                                "ticker": "AAPL",
                                "exchange": "NASDAQ",
                            },
                            {
                                "security_id": "US:JPM",
                                "ticker": "JPM",
                                "exchange": "NYSE",
                            },
                        ],
                        "minute_range": {
                            "start": "2026-07-15T13:30:00Z",
                            "end": "2026-07-15T13:32:00Z",
                            "session_dates": ["2026-07-15"],
                        },
                        "daily_range": {
                            "start": "2026-07-01T00:00:00Z",
                            "end": "2026-07-16T00:00:00Z",
                        },
                        "included_sessions": ["regular"],
                        "feed": "iex",
                        "adjustment": "raw",
                        "raw_root": "data/raw/alpaca",
                        "rate_limit": {
                            "minimum_request_interval_seconds": 0,
                            "max_attempts": 2,
                            "max_retry_delay_seconds": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = run_historical_quality_check(
                config_path,
                root / "output",
                root,
                environment={
                    "ALPACA_API_KEY_ID": "key",
                    "ALPACA_API_SECRET_KEY": "secret",
                },
                transport=paginated_transport(),
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            self.assertTrue(summary["read_only_research_only"])
            self.assertFalse(summary["trade_or_account_endpoints_used"])
            self.assertFalse(summary["training_performed"])
            self.assertFalse(summary["predictions_generated"])
            self.assertFalse(summary["profitability_claimed"])
            self.assertGreater(summary["quality_errors"], 0)
            run_dir = Path(str(summary["run_directory"]))
            self.assertTrue((run_dir / "run_manifest.json").exists())
            self.assertTrue((run_dir / "normalized" / "bars_1m.jsonl").exists())
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                run_historical_quality_check(
                    config_path,
                    root / "output",
                    root,
                    environment={
                        "ALPACA_API_KEY_ID": "key",
                        "ALPACA_API_SECRET_KEY": "secret",
                    },
                    transport=paginated_transport(),
                    clock=lambda: NOW,
                    sleeper=lambda _: None,
                )

    def test_cli_fails_closed_before_network_when_credentials_missing(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                code = main(
                    [
                        "--config",
                        "config/alpaca_historical.sample.json",
                        "--output-dir",
                        "output/unused",
                    ]
                )
        self.assertEqual(code, 2)
        self.assertIn("ALPACA_API_KEY_ID", error.getvalue())


if __name__ == "__main__":
    unittest.main()
