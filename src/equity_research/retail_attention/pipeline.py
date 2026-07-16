"""Permission-gated attention validation and scoring orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import AttentionSignal, SourceBatch
from .provider import AttentionSourceProvider
from .quality import (
    AttentionQualityError,
    AttentionQualityIssue,
    Severity,
    run_attention_quality_checks,
)
from .scoring import AttentionScoringConfig, RuleBasedAttentionScorer


@dataclass(frozen=True, slots=True)
class AttentionPipelineConfig:
    scoring: AttentionScoringConfig
    fail_on_quality_error: bool = True


@dataclass(frozen=True, slots=True)
class AttentionPipelineResult:
    batch: SourceBatch
    signals: tuple[AttentionSignal, ...]
    quality_issues: tuple[AttentionQualityIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.severity is Severity.ERROR for issue in self.quality_issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity is Severity.WARNING for issue in self.quality_issues)


class AttentionPipeline:
    def __init__(
        self,
        provider: AttentionSourceProvider,
        config: AttentionPipelineConfig,
    ) -> None:
        self._provider = provider
        self._config = config

    def run(self) -> AttentionPipelineResult:
        batch = self._provider.load()
        issues = run_attention_quality_checks(batch)
        errors = tuple(issue for issue in issues if issue.severity is Severity.ERROR)
        if errors and self._config.fail_on_quality_error:
            raise AttentionQualityError(errors)
        if self._config.scoring.as_of > batch.fetched_at:
            raise ValueError("attention as_of cannot be later than batch fetched_at")
        signals = RuleBasedAttentionScorer(self._config.scoring).score(batch)
        return AttentionPipelineResult(
            batch=batch,
            signals=signals,
            quality_issues=issues,
        )
