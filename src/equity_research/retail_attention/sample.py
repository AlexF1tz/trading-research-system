"""Run the deterministic, non-recommendation retail-attention sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .pipeline import AttentionPipeline, AttentionPipelineConfig
from .provider import AttentionSourceProvider, JsonlAttentionProvider, parse_timestamp
from .scoring import AttentionScoringConfig
from .synthetic import SyntheticAttentionFixtureProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure point-in-time retail attention; never recommend trades."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/retail_attention.sample.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/retail_attention_sample"),
    )
    return parser


def _load_config(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing attention config: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _provider(config: dict[str, object]) -> AttentionSourceProvider:
    provider_name = str(config.get("provider", "synthetic_fixture"))
    if provider_name == "synthetic_fixture":
        return SyntheticAttentionFixtureProvider()
    if provider_name == "jsonl_directory":
        provider_path = config.get("provider_path")
        if not provider_path:
            raise ValueError("jsonl_directory provider requires provider_path")
        return JsonlAttentionProvider(Path(str(provider_path)))
    raise ValueError(f"unsupported attention provider: {provider_name}")


def run_sample(output_dir: Path, config_path: Path) -> dict[str, object]:
    raw = _load_config(config_path)
    scoring = raw.get("scoring")
    if not isinstance(scoring, dict):
        raise ValueError("attention scoring configuration must be an object")
    as_of = scoring.get("as_of")
    if as_of is None:
        raise ValueError("attention scoring requires an explicit UTC as_of")
    result = AttentionPipeline(
        _provider(raw),
        AttentionPipelineConfig(
            scoring=AttentionScoringConfig(
                as_of=parse_timestamp(str(as_of)),
                interval_minutes=int(scoring.get("interval_minutes", 15)),
                baseline_intervals=int(scoring.get("baseline_intervals", 8)),
                minimum_complete_baseline_intervals=int(
                    scoring.get("minimum_complete_baseline_intervals", 4)
                ),
                minimum_metric_coverage=float(
                    scoring.get("minimum_metric_coverage", 0.5)
                ),
                duplicate_similarity_threshold=float(
                    scoring.get("duplicate_similarity_threshold", 0.82)
                ),
                coordination_lookback_intervals=int(
                    scoring.get("coordination_lookback_intervals", 4)
                ),
                crowded_mention_count=int(
                    scoring.get("crowded_mention_count", 12)
                ),
                crowded_baseline_score=float(
                    scoring.get("crowded_baseline_score", 85.0)
                ),
                expanding_baseline_score=float(
                    scoring.get("expanding_baseline_score", 65.0)
                ),
                collapsing_previous_count=int(
                    scoring.get("collapsing_previous_count", 4)
                ),
                collapsing_ratio=float(scoring.get("collapsing_ratio", 0.5)),
                high_engagement_interactions=int(
                    scoring.get("high_engagement_interactions", 25)
                ),
                large_observed_move_pct=float(
                    scoring.get("large_observed_move_pct", 10.0)
                ),
                late_discovery_minutes=int(
                    scoring.get("late_discovery_minutes", 30)
                ),
                maximum_supporting_links=int(
                    scoring.get("maximum_supporting_links", 10)
                ),
            ),
            fail_on_quality_error=bool(raw.get("fail_on_quality_error", True)),
        ),
    ).run()

    output_dir.mkdir(parents=True, exist_ok=True)
    signals_path = output_dir / "attention_signals.jsonl"
    with signals_path.open("w", encoding="utf-8", newline="\n") as handle:
        for signal in result.signals:
            handle.write(json.dumps(signal.to_dict(), sort_keys=True) + "\n")
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
    fixture = "synthetic" in result.batch.dataset_kind.lower()
    stages: dict[str, int] = {}
    for signal in result.signals:
        stages[signal.attention_stage.value] = (
            stages.get(signal.attention_stage.value, 0) + 1
        )
    summary: dict[str, object] = {
        "status": (
            "ENGINEERING_FIXTURE_NOT_SOCIAL_OR_MARKET_DATA"
            if fixture
            else "AUTHORIZED_NORMALIZED_SOURCE_EXPORT_MEASURED"
        ),
        "interpretation": "attention_measurement_only_not_a_trade_recommendation",
        "provider": result.batch.provider,
        "dataset_kind": result.batch.dataset_kind,
        "monitored_tickers": len(result.batch.monitored_securities),
        "mentions": len(result.batch.mentions),
        "signals": len(result.signals),
        "stages": stages,
        "quality_errors": result.error_count,
        "quality_warnings": result.warning_count,
        "coverage_notes": list(result.batch.notes),
        "output_files": {
            "signals": str(signals_path),
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
