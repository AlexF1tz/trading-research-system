from __future__ import annotations

import unittest
from dataclasses import replace

from equity_research.retail_attention.pipeline import (
    AttentionPipeline,
    AttentionPipelineConfig,
)
from equity_research.retail_attention.quality import (
    AttentionQualityError,
    run_attention_quality_checks,
)
from equity_research.retail_attention.scoring import AttentionScoringConfig
from equity_research.retail_attention.synthetic import (
    AS_OF,
    SyntheticAttentionFixtureProvider,
)


class StaticProvider:
    def __init__(self, batch: object) -> None:
        self._batch = batch

    @property
    def name(self) -> str:
        return "test"

    def load(self):
        return self._batch


class AttentionQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = SyntheticAttentionFixtureProvider().load()

    def test_synthetic_batch_has_no_quality_issues(self) -> None:
        self.assertEqual(run_attention_quality_checks(self.batch), ())

    def test_unconfirmed_collection_is_an_error_and_pipeline_fails_closed(self) -> None:
        descriptor = replace(
            self.batch.source_descriptors[0],
            collection_authorization_confirmed=False,
        )
        batch = replace(
            self.batch,
            source_descriptors=(descriptor,) + self.batch.source_descriptors[1:],
        )
        codes = {issue.code for issue in run_attention_quality_checks(batch)}
        self.assertIn("COLLECTION_NOT_AUTHORIZED", codes)
        with self.assertRaises(AttentionQualityError):
            AttentionPipeline(
                StaticProvider(batch),
                AttentionPipelineConfig(AttentionScoringConfig(as_of=AS_OF)),
            ).run()

    def test_text_and_account_metrics_require_declared_permission(self) -> None:
        descriptor = replace(
            self.batch.source_descriptors[0],
            text_analysis_permitted=False,
            author_metrics_permitted=False,
        )
        batch = replace(
            self.batch,
            source_descriptors=(descriptor,) + self.batch.source_descriptors[1:],
        )
        codes = {issue.code for issue in run_attention_quality_checks(batch)}
        self.assertIn("TEXT_USE_NOT_PERMITTED", codes)
        self.assertIn("AUTHOR_METRICS_NOT_PERMITTED", codes)

    def test_naive_timestamp_is_reported_without_crashing(self) -> None:
        mention = replace(
            self.batch.mentions[0],
            published_at=self.batch.mentions[0].published_at.replace(tzinfo=None),
        )
        batch = replace(
            self.batch,
            mentions=(mention,) + self.batch.mentions[1:],
        )
        codes = {issue.code for issue in run_attention_quality_checks(batch)}
        self.assertIn("TIMEZONE_NOT_UTC", codes)

    def test_content_hash_mismatch_is_an_error(self) -> None:
        mention = replace(self.batch.mentions[0], content_hash="0" * 64)
        batch = replace(
            self.batch,
            mentions=(mention,) + self.batch.mentions[1:],
        )
        codes = {issue.code for issue in run_attention_quality_checks(batch)}
        self.assertIn("CONTENT_HASH_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
