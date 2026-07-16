"""Independent metric calculations; intentionally does not import modelling.evaluation."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import fmean

from equity_research.modelling.contracts import (
    BREAKDOWN_DIMENSIONS,
    BINARY_TARGETS,
    REGRESSION_TARGETS,
    BinaryTarget,
    ModelRow,
    PredictionValue,
)


TOP_K = (1, 3, 5, 10)


@dataclass(frozen=True, slots=True)
class MetricReproductionResult:
    checks: int
    matches: int
    mismatches: tuple[dict[str, object], ...]

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, object]:
        return {
            "checks": self.checks,
            "matches": self.matches,
            "mismatch_count": len(self.mismatches),
            "mismatches": list(self.mismatches),
            "passed": self.passed,
            "implementation_independence": (
                "does_not_import_equity_research.modelling.evaluation"
            ),
        }


class _Comparator:
    def __init__(self, tolerance: float = 1.1e-6) -> None:
        self.tolerance = tolerance
        self.checks = 0
        self.matches = 0
        self.mismatches: list[dict[str, object]] = []

    def check(self, path: str, reproduced: object, reported: object) -> None:
        self.checks += 1
        if reproduced is None or reported is None:
            matches = reproduced is reported
        elif isinstance(reproduced, (int, float)) and isinstance(reported, (int, float)):
            matches = abs(float(reproduced) - float(reported)) <= self.tolerance
        else:
            matches = reproduced == reported
        if matches:
            self.matches += 1
        else:
            self.mismatches.append(
                {
                    "path": path,
                    "reproduced": reproduced,
                    "reported": reported,
                }
            )

    def result(self) -> MetricReproductionResult:
        return MetricReproductionResult(
            self.checks,
            self.matches,
            tuple(self.mismatches),
        )


def _binary_rows(rows: tuple[ModelRow, ...], target: BinaryTarget) -> tuple[ModelRow, ...]:
    return tuple(row for row in rows if row.binary_label(target) is not None)


def _labels(rows: tuple[ModelRow, ...], target: BinaryTarget) -> tuple[float, ...]:
    return tuple(float(bool(row.binary_label(target))) for row in rows)


def _clip(value: float) -> float:
    return min(1.0 - 1e-12, max(1e-12, value))


def _brier(predictions: tuple[float, ...], labels: tuple[float, ...]) -> float:
    return fmean((prediction - label) ** 2 for prediction, label in zip(predictions, labels))


def _log_loss(predictions: tuple[float, ...], labels: tuple[float, ...]) -> float:
    return -fmean(
        label * math.log(_clip(prediction))
        + (1.0 - label) * math.log(_clip(1.0 - prediction))
        for prediction, label in zip(predictions, labels)
    )


def _ranked(rows: tuple[ModelRow, ...], predictions: tuple[float, ...]) -> tuple[int, ...]:
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


def _calibration(
    predictions: tuple[float, ...], labels: tuple[float, ...], bins: int
) -> tuple[list[dict[str, object]], float]:
    curve: list[dict[str, object]] = []
    error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = tuple(
            position
            for position, value in enumerate(predictions)
            if lower <= value < upper or (index == bins - 1 and value == 1.0)
        )
        if not members:
            curve.append(
                {
                    "count": 0,
                    "mean_prediction": None,
                    "event_rate": None,
                }
            )
            continue
        predicted = fmean(predictions[position] for position in members)
        observed = fmean(labels[position] for position in members)
        error += len(members) / len(labels) * abs(predicted - observed)
        curve.append(
            {
                "count": len(members),
                "mean_prediction": round(predicted, 6),
                "event_rate": round(observed, 6),
            }
        )
    return curve, error


def _top_metrics(
    rows: tuple[ModelRow, ...],
    predictions: tuple[float, ...],
    labels: tuple[float, ...],
) -> dict[str, dict[str, object]]:
    ranking = _ranked(rows, predictions)
    output: dict[str, dict[str, object]] = {}
    for requested in TOP_K:
        count = min(requested, len(ranking))
        chosen = ranking[:count]
        output[str(requested)] = {
            "actual_n": count,
            "precision": round(fmean(labels[index] for index in chosen), 6),
            "base_rate": round(fmean(labels), 6),
            "target_before_stop_rate": round(
                fmean(float(rows[index].target_before_stop) for index in chosen), 6
            ),
            "expectancy_after_spread_slippage_pct": round(
                fmean(rows[index].policy_return_pct for index in chosen), 6
            ),
            "fill_rate": round(
                sum(rows[index].net_return_after_cost_pct is not None for index in chosen)
                / count,
                6,
            ),
        }
    return output


def _cluster_samples(
    rows: tuple[ModelRow, ...], repetitions: int, seed: int
) -> tuple[tuple[int, ...], ...]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[row.event_id].append(index)
    identifiers = tuple(sorted(groups))
    generator = random.Random(seed)
    return tuple(
        tuple(
            index
            for identifier in (
                generator.choice(identifiers) for _ in identifiers
            )
            for index in groups[identifier]
        )
        for _ in range(repetitions)
    )


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
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


def _classification_bootstrap(
    rows: tuple[ModelRow, ...],
    predictions: tuple[float, ...],
    labels: tuple[float, ...],
    repetitions: int,
    seed: int,
) -> dict[str, dict[str, float]]:
    briers: list[float] = []
    losses: list[float] = []
    bases: list[float] = []
    expectancies: list[float] = []
    for sample in _cluster_samples(rows, repetitions, seed):
        sample_rows = tuple(rows[index] for index in sample)
        sample_predictions = tuple(predictions[index] for index in sample)
        sample_labels = tuple(labels[index] for index in sample)
        briers.append(_brier(sample_predictions, sample_labels))
        losses.append(_log_loss(sample_predictions, sample_labels))
        bases.append(fmean(sample_labels))
        ranking = _ranked(sample_rows, sample_predictions)
        chosen = ranking[: min(10, len(ranking))]
        expectancies.append(
            fmean(sample_rows[index].policy_return_pct for index in chosen)
        )
    ranking = _ranked(rows, predictions)
    chosen = ranking[: min(10, len(ranking))]
    expectancy = fmean(rows[index].policy_return_pct for index in chosen)
    return {
        "base_rate": _interval(bases, fmean(labels)),
        "brier_score": _interval(briers, _brier(predictions, labels)),
        "log_loss": _interval(losses, _log_loss(predictions, labels)),
        "top_10_expectancy_after_spread_slippage_pct": _interval(
            expectancies, expectancy
        ),
    }


def _regression_bootstrap(
    rows: tuple[ModelRow, ...],
    predictions: tuple[float, ...],
    labels: tuple[float, ...],
    repetitions: int,
    seed: int,
) -> dict[str, dict[str, float]]:
    maes: list[float] = []
    rmses: list[float] = []
    for sample in _cluster_samples(rows, repetitions, seed):
        errors = tuple(predictions[index] - labels[index] for index in sample)
        maes.append(fmean(abs(value) for value in errors))
        rmses.append(math.sqrt(fmean(value * value for value in errors)))
    errors = tuple(prediction - label for prediction, label in zip(predictions, labels))
    mae = fmean(abs(value) for value in errors)
    rmse = math.sqrt(fmean(value * value for value in errors))
    return {
        "mean_absolute_error": _interval(maes, mae),
        "root_mean_squared_error": _interval(rmses, rmse),
    }


def _prediction_lookup(
    predictions: tuple[PredictionValue, ...]
) -> dict[tuple[str, str], dict[str, float]]:
    values: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for prediction in predictions:
        values[(prediction.target, prediction.model_name)][
            prediction.observation_id
        ] = prediction.value
    return values


def reproduce_report_metrics(
    report: dict[str, object],
    predictions: tuple[PredictionValue, ...],
    final_rows: tuple[ModelRow, ...],
    *,
    calibration_bins: int,
    bootstrap_repetitions: int,
    bootstrap_seed: int,
) -> MetricReproductionResult:
    compare = _Comparator()
    lookup = _prediction_lookup(predictions)
    classification = report["classification"]
    assert isinstance(classification, dict)
    for target in BINARY_TARGETS:
        target_rows = _binary_rows(final_rows, target)
        labels = _labels(target_rows, target)
        target_report = classification[target.value]
        models = target_report["models"]
        compare.check(
            f"classification.{target.value}.base_rates.final_test",
            round(fmean(labels), 6),
            target_report["base_rates"]["final_test"],
        )
        for model_name, model_report in models.items():
            prediction_map = lookup[(target.value, model_name)]
            values = tuple(prediction_map[row.observation_id] for row in target_rows)
            final = model_report["final_test"]
            curve, ece = _calibration(values, labels, calibration_bins)
            scalar_values = {
                "base_rate": round(fmean(labels), 6),
                "brier_score": round(_brier(values, labels), 6),
                "log_loss": round(_log_loss(values, labels), 6),
                "expected_calibration_error": round(ece, 6),
            }
            for name, reproduced in scalar_values.items():
                compare.check(
                    f"classification.{target.value}.{model_name}.{name}",
                    reproduced,
                    final[name],
                )
            for index, reproduced_bin in enumerate(curve):
                reported_bin = final["calibration_curve"][index]
                for name in ("count", "mean_prediction", "event_rate"):
                    compare.check(
                        f"classification.{target.value}.{model_name}.calibration[{index}].{name}",
                        reproduced_bin[name],
                        reported_bin[name],
                    )
            top = _top_metrics(target_rows, values, labels)
            for key in TOP_K:
                for name, reproduced in top[str(key)].items():
                    compare.check(
                        f"classification.{target.value}.{model_name}.top_{key}.{name}",
                        reproduced,
                        final["top_k"][str(key)][name],
                    )
            bootstrap = _classification_bootstrap(
                target_rows,
                values,
                labels,
                bootstrap_repetitions,
                bootstrap_seed,
            )
            reported_bootstrap = final["bootstrap_95_clustered_by_event"]
            for metric_name, interval in bootstrap.items():
                for endpoint, reproduced in interval.items():
                    compare.check(
                        f"classification.{target.value}.{model_name}.bootstrap.{metric_name}.{endpoint}",
                        reproduced,
                        reported_bootstrap[metric_name][endpoint],
                    )

    regression = report["regression"]
    assert isinstance(regression, dict)
    for target in REGRESSION_TARGETS:
        labels = tuple(row.regression_label(target) for row in final_rows)
        models = regression[target.value]["models"]
        for model_name, model_report in models.items():
            prediction_map = lookup[(target.value, model_name)]
            values = tuple(prediction_map[row.observation_id] for row in final_rows)
            errors = tuple(value - label for value, label in zip(values, labels))
            mae = fmean(abs(value) for value in errors)
            rmse = math.sqrt(fmean(value * value for value in errors))
            reproduced_scalars = {
                "mean_actual": round(fmean(labels), 6),
                "mean_prediction": round(fmean(values), 6),
                "mean_absolute_error": round(mae, 6),
                "root_mean_squared_error": round(rmse, 6),
                "bias": round(fmean(errors), 6),
            }
            final = model_report["final_test"]
            for name, reproduced in reproduced_scalars.items():
                compare.check(
                    f"regression.{target.value}.{model_name}.{name}",
                    reproduced,
                    final[name],
                )
            bootstrap = _regression_bootstrap(
                final_rows,
                values,
                labels,
                bootstrap_repetitions,
                bootstrap_seed + 11,
            )
            reported_bootstrap = final["bootstrap_95_clustered_by_event"]
            for metric_name, interval in bootstrap.items():
                for endpoint, reproduced in interval.items():
                    compare.check(
                        f"regression.{target.value}.{model_name}.bootstrap.{metric_name}.{endpoint}",
                        reproduced,
                        reported_bootstrap[metric_name][endpoint],
                    )

    selected = report["target_before_stop_breakdowns_selected_model"]
    selected_model = selected["model"]
    target = BinaryTarget.TARGET_BEFORE_STOP
    prediction_map = lookup[(target.value, selected_model)]
    values = tuple(prediction_map[row.observation_id] for row in final_rows)
    labels = _labels(final_rows, target)
    for dimension in BREAKDOWN_DIMENSIONS:
        groups: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(final_rows):
            groups[row.breakdown_value(dimension)].append(index)
        reported_groups = {
            value["category"]: value
            for value in selected["dimensions"][dimension]
        }
        for category, indices in groups.items():
            group_predictions = tuple(values[index] for index in indices)
            group_labels = tuple(labels[index] for index in indices)
            reproduced = {
                "n": len(indices),
                "base_rate": round(fmean(group_labels), 6),
                "brier_score": round(_brier(group_predictions, group_labels), 6),
                "log_loss": round(_log_loss(group_predictions, group_labels), 6),
                "target_before_stop_rate": round(
                    fmean(float(final_rows[index].target_before_stop) for index in indices),
                    6,
                ),
                "expectancy_after_spread_slippage_pct": round(
                    fmean(final_rows[index].policy_return_pct for index in indices), 6
                ),
            }
            for name, value in reproduced.items():
                compare.check(
                    f"breakdowns.{dimension}.{category}.{name}",
                    value,
                    reported_groups[category][name],
                )
    return compare.result()


def cost_stress_summary(
    rows: tuple[ModelRow, ...],
    predictions: tuple[float, ...],
    *,
    cost_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0),
    top_k: int = 10,
) -> dict[str, object]:
    ranking = _ranked(rows, predictions)
    selected = ranking[: min(top_k, len(ranking))]
    scenarios: dict[str, float] = {}
    for multiplier in cost_multipliers:
        returns: list[float] = []
        for index in selected:
            row = rows[index]
            if row.gross_return_pct is None:
                returns.append(0.0)
            else:
                returns.append(
                    row.gross_return_pct
                    - multiplier * (row.spread_cost_pct + row.slippage_cost_pct)
                )
        scenarios[f"{multiplier:.1f}x_declared_cost"] = round(fmean(returns), 6)
    low_float_stress: list[float] = []
    for index in selected:
        row = rows[index]
        if row.gross_return_pct is None or row.float_category == "under_10m":
            low_float_stress.append(0.0)
        else:
            low_float_stress.append(
                row.gross_return_pct
                - 2.5 * (row.spread_cost_pct + row.slippage_cost_pct)
            )
    scenarios["low_float_unfilled_and_2.5x_cost"] = round(
        fmean(low_float_stress), 6
    )
    return {
        "top_k": len(selected),
        "scenarios": scenarios,
        "all_scenarios_positive": all(value > 0 for value in scenarios.values()),
    }


def security_concentration_summary(
    rows: tuple[ModelRow, ...], predictions: tuple[float, ...], top_k: int = 10
) -> dict[str, object]:
    ranking = _ranked(rows, predictions)
    selected = ranking[: min(top_k, len(ranking))]
    counts = Counter(rows[index].security_id for index in selected)
    maximum_share = max(counts.values()) / len(selected)
    overall_brier = _brier(
        predictions, tuple(float(row.target_before_stop) for row in rows)
    )
    leave_one_out: list[dict[str, object]] = []
    for security_id in sorted({row.security_id for row in rows}):
        indices = tuple(
            index for index, row in enumerate(rows) if row.security_id != security_id
        )
        score = _brier(
            tuple(predictions[index] for index in indices),
            tuple(float(rows[index].target_before_stop) for index in indices),
        )
        leave_one_out.append(
            {
                "excluded_security_id": security_id,
                "brier_score": round(score, 6),
                "change_from_overall": round(score - overall_brier, 6),
            }
        )
    return {
        "top_k": len(selected),
        "top_k_counts_by_security": dict(sorted(counts.items())),
        "maximum_single_security_share": round(maximum_share, 6),
        "concentration_threshold": 0.30,
        "concentration_failure": maximum_share > 0.30,
        "overall_brier_score": round(overall_brier, 6),
        "leave_one_security_out": leave_one_out,
    }
