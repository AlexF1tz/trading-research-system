"""Provider-neutral contracts for lawful public retail-attention research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class AttentionSource(str, Enum):
    REDDIT = "reddit"
    STOCKTWITS = "stocktwits"
    X_PUBLIC = "x_public"
    YOUTUBE = "youtube"
    TIKTOK_PUBLIC = "tiktok_public"
    TRADING_FORUM = "trading_forum"
    GOOGLE_TRENDS = "google_trends"
    OTHER_PUBLIC = "other_public"


class AccessMethod(str, Enum):
    APPROVED_API = "approved_api"
    PUBLIC_FEED = "public_feed"
    LICENSED_EXPORT = "licensed_export"
    MANUAL_RESEARCH_EXPORT = "manual_research_export"
    ENGINEERING_FIXTURE = "engineering_fixture"


class ContentStorage(str, Enum):
    NONE = "none"
    HASH_ONLY = "hash_only"
    EXCERPT_APPROVED = "excerpt_approved"
    FULL_TEXT_APPROVED = "full_text_approved"


class AttentionStage(str, Enum):
    EARLY = "early"
    EXPANDING = "expanding"
    CROWDED = "crowded"
    COLLAPSING = "collapsing"
    QUIET = "quiet"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    source: AttentionSource
    access_method: AccessMethod
    collection_authorization_confirmed: bool
    terms_url: str | None
    terms_reviewed_at: datetime | None
    rate_limit_policy: str
    coverage_started_at: datetime
    coverage_ended_at: datetime | None
    text_analysis_permitted: bool
    content_storage: ContentStorage
    author_metrics_permitted: bool
    coverage_note: str


@dataclass(frozen=True, slots=True)
class MonitoredSecurity:
    security_id: str
    ticker: str


@dataclass(frozen=True, slots=True)
class EngagementSnapshot:
    observed_at: datetime
    available_at: datetime
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    views: int | None = None

    @property
    def interactions(self) -> int | None:
        values = (self.likes, self.replies, self.reposts)
        present = tuple(value for value in values if value is not None)
        return sum(present) if present else None


@dataclass(frozen=True, slots=True)
class Mention:
    mention_id: str
    security_id: str
    ticker: str
    source: AttentionSource
    source_record_id: str
    source_url: str
    published_at: datetime
    first_seen_at: datetime
    ingested_at: datetime
    available_at: datetime
    content_hash: str
    text: str | None = None
    author_key: str | None = None
    is_repost: bool | None = None
    repost_of_source_record_id: str | None = None
    outbound_urls: tuple[str, ...] = ()
    linked_catalyst_urls: tuple[str, ...] = ()
    engagement_snapshots: tuple[EngagementSnapshot, ...] = ()
    account_quality_score: float | None = None
    account_quality_basis: str | None = None
    affiliate_or_paid_promotion: bool | None = None
    language: str | None = "en"


@dataclass(frozen=True, slots=True)
class CatalystReference:
    catalyst_id: str
    security_id: str
    ticker: str
    source_url: str
    first_public_at: datetime
    available_at: datetime
    is_primary_source: bool


@dataclass(frozen=True, slots=True)
class PriceMoveContext:
    security_id: str
    ticker: str
    reference_at: datetime
    observed_at: datetime
    available_at: datetime
    cumulative_return_pct: float
    source_url: str


@dataclass(frozen=True, slots=True)
class SourceBatch:
    provider: str
    dataset_kind: str
    fetched_at: datetime
    monitored_securities: tuple[MonitoredSecurity, ...]
    source_descriptors: tuple[SourceDescriptor, ...]
    mentions: tuple[Mention, ...]
    catalyst_references: tuple[CatalystReference, ...] = ()
    price_context: tuple[PriceMoveContext, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AttentionSignal:
    signal_id: str
    security_id: str
    ticker: str
    as_of: datetime
    interval_minutes: int
    window_start: datetime
    window_end: datetime
    interval_counts: tuple[int, ...]
    raw_mention_count: int
    previous_mention_count: int
    baseline_interval_count: int
    baseline_mean_mentions: float | None
    baseline_adjusted_mention_score: float | None
    mention_velocity_per_hour: float
    attention_acceleration_per_hour2: float
    unique_author_count: int | None
    author_coverage: float
    independent_author_score: float | None
    engagement_velocity_per_hour: float | None
    engagement_coverage: float
    sentiment: float | None
    sentiment_coverage: float
    account_quality_score: float | None
    account_quality_coverage: float
    original_post_ratio: float | None
    repost_coverage: float
    duplicate_language_ratio: float | None
    promotional_language_score: float | None
    source_counts: tuple[tuple[str, int], ...]
    source_concentration: float | None
    source_diversity_score: float | None
    first_observed_mention_at: datetime | None
    linked_primary_catalyst_urls: tuple[str, ...]
    supporting_links: tuple[str, ...]
    attention_stage: AttentionStage
    flags: tuple[str, ...]
    data_completeness_warnings: tuple[str, ...]
    scoring_version: str

    def to_dict(self) -> dict[str, Any]:
        def timestamp(value: datetime | None) -> str | None:
            if value is None:
                return None
            return value.isoformat().replace("+00:00", "Z")

        return {
            "signal_id": self.signal_id,
            "security_id": self.security_id,
            "ticker": self.ticker,
            "as_of": timestamp(self.as_of),
            "interval_minutes": self.interval_minutes,
            "window_start": timestamp(self.window_start),
            "window_end": timestamp(self.window_end),
            "interval_counts": list(self.interval_counts),
            "raw_mention_count": self.raw_mention_count,
            "previous_mention_count": self.previous_mention_count,
            "baseline_interval_count": self.baseline_interval_count,
            "baseline_mean_mentions": self.baseline_mean_mentions,
            "baseline_adjusted_mention_score": self.baseline_adjusted_mention_score,
            "mention_velocity_per_hour": self.mention_velocity_per_hour,
            "attention_acceleration_per_hour2": self.attention_acceleration_per_hour2,
            "unique_author_count": self.unique_author_count,
            "author_coverage": self.author_coverage,
            "independent_author_score": self.independent_author_score,
            "engagement_velocity_per_hour": self.engagement_velocity_per_hour,
            "engagement_coverage": self.engagement_coverage,
            "sentiment": self.sentiment,
            "sentiment_coverage": self.sentiment_coverage,
            "account_quality_score": self.account_quality_score,
            "account_quality_coverage": self.account_quality_coverage,
            "original_post_ratio": self.original_post_ratio,
            "repost_coverage": self.repost_coverage,
            "duplicate_language_ratio": self.duplicate_language_ratio,
            "promotional_language_score": self.promotional_language_score,
            "source_counts": dict(self.source_counts),
            "source_concentration": self.source_concentration,
            "source_diversity_score": self.source_diversity_score,
            "first_observed_mention_time": timestamp(
                self.first_observed_mention_at
            ),
            "linked_primary_catalyst_urls": list(
                self.linked_primary_catalyst_urls
            ),
            "supporting_links": list(self.supporting_links),
            "attention_stage": self.attention_stage.value,
            "flags": list(self.flags),
            "data_completeness_warning": list(
                self.data_completeness_warnings
            ),
            "scoring_version": self.scoring_version,
            "interpretation": "attention_measurement_only_not_a_trade_recommendation",
        }
