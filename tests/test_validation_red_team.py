from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields, replace
from datetime import timedelta
from pathlib import Path

from equity_research.catalyst_intelligence.contracts import SourceBatch
from equity_research.catalyst_intelligence.quality import run_source_quality_checks
from equity_research.catalyst_intelligence.synthetic import (
    SyntheticCatalystFixtureProvider,
)
from equity_research.modelling.contracts import ModelRow
from equity_research.modelling.quality import run_modelling_quality_checks
from equity_research.modelling.synthetic import SyntheticModelFixtureProvider
from equity_research.retail_attention.scoring import (
    AttentionScoringConfig,
    RuleBasedAttentionScorer,
)
from equity_research.retail_attention.synthetic import (
    AS_OF,
    SyntheticAttentionFixtureProvider,
)
from equity_research.validation.audit import scan_for_random_time_splits
from equity_research.validation.sample import run_validation_sample


class ValidationRedTeamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.output = Path(cls._temporary.name)
        cls.summary = run_validation_sample(
            cls.output,
            Path("config/validation.sample.json"),
            Path("."),
        )
        cls.report = json.loads(
            (cls.output / "validation_report.json").read_text(encoding="utf-8")
        )
        cls.findings = {
            finding["check_id"]: finding for finding in cls.report["findings"]
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_independent_reproduction_matches_every_reported_metric(self) -> None:
        reproduction = self.report["metric_reproduction"]
        self.assertTrue(reproduction["passed"])
        self.assertEqual(reproduction["checks"], 2247)
        self.assertEqual(reproduction["matches"], reproduction["checks"])
        self.assertEqual(reproduction["mismatches"], [])
        self.assertEqual(
            reproduction["implementation_independence"],
            "does_not_import_equity_research.modelling.evaluation",
        )
        self.assertIn("final-test", reproduction["coverage"])

    def test_walk_forward_selection_requires_fold_artifacts(self) -> None:
        finding = self.findings["MODEL_SELECTION_REPRODUCTION"]
        self.assertEqual(finding["status"], "not_verifiable")
        self.assertIn(
            "Per-fold observation membership",
            " ".join(finding["evidence"]),
        )

    def test_future_feature_and_outcome_injection_fail_closed(self) -> None:
        dataset = SyntheticModelFixtureProvider().load()
        original = dataset.rows[0]
        injected = replace(
            original,
            features_available_at=original.prediction_as_of + timedelta(seconds=1),
            outcome_available_at=original.prediction_as_of,
        )
        issues = run_modelling_quality_checks(
            replace(dataset, rows=(injected,) + dataset.rows[1:])
        )
        codes = {issue.code for issue in issues}
        self.assertIn("FEATURE_AVAILABLE_AFTER_PREDICTION", codes)
        self.assertIn("OUTCOME_AVAILABLE_AT_PREDICTION", codes)

    def test_repository_has_no_random_time_series_split_calls(self) -> None:
        self.assertEqual(scan_for_random_time_splits(Path(".").resolve()), ())
        self.assertEqual(self.findings["TIME_SPLIT"]["status"], "pass")

    def test_limited_universe_cannot_support_survivorship_safe_claim(self) -> None:
        self.assertEqual(self.findings["SURVIVORSHIP"]["status"], "fail")
        self.assertIn("SURVIVORSHIP", self.report["blockers"])

    def test_incorrect_announcement_timestamp_is_rejected(self) -> None:
        fixture = SyntheticCatalystFixtureProvider().load()
        document = fixture.documents[0]
        invalid = replace(
            document,
            first_public_at=document.first_seen_at + timedelta(seconds=1),
        )
        batch = SourceBatch(
            provider="red-team",
            dataset_kind="adversarial_fixture",
            fetched_at=fixture.fetched_at,
            documents=(invalid,),
        )
        codes = {issue.code for issue in run_source_quality_checks(batch)}
        self.assertIn("PUBLIC_TIME_AFTER_DISCOVERY", codes)
        self.assertEqual(
            self.findings["ANNOUNCEMENT_TIME"]["status"], "not_verifiable"
        )

    def test_model_rows_require_revision_lineage_before_promotion(self) -> None:
        names = {field.name for field in fields(ModelRow)}
        self.assertTrue(
            {"raw_response_hash", "revision_id", "source_snapshot_id"}.isdisjoint(
                names
            )
        )
        self.assertEqual(self.findings["REVISED_DATA"]["status"], "fail")

    def test_omitted_or_miscalculated_costs_fail_closed(self) -> None:
        dataset = SyntheticModelFixtureProvider().load()
        filled = next(
            row for row in dataset.rows if row.net_return_after_cost_pct is not None
        )
        invalid = replace(
            filled,
            net_return_after_cost_pct=filled.net_return_after_cost_pct + 0.25,
        )
        rows = tuple(
            invalid if row.observation_id == invalid.observation_id else row
            for row in dataset.rows
        )
        codes = {
            issue.code
            for issue in run_modelling_quality_checks(replace(dataset, rows=rows))
        }
        self.assertIn("NET_RETURN_COST_MISMATCH", codes)
        self.assertEqual(self.findings["DECLARED_COSTS"]["status"], "pass")

    def test_missing_fill_fidelity_blocks_model_promotion(self) -> None:
        names = {field.name for field in fields(ModelRow)}
        required = {
            "quote_size",
            "participation_rate",
            "order_latency_ms",
            "fill_fidelity",
            "same_barrier_touch_policy",
        }
        self.assertTrue(required.isdisjoint(names))
        self.assertEqual(self.findings["FILL_REALISM"]["status"], "fail")

    def test_missing_halt_evidence_blocks_model_promotion(self) -> None:
        names = {field.name for field in fields(ModelRow)}
        self.assertTrue(
            {"halt_intervals", "halt_source_id", "reopening_price"}.isdisjoint(names)
        )
        self.assertEqual(self.findings["HALTS"]["status"], "fail")

    def test_missing_candidate_ledger_blocks_model_promotion(self) -> None:
        names = {field.name for field in fields(ModelRow)}
        self.assertTrue(
            {"candidate_universe_id", "eligibility_decision", "rejection_reason"}.isdisjoint(
                names
            )
        )
        self.assertEqual(self.findings["SELECTION_BIAS"]["status"], "fail")

    def test_missing_catalyst_discovery_manifest_blocks_model_promotion(self) -> None:
        names = {field.name for field in fields(ModelRow)}
        self.assertNotIn("catalyst_discovery_manifest_id", names)
        self.assertEqual(self.findings["CATALYST_SELECTION"]["status"], "fail")

    def test_duplicated_social_posts_do_not_increase_independent_attention(self) -> None:
        batch = SyntheticAttentionFixtureProvider().load()
        signals = {
            signal.ticker: signal
            for signal in RuleBasedAttentionScorer(
                AttentionScoringConfig(as_of=AS_OF, crowded_mention_count=6)
            ).score(batch)
        }
        copied = signals["CROWD"]
        self.assertEqual(copied.duplicate_language_ratio, 1.0)
        self.assertEqual(copied.independent_author_score, 0.0)
        self.assertEqual(
            self.findings["SOCIAL_DUPLICATION"]["status"], "not_verifiable"
        )

    def test_final_holdout_requires_access_registry_before_promotion(self) -> None:
        self.assertEqual(self.findings["REPEATED_TESTING"]["status"], "fail")
        self.assertIn(
            "final-test pipeline is rerunnable",
            " ".join(self.findings["REPEATED_TESTING"]["evidence"]),
        )

    def test_security_concentration_and_leave_one_out_are_reported(self) -> None:
        concentration = self.report["security_concentration"]
        self.assertGreater(len(concentration["leave_one_security_out"]), 1)
        self.assertGreaterEqual(concentration["maximum_single_security_share"], 0.0)
        self.assertLessEqual(concentration["maximum_single_security_share"], 1.0)
        self.assertEqual(self.findings["SECURITY_CONCENTRATION"]["status"], "warning")

    def test_cost_stress_failures_are_rejected(self) -> None:
        stress = self.report["cost_stress_classification_models"]
        failed = {
            (target, model)
            for target, models in stress.items()
            for model, values in models.items()
            if not values["all_scenarios_positive"]
        }
        self.assertTrue(failed)
        decisions = {
            (value["target"], value["model"]): value
            for value in self.report["model_decisions"]
        }
        for key in failed:
            self.assertEqual(decisions[key]["decision"], "REJECT_FOR_PROMOTION")
            self.assertIn("COST_STRESS", decisions[key]["reasons"])
        self.assertEqual(self.findings["COST_STRESS"]["status"], "fail")

    def test_synthetic_fixture_is_never_promoted_as_empirical_evidence(self) -> None:
        self.assertEqual(self.findings["EMPIRICAL_STATUS"]["status"], "fail")
        self.assertEqual(
            self.report["disposition"],
            "REJECT_ALL_MODELS_FOR_PROMOTION_OR_PERFORMANCE_CLAIMS",
        )
        self.assertFalse(self.report["profitability_claimed"])
        self.assertTrue(
            all(
                decision["decision"] == "REJECT_FOR_PROMOTION"
                for decision in self.report["model_decisions"]
            )
        )


if __name__ == "__main__":
    unittest.main()
