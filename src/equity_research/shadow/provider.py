"""Read-only shadow source providers and endpoint policy."""

from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from equity_research.catalyst_intelligence.contracts import (
    NumericalDetail,
    SourceBatch,
    SourceDocument,
    SourceKind,
)

from .contracts import (
    MarketObservation,
    HaltObservation,
    MonitorMode,
    RawSourceItem,
    SecBootstrapManifest,
    ShadowInputBatch,
    SourceFamily,
    canonical_hash,
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
        if family is SourceFamily.TRADING_HALTS and not (host == "nasdaqtrader.com" or host.endswith(".nasdaqtrader.com")):
            raise ValueError(f"unapproved trading-halt endpoint: {url}")
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
                 opener=urlopen, *, state_path: Path | None = None,
                 minimum_request_interval_seconds: float = 0.11,
                 sleeper=time.sleep) -> None:  # type: ignore[no-untyped-def]
        self._mapping = {str(cik).zfill(10): ticker.upper() for cik, ticker in cik_to_ticker.items()}
        self._user_agent = (user_agent or os.environ.get("SEC_USER_AGENT", "")).strip()
        if not self._user_agent:
            raise ValueError("SEC_USER_AGENT is required for EDGAR automated access")
        self._opener = opener
        self._state_path = state_path
        self._minimum_interval = minimum_request_interval_seconds
        if self._minimum_interval < 0.1:
            raise ValueError("SEC request interval must be at least 0.1 seconds")
        self._sleeper = sleeper
        self._last_request_monotonic: float | None = None
        self._seen_by_cik, self._initialized_ciks = self._load_state()

    def _load_state(self) -> tuple[dict[str, set[str]], set[str]]:
        if self._state_path is None or not self._state_path.exists():
            return {}, set()
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("state root must be an object")
            if value.get("schema_version") == 2:
                ciks = value.get("ciks")
                if not isinstance(ciks, dict):
                    raise ValueError("version 2 state requires a ciks object")
                seen_by_cik: dict[str, set[str]] = {}
                initialized: set[str] = set()
                for raw_cik, raw_state in ciks.items():
                    cik = str(raw_cik).zfill(10)
                    if not isinstance(raw_state, dict) or not isinstance(raw_state.get("initialized"), bool):
                        raise ValueError("CIK state requires an initialized boolean")
                    accessions = raw_state.get("accessions")
                    if not isinstance(accessions, list) or any(not isinstance(item, str) for item in accessions):
                        raise ValueError("CIK state accessions must be a string list")
                    seen_by_cik[cik] = set(accessions)
                    if raw_state["initialized"]:
                        initialized.add(cik)
                return seen_by_cik, initialized
            if set(value) == {"accessions"} and isinstance(value["accessions"], list):
                if any(not isinstance(item, str) for item in value["accessions"]):
                    raise ValueError("legacy accessions must be strings")
                # SEC accession prefixes can identify a filing agent rather than
                # the issuer CIK. Conservatively copy the global legacy set to
                # each currently configured CIK so migration cannot replay
                # history. New CIKs added after version-2 migration remain
                # independently uninitialized and follow bootstrap behavior.
                legacy_seen = set(value["accessions"])
                return (
                    {cik: set(legacy_seen) for cik in self._mapping},
                    set(self._mapping),
                )
            raise ValueError("unrecognized SEC state schema")
        except (OSError, ValueError, AttributeError) as exc:
            raise ValueError("SEC state file is invalid") from exc

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        payload = {
            "schema_version": 2,
            "ciks": {
                cik: {
                    "initialized": cik in self._initialized_ciks,
                    "accessions": sorted(accessions),
                }
                for cik, accessions in sorted(self._seen_by_cik.items())
            },
        }
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self._state_path)

    def poll(self, processing_time: datetime) -> ShadowInputBatch:
        raw_items: list[RawSourceItem] = []
        documents: list[SourceDocument] = []
        bootstrap_manifests: list[SecBootstrapManifest] = []
        for cik, ticker in self._mapping.items():
            if self._last_request_monotonic is not None:
                delay = self._minimum_interval - (time.monotonic() - self._last_request_monotonic)
                if delay > 0:
                    self._sleeper(delay)
            self._last_request_monotonic = time.monotonic()
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            request = Request(url, headers={"User-Agent": self._user_agent, "Accept": "application/json"})
            try:
                with self._opener(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (OSError, ValueError) as exc:
                raise TransientSourceError(f"SEC EDGAR request failed for {cik}") from exc
            received = processing_time
            raw_items.append(RawSourceItem(
                source_id=f"submissions-{cik}", source_family=SourceFamily.SEC, source_url=url,
                source_timestamp=received, first_seen_at=received, processing_timestamp=received,
                payload=payload, license_class="sec_public_edgar", provider_received_at=received,
            ))
            recent = payload.get("filings", {}).get("recent", {})
            fields = list(recent.get("accessionNumber", []))
            acceptance_values = list(recent.get("acceptanceDateTime", [""] * len(fields)))
            if cik not in self._initialized_ciks:
                self._seen_by_cik[cik] = {str(accession) for accession in fields}
                self._initialized_ciks.add(cik)
                bootstrap_manifests.append(SecBootstrapManifest(
                    manifest_id=canonical_hash(["sec-bootstrap", cik, processing_time.isoformat(), sorted(fields)]),
                    cik=cik,
                    ticker=ticker,
                    seeded_accession_count=len(fields),
                    initialized_at=processing_time,
                    source_url=url,
                ))
                continue
            seen = self._seen_by_cik.setdefault(cik, set())
            for index, accession in enumerate(fields):
                if accession in seen:
                    continue
                seen.add(accession)
                filing_date = str(recent.get("filingDate", [])[index])
                accepted_text = str(acceptance_values[index] or "")
                form = str(recent.get("form", [])[index])
                if form not in {"8-K", "S-1", "S-3", "424B3", "424B5", "10-Q", "10-K", "6-K", "4", "13D", "13G", "DEF 14A"}:
                    continue
                accession_compact = accession.replace("-", "")
                source_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_compact}/{accession}-index.html"
                published = datetime.fromisoformat(filing_date).replace(tzinfo=received.tzinfo)
                accepted = _dt(accepted_text) if accepted_text else None
                first_public = accepted or published
                document_id = f"sec-{accession}"
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
                    published_at=published, first_public_at=first_public, first_seen_at=received,
                    ingested_at=received, available_at=max(first_public, received), source_timestamp_verified=accepted is not None,
                    source_record_id=accession, form_type=form, accession_number=accession,
                    accepted_at=accepted,
                ))
        self._save_state()
        batch = SourceBatch("sec_edgar", "sec_submissions_recent", processing_time, tuple(documents))
        return ShadowInputBatch("sec_edgar", MonitorMode.LIVE, processing_time, tuple(raw_items), (), batch,
                                ((SourceFamily.SEC, processing_time),), (), tuple(bootstrap_manifests))


class AlpacaLiveMarketProvider:
    """GET-only Alpaca stock snapshots adapter; never touches brokerage APIs."""

    def __init__(self, symbols: dict[str, str], key_id: str, secret_key: str, *,
                 feed: str = "iex", delayed_seconds: int = 0, opener=urlopen) -> None:  # type: ignore[no-untyped-def]
        if not symbols:
            raise ValueError("at least one Alpaca symbol is required")
        if feed not in {"iex", "sip"}:
            raise ValueError("Alpaca feed must be iex or sip")
        if not key_id or not secret_key:
            raise ValueError("ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY are required")
        if delayed_seconds < 0:
            raise ValueError("delayed_seconds cannot be negative")
        self._symbols = {ticker.upper(): security_id for ticker, security_id in symbols.items()}
        self._headers = {
            "APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json", "User-Agent": "equity-research-system/read-only-shadow",
        }
        self._feed = feed
        self._delayed_seconds = delayed_seconds
        self._opener = opener

    def poll(self, processing_time: datetime) -> ShadowInputBatch:
        query = urlencode({"symbols": ",".join(sorted(self._symbols)), "feed": self._feed})
        url = f"https://data.alpaca.markets/v2/stocks/snapshots?{query}"
        try:
            with self._opener(Request(url, headers=self._headers), timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise TransientSourceError("Alpaca market-data snapshot request failed") from exc
        observations: list[MarketObservation] = []
        source_times: list[datetime] = []
        for ticker, security_id in self._symbols.items():
            snapshot = payload.get(ticker, {})
            bar = snapshot.get("minuteBar") or {}
            quote = snapshot.get("latestQuote") or {}
            bar_time = _dt(str(bar["t"])) if bar.get("t") else processing_time
            quote_time = _dt(str(quote["t"])) if quote.get("t") else None
            source_times.extend(value for value in (bar_time, quote_time) if value is not None)
            flags: list[str] = []
            if not bar: flags.append("MISSING_BAR")
            bar_complete = bool(bar) and processing_time >= bar_time + timedelta(minutes=1)
            if bar and not bar_complete: flags.append("INCOMPLETE_BAR")
            if not quote: flags.append("MISSING_QUOTES")
            if self._feed == "iex": flags.append("NON_CONSOLIDATED_COVERAGE")
            if self._delayed_seconds: flags.append("DELAYED_COVERAGE")
            flags.extend(("MISSING_HALT_STATUS", "MISSING_FLOAT", "MISSING_RELATIVE_VOLUME_HISTORY"))
            observations.append(MarketObservation(
                observation_id=canonical_hash(["alpaca", self._feed, ticker, bar_time.isoformat()]),
                security_id=security_id, ticker=ticker, source_url=url,
                source_timestamp=bar_time, first_seen_at=processing_time,
                processing_timestamp=processing_time, feed=f"alpaca_{self._feed}",
                bar_complete=bar_complete, close=float(bar["c"]) if bar.get("c") is not None else None,
                volume=int(bar["v"]) if bar.get("v") is not None else None,
                bid=float(quote["bp"]) if quote.get("bp") is not None else None,
                ask=float(quote["ap"]) if quote.get("ap") is not None else None,
                consolidated_coverage=self._feed == "sip", halt_status=None, free_float=None,
                missing_flags=tuple(sorted(set(flags))), provider_received_at=processing_time,
            ))
        source_timestamp = max(source_times, default=processing_time)
        raw = RawSourceItem(
            source_id=f"alpaca-snapshots-{self._feed}-{'-'.join(sorted(self._symbols))}",
            source_family=SourceFamily.MARKET_DATA, source_url=url,
            source_timestamp=source_timestamp, first_seen_at=processing_time,
            processing_timestamp=processing_time, payload=payload,
            license_class="alpaca_market_data_entitlement_required",
            provider_received_at=processing_time,
        )
        empty = SourceBatch("alpaca_market_data", "live_snapshots", processing_time, ())
        return ShadowInputBatch("alpaca_market_data", MonitorMode.LIVE, processing_time, (raw,),
                                tuple(observations), empty, ((SourceFamily.MARKET_DATA, source_timestamp),))


class CompositeShadowProvider:
    """Combine independently read-only providers into one timestamped cycle."""

    def __init__(self, providers: tuple[ShadowSourceProvider, ...]) -> None:
        if not providers:
            raise ValueError("composite provider requires at least one provider")
        self._providers = providers

    def poll(self, processing_time: datetime) -> ShadowInputBatch:
        batches = tuple(provider.poll(processing_time) for provider in self._providers)
        documents = tuple(document for batch in batches for document in batch.catalyst_batch.documents)
        catalysts = SourceBatch("composite_shadow", "live_sources", processing_time, documents)
        return ShadowInputBatch(
            "composite_shadow", MonitorMode.LIVE, processing_time,
            tuple(item for batch in batches for item in batch.raw_items),
            tuple(item for batch in batches for item in batch.market_observations), catalysts,
            tuple(item for batch in batches for item in batch.source_watermarks),
            tuple(item for batch in batches for item in batch.halt_observations),
            tuple(item for batch in batches for item in batch.sec_bootstrap_manifests),
        )


class NasdaqHaltProvider:
    """Read-only official Nasdaq Trader RSS halt collector."""

    FEED_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"

    def __init__(self, *, opener=urlopen, state_path: Path | None = None,
                 minimum_request_interval_seconds: float = 60.0,
                 sleeper=time.sleep) -> None:  # type: ignore[no-untyped-def]
        if minimum_request_interval_seconds < 60:
            raise ValueError("Nasdaq halt feed must not be polled more than once per minute")
        self._opener = opener
        self._state_path = state_path
        self._minimum_interval = minimum_request_interval_seconds
        self._sleeper = sleeper
        self._last_request_monotonic: float | None = None
        self._seen = self._load_seen()

    def _load_seen(self) -> set[str]:
        if self._state_path is None or not self._state_path.exists():
            return set()
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            return {str(value) for value in payload.get("halt_ids", [])}
        except (OSError, ValueError, AttributeError) as exc:
            raise ValueError("Nasdaq halt state file is invalid") from exc

    def _save_seen(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary.write_text(json.dumps({"halt_ids": sorted(self._seen)}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self._state_path)

    def poll(self, processing_time: datetime) -> ShadowInputBatch:
        if self._last_request_monotonic is not None:
            delay = self._minimum_interval - (time.monotonic() - self._last_request_monotonic)
            if delay > 0:
                self._sleeper(delay)
        self._last_request_monotonic = time.monotonic()
        try:
            with self._opener(Request(self.FEED_URL, headers={"Accept": "application/rss+xml", "User-Agent": "equity-research-system/read-only-shadow"}), timeout=30) as response:
                xml_text = response.read().decode("utf-8")
            root = ET.fromstring(xml_text)
        except (OSError, UnicodeError, ET.ParseError) as exc:
            raise TransientSourceError("Nasdaq trading-halt RSS request failed") from exc
        observations: list[HaltObservation] = []
        for item in root.iter():
            if _local_name(item.tag) != "item":
                continue
            fields = {_local_name(child.tag).lower(): (child.text or "").strip() for child in item}
            ticker = fields.get("issuesymbol", "").upper()
            halt_date, halt_time = fields.get("haltdate", ""), fields.get("halttime", "")
            if not ticker or not halt_date or not halt_time:
                continue
            halt_at = _nasdaq_eastern_timestamp(halt_date, halt_time)
            reason = fields.get("reasoncode", "UNKNOWN") or "UNKNOWN"
            halt_id = canonical_hash([ticker, halt_at.isoformat(), reason])
            quote_at = _optional_nasdaq_timestamp(fields.get("resumptiondate", ""), fields.get("resumptionquotetime", ""))
            trade_at = _optional_nasdaq_timestamp(fields.get("resumptiondate", ""), fields.get("resumptiontradetime", ""))
            self._seen.add(halt_id)
            observations.append(HaltObservation(
                halt_id, ticker, reason, halt_at, quote_at, trade_at, self.FEED_URL,
                max((value for value in (halt_at, quote_at, trade_at) if value is not None), default=halt_at),
                processing_time, processing_time, processing_time,
            ))
        self._save_seen()
        raw = RawSourceItem(
            source_id="nasdaq-trading-halts-rss", source_family=SourceFamily.TRADING_HALTS,
            source_url=self.FEED_URL, source_timestamp=processing_time, first_seen_at=processing_time,
            processing_timestamp=processing_time, payload={"rss_xml": xml_text},
            license_class="nasdaq_trader_halt_rss_terms_apply", provider_received_at=processing_time,
        )
        empty = SourceBatch("nasdaq_trader", "trading_halts_rss", processing_time, ())
        return ShadowInputBatch("nasdaq_trader", MonitorMode.LIVE, processing_time, (raw,), (), empty,
                                ((SourceFamily.TRADING_HALTS, processing_time),), tuple(observations))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _nasdaq_eastern_timestamp(date_text: str, time_text: str) -> datetime:
    parsed_date = datetime.strptime(date_text, "%m/%d/%Y").date()
    parsed_time = datetime.strptime(time_text.split(".")[0], "%H:%M:%S").time()
    return datetime.combine(parsed_date, parsed_time, ZoneInfo("America/New_York")).astimezone(UTC)


def _optional_nasdaq_timestamp(date_text: str, time_text: str) -> datetime | None:
    return _nasdaq_eastern_timestamp(date_text, time_text) if date_text and time_text else None


def _dt(value: str) -> datetime:
    if len(value) == 14 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
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
    halts = tuple(
        HaltObservation(
            halt_id=str(item["halt_id"]), ticker=str(item["ticker"]), reason_code=str(item["reason_code"]),
            halt_at=_dt(str(item["halt_at"])),
            resumption_quote_at=_dt(str(item["resumption_quote_at"])) if item.get("resumption_quote_at") else None,
            resumption_trade_at=_dt(str(item["resumption_trade_at"])) if item.get("resumption_trade_at") else None,
            source_url=str(item["source_url"]), source_timestamp=_dt(str(item["source_timestamp"])),
            provider_received_at=_dt(str(item.get("provider_received_at", processing_time.isoformat()))),
            first_seen_at=_dt(str(item.get("first_seen_at", processing_time.isoformat()))),
            processing_timestamp=processing_time,
        ) for item in value.get("halt_observations", [])  # type: ignore[union-attr]
    )
    manifests = tuple(
        SecBootstrapManifest(
            manifest_id=str(item["manifest_id"]), cik=str(item["cik"]), ticker=str(item["ticker"]),
            seeded_accession_count=int(item["seeded_accession_count"]),
            initialized_at=_dt(str(item["initialized_at"])), source_url=str(item["source_url"]),
            state_schema_version=int(item.get("state_schema_version", 2)),
        ) for item in value.get("sec_bootstrap_manifests", [])  # type: ignore[union-attr]
    )
    return ShadowInputBatch(
        str(value.get("provider", "replay")), mode, processing_time, raw_items, market,
        batch, watermarks, halts, manifests
    )


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
        accepted_at=_dt(str(item["accepted_at"])) if item.get("accepted_at") else None,
    )
