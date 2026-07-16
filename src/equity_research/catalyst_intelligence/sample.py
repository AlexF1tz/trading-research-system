"""Run the deterministic catalyst-intelligence engineering sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .pipeline import CatalystPipeline, CatalystPipelineConfig
from .provider import CatalystSourceProvider, JsonlCatalystProvider
from .rules import ClassifierConfig
from .synthetic import SyntheticCatalystFixtureProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run catalyst classification with explicit source provenance."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/catalyst.sample.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/catalyst_sample"),
    )
    return parser


def _config(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing catalyst config: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _provider(config: dict[str, object]) -> CatalystSourceProvider:
    provider_name = str(config.get("provider", "synthetic_fixture"))
    if provider_name == "synthetic_fixture":
        return SyntheticCatalystFixtureProvider()
    if provider_name == "jsonl_directory":
        provider_path = config.get("provider_path")
        if not provider_path:
            raise ValueError("jsonl_directory provider requires provider_path")
        return JsonlCatalystProvider(Path(str(provider_path)))
    raise ValueError(f"unsupported catalyst provider: {provider_name}")


def run_sample(output_dir: Path, config_path: Path) -> dict[str, object]:
    raw = _config(config_path)
    classifier = raw.get("classifier", {})
    if not isinstance(classifier, dict):
        raise ValueError("classifier configuration must be an object")
    result = CatalystPipeline(
        _provider(raw),
        CatalystPipelineConfig(
            classifier=ClassifierConfig(
                stale_after_hours=int(classifier.get("stale_after_hours", 24)),
                duplicate_similarity_threshold=float(
                    classifier.get("duplicate_similarity_threshold", 0.82)
                ),
                recent_category_days=int(
                    classifier.get("recent_category_days", 7)
                ),
            ),
            fail_on_quality_error=bool(raw.get("fail_on_quality_error", True)),
        ),
    ).run()
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in result.events:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
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
    is_fixture = "synthetic" in result.batch.dataset_kind.lower()
    categories: dict[str, int] = {}
    for event in result.events:
        categories[event.catalyst_category.value] = (
            categories.get(event.catalyst_category.value, 0) + 1
        )
    summary: dict[str, object] = {
        "status": (
            "ENGINEERING_FIXTURE_NOT_COMPANY_NEWS"
            if is_fixture
            else "SOURCE_DATA_VALIDATED_WITH_DECLARED_PROVENANCE"
        ),
        "provider": result.batch.provider,
        "dataset_kind": result.batch.dataset_kind,
        "documents": len(result.batch.documents),
        "events": len(result.events),
        "categories": categories,
        "primary_events": sum(
            event.source_tier.value == "primary" for event in result.events
        ),
        "unverified_events": sum(
            event.verification_status.value == "unverified"
            for event in result.events
        ),
        "stale_events": sum(event.is_stale for event in result.events),
        "repeated_events": sum(
            event.duplicate_of_event_id is not None for event in result.events
        ),
        "promotional_without_numbers": sum(
            "PROMOTIONAL_WITHOUT_MATERIAL_NUMBERS" in event.flags
            for event in result.events
        ),
        "quality_errors": result.error_count,
        "quality_warnings": result.warning_count,
        "coverage_notes": list(result.batch.notes),
        "output_files": {
            "events": str(events_path),
            "quality": str(quality_path),
        },
    }
    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(json.dumps(run_sample(args.output_dir, args.config), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

