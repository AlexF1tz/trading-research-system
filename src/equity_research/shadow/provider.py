"""Read-only shadow source providers and endpoint policy."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen
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


class SecEdgarProvider:
    """Read-only SEC submissions poller; no API key or filing-text scraping."""

    def __init__(self, cik_to_ticker: dict[str, str], user_agent: str | None = None,
                 opener=urlopen) -> None:  # type: ignore[no-untyped-def]
        self._mapping = {str(cik).zfill(10): ticker.upper() for cik, ticker in cik_to_ticker.items()}
        self._user_agent = (user_agent or os.environ.get("SEC_USER_AGENT", "")).strip()
        if not self._user_agent:
            raise ValueError("SEC_USER_AGENT is required for EDGAR automated access")
        self._opener = opener
        self._seen: set[str] = set()

    def poll(self, processing_time: datetime) -> ShadowInputBatch:
        raw_items: list[RawSourceItem] = []
        documents: list[SourceDocument] = []
        for cik, ticker in self._mapping.items():
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            request = Request(url, headers={"User-Agent": self._user_agent, "Accept": "application/json"})
            try:
                with self._opener(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (OSError, ValueError) as exc:
                raise TransientSourceError(f"SEC EDGAR request failed for {cik}") from exc
            received = processing_time
            recent = payload.get("filings", {}).get("recent", {})
            fields = list(recent.get("accessionNumber", []))
            for index, accession in enumerate(fields):
                if accession in self._seen:
                    continue
                filing_date = str(recent.get("filingDate", [])[index])
                form = str(recent.get("form", [])[index])
                if form not in {"8-K", "S-1", "S-3", "424B3", "424B5", "10-Q", "10-K", "6-K", "4", "13D", "13G", "DEF 14A"}:
                    continue
                accession_compact = accession.replace("-", "")
                source_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_compact}/{accession}-index.html"
                published = datetime.fromisoformat(filing_date).replace(tzinfo=received.tzinfo)
                document_id = f"sec-{accession}"
                self._seen.add(accession)
                raw_items.append(RawSourceItem(
                    source_id=accession, source_family=SourceFamily.SEC, source_url=url,
                    source_timestamp=published, first_seen_at=received, processing_timestamp=received,
                    payload={"cik": cik, "accession": accession, "form": form, "filing_date": filing_date},
                    license_class="sec_public_edgar", provider_received_at=received,
                ))
                documents.append(SourceDocument(
                    document_id=document_id, ticker=ticker, issuer_id=cik,
                    title=f"SEC {form} filing {accession}", text=f"SEC filing form {form}; filing date {filing_date}.",
                    source_url=source_url, source_kind=SourceKind.SEC_FILING,
                    published_at=published, first_public_at=published, first_seen_at=received,
                    ingested_at=received, available_at=received, source_timestamp_verified=False,
                    source_record_id=accession, form_type=form, accession_number=accession,
                ))
        batch = SourceBatch("sec_edgar", "sec_submissions_recent", processing_time, tuple(documents))
        return ShadowInputBatch("sec_edgar", MonitorMode.LIVE, processing_time, tuple(raw_items), (), batch,
                                ((SourceFamily.SEC, processing_time),))


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
            provider_received_at=_dt(str(item["provider_received_at"])) if item.get("provider_received_at") else processing_time,
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
