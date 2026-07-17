from __future__ import annotations

import contextlib
import io
import json
import os
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
    AlpacaRequestError,
    ConfiguredSecurity,
    HttpResponse,
    UrllibReadOnlyTransport,
)
from equity_research.market_data.contracts import Exchange, Session
from equity_research.market_data.historical_quality import (
    load_environment_file,
    load_historical_config,
    main,
    run_historical_quality_check,
)


UTC = timezone.utc
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, url: str, headers, timeout_seconds: float) -> HttpResponse:
        self.calls.append((url, dict(headers), timeout_seconds))
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


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
            self.assertEqual(provider.audit.network_requests, 3)
            self.assertEqual(provider.audit.cache_hits, 0)
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
                self.assertIn(
                    '"license_class": "alpaca_personal_noncommercial_research_review_required"',
                    manifest_text,
                )

    def test_successful_responses_are_reused_from_hash_verified_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = config(root / "raw")
            first_transport = paginated_transport()
            first = AlpacaHistoricalProvider(
                value,
                AlpacaCredentials("key", "secret"),
                transport=first_transport,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            first_dataset = first.load()

            empty_transport = FakeTransport([])
            second = AlpacaHistoricalProvider(
                value,
                None,
                cache_only=True,
                transport=empty_transport,
                clock=lambda: NOW + timedelta(hours=1),
                sleeper=lambda _: None,
            )
            second_dataset = second.load()

            self.assertEqual(first_dataset.one_minute_bars, second_dataset.one_minute_bars)
            self.assertEqual(first_dataset.daily_bars, second_dataset.daily_bars)
            self.assertEqual(empty_transport.calls, [])
            self.assertEqual(second.audit.requests, 3)
            self.assertEqual(second.audit.network_requests, 0)
            self.assertEqual(second.audit.cache_hits, 3)
            self.assertTrue(all(item.cache_hit for item in second.audit.artifacts))

    def test_cache_only_miss_fails_before_transport_or_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            empty_transport = FakeTransport([])
            provider = AlpacaHistoricalProvider(
                config(Path(directory) / "raw"),
                None,
                cache_only=True,
                transport=empty_transport,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            with self.assertRaisesRegex(AlpacaRequestError, "cache-only run"):
                provider.load()
            self.assertEqual(empty_transport.calls, [])

    def test_corrupted_cached_response_fails_closed_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = config(root / "raw")
            first = AlpacaHistoricalProvider(
                value,
                AlpacaCredentials("key", "secret"),
                transport=paginated_transport(),
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            first.load()
            Path(first.audit.artifacts[0].response_path).write_bytes(b"corrupt")

            empty_transport = FakeTransport([])
            second = AlpacaHistoricalProvider(
                value,
                AlpacaCredentials("key", "secret"),
                transport=empty_transport,
                clock=lambda: NOW + timedelta(hours=1),
                sleeper=lambda _: None,
            )
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                second.load()
            self.assertEqual(empty_transport.calls, [])

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
            self.assertEqual(provider.audit.network_requests, 3)
            self.assertEqual(provider.audit.artifacts[0].status, 429)

    def test_transient_connection_failure_is_retried_without_fabricated_raw_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                [
                    AlpacaRequestError("temporary connection failure"),
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
            self.assertIn(1.0, sleeps)
            self.assertEqual(provider.audit.network_requests, 3)
            self.assertEqual(provider.audit.requests, 2)
            self.assertEqual(len(provider.audit.artifacts), 2)

    def test_config_rejects_unbounded_ranges_and_large_universe(self) -> None:
        value = config(Path("raw"))
        too_long = replace(
            value,
            minute_end=value.minute_start + timedelta(days=32),
        )
        with self.assertRaisesRegex(AlpacaConfigurationError, "thirty-one days"):
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

    def test_sample_config_is_three_stocks_and_one_month_of_sessions(self) -> None:
        value = load_historical_config(
            Path("config/alpaca_historical.sample.json"), Path(".")
        )
        value.validate(NOW)
        self.assertEqual([item.ticker for item in value.universe], ["AAPL", "MSFT", "JPM"])
        self.assertEqual(len(value.minute_session_dates), 21)
        self.assertEqual(value.minute_start, datetime(2026, 6, 1, 8, 0, tzinfo=UTC))
        self.assertEqual(value.minute_end, datetime(2026, 7, 1, 0, 0, tzinfo=UTC))
        self.assertTrue(value.cache_enabled)
        self.assertEqual(value.cache_max_age_hours, 24.0)

    def test_dotenv_loading_is_local_non_mutating_and_process_env_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "# local only\n"
                "ALPACA_API_KEY_ID=file-key\n"
                "export ALPACA_API_SECRET_KEY='file-secret'\n",
                encoding="utf-8",
            )
            before = dict(os.environ)
            loaded = load_environment_file(
                env_path, {"ALPACA_API_KEY_ID": "process-key"}
            )
            self.assertEqual(loaded["ALPACA_API_KEY_ID"], "process-key")
            self.assertEqual(loaded["ALPACA_API_SECRET_KEY"], "file-secret")
            self.assertEqual(dict(os.environ), before)

    def test_credentials_are_rejected_anywhere_in_json_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.loads(
                Path("config/alpaca_historical.sample.json").read_text(
                    encoding="utf-8"
                )
            )
            raw["cache"]["api_key"] = "must-not-be-accepted"
            config_path = root / "alpaca.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(
                AlpacaConfigurationError, "credentials must come from environment"
            ):
                load_historical_config(config_path, root)

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
                        "cache": {"enabled": True, "max_age_hours": 24},
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
            self.assertFalse(summary["cache_only"])
            self.assertEqual(summary["validated_at"], NOW)
            self.assertEqual(summary["network_requests"], 3)
            self.assertEqual(summary["cache_hits"], 0)
            self.assertRegex(str(summary["config_file_sha256"]), r"^[0-9a-f]{64}$")
            self.assertGreater(summary["quality_errors"], 0)
            run_dir = Path(str(summary["run_directory"]))
            self.assertTrue((run_dir / "run_manifest.json").exists())
            self.assertTrue((run_dir / "normalized" / "bars_1m.jsonl").exists())
            quality_text = (run_dir / "quality_report.json").read_text(
                encoding="utf-8"
            )
            self.assertIn("UNOBSERVED_TRADE_BAR_INTERVALS", quality_text)
            self.assertNotIn('"code": "MISSING_BARS"', quality_text)

            cached = run_historical_quality_check(
                config_path,
                root / "output",
                root,
                environment=None,
                transport=FakeTransport([]),
                clock=lambda: NOW + timedelta(hours=1),
                sleeper=lambda _: None,
                cache_only=True,
            )
            self.assertTrue(cached["cache_only"])
            self.assertEqual(cached["network_requests"], 0)
            self.assertEqual(cached["cache_hits"], 3)
            self.assertEqual(cached["retrieved_at"], summary["retrieved_at"])
            self.assertEqual(cached["validated_at"], NOW + timedelta(hours=1))
            self.assertNotEqual(cached["run_directory"], summary["run_directory"])
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                run_historical_quality_check(
                    config_path,
                    root / "output",
                    root,
                    environment=None,
                    transport=FakeTransport([]),
                    clock=lambda: NOW + timedelta(hours=1),
                    sleeper=lambda _: None,
                    cache_only=True,
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
                        "--env-file",
                        "output/definitely-missing.env",
                    ]
                )
        self.assertEqual(code, 2)
        self.assertIn("ALPACA_API_KEY_ID", error.getvalue())

    def test_cli_cache_only_does_not_load_environment_file(self) -> None:
        summary = {
            "cache_only": True,
            "quality_errors": 1,
            "training_performed": False,
            "profitability_claimed": False,
        }
        with mock.patch(
            "equity_research.market_data.historical_quality.load_environment_file",
            side_effect=AssertionError("dotenv must not be read"),
        ), mock.patch(
            "equity_research.market_data.historical_quality.run_historical_quality_check",
            return_value=summary,
        ) as runner, contextlib.redirect_stdout(io.StringIO()):
            code = main(["--cache-only"])
        self.assertEqual(code, 2)
        self.assertTrue(runner.call_args.kwargs["cache_only"])
        self.assertIsNone(runner.call_args.kwargs["environment"])


if __name__ == "__main__":
    unittest.main()
