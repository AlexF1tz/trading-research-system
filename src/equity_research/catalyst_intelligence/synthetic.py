"""Deterministic catalyst fixture; never present as real company news."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .contracts import SourceBatch, SourceDocument, SourceKind


UTC = timezone.utc
FIXTURE_SOURCE = "synthetic_catalyst_fixture"


def _document(
    sequence: int,
    ticker: str,
    title: str,
    text: str,
    source_kind: SourceKind,
    published_at: datetime,
    *,
    first_seen_delay: timedelta = timedelta(seconds=5),
    form_type: str | None = None,
    form_items: tuple[str, ...] = (),
    expected_catalyst_date: date | None = None,
    related_primary_document_id: str | None = None,
) -> SourceDocument:
    document_id = f"fixture-{sequence:02d}"
    first_seen = published_at + first_seen_delay
    timestamp_verified = source_kind is not SourceKind.SOCIAL_UNVERIFIED
    available_at = published_at if timestamp_verified else first_seen
    return SourceDocument(
        document_id=document_id,
        ticker=ticker,
        issuer_id=f"issuer-{ticker.lower()}",
        title=title,
        text=text,
        source_url=f"fixture://catalyst/{document_id}",
        source_kind=source_kind,
        published_at=published_at,
        first_public_at=published_at,
        first_seen_at=first_seen,
        ingested_at=first_seen + timedelta(seconds=1),
        available_at=available_at,
        source_timestamp_verified=timestamp_verified,
        source_record_id=document_id,
        form_type=form_type,
        form_items=form_items,
        accession_number=(f"0000000000-26-{sequence:06d}" if form_type else None),
        expected_catalyst_date=expected_catalyst_date,
        related_primary_document_id=related_primary_document_id,
    )


class SyntheticCatalystFixtureProvider:
    @property
    def name(self) -> str:
        return FIXTURE_SOURCE

    def load(self) -> SourceBatch:
        base = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        contract_text = (
            "The company received a binding purchase order valued at $25 million. "
            "Initial deliveries are expected on 2026-08-15, subject to customer acceptance."
        )
        documents = (
            _document(
                1,
                "DEMO",
                "DEMO reports quarterly results and raises guidance",
                "Revenue was $12.5 million and EPS was $0.12. Results beat expectations and management raises guidance by 20%.",
                SourceKind.SEC_FILING,
                base,
                form_type="8-K",
                form_items=("2.02",),
            ),
            _document(
                2,
                "DEMO",
                "DEMO receives material purchase order",
                contract_text,
                SourceKind.COMPANY_IR,
                base + timedelta(minutes=10),
                expected_catalyst_date=date(2026, 8, 15),
            ),
            _document(
                3,
                "DEMO",
                "DEMO announces strategic collaboration",
                "A transformative, revolutionary and game-changing strategic collaboration creates an exciting massive opportunity.",
                SourceKind.COMPANY_IR,
                base + timedelta(minutes=20),
            ),
            _document(
                4,
                "TARG",
                "TARG enters definitive merger agreement",
                "TARG is to be acquired for $18.00 per share, representing a 35% premium, subject to approvals and closing conditions.",
                SourceKind.SEC_FILING,
                base + timedelta(minutes=30),
                form_type="8-K",
                form_items=("1.01",),
            ),
            _document(
                5,
                "BIOX",
                "Regulator approves BIOX therapy",
                "The FDA approved the therapy after review of the submitted clinical trial evidence.",
                SourceKind.REGULATOR_ANNOUNCEMENT,
                base + timedelta(minutes=40),
            ),
            _document(
                6,
                "LEGAL",
                "Court enters judgment against LEGAL",
                "The court entered judgment against the company for $10 million. The company may appeal.",
                SourceKind.REGULATOR_ANNOUNCEMENT,
                base + timedelta(minutes=50),
            ),
            _document(
                7,
                "PROD",
                "PROD launches breakthrough platform",
                "The revolutionary, transformative and industry-leading product launch is an exciting game-changing development.",
                SourceKind.COMPANY_IR,
                base + timedelta(minutes=60),
            ),
            _document(
                8,
                "MGMT",
                "Chief financial officer resigns",
                "The chief financial officer resigns effective immediately. An interim officer was appointed.",
                SourceKind.SEC_FILING,
                base + timedelta(minutes=70),
                form_type="8-K",
                form_items=("5.02",),
            ),
            _document(
                9,
                "INSD",
                "Director reports insider purchase",
                "A director reported an open-market insider purchase of 100,000 shares.",
                SourceKind.SEC_FILING,
                base + timedelta(minutes=80),
                form_type="4",
            ),
            _document(
                10,
                "DILU",
                "DILU establishes at-the-market offering",
                "The company entered an at-the-market ATM program permitting sales of up to $50 million of common stock.",
                SourceKind.SEC_FILING,
                base + timedelta(minutes=90),
                form_type="424B5",
            ),
            _document(
                11,
                "RSPL",
                "Exchange publishes reverse stock split notice",
                "The issuer will implement a 1-for-20 reverse stock split at market open.",
                SourceKind.EXCHANGE_ANNOUNCEMENT,
                base + timedelta(minutes=100),
            ),
            _document(
                12,
                "RUMR",
                "Unverified social claim about acquisition",
                "An unverified rumor claims the company may be acquired. No primary source was provided.",
                SourceKind.SOCIAL_UNVERIFIED,
                base + timedelta(minutes=110),
            ),
            _document(
                13,
                "DEMO",
                "DEMO repeats purchase-order announcement",
                contract_text,
                SourceKind.COMPANY_IR,
                base + timedelta(minutes=120),
                expected_catalyst_date=date(2026, 8, 15),
            ),
            _document(
                14,
                "STALE",
                "STALE announces commercial product launch",
                "The company announced a commercial product launch for a new product.",
                SourceKind.COMPANY_IR,
                base - timedelta(days=5),
                first_seen_delay=timedelta(days=5, minutes=130),
            ),
        )
        return SourceBatch(
            provider=FIXTURE_SOURCE,
            dataset_kind="synthetic_engineering_fixture_not_company_news",
            fetched_at=datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
            documents=documents,
            notes=(
                "All issuers, documents, URLs, numbers, and events are synthetic fixtures.",
                "Primary-source labels describe adapter shape only, not real-world verification.",
                "The social fixture must remain unverified and ambiguous.",
            ),
        )

