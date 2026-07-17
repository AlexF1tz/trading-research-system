"""Command line entry point for the continuous Stage 3 monitor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .monitor import MonitorConfig, ShadowMonitor
from .provider import EndpointPolicy, ReplayShadowProvider
from .storage import ImmutableStore
from .synthetic import SyntheticShadowProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only shadow-data monitor; Ctrl+C stops it")
    parser.add_argument("--config", type=Path, default=Path("config/shadow_monitor.sample.json"))
    parser.add_argument("--max-cycles", type=int, help="finite test/demo run; omit for continuous monitoring")
    args = parser.parse_args()
    value = json.loads(args.config.read_text(encoding="utf-8"))
    mode = value.get("mode", "synthetic")
    if mode == "synthetic":
        provider = SyntheticShadowProvider()
    elif mode == "replay":
        provider = ReplayShadowProvider(Path(value["replay_path"]), loop=bool(value.get("replay_loop", False)))
    else:
        raise SystemExit("Only synthetic and cache/replay modes are enabled until approved live endpoint adapters are configured")
    monitor = ShadowMonitor(
        provider,
        ImmutableStore(Path(value["data_root"]), Path(value["prediction_root"])),
        config=MonitorConfig(**value.get("monitor", {})),
        endpoint_policy=EndpointPolicy(tuple(value.get("approved_news_domains", []))),
    )
    if args.max_cycles is None:
        monitor.run_forever()
    elif args.max_cycles < 1:
        raise SystemExit("--max-cycles must be positive")
    else:
        monitor.run_cycles(args.max_cycles)


if __name__ == "__main__":
    main()
