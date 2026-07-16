"""Transparent point-in-time retail-attention measurements and stage rules."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import (
    AttentionSignal,
    AttentionStage,
    Mention,
    MonitoredSecurity,
    PriceMoveContext,
    SourceBatch,
)


SCORING_VERSION = "retail-attention-rules-v1"

_TOKEN = re.compile(r"[a-z0-9]+")
_PROMOTIONAL_PHRASES = (
    "guaranteed squeeze",
    "guaranteed short squeeze",
    "cannot lose",
    "can't lose",
    "risk free",
    "buy now",
    "100x",
    "to the moon",
)
_POSITIVE_WORDS = frozenset(
    {
        "beat",
        "growth",
        "approved",
        "contract",
        "partnership",
        "launch",
        "strong",
        "positive",
        "bullish",
        "upside",
    }
)
_NEGATIVE_WORDS = frozenset(
    {
        "miss",
        "offering",
        "dilution",
        "lawsuit",
        "halt",
        "weak",
        "negative",
        "bearish",
        "downside",
        "bankruptcy",
    }
)


@dataclass(frozen=True, slots=True)
class AttentionScoringConfig:
    as_of: datetime
    interval_minutes: int = 15
    baseline_intervals: int = 8
    minimum_complete_baseline_intervals: int = 4
    minimum_metric_coverage: float = 0.5
    duplicate_similarity_threshold: float = 0.82
    coordination_lookback_intervals: int = 4
    crowded_mention_count: int = 12
    crowded_baseline_score: float = 85.0
    expanding_baseline_score: float = 65.0
    collapsing_previous_count: int = 4
    collapsing_ratio: float = 0.5
    high_engagement_interactions: int = 25
    large_observed_move_pct: float = 10.0
    late_discovery_minutes: int = 30
    maximum_supporting_links: int = 10

    def validate(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("attention as_of must be timezone-aware")
        if self.as_of.utcoffset() != timedelta(0):
            raise ValueError("attention as_of must be UTC")
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")
        if self.baseline_intervals < 1:
            raise ValueError("baseline_intervals must be positive")
        if not 1 <= self.minimum_complete_baseline_intervals <= self.baseline_intervals:
            raise ValueError("minimum complete baseline intervals is invalid")
        if not 0.0 <= self.minimum_metric_coverage <= 1.0:
            raise ValueError("minimum_metric_coverage must be between zero and one")
        if not 0.0 <= self.duplicate_similarity_threshold <= 1.0:
            raise ValueError("duplicate similarity threshold must be between zero and one")
        if self.coordination_lookback_intervals < 1:
            raise ValueError("coordination lookback must be positive")
        if self.maximum_supporting_links < 1:
            raise ValueError("maximum_supporting_links must be positive")


def _tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    values = frozenset(_TOKEN.findall(text.lower()))
    return values if len(values) >= 4 else frozenset()


def _similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _sentiment(text: str) -> float:
    values = _TOKEN.findall(text.lower())
    positive = sum(value in _POSITIVE_WORDS for value in values)
    negative = sum(value in _NEGATIVE_WORDS for value in values)
    total = positive + negative
    return (positive - negative) / total if total else 0.0


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 4)


def _baseline_score(current: int, historical: tuple[int, ...]) -> float:
    mean = sum(historical) / len(historical)
    variance = sum((value - mean) ** 2 for value in historical) / len(historical)
    scale = max(1.0, math.sqrt(variance + mean + 1.0))
    return _bounded(50.0 + 10.0 * ((current - mean) / scale))


def _engagement_velocity(mention: Mention, as_of: datetime) -> float | None:
    snapshots = tuple(
        value
        for value in mention.engagement_snapshots
        if value.available_at <= as_of and value.observed_at <= as_of
    )
    if len(snapshots) < 2:
        return None
    first = snapshots[0]
    last = snapshots[-1]
    first_value = first.interactions
    last_value = last.interactions
    hours = (last.observed_at - first.observed_at).total_seconds() / 3600.0
    if first_value is None or last_value is None or hours <= 0:
        return None
    return max(0.0, (last_value - first_value) / hours)


def _latest_interactions(mention: Mention, as_of: datetime) -> int | None:
    snapshots = tuple(
        value
        for value in mention.engagement_snapshots
        if value.available_at <= as_of and value.observed_at <= as_of
    )
    return snapshots[-1].interactions if snapshots else None


def _primary_links(
    batch: SourceBatch,
    security_id: str,
    as_of: datetime,
) -> frozenset[str]:
    return frozenset(
        value.source_url
        for value in batch.catalyst_references
        if value.security_id == security_id
        and value.is_primary_source
        and value.first_public_at <= as_of
        and value.available_at <= as_of
    )


def _linked_primary_urls(
    mentions: tuple[Mention, ...], primary_links: frozenset[str]
) -> tuple[str, ...]:
    linked = {
        link
        for mention in mentions
        for link in mention.linked_catalyst_urls + mention.outbound_urls
        if link in primary_links
    }
    return tuple(sorted(linked))


def _late_after_move(
    mention: Mention,
    contexts: tuple[PriceMoveContext, ...],
    threshold: float,
    as_of: datetime,
) -> bool:
    eligible = tuple(
        value
        for value in contexts
        if value.security_id == mention.security_id
        and value.observed_at <= mention.published_at
        and value.available_at <= mention.available_at
        and value.available_at <= as_of
    )
    if not eligible:
        return False
    latest = max(eligible, key=lambda value: value.observed_at)
    return latest.cumulative_return_pct >= threshold


def _duplicate_flags(
    current_mentions: tuple[Mention, ...],
    lookback_mentions: tuple[Mention, ...],
    threshold: float,
) -> tuple[dict[str, bool], float]:
    ordered = sorted(
        lookback_mentions,
        key=lambda value: (value.available_at, value.mention_id),
    )
    seen: list[tuple[str, frozenset[str]]] = []
    flags: dict[str, bool] = {}
    current_ids = {value.mention_id for value in current_mentions}
    comparable = 0
    for mention in ordered:
        tokens = _tokens(mention.text)
        if mention.mention_id in current_ids and tokens:
            comparable += 1
            flags[mention.mention_id] = any(
                _similarity(tokens, prior_tokens) >= threshold
                for prior_id, prior_tokens in seen
                if prior_id != mention.mention_id
            )
        if tokens:
            seen.append((mention.mention_id, tokens))
    for mention in current_mentions:
        flags.setdefault(mention.mention_id, False)
    coverage = comparable / len(current_mentions) if current_mentions else 0.0
    return flags, coverage


def _supporting_links(
    mentions: tuple[Mention, ...],
    limit: int,
) -> tuple[str, ...]:
    ordered = sorted(
        mentions,
        key=lambda value: (
            value.is_repost is True,
            value.first_seen_at,
            value.source_url,
        ),
    )
    result: list[str] = []
    for mention in ordered:
        if mention.source_url not in result:
            result.append(mention.source_url)
        if len(result) >= limit:
            break
    return tuple(result)


def _attention_stage(
    *,
    current: int,
    previous: int,
    baseline_mean: float | None,
    baseline_score: float | None,
    acceleration: float,
    source_count: int,
    independent_score: float | None,
    promotional_score: float | None,
    original_ratio: float | None,
    source_concentration: float | None,
    config: AttentionScoringConfig,
) -> AttentionStage:
    if current == 0:
        if previous >= config.collapsing_previous_count:
            return AttentionStage.COLLAPSING
        return AttentionStage.QUIET
    if baseline_score is None or baseline_mean is None:
        return AttentionStage.INSUFFICIENT_DATA
    if (
        previous >= config.collapsing_previous_count
        and current <= previous * config.collapsing_ratio
        and acceleration < 0
    ):
        return AttentionStage.COLLAPSING
    crowd_evidence = (
        (promotional_score is not None and promotional_score >= 45.0)
        or (original_ratio is not None and original_ratio < 0.5)
        or (source_concentration is not None and source_concentration >= 0.75)
    )
    if current >= config.crowded_mention_count or (
        baseline_score >= config.crowded_baseline_score and crowd_evidence
    ):
        return AttentionStage.CROWDED
    if (
        baseline_score >= config.expanding_baseline_score
        and acceleration > 0
        and source_count >= 2
        and independent_score is not None
        and independent_score >= 40.0
    ):
        return AttentionStage.EXPANDING
    if acceleration > 0 and current > baseline_mean:
        return AttentionStage.EARLY
    return AttentionStage.QUIET


class RuleBasedAttentionScorer:
    def __init__(self, config: AttentionScoringConfig) -> None:
        config.validate()
        self._config = config

    def score(self, batch: SourceBatch) -> tuple[AttentionSignal, ...]:
        return tuple(
            self._score_security(batch, security)
            for security in batch.monitored_securities
        )

    def _score_security(
        self,
        batch: SourceBatch,
        security: MonitoredSecurity,
    ) -> AttentionSignal:
        config = self._config
        interval = timedelta(minutes=config.interval_minutes)
        interval_hours = config.interval_minutes / 60.0
        total_intervals = config.baseline_intervals + 1
        history_start = config.as_of - interval * total_intervals
        current_start = config.as_of - interval

        eligible = tuple(
            mention
            for mention in batch.mentions
            if mention.security_id == security.security_id
            and mention.published_at <= config.as_of
            and mention.available_at <= config.as_of
        )
        window_mentions = tuple(
            mention
            for mention in eligible
            if history_start <= mention.available_at <= config.as_of
        )
        counts = [0] * total_intervals
        for mention in window_mentions:
            index = int((mention.available_at - history_start) // interval)
            index = min(total_intervals - 1, max(0, index))
            counts[index] += 1
        interval_counts = tuple(counts)
        current_mentions = tuple(
            mention
            for mention in window_mentions
            if current_start <= mention.available_at <= config.as_of
        )
        current = len(current_mentions)
        previous = interval_counts[-2] if len(interval_counts) >= 2 else 0
        historical_counts = interval_counts[:-1]

        requested_start = history_start
        complete_baseline = tuple(
            index
            for index in range(config.baseline_intervals)
            if all(
                descriptor.coverage_started_at <= requested_start + interval * index
                and (
                    descriptor.coverage_ended_at is None
                    or descriptor.coverage_ended_at
                    >= requested_start + interval * (index + 1)
                )
                for descriptor in batch.source_descriptors
            )
        )
        current_coverage_complete = bool(batch.source_descriptors) and all(
            descriptor.coverage_started_at <= current_start
            and (
                descriptor.coverage_ended_at is None
                or descriptor.coverage_ended_at >= config.as_of
            )
            for descriptor in batch.source_descriptors
        )
        baseline_count = len(complete_baseline)
        baseline_usable = (
            current_coverage_complete
            and baseline_count >= config.minimum_complete_baseline_intervals
            and complete_baseline
            == tuple(range(len(historical_counts) - baseline_count, len(historical_counts)))
        )
        usable_history = (
            historical_counts[-baseline_count:] if baseline_count else ()
        )
        baseline_mean = (
            round(sum(usable_history) / len(usable_history), 4)
            if baseline_usable and usable_history
            else None
        )
        baseline_score = (
            _baseline_score(current, usable_history)
            if baseline_usable and usable_history
            else None
        )
        velocity = current / interval_hours
        acceleration = (current - previous) / (interval_hours**2)

        author_values = tuple(
            mention.author_key
            for mention in current_mentions
            if mention.author_key is not None
        )
        author_coverage = len(author_values) / current if current else 0.0
        unique_author_count = len(set(author_values)) if author_values else None

        coordination_start = config.as_of - interval * config.coordination_lookback_intervals
        coordination_mentions = tuple(
            mention
            for mention in eligible
            if coordination_start <= mention.available_at <= config.as_of
        )
        duplicate_flags, text_coverage = _duplicate_flags(
            current_mentions,
            coordination_mentions,
            config.duplicate_similarity_threshold,
        )
        duplicate_ratio = (
            sum(duplicate_flags.values()) / current
            if current and text_coverage >= config.minimum_metric_coverage
            else None
        )
        independent_score = None
        if (
            current
            and author_coverage >= config.minimum_metric_coverage
            and duplicate_ratio is not None
            and author_values
        ):
            author_counts = Counter(author_values)
            unique_ratio = len(author_counts) / len(author_values)
            max_share = max(author_counts.values()) / len(author_values)
            support = min(1.0, len(author_counts) / 3.0)
            independent_score = _bounded(
                100.0
                * unique_ratio
                * (1.0 - duplicate_ratio)
                * (1.0 - 0.5 * max_share)
                * support
            )

        engagement_values = tuple(
            value
            for mention in current_mentions
            if (value := _engagement_velocity(mention, config.as_of)) is not None
        )
        engagement_coverage = len(engagement_values) / current if current else 0.0
        engagement_velocity = (
            round(sum(engagement_values), 4) if engagement_values else None
        )

        sentiment_values = tuple(
            _sentiment(mention.text)
            for mention in current_mentions
            if mention.text is not None
        )
        sentiment_coverage = len(sentiment_values) / current if current else 0.0
        sentiment = (
            round(sum(sentiment_values) / len(sentiment_values), 4)
            if sentiment_values
            else None
        )
        account_values = tuple(
            mention.account_quality_score
            for mention in current_mentions
            if mention.account_quality_score is not None
        )
        account_coverage = len(account_values) / current if current else 0.0
        account_quality = (
            round(sum(account_values) / len(account_values), 4)
            if account_values
            else None
        )
        repost_values = tuple(
            mention.is_repost
            for mention in current_mentions
            if mention.is_repost is not None
        )
        repost_coverage = len(repost_values) / current if current else 0.0
        original_ratio = (
            round(sum(not value for value in repost_values) / len(repost_values), 4)
            if repost_values
            else None
        )

        source_counter = Counter(mention.source.value for mention in current_mentions)
        source_counts = tuple(sorted(source_counter.items()))
        source_concentration = (
            round(
                sum((count / current) ** 2 for count in source_counter.values()),
                4,
            )
            if current
            else None
        )
        source_diversity = (
            _bounded(100.0 * (1.0 - source_concentration))
            if source_concentration is not None
            else None
        )

        primary_links = _primary_links(batch, security.security_id, config.as_of)
        linked_primary = _linked_primary_urls(current_mentions, primary_links)
        has_primary_link = bool(linked_primary)
        per_mention_promo: list[float] = []
        flags: set[str] = set()
        evidence_coverage = 0
        for mention in current_mentions:
            points = 0.0
            lowered = mention.text.lower() if mention.text is not None else ""
            if mention.text is not None:
                evidence_coverage += 1
                if any(phrase in lowered for phrase in _PROMOTIONAL_PHRASES):
                    points += 55.0
                    flags.add("PROMOTIONAL_GUARANTEE_LANGUAGE")
            if duplicate_flags[mention.mention_id]:
                points += 30.0
                flags.add("DUPLICATE_OR_COORDINATED_LANGUAGE")
            if mention.affiliate_or_paid_promotion is not None:
                evidence_coverage += 1
                if mention.affiliate_or_paid_promotion:
                    points += 50.0
                    flags.add("AFFILIATE_OR_PAID_PROMOTION")
            interactions = _latest_interactions(mention, config.as_of)
            if interactions is not None:
                evidence_coverage += 1
                if (
                    interactions >= config.high_engagement_interactions
                    and not has_primary_link
                ):
                    points += 20.0
                    flags.add("HIGH_ENGAGEMENT_WITHOUT_PRIMARY_CATALYST")
            if _late_after_move(
                mention,
                batch.price_context,
                config.large_observed_move_pct,
                config.as_of,
            ):
                points += 20.0
                flags.add("AFTER_LARGE_OBSERVED_MOVE")
            if mention.first_seen_at - mention.published_at > timedelta(
                minutes=config.late_discovery_minutes
            ):
                flags.add("LATE_DISCOVERY")
            per_mention_promo.append(min(100.0, points))
        promo_score = (
            round(sum(per_mention_promo) / len(per_mention_promo), 4)
            if current and evidence_coverage
            else None
        )
        if current and not has_primary_link:
            flags.add("NO_PRIMARY_CATALYST_LINK")
        if source_concentration is not None and source_concentration >= 0.75:
            flags.add("SOURCE_CONCENTRATION_HIGH")

        warnings: set[str] = set()
        if not baseline_usable:
            warnings.add("INCOMPLETE_OR_CHANGING_BASELINE_SOURCE_COVERAGE")
        if current and author_coverage < config.minimum_metric_coverage:
            warnings.add("UNIQUE_AUTHOR_COVERAGE_LOW")
        if current and text_coverage < config.minimum_metric_coverage:
            warnings.add("TEXT_ANALYSIS_COVERAGE_LOW")
        if current and engagement_coverage < config.minimum_metric_coverage:
            warnings.add("ENGAGEMENT_VELOCITY_COVERAGE_LOW")
        if current and account_coverage < config.minimum_metric_coverage:
            warnings.add("ACCOUNT_QUALITY_COVERAGE_LOW")
        if current and repost_coverage < config.minimum_metric_coverage:
            warnings.add("REPOST_STATUS_COVERAGE_LOW")
        if current and len(source_counter) < 2:
            warnings.add("SINGLE_SOURCE_ATTENTION")
        if current and not has_primary_link:
            warnings.add("NO_VERIFIED_PRIMARY_CATALYST_LINK")
        if current and not any(
            value.security_id == security.security_id
            and value.available_at <= config.as_of
            for value in batch.price_context
        ):
            warnings.add("PRICE_MOVE_CONTEXT_UNAVAILABLE")
        warnings.update(f"PROVIDER_NOTE:{value}" for value in batch.notes)

        stage = _attention_stage(
            current=current,
            previous=previous,
            baseline_mean=baseline_mean,
            baseline_score=baseline_score,
            acceleration=acceleration,
            source_count=len(source_counter),
            independent_score=independent_score,
            promotional_score=promo_score,
            original_ratio=original_ratio,
            source_concentration=source_concentration,
            config=config,
        )
        signal_material = (
            f"{security.security_id}|{security.ticker}|{config.as_of.isoformat()}|"
            f"{config.interval_minutes}|{SCORING_VERSION}"
        )
        signal_id = hashlib.sha256(signal_material.encode("utf-8")).hexdigest()[:24]
        first_observed = min(
            (mention.first_seen_at for mention in eligible),
            default=None,
        )
        return AttentionSignal(
            signal_id=signal_id,
            security_id=security.security_id,
            ticker=security.ticker,
            as_of=config.as_of,
            interval_minutes=config.interval_minutes,
            window_start=current_start,
            window_end=config.as_of,
            interval_counts=interval_counts,
            raw_mention_count=current,
            previous_mention_count=previous,
            baseline_interval_count=baseline_count,
            baseline_mean_mentions=baseline_mean,
            baseline_adjusted_mention_score=baseline_score,
            mention_velocity_per_hour=round(velocity, 4),
            attention_acceleration_per_hour2=round(acceleration, 4),
            unique_author_count=unique_author_count,
            author_coverage=round(author_coverage, 4),
            independent_author_score=independent_score,
            engagement_velocity_per_hour=engagement_velocity,
            engagement_coverage=round(engagement_coverage, 4),
            sentiment=sentiment,
            sentiment_coverage=round(sentiment_coverage, 4),
            account_quality_score=account_quality,
            account_quality_coverage=round(account_coverage, 4),
            original_post_ratio=original_ratio,
            repost_coverage=round(repost_coverage, 4),
            duplicate_language_ratio=(
                round(duplicate_ratio, 4) if duplicate_ratio is not None else None
            ),
            promotional_language_score=promo_score,
            source_counts=source_counts,
            source_concentration=source_concentration,
            source_diversity_score=source_diversity,
            first_observed_mention_at=first_observed,
            linked_primary_catalyst_urls=linked_primary,
            supporting_links=_supporting_links(
                current_mentions, config.maximum_supporting_links
            ),
            attention_stage=stage,
            flags=tuple(sorted(flags)),
            data_completeness_warnings=tuple(sorted(warnings)),
            scoring_version=SCORING_VERSION,
        )
