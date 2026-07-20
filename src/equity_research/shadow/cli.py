"""Command line entry point for the continuous Stage 3 monitor."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .monitor import MonitorConfig, ShadowMonitor
from .provider import (
    AlpacaLiveMarketProvider, CompositeShadowProvider, EndpointPolicy,
    ReplayShadowProvider, SecEdgarProvider,
)
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
    elif mode in {"sec", "sec_alpaca"}:
        sec_provider = SecEdgarProvider(
            dict(value.get("cik_to_ticker", {})), value.get("sec_user_agent"),
            state_path=Path(value["sec_state_path"]) if value.get("sec_state_path") else None,
            minimum_request_interval_seconds=float(value.get("sec_minimum_request_interval_seconds", 0.11)),
        )
        if mode == "sec":
            provider = sec_provider
        else:
            market_provider = AlpacaLiveMarketProvider(
                dict(value.get("alpaca_symbols", {})),
                os.environ.get("ALPACA_API_KEY_ID", ""),
                os.environ.get("ALPACA_API_SECRET_KEY", ""),
                feed=str(value.get("alpaca_feed", "iex")),
                delayed_seconds=int(value.get("alpaca_delayed_seconds", 0)),
            )
            provider = CompositeShadowProvider((sec_provider, market_provider))
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
