from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from equity_research.modelling.contracts import ChronologicalSplitConfig, SplitName
from equity_research.modelling.preprocessing import fit_transformer
from equity_research.modelling.splitting import (
    WalkForwardConfig,
    expanding_walk_forward_folds,
    partition_rows,
    validate_outcome_embargo,
)
from equity_research.modelling.synthetic import SyntheticModelFixtureProvider


SPLITS = ChronologicalSplitConfig(
    train_end=datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc),
    calibration_start=datetime(2026, 4, 3, tzinfo=timezone.utc),
    calibration_end=datetime(2026, 5, 15, 23, 59, 59, tzinfo=timezone.utc),
    final_test_start=datetime(2026, 5, 20, tzinfo=timezone.utc),
    final_test_end=datetime(2026, 7, 15, 23, 59, 59, tzinfo=timezone.utc),
)


class ModellingSplittingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = SyntheticModelFixtureProvider().load()

    def test_partitions_are_chronological_and_keep_final_separate(self) -> None:
        partitions = partition_rows(self.dataset.rows, SPLITS)
        self.assertTrue(partitions[SplitName.TRAIN])
        self.assertTrue(partitions[SplitName.CALIBRATION])
        self.assertTrue(partitions[SplitName.FINAL_TEST])
        self.assertLess(
            max(row.prediction_as_of for row in partitions[SplitName.TRAIN]),
            min(row.prediction_as_of for row in partitions[SplitName.CALIBRATION]),
        )

    def test_transformer_is_fit_on_training_only(self) -> None:
        partitions = partition_rows(self.dataset.rows, SPLITS)
        train = partitions[SplitName.TRAIN]
        fitted = fit_transformer(train, self.dataset.feature_names)
        final = partitions[SplitName.FINAL_TEST][0]
        changed_features = tuple(
            (name, 1_000_000.0 if name == "gap_pct" else value)
            for name, value in final.features
        )
        changed = replace(final, features=changed_features)
        fitted_again = fit_transformer(train, self.dataset.feature_names)
        self.assertEqual(fitted.medians, fitted_again.medians)
        self.assertNotEqual(fitted.transform(final), fitted.transform(changed))
        self.assertLessEqual(fitted.fitted_through, SPLITS.train_end)

    def test_embargo_rejects_overlapping_outcome_windows(self) -> None:
        partitions = partition_rows(self.dataset.rows, SPLITS)
        train = list(partitions[SplitName.TRAIN])
        train[-1] = replace(
            train[-1], outcome_available_at=SPLITS.calibration_start
        )
        with self.assertRaises(ValueError):
            validate_outcome_embargo(
                tuple(train), partitions[SplitName.CALIBRATION], SPLITS
            )

    def test_walk_forward_is_expanding_and_purged(self) -> None:
        train = partition_rows(self.dataset.rows, SPLITS)[SplitName.TRAIN]
        folds = expanding_walk_forward_folds(
            train,
            WalkForwardConfig(
                minimum_train_rows=30,
                validation_rows=10,
                step_rows=10,
            ),
        )
        self.assertGreaterEqual(len(folds), 3)
        self.assertTrue(
            all(
                max(row.outcome_available_at for row in fold.train_rows)
                < min(row.prediction_as_of for row in fold.validation_rows)
                for fold in folds
            )
        )
        self.assertTrue(
            all(
                len(left.train_rows) < len(right.train_rows)
                for left, right in zip(folds, folds[1:])
            )
        )


if __name__ == "__main__":
    unittest.main()
