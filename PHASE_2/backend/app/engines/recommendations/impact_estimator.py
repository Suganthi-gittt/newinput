from __future__ import annotations

from typing import List

from app.domain.models import ProjectState
from app.engines.recommendations.models import (
    ConfidenceLevel,
    ImpactEstimate,
    RecommendationAction,
    RecommendationCandidate,
    SignalEvidence,
    UpstreamEngineOutputs,
)


# Severity mapping reused for recommendation impact estimation
SEVERITY_SCORES = {
    "Critical": 40.0,
    "High": 20.0,
    "Medium": 10.0,
    "Low": 5.0,
}


class ImpactEstimator:
    def __init__(self, project_state: ProjectState, upstream: UpstreamEngineOutputs) -> None:
        self.project_state = project_state
        self.upstream = upstream

    def estimate(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        dispatch = {
            RecommendationAction.RESOLVE_BLOCKER: self._estimate_resolve_blocker,
            RecommendationAction.REASSIGN_ITEM: self._estimate_reassign_item,
            RecommendationAction.SPLIT_ITEM: self._estimate_split_item,
            RecommendationAction.ADVANCE_ITEM_TO_EARLIER_SPRINT: self._estimate_advance_item,
            RecommendationAction.PARALLELIZE_ITEMS: self._estimate_parallelize_items,
            RecommendationAction.REBALANCE_SPRINT_LOAD: self._estimate_rebalance_sprint_load,
            RecommendationAction.REMOVE_DEPENDENCY_BOTTLENECK: self._estimate_remove_dependency_bottleneck,
            RecommendationAction.ADD_RESOURCE_SKILL: self._estimate_add_resource_skill,
        }
        estimator = dispatch.get(candidate.action_type)
        if estimator is None:
            return self._default_estimate(candidate)
        return estimator(candidate)

    def _estimate_resolve_blocker(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        # Hours recovered: proportional share of remaining effort attributed to this one blocker.
        # active_blocker_count guard ensures we never divide by zero.
        blocker_velocity_impact = float(self.upstream.metrics.estimated_blocker_velocity_impact or 0.0)

        # Weight active blockers by severity to compute each blocker's relative share
        active_blockers = [b for b in self.project_state.blockers if not b.actual_resolution_date]
        active_blocker_count = max(len(active_blockers), 1)
        if active_blockers:
            total_weight = 0.0
            by_id = {b.blocker_id: b for b in active_blockers}
            for b in active_blockers:
                total_weight += SEVERITY_SCORES.get(getattr(b.severity, 'value', str(b.severity)), 0.0)

            # Find the first affected blocker id for this candidate (resolver targets one blocker)
            target_blocker_id = candidate.affected_blocker_ids[0] if candidate.affected_blocker_ids else None
            if target_blocker_id and target_blocker_id in by_id and total_weight > 0.0:
                b = by_id[target_blocker_id]
                weight = SEVERITY_SCORES.get(getattr(b.severity, 'value', str(b.severity)), 0.0)
                per_blocker_impact_share = (weight / total_weight) * blocker_velocity_impact
            else:
                # Fallback to even split
                per_blocker_impact_share = blocker_velocity_impact / active_blocker_count
        else:
            per_blocker_impact_share = 0.0

        # Delay days recoverable by resolving this one blocker.
        recoverable_delay_days = round(
            self.upstream.forecast.expected_delay_days * per_blocker_impact_share, 2
        )

        # Hours recovered: same fraction of remaining effort.
        blocked_hours = min(
            self.upstream.forecast.remaining_effort_hours * per_blocker_impact_share,
            self.upstream.forecast.remaining_effort_hours,
        )

        return self._build_estimate(
            candidate,
            hours_recovered=blocked_hours,
            delay_days=recoverable_delay_days,
            risk_reduction=min(0.15 + per_blocker_impact_share, 0.40),
            confidence=ConfidenceLevel.HIGH,
            evidence=[self._evidence(
                "MetricsEngine",
                "estimated_blocker_velocity_impact",
                blocker_velocity_impact,
                0.0,
                f"This blocker accounts for ~{round(per_blocker_impact_share * 100, 1)}% of total blocker velocity drag (severity-weighted)",
            )],
            notes=(
                f"Resolving this blocker removes its share of the {round(blocker_velocity_impact * 100, 1)}% "
                f"compound velocity reduction from {active_blocker_count} active blocker(s), "
                f"recovering an estimated {recoverable_delay_days} days."
            ),
        )

    def _estimate_reassign_item(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        hours_recovered = min(self.upstream.metrics.average_item_effort, self.upstream.forecast.remaining_effort_hours)
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=0.0,
            risk_reduction=0.05,
            confidence=ConfidenceLevel.MEDIUM,
            evidence=[self._evidence("MetricsEngine", "average_item_effort", self.upstream.metrics.average_item_effort, 0.0, "Reassigning work uses average item effort")],
            notes="Reassigning work can reduce queueing pressure without changing total scope",
        )

    def _estimate_split_item(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        hours_recovered = min(self.upstream.metrics.average_item_effort * 0.5, self.upstream.forecast.remaining_effort_hours)
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=0.0,
            risk_reduction=0.04,
            confidence=ConfidenceLevel.MEDIUM,
            evidence=[self._evidence("MetricsEngine", "average_item_effort", self.upstream.metrics.average_item_effort, 0.0, "Splitting work reduces large-item risk")],
            notes="Splitting an item reduces batch size and can improve flow",
        )

    def _estimate_advance_item(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        hours_recovered = min(self.upstream.forecast.remaining_effort_hours * 0.1, self.upstream.forecast.remaining_effort_hours)
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=round(min(self.upstream.forecast.expected_delay_days * 0.25, 2.0), 2),
            risk_reduction=0.06,
            confidence=ConfidenceLevel.MEDIUM,
            evidence=[self._evidence("ForecastEngine", "expected_delay_days", self.upstream.forecast.expected_delay_days, 0.0, "Advancing an item shortens the plan window")],
            notes="Advancing an item can reduce downstream schedule pressure",
        )

    def _estimate_parallelize_items(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        hours_recovered = min(self.upstream.forecast.remaining_effort_hours * 0.12, self.upstream.forecast.remaining_effort_hours)
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=round(min(self.upstream.forecast.expected_delay_days * 0.2, 1.5), 2),
            risk_reduction=0.07,
            confidence=ConfidenceLevel.LOW,
            evidence=[self._evidence("CriticalPathEngine", "critical_path_length", float(self.upstream.cp_result.critical_path_length), 0.0, "Parallelizing work reduces critical path dependency pressure")],
            notes="Parallelizing independent items can reduce serial dependency drag",
        )

    def _estimate_rebalance_sprint_load(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        hours_recovered = min(self.upstream.metrics.average_item_effort * 0.25, self.upstream.forecast.remaining_effort_hours)
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=0.0,
            risk_reduction=0.03,
            confidence=ConfidenceLevel.LOW,
            evidence=[self._evidence("MetricsEngine", "average_item_effort", self.upstream.metrics.average_item_effort, 0.0, "Rebalancing load within a sprint changes throughput shape")],
            notes="Simple sprint rebalancing has limited schedule leverage without slack",
        )

    def _estimate_remove_dependency_bottleneck(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        hours_recovered = min(self.upstream.forecast.remaining_effort_hours * 0.15, self.upstream.forecast.remaining_effort_hours)
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=round(min(self.upstream.forecast.expected_delay_days * 0.3, 2.5), 2),
            risk_reduction=0.08,
            confidence=ConfidenceLevel.MEDIUM,
            evidence=[self._evidence("DependencyGraphEngine", "dependency_count", float(self.upstream.metrics.dependency_count), 0.0, "Removing a dependency bottleneck eases critical path pressure")],
            notes="Removing a dependency bottleneck can unlock downstream work",
        )

    def _estimate_add_resource_skill(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        hours_recovered = min(self.upstream.metrics.average_item_effort * 0.3, self.upstream.forecast.remaining_effort_hours)
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=0.0,
            risk_reduction=0.04,
            confidence=ConfidenceLevel.MEDIUM,
            evidence=[self._evidence("RiskEngine", "overall_risk_score", self.upstream.risk_result.overall_risk_score, 0.0, "Adding skill coverage improves capacity resilience")],
            notes="Skill coverage improves execution flexibility but may not change schedule directly",
        )

    def _default_estimate(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        return self._build_estimate(
            candidate,
            hours_recovered=0.0,
            delay_days=0.0,
            risk_reduction=0.0,
            confidence=ConfidenceLevel.LOW,
            evidence=[self._evidence("ForecastEngine", "remaining_effort_hours", self.upstream.forecast.remaining_effort_hours, 0.0, "No direct impact estimate available")],
            notes="Fell back to a neutral estimate",
        )

    def _build_estimate(
        self,
        candidate: RecommendationCandidate,
        *,
        hours_recovered: float,
        delay_days: float,
        risk_reduction: float,
        confidence: ConfidenceLevel,
        evidence: List[SignalEvidence],
        notes: str,
    ) -> ImpactEstimate:
        cap = max(0.0, self.upstream.forecast.remaining_effort_hours)
        return ImpactEstimate(
            estimated_hours_recovered=float(min(max(hours_recovered, 0.0), cap)),
            estimated_delay_reduction_days=float(max(delay_days, 0.0)),
            estimated_risk_reduction=float(max(risk_reduction, 0.0)),
            confidence=confidence,
            evidence=evidence,
            calculation_notes=notes,
        )

    def _evidence(self, source_engine: str, metric_name: str, metric_value: float, threshold: float, explanation: str) -> SignalEvidence:
        return SignalEvidence(
            source_engine=source_engine,
            metric_name=metric_name,
            metric_value=float(metric_value),
            threshold=float(threshold),
            explanation=explanation,
        )
