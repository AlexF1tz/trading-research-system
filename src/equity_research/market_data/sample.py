"""Run the deterministic market-data engineering sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .features import FeatureConfig
from .pipeline import MarketDataPipeline, PipelineConfig
from .provider import CsvDirectoryProvider, MarketDataProvider, parse_timestamp
from .quality import QualityConfig
from .synthetic import SyntheticFixtureProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the synthetic market-data pipeline (not market evidence)."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/market_data.sample.json"),
        help="JSON pipeline configuration.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/market_data_sample"),
        help="Ignored output directory for JSONL and manifests.",
    )
    return parser


def _load_config(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing pipeline config: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _provider(config: dict[str, object]) -> MarketDataProvider:
    provider_name = str(config.get("provider", "synthetic_fixture"))
    if provider_name == "synthetic_fixture":
        return SyntheticFixtureProvider()
    if provider_name == "csv_directory":
        provider_path = config.get("provider_path")
        if not provider_path:
            raise ValueError("csv_directory provider requires provider_path")
        return CsvDirectoryProvider(Path(str(provider_path)))
    raise ValueError(f"unsupported provider: {provider_name}")


def run_sample(output_dir: Path, config_path: Path) -> dict[str, object]:
    raw_config = _load_config(config_path)
    provider = _provider(raw_config)
    quality_config = raw_config.get("quality", {})
    feature_config = raw_config.get("features", {})
    if not isinstance(quality_config, dict) or not isinstance(feature_config, dict):
        raise ValueError("quality and features configuration must be objects")
    pipeline = MarketDataPipeline(
        provider,
        PipelineConfig(
            quality=QualityConfig(
                run_as_of=parse_timestamp(
                    str(raw_config.get("run_as_of", "2026-07-16T00:00:00Z"))
                ),
                max_float_age_days=int(quality_config.get("max_float_age_days", 120)),
                split_tolerance=float(quality_config.get("split_tolerance", 0.12)),
            ),
            features=FeatureConfig(
                atr_period=int(feature_config.get("atr_period", 14)),
                realised_volatility_window=int(
                    feature_config.get("realised_volatility_window", 30)
                ),
                relative_return_window=int(
                    feature_config.get("relative_return_window", 5)
                ),
                index_benchmark_id=str(
                    feature_config.get("index_benchmark_id", "INDEX.SPY")
                ),
                sector_benchmarks={
                    str(key): str(value)
                    for key, value in dict(
                        feature_config.get(
                            "sector_benchmarks",
                            {
                                "Technology": "SECTOR.TECH",
                                "Healthcare": "SECTOR.HEALTH",
                            },
                        )
                    ).items()
                },
            ),
            fail_on_quality_error=bool(
                raw_config.get("fail_on_quality_error", True)
            ),
        ),
    )
    result = pipeline.run()
    output_dir.mkdir(parents=True, exist_ok=True)

    feature_path = output_dir / "features.jsonl"
    with feature_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in result.features:
            handle.write(json.dumps(row.to_dict(), sort_keys=True) + "\n")

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

    latest_by_security: dict[str, dict[str, object]] = {}
    for row in result.features:
        latest_by_security[row.security_id] = row.to_dict()
    is_fixture = "synthetic" in result.dataset.coverage.dataset_kind.lower()
    summary: dict[str, object] = {
        "status": (
            "ENGINEERING_FIXTURE_NOT_EMPIRICAL_EVIDENCE"
            if is_fixture
            else "PROVIDER_DATA_VALIDATED_WITH_DECLARED_COVERAGE"
        ),
        "provider": result.dataset.coverage.provider,
        "dataset_kind": result.dataset.coverage.dataset_kind,
        "one_minute_bars": len(result.dataset.one_minute_bars),
        "daily_bars": len(result.dataset.daily_bars),
        "instruments": len(result.dataset.instruments),
        "delisted_instruments": sum(
            instrument.is_delisted for instrument in result.dataset.instruments
        ),
        "corporate_actions": len(result.dataset.corporate_actions),
        "halts": len(result.dataset.halts),
        "features": len(result.features),
        "quality_errors": result.error_count,
        "quality_warnings": result.warning_count,
        "coverage_notes": list(result.dataset.coverage.notes),
        "latest_features": latest_by_security,
        "output_files": {
            "features": str(feature_path),
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
    summary = run_sample(args.output_dir, args.config)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
