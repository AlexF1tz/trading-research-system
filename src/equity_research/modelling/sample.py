"""Run the chronological modelling engineering sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .contracts import ChronologicalSplitConfig
from .pipeline import ModellingPipeline, ModellingPipelineConfig
from .provider import JsonlModelDatasetProvider, ModelDatasetProvider, parse_timestamp
from .splitting import WalkForwardConfig
from .synthetic import SyntheticModelFixtureProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare chronological calibrated models without trade execution."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/modelling.sample.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/modelling_sample"),
    )
    return parser


def load_modelling_config(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing modelling config: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def provider_from_config(config: dict[str, object]) -> ModelDatasetProvider:
    name = str(config.get("provider", "synthetic_fixture"))
    if name == "synthetic_fixture":
        return SyntheticModelFixtureProvider()
    if name == "jsonl_directory":
        provider_path = config.get("provider_path")
        if not provider_path:
            raise ValueError("jsonl_directory provider requires provider_path")
        return JsonlModelDatasetProvider(Path(str(provider_path)))
    raise ValueError(f"unsupported modelling provider: {name}")


def build_modelling_pipeline(raw: dict[str, object]) -> ModellingPipeline:
    split = raw.get("splits")
    walk = raw.get("walk_forward", {})
    evaluation = raw.get("evaluation", {})
    if not isinstance(split, dict):
        raise ValueError("modelling splits configuration must be an object")
    if not isinstance(walk, dict) or not isinstance(evaluation, dict):
        raise ValueError("walk_forward and evaluation must be objects")
    required = (
        "train_end",
        "calibration_start",
        "calibration_end",
        "final_test_start",
        "final_test_end",
    )
    if any(name not in split for name in required):
        raise ValueError("all explicit chronological split cutoffs are required")
    return ModellingPipeline(
        provider_from_config(raw),
        ModellingPipelineConfig(
            splits=ChronologicalSplitConfig(
                train_end=parse_timestamp(str(split["train_end"])),
                calibration_start=parse_timestamp(str(split["calibration_start"])),
                calibration_end=parse_timestamp(str(split["calibration_end"])),
                final_test_start=parse_timestamp(str(split["final_test_start"])),
                final_test_end=parse_timestamp(str(split["final_test_end"])),
            ),
            walk_forward=WalkForwardConfig(
                minimum_train_rows=int(walk.get("minimum_train_rows", 30)),
                validation_rows=int(walk.get("validation_rows", 10)),
                step_rows=int(walk.get("step_rows", 10)),
            ),
            calibration_bins=int(evaluation.get("calibration_bins", 10)),
            bootstrap_repetitions=int(
                evaluation.get("bootstrap_repetitions", 200)
            ),
            bootstrap_seed=int(evaluation.get("bootstrap_seed", 17)),
            classification_simplicity_tolerance=float(
                evaluation.get("classification_simplicity_tolerance", 0.005)
            ),
            regression_simplicity_tolerance=float(
                evaluation.get("regression_simplicity_tolerance", 0.05)
            ),
            overfit_brier_gap=float(evaluation.get("overfit_brier_gap", 0.03)),
            overfit_mae_gap=float(evaluation.get("overfit_mae_gap", 0.50)),
            allow_limited_universe=bool(raw.get("allow_limited_universe", False)),
            fail_on_quality_error=bool(raw.get("fail_on_quality_error", True)),
        ),
    )


def run_sample(output_dir: Path, config_path: Path) -> dict[str, object]:
    raw = load_modelling_config(config_path)
    result = build_modelling_pipeline(raw).run()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "modelling_report.json"
    report_path.write_text(
        json.dumps(result.report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    predictions_path = output_dir / "final_test_evaluation_predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8", newline="\n") as handle:
        for prediction in result.predictions:
            handle.write(json.dumps(prediction.to_dict(), sort_keys=True) + "\n")
    quality_path = output_dir / "quality_report.json"
    quality_path.write_text(
        json.dumps(
            [issue.to_dict() for issue in result.quality_issues],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    classification = result.report["classification"]
    regression = result.report["regression"]
    assert isinstance(classification, dict) and isinstance(regression, dict)
    summary: dict[str, object] = {
        "status": result.report["status"],
        "dataset_kind": result.dataset.dataset_kind,
        "rows": len(result.dataset.rows),
        "quality_errors": result.error_count,
        "quality_warnings": result.warning_count,
        "classification_targets": len(classification),
        "regression_targets": len(regression),
        "selected_models": {
            target: value["selected_model_from_walk_forward_only"]
            for target, value in {**classification, **regression}.items()
        },
        "final_test_used_for_model_selection": False,
        "profitability_claimed": False,
        "output_files": {
            "report": str(report_path),
            "evaluation_predictions": str(predictions_path),
            "quality": str(quality_path),
        },
    }
    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(json.dumps(run_sample(args.output_dir, args.config), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
