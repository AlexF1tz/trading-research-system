from __future__ import annotations

import unittest

from equity_research.modelling.contracts import BinaryTarget, RegressionTarget
from equity_research.modelling.evaluation import (
    classification_metrics,
    feature_stability_report,
)
from equity_research.modelling.models import (
    BoostedStumpsBinary,
    HistoricalFrequencyBinary,
    LogisticBinary,
    RidgeRegression,
)
from equity_research.modelling.preprocessing import fit_transformer
from equity_research.modelling.synthetic import SyntheticModelFixtureProvider


class ModellingModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = SyntheticModelFixtureProvider().load()
        self.rows = self.dataset.rows[:50]
        self.transformer = fit_transformer(self.rows, self.dataset.feature_names)
        self.matrix = self.transformer.transform_many(self.rows)

    def test_historical_frequency_is_laplace_smoothed(self) -> None:
        target = BinaryTarget.TARGET_BEFORE_STOP
        labels = tuple(float(row.target_before_stop) for row in self.rows)
        model = HistoricalFrequencyBinary.fit(
            self.rows,
            self.matrix,
            labels,
            self.transformer.output_names,
            target,
        )
        expected = (sum(labels) + 1.0) / (len(labels) + 2.0)
        self.assertAlmostEqual(model.probability, expected)

    def test_logistic_and_boosting_emit_probabilities(self) -> None:
        target = BinaryTarget.TOUCH_UP_05
        labels = tuple(float(row.touch_up_05) for row in self.rows)
        for model_class in (LogisticBinary, BoostedStumpsBinary):
            model = model_class.fit(
                self.rows,
                self.matrix,
                labels,
                self.transformer.output_names,
                target,
            )
            predictions = tuple(
                model.predict(row, vector)
                for row, vector in zip(self.rows, self.matrix)
            )
            self.assertTrue(all(0.0 <= value <= 1.0 for value in predictions))
            self.assertGreater(len(set(round(value, 6) for value in predictions)), 1)

    def test_regression_respects_excursion_sign(self) -> None:
        for target in (RegressionTarget.MFE, RegressionTarget.MAE):
            labels = tuple(row.regression_label(target) for row in self.rows)
            model = RidgeRegression.fit(
                self.rows,
                self.matrix,
                labels,
                self.transformer.output_names,
                target,
            )
            values = tuple(
                model.predict(row, vector)
                for row, vector in zip(self.rows, self.matrix)
            )
            if target is RegressionTarget.MFE:
                self.assertTrue(all(value >= 0 for value in values))
            else:
                self.assertTrue(all(value <= 0 for value in values))

    def test_metrics_show_base_rate_not_accuracy(self) -> None:
        rows = self.rows[:20]
        labels = tuple(float(row.touch_up_05) for row in rows)
        predictions = tuple(0.4 + index / 100 for index in range(len(rows)))
        metrics = classification_metrics(
            rows,
            predictions,
            labels,
            bootstrap_repetitions=10,
        )
        self.assertIn("base_rate", metrics)
        self.assertNotIn("accuracy", metrics)
        self.assertEqual(set(metrics["top_k"]), {"1", "3", "5", "10"})
        self.assertEqual(len(metrics["calibration_curve"]), 10)

    def test_feature_stability_detects_sign_flip(self) -> None:
        report = feature_stability_report(
            {"logistic": [{"unstable": 1.0}, {"unstable": -0.5}, {"unstable": 0.4}]}
        )
        unstable = report["logistic"]["unstable_features"]
        self.assertEqual(unstable[0]["feature"], "unstable")
        self.assertTrue(unstable[0]["sign_flip"])


if __name__ == "__main__":
    unittest.main()
