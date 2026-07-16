"""Run the independent audit against the chronological modelling sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from equity_research.modelling.contracts import ChronologicalSplitConfig
from equity_research.modelling.provider import parse_timestamp
from equity_research.modelling.sample import (
    build_modelling_pipeline,
    load_modelling_config,
)

from .audit import IndependentModelValidator, ValidationConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Independently reproduce and red-team modelling results."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config/validation.sample.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/validation_sample")
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    return parser


def _load(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing validation config: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("validation config must be an object")
    return value


def run_validation_sample(
    output_dir: Path, config_path: Path, repo_root: Path
) -> dict[str, object]:
    raw = _load(config_path)
    modelling_config_path = Path(
        str(raw.get("modelling_config", "config/modelling.sample.json"))
    )
    modelling_raw = load_modelling_config(modelling_config_path)
    split = modelling_raw.get("splits")
    evaluation = modelling_raw.get("evaluation", {})
    if not isinstance(split, dict) or not isinstance(evaluation, dict):
        raise ValueError("modelling split and evaluation configs must be objects")
    model_result = build_modelling_pipeline(modelling_raw).run()
    validator = IndependentModelValidator(
        ValidationConfig(
            splits=ChronologicalSplitConfig(
                train_end=parse_timestamp(str(split["train_end"])),
                calibration_start=parse_timestamp(str(split["calibration_start"])),
                calibration_end=parse_timestamp(str(split["calibration_end"])),
                final_test_start=parse_timestamp(str(split["final_test_start"])),
                final_test_end=parse_timestamp(str(split["final_test_end"])),
            ),
            calibration_bins=int(evaluation.get("calibration_bins", 10)),
            bootstrap_repetitions=int(
                evaluation.get("bootstrap_repetitions", 200)
            ),
            bootstrap_seed=int(evaluation.get("bootstrap_seed", 17)),
            cost_multipliers=tuple(
                float(value)
                for value in raw.get("cost_multipliers", [1.0, 1.5, 2.0])
            ),
            top_k=int(raw.get("top_k", 10)),
            concentration_threshold=float(
                raw.get("concentration_threshold", 0.30)
            ),
        )
    )
    validation = validator.validate(model_result, repo_root=repo_root.resolve())
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "validation_report.json"
    markdown_path = output_dir / "VALIDATION_REPORT.md"
    json_path.write_text(
        json.dumps(validation.report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(validation.markdown, encoding="utf-8")
    summary: dict[str, object] = {
        "disposition": validation.report["disposition"],
        "dataset_kind": model_result.dataset.dataset_kind,
        "models_reviewed": len(validation.report["model_decisions"]),
        "blockers": validation.report["blockers"],
        "summary_counts": validation.report["summary_counts"],
        "metric_reproduction": validation.report["metric_reproduction"],
        "profitability_claimed": False,
        "output_files": {
            "json": str(json_path),
            "markdown": str(markdown_path),
        },
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(
        json.dumps(
            run_validation_sample(args.output_dir, args.config, args.repo_root),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
