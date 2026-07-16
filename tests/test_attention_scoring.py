from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta

from equity_research.retail_attention.contracts import AttentionStage
from equity_research.retail_attention.scoring import (
    AttentionScoringConfig,
    RuleBasedAttentionScorer,
)
from equity_research.retail_attention.synthetic import (
    AS_OF,
    SyntheticAttentionFixtureProvider,
)


def _signals(config: AttentionScoringConfig | None = None):
    batch = SyntheticAttentionFixtureProvider().load()
    values = RuleBasedAttentionScorer(
        config
        or AttentionScoringConfig(
            as_of=AS_OF,
            crowded_mention_count=6,
        )
    ).score(batch)
    return {value.ticker: value for value in values}


class AttentionScoringTests(unittest.TestCase):
    def test_fixture_exercises_expanding_crowded_collapsing_and_quiet(self) -> None:
        signals = _signals()
        self.assertEqual(signals["EARLY"].attention_stage, AttentionStage.EXPANDING)
        self.assertEqual(signals["CROWD"].attention_stage, AttentionStage.CROWDED)
        self.assertEqual(signals["FADE"].attention_stage, AttentionStage.COLLAPSING)
        self.assertEqual(signals["QUIET"].attention_stage, AttentionStage.QUIET)

    def test_accelerating_attention_below_expanding_gate_is_early(self) -> None:
        signals = _signals(
            AttentionScoringConfig(
                as_of=AS_OF,
                crowded_mention_count=100,
                crowded_baseline_score=100.0,
                expanding_baseline_score=99.0,
            )
        )
        self.assertEqual(signals["EARLY"].attention_stage, AttentionStage.EARLY)

    def test_independent_discovery_preserves_primary_catalyst_link(self) -> None:
        signal = _signals()["EARLY"]
        self.assertEqual(signal.raw_mention_count, 3)
        self.assertEqual(signal.unique_author_count, 3)
        self.assertGreater(signal.independent_author_score or 0.0, 75.0)
        self.assertGreater(signal.source_diversity_score or 0.0, 60.0)
        self.assertEqual(
            signal.linked_primary_catalyst_urls,
            ("fixture://company/early/primary-catalyst",),
        )
        self.assertEqual(signal.promotional_language_score, 0.0)

    def test_copied_affiliate_burst_is_downgraded(self) -> None:
        signal = _signals()["CROWD"]
        self.assertEqual(signal.duplicate_language_ratio, 1.0)
        self.assertEqual(signal.independent_author_score, 0.0)
        self.assertEqual(signal.promotional_language_score, 100.0)
        self.assertIn("PROMOTIONAL_GUARANTEE_LANGUAGE", signal.flags)
        self.assertIn("AFFILIATE_OR_PAID_PROMOTION", signal.flags)
        self.assertIn("HIGH_ENGAGEMENT_WITHOUT_PRIMARY_CATALYST", signal.flags)
        self.assertIn("AFTER_LARGE_OBSERVED_MOVE", signal.flags)

    def test_unavailable_future_mentions_do_not_enter_earlier_signal(self) -> None:
        earlier = AS_OF - timedelta(minutes=15)
        signals = _signals(
            AttentionScoringConfig(
                as_of=earlier,
                crowded_mention_count=6,
            )
        )
        self.assertEqual(signals["EARLY"].raw_mention_count, 1)
        self.assertEqual(signals["CROWD"].raw_mention_count, 4)
        self.assertNotIn(
            "fixture://attention/reddit/early-current-1",
            signals["EARLY"].supporting_links,
        )

    def test_incomplete_collection_coverage_nulls_adjusted_baseline(self) -> None:
        batch = SyntheticAttentionFixtureProvider().load()
        descriptor = replace(
            batch.source_descriptors[0],
            coverage_started_at=AS_OF - timedelta(minutes=30),
        )
        batch = replace(
            batch,
            source_descriptors=(descriptor,) + batch.source_descriptors[1:],
        )
        signal = {
            value.ticker: value
            for value in RuleBasedAttentionScorer(
                AttentionScoringConfig(as_of=AS_OF, crowded_mention_count=6)
            ).score(batch)
        }["EARLY"]
        self.assertIsNone(signal.baseline_adjusted_mention_score)
        self.assertEqual(signal.attention_stage, AttentionStage.INSUFFICIENT_DATA)
        self.assertIn(
            "INCOMPLETE_OR_CHANGING_BASELINE_SOURCE_COVERAGE",
            signal.data_completeness_warnings,
        )

    def test_output_explicitly_disclaims_trade_recommendation(self) -> None:
        self.assertEqual(
            _signals()["EARLY"].to_dict()["interpretation"],
            "attention_measurement_only_not_a_trade_recommendation",
        )

    def test_price_context_not_yet_available_at_mention_cannot_downgrade_it(self) -> None:
        batch = SyntheticAttentionFixtureProvider().load()
        delayed_context = replace(
            batch.price_context[1],
            available_at=AS_OF,
        )
        batch = replace(
            batch,
            price_context=(batch.price_context[0], delayed_context, batch.price_context[2]),
        )
        signal = {
            value.ticker: value
            for value in RuleBasedAttentionScorer(
                AttentionScoringConfig(as_of=AS_OF, crowded_mention_count=6)
            ).score(batch)
        }["CROWD"]
        self.assertNotIn("AFTER_LARGE_OBSERVED_MOVE", signal.flags)


if __name__ == "__main__":
    unittest.main()
