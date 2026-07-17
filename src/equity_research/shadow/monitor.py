"""Continuous read-only Stage 3 shadow monitor."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from equity_research.catalyst_intelligence.pipeline import CatalystPipeline

from .contracts import (
    AlertStatus, Heartbeat, HeartbeatStatus, ResearchAlert, ShadowFeatureRecord,
    ShadowOutcome, SourceFamily, canonical_hash,
)
from .provider import BatchCatalystProvider, EndpointPolicy, ShadowSourceProvider, TransientSourceError
from .storage import ImmutableStore

MODELLING_BLOCK = (
    "ALPACA_IEX_EMPIRICAL_MODELLING_BLOCKED: historical coverage has unresolved "
    "session/data limitations and lacks consolidated quotes, halts, float, and a survivorship-safe universe"
)


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    poll_interval_seconds: float = 30.0
    stale_after_seconds: float = 120.0
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    outcome_horizon_minutes: int = 120


class ShadowMonitor:
    def __init__(self, provider: ShadowSourceProvider, store: ImmutableStore, *, config: MonitorConfig | None = None,
                 endpoint_policy: EndpointPolicy | None = None, clock: Callable[[], datetime] | None = None,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.provider, self.store = provider, store
        self.config = config or MonitorConfig()
        self.policy = endpoint_policy or EndpointPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleeper = sleeper
        self.cycle = 0
        self.reconnect_attempt = 0

    def run_forever(self) -> None:
        try:
            while True:
                self.run_cycle()
                self.sleeper(self.config.poll_interval_seconds)
        except (KeyboardInterrupt, StopIteration):
            self._stopped_heartbeat()

    def run_cycles(self, count: int) -> None:
        for _ in range(count):
            self.run_cycle()

    def run_cycle(self) -> Heartbeat:
        self.cycle += 1
        now = self.clock()
        try:
            batch = self.provider.poll(now)
            self.reconnect_attempt = 0
        except TransientSourceError:
            self.reconnect_attempt += 1
            heartbeat = self._heartbeat(now, "unknown", "synthetic", HeartbeatStatus.RECONNECTING, (), 0, 0, 0, 0, 0)
            self.sleeper(min(self.config.reconnect_initial_seconds * 2 ** (self.reconnect_attempt - 1), self.config.reconnect_max_seconds))
            return heartbeat
        for item in batch.raw_items:
            self.policy.validate(item.source_family, item.source_url, batch.mode)
            self.store.write_raw(item.source_family.value, item.source_id, item.to_dict())
        for observation in batch.market_observations:
            self.policy.validate(SourceFamily.MARKET_DATA, observation.source_url, batch.mode)
            if not self.store.has_normalized("market", observation.observation_id):
                self.store.write_normalized("market", observation.observation_id, observation.to_dict())
        result = CatalystPipeline(BatchCatalystProvider(batch.catalyst_batch)).run()
        for document in result.batch.documents:
            family = SourceFamily.SEC if document.source_kind.value == "sec_filing" else SourceFamily.APPROVED_NEWS
            self.policy.validate(family, document.source_url, batch.mode)
            if not self.store.has_normalized("catalyst_documents", document.document_id):
                self.store.write_normalized("catalyst_documents", document.document_id, document)
        for event in result.events:
            if not self.store.has_normalized("catalyst_events", event.event_id):
                self.store.write_normalized("catalyst_events", event.event_id, event.to_dict())
        stale = tuple(sorted(family.value for family, watermark in batch.source_watermarks if (now - watermark).total_seconds() > self.config.stale_after_seconds))
        features = self._features(batch.market_observations, now, stale)
        alerts_written = self._alerts(result.events, batch.market_observations, features, stale, now)
        outcomes_written = self._outcomes(now)
        status = HeartbeatStatus.DEGRADED if stale else HeartbeatStatus.HEALTHY
        return self._heartbeat(now, batch.provider, batch.mode.value, status, stale, len(batch.raw_items), len(batch.market_observations), len(result.batch.documents), alerts_written, outcomes_written)

    def _features(self, observations, now: datetime, stale: tuple[str, ...]):  # type: ignore[no-untyped-def]
        output = {}
        for obs in observations:
            records = self.store.market_records(obs.security_id)
            flags = set(obs.missing_flags)
            if not obs.bar_complete or obs.close is None or obs.volume is None: flags.add("MISSING_BAR")
            if obs.bid is None or obs.ask is None: flags.add("MISSING_QUOTES")
            if obs.halt_status is None: flags.add("MISSING_HALT_STATUS")
            if obs.free_float is None: flags.add("MISSING_FLOAT")
            if not obs.consolidated_coverage: flags.add("NON_CONSOLIDATED_COVERAGE")
            if SourceFamily.MARKET_DATA.value in stale: flags.add("STALE_MARKET_DATA")
            if len(records) < 2: flags.add("INSUFFICIENT_FEATURE_HISTORY")
            if obs.halt_status == "halted": flags.add("TRADING_HALT_ACTIVE")
            if flags:
                output[obs.ticker] = (None, tuple(sorted(flags)))
                continue
            previous = records[-2]
            previous_close = float(previous["close"])
            feature = ShadowFeatureRecord(
                canonical_hash([obs.observation_id, "shadow-market-features-v1"]), obs.security_id, obs.ticker,
                obs.source_timestamp, now, (float(obs.close) / previous_close - 1) * 100,
                float(obs.close) * int(obs.volume), (float(obs.ask) - float(obs.bid)) / float(obs.close) * 100,
                int(obs.volume) / int(obs.free_float),
            )
            if not self.store.has_normalized("features", feature.feature_id):
                self.store.write_normalized("features", feature.feature_id, feature.to_dict())
            output[obs.ticker] = (feature, ())
        return output

    def _alerts(self, events, observations, features, stale, now):  # type: ignore[no-untyped-def]
        written = 0
        by_ticker = {obs.ticker: obs for obs in observations}
        for event in events:
            observation = by_ticker.get(event.ticker)
            feature, flags = features.get(event.ticker, (None, ("MISSING_MARKET_OBSERVATION",)))
            alert_id = canonical_hash([event.event_id, event.available_at.isoformat(), "shadow-research-alert-v1"])
            if self.store.has_alert(alert_id):
                continue
            alert = ResearchAlert(
                alert_id, now, now, now + timedelta(minutes=self.config.outcome_horizon_minutes),
                observation.security_id if observation else f"unresolved:{event.ticker}", event.ticker,
                event.event_id, event.catalyst_category.value, event.source_url,
                AlertStatus.RESEARCH_ONLY if feature is not None and not stale else AlertStatus.BLOCKED_DATA,
                feature.feature_id if feature else None, observation.close if observation else None,
                tuple(sorted(set(flags) | {"EMPIRICAL_MODELLING_BLOCKED_ALPACA_IEX"})), stale,
                True, MODELLING_BLOCK,
                f"Research-only catalyst observation: {event.catalyst_category.value}; no return or execution recommendation.",
            )
            written += int(self.store.write_alert(alert_id, alert.to_dict()))
        return written

    def _outcomes(self, now: datetime) -> int:
        written = 0
        for alert in self.store.alert_records():
            horizon = datetime.fromisoformat(str(alert["horizon_end"]).replace("Z", "+00:00"))
            if horizon > now: continue
            records = self.store.market_records(str(alert["security_id"]))
            start = datetime.fromisoformat(str(alert["alert_as_of"]).replace("Z", "+00:00"))
            prices = [float(r["close"]) for r in records if r.get("close") is not None and start < datetime.fromisoformat(str(r["source_timestamp"]).replace("Z", "+00:00")) <= horizon]
            reference = alert.get("reference_price")
            complete = bool(prices and reference)
            outcome = ShadowOutcome(
                canonical_hash([alert["alert_id"], "shadow-outcome-v1"]), str(alert["alert_id"]), now, horizon,
                "complete" if complete else "insufficient_observations",
                (prices[-1] / float(reference) - 1) * 100 if complete else None,
                (max(prices) / float(reference) - 1) * 100 if complete else None,
                (min(prices) / float(reference) - 1) * 100 if complete else None, len(prices), False,
            )
            written += int(self.store.write_outcome(outcome.outcome_id, outcome.to_dict()))
        return written

    def _heartbeat(self, now, provider, mode, status, stale, raw, market, docs, alerts, outcomes):  # type: ignore[no-untyped-def]
        from .contracts import MonitorMode
        heartbeat = Heartbeat(canonical_hash([now.isoformat(), self.cycle, status.value, raw, market, docs, alerts, outcomes]), self.cycle, now, provider,
                              MonitorMode(mode), status, stale, self.reconnect_attempt, raw, market, docs, alerts, outcomes)
        self.store.write_heartbeat(heartbeat.heartbeat_id, heartbeat.to_dict())
        return heartbeat

    def _stopped_heartbeat(self) -> None:
        now = self.clock()
        self._heartbeat(now, "monitor", "synthetic", HeartbeatStatus.STOPPED, (), 0, 0, 0, 0, 0)
