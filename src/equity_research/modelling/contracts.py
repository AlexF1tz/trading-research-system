"""Provider-neutral, point-in-time modelling and outcome contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class BinaryTarget(str, Enum):
    TARGET_BEFORE_STOP = "target_before_stop_10_up_05_down"
    TOUCH_UP_05 = "touch_up_05"
    TOUCH_UP_10 = "touch_up_10"
    TOUCH_UP_20 = "touch_up_20"
    TOUCH_DOWN_05 = "touch_down_05"
    TOUCH_DOWN_10 = "touch_down_10"
    CONTINUATION = "continuation"


class RegressionTarget(str, Enum):
    MFE = "maximum_favourable_excursion_pct"
    MAE = "maximum_adverse_excursion_pct"


class FillStatus(str, Enum):
    FILLED = "filled"
    UNFILLED = "unfilled"


class SplitName(str, Enum):
    TRAIN = "train"
    CALIBRATION = "calibration"
    FINAL_TEST = "final_test"
    EMBARGO = "embargo"
    OUT_OF_RANGE = "out_of_range"


@dataclass(frozen=True, slots=True)
class ModelRow:
    observation_id: str
    event_id: str
    security_id: str
    ticker: str
    prediction_as_of: datetime
    features_available_at: datetime
    outcome_available_at: datetime
    source_url: str
    features: tuple[tuple[str, float | None], ...]
    target_before_stop: bool
    touch_up_05: bool
    touch_up_10: bool
    touch_up_20: bool
    touch_down_05: bool
    touch_down_10: bool
    mfe_pct: float
    mae_pct: float
    continuation: bool | None
    fill_status: FillStatus
    gross_return_pct: float | None
    spread_cost_pct: float
    slippage_cost_pct: float
    net_return_after_cost_pct: float | None
    catalyst_category: str
    float_category: str
    market_cap_category: str
    market_regime: str
    time_of_day: str
    gap_size_category: str
    relative_volume_category: str
    retail_attention_stage: str
    data_quality_score: float
    label_policy_version: str
    fill_policy_version: str

    @property
    def feature_map(self) -> dict[str, float | None]:
        return dict(self.features)

    def to_dict(self) -> dict[str, Any]:
        def timestamp(value: datetime) -> str:
            return value.isoformat().replace("+00:00", "Z")

        return {
            "observation_id": self.observation_id,
            "event_id": self.event_id,
            "security_id": self.security_id,
            "ticker": self.ticker,
            "prediction_as_of": timestamp(self.prediction_as_of),
            "features_available_at": timestamp(self.features_available_at),
            "outcome_available_at": timestamp(self.outcome_available_at),
            "source_url": self.source_url,
            "features": dict(self.features),
            "target_before_stop": self.target_before_stop,
            "touch_up_05": self.touch_up_05,
            "touch_up_10": self.touch_up_10,
            "touch_up_20": self.touch_up_20,
            "touch_down_05": self.touch_down_05,
            "touch_down_10": self.touch_down_10,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "continuation": self.continuation,
            "fill_status": self.fill_status.value,
            "gross_return_pct": self.gross_return_pct,
            "spread_cost_pct": self.spread_cost_pct,
            "slippage_cost_pct": self.slippage_cost_pct,
            "net_return_after_cost_pct": self.net_return_after_cost_pct,
            "catalyst_category": self.catalyst_category,
            "float_category": self.float_category,
            "market_cap_category": self.market_cap_category,
            "market_regime": self.market_regime,
            "time_of_day": self.time_of_day,
            "gap_size_category": self.gap_size_category,
            "relative_volume_category": self.relative_volume_category,
            "retail_attention_stage": self.retail_attention_stage,
            "data_quality_score": self.data_quality_score,
            "label_policy_version": self.label_policy_version,
            "fill_policy_version": self.fill_policy_version,
        }

    def binary_label(self, target: BinaryTarget) -> bool | None:
        values: dict[BinaryTarget, bool | None] = {
            BinaryTarget.TARGET_BEFORE_STOP: self.target_before_stop,
            BinaryTarget.TOUCH_UP_05: self.touch_up_05,
            BinaryTarget.TOUCH_UP_10: self.touch_up_10,
            BinaryTarget.TOUCH_UP_20: self.touch_up_20,
            BinaryTarget.TOUCH_DOWN_05: self.touch_down_05,
            BinaryTarget.TOUCH_DOWN_10: self.touch_down_10,
            BinaryTarget.CONTINUATION: self.continuation,
        }
        return values[target]

    def regression_label(self, target: RegressionTarget) -> float:
        return self.mfe_pct if target is RegressionTarget.MFE else self.mae_pct

    @property
    def policy_return_pct(self) -> float:
        return self.net_return_after_cost_pct or 0.0

    def breakdown_value(self, dimension: str) -> str:
        values = {
            "catalyst_category": self.catalyst_category,
            "float_category": self.float_category,
            "market_cap_category": self.market_cap_category,
            "market_regime": self.market_regime,
            "time_of_day": self.time_of_day,
            "gap_size": self.gap_size_category,
            "relative_volume": self.relative_volume_category,
            "retail_attention_stage": self.retail_attention_stage,
        }
        if dimension not in values:
            raise KeyError(f"unknown breakdown dimension: {dimension}")
        return values[dimension]


@dataclass(frozen=True, slots=True)
class ModelDataset:
    provider: str
    dataset_kind: str
    fetched_at: datetime
    feature_names: tuple[str, ...]
    rows: tuple[ModelRow, ...]
    target_barrier_pct: float
    stop_barrier_pct: float
    universe_survivorship_safe: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChronologicalSplitConfig:
    train_end: datetime
    calibration_start: datetime
    calibration_end: datetime
    final_test_start: datetime
    final_test_end: datetime

    def split_for(self, timestamp: datetime) -> SplitName:
        if timestamp <= self.train_end:
            return SplitName.TRAIN
        if self.calibration_start <= timestamp <= self.calibration_end:
            return SplitName.CALIBRATION
        if self.final_test_start <= timestamp <= self.final_test_end:
            return SplitName.FINAL_TEST
        if self.train_end < timestamp < self.final_test_start:
            return SplitName.EMBARGO
        return SplitName.OUT_OF_RANGE


@dataclass(frozen=True, slots=True)
class PredictionValue:
    observation_id: str
    event_id: str
    security_id: str
    prediction_as_of: datetime
    target: str
    model_name: str
    value: float
    calibrated: bool
    trained_through: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "event_id": self.event_id,
            "security_id": self.security_id,
            "prediction_as_of": self.prediction_as_of.isoformat().replace(
                "+00:00", "Z"
            ),
            "target": self.target,
            "model_name": self.model_name,
            "value": self.value,
            "calibrated": self.calibrated,
            "trained_through": self.trained_through.isoformat().replace(
                "+00:00", "Z"
            ),
        }


BINARY_TARGETS = tuple(BinaryTarget)
REGRESSION_TARGETS = tuple(RegressionTarget)
BREAKDOWN_DIMENSIONS = (
    "catalyst_category",
    "float_category",
    "market_cap_category",
    "market_regime",
    "time_of_day",
    "gap_size",
    "relative_volume",
    "retail_attention_stage",
)
