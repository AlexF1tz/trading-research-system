"""Read-only Alpaca historical bars adapter with immutable raw preservation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time as wall_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .calendar import UsEquityCalendar, market_local_to_utc
from .contracts import (
    Adjustment,
    Bar,
    CoverageManifest,
    Exchange,
    Instrument,
    ProviderDataset,
    Session,
    Timeframe,
)
from .provider import parse_timestamp


ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
ALPACA_BARS_PATH = "/v2/stocks/bars"
ALPACA_BARS_URL = f"{ALPACA_DATA_BASE_URL}{ALPACA_BARS_PATH}"
ALPACA_PROVIDER_NAME = "alpaca_market_data"
ADAPTER_VERSION = "alpaca-historical-bars-v2"
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
SAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "date",
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "request-id",
        "x-request-id",
    }
)
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
UTC = timezone.utc


class AlpacaConfigurationError(ValueError):
    """The requested historical run is unsafe, unbounded, or incomplete."""


class AlpacaRequestError(RuntimeError):
    """A read-only market-data request failed without exposing credentials."""


@dataclass(frozen=True, slots=True)
class AlpacaCredentials:
    key_id: str
    secret_key: str

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "AlpacaCredentials":
        values = environment if environment is not None else os.environ
        key_id = values.get("ALPACA_API_KEY_ID", "").strip()
        secret_key = values.get("ALPACA_API_SECRET_KEY", "").strip()
        missing = [
            name
            for name, value in (
                ("ALPACA_API_KEY_ID", key_id),
                ("ALPACA_API_SECRET_KEY", secret_key),
            )
            if not value
        ]
        if missing:
            raise AlpacaConfigurationError(
                "missing required environment variable(s): " + ", ".join(missing)
            )
        return cls(key_id=key_id, secret_key=secret_key)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
            "User-Agent": "equity-research-system/read-only-stage2",
        }


@dataclass(frozen=True, slots=True)
class ConfiguredSecurity:
    security_id: str
    ticker: str
    exchange: Exchange


@dataclass(frozen=True, slots=True)
class AlpacaHistoricalConfig:
    universe: tuple[ConfiguredSecurity, ...]
    minute_start: datetime
    minute_end: datetime
    daily_start: datetime
    daily_end: datetime
    minute_session_dates: tuple[date, ...]
    included_sessions: tuple[Session, ...] = (Session.REGULAR,)
    feed: str = "iex"
    adjustment: str = "raw"
    page_limit: int = 10_000
    max_pages_per_timeframe: int = 100
    timeout_seconds: float = 30.0
    minimum_request_interval_seconds: float = 0.35
    max_attempts: int = 5
    max_retry_delay_seconds: float = 30.0
    minimum_historical_lag_minutes: int = 15
    raw_root: Path = Path("data/raw/alpaca")
    cache_enabled: bool = True
    cache_max_age_hours: float = 24.0
    license_class: str = "alpaca_personal_noncommercial_research_review_required"

    def validate(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise AlpacaConfigurationError("validation clock must be timezone-aware")
        now = now.astimezone(UTC)
        for name, value in (
            ("minute_start", self.minute_start),
            ("minute_end", self.minute_end),
            ("daily_start", self.daily_start),
            ("daily_end", self.daily_end),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise AlpacaConfigurationError(f"{name} must be timezone-aware")
            if value.utcoffset() != timedelta(0):
                raise AlpacaConfigurationError(f"{name} must be normalized to UTC")
        if not self.minute_start < self.minute_end:
            raise AlpacaConfigurationError("minute_start must precede minute_end")
        if not self.daily_start < self.daily_end:
            raise AlpacaConfigurationError("daily_start must precede daily_end")
        if self.minute_end - self.minute_start > timedelta(days=31):
            raise AlpacaConfigurationError("minute range is limited to thirty-one days")
        if self.daily_end - self.daily_start > timedelta(days=90):
            raise AlpacaConfigurationError("daily range is limited to ninety days")
        if not (
            self.daily_start <= self.minute_start
            and self.daily_end >= self.minute_end
        ):
            raise AlpacaConfigurationError(
                "daily range must cover the requested minute range"
            )
        if not 15 <= self.minimum_historical_lag_minutes <= 1_440:
            raise AlpacaConfigurationError(
                "minimum_historical_lag_minutes must be between 15 and 1440"
            )
        latest_allowed = now - timedelta(minutes=self.minimum_historical_lag_minutes)
        if self.minute_end > latest_allowed or self.daily_end > latest_allowed:
            raise AlpacaConfigurationError(
                "historical end times must satisfy the configured publication lag"
            )
        if not 1 <= len(self.universe) <= 10:
            raise AlpacaConfigurationError("universe must contain one to ten securities")
        security_ids = [value.security_id for value in self.universe]
        tickers = [value.ticker for value in self.universe]
        if len(set(security_ids)) != len(security_ids):
            raise AlpacaConfigurationError("security IDs must be unique")
        if len(set(tickers)) != len(tickers):
            raise AlpacaConfigurationError("tickers must be unique")
        for security in self.universe:
            if not security.security_id.strip():
                raise AlpacaConfigurationError("security_id cannot be blank")
            if not _TICKER.fullmatch(security.ticker):
                raise AlpacaConfigurationError(
                    f"invalid normalized ticker: {security.ticker!r}"
                )
            if security.exchange not in {Exchange.NASDAQ, Exchange.NYSE}:
                raise AlpacaConfigurationError(
                    "Stage 2 sample permits Nasdaq and NYSE securities only"
                )
        if self.feed not in {"iex", "sip"}:
            raise AlpacaConfigurationError("feed must be iex or sip")
        if self.adjustment not in {"raw", "split"}:
            raise AlpacaConfigurationError("adjustment must be raw or split")
        if not self.included_sessions or any(
            value not in {Session.PREMARKET, Session.REGULAR}
            for value in self.included_sessions
        ):
            raise AlpacaConfigurationError(
                "included_sessions must contain premarket and/or regular"
            )
        if len(set(self.included_sessions)) != len(self.included_sessions):
            raise AlpacaConfigurationError("included_sessions cannot contain duplicates")
        if not self.minute_session_dates or len(self.minute_session_dates) > 23:
            raise AlpacaConfigurationError(
                "one to twenty-three explicit minute_session_dates are required"
            )
        if len(set(self.minute_session_dates)) != len(self.minute_session_dates):
            raise AlpacaConfigurationError(
                "minute_session_dates cannot contain duplicates"
            )
        calendar = UsEquityCalendar()
        range_start_date = calendar.local_date(self.minute_start)
        range_end_date = calendar.local_date(
            self.minute_end - timedelta(microseconds=1)
        )
        for session_date in self.minute_session_dates:
            if not calendar.is_session_date(session_date):
                raise AlpacaConfigurationError(
                    f"minute session date is not a configured weekday: {session_date}"
                )
            if not range_start_date <= session_date <= range_end_date:
                raise AlpacaConfigurationError(
                    "minute_session_dates must fall inside the requested minute range"
                )
        if not 1 <= self.page_limit <= 10_000:
            raise AlpacaConfigurationError("page_limit must be between 1 and 10000")
        if not 1 <= self.max_pages_per_timeframe <= 100:
            raise AlpacaConfigurationError(
                "max_pages_per_timeframe must be between 1 and 100"
            )
        if self.timeout_seconds <= 0 or self.minimum_request_interval_seconds < 0:
            raise AlpacaConfigurationError("timeout and pacing values are invalid")
        if not 1 <= self.max_attempts <= 10:
            raise AlpacaConfigurationError("max_attempts must be between 1 and 10")
        if self.max_retry_delay_seconds < 0:
            raise AlpacaConfigurationError("max retry delay cannot be negative")
        if not isinstance(self.cache_enabled, bool):
            raise AlpacaConfigurationError("cache_enabled must be true or false")
        if not 0 < self.cache_max_age_hours <= 168:
            raise AlpacaConfigurationError(
                "cache_max_age_hours must be greater than zero and at most 168"
            )
        if not self.license_class.strip():
            raise AlpacaConfigurationError("license_class is required")


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class ReadOnlyHttpTransport(Protocol):
    def get(
        self, url: str, headers: Mapping[str, str], timeout_seconds: float
    ) -> HttpResponse: ...


class UrllibReadOnlyTransport:
    """GET-only transport locked to Alpaca's market-data host and bars path."""

    def get(
        self, url: str, headers: Mapping[str, str], timeout_seconds: float
    ) -> HttpResponse:
        if not url.startswith(f"{ALPACA_BARS_URL}?"):
            raise AlpacaRequestError("refusing request outside the historical bars endpoint")
        request = Request(url=url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status=int(response.status),
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as exc:
            return HttpResponse(
                status=int(exc.code),
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=exc.read(),
            )
        except URLError as exc:
            raise AlpacaRequestError(
                f"read-only Alpaca market-data request failed: {exc.reason}"
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise AlpacaRequestError(
                "read-only Alpaca market-data request failed before a response arrived"
            ) from exc


@dataclass(frozen=True, slots=True)
class RawArtifact:
    response_sha256: str
    request_sha256: str
    response_path: str
    manifest_path: str
    request_url: str
    retrieved_at: datetime
    status: int
    cache_hit: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "response_sha256": self.response_sha256,
            "request_sha256": self.request_sha256,
            "response_path": self.response_path,
            "manifest_path": self.manifest_path,
            "request_url": self.request_url,
            "retrieved_at": self.retrieved_at.isoformat().replace("+00:00", "Z"),
            "status": self.status,
            "cache_hit": self.cache_hit,
        }


class ImmutableRawStore:
    def __init__(self, root: Path, license_class: str) -> None:
        self._root = root
        self._license_class = license_class

    @staticmethod
    def _write_once(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise RuntimeError(f"immutable raw path collision: {path}")

    def preserve(
        self,
        *,
        request_url: str,
        response: HttpResponse,
        retrieved_at: datetime,
        timeframe: str,
        attempt: int,
    ) -> RawArtifact:
        response_hash = hashlib.sha256(response.body).hexdigest()
        response_path = (
            self._root / "responses" / response_hash[:2] / f"{response_hash}.json"
        )
        self._write_once(response_path, response.body)
        safe_headers = {
            str(key).lower(): str(value)
            for key, value in response.headers.items()
            if str(key).lower() in SAFE_RESPONSE_HEADERS
        }
        identity = {
            "adapter_version": ADAPTER_VERSION,
            "attempt": attempt,
            "license_class": self._license_class,
            "provider": ALPACA_PROVIDER_NAME,
            "request_url": request_url,
            "response_sha256": response_hash,
            "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
            "status": response.status,
            "timeframe": timeframe,
        }
        request_hash = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest = {
            **identity,
            "request_sha256": request_hash,
            "response_bytes": len(response.body),
            "response_headers": safe_headers,
            "credential_header_names": [
                "APCA-API-KEY-ID",
                "APCA-API-SECRET-KEY",
            ],
            "credential_values_persisted": False,
            "license_storage_warning": (
                "Local research retention only; do not commit or redistribute raw data."
            ),
        }
        manifest_bytes = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        manifest_path = self._root / "manifests" / f"{request_hash}.json"
        self._write_once(manifest_path, manifest_bytes)
        return RawArtifact(
            response_sha256=response_hash,
            request_sha256=request_hash,
            response_path=str(response_path),
            manifest_path=str(manifest_path),
            request_url=request_url,
            retrieved_at=retrieved_at,
            status=response.status,
        )

    def _request_cache_key(self, request_url: str, timeframe: str) -> str:
        identity = {
            "adapter_version": ADAPTER_VERSION,
            "license_class": self._license_class,
            "provider": ALPACA_PROVIDER_NAME,
            "request_url": request_url,
            "timeframe": timeframe,
        }
        return hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    def cache_success(self, artifact: RawArtifact, timeframe: str) -> None:
        if artifact.status != 200:
            raise RuntimeError("only successful historical responses may be cached")
        request_cache_key = self._request_cache_key(artifact.request_url, timeframe)
        stamp = artifact.retrieved_at.strftime("%Y%m%dT%H%M%S%fZ")
        record = {
            "schema_version": "alpaca-immutable-request-cache-v1",
            "adapter_version": ADAPTER_VERSION,
            "license_class": self._license_class,
            "provider": ALPACA_PROVIDER_NAME,
            "request_cache_key": request_cache_key,
            "request_url": artifact.request_url,
            "timeframe": timeframe,
            "retrieved_at": artifact.retrieved_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "status": artifact.status,
            "response_sha256": artifact.response_sha256,
            "response_path": str(
                Path(artifact.response_path).resolve().relative_to(
                    self._root.resolve()
                )
            ),
            "raw_manifest_path": str(
                Path(artifact.manifest_path).resolve().relative_to(
                    self._root.resolve()
                )
            ),
            "credential_values_persisted": False,
        }
        content = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        record_path = (
            self._root
            / "cache"
            / request_cache_key
            / f"{stamp}-{artifact.response_sha256}.json"
        )
        self._write_once(record_path, content)

    def load_cached(
        self,
        *,
        request_url: str,
        timeframe: str,
        now: datetime,
        max_age: timedelta,
    ) -> tuple[HttpResponse, RawArtifact] | None:
        request_cache_key = self._request_cache_key(request_url, timeframe)
        cache_dir = self._root / "cache" / request_cache_key
        if not cache_dir.exists():
            return None
        candidates = sorted(cache_dir.glob("*.json"), reverse=True)
        for record_path in candidates:
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"invalid immutable cache record: {record_path}"
                ) from exc
            if not isinstance(record, dict):
                raise RuntimeError(f"invalid immutable cache record: {record_path}")
            if (
                record.get("schema_version")
                != "alpaca-immutable-request-cache-v1"
                or record.get("adapter_version") != ADAPTER_VERSION
                or record.get("license_class") != self._license_class
                or record.get("provider") != ALPACA_PROVIDER_NAME
                or record.get("request_cache_key") != request_cache_key
                or record.get("request_url") != request_url
                or record.get("timeframe") != timeframe
                or record.get("status") != 200
            ):
                raise RuntimeError(
                    f"immutable cache identity mismatch: {record_path}"
                )
            try:
                retrieved_at = parse_timestamp(str(record["retrieved_at"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"invalid immutable cache timestamp: {record_path}"
                ) from exc
            age = now.astimezone(UTC) - retrieved_at
            if age < timedelta(0):
                raise RuntimeError(
                    f"immutable cache record is future-dated: {record_path}"
                )
            if age > max_age:
                continue
            response_path = (
                self._root / str(record.get("response_path", ""))
            ).resolve()
            manifest_path = (
                self._root / str(record.get("raw_manifest_path", ""))
            ).resolve()
            try:
                response_path.relative_to(self._root.resolve())
                manifest_path.relative_to(self._root.resolve())
            except ValueError as exc:
                raise RuntimeError(
                    f"immutable cache path escapes raw root: {record_path}"
                ) from exc
            if not response_path.is_file() or not manifest_path.is_file():
                raise RuntimeError(
                    f"immutable cache references a missing raw artifact: {record_path}"
                )
            response_body = response_path.read_bytes()
            response_hash = hashlib.sha256(response_body).hexdigest()
            if response_hash != record.get("response_sha256"):
                raise RuntimeError(
                    f"immutable cached response hash mismatch: {response_path}"
                )
            try:
                raw_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"invalid immutable cached raw manifest: {manifest_path}"
                ) from exc
            manifest_identity = {
                key: raw_manifest.get(key) if isinstance(raw_manifest, dict) else None
                for key in (
                    "adapter_version",
                    "attempt",
                    "license_class",
                    "provider",
                    "request_url",
                    "response_sha256",
                    "retrieved_at",
                    "status",
                    "timeframe",
                )
            }
            manifest_hash = hashlib.sha256(
                json.dumps(
                    manifest_identity, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            if (
                not isinstance(raw_manifest, dict)
                or raw_manifest.get("adapter_version") != ADAPTER_VERSION
                or raw_manifest.get("license_class") != self._license_class
                or raw_manifest.get("provider") != ALPACA_PROVIDER_NAME
                or raw_manifest.get("request_url") != request_url
                or raw_manifest.get("response_sha256") != response_hash
                or raw_manifest.get("retrieved_at") != record.get("retrieved_at")
                or raw_manifest.get("status") != 200
                or raw_manifest.get("timeframe") != timeframe
                or raw_manifest.get("request_sha256") != manifest_hash
                or manifest_path.stem != manifest_hash
            ):
                raise RuntimeError(
                    f"immutable cached raw manifest mismatch: {manifest_path}"
                )
            artifact = RawArtifact(
                response_sha256=response_hash,
                request_sha256=str(raw_manifest.get("request_sha256", "")),
                response_path=str(response_path),
                manifest_path=str(manifest_path),
                request_url=request_url,
                retrieved_at=retrieved_at,
                status=200,
                cache_hit=True,
            )
            return HttpResponse(status=200, headers={}, body=response_body), artifact
        return None


@dataclass(frozen=True, slots=True)
class AcceptedPage:
    payload: dict[str, object]
    artifact: RawArtifact


@dataclass(frozen=True, slots=True)
class AlpacaIngestionAudit:
    adapter_version: str
    requests: int
    network_requests: int
    cache_hits: int
    accepted_pages: int
    minute_raw_bars: int
    minute_normalized_bars: int
    minute_dropped_outside_configured_sessions: int
    daily_raw_bars: int
    daily_normalized_bars: int
    artifacts: tuple[RawArtifact, ...]

    @property
    def reconciled(self) -> bool:
        return (
            self.minute_raw_bars
            == self.minute_normalized_bars
            + self.minute_dropped_outside_configured_sessions
            and self.daily_raw_bars == self.daily_normalized_bars
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "adapter_version": self.adapter_version,
            "requests": self.requests,
            "network_requests": self.network_requests,
            "cache_hits": self.cache_hits,
            "accepted_pages": self.accepted_pages,
            "minute_raw_bars": self.minute_raw_bars,
            "minute_normalized_bars": self.minute_normalized_bars,
            "minute_dropped_outside_configured_sessions": (
                self.minute_dropped_outside_configured_sessions
            ),
            "daily_raw_bars": self.daily_raw_bars,
            "daily_normalized_bars": self.daily_normalized_bars,
            "raw_normalized_counts_reconciled": self.reconciled,
            "artifacts": [value.to_dict() for value in self.artifacts],
        }


class AlpacaHistoricalProvider:
    """Fetch a deliberately small historical bar sample; never touches trading APIs."""

    name = ALPACA_PROVIDER_NAME

    def __init__(
        self,
        config: AlpacaHistoricalConfig,
        credentials: AlpacaCredentials | None,
        *,
        cache_only: bool = False,
        transport: ReadOnlyHttpTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if credentials is None and not cache_only:
            raise AlpacaConfigurationError(
                "credentials are required unless cache_only is enabled"
            )
        self._config = config
        self._credentials = credentials
        self._cache_only = cache_only
        self._transport = transport or UrllibReadOnlyTransport()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._raw_store = ImmutableRawStore(
            config.raw_root, config.license_class
        )
        self._last_request_at: float | None = None
        self._artifacts: list[RawArtifact] = []
        self._network_requests = 0
        self._cache_hits = 0
        self._audit: AlpacaIngestionAudit | None = None

    @property
    def audit(self) -> AlpacaIngestionAudit:
        if self._audit is None:
            raise RuntimeError("provider audit is unavailable before load()")
        return self._audit

    @property
    def cache_only(self) -> bool:
        return self._cache_only

    def _pace(self) -> None:
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = (
                self._config.minimum_request_interval_seconds
                - (now - self._last_request_at)
            )
            if remaining > 0:
                self._sleeper(remaining)
        self._last_request_at = self._monotonic()

    def _retry_delay(self, response: HttpResponse, attempt: int) -> float:
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        retry_after = headers.get("retry-after")
        delay: float | None = None
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    retry_time = parsedate_to_datetime(retry_after).astimezone(UTC)
                    delay = max(0.0, (retry_time - self._clock().astimezone(UTC)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = None
        if delay is None and headers.get("x-ratelimit-reset"):
            try:
                reset = datetime.fromtimestamp(
                    float(headers["x-ratelimit-reset"]), tz=UTC
                )
                delay = max(0.0, (reset - self._clock().astimezone(UTC)).total_seconds())
            except (ValueError, OSError, OverflowError):
                delay = None
        if delay is None:
            delay = float(2 ** (attempt - 1))
        return min(delay, self._config.max_retry_delay_seconds)

    def _request_json(self, url: str, timeframe: str) -> AcceptedPage:
        if self._config.cache_enabled:
            cached = self._raw_store.load_cached(
                request_url=url,
                timeframe=timeframe,
                now=self._clock().astimezone(UTC),
                max_age=timedelta(hours=self._config.cache_max_age_hours),
            )
            if cached is not None:
                response, artifact = cached
                self._artifacts.append(artifact)
                self._cache_hits += 1
                return self._decode_accepted_page(response, artifact)
        if self._cache_only:
            raise AlpacaRequestError(
                "cache-only run found no valid cached response for the exact request"
            )
        if self._credentials is None:
            raise AssertionError("network request reached without credentials")
        for attempt in range(1, self._config.max_attempts + 1):
            self._pace()
            self._network_requests += 1
            try:
                response = self._transport.get(
                    url,
                    self._credentials.headers,
                    self._config.timeout_seconds,
                )
            except AlpacaRequestError as exc:
                if attempt < self._config.max_attempts:
                    self._sleeper(
                        min(
                            float(2 ** (attempt - 1)),
                            self._config.max_retry_delay_seconds,
                        )
                    )
                    continue
                raise AlpacaRequestError(
                    "Alpaca historical bars request failed after configured retries; "
                    "no response body was available to preserve"
                ) from exc
            retrieved_at = self._clock().astimezone(UTC)
            artifact = self._raw_store.preserve(
                request_url=url,
                response=response,
                retrieved_at=retrieved_at,
                timeframe=timeframe,
                attempt=attempt,
            )
            self._artifacts.append(artifact)
            if response.status == 200:
                page = self._decode_accepted_page(response, artifact)
                if self._config.cache_enabled:
                    self._raw_store.cache_success(artifact, timeframe)
                return page
            if response.status in RETRYABLE_STATUSES and attempt < self._config.max_attempts:
                self._sleeper(self._retry_delay(response, attempt))
                continue
            raise AlpacaRequestError(
                f"Alpaca historical bars returned HTTP {response.status}; raw response preserved"
            )
        raise AssertionError("unreachable request retry state")

    @staticmethod
    def _decode_accepted_page(
        response: HttpResponse, artifact: RawArtifact
    ) -> AcceptedPage:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AlpacaRequestError(
                "Alpaca historical bars returned invalid JSON; raw response preserved"
            ) from exc
        if not isinstance(payload, dict):
            raise AlpacaRequestError("Alpaca response root must be an object")
        return AcceptedPage(payload=payload, artifact=artifact)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    def _fetch_pages(
        self, timeframe: str, start: datetime, end: datetime
    ) -> tuple[AcceptedPage, ...]:
        base = {
            "adjustment": self._config.adjustment,
            "asof": "-",
            "currency": "USD",
            "end": self._timestamp(end),
            "feed": self._config.feed,
            "limit": str(self._config.page_limit),
            "sort": "asc",
            "start": self._timestamp(start),
            "symbols": ",".join(value.ticker for value in self._config.universe),
            "timeframe": timeframe,
        }
        pages: list[AcceptedPage] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            params = dict(base)
            if page_token is not None:
                params["page_token"] = page_token
            url = f"{ALPACA_BARS_URL}?{urlencode(params)}"
            page = self._request_json(url, timeframe)
            pages.append(page)
            token_value = page.payload.get("next_page_token")
            if token_value is None:
                break
            if not isinstance(token_value, str) or not token_value:
                raise AlpacaRequestError("invalid next_page_token in Alpaca response")
            if token_value in seen_tokens:
                raise AlpacaRequestError("repeated next_page_token in Alpaca response")
            seen_tokens.add(token_value)
            page_token = token_value
            if len(pages) >= self._config.max_pages_per_timeframe:
                raise AlpacaRequestError(
                    "historical pagination exceeded configured safety limit"
                )
        return tuple(pages)

    @staticmethod
    def _bar_lists(page: AcceptedPage) -> tuple[tuple[str, list[object]], ...]:
        bars = page.payload.get("bars")
        if not isinstance(bars, dict):
            raise AlpacaRequestError("Alpaca response has no bars object")
        result: list[tuple[str, list[object]]] = []
        for ticker, values in bars.items():
            if not isinstance(ticker, str) or not isinstance(values, list):
                raise AlpacaRequestError("Alpaca bars object has an invalid shape")
            result.append((ticker, values))
        return tuple(result)

    def _normalize_pages(
        self,
        pages: tuple[AcceptedPage, ...],
        timeframe: Timeframe,
    ) -> tuple[tuple[Bar, ...], int, int]:
        securities = {value.ticker: value for value in self._config.universe}
        normalized: list[Bar] = []
        raw_count = 0
        dropped = 0
        calendar = UsEquityCalendar()
        for page in pages:
            for ticker, values in self._bar_lists(page):
                security = securities.get(ticker)
                if security is None:
                    raise AlpacaRequestError(
                        f"response contains unrequested ticker {ticker!r}"
                    )
                for raw in values:
                    raw_count += 1
                    if not isinstance(raw, dict):
                        raise AlpacaRequestError("bar value must be an object")
                    try:
                        timestamp = parse_timestamp(str(raw["t"]))
                        open_price = float(raw["o"])
                        high = float(raw["h"])
                        low = float(raw["l"])
                        close = float(raw["c"])
                        volume = int(raw["v"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise AlpacaRequestError(
                            "bar is missing a required typed field; raw response preserved"
                        ) from exc
                    if timeframe is Timeframe.ONE_MINUTE:
                        session = calendar.classify(timestamp)
                        if session not in self._config.included_sessions:
                            dropped += 1
                            continue
                        available_at = timestamp + timedelta(minutes=1)
                    else:
                        session = Session.REGULAR
                        available_at = market_local_to_utc(
                            calendar.local_date(timestamp), wall_time(16, 1)
                        )
                    normalized.append(
                        Bar(
                            security_id=security.security_id,
                            timestamp=timestamp,
                            timeframe=timeframe,
                            session=session,
                            open=open_price,
                            high=high,
                            low=low,
                            close=close,
                            volume=volume,
                            available_at=available_at,
                            source="alpaca_market_data_historical_bars",
                            source_url=page.artifact.request_url,
                            feed=f"alpaca_{self._config.feed}",
                            adjustment=(
                                Adjustment.RAW
                                if self._config.adjustment == "raw"
                                else Adjustment.SPLIT_ADJUSTED
                            ),
                            vwap=(float(raw["vw"]) if raw.get("vw") is not None else None),
                            trade_count=(
                                int(raw["n"]) if raw.get("n") is not None else None
                            ),
                        )
                    )
        return (
            tuple(
                sorted(
                    normalized,
                    key=lambda value: (
                        value.security_id,
                        value.timestamp,
                        value.timeframe.value,
                    ),
                )
            ),
            raw_count,
            dropped,
        )

    def load(self) -> ProviderDataset:
        clock_value = self._clock()
        if clock_value.tzinfo is None or clock_value.utcoffset() is None:
            raise AlpacaConfigurationError("provider clock must be timezone-aware")
        now = clock_value.astimezone(UTC)
        self._config.validate(now)
        self._artifacts = []
        self._network_requests = 0
        self._cache_hits = 0
        minute_pages = self._fetch_pages(
            "1Min", self._config.minute_start, self._config.minute_end
        )
        daily_pages = self._fetch_pages(
            "1Day", self._config.daily_start, self._config.daily_end
        )
        minute_bars, minute_raw, minute_dropped = self._normalize_pages(
            minute_pages, Timeframe.ONE_MINUTE
        )
        daily_bars, daily_raw, daily_dropped = self._normalize_pages(
            daily_pages, Timeframe.ONE_DAY
        )
        if daily_dropped:
            raise AssertionError("daily normalization cannot drop session bars")
        retrieved_at = max(value.retrieved_at for value in self._artifacts)
        instruments = tuple(
            Instrument(
                security_id=value.security_id,
                ticker=value.ticker,
                exchange=value.exchange,
                security_type="common_stock",
                source="stage2_configured_universe_not_provider_reference_data",
                source_url="config/alpaca_historical.sample.json",
                available_at=retrieved_at,
                effective_from=self._config.daily_start,
                sector_status="not_provided_by_historical_bars_endpoint",
                shares_outstanding_status="not_provided_by_historical_bars_endpoint",
                free_float_status="not_reliably_available_from_alpaca_historical_bars",
                market_cap_status="not_provided_by_historical_bars_endpoint",
            )
            for value in self._config.universe
        )
        coverage = CoverageManifest(
            provider=self.name,
            dataset_kind="real_historical_bounded_research_sample",
            retrieved_at=retrieved_at,
            minute_dates=self._config.minute_session_dates,
            included_sessions=self._config.included_sessions,
            expected_security_ids=tuple(
                value.security_id for value in self._config.universe
            ),
            historical_universe_complete=False,
            consolidated_quotes=False,
            sector_classification_available=False,
            free_float_reliability="unavailable_from_bars_endpoint",
            notes=(
                f"Feed is {self._config.feed}; iex is single-venue and sip requires entitlement.",
                "Universe is a configured small sample, not a point-in-time listing master.",
                "Historical bars endpoint supplies no bid/ask, halt, float, sector, or delisting history.",
                "Symbol mapping is disabled with asof=- to avoid current rename backfills.",
                f"Raw responses retained under license class {self._config.license_class}.",
            ),
        )
        self._audit = AlpacaIngestionAudit(
            adapter_version=ADAPTER_VERSION,
            requests=len(self._artifacts),
            network_requests=self._network_requests,
            cache_hits=self._cache_hits,
            accepted_pages=len(minute_pages) + len(daily_pages),
            minute_raw_bars=minute_raw,
            minute_normalized_bars=len(minute_bars),
            minute_dropped_outside_configured_sessions=minute_dropped,
            daily_raw_bars=daily_raw,
            daily_normalized_bars=len(daily_bars),
            artifacts=tuple(self._artifacts),
        )
        return ProviderDataset(
            instruments=instruments,
            one_minute_bars=minute_bars,
            daily_bars=daily_bars,
            corporate_actions=(),
            halts=(),
            coverage=coverage,
        )
