"""Fail-closed validation for point-in-time modelling rows and labels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from math import isfinite
from urllib.parse import urlparse

from .contracts import FillStatus, ModelDataset


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ModellingQualityIssue:
    code: str
    severity: Severity
    message: str
    observation_id: str | None = None
    timestamp: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "observation_id": self.observation_id,
            "timestamp": (
                self.timestamp.isoformat().replace("+00:00", "Z")
                if self.timestamp is not None
                else None
            ),
        }


class ModellingQualityError(RuntimeError):
    def __init__(self, issues: tuple[ModellingQualityIssue, ...]) -> None:
        self.issues = issues
        super().__init__(f"modelling data quality failed with {len(issues)} error(s)")


def _is_utc(value: datetime) -> bool:
    return (
        value.tzinfo is not None
        and value.utcoffset() is not None
        and value.utcoffset() == timedelta(0)
    )


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "fixture"} and bool(
        parsed.netloc or parsed.scheme == "fixture"
    )


def _timestamp_issue(
    issues: list[ModellingQualityIssue],
    name: str,
    value: datetime,
    observation_id: str | None,
) -> None:
    if not _is_utc(value):
        issues.append(
            ModellingQualityIssue(
                "TIMEZONE_NOT_UTC",
                Severity.ERROR,
                f"{name} must be timezone-aware UTC",
                observation_id,
                value,
            )
        )


def run_modelling_quality_checks(
    dataset: ModelDataset,
) -> tuple[ModellingQualityIssue, ...]:
    issues: list[ModellingQualityIssue] = []
    _timestamp_issue(issues, "fetched_at", dataset.fetched_at, None)
    if len(set(dataset.feature_names)) != len(dataset.feature_names):
        issues.append(
            ModellingQualityIssue(
                "DUPLICATE_FEATURE_NAME",
                Severity.ERROR,
                "dataset feature names must be unique",
            )
        )
    if not dataset.feature_names:
        issues.append(
            ModellingQualityIssue(
                "NO_MODEL_FEATURES",
                Severity.ERROR,
                "at least one model feature is required",
            )
        )
    if dataset.target_barrier_pct <= 0 or dataset.stop_barrier_pct >= 0:
        issues.append(
            ModellingQualityIssue(
                "INVALID_BARRIER_PAIR",
                Severity.ERROR,
                "target barrier must be positive and stop barrier negative",
            )
        )
    if not dataset.universe_survivorship_safe:
        issues.append(
            ModellingQualityIssue(
                "LIMITED_UNIVERSE_NOT_SURVIVORSHIP_SAFE",
                Severity.WARNING,
                "dataset cannot support survivorship-bias-safe claims",
            )
        )

    seen_ids: set[str] = set()
    expected_features = set(dataset.feature_names)
    for row in dataset.rows:
        if row.observation_id in seen_ids:
            issues.append(
                ModellingQualityIssue(
                    "DUPLICATE_OBSERVATION_ID",
                    Severity.ERROR,
                    "observation ID occurs more than once",
                    row.observation_id,
                )
            )
        seen_ids.add(row.observation_id)
        for name, value in (
            ("prediction_as_of", row.prediction_as_of),
            ("features_available_at", row.features_available_at),
            ("outcome_available_at", row.outcome_available_at),
        ):
            _timestamp_issue(issues, name, value, row.observation_id)
        if (
            _is_utc(row.features_available_at)
            and _is_utc(row.prediction_as_of)
            and row.features_available_at > row.prediction_as_of
        ):
            issues.append(
                ModellingQualityIssue(
                    "FEATURE_AVAILABLE_AFTER_PREDICTION",
                    Severity.ERROR,
                    "feature row contains information unavailable at prediction time",
                    row.observation_id,
                    row.features_available_at,
                )
            )
        if (
            _is_utc(row.outcome_available_at)
            and _is_utc(row.prediction_as_of)
            and row.outcome_available_at <= row.prediction_as_of
        ):
            issues.append(
                ModellingQualityIssue(
                    "OUTCOME_AVAILABLE_AT_PREDICTION",
                    Severity.ERROR,
                    "outcome must become available strictly after prediction time",
                    row.observation_id,
                    row.outcome_available_at,
                )
            )
        if (
            _is_utc(row.outcome_available_at)
            and _is_utc(dataset.fetched_at)
            and row.outcome_available_at > dataset.fetched_at
        ):
            issues.append(
                ModellingQualityIssue(
                    "OUTCOME_NOT_MATURED_AT_FETCH",
                    Severity.ERROR,
                    "outcome is not yet available at dataset fetch time",
                    row.observation_id,
                    row.outcome_available_at,
                )
            )
        if not row.observation_id or not row.event_id or not row.security_id:
            issues.append(
                ModellingQualityIssue(
                    "MISSING_STABLE_IDENTITY",
                    Severity.ERROR,
                    "observation, event, and security IDs are required",
                    row.observation_id,
                )
            )
        if not row.ticker or row.ticker.upper() != row.ticker:
            issues.append(
                ModellingQualityIssue(
                    "INVALID_TICKER",
                    Severity.ERROR,
                    "ticker must be a normalized uppercase as-of label",
                    row.observation_id,
                )
            )
        if not _valid_url(row.source_url):
            issues.append(
                ModellingQualityIssue(
                    "INVALID_SOURCE_URL",
                    Severity.ERROR,
                    "source URL must be http(s) or an explicit fixture URI",
                    row.observation_id,
                )
            )
        feature_keys = [name for name, _ in row.features]
        if len(feature_keys) != len(set(feature_keys)):
            issues.append(
                ModellingQualityIssue(
                    "DUPLICATE_ROW_FEATURE",
                    Severity.ERROR,
                    "feature appears more than once in a row",
                    row.observation_id,
                )
            )
        if set(feature_keys) != expected_features:
            issues.append(
                ModellingQualityIssue(
                    "FEATURE_SCHEMA_MISMATCH",
                    Severity.ERROR,
                    "row features do not exactly match dataset feature names",
                    row.observation_id,
                )
            )
        for feature_name, value in row.features:
            if value is not None and not isfinite(value):
                issues.append(
                    ModellingQualityIssue(
                        "NONFINITE_FEATURE",
                        Severity.ERROR,
                        f"feature {feature_name} is not finite",
                        row.observation_id,
                    )
                )
        if not (row.touch_up_20 <= row.touch_up_10 <= row.touch_up_05):
            issues.append(
                ModellingQualityIssue(
                    "NONMONOTONIC_UPSIDE_LABELS",
                    Severity.ERROR,
                    "+20/+10/+5 touch labels violate path monotonicity",
                    row.observation_id,
                )
            )
        if not (row.touch_down_10 <= row.touch_down_05):
            issues.append(
                ModellingQualityIssue(
                    "NONMONOTONIC_DOWNSIDE_LABELS",
                    Severity.ERROR,
                    "-10/-5 touch labels violate path monotonicity",
                    row.observation_id,
                )
            )
        if row.target_before_stop and not row.touch_up_10:
            issues.append(
                ModellingQualityIssue(
                    "TARGET_FIRST_WITHOUT_TARGET_TOUCH",
                    Severity.ERROR,
                    "target-before-stop requires the configured target touch",
                    row.observation_id,
                )
            )
        expected_up = (
            row.mfe_pct >= 5.0,
            row.mfe_pct >= 10.0,
            row.mfe_pct >= 20.0,
        )
        if expected_up != (row.touch_up_05, row.touch_up_10, row.touch_up_20):
            issues.append(
                ModellingQualityIssue(
                    "MFE_TOUCH_INCONSISTENCY",
                    Severity.ERROR,
                    "MFE is inconsistent with upside touch labels",
                    row.observation_id,
                )
            )
        expected_down = (row.mae_pct <= -5.0, row.mae_pct <= -10.0)
        if expected_down != (row.touch_down_05, row.touch_down_10):
            issues.append(
                ModellingQualityIssue(
                    "MAE_TOUCH_INCONSISTENCY",
                    Severity.ERROR,
                    "MAE is inconsistent with downside touch labels",
                    row.observation_id,
                )
            )
        if row.mfe_pct < 0 or row.mae_pct > 0:
            issues.append(
                ModellingQualityIssue(
                    "INVALID_EXCURSION_SIGN",
                    Severity.ERROR,
                    "MFE must be non-negative and MAE non-positive",
                    row.observation_id,
                )
            )
        for name, value in (
            ("mfe_pct", row.mfe_pct),
            ("mae_pct", row.mae_pct),
            ("spread_cost_pct", row.spread_cost_pct),
            ("slippage_cost_pct", row.slippage_cost_pct),
            ("data_quality_score", row.data_quality_score),
        ):
            if not isfinite(value):
                issues.append(
                    ModellingQualityIssue(
                        "NONFINITE_OUTCOME_VALUE",
                        Severity.ERROR,
                        f"{name} must be finite",
                        row.observation_id,
                    )
                )
        if row.spread_cost_pct < 0 or row.slippage_cost_pct < 0:
            issues.append(
                ModellingQualityIssue(
                    "NEGATIVE_EXECUTION_COST",
                    Severity.ERROR,
                    "spread and slippage costs cannot be negative",
                    row.observation_id,
                )
            )
        if not 0 <= row.data_quality_score <= 100:
            issues.append(
                ModellingQualityIssue(
                    "INVALID_DATA_QUALITY_SCORE",
                    Severity.ERROR,
                    "data quality score must be between zero and 100",
                    row.observation_id,
                )
            )
        if row.fill_status is FillStatus.FILLED:
            if row.gross_return_pct is None or row.net_return_after_cost_pct is None:
                issues.append(
                    ModellingQualityIssue(
                        "FILLED_RETURN_MISSING",
                        Severity.ERROR,
                        "filled observations require gross and net returns",
                        row.observation_id,
                    )
                )
            elif abs(
                row.net_return_after_cost_pct
                - (
                    row.gross_return_pct
                    - row.spread_cost_pct
                    - row.slippage_cost_pct
                )
            ) > 1e-8:
                issues.append(
                    ModellingQualityIssue(
                        "NET_RETURN_COST_MISMATCH",
                        Severity.ERROR,
                        "net return does not equal gross less spread and slippage",
                        row.observation_id,
                    )
                )
        elif row.gross_return_pct is not None or row.net_return_after_cost_pct is not None:
            issues.append(
                ModellingQualityIssue(
                    "UNFILLED_RETURN_PRESENT",
                    Severity.ERROR,
                    "unfilled observations cannot carry a simulated return",
                    row.observation_id,
                )
            )
        for value in (
            row.catalyst_category,
            row.float_category,
            row.market_cap_category,
            row.market_regime,
            row.time_of_day,
            row.gap_size_category,
            row.relative_volume_category,
            row.retail_attention_stage,
            row.label_policy_version,
            row.fill_policy_version,
        ):
            if not value.strip():
                issues.append(
                    ModellingQualityIssue(
                        "MISSING_CATEGORY_OR_POLICY",
                        Severity.ERROR,
                        "breakdown categories and policy versions cannot be empty",
                        row.observation_id,
                    )
                )
                break
    if not dataset.rows:
        issues.append(
            ModellingQualityIssue(
                "EMPTY_MODEL_DATASET",
                Severity.ERROR,
                "dataset contains no modelling rows",
            )
        )
    return tuple(issues)
