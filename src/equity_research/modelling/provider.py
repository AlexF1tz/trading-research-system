"""Replaceable providers for normalized, matured modelling rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import FillStatus, ModelDataset, ModelRow


@runtime_checkable
class ModelDatasetProvider(Protocol):
    @property
    def name(self) -> str: ...

    def load(self) -> ModelDataset: ...


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp lacks timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


class JsonlModelDatasetProvider:
    """Load `metadata.json` and `rows.jsonl` without vendor dependencies."""

    def __init__(self, root: Path) -> None:
        self._root = root
        metadata_path = root / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"missing model metadata: {metadata_path}")
        self._metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        for name in (
            "provider",
            "dataset_kind",
            "fetched_at",
            "feature_names",
            "target_barrier_pct",
            "stop_barrier_pct",
            "universe_survivorship_safe",
        ):
            if name not in self._metadata:
                raise ValueError(f"model metadata missing required field: {name}")

    @property
    def name(self) -> str:
        return str(self._metadata["provider"])

    def load(self) -> ModelDataset:
        rows_path = self._root / "rows.jsonl"
        if not rows_path.exists():
            raise FileNotFoundError(f"missing modelling rows: {rows_path}")
        rows: list[ModelRow] = []
        with rows_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    features = value["features"]
                    if not isinstance(features, dict):
                        raise ValueError("features must be an object")
                    rows.append(
                        ModelRow(
                            observation_id=str(value["observation_id"]),
                            event_id=str(value["event_id"]),
                            security_id=str(value["security_id"]),
                            ticker=str(value["ticker"]),
                            prediction_as_of=parse_timestamp(
                                str(value["prediction_as_of"])
                            ),
                            features_available_at=parse_timestamp(
                                str(value["features_available_at"])
                            ),
                            outcome_available_at=parse_timestamp(
                                str(value["outcome_available_at"])
                            ),
                            source_url=str(value["source_url"]),
                            features=tuple(
                                (str(name), float(raw) if raw is not None else None)
                                for name, raw in features.items()
                            ),
                            target_before_stop=bool(value["target_before_stop"]),
                            touch_up_05=bool(value["touch_up_05"]),
                            touch_up_10=bool(value["touch_up_10"]),
                            touch_up_20=bool(value["touch_up_20"]),
                            touch_down_05=bool(value["touch_down_05"]),
                            touch_down_10=bool(value["touch_down_10"]),
                            mfe_pct=float(value["mfe_pct"]),
                            mae_pct=float(value["mae_pct"]),
                            continuation=(
                                bool(value["continuation"])
                                if value.get("continuation") is not None
                                else None
                            ),
                            fill_status=FillStatus(str(value["fill_status"])),
                            gross_return_pct=(
                                float(value["gross_return_pct"])
                                if value.get("gross_return_pct") is not None
                                else None
                            ),
                            spread_cost_pct=float(value["spread_cost_pct"]),
                            slippage_cost_pct=float(value["slippage_cost_pct"]),
                            net_return_after_cost_pct=(
                                float(value["net_return_after_cost_pct"])
                                if value.get("net_return_after_cost_pct") is not None
                                else None
                            ),
                            catalyst_category=str(value["catalyst_category"]),
                            float_category=str(value["float_category"]),
                            market_cap_category=str(value["market_cap_category"]),
                            market_regime=str(value["market_regime"]),
                            time_of_day=str(value["time_of_day"]),
                            gap_size_category=str(value["gap_size_category"]),
                            relative_volume_category=str(
                                value["relative_volume_category"]
                            ),
                            retail_attention_stage=str(
                                value["retail_attention_stage"]
                            ),
                            data_quality_score=float(value["data_quality_score"]),
                            label_policy_version=str(value["label_policy_version"]),
                            fill_policy_version=str(value["fill_policy_version"]),
                        )
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        f"invalid rows.jsonl line {line_number}: {error}"
                    ) from error
        feature_names = self._metadata["feature_names"]
        if not isinstance(feature_names, list):
            raise ValueError("feature_names must be an array")
        return ModelDataset(
            provider=self.name,
            dataset_kind=str(self._metadata["dataset_kind"]),
            fetched_at=parse_timestamp(str(self._metadata["fetched_at"])),
            feature_names=tuple(str(value) for value in feature_names),
            rows=tuple(rows),
            target_barrier_pct=float(self._metadata["target_barrier_pct"]),
            stop_barrier_pct=float(self._metadata["stop_barrier_pct"]),
            universe_survivorship_safe=bool(
                self._metadata["universe_survivorship_safe"]
            ),
            notes=tuple(str(value) for value in self._metadata.get("notes", [])),
        )
