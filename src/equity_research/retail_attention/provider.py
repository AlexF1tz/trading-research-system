"""Replaceable retail-attention providers and strict normalized JSONL input."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import (
    AccessMethod,
    AttentionSource,
    CatalystReference,
    ContentStorage,
    EngagementSnapshot,
    Mention,
    MonitoredSecurity,
    PriceMoveContext,
    SourceBatch,
    SourceDescriptor,
)


@runtime_checkable
class AttentionSourceProvider(Protocol):
    @property
    def name(self) -> str: ...

    def load(self) -> SourceBatch: ...


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp lacks timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(value: object) -> datetime | None:
    return parse_timestamp(str(value)) if value else None


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


class JsonlAttentionProvider:
    """Load an already-authorized normalized provider/export directory.

    This adapter does not fetch or scrape any platform. The directory requires
    `metadata.json` and `mentions.jsonl`; catalyst and price context files are
    optional. Source access and permitted analysis fields must be declared in
    metadata and are validated before scoring.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        metadata_path = root / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"missing attention metadata: {metadata_path}")
        self._metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        for field_name in (
            "provider",
            "dataset_kind",
            "fetched_at",
            "monitored_securities",
            "sources",
        ):
            if field_name not in self._metadata:
                raise ValueError(f"metadata missing required field: {field_name}")

    @property
    def name(self) -> str:
        return str(self._metadata["provider"])

    @staticmethod
    def _engagement(values: object) -> tuple[EngagementSnapshot, ...]:
        if not isinstance(values, list):
            return ()
        return tuple(
            EngagementSnapshot(
                observed_at=parse_timestamp(str(value["observed_at"])),
                available_at=parse_timestamp(str(value["available_at"])),
                likes=_optional_int(value.get("likes")),
                replies=_optional_int(value.get("replies")),
                reposts=_optional_int(value.get("reposts")),
                views=_optional_int(value.get("views")),
            )
            for value in values
            if isinstance(value, dict)
        )

    def _mentions(self) -> tuple[Mention, ...]:
        path = self._root / "mentions.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"missing attention mentions: {path}")
        mentions: list[Mention] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    mentions.append(
                        Mention(
                            mention_id=str(row["mention_id"]),
                            security_id=str(row["security_id"]),
                            ticker=str(row["ticker"]),
                            source=AttentionSource(str(row["source"])),
                            source_record_id=str(row["source_record_id"]),
                            source_url=str(row["source_url"]),
                            published_at=parse_timestamp(str(row["published_at"])),
                            first_seen_at=parse_timestamp(str(row["first_seen_at"])),
                            ingested_at=parse_timestamp(str(row["ingested_at"])),
                            available_at=parse_timestamp(str(row["available_at"])),
                            content_hash=str(row["content_hash"]),
                            text=(
                                str(row["text"])
                                if row.get("text") is not None
                                else None
                            ),
                            author_key=(
                                str(row["author_key"])
                                if row.get("author_key") is not None
                                else None
                            ),
                            is_repost=(
                                bool(row["is_repost"])
                                if row.get("is_repost") is not None
                                else None
                            ),
                            repost_of_source_record_id=(
                                str(row["repost_of_source_record_id"])
                                if row.get("repost_of_source_record_id") is not None
                                else None
                            ),
                            outbound_urls=tuple(
                                str(value) for value in row.get("outbound_urls", [])
                            ),
                            linked_catalyst_urls=tuple(
                                str(value)
                                for value in row.get("linked_catalyst_urls", [])
                            ),
                            engagement_snapshots=self._engagement(
                                row.get("engagement_snapshots")
                            ),
                            account_quality_score=_optional_float(
                                row.get("account_quality_score")
                            ),
                            account_quality_basis=(
                                str(row["account_quality_basis"])
                                if row.get("account_quality_basis") is not None
                                else None
                            ),
                            affiliate_or_paid_promotion=(
                                bool(row["affiliate_or_paid_promotion"])
                                if row.get("affiliate_or_paid_promotion") is not None
                                else None
                            ),
                            language=(
                                str(row["language"])
                                if row.get("language") is not None
                                else None
                            ),
                        )
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        f"invalid mentions.jsonl line {line_number}: {error}"
                    ) from error
        return tuple(mentions)

    def _catalysts(self) -> tuple[CatalystReference, ...]:
        path = self._root / "catalysts.jsonl"
        if not path.exists():
            return ()
        values: list[CatalystReference] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    values.append(
                        CatalystReference(
                            catalyst_id=str(row["catalyst_id"]),
                            security_id=str(row["security_id"]),
                            ticker=str(row["ticker"]),
                            source_url=str(row["source_url"]),
                            first_public_at=parse_timestamp(
                                str(row["first_public_at"])
                            ),
                            available_at=parse_timestamp(str(row["available_at"])),
                            is_primary_source=bool(row["is_primary_source"]),
                        )
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        f"invalid catalysts.jsonl line {line_number}: {error}"
                    ) from error
        return tuple(values)

    def _price_context(self) -> tuple[PriceMoveContext, ...]:
        path = self._root / "price_context.jsonl"
        if not path.exists():
            return ()
        values: list[PriceMoveContext] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    values.append(
                        PriceMoveContext(
                            security_id=str(row["security_id"]),
                            ticker=str(row["ticker"]),
                            reference_at=parse_timestamp(str(row["reference_at"])),
                            observed_at=parse_timestamp(str(row["observed_at"])),
                            available_at=parse_timestamp(str(row["available_at"])),
                            cumulative_return_pct=float(
                                row["cumulative_return_pct"]
                            ),
                            source_url=str(row["source_url"]),
                        )
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        f"invalid price_context.jsonl line {line_number}: {error}"
                    ) from error
        return tuple(values)

    def load(self) -> SourceBatch:
        sources = self._metadata["sources"]
        securities = self._metadata["monitored_securities"]
        if not isinstance(sources, list) or not isinstance(securities, list):
            raise ValueError("sources and monitored_securities must be arrays")
        return SourceBatch(
            provider=self.name,
            dataset_kind=str(self._metadata["dataset_kind"]),
            fetched_at=parse_timestamp(str(self._metadata["fetched_at"])),
            monitored_securities=tuple(
                MonitoredSecurity(
                    security_id=str(value["security_id"]),
                    ticker=str(value["ticker"]),
                )
                for value in securities
                if isinstance(value, dict)
            ),
            source_descriptors=tuple(
                SourceDescriptor(
                    source=AttentionSource(str(value["source"])),
                    access_method=AccessMethod(str(value["access_method"])),
                    collection_authorization_confirmed=bool(
                        value["collection_authorization_confirmed"]
                    ),
                    terms_url=(
                        str(value["terms_url"])
                        if value.get("terms_url") is not None
                        else None
                    ),
                    terms_reviewed_at=_optional_timestamp(
                        value.get("terms_reviewed_at")
                    ),
                    rate_limit_policy=str(value["rate_limit_policy"]),
                    coverage_started_at=parse_timestamp(
                        str(value["coverage_started_at"])
                    ),
                    coverage_ended_at=_optional_timestamp(
                        value.get("coverage_ended_at")
                    ),
                    text_analysis_permitted=bool(
                        value.get("text_analysis_permitted", False)
                    ),
                    content_storage=ContentStorage(
                        str(value.get("content_storage", "none"))
                    ),
                    author_metrics_permitted=bool(
                        value.get("author_metrics_permitted", False)
                    ),
                    coverage_note=str(value.get("coverage_note", "")),
                )
                for value in sources
                if isinstance(value, dict)
            ),
            mentions=self._mentions(),
            catalyst_references=self._catalysts(),
            price_context=self._price_context(),
            notes=tuple(str(value) for value in self._metadata.get("notes", [])),
        )
