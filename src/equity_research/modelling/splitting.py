"""Chronological splits and purged expanding walk-forward folds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .contracts import ChronologicalSplitConfig, ModelRow, SplitName


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    minimum_train_rows: int = 30
    validation_rows: int = 10
    step_rows: int = 10

    def validate(self) -> None:
        if self.minimum_train_rows < 2:
            raise ValueError("minimum walk-forward training rows must be at least two")
        if self.validation_rows < 1 or self.step_rows < 1:
            raise ValueError("walk-forward validation and step rows must be positive")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_id: str
    train_rows: tuple[ModelRow, ...]
    validation_rows: tuple[ModelRow, ...]


def validate_split_config(config: ChronologicalSplitConfig) -> None:
    values = (
        config.train_end,
        config.calibration_start,
        config.calibration_end,
        config.final_test_start,
        config.final_test_end,
    )
    if any(value.tzinfo is None or value.utcoffset() != timedelta(0) for value in values):
        raise ValueError("all chronological split cutoffs must be UTC")
    if not (
        config.train_end
        < config.calibration_start
        <= config.calibration_end
        < config.final_test_start
        <= config.final_test_end
    ):
        raise ValueError("chronological split cutoffs are not strictly ordered")


def partition_rows(
    rows: tuple[ModelRow, ...], config: ChronologicalSplitConfig
) -> dict[SplitName, tuple[ModelRow, ...]]:
    validate_split_config(config)
    grouped: dict[SplitName, list[ModelRow]] = {value: [] for value in SplitName}
    for row in sorted(rows, key=lambda value: (value.prediction_as_of, value.observation_id)):
        grouped[config.split_for(row.prediction_as_of)].append(row)
    return {name: tuple(values) for name, values in grouped.items()}


def validate_outcome_embargo(
    train_rows: tuple[ModelRow, ...],
    calibration_rows: tuple[ModelRow, ...],
    config: ChronologicalSplitConfig,
) -> None:
    leaking_train = tuple(
        row for row in train_rows if row.outcome_available_at >= config.calibration_start
    )
    if leaking_train:
        raise ValueError(
            "training outcomes overlap calibration start; increase the chronological embargo"
        )
    leaking_calibration = tuple(
        row
        for row in calibration_rows
        if row.outcome_available_at >= config.final_test_start
    )
    if leaking_calibration:
        raise ValueError(
            "calibration outcomes overlap final-test start; increase the chronological embargo"
        )


def expanding_walk_forward_folds(
    rows: tuple[ModelRow, ...], config: WalkForwardConfig
) -> tuple[WalkForwardFold, ...]:
    config.validate()
    ordered = tuple(
        sorted(rows, key=lambda value: (value.prediction_as_of, value.observation_id))
    )
    folds: list[WalkForwardFold] = []
    validation_start = config.minimum_train_rows
    while validation_start + config.validation_rows <= len(ordered):
        validation = ordered[
            validation_start : validation_start + config.validation_rows
        ]
        validation_time = validation[0].prediction_as_of
        train = tuple(
            row
            for row in ordered[:validation_start]
            if row.outcome_available_at < validation_time
        )
        if len(train) >= 2:
            folds.append(
                WalkForwardFold(
                    fold_id=f"wf-{len(folds) + 1:02d}",
                    train_rows=train,
                    validation_rows=validation,
                )
            )
        validation_start += config.step_rows
    if not folds:
        raise ValueError("insufficient chronological rows for one walk-forward fold")
    return tuple(folds)
