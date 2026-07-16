"""Catalyst-source validation and deterministic classification orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import CatalystEvent, SourceBatch
from .provider import CatalystSourceProvider
from .quality import (
    CatalystQualityError,
    CatalystQualityIssue,
    Severity,
    run_source_quality_checks,
)
from .rules import ClassifierConfig, RuleBasedCatalystClassifier


@dataclass(frozen=True, slots=True)
class CatalystPipelineConfig:
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    fail_on_quality_error: bool = True


@dataclass(frozen=True, slots=True)
class CatalystPipelineResult:
    batch: SourceBatch
    events: tuple[CatalystEvent, ...]
    quality_issues: tuple[CatalystQualityIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.severity is Severity.ERROR for issue in self.quality_issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity is Severity.WARNING for issue in self.quality_issues)


class CatalystPipeline:
    def __init__(
        self,
        provider: CatalystSourceProvider,
        config: CatalystPipelineConfig | None = None,
    ) -> None:
        self._provider = provider
        self._config = config or CatalystPipelineConfig()

    def run(self) -> CatalystPipelineResult:
        batch = self._provider.load()
        issues = run_source_quality_checks(batch)
        errors = tuple(issue for issue in issues if issue.severity is Severity.ERROR)
        if errors and self._config.fail_on_quality_error:
            raise CatalystQualityError(errors)
        classifier = RuleBasedCatalystClassifier(self._config.classifier)
        events = classifier.classify(batch.documents)
        return CatalystPipelineResult(
            batch=batch,
            events=events,
            quality_issues=issues,
        )

