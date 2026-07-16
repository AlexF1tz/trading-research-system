from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta

from equity_research.catalyst_intelligence.contracts import (
    CatalystCategory,
    DilutionRisk,
    Direction,
    SourceKind,
    VerificationStatus,
)
from equity_research.catalyst_intelligence.rules import RuleBasedCatalystClassifier
from equity_research.catalyst_intelligence.synthetic import (
    SyntheticCatalystFixtureProvider,
)


class CatalystRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = SyntheticCatalystFixtureProvider().load().documents
        self.events = RuleBasedCatalystClassifier().classify(self.documents)

    def by_category(self, category: CatalystCategory):
        return [event for event in self.events if event.catalyst_category is category]

    def test_fixture_covers_every_requested_category(self) -> None:
        expected = set(CatalystCategory) - {CatalystCategory.OTHER}
        actual = {event.catalyst_category for event in self.events}
        self.assertEqual(actual, expected)

    def test_unverified_social_post_is_never_confirmed(self) -> None:
        event = self.by_category(CatalystCategory.UNVERIFIED_RUMOUR)[0]
        self.assertEqual(event.source_kind, SourceKind.SOCIAL_UNVERIFIED)
        self.assertEqual(event.verification_status, VerificationStatus.UNVERIFIED)
        self.assertEqual(event.direction, Direction.AMBIGUOUS)
        self.assertIn("UNVERIFIED_SOURCE", event.flags)

    def test_social_post_remains_unverified_even_with_primary_reference(self) -> None:
        primary = self.documents[0]
        social = replace(
            self.documents[11],
            related_primary_document_id=primary.document_id,
        )
        event = RuleBasedCatalystClassifier().classify((primary, social))[1]
        self.assertEqual(event.verification_status, VerificationStatus.UNVERIFIED)
        self.assertEqual(event.catalyst_category, CatalystCategory.UNVERIFIED_RUMOUR)

    def test_later_primary_document_does_not_retroactively_verify_secondary(self) -> None:
        primary = self.documents[0]
        secondary = replace(
            self.documents[11],
            source_kind=SourceKind.SECONDARY_NEWS,
            title="Secondary earnings report",
            text="Secondary report says earnings may have improved.",
            related_primary_document_id=primary.document_id,
            available_at=primary.available_at - timedelta(minutes=10),
            first_public_at=primary.first_public_at - timedelta(minutes=10),
            first_seen_at=primary.first_public_at - timedelta(minutes=9),
            ingested_at=primary.first_public_at - timedelta(minutes=8),
        )
        event = RuleBasedCatalystClassifier().classify((secondary, primary))[0]
        self.assertEqual(event.verification_status, VerificationStatus.UNVERIFIED)

    def test_stale_duplicate_and_promotional_flags(self) -> None:
        self.assertEqual(sum(event.is_stale for event in self.events), 1)
        duplicate = [event for event in self.events if event.duplicate_of_event_id]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0].novelty_score, 5)
        promotional = [
            event
            for event in self.events
            if "PROMOTIONAL_WITHOUT_MATERIAL_NUMBERS" in event.flags
        ]
        self.assertEqual(len(promotional), 2)
        self.assertTrue(all(event.direction is Direction.AMBIGUOUS for event in promotional))

    def test_numerical_details_and_dilution(self) -> None:
        offering = self.by_category(CatalystCategory.OFFERING_DILUTION)[0]
        self.assertEqual(offering.dilution_risk, DilutionRisk.HIGH)
        self.assertEqual(offering.direction, Direction.NEGATIVE)
        self.assertTrue(
            any(detail.value == 50_000_000 for detail in offering.numerical_details)
        )
        split = self.by_category(CatalystCategory.REVERSE_SPLIT)[0]
        self.assertTrue(
            any(detail.kind == "ratio" and detail.value == 20 for detail in split.numerical_details)
        )

    def test_contract_has_future_date_cases_and_primary_timestamp(self) -> None:
        contract = self.by_category(CatalystCategory.CONTRACT_PURCHASE_ORDER)[0]
        document = next(
            value for value in self.documents if value.document_id == contract.document_id
        )
        self.assertEqual(contract.first_public_at, document.first_public_at)
        self.assertEqual(contract.expected_catalyst_date.isoformat(), "2026-08-15")
        self.assertTrue(contract.bull_case)
        self.assertTrue(contract.failure_case)


if __name__ == "__main__":
    unittest.main()
