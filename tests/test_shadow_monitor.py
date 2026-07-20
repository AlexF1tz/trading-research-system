from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from equity_research.shadow.contracts import MonitorMode, SourceFamily
from equity_research.shadow.monitor import MODELLING_BLOCK, MonitorConfig, ShadowMonitor
from equity_research.shadow.provider import (
    AlpacaLiveMarketProvider, CompositeShadowProvider, EndpointPolicy,
    ReplayShadowProvider, SecEdgarProvider, TransientSourceError,
)
from equity_research.shadow.storage import ImmutableStore
from equity_research.shadow.synthetic import SyntheticShadowProvider


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(minutes=1)
        return result


def monitor(tmp_path, provider=None, **config):
    store = ImmutableStore(tmp_path / "data", tmp_path / "predictions")
    return ShadowMonitor(
        provider or SyntheticShadowProvider(), store,
        config=MonitorConfig(poll_interval_seconds=0, **config),
        clock=Clock(datetime(2026, 7, 17, 13, 30, tzinfo=UTC)), sleeper=lambda _: None,
    ), store


def test_shadow_records_are_deduplicated_and_features_are_gated(tmp_path):
    subject, store = monitor(tmp_path)
    first = subject.run_cycle()
    second = subject.run_cycle()
    assert first.alerts_written == 1
    assert second.alerts_written == 0
    alerts = store.alert_records()
    assert len(alerts) == 1
    assert alerts[0]["empirical_modelling_blocked"] is True
    assert alerts[0]["empirical_modelling_block_reason"] == MODELLING_BLOCK
    assert alerts[0]["execution_recommendation"] is None
    assert alerts[0]["profitability_claimed"] is False
    assert "INSUFFICIENT_FEATURE_HISTORY" in alerts[0]["data_quality_flags"]
    assert len(list((tmp_path / "data/normalized/features").glob("*.json"))) == 1
    assert len(list((tmp_path / "data/normalized/catalyst_documents").glob("*.json"))) == 1
    assert len(list((tmp_path / "data/raw/sec").glob("*.json"))) == 1


def test_restarting_same_second_does_not_collide_on_duplicate_market_observation(tmp_path):
    first, _ = monitor(tmp_path)
    first.run_cycle()
    second, _ = monitor(tmp_path)
    second.run_cycle()


def test_outcome_is_append_only_and_never_training_data(tmp_path):
    subject, _ = monitor(tmp_path, outcome_intervals_minutes=(1,))
    subject.run_cycle()
    heartbeat = subject.run_cycle()
    assert heartbeat.outcomes_written == 1
    outcome = json.loads(next((tmp_path / "predictions/outcomes").glob("*.json")).read_text())
    assert outcome["used_for_training"] is False
    assert outcome["horizon_minutes"] == 1


def test_endpoint_policy_rejects_trading_and_unapproved_news():
    policy = EndpointPolicy(("example.com",))
    policy.validate(SourceFamily.MARKET_DATA, "https://data.alpaca.markets/v2/stocks/bars", MonitorMode.REPLAY)
    policy.validate(SourceFamily.SEC, "https://www.sec.gov/files/company_tickers.json", MonitorMode.REPLAY)
    with pytest.raises(ValueError, match="prohibited"):
        policy.validate(SourceFamily.MARKET_DATA, "https://paper-api.alpaca.markets/v2/orders", MonitorMode.REPLAY)
    with pytest.raises(ValueError, match="unapproved news"):
        policy.validate(SourceFamily.APPROVED_NEWS, "https://unapproved.invalid/story", MonitorMode.REPLAY)


class FlakyProvider:
    def poll(self, processing_time):
        raise TransientSourceError("temporary")


def test_transient_failure_writes_reconnecting_heartbeat(tmp_path):
    subject, _ = monitor(tmp_path, FlakyProvider())
    heartbeat = subject.run_cycle()
    assert heartbeat.status.value == "reconnecting"
    assert heartbeat.reconnect_attempt == 1


class StaleProvider(SyntheticShadowProvider):
    def poll(self, processing_time):
        batch = super().poll(processing_time)
        return type(batch)(batch.provider, batch.mode, batch.fetched_at, batch.raw_items,
                           batch.market_observations, batch.catalyst_batch,
                           tuple((family, timestamp - timedelta(hours=1)) for family, timestamp in batch.source_watermarks))


def test_stale_feed_is_explicit_and_blocks_features(tmp_path):
    subject, store = monitor(tmp_path, StaleProvider(), stale_after_seconds=10)
    heartbeat = subject.run_cycle()
    assert heartbeat.status.value == "degraded"
    assert "market_data" in heartbeat.stale_sources
    alert = store.alert_records()[0]
    assert alert["status"] == "blocked_data"
    assert "STALE_MARKET_DATA" in alert["data_quality_flags"]


def test_replay_provider_uses_cached_cycle_without_network(tmp_path):
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({"cycles": [{
        "provider": "captured-test", "raw_items": [], "market_observations": [],
        "catalyst_documents": [],
        "source_watermarks": [{"source_family": "market_data", "timestamp": "2026-07-17T13:30:00Z"}],
    }]}))
    provider = ReplayShadowProvider(replay)
    batch = provider.poll(datetime(2026, 7, 17, 13, 30, tzinfo=UTC))
    assert batch.mode is MonitorMode.REPLAY
    assert batch.provider == "captured-test"
    with pytest.raises(StopIteration):
        provider.poll(datetime(2026, 7, 17, 13, 31, tzinfo=UTC))


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()


def test_sec_provider_normalizes_and_deduplicates_filings():
    payload = {"filings": {"recent": {
        "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
        "filingDate": ["2026-07-17", "2026-07-17"], "form": ["8-K", "8-K"]
    }}}
    provider = SecEdgarProvider({"320193": "AAPL"}, "Research contact@example.edu", lambda *args, **kwargs: FakeResponse(payload))
    now = datetime(2026, 7, 17, 13, 30, tzinfo=UTC)
    first = provider.poll(now)
    second = provider.poll(now)
    assert first.mode is MonitorMode.LIVE
    assert len(first.catalyst_batch.documents) == 2
    assert len(first.raw_items) == 3  # full submissions response plus two new filings
    assert second.catalyst_batch.documents == ()
    assert first.raw_items[0].provider_received_at == now


def test_sec_provider_persists_dedup_state_and_acceptance_time(tmp_path):
    payload = {"filings": {"recent": {
        "accessionNumber": ["0000320193-26-000003"], "filingDate": ["2026-07-17"],
        "acceptanceDateTime": ["20260717133000"], "form": ["8-K"]
    }}}
    path = tmp_path / "sec_seen.json"
    opener = lambda *args, **kwargs: FakeResponse(payload)
    now = datetime(2026, 7, 17, 13, 40, tzinfo=UTC)
    first = SecEdgarProvider({"320193": "AAPL"}, "Research contact@example.edu", opener, state_path=path)
    batch = first.poll(now)
    assert batch.catalyst_batch.documents[0].accepted_at == datetime(2026, 7, 17, 13, 30, tzinfo=UTC)
    assert json.loads(path.read_text())["accessions"] == ["0000320193-26-000003"]
    restarted = SecEdgarProvider({"320193": "AAPL"}, "Research contact@example.edu", opener, state_path=path)
    assert restarted.poll(now).catalyst_batch.documents == ()
    assert batch.raw_items[0].payload["filings"] == payload["filings"]


def test_sec_provider_enforces_request_pacing():
    payload = {"filings": {"recent": {"accessionNumber": [], "filingDate": [], "form": []}}}
    delays = []
    provider = SecEdgarProvider(
        {"1": "AAA", "2": "BBB"}, "Research contact@example.edu",
        lambda *args, **kwargs: FakeResponse(payload), minimum_request_interval_seconds=0.1,
        sleeper=delays.append,
    )
    provider.poll(datetime(2026, 7, 17, 13, 40, tzinfo=UTC))
    assert delays and delays[0] >= 0


def test_alpaca_live_provider_labels_iex_and_missing_fields():
    payload = {"AAPL": {
        "minuteBar": {"t": "2026-07-17T13:30:00Z", "c": 210.5, "v": 12345},
        "latestQuote": {"t": "2026-07-17T13:30:01Z", "bp": 210.49, "ap": 210.51},
    }}
    captured = []
    def opener(request, timeout):
        captured.append(request.full_url)
        return FakeResponse(payload)
    provider = AlpacaLiveMarketProvider({"AAPL": "SEC-CIK-0000320193"}, "key", "secret", feed="iex", opener=opener)
    batch = provider.poll(datetime(2026, 7, 17, 13, 30, 2, tzinfo=UTC))
    observation = batch.market_observations[0]
    assert captured[0].startswith("https://data.alpaca.markets/v2/stocks/snapshots?")
    assert observation.close == 210.5 and observation.volume == 12345
    assert observation.bid == 210.49 and observation.ask == 210.51
    assert observation.consolidated_coverage is False
    assert "NON_CONSOLIDATED_COVERAGE" in observation.missing_flags
    assert "INCOMPLETE_BAR" in observation.missing_flags
    assert "MISSING_HALT_STATUS" in observation.missing_flags
    assert batch.raw_items[0].payload == payload


def test_composite_provider_associates_sec_and_market_by_ticker(tmp_path):
    sec_payload = {"filings": {"recent": {
        "accessionNumber": ["0000320193-26-000004"], "filingDate": ["2026-07-17"],
        "acceptanceDateTime": ["20260717133000"], "form": ["8-K"]
    }}}
    market_payload = {"AAPL": {
        "minuteBar": {"t": "2026-07-17T13:30:00Z", "c": 210.5, "v": 12345},
        "latestQuote": {"t": "2026-07-17T13:30:01Z", "bp": 210.49, "ap": 210.51},
    }}
    sec = SecEdgarProvider({"320193": "AAPL"}, "Research contact@example.edu", lambda *a, **k: FakeResponse(sec_payload))
    market = AlpacaLiveMarketProvider({"AAPL": "SEC-CIK-0000320193"}, "key", "secret", opener=lambda *a, **k: FakeResponse(market_payload))
    subject, store = monitor(tmp_path, CompositeShadowProvider((sec, market)))
    subject.run_cycle()
    alert = store.alert_records()[0]
    assert alert["ticker"] == "AAPL"
    assert alert["reference_price"] == 210.5
    assert "NON_CONSOLIDATED_COVERAGE" in alert["data_quality_flags"]
