"""Market-data pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field

from .calendar import UsEquityCalendar
from .contracts import FeatureRow, ProviderDataset
from .features import FeatureConfig, compute_features
from .provider import MarketDataProvider
from .quality import (
    DataQualityError,
    QualityConfig,
    QualityIssue,
    Severity,
    run_quality_checks,
)


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    quality: QualityConfig
    features: FeatureConfig = field(default_factory=FeatureConfig)
    fail_on_quality_error: bool = True


@dataclass(frozen=True, slots=True)
class PipelineResult:
    dataset: ProviderDataset
    features: tuple[FeatureRow, ...]
    quality_issues: tuple[QualityIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.severity is Severity.ERROR for issue in self.quality_issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity is Severity.WARNING for issue in self.quality_issues)


class MarketDataPipeline:
    def __init__(
        self,
        provider: MarketDataProvider,
        config: PipelineConfig,
        calendar: UsEquityCalendar | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._calendar = calendar or UsEquityCalendar()

    def run(self) -> PipelineResult:
        dataset = self._provider.load()
        issues = run_quality_checks(dataset, self._calendar, self._config.quality)
        errors = tuple(issue for issue in issues if issue.severity is Severity.ERROR)
        if errors and self._config.fail_on_quality_error:
            raise DataQualityError(errors)
        features = compute_features(
            dataset.one_minute_bars,
            dataset.daily_bars,
            dataset.instruments,
            dataset.corporate_actions,
            self._calendar,
            self._config.features,
        )
        return PipelineResult(dataset=dataset, features=features, quality_issues=issues)

