from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from equity_research.modelling.provider import JsonlModelDatasetProvider
from equity_research.modelling.sample import run_sample
from equity_research.modelling.synthetic import SyntheticModelFixtureProvider


class ModellingPipelineTests(unittest.TestCase):
    def test_jsonl_provider_round_trips_normalized_rows(self) -> None:
        dataset = SyntheticModelFixtureProvider().load()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = {
                "provider": "normalized-test-export",
                "dataset_kind": "fixture_contract_test",
                "fetched_at": dataset.fetched_at.isoformat(),
                "feature_names": list(dataset.feature_names),
                "target_barrier_pct": dataset.target_barrier_pct,
                "stop_barrier_pct": dataset.stop_barrier_pct,
                "universe_survivorship_safe": False,
                "notes": ["offline test"],
            }
            (root / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            (root / "rows.jsonl").write_text(
                json.dumps(dataset.rows[0].to_dict()) + "\n", encoding="utf-8"
            )
            loaded = JsonlModelDatasetProvider(root).load()
            self.assertEqual(loaded.feature_names, dataset.feature_names)
            self.assertEqual(loaded.rows[0], dataset.rows[0])

    def test_sample_runs_all_targets_without_final_test_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = run_sample(
                Path(directory), Path("config/modelling.sample.json")
            )
            self.assertEqual(
                summary["status"], "ENGINEERING_FIXTURE_NOT_EMPIRICAL_EVIDENCE"
            )
            self.assertEqual(summary["classification_targets"], 7)
            self.assertEqual(summary["regression_targets"], 2)
            self.assertFalse(summary["final_test_used_for_model_selection"])
            self.assertFalse(summary["profitability_claimed"])
            report = json.loads(
                (Path(directory) / "modelling_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(
                report["chronological_splits"]["final_test_used_for_model_selection"]
            )
            self.assertFalse(report["claims"]["accuracy_reported"])
            for target in report["classification"].values():
                self.assertIn("final_test", target["base_rates"])
            evaluation_rows = [
                json.loads(value)
                for value in (
                    Path(directory) / "final_test_evaluation_predictions.jsonl"
                )
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            continuation = next(
                value for value in evaluation_rows if value["target"] == "continuation"
            )
            reversal = next(
                value
                for value in evaluation_rows
                if value["target"] == "reversal"
                and value["observation_id"] == continuation["observation_id"]
                and value["model_name"] == continuation["model_name"]
            )
            self.assertAlmostEqual(
                continuation["value"] + reversal["value"], 1.0
            )
            dimensions = report["target_before_stop_breakdowns_selected_model"][
                "dimensions"
            ]
            self.assertEqual(
                set(dimensions),
                {
                    "catalyst_category",
                    "float_category",
                    "market_cap_category",
                    "market_regime",
                    "time_of_day",
                    "gap_size",
                    "relative_volume",
                    "retail_attention_stage",
                },
            )


if __name__ == "__main__":
    unittest.main()
