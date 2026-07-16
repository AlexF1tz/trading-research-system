"""Contracts for independent validation findings and dispositions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AuditStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    NOT_VERIFIABLE = "not_verifiable"


class FindingSeverity(str, Enum):
    BLOCKER = "blocker"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    check_id: str
    title: str
    status: AuditStatus
    severity: FindingSeverity
    evidence: tuple[str, ...]
    impact: str
    recommendation: str
    automated_test: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "status": self.status.value,
            "severity": self.severity.value,
            "evidence": list(self.evidence),
            "impact": self.impact,
            "recommendation": self.recommendation,
            "automated_test": self.automated_test,
        }
