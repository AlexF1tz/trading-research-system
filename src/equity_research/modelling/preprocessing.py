"""Training-only median imputation and standardization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from statistics import fmean, median

from .contracts import ModelRow


@dataclass(frozen=True, slots=True)
class FittedTransformer:
    feature_names: tuple[str, ...]
    medians: tuple[float, ...]
    means: tuple[float, ...]
    standard_deviations: tuple[float, ...]
    output_names: tuple[str, ...]
    fitted_through: datetime

    def transform(self, row: ModelRow) -> tuple[float, ...]:
        values = row.feature_map
        output: list[float] = []
        missing: list[float] = []
        for index, name in enumerate(self.feature_names):
            raw = values[name]
            is_missing = raw is None
            value = self.medians[index] if is_missing else raw
            output.append(
                (value - self.means[index]) / self.standard_deviations[index]
            )
            missing.append(1.0 if is_missing else 0.0)
        return tuple(output + missing)

    def transform_many(self, rows: tuple[ModelRow, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.transform(row) for row in rows)


def fit_transformer(
    rows: tuple[ModelRow, ...], feature_names: tuple[str, ...]
) -> FittedTransformer:
    if not rows:
        raise ValueError("cannot fit preprocessing without training rows")
    medians: list[float] = []
    means: list[float] = []
    deviations: list[float] = []
    for name in feature_names:
        observed = tuple(
            value
            for row in rows
            if (value := row.feature_map[name]) is not None
        )
        if not observed:
            raise ValueError(f"training feature is entirely missing: {name}")
        middle = float(median(observed))
        imputed = tuple(
            row.feature_map[name]
            if row.feature_map[name] is not None
            else middle
            for row in rows
        )
        mean = fmean(imputed)
        variance = fmean((value - mean) ** 2 for value in imputed)
        medians.append(middle)
        means.append(mean)
        deviations.append(sqrt(variance) if variance > 1e-12 else 1.0)
    return FittedTransformer(
        feature_names=feature_names,
        medians=tuple(medians),
        means=tuple(means),
        standard_deviations=tuple(deviations),
        output_names=feature_names
        + tuple(f"{name}__missing" for name in feature_names),
        fitted_through=max(row.prediction_as_of for row in rows),
    )
