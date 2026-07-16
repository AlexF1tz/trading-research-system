"""Replaceable provider interface and strict CSV-directory adapter."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import (
    ActionType,
    Adjustment,
    Bar,
    CorporateAction,
    CoverageManifest,
    Exchange,
    Halt,
    Instrument,
    ProviderDataset,
    Session,
    Timeframe,
)


@runtime_checkable
class MarketDataProvider(Protocol):
    """A provider returns normalized data plus an explicit coverage manifest."""

    @property
    def name(self) -> str: ...

    def load(self) -> ProviderDataset: ...


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp lacks timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(value: str | None) -> datetime | None:
    return parse_timestamp(value) if value and value.strip() else None


def _optional_int(value: str | None) -> int | None:
    return int(value) if value and value.strip() else None


def _optional_float(value: str | None) -> float | None:
    return float(value) if value and value.strip() else None


def _optional_bool(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "y"})


class CsvDirectoryProvider:
    """Load a provider export from documented CSV files.

    Required files are `metadata.json`, `instruments.csv`, `bars_1m.csv`, and
    `bars_1d.csv`.  Corporate actions and halts are optional empty files.  The
    adapter performs parsing only; quality policy remains in `quality.py`.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._metadata = self._read_metadata()

    @property
    def name(self) -> str:
        return str(self._metadata["provider"])

    def _read_metadata(self) -> dict[str, object]:
        path = self._root / "metadata.json"
        if not path.exists():
            raise FileNotFoundError(f"missing provider metadata: {path}")
        result = json.loads(path.read_text(encoding="utf-8"))
        required = {"provider", "dataset_kind", "retrieved_at", "minute_dates"}
        missing = sorted(required.difference(result))
        if missing:
            raise ValueError(f"metadata missing required fields: {missing}")
        return result

    def _rows(self, filename: str, *, required: bool = True) -> list[dict[str, str]]:
        path = self._root / filename
        if not path.exists():
            if required:
                raise FileNotFoundError(f"missing provider file: {path}")
            return []
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def _instruments(self) -> tuple[Instrument, ...]:
        values: list[Instrument] = []
        for row in self._rows("instruments.csv"):
            values.append(
                Instrument(
                    security_id=row["security_id"],
                    ticker=row["ticker"],
                    exchange=Exchange(row["exchange"]),
                    security_type=row["security_type"],
                    source=row["source"],
                    source_url=row["source_url"],
                    available_at=parse_timestamp(row["available_at"]),
                    effective_from=parse_timestamp(row["effective_from"]),
                    effective_to=_optional_timestamp(row.get("effective_to")),
                    sector=row.get("sector") or None,
                    sector_available_at=_optional_timestamp(row.get("sector_available_at")),
                    sector_status=row.get("sector_status") or "unavailable",
                    shares_outstanding=_optional_int(row.get("shares_outstanding")),
                    shares_outstanding_as_of=_optional_timestamp(row.get("shares_outstanding_as_of")),
                    shares_outstanding_available_at=_optional_timestamp(row.get("shares_outstanding_available_at")),
                    shares_outstanding_status=row.get("shares_outstanding_status") or "unavailable",
                    free_float=_optional_int(row.get("free_float")),
                    free_float_as_of=_optional_timestamp(row.get("free_float_as_of")),
                    free_float_available_at=_optional_timestamp(row.get("free_float_available_at")),
                    free_float_status=row.get("free_float_status") or "not_reliably_available_from_free_sources",
                    reported_market_cap=_optional_float(row.get("reported_market_cap")),
                    market_cap_as_of=_optional_timestamp(row.get("market_cap_as_of")),
                    market_cap_status=row.get("market_cap_status") or "derived_only",
                    is_delisted=_optional_bool(row.get("is_delisted")),
                    delisted_at=_optional_timestamp(row.get("delisted_at")),
                )
            )
        return tuple(values)

    def _bars(self, filename: str, timeframe: Timeframe) -> tuple[Bar, ...]:
        values: list[Bar] = []
        for row in self._rows(filename):
            values.append(
                Bar(
                    security_id=row["security_id"],
                    timestamp=parse_timestamp(row["timestamp"]),
                    timeframe=timeframe,
                    session=Session(row["session"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                    available_at=parse_timestamp(row["available_at"]),
                    source=row["source"],
                    source_url=row["source_url"],
                    feed=row["feed"],
                    adjustment=Adjustment(row.get("adjustment") or "raw"),
                    vwap=_optional_float(row.get("vwap")),
                    bid=_optional_float(row.get("bid")),
                    ask=_optional_float(row.get("ask")),
                    trade_count=_optional_int(row.get("trade_count")),
                )
            )
        return tuple(values)

    def _actions(self) -> tuple[CorporateAction, ...]:
        values: list[CorporateAction] = []
        for row in self._rows("corporate_actions.csv", required=False):
            values.append(
                CorporateAction(
                    security_id=row["security_id"],
                    action_type=ActionType(row["action_type"]),
                    effective_at=parse_timestamp(row["effective_at"]),
                    announced_at=_optional_timestamp(row.get("announced_at")),
                    available_at=parse_timestamp(row["available_at"]),
                    source=row["source"],
                    source_url=row["source_url"],
                    split_ratio=_optional_float(row.get("split_ratio")),
                    cash_amount=_optional_float(row.get("cash_amount")),
                    old_ticker=row.get("old_ticker") or None,
                    new_ticker=row.get("new_ticker") or None,
                )
            )
        return tuple(values)

    def _halts(self) -> tuple[Halt, ...]:
        values: list[Halt] = []
        for row in self._rows("halts.csv", required=False):
            values.append(
                Halt(
                    security_id=row["security_id"],
                    started_at=parse_timestamp(row["started_at"]),
                    resumed_at=_optional_timestamp(row.get("resumed_at")),
                    reason=row["reason"],
                    available_at=parse_timestamp(row["available_at"]),
                    source=row["source"],
                    source_url=row["source_url"],
                )
            )
        return tuple(values)

    def load(self) -> ProviderDataset:
        metadata = self._metadata
        sessions = metadata.get("included_sessions", ["premarket", "regular"])
        coverage = CoverageManifest(
            provider=str(metadata["provider"]),
            dataset_kind=str(metadata["dataset_kind"]),
            retrieved_at=parse_timestamp(str(metadata["retrieved_at"])),
            minute_dates=tuple(date.fromisoformat(value) for value in metadata["minute_dates"]),  # type: ignore[index]
            included_sessions=tuple(Session(value) for value in sessions),  # type: ignore[arg-type]
            expected_security_ids=tuple(str(value) for value in metadata.get("expected_security_ids", [])),
            historical_universe_complete=bool(metadata.get("historical_universe_complete", False)),
            consolidated_quotes=bool(metadata.get("consolidated_quotes", False)),
            sector_classification_available=bool(metadata.get("sector_classification_available", False)),
            free_float_reliability=str(metadata.get("free_float_reliability", "unknown")),
            notes=tuple(str(value) for value in metadata.get("notes", [])),
        )
        return ProviderDataset(
            instruments=self._instruments(),
            one_minute_bars=self._bars("bars_1m.csv", Timeframe.ONE_MINUTE),
            daily_bars=self._bars("bars_1d.csv", Timeframe.ONE_DAY),
            corporate_actions=self._actions(),
            halts=self._halts(),
            coverage=coverage,
        )

