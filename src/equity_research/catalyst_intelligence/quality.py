"""Source and timestamp validation for catalyst documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from urllib.parse import urlparse

from .contracts import SourceBatch, SourceKind, SourceTier


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class CatalystQualityIssue:
    code: str
    severity: Severity
    message: str
    document_id: str | None = None
    timestamp: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "document_id": self.document_id,
            "timestamp": (
                self.timestamp.isoformat().replace("+00:00", "Z")
                if self.timestamp is not None
                else None
            ),
        }


class CatalystQualityError(RuntimeError):
    def __init__(self, issues: tuple[CatalystQualityIssue, ...]) -> None:
        self.issues = issues
        super().__init__(f"catalyst source quality failed with {len(issues)} error(s)")


def _is_utc(value: datetime) -> bool:
    return (
        value.tzinfo is not None
        and value.utcoffset() is not None
        and value.utcoffset() == timedelta(0)
    )


def _valid_source_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "fixture"} and bool(
        parsed.netloc or parsed.scheme == "fixture"
    )


def run_source_quality_checks(batch: SourceBatch) -> tuple[CatalystQualityIssue, ...]:
    issues: list[CatalystQualityIssue] = []
    if not _is_utc(batch.fetched_at):
        issues.append(
            CatalystQualityIssue(
                "TIMEZONE_NOT_UTC",
                Severity.ERROR,
                "batch fetched_at must be timezone-aware UTC",
            )
        )

    seen_ids: set[str] = set()
    known_ids = {document.document_id for document in batch.documents}
    for document in batch.documents:
        if document.document_id in seen_ids:
            issues.append(
                CatalystQualityIssue(
                    "DUPLICATE_DOCUMENT_ID",
                    Severity.ERROR,
                    "document ID occurs more than once in the source batch",
                    document.document_id,
                )
            )
        seen_ids.add(document.document_id)
        for field_name, timestamp in (
            ("published_at", document.published_at),
            ("first_public_at", document.first_public_at),
            ("first_seen_at", document.first_seen_at),
            ("ingested_at", document.ingested_at),
            ("available_at", document.available_at),
        ):
            if not _is_utc(timestamp):
                issues.append(
                    CatalystQualityIssue(
                        "TIMEZONE_NOT_UTC",
                        Severity.ERROR,
                        f"{field_name} must be timezone-aware UTC",
                        document.document_id,
                        timestamp,
                    )
                )
        if (
            _is_utc(document.first_public_at)
            and _is_utc(document.first_seen_at)
            and document.first_public_at > document.first_seen_at
        ):
            issues.append(
                CatalystQualityIssue(
                    "PUBLIC_TIME_AFTER_DISCOVERY",
                    Severity.ERROR,
                    "first-public timestamp is later than first-seen timestamp",
                    document.document_id,
                    document.first_public_at,
                )
            )
        if (
            _is_utc(document.published_at)
            and _is_utc(document.first_seen_at)
            and document.published_at > document.first_seen_at
        ):
            issues.append(
                CatalystQualityIssue(
                    "PUBLISHED_TIME_AFTER_DISCOVERY",
                    Severity.ERROR,
                    "published timestamp is later than first-seen timestamp",
                    document.document_id,
                    document.published_at,
                )
            )
        if (
            _is_utc(document.ingested_at)
            and _is_utc(document.first_seen_at)
            and document.ingested_at < document.first_seen_at
        ):
            issues.append(
                CatalystQualityIssue(
                    "INGESTED_BEFORE_DISCOVERY",
                    Severity.ERROR,
                    "ingested_at precedes first_seen_at",
                    document.document_id,
                    document.ingested_at,
                )
            )
        if (
            _is_utc(document.available_at)
            and _is_utc(document.first_public_at)
            and document.available_at < document.first_public_at
        ):
            issues.append(
                CatalystQualityIssue(
                    "AVAILABLE_BEFORE_PUBLIC",
                    Severity.ERROR,
                    "available_at precedes first-public timestamp",
                    document.document_id,
                    document.available_at,
                )
            )
        if (
            not document.source_timestamp_verified
            and _is_utc(document.available_at)
            and _is_utc(document.first_seen_at)
            and document.available_at < document.first_seen_at
        ):
            issues.append(
                CatalystQualityIssue(
                    "UNVERIFIED_TIME_USED_EARLY",
                    Severity.ERROR,
                    "unverified source timestamp cannot precede system discovery",
                    document.document_id,
                    document.available_at,
                )
            )
        if (
            _is_utc(document.ingested_at)
            and _is_utc(batch.fetched_at)
            and document.ingested_at > batch.fetched_at
        ):
            issues.append(
                CatalystQualityIssue(
                    "DOCUMENT_AFTER_BATCH_FETCH",
                    Severity.ERROR,
                    "document ingestion is later than batch fetch time",
                    document.document_id,
                    document.ingested_at,
                )
            )
        if not document.ticker or document.ticker != document.ticker.upper():
            issues.append(
                CatalystQualityIssue(
                    "INVALID_TICKER",
                    Severity.ERROR,
                    "ticker must be a non-empty normalized uppercase label",
                    document.document_id,
                )
            )
        if not document.title.strip():
            issues.append(
                CatalystQualityIssue(
                    "MISSING_TITLE",
                    Severity.ERROR,
                    "source document has no title",
                    document.document_id,
                )
            )
        if not document.text.strip():
            issues.append(
                CatalystQualityIssue(
                    "MISSING_TEXT_EXTRACT",
                    Severity.WARNING,
                    "no licensed text or extract was supplied; classification coverage is limited",
                    document.document_id,
                )
            )
        if not _valid_source_url(document.source_url):
            issues.append(
                CatalystQualityIssue(
                    "INVALID_SOURCE_URL",
                    Severity.ERROR,
                    "source URL must be http(s) or an explicit fixture URI",
                    document.document_id,
                )
            )
        parsed = urlparse(document.source_url)
        if (
            document.source_kind is SourceKind.SEC_FILING
            and parsed.scheme != "fixture"
            and not parsed.netloc.lower().endswith("sec.gov")
        ):
            issues.append(
                CatalystQualityIssue(
                    "SEC_SOURCE_DOMAIN_MISMATCH",
                    Severity.ERROR,
                    "SEC filing source URL is not on sec.gov",
                    document.document_id,
                )
            )
        if document.source_kind is SourceKind.SEC_FILING and not document.form_type:
            issues.append(
                CatalystQualityIssue(
                    "SEC_FORM_MISSING",
                    Severity.WARNING,
                    "SEC source has no form type",
                    document.document_id,
                )
            )
        if (
            document.source_tier is SourceTier.PRIMARY
            and not document.source_timestamp_verified
        ):
            issues.append(
                CatalystQualityIssue(
                    "PRIMARY_TIMESTAMP_UNVERIFIED",
                    Severity.WARNING,
                    "primary-source content is usable but its declared public time is unverified",
                    document.document_id,
                )
            )
        if (
            document.related_primary_document_id is not None
            and document.related_primary_document_id not in known_ids
        ):
            issues.append(
                CatalystQualityIssue(
                    "CORROBORATING_DOCUMENT_MISSING",
                    Severity.WARNING,
                    "referenced primary document is absent from this batch",
                    document.document_id,
                )
            )
    return tuple(issues)
