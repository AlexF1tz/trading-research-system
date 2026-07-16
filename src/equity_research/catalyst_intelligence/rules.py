"""Auditable catalyst taxonomy, evidence extraction, and event scoring rules."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .contracts import (
    CatalystCategory,
    CatalystEvent,
    DilutionRisk,
    Direction,
    NumericalDetail,
    SourceDocument,
    SourceKind,
    SourceTier,
    VerificationStatus,
)


CLASSIFICATION_VERSION = "rules-v1"


CATEGORY_RULES: tuple[
    tuple[CatalystCategory, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        CatalystCategory.REVERSE_SPLIT,
        ("reverse split", "reverse stock split", "share consolidation"),
        (),
    ),
    (
        CatalystCategory.OFFERING_DILUTION,
        (
            "at-the-market",
            "atm program",
            "registered direct",
            "public offering",
            "private placement",
            "shelf registration",
            "warrant exercise",
            "dilution",
        ),
        ("S-1", "S-3", "424B3", "424B5", "424B"),
    ),
    (
        CatalystCategory.INSIDER_TRANSACTION,
        ("insider purchase", "insider sale", "beneficial ownership"),
        ("4",),
    ),
    (
        CatalystCategory.FDA_CLINICAL,
        (
            "fda",
            "food and drug administration",
            "clinical trial",
            "phase 1",
            "phase 2",
            "phase 3",
            "topline data",
            "clinical hold",
            "pdufa",
        ),
        (),
    ),
    (
        CatalystCategory.MERGER_ACQUISITION,
        (
            "merger",
            "acquisition",
            "to be acquired",
            "tender offer",
            "business combination",
            "strategic alternatives",
        ),
        ("SC TO", "DEFM14A", "S-4"),
    ),
    (
        CatalystCategory.EARNINGS_GUIDANCE,
        (
            "earnings",
            "quarterly results",
            "financial results",
            "revenue",
            "guidance",
            "earnings per share",
            "eps",
        ),
        ("10-Q", "10-K"),
    ),
    (
        CatalystCategory.CONTRACT_PURCHASE_ORDER,
        (
            "purchase order",
            "contract awarded",
            "contract award",
            "definitive contract",
            "supply agreement",
            "task order",
        ),
        (),
    ),
    (
        CatalystCategory.PARTNERSHIP,
        (
            "partnership",
            "strategic collaboration",
            "collaboration agreement",
            "joint development",
        ),
        (),
    ),
    (
        CatalystCategory.LITIGATION,
        (
            "litigation",
            "lawsuit",
            "court",
            "judgment",
            "settlement",
            "injunction",
        ),
        (),
    ),
    (
        CatalystCategory.PRODUCT_LAUNCH,
        ("product launch", "launches", "commercial launch", "new product"),
        (),
    ),
    (
        CatalystCategory.MANAGEMENT_CHANGE,
        (
            "chief executive officer",
            "chief financial officer",
            "appoints ceo",
            "appoints cfo",
            "resigns",
            "management transition",
        ),
        (),
    ),
)


SOURCE_PRIORITY = {
    SourceKind.SEC_FILING: 100,
    SourceKind.REGULATOR_ANNOUNCEMENT: 95,
    SourceKind.EXCHANGE_ANNOUNCEMENT: 90,
    SourceKind.COMPANY_IR: 85,
    SourceKind.SECONDARY_NEWS: 50,
    SourceKind.SOCIAL_UNVERIFIED: 10,
}


PROMOTIONAL_TERMS = frozenset(
    {
        "breakthrough",
        "revolutionary",
        "transformative",
        "game-changing",
        "industry-leading",
        "unprecedented",
        "exciting",
        "massive opportunity",
    }
)


MONEY_PATTERN = re.compile(
    r"\$\s*([0-9]+(?:\.[0-9]+)?|[0-9][0-9,]*)\s*"
    r"(billion|million|thousand|bn|mm|m|k)?\b",
    re.IGNORECASE,
)
PERCENT_PATTERN = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*%")
SHARES_PATTERN = re.compile(
    r"\b([0-9]+(?:\.[0-9]+)?|[0-9][0-9,]*)\s*"
    r"(billion|million|thousand|bn|mm|m|k)?\s+shares\b",
    re.IGNORECASE,
)
RATIO_PATTERN = re.compile(r"\b([0-9]+)\s*[- ]for[- ]\s*([0-9]+)\b", re.IGNORECASE)
EXPECTED_DATE_PATTERN = re.compile(
    r"\b(?:expected|scheduled|anticipated)\b.{0,40}?\b(20[0-9]{2}-[0-9]{2}-[0-9]{2})\b",
    re.IGNORECASE,
)


MATERIALITY_BASE = {
    CatalystCategory.MERGER_ACQUISITION: 70,
    CatalystCategory.FDA_CLINICAL: 70,
    CatalystCategory.EARNINGS_GUIDANCE: 65,
    CatalystCategory.OFFERING_DILUTION: 65,
    CatalystCategory.LITIGATION: 55,
    CatalystCategory.CONTRACT_PURCHASE_ORDER: 50,
    CatalystCategory.REVERSE_SPLIT: 50,
    CatalystCategory.INSIDER_TRANSACTION: 45,
    CatalystCategory.MANAGEMENT_CHANGE: 40,
    CatalystCategory.PARTNERSHIP: 40,
    CatalystCategory.PRODUCT_LAUNCH: 35,
    CatalystCategory.UNVERIFIED_RUMOUR: 10,
    CatalystCategory.OTHER: 20,
}


@dataclass(frozen=True, slots=True)
class ClassifierConfig:
    stale_after_hours: int = 24
    duplicate_similarity_threshold: float = 0.82
    recent_category_days: int = 7


def _normalized_text(document: SourceDocument) -> str:
    return re.sub(r"\s+", " ", f"{document.title} {document.text}".lower()).strip()


def _tokens(value: str) -> frozenset[str]:
    ignored = {
        "the",
        "and",
        "of",
        "to",
        "a",
        "in",
        "for",
        "company",
        "announces",
        "said",
    }
    return frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if token not in ignored and len(token) > 1
    )


def _similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens and not right_tokens:
        return 1.0
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def _scaled_number(raw: str, suffix: str | None) -> float:
    value = float(raw.replace(",", ""))
    multiplier = {
        "billion": 1_000_000_000.0,
        "bn": 1_000_000_000.0,
        "million": 1_000_000.0,
        "mm": 1_000_000.0,
        "m": 1_000_000.0,
        "thousand": 1_000.0,
        "k": 1_000.0,
    }.get((suffix or "").lower(), 1.0)
    return value * multiplier


def extract_numerical_details(document: SourceDocument) -> tuple[NumericalDetail, ...]:
    text = f"{document.title}. {document.text}"
    values: list[NumericalDetail] = list(document.structured_numerical_details)
    for match in MONEY_PATTERN.finditer(text):
        context = text[max(0, match.start() - 35) : match.end() + 35].lower()
        label = "eps" if "eps" in context or "earnings per share" in context else "money"
        values.append(
            NumericalDetail(
                kind="money",
                label=label,
                raw_text=match.group(0),
                value=_scaled_number(match.group(1), match.group(2)),
                unit="USD",
            )
        )
    for match in PERCENT_PATTERN.finditer(text):
        values.append(
            NumericalDetail(
                kind="percentage",
                label="percentage",
                raw_text=match.group(0),
                value=float(match.group(1)),
                unit="percent",
            )
        )
    for match in SHARES_PATTERN.finditer(text):
        values.append(
            NumericalDetail(
                kind="shares",
                label="share_count",
                raw_text=match.group(0),
                value=_scaled_number(match.group(1), match.group(2)),
                unit="shares",
            )
        )
    for match in RATIO_PATTERN.finditer(text):
        numerator, denominator = float(match.group(1)), float(match.group(2))
        values.append(
            NumericalDetail(
                kind="ratio",
                label="split_ratio",
                raw_text=match.group(0),
                value=denominator / numerator if numerator else 0.0,
                unit="new_shares_per_old_share_denominator",
            )
        )
    deduplicated: dict[tuple[str, str, float, str], NumericalDetail] = {}
    for value in values:
        key = (value.kind, value.label, value.value, value.unit)
        deduplicated.setdefault(key, value)
    return tuple(deduplicated.values())


def _categories(document: SourceDocument) -> tuple[
    CatalystCategory, tuple[CatalystCategory, ...], tuple[str, ...]
]:
    text = _normalized_text(document)
    if document.source_kind is SourceKind.SOCIAL_UNVERIFIED or any(
        phrase in text for phrase in ("unverified rumour", "unverified rumor", "rumoured", "rumored")
    ):
        return (
            CatalystCategory.UNVERIFIED_RUMOUR,
            (),
            ("unverified source or rumour wording",),
        )
    matches: list[CatalystCategory] = []
    evidence: list[str] = []
    form_type = (document.form_type or "").upper()
    for category, phrases, forms in CATEGORY_RULES:
        phrase_hits = tuple(phrase for phrase in phrases if phrase in text)
        form_hit = form_type in forms
        item_hit = (
            category is CatalystCategory.MANAGEMENT_CHANGE
            and "5.02" in document.form_items
        )
        if phrase_hits or form_hit or item_hit:
            matches.append(category)
            evidence.extend(f"phrase:{value}" for value in phrase_hits[:3])
            if form_hit:
                evidence.append(f"form:{form_type}")
            if item_hit:
                evidence.append("form_item:5.02")
    if not matches:
        return CatalystCategory.OTHER, (), ("no deterministic category rule matched",)
    return matches[0], tuple(matches[1:]), tuple(dict.fromkeys(evidence))


def _verification(
    document: SourceDocument, documents_by_id: dict[str, SourceDocument]
) -> VerificationStatus:
    if document.source_kind is SourceKind.SOCIAL_UNVERIFIED:
        return VerificationStatus.UNVERIFIED
    if document.source_tier is SourceTier.PRIMARY:
        return VerificationStatus.CONFIRMED_PRIMARY
    if document.related_primary_document_id:
        primary = documents_by_id.get(document.related_primary_document_id)
        if (
            primary is not None
            and primary.source_tier is SourceTier.PRIMARY
            and primary.available_at <= document.available_at
        ):
            return VerificationStatus.CORROBORATED
    return VerificationStatus.UNVERIFIED


def _direction(
    category: CatalystCategory,
    text: str,
    numerical_details: tuple[NumericalDetail, ...],
    promotional_without_numbers: bool,
) -> Direction:
    if category is CatalystCategory.UNVERIFIED_RUMOUR:
        return Direction.AMBIGUOUS
    if category in {CatalystCategory.OFFERING_DILUTION, CatalystCategory.REVERSE_SPLIT}:
        return Direction.NEGATIVE
    negative = any(
        phrase in text
        for phrase in (
            "clinical hold",
            "failed",
            "missed",
            "lowers guidance",
            "withdraws guidance",
            "resigns",
            "terminated",
            "cancelled",
            "canceled",
            "judgment against",
            "rejected",
            "denied",
        )
    )
    positive = any(
        phrase in text
        for phrase in (
            "approved",
            "contract awarded",
            "purchase order",
            "beat expectations",
            "raises guidance",
            "positive topline",
            "to be acquired",
            "premium",
            "insider purchase",
            "dismissed",
        )
    )
    if negative and not positive:
        return Direction.NEGATIVE
    if positive and not negative:
        return Direction.POSITIVE
    if category is CatalystCategory.CONTRACT_PURCHASE_ORDER and numerical_details and not negative:
        return Direction.POSITIVE
    if category is CatalystCategory.INSIDER_TRANSACTION:
        if "purchase" in text or "acquired" in text:
            return Direction.POSITIVE
        if "sale" in text or "sold" in text or "disposed" in text:
            return Direction.NEGATIVE
    if category is CatalystCategory.PRODUCT_LAUNCH and numerical_details and not promotional_without_numbers:
        return Direction.POSITIVE
    return Direction.AMBIGUOUS


def _dilution_risk(category: CatalystCategory, text: str) -> DilutionRisk:
    if category is CatalystCategory.OFFERING_DILUTION:
        if any(
            phrase in text
            for phrase in (
                "at-the-market",
                "atm program",
                "registered direct",
                "public offering",
                "private placement",
                "warrant exercise",
            )
        ):
            return DilutionRisk.HIGH
        return DilutionRisk.MEDIUM
    if category is CatalystCategory.REVERSE_SPLIT:
        return DilutionRisk.MEDIUM
    if "warrant" in text or "convertible" in text:
        return DilutionRisk.MEDIUM
    return DilutionRisk.NONE


def _expected_date(document: SourceDocument, text: str) -> date | None:
    if document.expected_catalyst_date is not None:
        return document.expected_catalyst_date
    match = EXPECTED_DATE_PATTERN.search(text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


BULL_CASES = {
    CatalystCategory.EARNINGS_GUIDANCE: "Results or guidance may indicate improving growth, margins, or expectations.",
    CatalystCategory.CONTRACT_PURCHASE_ORDER: "The award may add revenue and validate demand if it becomes recognised sales.",
    CatalystCategory.PARTNERSHIP: "The relationship may expand distribution, capability, or future commercial opportunities.",
    CatalystCategory.MERGER_ACQUISITION: "A completed transaction may crystallise value or add a strategic premium.",
    CatalystCategory.FDA_CLINICAL: "A favourable regulatory or clinical outcome may improve approval and commercial odds.",
    CatalystCategory.LITIGATION: "A favourable outcome may remove liability or operational uncertainty.",
    CatalystCategory.PRODUCT_LAUNCH: "A successful launch may create incremental revenue and customer adoption.",
    CatalystCategory.MANAGEMENT_CHANGE: "New leadership may improve execution or strategic credibility.",
    CatalystCategory.INSIDER_TRANSACTION: "A genuine open-market purchase may signal insider conviction.",
    CatalystCategory.OFFERING_DILUTION: "New capital may extend runway or fund a value-creating programme.",
    CatalystCategory.REVERSE_SPLIT: "The action may restore listing compliance and preserve market access.",
    CatalystCategory.UNVERIFIED_RUMOUR: "If later confirmed by a primary source, the claimed event could reprice expectations.",
    CatalystCategory.OTHER: "The event may prove material if primary evidence or numerical detail emerges.",
}


FAILURE_CASES = {
    CatalystCategory.EARNINGS_GUIDANCE: "Headline strength may be non-recurring, below consensus, or offset by weaker cash flow.",
    CatalystCategory.CONTRACT_PURCHASE_ORDER: "The order may be non-binding, delayed, low-margin, cancellable, or immaterial.",
    CatalystCategory.PARTNERSHIP: "The announcement may lack binding economics, exclusivity, milestones, or near-term revenue.",
    CatalystCategory.MERGER_ACQUISITION: "Financing, approvals, conditions, dilution, or termination risk may prevent completion.",
    CatalystCategory.FDA_CLINICAL: "The evidence may be preliminary, underpowered, unsafe, delayed, or insufficient for approval.",
    CatalystCategory.LITIGATION: "Appeals, damages, legal costs, or unresolved claims may preserve the downside.",
    CatalystCategory.PRODUCT_LAUNCH: "Adoption, pricing, competition, margins, or production may disappoint.",
    CatalystCategory.MANAGEMENT_CHANGE: "The transition may expose governance, continuity, or execution problems.",
    CatalystCategory.INSIDER_TRANSACTION: "The filing may reflect compensation, tax, or planned-sale activity rather than conviction.",
    CatalystCategory.OFFERING_DILUTION: "Issuance may dilute holders and create persistent supply despite added cash.",
    CatalystCategory.REVERSE_SPLIT: "The action does not fix fundamentals and may precede further dilution or weakness.",
    CatalystCategory.UNVERIFIED_RUMOUR: "The claim may be false, stale, manipulated, or never confirmed by a primary source.",
    CatalystCategory.OTHER: "The available evidence may remain too weak or immaterial to change valuation.",
}


def _score_novelty(
    document: SourceDocument,
    duplicate: CatalystEvent | None,
    stale: bool,
    promotional_without_numbers: bool,
    prior_same_category: bool,
) -> int:
    if duplicate is not None:
        return 5
    value = 90 if document.source_tier is SourceTier.PRIMARY else 75
    if stale:
        value -= 30
    if promotional_without_numbers:
        value -= 20
    if prior_same_category:
        value -= 15
    return max(0, min(100, value))


def _score_materiality(
    category: CatalystCategory,
    document: SourceDocument,
    verification: VerificationStatus,
    numerical_details: tuple[NumericalDetail, ...],
    duplicate: CatalystEvent | None,
    stale: bool,
    promotional_without_numbers: bool,
) -> int:
    value = MATERIALITY_BASE[category]
    value += min(20, len(numerical_details) * 5)
    if document.source_tier is SourceTier.PRIMARY:
        value += 5
    if verification in {
        VerificationStatus.CONFIRMED_PRIMARY,
        VerificationStatus.CORROBORATED,
    }:
        value += 5
    if any(detail.kind == "money" and detail.value >= 10_000_000 for detail in numerical_details):
        value += 10
    if duplicate is not None:
        value -= 45
    if stale:
        value -= 10
    if promotional_without_numbers:
        value -= 25
    if category is CatalystCategory.UNVERIFIED_RUMOUR:
        value = min(value, 15)
    return max(0, min(100, value))


class RuleBasedCatalystClassifier:
    def __init__(self, config: ClassifierConfig | None = None) -> None:
        self._config = config or ClassifierConfig()

    def classify(self, documents: tuple[SourceDocument, ...]) -> tuple[CatalystEvent, ...]:
        documents_by_id = {document.document_id: document for document in documents}
        ordered = sorted(
            documents,
            key=lambda value: (
                value.available_at,
                -SOURCE_PRIORITY[value.source_kind],
                value.document_id,
            ),
        )
        events: list[CatalystEvent] = []
        event_text: dict[str, str] = {}
        for document in ordered:
            text = _normalized_text(document)
            category, related, evidence = _categories(document)
            numbers = extract_numerical_details(document)
            promotional_count = sum(term in text for term in PROMOTIONAL_TERMS)
            promotional_without_numbers = promotional_count >= 2 and not numbers
            stale = document.first_seen_at - document.first_public_at > timedelta(
                hours=self._config.stale_after_hours
            )
            duplicate: CatalystEvent | None = None
            for prior in reversed(events):
                if prior.ticker != document.ticker or prior.catalyst_category is not category:
                    continue
                similarity = _similarity(text, event_text[prior.event_id])
                if similarity >= self._config.duplicate_similarity_threshold:
                    duplicate = prior
                    break
            prior_same_category = any(
                prior.ticker == document.ticker
                and prior.catalyst_category is category
                and document.first_public_at - prior.first_public_at
                <= timedelta(days=self._config.recent_category_days)
                for prior in events
                if prior.first_public_at <= document.first_public_at
            )
            verification = _verification(document, documents_by_id)
            direction = _direction(
                category, text, numbers, promotional_without_numbers
            )
            dilution = _dilution_risk(category, text)
            flags: list[str] = []
            if stale:
                flags.append("STALE_NEWS")
            if duplicate is not None:
                flags.append("REPEATED_ANNOUNCEMENT")
            if promotional_without_numbers:
                flags.append("PROMOTIONAL_WITHOUT_MATERIAL_NUMBERS")
            if verification is VerificationStatus.UNVERIFIED:
                flags.append("UNVERIFIED_SOURCE")
            if not document.source_timestamp_verified:
                flags.append("TIMESTAMP_UNVERIFIED")
            if dilution is DilutionRisk.HIGH:
                flags.append("DILUTION_RISK_HIGH")
            event_id = hashlib.sha256(
                f"{document.document_id}|{category.value}|{CLASSIFICATION_VERSION}".encode()
            ).hexdigest()[:24]
            event = CatalystEvent(
                event_id=event_id,
                document_id=document.document_id,
                ticker=document.ticker,
                first_public_at=document.first_public_at,
                available_at=document.available_at,
                source_url=document.source_url,
                source_kind=document.source_kind,
                source_tier=document.source_tier,
                verification_status=verification,
                catalyst_category=category,
                related_categories=related,
                direction=direction,
                novelty_score=_score_novelty(
                    document,
                    duplicate,
                    stale,
                    promotional_without_numbers,
                    prior_same_category,
                ),
                materiality_score=_score_materiality(
                    category,
                    document,
                    verification,
                    numbers,
                    duplicate,
                    stale,
                    promotional_without_numbers,
                ),
                numerical_details=numbers,
                dilution_risk=dilution,
                expected_catalyst_date=_expected_date(document, text),
                bull_case=BULL_CASES[category],
                failure_case=FAILURE_CASES[category],
                is_stale=stale,
                duplicate_of_event_id=(duplicate.event_id if duplicate else None),
                flags=tuple(flags),
                classification_evidence=evidence,
                classification_version=CLASSIFICATION_VERSION,
            )
            events.append(event)
            event_text[event_id] = text
        return tuple(events)
