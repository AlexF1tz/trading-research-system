"""Deterministic transparent baselines and dependency-free statistical models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean

from .contracts import BinaryTarget, ModelRow, RegressionTarget


def sigmoid(value: float) -> float:
    if value >= 0:
        decay = math.exp(-min(value, 40.0))
        return 1.0 / (1.0 + decay)
    growth = math.exp(max(value, -40.0))
    return growth / (1.0 + growth)


def logit(probability: float) -> float:
    clipped = min(1.0 - 1e-8, max(1e-8, probability))
    return math.log(clipped / (1.0 - clipped))


def _smoothed_frequency(labels: tuple[float, ...]) -> float:
    return (sum(labels) + 1.0) / (len(labels) + 2.0)


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class HistoricalFrequencyBinary:
    name = "historical_frequency"

    def __init__(self, probability: float, trained_through: datetime) -> None:
        self.probability = probability
        self.trained_through = trained_through
        self.feature_effects: dict[str, float] = {}

    @classmethod
    def fit(
        cls,
        rows: tuple[ModelRow, ...],
        matrix: tuple[tuple[float, ...], ...],
        labels: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: BinaryTarget,
    ) -> "HistoricalFrequencyBinary":
        del matrix, feature_names, target
        return cls(
            _smoothed_frequency(labels),
            max(row.prediction_as_of for row in rows),
        )

    def predict(self, row: ModelRow, vector: tuple[float, ...]) -> float:
        del row, vector
        return self.probability


def _raw(row: ModelRow, name: str, default: float = 0.0) -> float:
    value = row.feature_map.get(name)
    return default if value is None else value


def _bullish_rule_score(row: ModelRow) -> float:
    return (
        0.45 * _clip((_raw(row, "relative_volume", 1.0) - 1.0) / 3.0, -1, 1)
        + 0.35 * _clip(_raw(row, "gap_pct") / 15.0, -1, 1)
        + 0.40 * _clip(_raw(row, "momentum_5m_pct") / 6.0, -1, 1)
        + 0.55
        * _clip((_raw(row, "catalyst_materiality", 50.0) - 50.0) / 50.0, -1, 1)
        + 0.25
        * _clip(_raw(row, "attention_acceleration") / 60.0, -1, 1)
        + 0.20
        * _clip(
            (_raw(row, "independent_author_score", 50.0) - 50.0) / 50.0,
            -1,
            1,
        )
        - 0.45 * _clip(_raw(row, "promotional_score") / 100.0, 0, 1)
        - 0.55 * _clip(_raw(row, "dilution_risk_score") / 100.0, 0, 1)
        - 0.40 * _clip(_raw(row, "spread_pct") / 5.0, 0, 1)
    )


class RuleBasedBinary:
    name = "transparent_rules"

    def __init__(
        self,
        base_logit: float,
        target: BinaryTarget,
        trained_through: datetime,
    ) -> None:
        self.base_logit = base_logit
        self.target = target
        self.trained_through = trained_through
        self.feature_effects = {
            "relative_volume": 0.45,
            "gap_pct": 0.35,
            "momentum_5m_pct": 0.40,
            "catalyst_materiality": 0.55,
            "attention_acceleration": 0.25,
            "independent_author_score": 0.20,
            "promotional_score": -0.45,
            "dilution_risk_score": -0.55,
            "spread_pct": -0.40,
        }

    @classmethod
    def fit(
        cls,
        rows: tuple[ModelRow, ...],
        matrix: tuple[tuple[float, ...], ...],
        labels: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: BinaryTarget,
    ) -> "RuleBasedBinary":
        del matrix, feature_names
        return cls(
            logit(_smoothed_frequency(labels)),
            target,
            max(row.prediction_as_of for row in rows),
        )

    def predict(self, row: ModelRow, vector: tuple[float, ...]) -> float:
        del vector
        bullish = _bullish_rule_score(row)
        if self.target in {BinaryTarget.TOUCH_DOWN_05, BinaryTarget.TOUCH_DOWN_10}:
            volatility = _clip(_raw(row, "realised_volatility") / 12.0, 0, 1)
            dilution = _clip(_raw(row, "dilution_risk_score") / 100.0, 0, 1)
            score = -0.65 * bullish + 0.35 * volatility + 0.30 * dilution
        else:
            score = bullish
        return sigmoid(self.base_logit + score)


@dataclass(frozen=True, slots=True)
class LogisticConfig:
    learning_rate: float = 0.08
    iterations: int = 280
    l2_penalty: float = 0.08


class LogisticBinary:
    name = "logistic_regression"

    def __init__(
        self,
        intercept: float,
        weights: tuple[float, ...],
        feature_names: tuple[str, ...],
        trained_through: datetime,
    ) -> None:
        self.intercept = intercept
        self.weights = weights
        self.trained_through = trained_through
        self.feature_effects = dict(zip(feature_names, weights))

    @classmethod
    def fit(
        cls,
        rows: tuple[ModelRow, ...],
        matrix: tuple[tuple[float, ...], ...],
        labels: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: BinaryTarget,
        config: LogisticConfig | None = None,
    ) -> "LogisticBinary":
        del target
        settings = config or LogisticConfig()
        intercept = logit(_smoothed_frequency(labels))
        weights = [0.0] * len(feature_names)
        count = len(labels)
        for iteration in range(settings.iterations):
            intercept_gradient = 0.0
            gradients = [0.0] * len(weights)
            for vector, label in zip(matrix, labels):
                probability = sigmoid(
                    intercept + sum(weight * value for weight, value in zip(weights, vector))
                )
                residual = probability - label
                intercept_gradient += residual
                for index, value in enumerate(vector):
                    gradients[index] += residual * value
            rate = settings.learning_rate / math.sqrt(1.0 + iteration / 100.0)
            intercept -= rate * intercept_gradient / count
            for index in range(len(weights)):
                gradient = gradients[index] / count + settings.l2_penalty * weights[index]
                weights[index] -= rate * gradient
        return cls(
            intercept,
            tuple(weights),
            feature_names,
            max(row.prediction_as_of for row in rows),
        )

    def predict(self, row: ModelRow, vector: tuple[float, ...]) -> float:
        del row
        return sigmoid(
            self.intercept
            + sum(weight * value for weight, value in zip(self.weights, vector))
        )


@dataclass(frozen=True, slots=True)
class DecisionStump:
    feature_index: int
    threshold: float
    left_value: float
    right_value: float
    gain: float

    def predict(self, vector: tuple[float, ...]) -> float:
        return self.left_value if vector[self.feature_index] <= self.threshold else self.right_value


@dataclass(frozen=True, slots=True)
class BoostingConfig:
    estimators: int = 16
    learning_rate: float = 0.12
    candidate_quantiles: tuple[float, ...] = (0.2, 0.4, 0.6, 0.8)


def _candidate_thresholds(
    matrix: tuple[tuple[float, ...], ...],
    feature_index: int,
    quantiles: tuple[float, ...],
) -> tuple[float, ...]:
    values = sorted({row[feature_index] for row in matrix})
    if len(values) < 2:
        return ()
    thresholds: set[float] = set()
    for quantile in quantiles:
        index = min(len(values) - 2, max(0, int(quantile * (len(values) - 1))))
        thresholds.add((values[index] + values[index + 1]) / 2.0)
    return tuple(sorted(thresholds))


def _best_stump(
    matrix: tuple[tuple[float, ...], ...],
    residuals: tuple[float, ...],
    quantiles: tuple[float, ...],
) -> DecisionStump | None:
    baseline_mean = fmean(residuals)
    baseline_error = sum((value - baseline_mean) ** 2 for value in residuals)
    best: DecisionStump | None = None
    best_error = baseline_error
    for feature_index in range(len(matrix[0])):
        for threshold in _candidate_thresholds(matrix, feature_index, quantiles):
            left = tuple(
                residual
                for vector, residual in zip(matrix, residuals)
                if vector[feature_index] <= threshold
            )
            right = tuple(
                residual
                for vector, residual in zip(matrix, residuals)
                if vector[feature_index] > threshold
            )
            if not left or not right:
                continue
            left_mean = fmean(left)
            right_mean = fmean(right)
            error = sum(
                (
                    residual
                    - (left_mean if vector[feature_index] <= threshold else right_mean)
                )
                ** 2
                for vector, residual in zip(matrix, residuals)
            )
            if error < best_error - 1e-12:
                best_error = error
                best = DecisionStump(
                    feature_index,
                    threshold,
                    left_mean,
                    right_mean,
                    baseline_error - error,
                )
    return best


class BoostedStumpsBinary:
    name = "gradient_boosted_trees"

    def __init__(
        self,
        base_logit: float,
        stumps: tuple[DecisionStump, ...],
        learning_rate: float,
        feature_names: tuple[str, ...],
        trained_through: datetime,
    ) -> None:
        self.base_logit = base_logit
        self.stumps = stumps
        self.learning_rate = learning_rate
        self.trained_through = trained_through
        effects: dict[str, float] = {name: 0.0 for name in feature_names}
        for stump in stumps:
            effects[feature_names[stump.feature_index]] += stump.gain
        total = sum(effects.values())
        self.feature_effects = {
            name: value / total if total > 0 else 0.0
            for name, value in effects.items()
        }

    @classmethod
    def fit(
        cls,
        rows: tuple[ModelRow, ...],
        matrix: tuple[tuple[float, ...], ...],
        labels: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: BinaryTarget,
        config: BoostingConfig | None = None,
    ) -> "BoostedStumpsBinary":
        del target
        settings = config or BoostingConfig()
        base = logit(_smoothed_frequency(labels))
        logits = [base] * len(labels)
        stumps: list[DecisionStump] = []
        for _ in range(settings.estimators):
            residuals = tuple(
                label - sigmoid(value) for label, value in zip(labels, logits)
            )
            stump = _best_stump(matrix, residuals, settings.candidate_quantiles)
            if stump is None or stump.gain <= 1e-12:
                break
            stumps.append(stump)
            for index, vector in enumerate(matrix):
                logits[index] += settings.learning_rate * stump.predict(vector)
        return cls(
            base,
            tuple(stumps),
            settings.learning_rate,
            feature_names,
            max(row.prediction_as_of for row in rows),
        )

    def predict(self, row: ModelRow, vector: tuple[float, ...]) -> float:
        del row
        value = self.base_logit + self.learning_rate * sum(
            stump.predict(vector) for stump in self.stumps
        )
        return sigmoid(value)


class HistoricalMeanRegression:
    name = "historical_mean"

    def __init__(self, mean: float, trained_through: datetime) -> None:
        self.mean = mean
        self.trained_through = trained_through
        self.feature_effects: dict[str, float] = {}

    @classmethod
    def fit(
        cls,
        rows: tuple[ModelRow, ...],
        matrix: tuple[tuple[float, ...], ...],
        labels: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: RegressionTarget,
    ) -> "HistoricalMeanRegression":
        del matrix, feature_names, target
        return cls(fmean(labels), max(row.prediction_as_of for row in rows))

    def predict(self, row: ModelRow, vector: tuple[float, ...]) -> float:
        del row, vector
        return self.mean


class RuleBasedRegression:
    name = "transparent_rules"

    def __init__(
        self,
        mean: float,
        target: RegressionTarget,
        trained_through: datetime,
    ) -> None:
        self.mean = mean
        self.target = target
        self.trained_through = trained_through
        self.feature_effects = {
            "relative_volume": 0.7,
            "gap_pct": 0.4,
            "momentum_5m_pct": 0.5,
            "realised_volatility": 0.5,
            "spread_pct": -0.4,
        }

    @classmethod
    def fit(
        cls,
        rows: tuple[ModelRow, ...],
        matrix: tuple[tuple[float, ...], ...],
        labels: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: RegressionTarget,
    ) -> "RuleBasedRegression":
        del matrix, feature_names
        return cls(fmean(labels), target, max(row.prediction_as_of for row in rows))

    def predict(self, row: ModelRow, vector: tuple[float, ...]) -> float:
        del vector
        bullish = _bullish_rule_score(row)
        volatility = _clip(_raw(row, "realised_volatility") / 10.0, 0, 1)
        if self.target is RegressionTarget.MFE:
            return max(0.0, self.mean + 2.5 * bullish + 1.5 * volatility)
        dilution = _clip(_raw(row, "dilution_risk_score") / 100.0, 0, 1)
        return min(0.0, self.mean + 1.2 * bullish - 1.5 * volatility - dilution)


@dataclass(frozen=True, slots=True)
class RidgeConfig:
    learning_rate: float = 0.025
    iterations: int = 360
    l2_penalty: float = 0.10


class RidgeRegression:
    name = "regularized_linear_regression"

    def __init__(
        self,
        intercept: float,
        weights: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: RegressionTarget,
        trained_through: datetime,
    ) -> None:
        self.intercept = intercept
        self.weights = weights
        self.target = target
        self.trained_through = trained_through
        self.feature_effects = dict(zip(feature_names, weights))

    @classmethod
    def fit(
        cls,
        rows: tuple[ModelRow, ...],
        matrix: tuple[tuple[float, ...], ...],
        labels: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: RegressionTarget,
        config: RidgeConfig | None = None,
    ) -> "RidgeRegression":
        settings = config or RidgeConfig()
        intercept = fmean(labels)
        weights = [0.0] * len(feature_names)
        count = len(labels)
        for iteration in range(settings.iterations):
            intercept_gradient = 0.0
            gradients = [0.0] * len(weights)
            for vector, label in zip(matrix, labels):
                prediction = intercept + sum(
                    weight * value for weight, value in zip(weights, vector)
                )
                residual = prediction - label
                intercept_gradient += residual
                for index, value in enumerate(vector):
                    gradients[index] += residual * value
            rate = settings.learning_rate / math.sqrt(1.0 + iteration / 150.0)
            intercept -= rate * intercept_gradient / count
            for index in range(len(weights)):
                gradient = gradients[index] / count + settings.l2_penalty * weights[index]
                weights[index] -= rate * gradient
        return cls(
            intercept,
            tuple(weights),
            feature_names,
            target,
            max(row.prediction_as_of for row in rows),
        )

    def predict(self, row: ModelRow, vector: tuple[float, ...]) -> float:
        del row
        value = self.intercept + sum(
            weight * feature for weight, feature in zip(self.weights, vector)
        )
        return max(0.0, value) if self.target is RegressionTarget.MFE else min(0.0, value)


class BoostedStumpsRegression:
    name = "gradient_boosted_trees"

    def __init__(
        self,
        base: float,
        stumps: tuple[DecisionStump, ...],
        learning_rate: float,
        feature_names: tuple[str, ...],
        target: RegressionTarget,
        trained_through: datetime,
    ) -> None:
        self.base = base
        self.stumps = stumps
        self.learning_rate = learning_rate
        self.target = target
        self.trained_through = trained_through
        effects: dict[str, float] = {name: 0.0 for name in feature_names}
        for stump in stumps:
            effects[feature_names[stump.feature_index]] += stump.gain
        total = sum(effects.values())
        self.feature_effects = {
            name: value / total if total > 0 else 0.0
            for name, value in effects.items()
        }

    @classmethod
    def fit(
        cls,
        rows: tuple[ModelRow, ...],
        matrix: tuple[tuple[float, ...], ...],
        labels: tuple[float, ...],
        feature_names: tuple[str, ...],
        target: RegressionTarget,
        config: BoostingConfig | None = None,
    ) -> "BoostedStumpsRegression":
        settings = config or BoostingConfig()
        base = fmean(labels)
        predictions = [base] * len(labels)
        stumps: list[DecisionStump] = []
        for _ in range(settings.estimators):
            residuals = tuple(
                label - prediction for label, prediction in zip(labels, predictions)
            )
            stump = _best_stump(matrix, residuals, settings.candidate_quantiles)
            if stump is None or stump.gain <= 1e-12:
                break
            stumps.append(stump)
            for index, vector in enumerate(matrix):
                predictions[index] += settings.learning_rate * stump.predict(vector)
        return cls(
            base,
            tuple(stumps),
            settings.learning_rate,
            feature_names,
            target,
            max(row.prediction_as_of for row in rows),
        )

    def predict(self, row: ModelRow, vector: tuple[float, ...]) -> float:
        del row
        value = self.base + self.learning_rate * sum(
            stump.predict(vector) for stump in self.stumps
        )
        return max(0.0, value) if self.target is RegressionTarget.MFE else min(0.0, value)


@dataclass(frozen=True, slots=True)
class PlattCalibrator:
    intercept: float
    slope: float
    fitted_through: datetime
    identity: bool = False

    @classmethod
    def fit(
        cls,
        probabilities: tuple[float, ...],
        labels: tuple[float, ...],
        rows: tuple[ModelRow, ...],
    ) -> "PlattCalibrator":
        if len(probabilities) < 10 or len(set(labels)) < 2:
            return cls(0.0, 1.0, max(row.prediction_as_of for row in rows), True)
        inputs = tuple(logit(value) for value in probabilities)
        intercept = 0.0
        slope = 1.0
        for iteration in range(220):
            intercept_gradient = 0.0
            slope_gradient = 0.0
            for value, label in zip(inputs, labels):
                residual = sigmoid(intercept + slope * value) - label
                intercept_gradient += residual
                slope_gradient += residual * value
            rate = 0.05 / math.sqrt(1.0 + iteration / 100.0)
            intercept -= rate * intercept_gradient / len(labels)
            slope -= rate * (slope_gradient / len(labels) + 0.02 * (slope - 1.0))
        return cls(
            intercept,
            slope,
            max(row.prediction_as_of for row in rows),
            False,
        )

    def predict(self, probability: float) -> float:
        if self.identity:
            return probability
        return sigmoid(self.intercept + self.slope * logit(probability))


BINARY_MODEL_CLASSES = (
    HistoricalFrequencyBinary,
    RuleBasedBinary,
    LogisticBinary,
    BoostedStumpsBinary,
)

REGRESSION_MODEL_CLASSES = (
    HistoricalMeanRegression,
    RuleBasedRegression,
    RidgeRegression,
    BoostedStumpsRegression,
)
