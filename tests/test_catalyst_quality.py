from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta

from equity_research.catalyst_intelligence.contracts import SourceBatch, SourceKind
from equity_research.catalyst_intelligence.quality import run_source_quality_checks
from equity_research.catalyst_intelligence.synthetic import (
    SyntheticCatalystFixtureProvider,
)


class CatalystQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = SyntheticCatalystFixtureProvider().load()
        self.document = self.batch.documents[0]

    def codes(self, document) -> set[str]:
        batch = SourceBatch(
            provider="test",
            dataset_kind="test",
            fetched_at=self.batch.fetched_at,
            documents=(document,),
        )
        return {issue.code for issue in run_source_quality_checks(batch)}

    def test_fixture_source_quality_has_no_errors(self) -> None:
        errors = [
            issue
            for issue in run_source_quality_checks(self.batch)
            if issue.severity.value == "error"
        ]
        self.assertEqual(errors, [])

    def test_public_time_after_discovery_fails(self) -> None:
        invalid = replace(
            self.document,
            first_public_at=self.document.first_seen_at + timedelta(seconds=1),
        )
        self.assertIn("PUBLIC_TIME_AFTER_DISCOVERY", self.codes(invalid))

    def test_unverified_timestamp_cannot_be_used_before_first_seen(self) -> None:
        invalid = replace(
            self.document,
            source_kind=SourceKind.SECONDARY_NEWS,
            source_timestamp_verified=False,
            available_at=self.document.first_seen_at - timedelta(seconds=1),
        )
        self.assertIn("UNVERIFIED_TIME_USED_EARLY", self.codes(invalid))

    def test_sec_url_must_be_sec_domain_or_fixture(self) -> None:
        invalid = replace(self.document, source_url="https://example.com/fake-sec")
        self.assertIn("SEC_SOURCE_DOMAIN_MISMATCH", self.codes(invalid))

    def test_duplicate_document_ids_fail(self) -> None:
        batch = replace(self.batch, documents=(self.document, self.document))
        codes = {issue.code for issue in run_source_quality_checks(batch)}
        self.assertIn("DUPLICATE_DOCUMENT_ID", codes)


if __name__ == "__main__":
    unittest.main()

