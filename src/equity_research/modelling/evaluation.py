"""Calibration, ranking, cost, bootstrap, breakdown, and stability evaluation."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import fmean
from .contracts import BREAKDOWN_DIMENSIONS, ModelRow


TOP_K_VALUES = (1, 3, 5, 10)


def _clip_probability(value: float) -> float:
    return min(1.0 - 1e-12, max(1e-12, value))


def brier_score(predictions: tuple[float, ...], labels: tuple[float, ...]) -> float:
    return fmean((prediction - label) ** 2 for prediction, label in zip(predictions, labels))


def log_loss(predictions: tuple[float, ...], labels: tuple[float, ...]) -> float:
    return -fmean(
        label * math.log(_clip_probability(prediction))
        + (1.0 - label) * math.log(_clip_probability(1.0 - prediction))
        for prediction, label in zip(predictions, labels)
    )


def _calibration_curve(
    predictions: tuple[float, ...], labels: tuple[float, ...], bins: int
) -> tuple[list[dict[str, object]], float]:
    output: list[dict[str, object]] = []
    expected_error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = tuple(
            position
            for position, prediction in enumerate(predictions)
            if lower <= prediction < upper or (index == bins - 1 and prediction == 1.0)
        )
        if not members:
            output.append(
                {
                    "lower": lower,
                    "upper": upper,
                    "count": 0,
                    "mean_prediction": None,
                    "event_rate": None,
                }
            )
            continue
        mean_prediction = fmean(predictions[position] for position in members)
        event_rate = fmean(labels[position] for position in members)
        expected_error += len(members) / len(labels) * abs(mean_prediction - event_rate)
        output.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(members),
                "mean_prediction": round(mean_prediction, 6),
                "event_rate": round(event_rate, 6),
            }
        )
    return output, expected_error


def _ranked_indices(
    rows: tuple[ModelRow, ...], predictions: tuple[float, ...]
) -> tuple[int, ...]:
    return tuple(
        sorted(
            range(len(rows)),
            key=lambda index: (
                -predictions[index],
                rows[index].prediction_as_of,
                rows[index].observation_id,
            ),
        )
    )


def _top_k_metrics(
    rows: tuple[ModelRow, ...],
    predictions: tuple[float, ...],
    labels: tuple[float, ...],
) -> dict[str, dict[str, object]]:
    ranked = _ranked_indices(rows, predictions)
    output: dict[str, dict[str, object]] = {}
    for requested in TOP_K_VALUES:
        count = min(requested, len(ranked))
        selected = ranked[:count]
        filled = sum(rows[index].net_return_after_cost_pct is not None for index in selected)
        output[str(requested)] = {
            "requested_k": requested,
            "actual_n": count,
            "precision": round(fmean(labels[index] for index in selected), 6),
            "base_rate": round(fmean(labels), 6),
            "target_before_stop_rate": round(
                fmean(float(rows[index].target_before_stop) for index in selected), 6
            ),
            "expectancy_after_spread_slippage_pct": round(
                fmean(rows[index].policy_return_pct for index in selected), 6
            ),
            "fill_rate": round(filled / count, 6),
        }
    return output


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _interval(values: list[float], point: float) -> dict[str, float]:
    return {
        "point": round(point, 6),
        "lower_95": round(_percentile(values, 0.025), 6),
        "upper_95": round(_percentile(values, 0.975), 6),
    }


def _cluster_bootstrap_indices(
    rows: tuple[ModelRow, ...], repetitions: int, seed: int
) -> tuple[tuple[int, ...], ...]:
    by_event: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_event[row.event_id].append(index)
    event_ids = tuple(sorted(by_event))
    generator = random.Random(seed)
    samples: list[tuple[int, ...]] = []
    for _ in range(repetitions):
        selected_events = tuple(generator.choice(event_ids) for _ in event_ids)
        samples.append(
            tuple(index for event_id in selected_events for index in by_event[event_id])
        )
    return tuple(samples)


def classification_metrics(
    rows: tuple[ModelRow, ...],
    predictions: tuple[float, ...],
    labels: tuple[float, ...],
    *,
    calibration_bins: int = 10,
    bootstrap_repetitions: int = 200,
    bootstrap_seed: int = 17,
) -> dict[str, object]:
    if not rows or not (len(rows) == len(predictions) == len(labels)):
        raise ValueError("classification metrics require aligned non-empty values")
    base_rate = fmean(labels)
    brier = brier_score(predictions, labels)
    loss = log_loss(predictions, labels)
    curve, calibration_error = _calibration_curve(
        predictions, labels, calibration_bins
    )
    samples = _cluster_bootstrap_indices(
        rows, bootstrap_repetitions, bootstrap_seed
    )
    brier_values: list[float] = []
    loss_values: list[float] = []
    base_values: list[float] = []
    expectancy_values: list[float] = []
    for sample in samples:
        sample_rows = tuple(rows[index] for index in sample)
        sample_predictions = tuple(predictions[index] for index in sample)
        sample_labels = tuple(labels[index] for index in sample)
        brier_values.append(brier_score(sample_predictions, sample_labels))
        loss_values.append(log_loss(sample_predictions, sample_labels))
        base_values.append(fmean(sample_labels))
        ranked = _ranked_indices(sample_rows, sample_predictions)
        selected = ranked[: min(10, len(ranked))]
        expectancy_values.append(
            fmean(sample_rows[index].policy_return_pct for index in selected)
        )
    ranked = _ranked_indices(rows, predictions)
    selected = ranked[: min(10, len(ranked))]
    expectancy = fmean(rows[index].policy_return_pct for index in selected)
    return {
        "n": len(rows),
        "positives": int(sum(labels)),
        "base_rate": round(base_rate, 6),
        "brier_score": round(brier, 6),
        "log_loss": round(loss, 6),
        "expected_calibration_error": round(calibration_error, 6),
        "calibration_curve": curve,
        "top_k": _top_k_metrics(rows, predictions, labels),
        "bootstrap_95_clustered_by_event": {
            "base_rate": _interval(base_values, base_rate),
            "brier_score": _interval(brier_values, brier),
            "log_loss": _interval(loss_values, loss),
            "top_10_expectancy_after_spread_slippage_pct": _interval(
                expectancy_values, expectancy
            ),
        },
    }


def regression_metrics(
    rows: tuple[ModelRow, ...],
    predictions: tuple[float, ...],
    labels: tuple[float, ...],
    *,
    bootstrap_repetitions: int = 200,
    bootstrap_seed: int = 29,
) -> dict[str, object]:
    if not rows or not (len(rows) == len(predictions) == len(labels)):
        raise ValueError("regression metrics require aligned non-empty values")
    mae = fmean(abs(prediction - label) for prediction, label in zip(predictions, labels))
    rmse = math.sqrt(
        fmean((prediction - label) ** 2 for prediction, label in zip(predictions, labels))
    )
    bias = fmean(prediction - label for prediction, label in zip(predictions, labels))
    samples = _cluster_bootstrap_indices(
        rows, bootstrap_repetitions, bootstrap_seed
    )
    mae_values: list[float] = []
    rmse_values: list[float] = []
    for sample in samples:
        errors = tuple(predictions[index] - labels[index] for index in sample)
        mae_values.append(fmean(abs(value) for value in errors))
        rmse_values.append(math.sqrt(fmean(value * value for value in errors)))
    return {
        "n": len(rows),
        "mean_actual": round(fmean(labels), 6),
        "mean_prediction": round(fmean(predictions), 6),
        "mean_absolute_error": round(mae, 6),
        "root_mean_squared_error": round(rmse, 6),
        "bias": round(bias, 6),
        "bootstrap_95_clustered_by_event": {
            "mean_absolute_error": _interval(mae_values, mae),
            "root_mean_squared_error": _interval(rmse_values, rmse),
        },
    }


def performance_breakdowns(
    rows: tuple[ModelRow, ...],
    predictions: tuple[float, ...],
    labels: tuple[float, ...],
) -> dict[str, list[dict[str, object]]]:
    output: dict[str, list[dict[str, object]]] = {}
    for dimension in BREAKDOWN_DIMENSIONS:
        groups: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            groups[row.breakdown_value(dimension)].append(index)
        values: list[dict[str, object]] = []
        for category in sorted(groups):
            indices = groups[category]
            group_predictions = tuple(predictions[index] for index in indices)
            group_labels = tuple(labels[index] for index in indices)
            values.append(
                {
                    "category": category,
                    "n": len(indices),
                    "base_rate": round(fmean(group_labels), 6),
                    "brier_score": round(
                        brier_score(group_predictions, group_labels), 6
                    ),
                    "log_loss": round(log_loss(group_predictions, group_labels), 6),
                    "target_before_stop_rate": round(
                        fmean(float(rows[index].target_before_stop) for index in indices),
                        6,
                    ),
                    "expectancy_after_spread_slippage_pct": round(
                        fmean(rows[index].policy_return_pct for index in indices), 6
                    ),
                    "small_sample_warning": len(indices) < 10,
                }
            )
        output[dimension] = values
    return output


def feature_stability_report(
    snapshots: dict[str, list[dict[str, float]]],
) -> dict[str, object]:
    report: dict[str, object] = {}
    for model_name, model_snapshots in snapshots.items():
        names = sorted({name for snapshot in model_snapshots for name in snapshot})
        features: list[dict[str, object]] = []
        for name in names:
            values = [snapshot.get(name, 0.0) for snapshot in model_snapshots]
            mean = fmean(values)
            deviation = math.sqrt(fmean((value - mean) ** 2 for value in values))
            nonzero_signs = {1 if value > 1e-10 else -1 for value in values if abs(value) > 1e-10}
            sign_flip = len(nonzero_signs) > 1
            relative_std = deviation / max(abs(mean), 1e-8)
            presence = sum(abs(value) > 1e-10 for value in values) / len(values)
            unstable = sign_flip or relative_std > 1.5 or (0.2 < presence < 0.8)
            features.append(
                {
                    "feature": name,
                    "fold_mean_effect": round(mean, 6),
                    "fold_standard_deviation": round(deviation, 6),
                    "relative_standard_deviation": round(relative_std, 6),
                    "nonzero_fold_fraction": round(presence, 6),
                    "sign_flip": sign_flip,
                    "unstable": unstable,
                }
            )
        report[model_name] = {
            "folds": len(model_snapshots),
            "unstable_features": [
                value for value in features if bool(value["unstable"])
            ],
            "all_features": features,
        }
    return report


def mean_metric(values: list[dict[str, object]], key: str) -> float:
    return fmean(float(value[key]) for value in values)
