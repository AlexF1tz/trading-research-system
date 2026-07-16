from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from equity_research.retail_attention.pipeline import (
    AttentionPipeline,
    AttentionPipelineConfig,
)
from equity_research.retail_attention.provider import JsonlAttentionProvider
from equity_research.retail_attention.sample import run_sample
from equity_research.retail_attention.scoring import AttentionScoringConfig
from equity_research.retail_attention.synthetic import (
    AS_OF,
    SyntheticAttentionFixtureProvider,
)


class AttentionPipelineTests(unittest.TestCase):
    def test_pipeline_emits_one_signal_for_every_monitored_ticker(self) -> None:
        result = AttentionPipeline(
            SyntheticAttentionFixtureProvider(),
            AttentionPipelineConfig(
                AttentionScoringConfig(as_of=AS_OF, crowded_mention_count=6)
            ),
        ).run()
        self.assertEqual(len(result.signals), 4)
        self.assertEqual(result.error_count, 0)
        quiet = next(value for value in result.signals if value.ticker == "QUIET")
        self.assertEqual(quiet.raw_mention_count, 0)

    def test_sample_writes_required_measurements_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = run_sample(
                Path(directory), Path("config/retail_attention.sample.json")
            )
            self.assertEqual(
                summary["status"],
                "ENGINEERING_FIXTURE_NOT_SOCIAL_OR_MARKET_DATA",
            )
            rows = [
                json.loads(value)
                for value in (Path(directory) / "attention_signals.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(rows), 4)
            required = {
                "raw_mention_count",
                "baseline_adjusted_mention_score",
                "independent_author_score",
                "promotional_language_score",
                "attention_acceleration_per_hour2",
                "source_diversity_score",
                "attention_stage",
                "supporting_links",
                "data_completeness_warning",
            }
            self.assertTrue(all(required <= set(row) for row in rows))
            self.assertTrue((Path(directory) / "quality_report.json").exists())
            self.assertTrue((Path(directory) / "run_manifest.json").exists())

    def test_as_of_after_fetch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AttentionPipeline(
                SyntheticAttentionFixtureProvider(),
                AttentionPipelineConfig(
                    AttentionScoringConfig(as_of=AS_OF.replace(hour=15))
                ),
            ).run()

    def test_jsonl_directory_is_a_replaceable_offline_provider(self) -> None:
        text = "A permitted normalized attention extract with catalyst context"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = {
                "provider": "authorized-test-export",
                "dataset_kind": "normalized_test_export",
                "fetched_at": "2026-07-15T14:00:00Z",
                "monitored_securities": [
                    {"security_id": "SEC-TEST", "ticker": "TEST"}
                ],
                "sources": [
                    {
                        "source": "other_public",
                        "access_method": "engineering_fixture",
                        "collection_authorization_confirmed": True,
                        "terms_url": "fixture://terms/test",
                        "terms_reviewed_at": "2026-07-15T11:00:00Z",
                        "rate_limit_policy": "no network requests in contract test",
                        "coverage_started_at": "2026-07-15T11:00:00Z",
                        "coverage_ended_at": None,
                        "text_analysis_permitted": True,
                        "content_storage": "excerpt_approved",
                        "author_metrics_permitted": True,
                        "coverage_note": "offline contract test only",
                    }
                ],
            }
            mention = {
                "mention_id": "mention-test",
                "security_id": "SEC-TEST",
                "ticker": "TEST",
                "source": "other_public",
                "source_record_id": "source-test",
                "source_url": "fixture://attention/test",
                "published_at": "2026-07-15T13:50:00Z",
                "first_seen_at": "2026-07-15T13:50:10Z",
                "ingested_at": "2026-07-15T13:50:10Z",
                "available_at": "2026-07-15T13:50:10Z",
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
                "author_key": "source-scoped-pseudonym",
                "is_repost": False,
                "account_quality_score": 0.5,
                "account_quality_basis": "test fixture basis",
                "affiliate_or_paid_promotion": False,
            }
            (root / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            (root / "mentions.jsonl").write_text(
                json.dumps(mention) + "\n", encoding="utf-8"
            )
            result = AttentionPipeline(
                JsonlAttentionProvider(root),
                AttentionPipelineConfig(AttentionScoringConfig(as_of=AS_OF)),
            ).run()
            self.assertEqual(result.signals[0].raw_mention_count, 1)
            self.assertEqual(result.error_count, 0)


if __name__ == "__main__":
    unittest.main()
