"""Read-only shadow source providers and endpoint policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from equity_research.catalyst_intelligence.contracts import (
    NumericalDetail,
    SourceBatch,
    SourceDocument,
    SourceKind,
)

from .contracts import (
    MarketObservation,
    MonitorMode,
    RawSourceItem,
    ShadowInputBatch,
    SourceFamily,
)


class TransientSourceError(RuntimeError):
    """A retryable read-only source failure."""


class ShadowSourceProvider(Protocol):
    def poll(self, processing_time: datetime) -> ShadowInputBatch: ...


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    approved_news_domains: tuple[str, ...] = ()

    def validate(self, family: SourceFamily, url: str, mode: MonitorMode) -> None:
        parsed = urlparse(url)
        if parsed.scheme == "fixture" and mode in {MonitorMode.SYNTHETIC, MonitorMode.REPLAY}:
            return
        if parsed.scheme != "https":
            raise ValueError(f"source URL must use HTTPS: {url}")
        host = (parsed.hostname or "").lower()
        if any(token in host for token in ("trading.alpaca", "broker", "paper-api")):
            raise ValueError(f"brokerage/trading endpoint prohibited: {url}")
        if family is SourceFamily.MARKET_DATA and host != "data.alpaca.markets":
            raise ValueError(f"unapproved market-data endpoint: {url}")
        if family is SourceFamily.SEC and not (host == "sec.gov" or host.endswith(".sec.gov")):
            raise ValueError(f"unapproved SEC endpoint: {url}")
        if family is SourceFamily.APPROVED_NEWS and not any(
            host == domain or host.endswith(f".{domain}")
            for domain in self.approved_news_domains
        ):
            raise ValueError(f"unapproved news endpoint: {url}")


class BatchCatalystProvider:
    def __init__(self, batch: SourceBatch) -> None:
        self._batch = batch

    def load(self) -> SourceBatch:
        return self._batch


class ReplayShadowProvider:
    """Replay captured cycles without network access."""

    def __init__(self, path: Path, *, loop: bool = False) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cycles = payload.get("cycles")
        if not isinstance(cycles, list) or not cycles:
            raise ValueError("replay file must contain a non-empty cycles list")
        self._cycles = cycles
        self._index = 0
        self._loop = loop

    def poll(self, processing_time: datetime) -> ShadowInputBatch:
        if self._index >= len(self._cycles):
            if not self._loop:
                raise StopIteration
            self._index = 0
        cycle = self._cycles[self._index]
        self._index += 1
        return batch_from_dict(cycle, processing_time, MonitorMode.REPLAY)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def batch_from_dict(
    value: dict[str, object], processing_time: datetime, mode: MonitorMode
) -> ShadowInputBatch:
    raw_items = tuple(
        RawSourceItem(
            source_id=str(item["source_id"]),
            source_family=SourceFamily(str(item["source_family"])),
            source_url=str(item["source_url"]),
            source_timestamp=_dt(str(item["source_timestamp"])),
            first_seen_at=_dt(str(item.get("first_seen_at", item["source_timestamp"]))),
            processing_timestamp=processing_time,
            payload=dict(item.get("payload", {})),
            license_class=str(item.get("license_class", "replay_fixture")),
        )
        for item in value.get("raw_items", [])  # type: ignore[union-attr]
    )
    market = tuple(
        MarketObservation(
            observation_id=str(item["observation_id"]),
            security_id=str(item["security_id"]),
            ticker=str(item["ticker"]),
            source_url=str(item["source_url"]),
            source_timestamp=_dt(str(item["source_timestamp"])),
            first_seen_at=_dt(str(item.get("first_seen_at", item["source_timestamp"]))),
            processing_timestamp=processing_time,
            feed=str(item.get("feed", "replay")),
            bar_complete=bool(item.get("bar_complete", False)),
            close=float(item["close"]) if item.get("close") is not None else None,
            volume=int(item["volume"]) if item.get("volume") is not None else None,
            bid=float(item["bid"]) if item.get("bid") is not None else None,
            ask=float(item["ask"]) if item.get("ask") is not None else None,
            consolidated_coverage=bool(item.get("consolidated_coverage", False)),
            halt_status=str(item["halt_status"]) if item.get("halt_status") is not None else None,
            free_float=int(item["free_float"]) if item.get("free_float") is not None else None,
            missing_flags=tuple(str(flag) for flag in item.get("missing_flags", [])),
        )
        for item in value.get("market_observations", [])  # type: ignore[union-attr]
    )
    documents = tuple(_document_from_dict(item, processing_time) for item in value.get("catalyst_documents", []))  # type: ignore[union-attr]
    batch = SourceBatch(
        provider=str(value.get("provider", "replay")),
        dataset_kind="shadow_replay",
        fetched_at=processing_time,
        documents=documents,
    )
    watermarks = tuple(
        (SourceFamily(str(item["source_family"])), _dt(str(item["timestamp"])))
        for item in value.get("source_watermarks", [])  # type: ignore[union-attr]
    )
    return ShadowInputBatch(str(value.get("provider", "replay")), mode, processing_time, raw_items, market, batch, watermarks)


def _document_from_dict(item: dict[str, object], processing_time: datetime) -> SourceDocument:
    published = _dt(str(item["published_at"]))
    first_public = _dt(str(item.get("first_public_at", item["published_at"])))
    first_seen = _dt(str(item.get("first_seen_at", processing_time.isoformat())))
    return SourceDocument(
        document_id=str(item["document_id"]), ticker=str(item["ticker"]),
        issuer_id=str(item["issuer_id"]) if item.get("issuer_id") else None,
        title=str(item["title"]), text=str(item["text"]), source_url=str(item["source_url"]),
        source_kind=SourceKind(str(item["source_kind"])), published_at=published,
        first_public_at=first_public, first_seen_at=first_seen, ingested_at=processing_time,
        available_at=max(first_public, first_seen), source_timestamp_verified=bool(item.get("source_timestamp_verified", False)),
        source_record_id=str(item["source_record_id"]) if item.get("source_record_id") else None,
        form_type=str(item["form_type"]) if item.get("form_type") else None,
        form_items=tuple(str(x) for x in item.get("form_items", [])),
        accession_number=str(item["accession_number"]) if item.get("accession_number") else None,
        expected_catalyst_date=date.fromisoformat(str(item["expected_catalyst_date"])) if item.get("expected_catalyst_date") else None,
        structured_numerical_details=tuple(NumericalDetail(**detail) for detail in item.get("structured_numerical_details", [])),  # type: ignore[arg-type]
    )
