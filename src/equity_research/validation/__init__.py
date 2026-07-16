"""Independent metric reproduction and adversarial model validation."""

from .audit import IndependentModelValidator, ValidationConfig, ValidationResult
from .contracts import AuditStatus, ValidationFinding

__all__ = [
    "AuditStatus",
    "IndependentModelValidator",
    "ValidationConfig",
    "ValidationFinding",
    "ValidationResult",
]
"""Independent validation and red-team controls."""

from .audit import (
    IndependentModelValidator,
    ValidationConfig,
    ValidationResult,
    scan_for_random_time_splits,
)
from .contracts import AuditStatus, FindingSeverity, ValidationFinding

__all__ = [
    "AuditStatus",
    "FindingSeverity",
    "IndependentModelValidator",
    "ValidationConfig",
    "ValidationFinding",
    "ValidationResult",
    "scan_for_random_time_splits",
]
