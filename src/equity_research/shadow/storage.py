"""Append-only filesystem persistence for shadow records."""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import canonical_hash, json_safe


class ImmutableStore:
    def __init__(self, data_root: Path, prediction_root: Path) -> None:
        self.data_root = data_root
        self.prediction_root = prediction_root

    def write_raw(self, family: str, source_id: str, value: object) -> bool:
        digest = str(value.get("payload_sha256")) if isinstance(value, dict) and value.get("payload_sha256") else canonical_hash(value)
        path = self.data_root / "raw" / family / f"{source_id}-{digest}.json"
        # A repeated fetch of identical source-native bytes is a duplicate even
        # though its processing timestamp is later. Preserve the first capture.
        if path.exists():
            return False
        return self._write(path, value)

    def write_normalized(self, kind: str, record_id: str, value: object) -> bool:
        return self._write(self.data_root / "normalized" / kind / f"{record_id}.json", value)

    def has_normalized(self, kind: str, record_id: str) -> bool:
        return (self.data_root / "normalized" / kind / f"{record_id}.json").exists()

    def write_alert(self, alert_id: str, value: object) -> bool:
        return self._write(self.prediction_root / "alerts" / f"{alert_id}.json", value)

    def has_alert(self, alert_id: str) -> bool:
        return (self.prediction_root / "alerts" / f"{alert_id}.json").exists()

    def write_outcome(self, outcome_id: str, value: object) -> bool:
        return self._write(self.prediction_root / "outcomes" / f"{outcome_id}.json", value)

    def write_heartbeat(self, heartbeat_id: str, value: object) -> bool:
        return self._write(self.prediction_root / "heartbeats" / f"{heartbeat_id}.json", value)

    def alert_records(self) -> list[dict[str, object]]:
        root = self.prediction_root / "alerts"
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*.json"))] if root.exists() else []

    def market_records(self, security_id: str) -> list[dict[str, object]]:
        root = self.data_root / "normalized" / "market"
        if not root.exists():
            return []
        records = [json.loads(path.read_text(encoding="utf-8")) for path in root.glob("*.json")]
        return sorted((r for r in records if r["security_id"] == security_id), key=lambda r: str(r["source_timestamp"]))

    @staticmethod
    def _write(path: Path, value: object) -> bool:
        payload = json.dumps(json_safe(value), indent=2, sort_keys=True) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_text(encoding="utf-8") != payload:
                raise RuntimeError(f"immutable record collision: {path}")
            return False
        path.write_text(payload, encoding="utf-8")
        return True
