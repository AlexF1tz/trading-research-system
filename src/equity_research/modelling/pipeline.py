"""Walk-forward model comparison with isolated calibration and final test periods."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean

from .contracts import (
    BINARY_TARGETS,
    REGRESSION_TARGETS,
    BinaryTarget,
    ChronologicalSplitConfig,
    ModelDataset,
    ModelRow,
    PredictionValue,
    RegressionTarget,
    SplitName,
)
from .evaluation import (
    brier_score,
    classification_metrics,
    feature_stability_report,
    log_loss,
    performance_breakdowns,
    regression_metrics,
)
from .models import (
    BINARY_MODEL_CLASSES,
    REGRESSION_MODEL_CLASSES,
    HistoricalFrequencyBinary,
    PlattCalibrator,
)
from .preprocessing import fit_transformer
from .provider import ModelDatasetProvider
from .quality import (
    ModellingQualityError,
    ModellingQualityIssue,
    Severity,
    run_modelling_quality_checks,
)
from .splitting import (
    WalkForwardConfig,
    expanding_walk_forward_folds,
    partition_rows,
    validate_outcome_embargo,
)


@dataclass(frozen=True, slots=True)
class ModellingPipelineConfig:
    splits: ChronologicalSplitConfig
    walk_forward: WalkForwardConfig = field(default_factory=WalkForwardConfig)
    calibration_bins: int = 10
    bootstrap_repetitions: int = 200
    bootstrap_seed: int = 17
    classification_simplicity_tolerance: float = 0.005
    regression_simplicity_tolerance: float = 0.05
    overfit_brier_gap: float = 0.03
    overfit_mae_gap: float = 0.50
    allow_limited_universe: bool = False
    fail_on_quality_error: bool = True


@dataclass(frozen=True, slots=True)
class ModellingPipelineResult:
    dataset: ModelDataset
    report: dict[str, object]
    predictions: tuple[PredictionValue, ...]
    quality_issues: tuple[ModellingQualityIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.severity is Severity.ERROR for issue in self.quality_issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity is Severity.WARNING for issue in self.quality_issues)


def _binary_rows(
    rows: tuple[ModelRow, ...], target: BinaryTarget
) -> tuple[ModelRow, ...]:
    return tuple(row for row in rows if row.binary_label(target) is not None)


def _binary_labels(
    rows: tuple[ModelRow, ...], target: BinaryTarget
) -> tuple[float, ...]:
    return tuple(float(bool(row.binary_label(target))) for row in rows)


def _regression_labels(
    rows: tuple[ModelRow, ...], target: RegressionTarget
) -> tuple[float, ...]:
    return tuple(row.regression_label(target) for row in rows)


def _base_rate(rows: tuple[ModelRow, ...], target: BinaryTarget) -> float | None:
    eligible = _binary_rows(rows, target)
    return fmean(_binary_labels(eligible, target)) if eligible else None


def _select_simplest(
    scores: list[tuple[str, float]], tolerance: float
) -> str:
    best = min(score for _, score in scores)
    return next(name for name, score in scores if score <= best + tolerance)


class ModellingPipeline:
    def __init__(
        self,
        provider: ModelDatasetProvider,
        config: ModellingPipelineConfig,
    ) -> None:
        self._provider = provider
        self._config = config

    def run(self) -> ModellingPipelineResult:
        if self._config.calibration_bins < 2:
            raise ValueError("calibration_bins must be at least two")
        if self._config.bootstrap_repetitions < 1:
            raise ValueError("bootstrap_repetitions must be positive")
        if (
            self._config.classification_simplicity_tolerance < 0
            or self._config.regression_simplicity_tolerance < 0
            or self._config.overfit_brier_gap < 0
            or self._config.overfit_mae_gap < 0
        ):
            raise ValueError("selection tolerances and overfit gaps cannot be negative")
        dataset = self._provider.load()
        issues = run_modelling_quality_checks(dataset)
        errors = tuple(issue for issue in issues if issue.severity is Severity.ERROR)
        if errors and self._config.fail_on_quality_error:
            raise ModellingQualityError(errors)
        if not dataset.universe_survivorship_safe and not self._config.allow_limited_universe:
            raise ValueError(
                "limited universe requires explicit allow_limited_universe and cannot support survivorship-safe claims"
            )

        partitions = partition_rows(dataset.rows, self._config.splits)
        train_rows = partitions[SplitName.TRAIN]
        calibration_rows = partitions[SplitName.CALIBRATION]
        final_rows = partitions[SplitName.FINAL_TEST]
        if not train_rows or not calibration_rows or not final_rows:
            raise ValueError("train, calibration, and final-test periods must all contain rows")
        validate_outcome_embargo(train_rows, calibration_rows, self._config.splits)
        folds = expanding_walk_forward_folds(train_rows, self._config.walk_forward)
        final_transformer = fit_transformer(train_rows, dataset.feature_names)

        classification_report: dict[str, object] = {}
        regression_report: dict[str, object] = {}
        predictions: list[PredictionValue] = []
        stability_snapshots: dict[str, list[dict[str, float]]] = {}
        overfitting_evidence: list[dict[str, object]] = []
        final_probability_lookup: dict[
            str, dict[BinaryTarget, dict[str, float]]
        ] = {}
        selected_models: dict[BinaryTarget, str] = {}
        selected_final_probabilities: dict[BinaryTarget, tuple[float, ...]] = {}

        for target in BINARY_TARGETS:
            cv_by_model: dict[str, list[dict[str, object]]] = {
                model_class.name: [] for model_class in BINARY_MODEL_CLASSES
            }
            for fold in folds:
                fold_transformer = fit_transformer(
                    fold.train_rows, dataset.feature_names
                )
                fold_train = _binary_rows(fold.train_rows, target)
                fold_validation = _binary_rows(fold.validation_rows, target)
                if len(fold_train) < 2 or not fold_validation:
                    continue
                train_matrix = fold_transformer.transform_many(fold_train)
                validation_matrix = fold_transformer.transform_many(fold_validation)
                train_labels = _binary_labels(fold_train, target)
                validation_labels = _binary_labels(fold_validation, target)
                for model_class in BINARY_MODEL_CLASSES:
                    model = model_class.fit(
                        fold_train,
                        train_matrix,
                        train_labels,
                        fold_transformer.output_names,
                        target,
                    )
                    fold_predictions = tuple(
                        model.predict(row, vector)
                        for row, vector in zip(fold_validation, validation_matrix)
                    )
                    cv_by_model[model.name].append(
                        {
                            "fold_id": fold.fold_id,
                            "train_n": len(fold_train),
                            "validation_n": len(fold_validation),
                            "validation_base_rate": round(
                                fmean(validation_labels), 6
                            ),
                            "brier_score": round(
                                brier_score(fold_predictions, validation_labels), 6
                            ),
                            "log_loss": round(
                                log_loss(fold_predictions, validation_labels), 6
                            ),
                        }
                    )
                    stability_snapshots.setdefault(
                        f"{target.value}:{model.name}", []
                    ).append(model.feature_effects)

            scores: list[tuple[str, float]] = []
            cv_summary: dict[str, object] = {}
            for model_class in BINARY_MODEL_CLASSES:
                values = cv_by_model[model_class.name]
                if not values:
                    raise ValueError(
                        f"no walk-forward results for {target.value}/{model_class.name}"
                    )
                mean_brier = fmean(float(value["brier_score"]) for value in values)
                mean_loss = fmean(float(value["log_loss"]) for value in values)
                scores.append((model_class.name, mean_brier))
                cv_summary[model_class.name] = {
                    "folds": values,
                    "mean_brier_score": round(mean_brier, 6),
                    "mean_log_loss": round(mean_loss, 6),
                }
            selected_name = _select_simplest(
                scores, self._config.classification_simplicity_tolerance
            )
            selected_models[target] = selected_name

            target_train = _binary_rows(train_rows, target)
            target_calibration = _binary_rows(calibration_rows, target)
            target_final = _binary_rows(final_rows, target)
            train_matrix = final_transformer.transform_many(target_train)
            calibration_matrix = final_transformer.transform_many(target_calibration)
            final_matrix = final_transformer.transform_many(target_final)
            train_labels = _binary_labels(target_train, target)
            calibration_labels = _binary_labels(target_calibration, target)
            final_labels = _binary_labels(target_final, target)
            model_reports: dict[str, object] = {}
            for model_class in BINARY_MODEL_CLASSES:
                model = model_class.fit(
                    target_train,
                    train_matrix,
                    train_labels,
                    final_transformer.output_names,
                    target,
                )
                raw_train = tuple(
                    model.predict(row, vector)
                    for row, vector in zip(target_train, train_matrix)
                )
                raw_calibration = tuple(
                    model.predict(row, vector)
                    for row, vector in zip(target_calibration, calibration_matrix)
                )
                calibrator = (
                    PlattCalibrator.fit(
                        raw_calibration, calibration_labels, target_calibration
                    )
                    if model_class is not HistoricalFrequencyBinary
                    else PlattCalibrator(
                        0.0,
                        1.0,
                        max(row.prediction_as_of for row in target_calibration),
                        True,
                    )
                )
                raw_final = tuple(
                    model.predict(row, vector)
                    for row, vector in zip(target_final, final_matrix)
                )
                final_probabilities = tuple(
                    calibrator.predict(value) for value in raw_final
                )
                metrics = classification_metrics(
                    target_final,
                    final_probabilities,
                    final_labels,
                    calibration_bins=self._config.calibration_bins,
                    bootstrap_repetitions=self._config.bootstrap_repetitions,
                    bootstrap_seed=self._config.bootstrap_seed,
                )
                train_brier = brier_score(raw_train, train_labels)
                cv_brier = float(
                    cv_summary[model.name]["mean_brier_score"]  # type: ignore[index]
                )
                final_brier = float(metrics["brier_score"])
                resubstitution_gap = cv_brier - train_brier
                final_degradation = final_brier - cv_brier
                flags: list[str] = []
                if resubstitution_gap > self._config.overfit_brier_gap:
                    flags.append("TRAIN_TO_WALK_FORWARD_OVERFIT_GAP")
                if final_degradation > self._config.overfit_brier_gap:
                    flags.append("FINAL_TEST_DEGRADATION_VERSUS_WALK_FORWARD")
                if flags:
                    overfitting_evidence.append(
                        {
                            "target": target.value,
                            "model": model.name,
                            "flags": flags,
                            "train_resubstitution_brier": round(train_brier, 6),
                            "walk_forward_brier": round(cv_brier, 6),
                            "final_test_brier": round(final_brier, 6),
                        }
                    )
                model_reports[model.name] = {
                    "walk_forward": cv_summary[model.name],
                    "calibration": {
                        "method": (
                            "none_for_historical_frequency"
                            if model_class is HistoricalFrequencyBinary
                            else "platt_logistic"
                        ),
                        "fitted_on": "calibration_only",
                        "fitted_through": calibrator.fitted_through.isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "identity_fallback": calibrator.identity,
                    },
                    "train_resubstitution_brier": round(train_brier, 6),
                    "final_test": metrics,
                    "overfitting_flags": flags,
                }
                final_probability_lookup.setdefault(model.name, {})[target] = {
                    row.observation_id: probability
                    for row, probability in zip(target_final, final_probabilities)
                }
                if model.name == selected_name:
                    selected_final_probabilities[target] = final_probabilities
                predictions.extend(
                    PredictionValue(
                        observation_id=row.observation_id,
                        event_id=row.event_id,
                        security_id=row.security_id,
                        prediction_as_of=row.prediction_as_of,
                        target=target.value,
                        model_name=model.name,
                        value=probability,
                        calibrated=not calibrator.identity,
                        trained_through=model.trained_through,
                    )
                    for row, probability in zip(target_final, final_probabilities)
                )
                if target is BinaryTarget.CONTINUATION:
                    predictions.extend(
                        PredictionValue(
                            observation_id=row.observation_id,
                            event_id=row.event_id,
                            security_id=row.security_id,
                            prediction_as_of=row.prediction_as_of,
                            target="reversal",
                            model_name=model.name,
                            value=1.0 - probability,
                            calibrated=not calibrator.identity,
                            trained_through=model.trained_through,
                        )
                        for row, probability in zip(
                            target_final, final_probabilities
                        )
                    )
            classification_report[target.value] = {
                "base_rates": {
                    "train": round(_base_rate(train_rows, target) or 0.0, 6),
                    "calibration": round(
                        _base_rate(calibration_rows, target) or 0.0, 6
                    ),
                    "final_test": round(_base_rate(final_rows, target) or 0.0, 6),
                },
                "selected_model_from_walk_forward_only": selected_name,
                "selection_tolerance_brier": self._config.classification_simplicity_tolerance,
                "models": model_reports,
            }
            if target is BinaryTarget.CONTINUATION:
                classification_report[target.value]["complementary_reversal"] = {
                    "definition": "1_minus_p_continuation_on_nonambiguous_rows",
                    "base_rates": {
                        name: round(1.0 - float(value), 6)
                        for name, value in classification_report[target.value][
                            "base_rates"
                        ].items()
                    },
                    "separate_model_fitted": False,
                }

        for target in REGRESSION_TARGETS:
            cv_by_model: dict[str, list[dict[str, object]]] = {
                model_class.name: [] for model_class in REGRESSION_MODEL_CLASSES
            }
            for fold in folds:
                fold_transformer = fit_transformer(
                    fold.train_rows, dataset.feature_names
                )
                train_matrix = fold_transformer.transform_many(fold.train_rows)
                validation_matrix = fold_transformer.transform_many(
                    fold.validation_rows
                )
                train_labels = _regression_labels(fold.train_rows, target)
                validation_labels = _regression_labels(fold.validation_rows, target)
                for model_class in REGRESSION_MODEL_CLASSES:
                    model = model_class.fit(
                        fold.train_rows,
                        train_matrix,
                        train_labels,
                        fold_transformer.output_names,
                        target,
                    )
                    fold_predictions = tuple(
                        model.predict(row, vector)
                        for row, vector in zip(
                            fold.validation_rows, validation_matrix
                        )
                    )
                    mae = fmean(
                        abs(prediction - label)
                        for prediction, label in zip(
                            fold_predictions, validation_labels
                        )
                    )
                    rmse = (
                        fmean(
                            (prediction - label) ** 2
                            for prediction, label in zip(
                                fold_predictions, validation_labels
                            )
                        )
                        ** 0.5
                    )
                    cv_by_model[model.name].append(
                        {
                            "fold_id": fold.fold_id,
                            "train_n": len(fold.train_rows),
                            "validation_n": len(fold.validation_rows),
                            "mean_absolute_error": round(mae, 6),
                            "root_mean_squared_error": round(rmse, 6),
                        }
                    )
                    stability_snapshots.setdefault(
                        f"{target.value}:{model.name}", []
                    ).append(model.feature_effects)
            scores: list[tuple[str, float]] = []
            cv_summary: dict[str, object] = {}
            for model_class in REGRESSION_MODEL_CLASSES:
                values = cv_by_model[model_class.name]
                mean_mae = fmean(
                    float(value["mean_absolute_error"]) for value in values
                )
                scores.append((model_class.name, mean_mae))
                cv_summary[model_class.name] = {
                    "folds": values,
                    "mean_absolute_error": round(mean_mae, 6),
                    "mean_root_mean_squared_error": round(
                        fmean(
                            float(value["root_mean_squared_error"])
                            for value in values
                        ),
                        6,
                    ),
                }
            selected_name = _select_simplest(
                scores, self._config.regression_simplicity_tolerance
            )
            train_matrix = final_transformer.transform_many(train_rows)
            final_matrix = final_transformer.transform_many(final_rows)
            train_labels = _regression_labels(train_rows, target)
            final_labels = _regression_labels(final_rows, target)
            model_reports: dict[str, object] = {}
            for model_class in REGRESSION_MODEL_CLASSES:
                model = model_class.fit(
                    train_rows,
                    train_matrix,
                    train_labels,
                    final_transformer.output_names,
                    target,
                )
                final_values = tuple(
                    model.predict(row, vector)
                    for row, vector in zip(final_rows, final_matrix)
                )
                metrics = regression_metrics(
                    final_rows,
                    final_values,
                    final_labels,
                    bootstrap_repetitions=self._config.bootstrap_repetitions,
                    bootstrap_seed=self._config.bootstrap_seed + 11,
                )
                train_values = tuple(
                    model.predict(row, vector)
                    for row, vector in zip(train_rows, train_matrix)
                )
                train_mae = fmean(
                    abs(prediction - label)
                    for prediction, label in zip(train_values, train_labels)
                )
                cv_mae = float(
                    cv_summary[model.name]["mean_absolute_error"]  # type: ignore[index]
                )
                final_mae = float(metrics["mean_absolute_error"])
                flags: list[str] = []
                if cv_mae - train_mae > self._config.overfit_mae_gap:
                    flags.append("TRAIN_TO_WALK_FORWARD_MAE_GAP")
                if final_mae - cv_mae > self._config.overfit_mae_gap:
                    flags.append("FINAL_TEST_MAE_DEGRADATION")
                if flags:
                    overfitting_evidence.append(
                        {
                            "target": target.value,
                            "model": model.name,
                            "flags": flags,
                            "train_resubstitution_mae": round(train_mae, 6),
                            "walk_forward_mae": round(cv_mae, 6),
                            "final_test_mae": round(final_mae, 6),
                        }
                    )
                model_reports[model.name] = {
                    "walk_forward": cv_summary[model.name],
                    "final_test": metrics,
                    "train_resubstitution_mean_absolute_error": round(
                        train_mae, 6
                    ),
                    "overfitting_flags": flags,
                }
                predictions.extend(
                    PredictionValue(
                        observation_id=row.observation_id,
                        event_id=row.event_id,
                        security_id=row.security_id,
                        prediction_as_of=row.prediction_as_of,
                        target=target.value,
                        model_name=model.name,
                        value=value,
                        calibrated=False,
                        trained_through=model.trained_through,
                    )
                    for row, value in zip(final_rows, final_values)
                )
            regression_report[target.value] = {
                "selected_model_from_walk_forward_only": selected_name,
                "selection_tolerance_mae": self._config.regression_simplicity_tolerance,
                "models": model_reports,
            }

        target = BinaryTarget.TARGET_BEFORE_STOP
        tbs_final = _binary_rows(final_rows, target)
        tbs_labels = _binary_labels(tbs_final, target)
        breakdowns = performance_breakdowns(
            tbs_final,
            selected_final_probabilities[target],
            tbs_labels,
        )
        monotonicity = self._monotonicity_report(
            final_probability_lookup, final_rows, selected_models
        )
        report: dict[str, object] = {
            "status": (
                "ENGINEERING_FIXTURE_NOT_EMPIRICAL_EVIDENCE"
                if "synthetic" in dataset.dataset_kind.lower()
                else "CHRONOLOGICAL_OUT_OF_SAMPLE_EVALUATION"
            ),
            "decision_support_only": True,
            "dataset": {
                "provider": dataset.provider,
                "dataset_kind": dataset.dataset_kind,
                "rows": len(dataset.rows),
                "feature_names": list(dataset.feature_names),
                "target_barrier_pct": dataset.target_barrier_pct,
                "stop_barrier_pct": dataset.stop_barrier_pct,
                "universe_survivorship_safe": dataset.universe_survivorship_safe,
                "notes": list(dataset.notes),
            },
            "chronological_splits": {
                "train_n": len(train_rows),
                "calibration_n": len(calibration_rows),
                "final_test_n": len(final_rows),
                "embargo_n": len(partitions[SplitName.EMBARGO]),
                "out_of_range_n": len(partitions[SplitName.OUT_OF_RANGE]),
                "train_end": self._config.splits.train_end.isoformat().replace(
                    "+00:00", "Z"
                ),
                "calibration_start": self._config.splits.calibration_start.isoformat().replace(
                    "+00:00", "Z"
                ),
                "calibration_end": self._config.splits.calibration_end.isoformat().replace(
                    "+00:00", "Z"
                ),
                "final_test_start": self._config.splits.final_test_start.isoformat().replace(
                    "+00:00", "Z"
                ),
                "final_test_end": self._config.splits.final_test_end.isoformat().replace(
                    "+00:00", "Z"
                ),
                "final_test_used_for_model_selection": False,
            },
            "preprocessing": {
                "fit_on": "training_only",
                "fitted_through": final_transformer.fitted_through.isoformat().replace(
                    "+00:00", "Z"
                ),
                "median_imputation_with_missing_indicators": True,
            },
            "classification": classification_report,
            "regression": regression_report,
            "target_before_stop_breakdowns_selected_model": {
                "model": selected_models[target],
                "dimensions": breakdowns,
            },
            "feature_stability": feature_stability_report(stability_snapshots),
            "overfitting_evidence": overfitting_evidence,
            "cross_target_probability_monotonicity": monotonicity,
            "claims": {
                "accuracy_reported": False,
                "profitability_claimed": False,
                "synthetic_results_must_not_be_used_as_performance_evidence": (
                    "synthetic" in dataset.dataset_kind.lower()
                ),
            },
        }
        return ModellingPipelineResult(
            dataset=dataset,
            report=report,
            predictions=tuple(predictions),
            quality_issues=issues,
        )

    @staticmethod
    def _monotonicity_report(
        lookup: dict[str, dict[BinaryTarget, dict[str, float]]],
        final_rows: tuple[ModelRow, ...],
        selected_models: dict[BinaryTarget, str],
    ) -> dict[str, object]:
        by_model: dict[str, object] = {}
        for model_name, targets in lookup.items():
            violations = 0
            for row in final_rows:
                up05 = targets[BinaryTarget.TOUCH_UP_05][row.observation_id]
                up10 = targets[BinaryTarget.TOUCH_UP_10][row.observation_id]
                up20 = targets[BinaryTarget.TOUCH_UP_20][row.observation_id]
                down05 = targets[BinaryTarget.TOUCH_DOWN_05][row.observation_id]
                down10 = targets[BinaryTarget.TOUCH_DOWN_10][row.observation_id]
                violations += not (up20 <= up10 <= up05 and down10 <= down05)
            by_model[model_name] = {
                "rows": len(final_rows),
                "violation_count": int(violations),
                "requires_repair_or_rejection_before_shadow_use": violations > 0,
            }
        selected_violations = 0
        for row in final_rows:
            probability = lambda target: lookup[selected_models[target]][target][
                row.observation_id
            ]
            selected_violations += not (
                probability(BinaryTarget.TOUCH_UP_20)
                <= probability(BinaryTarget.TOUCH_UP_10)
                <= probability(BinaryTarget.TOUCH_UP_05)
                and probability(BinaryTarget.TOUCH_DOWN_10)
                <= probability(BinaryTarget.TOUCH_DOWN_05)
            )
        return {
            "by_same_model_family": by_model,
            "selected_per_target_violation_count": int(selected_violations),
            "selected_predictions_require_repair_or_rejection_before_shadow_use": (
                selected_violations > 0
            ),
        }
