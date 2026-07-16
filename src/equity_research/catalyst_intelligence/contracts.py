"""Provider-independent catalyst source and event contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


class SourceKind(str, Enum):
    SEC_FILING = "sec_filing"
    COMPANY_IR = "company_investor_relations"
    EXCHANGE_ANNOUNCEMENT = "exchange_announcement"
    REGULATOR_ANNOUNCEMENT = "regulator_announcement"
    SECONDARY_NEWS = "secondary_news"
    SOCIAL_UNVERIFIED = "social_unverified"


class SourceTier(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


PRIMARY_SOURCE_KINDS = frozenset(
    {
        SourceKind.SEC_FILING,
        SourceKind.COMPANY_IR,
        SourceKind.EXCHANGE_ANNOUNCEMENT,
        SourceKind.REGULATOR_ANNOUNCEMENT,
    }
)


def source_tier(kind: SourceKind) -> SourceTier:
    return SourceTier.PRIMARY if kind in PRIMARY_SOURCE_KINDS else SourceTier.SECONDARY


class VerificationStatus(str, Enum):
    CONFIRMED_PRIMARY = "confirmed_primary"
    CORROBORATED = "corroborated"
    UNVERIFIED = "unverified"


class CatalystCategory(str, Enum):
    EARNINGS_GUIDANCE = "earnings_and_guidance"
    CONTRACT_PURCHASE_ORDER = "contracts_and_purchase_orders"
    PARTNERSHIP = "partnerships"
    MERGER_ACQUISITION = "mergers_and_acquisitions"
    FDA_CLINICAL = "fda_or_clinical_events"
    LITIGATION = "litigation_and_court_outcomes"
    PRODUCT_LAUNCH = "product_launches"
    MANAGEMENT_CHANGE = "management_changes"
    INSIDER_TRANSACTION = "insider_transactions"
    OFFERING_DILUTION = "offerings_atm_and_dilution"
    REVERSE_SPLIT = "reverse_splits"
    UNVERIFIED_RUMOUR = "unverified_rumours"
    OTHER = "other_or_uncertain"


class Direction(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    AMBIGUOUS = "ambiguous"


class DilutionRisk(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NumericalDetail:
    kind: str
    label: str
    raw_text: str
    value: float
    unit: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label": self.label,
            "raw_text": self.raw_text,
            "value": self.value,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class SourceDocument:
    document_id: str
    ticker: str
    issuer_id: str | None
    title: str
    text: str
    source_url: str
    source_kind: SourceKind
    published_at: datetime
    first_public_at: datetime
    first_seen_at: datetime
    ingested_at: datetime
    available_at: datetime
    source_timestamp_verified: bool
    source_record_id: str | None = None
    form_type: str | None = None
    form_items: tuple[str, ...] = ()
    accession_number: str | None = None
    expected_catalyst_date: date | None = None
    related_primary_document_id: str | None = None
    structured_numerical_details: tuple[NumericalDetail, ...] = ()

    @property
    def source_tier(self) -> SourceTier:
        return source_tier(self.source_kind)


@dataclass(frozen=True, slots=True)
class SourceBatch:
    provider: str
    dataset_kind: str
    fetched_at: datetime
    documents: tuple[SourceDocument, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalystEvent:
    event_id: str
    document_id: str
    ticker: str
    first_public_at: datetime
    available_at: datetime
    source_url: str
    source_kind: SourceKind
    source_tier: SourceTier
    verification_status: VerificationStatus
    catalyst_category: CatalystCategory
    related_categories: tuple[CatalystCategory, ...]
    direction: Direction
    novelty_score: int
    materiality_score: int
    numerical_details: tuple[NumericalDetail, ...]
    dilution_risk: DilutionRisk
    expected_catalyst_date: date | None
    bull_case: str
    failure_case: str
    is_stale: bool
    duplicate_of_event_id: str | None
    flags: tuple[str, ...]
    classification_evidence: tuple[str, ...]
    classification_version: str

    def to_dict(self) -> dict[str, Any]:
        def timestamp(value: datetime) -> str:
            return value.isoformat().replace("+00:00", "Z")

        return {
            "event_id": self.event_id,
            "document_id": self.document_id,
            "ticker": self.ticker,
            "first_public_timestamp": timestamp(self.first_public_at),
            "available_at": timestamp(self.available_at),
            "source_url": self.source_url,
            "source_kind": self.source_kind.value,
            "source_tier": self.source_tier.value,
            "verification_status": self.verification_status.value,
            "catalyst_category": self.catalyst_category.value,
            "related_categories": [value.value for value in self.related_categories],
            "direction": self.direction.value,
            "novelty_score": self.novelty_score,
            "materiality_score": self.materiality_score,
            "numerical_details": [value.to_dict() for value in self.numerical_details],
            "dilution_risk": self.dilution_risk.value,
            "expected_catalyst_date": (
                self.expected_catalyst_date.isoformat()
                if self.expected_catalyst_date is not None
                else None
            ),
            "bull_case": self.bull_case,
            "failure_case": self.failure_case,
            "is_stale": self.is_stale,
            "duplicate_of_event_id": self.duplicate_of_event_id,
            "flags": list(self.flags),
            "classification_evidence": list(self.classification_evidence),
            "classification_version": self.classification_version,
        }

