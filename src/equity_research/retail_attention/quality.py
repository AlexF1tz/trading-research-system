"""Quality, provenance, permission, and timestamp checks for attention data."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from math import isfinite
from urllib.parse import urlparse

from .contracts import AccessMethod, ContentStorage, SourceBatch


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class AttentionQualityIssue:
    code: str
    severity: Severity
    message: str
    record_id: str | None = None
    timestamp: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "record_id": self.record_id,
            "timestamp": (
                self.timestamp.isoformat().replace("+00:00", "Z")
                if self.timestamp is not None
                else None
            ),
        }


class AttentionQualityError(RuntimeError):
    def __init__(self, issues: tuple[AttentionQualityIssue, ...]) -> None:
        self.issues = issues
        super().__init__(f"attention source quality failed with {len(issues)} error(s)")


def _is_utc(value: datetime) -> bool:
    return (
        value.tzinfo is not None
        and value.utcoffset() is not None
        and value.utcoffset() == timedelta(0)
    )


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "fixture"} and bool(
        parsed.netloc or parsed.scheme == "fixture"
    )


def _timestamp_issue(
    issues: list[AttentionQualityIssue],
    name: str,
    value: datetime,
    record_id: str | None,
) -> None:
    if not _is_utc(value):
        issues.append(
            AttentionQualityIssue(
                "TIMEZONE_NOT_UTC",
                Severity.ERROR,
                f"{name} must be timezone-aware UTC",
                record_id,
                value,
            )
        )


def run_attention_quality_checks(
    batch: SourceBatch,
) -> tuple[AttentionQualityIssue, ...]:
    issues: list[AttentionQualityIssue] = []
    _timestamp_issue(issues, "batch fetched_at", batch.fetched_at, None)

    descriptor_by_source = {}
    for descriptor in batch.source_descriptors:
        if descriptor.source in descriptor_by_source:
            issues.append(
                AttentionQualityIssue(
                    "DUPLICATE_SOURCE_DESCRIPTOR",
                    Severity.ERROR,
                    "a source has more than one access declaration",
                    descriptor.source.value,
                )
            )
        descriptor_by_source[descriptor.source] = descriptor
        if not descriptor.collection_authorization_confirmed:
            issues.append(
                AttentionQualityIssue(
                    "COLLECTION_NOT_AUTHORIZED",
                    Severity.ERROR,
                    "source collection authorization has not been confirmed",
                    descriptor.source.value,
                )
            )
        if descriptor.terms_reviewed_at is not None:
            _timestamp_issue(
                issues,
                "terms_reviewed_at",
                descriptor.terms_reviewed_at,
                descriptor.source.value,
            )
            if (
                _is_utc(descriptor.terms_reviewed_at)
                and _is_utc(batch.fetched_at)
                and descriptor.terms_reviewed_at > batch.fetched_at
            ):
                issues.append(
                    AttentionQualityIssue(
                        "TERMS_REVIEW_AFTER_BATCH_FETCH",
                        Severity.ERROR,
                        "terms review timestamp is later than batch fetch",
                        descriptor.source.value,
                    )
                )
        _timestamp_issue(
            issues,
            "coverage_started_at",
            descriptor.coverage_started_at,
            descriptor.source.value,
        )
        if descriptor.coverage_ended_at is not None:
            _timestamp_issue(
                issues,
                "coverage_ended_at",
                descriptor.coverage_ended_at,
                descriptor.source.value,
            )
            if (
                _is_utc(descriptor.coverage_started_at)
                and _is_utc(descriptor.coverage_ended_at)
                and descriptor.coverage_ended_at <= descriptor.coverage_started_at
            ):
                issues.append(
                    AttentionQualityIssue(
                        "INVALID_SOURCE_COVERAGE_INTERVAL",
                        Severity.ERROR,
                        "source coverage end must follow its start",
                        descriptor.source.value,
                    )
                )
        if (
            descriptor.access_method is not AccessMethod.ENGINEERING_FIXTURE
            and not descriptor.terms_url
        ):
            issues.append(
                AttentionQualityIssue(
                    "TERMS_URL_MISSING",
                    Severity.ERROR,
                    "non-fixture collection must preserve the governing terms URL",
                    descriptor.source.value,
                )
            )
        if not descriptor.coverage_note.strip():
            issues.append(
                AttentionQualityIssue(
                    "SOURCE_COVERAGE_UNDOCUMENTED",
                    Severity.WARNING,
                    "source declaration has no coverage limitation note",
                    descriptor.source.value,
                )
            )
        if not descriptor.rate_limit_policy.strip():
            issues.append(
                AttentionQualityIssue(
                    "RATE_LIMIT_POLICY_MISSING",
                    Severity.ERROR,
                    "source declaration must document its enforced rate-limit policy",
                    descriptor.source.value,
                )
            )

    seen_security_ids: set[str] = set()
    security_tickers: dict[str, str] = {}
    for security in batch.monitored_securities:
        if security.security_id in seen_security_ids:
            issues.append(
                AttentionQualityIssue(
                    "DUPLICATE_MONITORED_SECURITY",
                    Severity.ERROR,
                    "security appears more than once in the monitored universe",
                    security.security_id,
                )
            )
        seen_security_ids.add(security.security_id)
        security_tickers[security.security_id] = security.ticker
        if not security.security_id or not security.ticker or security.ticker.upper() != security.ticker:
            issues.append(
                AttentionQualityIssue(
                    "INVALID_MONITORED_SECURITY",
                    Severity.ERROR,
                    "security ID and normalized uppercase ticker are required",
                    security.security_id,
                )
            )

    seen_ids: set[str] = set()
    seen_source_ids: set[tuple[object, str]] = set()
    for mention in batch.mentions:
        if mention.mention_id in seen_ids:
            issues.append(
                AttentionQualityIssue(
                    "DUPLICATE_MENTION_ID",
                    Severity.ERROR,
                    "mention ID occurs more than once",
                    mention.mention_id,
                )
            )
        seen_ids.add(mention.mention_id)
        source_key = (mention.source, mention.source_record_id)
        if source_key in seen_source_ids:
            issues.append(
                AttentionQualityIssue(
                    "DUPLICATE_SOURCE_RECORD",
                    Severity.ERROR,
                    "source-native record occurs more than once",
                    mention.mention_id,
                )
            )
        seen_source_ids.add(source_key)

        descriptor = descriptor_by_source.get(mention.source)
        if descriptor is None:
            issues.append(
                AttentionQualityIssue(
                    "SOURCE_ACCESS_UNDECLARED",
                    Severity.ERROR,
                    "mention source has no access/terms declaration",
                    mention.mention_id,
                )
            )
        elif mention.text is not None and not descriptor.text_analysis_permitted:
            issues.append(
                AttentionQualityIssue(
                    "TEXT_USE_NOT_PERMITTED",
                    Severity.ERROR,
                    "text was supplied where analysis permission is not declared",
                    mention.mention_id,
                )
            )
        elif mention.text is not None and descriptor.content_storage in {
            ContentStorage.NONE,
            ContentStorage.HASH_ONLY,
        }:
            issues.append(
                AttentionQualityIssue(
                    "TEXT_STORAGE_NOT_PERMITTED",
                    Severity.ERROR,
                    "stored text exceeds the declared content-storage permission",
                    mention.mention_id,
                )
            )
        if (
            descriptor is not None
            and mention.account_quality_score is not None
            and not descriptor.author_metrics_permitted
        ):
            issues.append(
                AttentionQualityIssue(
                    "AUTHOR_METRICS_NOT_PERMITTED",
                    Severity.ERROR,
                    "account metric supplied without declared permission",
                    mention.mention_id,
                )
            )

        for name, value in (
            ("published_at", mention.published_at),
            ("first_seen_at", mention.first_seen_at),
            ("ingested_at", mention.ingested_at),
            ("available_at", mention.available_at),
        ):
            _timestamp_issue(issues, name, value, mention.mention_id)
        if (
            _is_utc(mention.published_at)
            and _is_utc(mention.first_seen_at)
            and mention.published_at > mention.first_seen_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "PUBLISHED_AFTER_FIRST_SEEN",
                    Severity.ERROR,
                    "published_at is later than first_seen_at",
                    mention.mention_id,
                    mention.published_at,
                )
            )
        if (
            _is_utc(mention.available_at)
            and _is_utc(mention.first_seen_at)
            and mention.available_at < mention.first_seen_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "AVAILABLE_BEFORE_FIRST_SEEN",
                    Severity.ERROR,
                    "attention cannot be available before the system first observed it",
                    mention.mention_id,
                    mention.available_at,
                )
            )
        if (
            _is_utc(mention.available_at)
            and _is_utc(mention.published_at)
            and mention.available_at < mention.published_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "AVAILABLE_BEFORE_PUBLISHED",
                    Severity.ERROR,
                    "attention cannot be available before publication",
                    mention.mention_id,
                    mention.available_at,
                )
            )
        if (
            _is_utc(mention.ingested_at)
            and _is_utc(mention.first_seen_at)
            and mention.ingested_at < mention.first_seen_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "INGESTED_BEFORE_FIRST_SEEN",
                    Severity.ERROR,
                    "ingested_at precedes first_seen_at",
                    mention.mention_id,
                    mention.ingested_at,
                )
            )
        if (
            _is_utc(mention.ingested_at)
            and _is_utc(batch.fetched_at)
            and mention.ingested_at > batch.fetched_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "MENTION_AFTER_BATCH_FETCH",
                    Severity.ERROR,
                    "mention ingestion is later than batch fetch",
                    mention.mention_id,
                    mention.ingested_at,
                )
            )
        if mention.security_id not in security_tickers:
            issues.append(
                AttentionQualityIssue(
                    "SECURITY_NOT_MONITORED",
                    Severity.ERROR,
                    "mention security is absent from the monitored universe",
                    mention.mention_id,
                )
            )
        elif security_tickers[mention.security_id] != mention.ticker:
            issues.append(
                AttentionQualityIssue(
                    "TICKER_IDENTITY_MISMATCH",
                    Severity.ERROR,
                    "mention ticker does not match the effective monitored label",
                    mention.mention_id,
                )
            )
        if not _valid_url(mention.source_url):
            issues.append(
                AttentionQualityIssue(
                    "INVALID_SOURCE_URL",
                    Severity.ERROR,
                    "source URL must be http(s) or an explicit fixture URI",
                    mention.mention_id,
                )
            )
        for link in mention.outbound_urls + mention.linked_catalyst_urls:
            if not _valid_url(link):
                issues.append(
                    AttentionQualityIssue(
                        "INVALID_SUPPORTING_URL",
                        Severity.ERROR,
                        "outbound/catalyst URL is invalid",
                        mention.mention_id,
                    )
                )
        if len(mention.content_hash) != 64 or any(
            char not in "0123456789abcdef" for char in mention.content_hash.lower()
        ):
            issues.append(
                AttentionQualityIssue(
                    "INVALID_CONTENT_HASH",
                    Severity.ERROR,
                    "content_hash must be a hexadecimal SHA-256 digest",
                    mention.mention_id,
                )
            )
        elif mention.text is not None:
            digest = hashlib.sha256(mention.text.encode("utf-8")).hexdigest()
            if digest != mention.content_hash.lower():
                issues.append(
                    AttentionQualityIssue(
                        "CONTENT_HASH_MISMATCH",
                        Severity.ERROR,
                        "content_hash does not match the supplied analysis text",
                        mention.mention_id,
                    )
                )
        if mention.account_quality_score is not None and not (
            0.0 <= mention.account_quality_score <= 1.0
        ):
            issues.append(
                AttentionQualityIssue(
                    "INVALID_ACCOUNT_QUALITY",
                    Severity.ERROR,
                    "normalized account quality must be between zero and one",
                    mention.mention_id,
                )
            )
        if mention.account_quality_score is not None and not mention.account_quality_basis:
            issues.append(
                AttentionQualityIssue(
                    "ACCOUNT_QUALITY_BASIS_MISSING",
                    Severity.ERROR,
                    "account quality requires a documented source-specific basis",
                    mention.mention_id,
                )
            )
        if mention.is_repost is True and not mention.repost_of_source_record_id:
            issues.append(
                AttentionQualityIssue(
                    "REPOST_ORIGIN_MISSING",
                    Severity.WARNING,
                    "repost is identified but its original source record is unknown",
                    mention.mention_id,
                )
            )
        previous_observed = None
        for snapshot in mention.engagement_snapshots:
            _timestamp_issue(
                issues, "engagement observed_at", snapshot.observed_at, mention.mention_id
            )
            _timestamp_issue(
                issues, "engagement available_at", snapshot.available_at, mention.mention_id
            )
            if (
                _is_utc(snapshot.observed_at)
                and _is_utc(snapshot.available_at)
                and snapshot.available_at < snapshot.observed_at
            ):
                issues.append(
                    AttentionQualityIssue(
                        "ENGAGEMENT_AVAILABLE_TOO_EARLY",
                        Severity.ERROR,
                        "engagement snapshot is available before observation",
                        mention.mention_id,
                        snapshot.available_at,
                    )
                )
            if (
                previous_observed is not None
                and _is_utc(previous_observed)
                and _is_utc(snapshot.observed_at)
                and snapshot.observed_at <= previous_observed
            ):
                issues.append(
                    AttentionQualityIssue(
                        "ENGAGEMENT_SNAPSHOTS_UNORDERED",
                        Severity.ERROR,
                        "engagement snapshots must be strictly chronological",
                        mention.mention_id,
                        snapshot.observed_at,
                    )
                )
            previous_observed = snapshot.observed_at
            if (
                _is_utc(snapshot.available_at)
                and _is_utc(batch.fetched_at)
                and snapshot.available_at > batch.fetched_at
            ):
                issues.append(
                    AttentionQualityIssue(
                        "ENGAGEMENT_AFTER_BATCH_FETCH",
                        Severity.ERROR,
                        "engagement availability is later than batch fetch",
                        mention.mention_id,
                        snapshot.available_at,
                    )
                )
            for value in (
                snapshot.likes,
                snapshot.replies,
                snapshot.reposts,
                snapshot.views,
            ):
                if value is not None and value < 0:
                    issues.append(
                        AttentionQualityIssue(
                            "NEGATIVE_ENGAGEMENT",
                            Severity.ERROR,
                            "engagement counts cannot be negative",
                            mention.mention_id,
                            snapshot.observed_at,
                        )
                    )

    for catalyst in batch.catalyst_references:
        for name, value in (
            ("first_public_at", catalyst.first_public_at),
            ("available_at", catalyst.available_at),
        ):
            _timestamp_issue(issues, name, value, catalyst.catalyst_id)
        if (
            _is_utc(catalyst.available_at)
            and _is_utc(catalyst.first_public_at)
            and catalyst.available_at < catalyst.first_public_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "CATALYST_AVAILABLE_TOO_EARLY",
                    Severity.ERROR,
                    "catalyst is available before first-public time",
                    catalyst.catalyst_id,
                    catalyst.available_at,
                )
            )
        if (
            _is_utc(catalyst.available_at)
            and _is_utc(batch.fetched_at)
            and catalyst.available_at > batch.fetched_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "CATALYST_AFTER_BATCH_FETCH",
                    Severity.ERROR,
                    "catalyst availability is later than batch fetch",
                    catalyst.catalyst_id,
                    catalyst.available_at,
                )
            )
        if catalyst.security_id not in security_tickers:
            issues.append(
                AttentionQualityIssue(
                    "CATALYST_SECURITY_NOT_MONITORED",
                    Severity.ERROR,
                    "catalyst security is absent from monitored universe",
                    catalyst.catalyst_id,
                )
            )
        if not _valid_url(catalyst.source_url):
            issues.append(
                AttentionQualityIssue(
                    "INVALID_CATALYST_URL",
                    Severity.ERROR,
                    "catalyst source URL is invalid",
                    catalyst.catalyst_id,
                )
            )

    for context in batch.price_context:
        for name, value in (
            ("reference_at", context.reference_at),
            ("observed_at", context.observed_at),
            ("available_at", context.available_at),
        ):
            _timestamp_issue(issues, name, value, context.security_id)
        if (
            _is_utc(context.reference_at)
            and _is_utc(context.observed_at)
            and context.reference_at > context.observed_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "PRICE_REFERENCE_AFTER_OBSERVATION",
                    Severity.ERROR,
                    "price-move reference follows its observation",
                    context.security_id,
                    context.reference_at,
                )
            )
        if (
            _is_utc(context.available_at)
            and _is_utc(context.observed_at)
            and context.available_at < context.observed_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "PRICE_CONTEXT_AVAILABLE_TOO_EARLY",
                    Severity.ERROR,
                    "price context is available before observation",
                    context.security_id,
                    context.available_at,
                )
            )
        if (
            _is_utc(context.available_at)
            and _is_utc(batch.fetched_at)
            and context.available_at > batch.fetched_at
        ):
            issues.append(
                AttentionQualityIssue(
                    "PRICE_CONTEXT_AFTER_BATCH_FETCH",
                    Severity.ERROR,
                    "price context availability is later than batch fetch",
                    context.security_id,
                    context.available_at,
                )
            )
        if not isfinite(context.cumulative_return_pct):
            issues.append(
                AttentionQualityIssue(
                    "INVALID_PRICE_RETURN",
                    Severity.ERROR,
                    "price context return must be finite",
                    context.security_id,
                )
            )
        if not _valid_url(context.source_url):
            issues.append(
                AttentionQualityIssue(
                    "INVALID_PRICE_SOURCE_URL",
                    Severity.ERROR,
                    "price context source URL is invalid",
                    context.security_id,
                )
            )
    return tuple(issues)
