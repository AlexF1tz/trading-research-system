"""Replaceable catalyst-source providers and strict JSONL ingestion."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import (
    NumericalDetail,
    SourceBatch,
    SourceDocument,
    SourceKind,
)


@runtime_checkable
class CatalystSourceProvider(Protocol):
    @property
    def name(self) -> str: ...

    def load(self) -> SourceBatch: ...


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp lacks timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


class JsonlCatalystProvider:
    """Load normalized source documents from an adapter export.

    The directory contains `metadata.json` and `documents.jsonl`.  It can hold
    metadata-only/excerpt records when full-text storage is not licensed.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        metadata_path = root / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"missing catalyst metadata: {metadata_path}")
        self._metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        for field_name in ("provider", "dataset_kind", "fetched_at"):
            if field_name not in self._metadata:
                raise ValueError(f"metadata missing required field: {field_name}")

    @property
    def name(self) -> str:
        return str(self._metadata["provider"])

    @staticmethod
    def _numerical_details(values: object) -> tuple[NumericalDetail, ...]:
        if not isinstance(values, list):
            return ()
        return tuple(
            NumericalDetail(
                kind=str(value["kind"]),
                label=str(value["label"]),
                raw_text=str(value["raw_text"]),
                value=float(value["value"]),
                unit=str(value["unit"]),
            )
            for value in values
            if isinstance(value, dict)
        )

    def load(self) -> SourceBatch:
        path = self._root / "documents.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"missing catalyst documents: {path}")
        documents: list[SourceDocument] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    documents.append(
                        SourceDocument(
                            document_id=str(row["document_id"]),
                            ticker=str(row["ticker"]),
                            issuer_id=(
                                str(row["issuer_id"])
                                if row.get("issuer_id") is not None
                                else None
                            ),
                            title=str(row["title"]),
                            text=str(row.get("text", "")),
                            source_url=str(row["source_url"]),
                            source_kind=SourceKind(str(row["source_kind"])),
                            published_at=parse_timestamp(str(row["published_at"])),
                            first_public_at=parse_timestamp(
                                str(row["first_public_at"])
                            ),
                            first_seen_at=parse_timestamp(str(row["first_seen_at"])),
                            ingested_at=parse_timestamp(str(row["ingested_at"])),
                            available_at=parse_timestamp(str(row["available_at"])),
                            source_timestamp_verified=bool(
                                row.get("source_timestamp_verified", False)
                            ),
                            source_record_id=(
                                str(row["source_record_id"])
                                if row.get("source_record_id") is not None
                                else None
                            ),
                            form_type=(
                                str(row["form_type"])
                                if row.get("form_type") is not None
                                else None
                            ),
                            form_items=tuple(
                                str(value) for value in row.get("form_items", [])
                            ),
                            accession_number=(
                                str(row["accession_number"])
                                if row.get("accession_number") is not None
                                else None
                            ),
                            expected_catalyst_date=(
                                date.fromisoformat(str(row["expected_catalyst_date"]))
                                if row.get("expected_catalyst_date")
                                else None
                            ),
                            related_primary_document_id=(
                                str(row["related_primary_document_id"])
                                if row.get("related_primary_document_id") is not None
                                else None
                            ),
                            structured_numerical_details=self._numerical_details(
                                row.get("structured_numerical_details")
                            ),
                        )
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        f"invalid documents.jsonl line {line_number}: {error}"
                    ) from error
        return SourceBatch(
            provider=self.name,
            dataset_kind=str(self._metadata["dataset_kind"]),
            fetched_at=parse_timestamp(str(self._metadata["fetched_at"])),
            documents=tuple(documents),
            notes=tuple(str(value) for value in self._metadata.get("notes", [])),
        )


class CompositeCatalystProvider:
    """Merge source batches without hiding their original provenance."""

    def __init__(self, *providers: CatalystSourceProvider) -> None:
        if not providers:
            raise ValueError("at least one catalyst provider is required")
        self._providers = providers

    @property
    def name(self) -> str:
        return "+".join(provider.name for provider in self._providers)

    def load(self) -> SourceBatch:
        batches = tuple(provider.load() for provider in self._providers)
        return SourceBatch(
            provider=self.name,
            dataset_kind="composite:" + "+".join(
                batch.dataset_kind for batch in batches
            ),
            fetched_at=max(batch.fetched_at for batch in batches),
            documents=tuple(
                document for batch in batches for document in batch.documents
            ),
            notes=tuple(note for batch in batches for note in batch.notes),
        )

