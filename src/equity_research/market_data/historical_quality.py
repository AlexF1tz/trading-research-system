"""Credential-gated real historical ingestion and quality-only reporting CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping, Sequence

from .alpaca import (
    ALPACA_BARS_URL,
    AlpacaConfigurationError,
    AlpacaCredentials,
    AlpacaHistoricalConfig,
    AlpacaHistoricalProvider,
    AlpacaRequestError,
    ConfiguredSecurity,
    ReadOnlyHttpTransport,
)
from .calendar import UsEquityCalendar
from .contracts import Exchange, ProviderDataset, Session
from .provider import parse_timestamp
from .quality import QualityConfig, QualityIssue, Severity, run_quality_checks


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a bounded real Alpaca historical sample and run data-quality "
            "checks only. No model training or trade execution."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/alpaca_historical.sample.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/alpaca_historical_quality"),
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    return parser


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing historical data config: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AlpacaConfigurationError("historical config must be a JSON object")
    forbidden = {
        "api_key",
        "api_secret",
        "key_id",
        "secret_key",
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
    }
    present = forbidden.intersection(value)
    if present:
        raise AlpacaConfigurationError(
            "credentials must come from environment variables, not config: "
            + ", ".join(sorted(present))
        )
    return value


def load_historical_config(path: Path, repo_root: Path) -> AlpacaHistoricalConfig:
    raw = _load_json(path)
    if raw.get("provider") != "alpaca_historical_bars":
        raise AlpacaConfigurationError(
            "provider must be alpaca_historical_bars for this command"
        )
    universe_raw = raw.get("universe")
    if not isinstance(universe_raw, list):
        raise AlpacaConfigurationError("universe must be a JSON array")
    universe: list[ConfiguredSecurity] = []
    for value in universe_raw:
        if not isinstance(value, dict):
            raise AlpacaConfigurationError("each universe item must be an object")
        try:
            universe.append(
                ConfiguredSecurity(
                    security_id=str(value["security_id"]),
                    ticker=str(value["ticker"]),
                    exchange=Exchange(str(value["exchange"])),
                )
            )
        except (KeyError, ValueError) as exc:
            raise AlpacaConfigurationError(
                "universe entries require security_id, ticker, and NASDAQ/NYSE exchange"
            ) from exc
    minute = raw.get("minute_range")
    daily = raw.get("daily_range")
    if not isinstance(minute, dict) or not isinstance(daily, dict):
        raise AlpacaConfigurationError(
            "minute_range and daily_range must be objects"
        )
    raw_root = Path(str(raw.get("raw_root", "data/raw/alpaca")))
    if raw_root.is_absolute() or ".." in raw_root.parts:
        raise AlpacaConfigurationError(
            "raw_root must be a repository-relative path without parent traversal"
        )
    root = repo_root.resolve()
    resolved_raw_root = (root / raw_root).resolve()
    try:
        resolved_raw_root.relative_to(root)
    except ValueError as exc:
        raise AlpacaConfigurationError("raw_root escapes the repository") from exc
    sessions_raw = raw.get("included_sessions", ["regular"])
    dates_raw = minute.get("session_dates")
    if not isinstance(sessions_raw, list) or not isinstance(dates_raw, list):
        raise AlpacaConfigurationError(
            "included_sessions and minute_range.session_dates must be arrays"
        )
    rate = raw.get("rate_limit", {})
    if not isinstance(rate, dict):
        raise AlpacaConfigurationError("rate_limit must be an object")
    try:
        return AlpacaHistoricalConfig(
            universe=tuple(universe),
            minute_start=parse_timestamp(str(minute["start"])),
            minute_end=parse_timestamp(str(minute["end"])),
            daily_start=parse_timestamp(str(daily["start"])),
            daily_end=parse_timestamp(str(daily["end"])),
            minute_session_dates=tuple(date.fromisoformat(str(value)) for value in dates_raw),
            included_sessions=tuple(Session(str(value)) for value in sessions_raw),
            feed=str(raw.get("feed", "iex")),
            adjustment=str(raw.get("adjustment", "raw")),
            page_limit=int(raw.get("page_limit", 10_000)),
            max_pages_per_timeframe=int(raw.get("max_pages_per_timeframe", 100)),
            timeout_seconds=float(raw.get("timeout_seconds", 30.0)),
            minimum_request_interval_seconds=float(
                rate.get("minimum_request_interval_seconds", 0.35)
            ),
            max_attempts=int(rate.get("max_attempts", 5)),
            max_retry_delay_seconds=float(
                rate.get("max_retry_delay_seconds", 30.0)
            ),
            minimum_historical_lag_minutes=int(
                raw.get("minimum_historical_lag_minutes", 15)
            ),
            raw_root=resolved_raw_root,
            license_class=str(
                raw.get(
                    "license_class",
                    "alpaca_personal_noncommercial_research_review_required",
                )
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AlpacaConfigurationError(
            "historical config contains a missing or invalid typed value"
        ) from exc


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def _write_once(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite immutable run artifact: {path}") from exc
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: object) -> str:
    return _write_once(
        path,
        (json.dumps(_json_safe(value), indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def _write_jsonl(path: Path, values: Sequence[object]) -> str:
    content = "".join(
        json.dumps(_json_safe(value), sort_keys=True) + "\n" for value in values
    ).encode("utf-8")
    return _write_once(path, content)


def _reconciliation_issues(provider: AlpacaHistoricalProvider) -> tuple[QualityIssue, ...]:
    if provider.audit.reconciled:
        return ()
    return (
        QualityIssue(
            code="RAW_NORMALIZED_COUNT_MISMATCH",
            severity=Severity.ERROR,
            message="raw bar counts do not reconcile with normalized plus explicitly dropped bars",
        ),
    )


def _run_directory(output_root: Path, retrieved_at: datetime) -> Path:
    stamp = retrieved_at.strftime("%Y%m%dT%H%M%S%fZ")
    return output_root / f"run-{stamp}"


def write_quality_artifacts(
    output_root: Path,
    dataset: ProviderDataset,
    provider: AlpacaHistoricalProvider,
    issues: tuple[QualityIssue, ...],
) -> dict[str, object]:
    run_dir = _run_directory(output_root, dataset.coverage.retrieved_at)
    normalized = run_dir / "normalized"
    files: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for name, path, values in (
        ("instruments", normalized / "instruments.jsonl", dataset.instruments),
        ("one_minute_bars", normalized / "bars_1m.jsonl", dataset.one_minute_bars),
        ("daily_bars", normalized / "bars_1d.jsonl", dataset.daily_bars),
    ):
        hashes[name] = _write_jsonl(path, values)
        files[name] = str(path)
    coverage_path = normalized / "coverage.json"
    hashes["coverage"] = _write_json(coverage_path, dataset.coverage)
    files["coverage"] = str(coverage_path)
    audit_path = run_dir / "ingestion_audit.json"
    hashes["ingestion_audit"] = _write_json(audit_path, provider.audit.to_dict())
    files["ingestion_audit"] = str(audit_path)
    quality_path = run_dir / "quality_report.json"
    hashes["quality_report"] = _write_json(
        quality_path, [value.to_dict() for value in issues]
    )
    files["quality_report"] = str(quality_path)
    errors = sum(value.severity is Severity.ERROR for value in issues)
    warnings = sum(value.severity is Severity.WARNING for value in issues)
    summary: dict[str, object] = {
        "schema_version": "real-historical-quality-run-v1",
        "status": (
            "REAL_HISTORICAL_DATA_QUALITY_ERRORS"
            if errors
            else "REAL_HISTORICAL_DATA_VALIDATED_WITH_DECLARED_LIMITATIONS"
        ),
        "read_only_research_only": True,
        "trade_or_account_endpoints_used": False,
        "allowed_endpoint": ALPACA_BARS_URL,
        "training_performed": False,
        "predictions_generated": False,
        "profitability_claimed": False,
        "provider": dataset.coverage.provider,
        "dataset_kind": dataset.coverage.dataset_kind,
        "retrieved_at": dataset.coverage.retrieved_at,
        "instruments": len(dataset.instruments),
        "one_minute_bars": len(dataset.one_minute_bars),
        "daily_bars": len(dataset.daily_bars),
        "corporate_actions": len(dataset.corporate_actions),
        "halts": len(dataset.halts),
        "quality_errors": errors,
        "quality_warnings": warnings,
        "raw_normalized_counts_reconciled": provider.audit.reconciled,
        "coverage_notes": list(dataset.coverage.notes),
        "normalized_file_sha256": hashes,
        "output_files": files,
        "run_directory": str(run_dir),
    }
    manifest_path = run_dir / "run_manifest.json"
    manifest_hash = _write_json(manifest_path, summary)
    summary["output_files"]["run_manifest"] = str(manifest_path)  # type: ignore[index]
    summary["run_manifest_sha256"] = manifest_hash
    return summary


def run_historical_quality_check(
    config_path: Path,
    output_root: Path,
    repo_root: Path,
    *,
    environment: Mapping[str, str] | None = None,
    transport: ReadOnlyHttpTransport | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, object]:
    config = load_historical_config(config_path, repo_root)
    credentials = AlpacaCredentials.from_environment(environment)
    provider = AlpacaHistoricalProvider(
        config,
        credentials,
        transport=transport,
        clock=clock,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    dataset = provider.load()
    quality_raw = _load_json(config_path).get("quality", {})
    if not isinstance(quality_raw, dict):
        raise AlpacaConfigurationError("quality must be an object")
    issues = run_quality_checks(
        dataset,
        UsEquityCalendar(),
        QualityConfig(
            run_as_of=dataset.coverage.retrieved_at,
            max_float_age_days=int(quality_raw.get("max_float_age_days", 120)),
            split_tolerance=float(quality_raw.get("split_tolerance", 0.12)),
        ),
    ) + _reconciliation_issues(provider)
    return write_quality_artifacts(output_root, dataset, provider, issues)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = run_historical_quality_check(
            args.config, args.output_dir, args.repo_root
        )
    except (
        AlpacaConfigurationError,
        AlpacaRequestError,
        FileNotFoundError,
        RuntimeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "REAL_HISTORICAL_QUALITY_RUN_FAILED_CLOSED",
                    "error": str(exc),
                    "training_performed": False,
                    "profitability_claimed": False,
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(_json_safe(summary), indent=2, sort_keys=True))
    return 2 if int(summary["quality_errors"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
