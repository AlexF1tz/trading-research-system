from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta

from equity_research.modelling.quality import run_modelling_quality_checks
from equity_research.modelling.synthetic import SyntheticModelFixtureProvider


class ModellingQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = SyntheticModelFixtureProvider().load()

    def test_fixture_has_only_explicit_limited_universe_warning(self) -> None:
        issues = run_modelling_quality_checks(self.dataset)
        self.assertEqual(
            {issue.code for issue in issues},
            {"LIMITED_UNIVERSE_NOT_SURVIVORSHIP_SAFE"},
        )

    def test_future_feature_is_rejected(self) -> None:
        row = replace(
            self.dataset.rows[0],
            features_available_at=self.dataset.rows[0].prediction_as_of
            + timedelta(seconds=1),
        )
        dataset = replace(self.dataset, rows=(row,) + self.dataset.rows[1:])
        codes = {issue.code for issue in run_modelling_quality_checks(dataset)}
        self.assertIn("FEATURE_AVAILABLE_AFTER_PREDICTION", codes)

    def test_outcome_cannot_be_available_at_prediction(self) -> None:
        row = replace(
            self.dataset.rows[0],
            outcome_available_at=self.dataset.rows[0].prediction_as_of,
        )
        dataset = replace(self.dataset, rows=(row,) + self.dataset.rows[1:])
        codes = {issue.code for issue in run_modelling_quality_checks(dataset)}
        self.assertIn("OUTCOME_AVAILABLE_AT_PREDICTION", codes)

    def test_excursion_and_touch_labels_must_agree(self) -> None:
        row = replace(self.dataset.rows[0], touch_up_20=True)
        dataset = replace(self.dataset, rows=(row,) + self.dataset.rows[1:])
        codes = {issue.code for issue in run_modelling_quality_checks(dataset)}
        self.assertTrue(
            {"NONMONOTONIC_UPSIDE_LABELS", "MFE_TOUCH_INCONSISTENCY"} & codes
        )

    def test_net_return_must_include_declared_costs(self) -> None:
        filled = next(
            row for row in self.dataset.rows if row.net_return_after_cost_pct is not None
        )
        replacement = replace(
            filled,
            net_return_after_cost_pct=filled.net_return_after_cost_pct + 1.0,
        )
        rows = tuple(
            replacement if row.observation_id == filled.observation_id else row
            for row in self.dataset.rows
        )
        codes = {
            issue.code
            for issue in run_modelling_quality_checks(replace(self.dataset, rows=rows))
        }
        self.assertIn("NET_RETURN_COST_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
