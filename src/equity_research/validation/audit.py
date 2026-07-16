"""Independent red-team audit of modelling artifacts and research controls."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from equity_research.modelling.contracts import (
    BINARY_TARGETS,
    REGRESSION_TARGETS,
    BinaryTarget,
    ChronologicalSplitConfig,
    ModelRow,
    PredictionValue,
    SplitName,
)
from equity_research.modelling.pipeline import ModellingPipelineResult

from .contracts import AuditStatus, FindingSeverity, ValidationFinding
from .independent_metrics import (
    MetricReproductionResult,
    cost_stress_summary,
    reproduce_report_metrics,
    security_concentration_summary,
)


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Parameters controlled by the validator, not the model selector."""

    splits: ChronologicalSplitConfig
    calibration_bins: int = 10
    bootstrap_repetitions: int = 100
    bootstrap_seed: int = 17
    cost_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0)
    top_k: int = 10
    concentration_threshold: float = 0.30


@dataclass(frozen=True, slots=True)
class ValidationResult:
    report: dict[str, object]
    markdown: str
    findings: tuple[ValidationFinding, ...]

    @property
    def rejected(self) -> bool:
        return str(self.report["disposition"]).startswith("REJECT")


def scan_for_random_time_splits(repo_root: Path) -> tuple[str, ...]:
    """AST-scan application code for common random split operations."""

    findings: list[str] = []
    source_root = repo_root / "src"
    if not source_root.exists():
        return ("src directory not found",)
    for path in sorted(source_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            findings.append(f"could not inspect {path.relative_to(repo_root)}: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            if isinstance(called, ast.Name) and called.id in {
                "train_test_split",
                "random_split",
            }:
                findings.append(
                    f"{path.relative_to(repo_root)}:{node.lineno} calls {called.id}"
                )
            if isinstance(called, ast.Attribute) and called.attr in {
                "shuffle",
                "permutation",
                "random_split",
            }:
                findings.append(
                    f"{path.relative_to(repo_root)}:{node.lineno} calls .{called.attr}"
                )
    return tuple(findings)


def _finding(
    check_id: str,
    title: str,
    status: AuditStatus,
    severity: FindingSeverity,
    evidence: Iterable[str],
    impact: str,
    recommendation: str,
    automated_test: str | None = None,
) -> ValidationFinding:
    return ValidationFinding(
        check_id=check_id,
        title=title,
        status=status,
        severity=severity,
        evidence=tuple(evidence),
        impact=impact,
        recommendation=recommendation,
        automated_test=automated_test,
    )


def _final_rows(
    rows: tuple[ModelRow, ...], splits: ChronologicalSplitConfig
) -> tuple[ModelRow, ...]:
    return tuple(
        sorted(
            (
                row
                for row in rows
                if splits.split_for(row.prediction_as_of) is SplitName.FINAL_TEST
            ),
            key=lambda row: (row.prediction_as_of, row.observation_id),
        )
    )


def _lookup(
    predictions: tuple[PredictionValue, ...], target: str, model_name: str
) -> dict[str, float]:
    return {
        prediction.observation_id: prediction.value
        for prediction in predictions
        if prediction.target == target and prediction.model_name == model_name
    }


def _metric_finding(result: MetricReproductionResult) -> ValidationFinding:
    if result.passed:
        return _finding(
            "METRIC_REPRODUCTION",
            "Independent metric reproduction",
            AuditStatus.PASS,
            FindingSeverity.BLOCKER,
            (
                f"Recomputed {result.checks} scalar, calibration, ranking, bootstrap, and breakdown values.",
                "The validator does not import equity_research.modelling.evaluation.",
                "Every independently recomputed final-test value matched within 1.1e-6.",
            ),
            "No metric discrepancy was detected in the supplied fixture artifacts.",
            "Keep the independent implementation separate and run it on every candidate report.",
            "test_independent_reproduction_matches_every_reported_metric",
        )
    examples = tuple(
        f"{item['path']}: reproduced={item['reproduced']!r}, reported={item['reported']!r}"
        for item in result.mismatches[:5]
    )
    return _finding(
        "METRIC_REPRODUCTION",
        "Independent metric reproduction",
        AuditStatus.FAIL,
        FindingSeverity.BLOCKER,
        (
            f"{len(result.mismatches)} of {result.checks} recomputed values did not match.",
            *examples,
        ),
        "Reported evaluation results are not independently reproducible.",
        "Resolve every mismatch before using any model output.",
        "test_independent_reproduction_matches_every_reported_metric",
    )


def _model_decisions(
    result: ModellingPipelineResult,
    cost_stress: dict[str, object],
    global_blockers: tuple[str, ...],
) -> list[dict[str, object]]:
    decisions: list[dict[str, object]] = []
    classification = result.report["classification"]
    regression = result.report["regression"]
    assert isinstance(classification, dict) and isinstance(regression, dict)
    for target in BINARY_TARGETS:
        target_report = classification[target.value]
        for model_name in target_report["models"]:
            target_stress = cost_stress.get(target.value, {})
            stress = (
                target_stress.get(model_name, {})
                if isinstance(target_stress, dict)
                else {}
            )
            reasons = list(global_blockers)
            if isinstance(stress, dict) and not bool(
                stress.get("all_scenarios_positive", False)
            ):
                reasons.append("COST_STRESS")
            decisions.append(
                {
                    "target": target.value,
                    "model": model_name,
                    "decision": "REJECT_FOR_PROMOTION",
                    "selected_by_walk_forward": model_name
                    == target_report["selected_model_from_walk_forward_only"],
                    "reasons": reasons,
                    "cost_stress": stress,
                }
            )
    for target in REGRESSION_TARGETS:
        target_report = regression[target.value]
        for model_name in target_report["models"]:
            decisions.append(
                {
                    "target": target.value,
                    "model": model_name,
                    "decision": "REJECT_FOR_PROMOTION",
                    "selected_by_walk_forward": model_name
                    == target_report["selected_model_from_walk_forward_only"],
                    "reasons": list(global_blockers),
                    "cost_stress": None,
                }
            )
    return decisions


class IndependentModelValidator:
    """Treat all supplied results as untrusted until independently cleared."""

    def __init__(self, config: ValidationConfig) -> None:
        self._config = config

    def validate(
        self,
        result: ModellingPipelineResult,
        *,
        repo_root: Path,
    ) -> ValidationResult:
        final_rows = _final_rows(result.dataset.rows, self._config.splits)
        if not final_rows:
            raise ValueError("validator found no final-test rows")

        reproduction = reproduce_report_metrics(
            result.report,
            result.predictions,
            final_rows,
            calibration_bins=self._config.calibration_bins,
            bootstrap_repetitions=self._config.bootstrap_repetitions,
            bootstrap_seed=self._config.bootstrap_seed,
        )
        findings: list[ValidationFinding] = [_metric_finding(reproduction)]
        findings.append(
            _finding(
                "MODEL_SELECTION_REPRODUCTION",
                "Walk-forward selection and instability reproduction",
                AuditStatus.NOT_VERIFIABLE,
                FindingSeverity.HIGH,
                (
                    "The exported predictions contain final-test values only.",
                    "Per-fold observation membership, predictions, fitted-transform manifests, model snapshots, and feature effects are not exported.",
                    "Rerunning the same modelling implementation is not an independent reproduction of model selection or instability flags.",
                ),
                "The validator cannot independently establish that walk-forward family selection and overfitting flags were calculated from the reported fold inputs.",
                "Export immutable fold assignments, per-family fold predictions, train-fitted transform manifests, and feature-effect snapshots with data/config/code hashes.",
                "test_walk_forward_selection_requires_fold_artifacts",
            )
        )

        feature_time_failures = tuple(
            row.observation_id
            for row in result.dataset.rows
            if row.features_available_at > row.prediction_as_of
        )
        outcome_time_failures = tuple(
            row.observation_id
            for row in result.dataset.rows
            if row.outcome_available_at <= row.prediction_as_of
        )
        training_time_failures = tuple(
            f"{prediction.target}/{prediction.model_name}/{prediction.observation_id}"
            for prediction in result.predictions
            if prediction.trained_through > self._config.splits.train_end
        )
        leakage_failures = feature_time_failures + outcome_time_failures + training_time_failures
        findings.append(
            _finding(
                "LOOKAHEAD",
                "Point-in-time and look-ahead controls",
                AuditStatus.FAIL if leakage_failures else AuditStatus.PASS,
                FindingSeverity.BLOCKER,
                (
                    f"feature timestamp violations: {len(feature_time_failures)}",
                    f"outcome-at-prediction violations: {len(outcome_time_failures)}",
                    f"final predictions trained after train cutoff: {len(training_time_failures)}",
                    "Preprocessing report states fit_on=training_only.",
                ),
                (
                    "Timestamp-validity failed and invalidates evaluation."
                    if leakage_failures
                    else "No row-level look-ahead violation was found in the synthetic fixture. This does not validate upstream vendor timestamps."
                ),
                "Keep fail-closed timestamp tests and add source-level lineage checks before real-data use.",
                "test_future_feature_and_outcome_injection_fail_closed",
            )
        )

        random_split_calls = scan_for_random_time_splits(repo_root)
        findings.append(
            _finding(
                "TIME_SPLIT",
                "Chronological splitting and final-test isolation",
                AuditStatus.FAIL if random_split_calls else AuditStatus.PASS,
                FindingSeverity.BLOCKER,
                (
                    f"random split calls found: {len(random_split_calls)}",
                    *random_split_calls[:5],
                    f"final-test rows: {len(final_rows)}",
                    f"reported final_test_used_for_model_selection={result.report['chronological_splits']['final_test_used_for_model_selection']}",
                ),
                (
                    "Randomized splitting can leak future regimes into training."
                    if random_split_calls
                    else "Application code uses explicit chronological and expanding walk-forward splits for this run."
                ),
                "Fail CI on random split calls and retain a one-time-use final-test registry.",
                "test_repository_has_no_random_time_series_split_calls",
            )
        )

        findings.append(
            _finding(
                "SURVIVORSHIP",
                "Historical universe and survivorship bias",
                AuditStatus.PASS if result.dataset.universe_survivorship_safe else AuditStatus.FAIL,
                FindingSeverity.BLOCKER,
                (
                    f"universe_survivorship_safe={result.dataset.universe_survivorship_safe}",
                    f"dataset_kind={result.dataset.dataset_kind}",
                    "The supplied sample contains synthetic active labels and is not a historical point-in-time listing master.",
                ),
                "Returns and hit rates may be overstated when failed, inactive, renamed, or delisted securities are absent.",
                "Require a point-in-time security master including inactive and delisted securities; fail closed otherwise.",
                "test_limited_universe_cannot_support_survivorship_safe_claim",
            )
        )

        findings.append(
            _finding(
                "ANNOUNCEMENT_TIME",
                "Announcement first-public timestamps",
                AuditStatus.NOT_VERIFIABLE,
                FindingSeverity.BLOCKER,
                (
                    "Fixture URLs and generated timestamps are not independently retrievable primary-source observations.",
                    "Row-level available_at ordering is checked, but first-public time cannot be corroborated from the fixture.",
                ),
                "A corrected or late-discovered timestamp could move a catalyst across the prediction boundary.",
                "Retain source-native IDs, original response hashes, first_seen_at, published_at, available_at, and retrieval evidence.",
                "test_incorrect_announcement_timestamp_is_rejected",
            )
        )

        findings.append(
            _finding(
                "REVISED_DATA",
                "Revision and immutable raw-data lineage",
                AuditStatus.FAIL,
                FindingSeverity.BLOCKER,
                (
                    "ModelRow has source_url but no raw response hash, source-native version, revision ID, or as-of snapshot ID.",
                    "The evaluator therefore cannot distinguish an original observation from a later revision or backfill.",
                ),
                "Later filing amendments, corporate-action repairs, or vendor backfills can silently leak into earlier feature rows.",
                "Join model rows to content-addressed immutable raw records and retain revision lineage in every feature manifest.",
                "test_model_rows_require_revision_lineage_before_promotion",
            )
        )

        cost_mismatches = tuple(
            row.observation_id
            for row in result.dataset.rows
            if row.net_return_after_cost_pct is not None
            and row.gross_return_pct is not None
            and abs(
                row.net_return_after_cost_pct
                - row.gross_return_pct
                + row.spread_cost_pct
                + row.slippage_cost_pct
            )
            > 1e-8
        )
        findings.append(
            _finding(
                "DECLARED_COSTS",
                "Declared spread and slippage arithmetic",
                AuditStatus.FAIL if cost_mismatches else AuditStatus.PASS,
                FindingSeverity.BLOCKER,
                (
                    f"cost arithmetic mismatches: {len(cost_mismatches)}",
                    "Independent top-k reproduction includes net expectancy and fill rate.",
                ),
                (
                    "Reported results omit or miscalculate declared costs."
                    if cost_mismatches
                    else "Declared spread and slippage are subtracted consistently; their empirical realism is not established."
                ),
                "Preserve this arithmetic check and validate cost inputs against timestamp-valid quotes.",
                "test_omitted_or_miscalculated_costs_fail_closed",
            )
        )

        findings.append(
            _finding(
                "FILL_REALISM",
                "Fill, capacity, and same-bar ambiguity modelling",
                AuditStatus.FAIL,
                FindingSeverity.BLOCKER,
                (
                    "Rows contain only a terminal filled/unfilled flag and aggregate spread/slippage costs.",
                    "No timestamp-valid bid/ask, quote size, participation limit, order latency, capacity, or same-barrier-touch evidence is retained.",
                    "Low-float full-fill assumptions cannot be independently reproduced.",
                ),
                "Costed expectancy can remain materially optimistic even when the arithmetic is correct.",
                "Add quote/bar-resolution-aware fills, participation limits, latency, conservative same-bar policy, and fill-fidelity flags.",
                "test_missing_fill_fidelity_blocks_model_promotion",
            )
        )

        findings.append(
            _finding(
                "HALTS",
                "Trading-halt and gap-through-stop handling",
                AuditStatus.FAIL,
                FindingSeverity.BLOCKER,
                (
                    "ModelRow and label policy expose no halt interval, reopening print, or halt-source lineage.",
                    "No automated evidence ties target/stop ordering or fills to halt-aware paths.",
                ),
                "Low-float loss tails and unfilled exits can be understated, especially across reopenings.",
                "Integrate a timestamped halt feed and test entries/exits, barrier ordering, and gap-through-stop policies around halts.",
                "test_missing_halt_evidence_blocks_model_promotion",
            )
        )

        findings.append(
            _finding(
                "SELECTION_BIAS",
                "Signal population, abstentions, and rejected candidates",
                AuditStatus.FAIL,
                FindingSeverity.HIGH,
                (
                    "Model rows have no candidate-universe ID, eligibility decision, or machine-readable rejection reason.",
                    "The evaluator cannot prove that all contemporaneous candidates, abstentions, and rejected signals were retained.",
                ),
                "Selective inclusion can inflate base rates and ranking metrics.",
                "Create an append-only candidate ledger covering accepted, rejected, and abstained signals before outcomes mature.",
                "test_missing_candidate_ledger_blocks_model_promotion",
            )
        )

        findings.append(
            _finding(
                "CATALYST_SELECTION",
                "Catalyst completeness and cherry-picking",
                AuditStatus.FAIL,
                FindingSeverity.HIGH,
                (
                    "Catalyst category is present, but no point-in-time discovery-universe manifest is linked to ModelRow.",
                    "There is no completeness denominator for eligible SEC/company/exchange announcements.",
                ),
                "Hand-selected catalysts can make conditional performance appear stronger than a deployable discovery process.",
                "Version the full discovered-event manifest and retain exclusions with deterministic reasons.",
                "test_missing_catalyst_discovery_manifest_blocks_model_promotion",
            )
        )

        findings.append(
            _finding(
                "SOCIAL_DUPLICATION",
                "Duplicated and coordinated social-post inputs",
                AuditStatus.NOT_VERIFIABLE,
                FindingSeverity.HIGH,
                (
                    "The retail-attention component has duplicate-language controls.",
                    "ModelRow retains only the derived attention stage, not source post IDs, text hashes, authors, or availability timestamps.",
                ),
                "Copied promotion may be counted as independent discovery once only the aggregate stage reaches the model table.",
                "Persist a point-in-time attention feature manifest with source IDs and deduplication decisions; keep missing coverage explicit.",
                "test_duplicated_social_posts_do_not_increase_independent_attention",
            )
        )

        findings.append(
            _finding(
                "REPEATED_TESTING",
                "Repeated final-test access and researcher degrees of freedom",
                AuditStatus.FAIL,
                FindingSeverity.HIGH,
                (
                    "The final-test pipeline is rerunnable and no immutable holdout access registry is present.",
                    "The report states final-test data were not used for selection within one run, but cannot prove that earlier runs did not influence code or thresholds.",
                ),
                "Repeated inspection turns the nominal final period into another validation set and invalidates confidence intervals.",
                "Register hypotheses before final evaluation, hash code/config/data, record each holdout access, and roll to a new untouched period after access.",
                "test_final_holdout_requires_access_registry_before_promotion",
            )
        )

        classification = result.report["classification"]
        assert isinstance(classification, dict)
        cost_stress: dict[str, object] = {}
        for target in BINARY_TARGETS:
            eligible = tuple(
                row for row in final_rows if row.binary_label(target) is not None
            )
            target_stress: dict[str, object] = {}
            for model_name in classification[target.value]["models"]:
                values_by_id = _lookup(result.predictions, target.value, model_name)
                values = tuple(values_by_id[row.observation_id] for row in eligible)
                target_stress[model_name] = cost_stress_summary(
                    eligible,
                    values,
                    cost_multipliers=self._config.cost_multipliers,
                    top_k=self._config.top_k,
                )
            cost_stress[target.value] = target_stress

        failed_stress_models = tuple(
            f"{target}/{model_name}"
            for target, target_values in cost_stress.items()
            for model_name, stress in target_values.items()
            if not bool(stress["all_scenarios_positive"])
        )
        findings.append(
            _finding(
                "COST_STRESS",
                "Adverse spread, slippage, and low-float fill stress",
                AuditStatus.FAIL if failed_stress_models else AuditStatus.WARNING,
                FindingSeverity.HIGH,
                (
                    f"classification model/target pairs failing at least one diagnostic scenario: {len(failed_stress_models)}",
                    *failed_stress_models[:10],
                    "Scenarios include 1.0x, 1.5x, and 2.0x declared costs plus a low-float-unfilled/2.5x-cost case.",
                    "Fixture cost inputs lack quote and fill provenance, so even passing pairs are not cleared.",
                ),
                "Models that fail diagnostic cost stress cannot advance; passing synthetic stress still provides no empirical edge evidence.",
                "Reject failing pairs and rerun all scenarios using timestamp-valid quotes, halts, capacity, and conservative fills.",
                "test_cost_stress_failures_are_rejected",
            )
        )

        tbs_model = classification[BinaryTarget.TARGET_BEFORE_STOP.value][
            "selected_model_from_walk_forward_only"
        ]
        tbs_values_by_id = _lookup(
            result.predictions, BinaryTarget.TARGET_BEFORE_STOP.value, tbs_model
        )
        tbs_values = tuple(tbs_values_by_id[row.observation_id] for row in final_rows)
        concentration = security_concentration_summary(
            final_rows, tbs_values, self._config.top_k
        )
        concentration["concentration_threshold"] = self._config.concentration_threshold
        concentration["concentration_failure"] = (
            float(concentration["maximum_single_security_share"])
            > self._config.concentration_threshold
        )
        findings.append(
            _finding(
                "SECURITY_CONCENTRATION",
                "Dependence on one unusual security",
                (
                    AuditStatus.WARNING
                    if bool(concentration["concentration_failure"])
                    or "synthetic" in result.dataset.dataset_kind.lower()
                    else AuditStatus.PASS
                ),
                FindingSeverity.HIGH,
                (
                    f"top-{concentration['top_k']} maximum single-security share={concentration['maximum_single_security_share']}",
                    f"threshold={self._config.concentration_threshold}",
                    "Leave-one-security-out Brier scores were independently computed.",
                    "This fixture result cannot establish real-market ticker diversification.",
                ),
                "A single idiosyncratic ticker can dominate small top-k results.",
                "Require event-clustered and security-clustered uncertainty plus leave-one-ticker-out stability on real data.",
                "test_security_concentration_and_leave_one_out_are_reported",
            )
        )

        findings.append(
            _finding(
                "EMPIRICAL_STATUS",
                "Empirical evidence and promotion eligibility",
                AuditStatus.FAIL,
                FindingSeverity.BLOCKER,
                (
                    f"report status={result.report['status']}",
                    f"dataset_kind={result.dataset.dataset_kind}",
                    "The supplied rows are an engineering fixture, not observed Nasdaq/NYSE outcomes.",
                ),
                "Synthetic metrics cannot demonstrate calibration, edge, capacity, or profitability.",
                "Run a timestamp-correct historical pipeline on a survivorship-safe universe, then evaluate a newly locked final period after realistic costs.",
                "test_synthetic_fixture_is_never_promoted_as_empirical_evidence",
            )
        )

        global_blockers = tuple(
            finding.check_id
            for finding in findings
            if finding.severity is FindingSeverity.BLOCKER
            and finding.status is not AuditStatus.PASS
        )
        decisions = _model_decisions(result, cost_stress, global_blockers)
        report: dict[str, object] = {
            "schema_version": "validation-report-v1",
            "audited_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scope": "synthetic chronological modelling engineering sample",
            "independence": {
                "assumption": "research results may be wrong",
                "metric_engine_imports_modelling_evaluation": False,
                "model_selection_changed_by_validator": False,
            },
            "disposition": "REJECT_ALL_MODELS_FOR_PROMOTION_OR_PERFORMANCE_CLAIMS",
            "decision_support_only": True,
            "automated_execution_authorized": False,
            "profitability_claimed": False,
            "blockers": list(global_blockers),
            "summary_counts": {
                status.value: sum(finding.status is status for finding in findings)
                for status in AuditStatus
            },
            "metric_reproduction": reproduction.to_dict(),
            "cost_stress_classification_models": cost_stress,
            "security_concentration": concentration,
            "findings": [finding.to_dict() for finding in findings],
            "model_decisions": decisions,
            "limitations": [
                "No real market, quote, halt, delisting, filing-revision, or social-post corpus was supplied.",
                "Code-level controls were tested; vendor timestamp correctness and historical completeness remain unverified.",
                "The cost stress is diagnostic only because fixture costs and fills have no empirical provenance.",
            ],
        }
        return ValidationResult(
            report=report,
            markdown=render_validation_markdown(report),
            findings=tuple(findings),
        )


def render_validation_markdown(report: dict[str, object]) -> str:
    """Render a concise, reviewable validation report from structured findings."""

    reproduction = report["metric_reproduction"]
    counts = report["summary_counts"]
    findings = report["findings"]
    concentration = report["security_concentration"]
    assert isinstance(reproduction, dict)
    assert isinstance(counts, dict)
    assert isinstance(findings, list)
    assert isinstance(concentration, dict)
    lines = [
        "# Independent Model Validation and Red-Team Report",
        "",
        f"**Disposition:** `{report['disposition']}`",
        "",
        "The audit assumes the research results may be wrong. The current sample is suitable only for engineering verification; it is not empirical performance evidence and supports no profitability claim.",
        "",
        "## Executive summary",
        "",
        f"- Findings: {counts.get('pass', 0)} pass, {counts.get('fail', 0)} fail, {counts.get('warning', 0)} warning, {counts.get('not_verifiable', 0)} not verifiable.",
        f"- Independent metric reproduction: {reproduction['matches']}/{reproduction['checks']} matched; {reproduction['mismatch_count']} mismatches.",
        f"- Top-10 maximum single-security share: {concentration['maximum_single_security_share']}.",
        "- All candidate models are rejected for promotion or performance claims until the blocker findings are resolved on timestamp-valid real data.",
        "",
        "## Findings",
        "",
        "| ID | Status | Severity | Finding |",
        "| --- | --- | --- | --- |",
    ]
    for finding in findings:
        lines.append(
            f"| {finding['check_id']} | {str(finding['status']).upper()} | {str(finding['severity']).upper()} | {finding['title']} |"
        )
    for finding in findings:
        lines.extend(
            [
                "",
                f"### {finding['check_id']}: {finding['title']}",
                "",
                f"Status: **{str(finding['status']).upper()}**; severity: **{str(finding['severity']).upper()}**.",
                "",
                *[f"- Evidence: {value}" for value in finding["evidence"]],
                f"- Impact: {finding['impact']}",
                f"- Required action: {finding['recommendation']}",
                f"- Automated test: `{finding['automated_test']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Promotion gate",
            "",
            "No model may advance to a live shadow claim until all blocker findings pass, the final period is newly locked and chronologically untouched, realistic halt/fill/capacity assumptions are independently reproducible, and performance remains after timestamp-valid spread and slippage costs. Shadow predictions remain decision support only and must never execute trades.",
            "",
        ]
    )
    return "\n".join(lines)
