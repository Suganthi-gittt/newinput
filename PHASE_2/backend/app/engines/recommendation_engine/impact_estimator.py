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
        """
        Estimate impact of a recommendation candidate.
        
        This method consumes from upstream engines (ProjectMetrics, ForecastResult, RiskResult)
        rather than performing its own calculations. This ensures consistency with the
        single source of truth from upstream engines.
        """
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
        """
        Estimate impact of resolving a blocker.
        
        Consumes:
        - blocker_velocity_impact from ProjectMetrics (severity-weighted share)
        - expected_delay_days from ForecastResult
        - delay_breakdown from ForecastResult for attribution
        """
        blocker_velocity_impact = float(self.upstream.metrics.estimated_blocker_velocity_impact or 0.0)
        
        # Blocker's share of the overall delay based on velocity impact
        # This is already weighted by severity in MetricsEngine
        active_blocker_count = max(
            len([b for b in self.project_state.blockers if not b.actual_resolution_date]), 1
        )
        
        # Use forecast delay breakdown if available to get blocker-attributable delay
        blocker_delay_days = 0.0
        if hasattr(self.upstream.forecast, "delay_breakdown") and self.upstream.forecast.delay_breakdown:
            blocker_delay_days = float(
                self.upstream.forecast.delay_breakdown.remaining_days_blocker_loss or 0.0
            )
        else:
            # Fallback: allocate blocker impact proportionally
            blocker_delay_days = (
                self.upstream.forecast.expected_delay_days * blocker_velocity_impact / active_blocker_count
            )
        
        # Hours recovered: same fraction of remaining effort
        blocked_hours = min(
            self.upstream.forecast.remaining_effort_hours * (blocker_velocity_impact / active_blocker_count),
            self.upstream.forecast.remaining_effort_hours,
        )
        
        return self._build_estimate(
            candidate,
            hours_recovered=blocked_hours,
            delay_days=blocker_delay_days,
            risk_reduction=min(0.15 + (blocker_velocity_impact / active_blocker_count), 0.40),
            confidence=ConfidenceLevel.HIGH,
            evidence=[self._evidence(
                "ForecastEngine",
                "delay_breakdown.blocker_loss",
                blocker_delay_days,
                0.0,
                f"Resolving blockers accounts for {round(blocker_delay_days, 1)} days of schedule delay",
            )],
            notes=(
                f"Resolving this blocker removes its share of the {round(blocker_velocity_impact * 100, 1)}% "
                f"velocity impact from {active_blocker_count} active blocker(s), "
                f"recovering an estimated {round(blocker_delay_days, 1)} days."
            ),
        )

    def _estimate_reassign_item(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        """
        Estimate impact of reassigning a work item.
        
        Consumes:
        - average_item_effort from ProjectMetrics
        - resource_metrics from ProjectMetrics for capacity relief
        """
        hours_recovered = min(
            self.upstream.metrics.average_item_effort,
            self.upstream.forecast.remaining_effort_hours
        )
        
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=0.0,
            risk_reduction=0.05,
            confidence=ConfidenceLevel.MEDIUM,
            evidence=[self._evidence(
                "MetricsEngine",
                "average_item_effort",
                self.upstream.metrics.average_item_effort,
                0.0,
                "Reassigning work can reduce resource contention"
            )],
            notes="Reassigning work can improve resource utilization without changing total scope",
        )

    def _estimate_split_item(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        """
        Estimate impact of splitting a work item.
        
        Consumes:
        - average_item_effort from ProjectMetrics
        - remaining_effort_hours from ForecastResult
        """
        hours_recovered = min(
            self.upstream.metrics.average_item_effort * 0.5,
            self.upstream.forecast.remaining_effort_hours
        )
        
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=0.0,
            risk_reduction=0.04,
            confidence=ConfidenceLevel.MEDIUM,
            evidence=[self._evidence(
                "MetricsEngine",
                "average_item_effort",
                self.upstream.metrics.average_item_effort,
                0.0,
                "Splitting large items reduces batch size and improves flow"
            )],
            notes="Splitting an item reduces batch size and can improve execution predictability",
        )

    def _estimate_advance_item(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        """
        Estimate impact of advancing an item to an earlier sprint.
        
        Consumes:
        - expected_delay_days from ForecastResult
        - remaining_effort_hours from ForecastResult
        - critical_path information from upstream
        """
        # Advancing items helps most when on or near the critical path
        is_on_cp = any(
            item_id in self.upstream.cp_result.items_on_critical_path
            for item_id in candidate.affected_item_ids
        )
        
        # Impact depends on whether item is on critical path
        if is_on_cp:
            delay_reduction = min(
                self.upstream.forecast.expected_delay_days * 0.35,  # Can recover more if on CP
                3.0
            )
        else:
            delay_reduction = min(
                self.upstream.forecast.expected_delay_days * 0.15,
                2.0
            )
        
        hours_recovered = min(
            self.upstream.forecast.remaining_effort_hours * 0.1,
            self.upstream.forecast.remaining_effort_hours
        )
        
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=delay_reduction,
            risk_reduction=0.08 if is_on_cp else 0.06,
            confidence=ConfidenceLevel.MEDIUM if is_on_cp else ConfidenceLevel.LOW,
            evidence=[self._evidence(
                "ForecastEngine",
                "expected_delay_days",
                self.upstream.forecast.expected_delay_days,
                0.0,
                f"Advancing item {'on critical path' if is_on_cp else 'reduces schedule pressure'}"
            )],
            notes=(
                f"Advancing an item {'on the critical path' if is_on_cp else ''} "
                f"can reduce downstream schedule pressure"
            ),
        )

    def _estimate_parallelize_items(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        """
        Estimate impact of parallelizing work items.
        
        Consumes:
        - critical_path_length from CriticalPathResult
        - dependency_count from ProjectMetrics
        """
        cp_length = float(self.upstream.cp_result.critical_path_length or 0.0)
        dependency_count = float(self.upstream.metrics.dependency_count or 0.0)
        
        # Impact depends on current dependency pressure
        dependency_pressure = min(1.0, dependency_count / max(len(self.project_state.work_items), 1))
        
        hours_recovered = min(
            self.upstream.forecast.remaining_effort_hours * 0.12 * dependency_pressure,
            self.upstream.forecast.remaining_effort_hours
        )
        
        delay_reduction = min(
            self.upstream.forecast.expected_delay_days * 0.2 * dependency_pressure,
            1.5
        )
        
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=delay_reduction,
            risk_reduction=0.07 if dependency_pressure > 0.5 else 0.04,
            confidence=ConfidenceLevel.LOW,
            evidence=[self._evidence(
                "CriticalPathEngine",
                "critical_path_length",
                cp_length,
                0.0,
                "Parallelizing independent items can reduce serial dependency drag"
            )],
            notes="Parallelizing items has impact proportional to dependency pressure",
        )

    def _estimate_rebalance_sprint_load(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        """
        Estimate impact of rebalancing sprint load.
        
        Consumes:
        - sprint_metrics from ProjectMetrics for current utilization
        """
        # Find affected sprint metrics to assess current load imbalance
        underutilized_sprints = sum(
            1 for sm in self.upstream.metrics.sprint_metrics
            if sm.completion_pct < 0.5
        )
        overutilized_sprints = sum(
            1 for sm in self.upstream.metrics.sprint_metrics
            if sm.completion_pct > 1.0
        )
        
        imbalance = (underutilized_sprints + overutilized_sprints) / max(
            len(self.upstream.metrics.sprint_metrics), 1
        )
        
        hours_recovered = min(
            self.upstream.metrics.average_item_effort * 0.25 * imbalance,
            self.upstream.forecast.remaining_effort_hours
        )
        
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=0.0,
            risk_reduction=0.03 * imbalance,
            confidence=ConfidenceLevel.LOW,
            evidence=[self._evidence(
                "MetricsEngine",
                "sprint_metrics",
                imbalance,
                0.0,
                f"Sprint load imbalance detected in {round(imbalance * 100, 1)}% of sprints"
            )],
            notes="Sprint rebalancing has limited schedule leverage without slack",
        )

    def _estimate_remove_dependency_bottleneck(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        """
        Estimate impact of removing a dependency bottleneck.
        
        Consumes:
        - critical_path information from upstream
        - dependency metrics from ProjectMetrics
        """
        # Find bottleneck items: those with high in-degree on the critical path
        cp_items = self.upstream.cp_result.items_on_critical_path or []
        
        hours_recovered = min(
            self.upstream.forecast.remaining_effort_hours * 0.15,
            self.upstream.forecast.remaining_effort_hours
        )
        
        # Impact if bottleneck is on critical path
        is_cp_bottleneck = any(
            item_id in cp_items
            for item_id in candidate.affected_item_ids
        )
        
        delay_reduction = min(
            self.upstream.forecast.expected_delay_days * (0.35 if is_cp_bottleneck else 0.15),
            2.5
        )
        
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=delay_reduction,
            risk_reduction=0.10 if is_cp_bottleneck else 0.06,
            confidence=ConfidenceLevel.MEDIUM if is_cp_bottleneck else ConfidenceLevel.LOW,
            evidence=[self._evidence(
                "DependencyGraphEngine",
                "dependency_count",
                float(self.upstream.metrics.dependency_count or 0.0),
                0.0,
                "Removing a dependency bottleneck eases critical path pressure"
            )],
            notes="Impact depends on whether bottleneck is on the critical path",
        )

    def _estimate_add_resource_skill(self, candidate: RecommendationCandidate) -> ImpactEstimate:
        """
        Estimate impact of adding resource skill coverage.
        
        Consumes:
        - risk_result from RiskEngine for resource risk
        - resource_metrics from ProjectMetrics
        """
        # Skill coverage helps when resource risk is high
        resource_risk_score = float(
            getattr(self.upstream.risk_result, "resource_risk", {}).score
            if hasattr(self.upstream.risk_result, "resource_risk") else 0.0
        )
        
        hours_recovered = min(
            self.upstream.metrics.average_item_effort * 0.3 * min(1.0, resource_risk_score),
            self.upstream.forecast.remaining_effort_hours
        )
        
        return self._build_estimate(
            candidate,
            hours_recovered=hours_recovered,
            delay_days=0.0,
            risk_reduction=0.08 if resource_risk_score > 0.5 else 0.04,
            confidence=ConfidenceLevel.MEDIUM if resource_risk_score > 0.5 else ConfidenceLevel.LOW,
            evidence=[self._evidence(
                "RiskEngine",
                "resource_risk_score",
                resource_risk_score,
                0.0,
                "Skill coverage improves capacity resilience"
            )],
            notes="Impact depends on current resource risk level",
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
