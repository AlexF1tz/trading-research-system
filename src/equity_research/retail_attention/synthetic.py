"""Explicit deterministic fixtures; these are not observations from any platform."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

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


AS_OF = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=timezone.utc)


def _mention(
    mention_id: str,
    security_id: str,
    ticker: str,
    source: AttentionSource,
    minute: int,
    text: str,
    author: str,
    *,
    hour: int = 13,
    repost: bool = False,
    affiliate: bool = False,
    catalyst_url: str | None = None,
    interactions: tuple[int, int] = (2, 6),
) -> Mention:
    published = _at(hour, minute)
    first_seen = published + timedelta(seconds=20)
    second_snapshot = min(AS_OF, first_seen + timedelta(minutes=5))
    snapshots = (
        EngagementSnapshot(
            observed_at=first_seen,
            available_at=first_seen,
            likes=interactions[0],
            replies=0,
            reposts=0,
        ),
        EngagementSnapshot(
            observed_at=second_snapshot,
            available_at=second_snapshot,
            likes=interactions[1],
            replies=1,
            reposts=1 if repost else 0,
        ),
    )
    if second_snapshot == first_seen:
        snapshots = snapshots[:1]
    return Mention(
        mention_id=mention_id,
        security_id=security_id,
        ticker=ticker,
        source=source,
        source_record_id=f"fixture-{mention_id}",
        source_url=f"fixture://attention/{source.value}/{mention_id}",
        published_at=published,
        first_seen_at=first_seen,
        ingested_at=first_seen,
        available_at=first_seen,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        author_key=author,
        is_repost=repost,
        repost_of_source_record_id=("fixture-crowd-origin" if repost else None),
        outbound_urls=((catalyst_url,) if catalyst_url else ()),
        linked_catalyst_urls=((catalyst_url,) if catalyst_url else ()),
        engagement_snapshots=snapshots,
        account_quality_score=0.72 if not affiliate else 0.18,
        account_quality_basis="fixture_normalized_account_history",
        affiliate_or_paid_promotion=affiliate,
    )


class SyntheticAttentionFixtureProvider:
    """Create expanding, crowded, collapsing, and quiet fixture tickers."""

    @property
    def name(self) -> str:
        return "synthetic_attention_fixture"

    def load(self) -> SourceBatch:
        primary_url = "fixture://company/early/primary-catalyst"
        crowd_text = (
            "Guaranteed squeeze on CROWD buy now this cannot lose and goes to the moon"
        )
        mentions = (
            _mention(
                "early-old",
                "SEC-EARLY",
                "EARLY",
                AttentionSource.REDDIT,
                10,
                "EARLY contract discussion with a primary company link",
                "author-early-0",
                hour=12,
                catalyst_url=primary_url,
            ),
            _mention(
                "early-prev",
                "SEC-EARLY",
                "EARLY",
                AttentionSource.REDDIT,
                36,
                "EARLY contract looks positive after the company announcement",
                "author-early-1",
                catalyst_url=primary_url,
            ),
            _mention(
                "early-current-1",
                "SEC-EARLY",
                "EARLY",
                AttentionSource.REDDIT,
                47,
                "Independent review of EARLY contract terms and customer value",
                "author-early-2",
                catalyst_url=primary_url,
            ),
            _mention(
                "early-current-2",
                "SEC-EARLY",
                "EARLY",
                AttentionSource.YOUTUBE,
                51,
                "EARLY company contract launch has measurable revenue potential",
                "author-early-3",
                catalyst_url=primary_url,
            ),
            _mention(
                "early-current-3",
                "SEC-EARLY",
                "EARLY",
                AttentionSource.STOCKTWITS,
                57,
                "EARLY primary release confirms a contract but execution remains uncertain",
                "author-early-4",
                catalyst_url=primary_url,
            ),
            _mention(
                "crowd-prev-1",
                "SEC-CROWD",
                "CROWD",
                AttentionSource.X_PUBLIC,
                31,
                crowd_text,
                "promoter-one",
                affiliate=True,
                interactions=(10, 45),
            ),
            _mention(
                "crowd-prev-2",
                "SEC-CROWD",
                "CROWD",
                AttentionSource.REDDIT,
                34,
                crowd_text,
                "promoter-one",
                repost=True,
                affiliate=True,
                interactions=(15, 55),
            ),
            _mention(
                "crowd-prev-3",
                "SEC-CROWD",
                "CROWD",
                AttentionSource.STOCKTWITS,
                38,
                crowd_text,
                "promoter-one",
                repost=True,
                affiliate=True,
                interactions=(20, 60),
            ),
            _mention(
                "crowd-prev-4",
                "SEC-CROWD",
                "CROWD",
                AttentionSource.X_PUBLIC,
                42,
                crowd_text,
                "promoter-two",
                repost=True,
                affiliate=True,
                interactions=(18, 70),
            ),
            *tuple(
                _mention(
                    f"crowd-current-{index}",
                    "SEC-CROWD",
                    "CROWD",
                    (
                        AttentionSource.X_PUBLIC,
                        AttentionSource.REDDIT,
                        AttentionSource.STOCKTWITS,
                    )[index % 3],
                    46 + index * 2,
                    crowd_text,
                    "promoter-one" if index < 4 else "promoter-two",
                    repost=index > 0,
                    affiliate=True,
                    interactions=(20 + index, 65 + index * 5),
                )
                for index in range(6)
            ),
            *tuple(
                _mention(
                    f"fade-prev-{index}",
                    "SEC-FADE",
                    "FADE",
                    AttentionSource.REDDIT,
                    31 + index * 2,
                    f"FADE discussion number {index} with uncertain momentum outlook",
                    f"fade-author-{index}",
                )
                for index in range(5)
            ),
            _mention(
                "fade-current",
                "SEC-FADE",
                "FADE",
                AttentionSource.REDDIT,
                52,
                "FADE attention appears weaker and downside risk remains",
                "fade-author-last",
            ),
        )
        coverage_start = _at(11, 0)
        descriptors = tuple(
            SourceDescriptor(
                source=source,
                access_method=AccessMethod.ENGINEERING_FIXTURE,
                collection_authorization_confirmed=True,
                terms_url=f"fixture://terms/{source.value}",
                terms_reviewed_at=_at(11, 0),
                rate_limit_policy="no_network_requests_engineering_fixture",
                coverage_started_at=coverage_start,
                coverage_ended_at=None,
                text_analysis_permitted=True,
                content_storage=ContentStorage.FULL_TEXT_APPROVED,
                author_metrics_permitted=True,
                coverage_note="deterministic engineering fixture; no platform was accessed",
            )
            for source in (
                AttentionSource.REDDIT,
                AttentionSource.STOCKTWITS,
                AttentionSource.X_PUBLIC,
                AttentionSource.YOUTUBE,
            )
        )
        return SourceBatch(
            provider=self.name,
            dataset_kind="synthetic_engineering_fixture_not_social_data",
            fetched_at=AS_OF,
            monitored_securities=(
                MonitoredSecurity("SEC-EARLY", "EARLY"),
                MonitoredSecurity("SEC-CROWD", "CROWD"),
                MonitoredSecurity("SEC-FADE", "FADE"),
                MonitoredSecurity("SEC-QUIET", "QUIET"),
            ),
            source_descriptors=descriptors,
            mentions=mentions,
            catalyst_references=(
                CatalystReference(
                    catalyst_id="fixture-catalyst-early",
                    security_id="SEC-EARLY",
                    ticker="EARLY",
                    source_url=primary_url,
                    first_public_at=_at(13, 30),
                    available_at=_at(13, 30),
                    is_primary_source=True,
                ),
            ),
            price_context=(
                PriceMoveContext(
                    security_id="SEC-EARLY",
                    ticker="EARLY",
                    reference_at=_at(13, 30),
                    observed_at=_at(13, 45),
                    available_at=_at(13, 45),
                    cumulative_return_pct=2.5,
                    source_url="fixture://market/early/1345",
                ),
                PriceMoveContext(
                    security_id="SEC-CROWD",
                    ticker="CROWD",
                    reference_at=_at(13, 0),
                    observed_at=_at(13, 44),
                    available_at=_at(13, 44),
                    cumulative_return_pct=14.0,
                    source_url="fixture://market/crowd/1344",
                ),
                PriceMoveContext(
                    security_id="SEC-FADE",
                    ticker="FADE",
                    reference_at=_at(13, 0),
                    observed_at=_at(13, 45),
                    available_at=_at(13, 45),
                    cumulative_return_pct=4.0,
                    source_url="fixture://market/fade/1345",
                ),
            ),
            notes=(
                "SYNTHETIC_FIXTURE: no Reddit, Stocktwits, X, YouTube, or other platform data was fetched",
            ),
        )
