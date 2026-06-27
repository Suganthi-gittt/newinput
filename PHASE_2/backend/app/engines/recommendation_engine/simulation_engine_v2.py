from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from app.domain.models import Blocker, BlockerStatus, ProjectState, WorkItemStatus, WorkItem
from app.engines.critical_path_engine import CriticalPathEngine, CriticalPathResult
from app.engines.dependency_engine import DependencyDAG, DependencyGraphEngine
from app.engines.forecast_engine import ForecastEngine, ForecastResult
from app.engines.impact_scoring_engine import ImpactScoringEngine, RiskScores
from app.engines.metrics_engine import MetricsEngine, ProjectMetrics
from app.engines.monte_carlo_engine import MonteCarloEngine, MonteCarloResult
from app.engines.risk_engine import RiskEngine, RiskResult
from app.engines.spillover_engine import SpilloverAnalysis, SpilloverAnalysisEngine

from .models import (
    BaselineMetrics,
    Recommendation,
    SimulatedMetrics,
    SimulationResult,
    UpstreamEngineOutputs,
)

MONTE_CARLO_SEED: int = 42


class ActionApplicator:
    """Apply a recommendation to a cloned ProjectState."""

    def apply(self, state: ProjectState, rec: Recommendation) -> None:
        action_name = rec.action_type
        if action_name == "resolve_blocker":
            self._apply_resolve_blocker(state, rec)
        elif action_name == "reassign_item":
            self._apply_reassign_item(state, rec)
        elif action_name == "split_item":
            self._apply_split_item(state, rec)
        elif action_name == "advance_item_to_earlier_sprint":
            self._apply_advance_item(state, rec)
        elif action_name == "parallelize_items":
            self._apply_parallelize_items(state, rec)
        elif action_name == "rebalance_sprint_load":
            self._apply_rebalance_sprint_load(state, rec)
        elif action_name == "remove_dependency_bottleneck":
            self._apply_remove_dependency_bottleneck(state, rec)
        elif action_name == "add_resource_skill":
            self._apply_add_resource_skill(state, rec)

    def apply_many(self, state: ProjectState, recs: List[Recommendation]) -> None:
        """Apply in lexicographic recommendation_id order for determinism."""
        for rec in sorted(recs, key=lambda r: r.recommendation_id):
            self.apply(state, rec)

    def _apply_resolve_blocker(self, state: ProjectState, rec: Recommendation) -> None:
        for blocker_id in rec.affected_blocker_ids:
            blocker = next((b for b in state.blockers if b.blocker_id == blocker_id), None)
            if blocker is not None:
                blocker.status = BlockerStatus.RESOLVED
                blocker.actual_resolution_date = blocker.raised_date
                self._unblock_impacted_items(state, blocker)

    def _unblock_impacted_items(self, state: ProjectState, blocker: Blocker) -> None:
        for impacted_item_id in getattr(blocker, "impacted_item_ids", []) or []:
            item = next((wi for wi in state.work_items if wi.item_id == impacted_item_id), None)
            if item and item.status == WorkItemStatus.BLOCKED:
                item.status = (
                    WorkItemStatus.IN_PROGRESS
                    if item.progress_pct > 0.0 or item.actual_effort_hrs > 0.0
                    else WorkItemStatus.NOT_STARTED
                )

    def _apply_reassign_item(self, state: ProjectState, rec: Recommendation) -> None:
        resource_id = rec.affected_resource_ids[0] if rec.affected_resource_ids else None
        if not resource_id:
            return
        for item in state.work_items:
            if item.item_id in rec.affected_item_ids:
                item.assigned_resource = resource_id

    def _apply_split_item(self, state: ProjectState, rec: Recommendation) -> None:
        for item in list(state.work_items):
            if item.item_id in rec.affected_item_ids:
                # Model splitting as two parallel items rather than shrinking scope in-place.
                original_hours = float(item.current_estimate_hrs)
                half_hours = max(1.0, original_hours / 2.0)

                # Update the existing item to represent one half
                item.current_estimate_hrs = half_hours
                item.remaining_effort_hrs = max(0.0, float(item.remaining_effort_hrs) / 2.0)

                # Create a new sibling work item representing the parallelized split
                suffix = "-split"
                new_id = item.item_id + suffix
                # Ensure uniqueness by appending a numeric suffix if necessary
                idx = 1
                existing_ids = {wi.item_id for wi in state.work_items}
                while new_id in existing_ids:
                    new_id = f"{item.item_id}{suffix}{idx}"
                    idx += 1

                new_item = deepcopy(item)
                new_item.item_id = new_id
                new_item.current_estimate_hrs = half_hours
                new_item.remaining_effort_hrs = max(0.0, float(new_item.remaining_effort_hrs) / 2.0)
                new_item.progress_pct = 0.0
                new_item.actual_effort_hrs = 0.0
                # Keep same assigned sprint/resource so both can run in parallel
                state.work_items.append(new_item)

    def _apply_advance_item(self, state: ProjectState, rec: Recommendation) -> None:
        for item in state.work_items:
            if item.item_id in rec.affected_item_ids:
                item.assigned_sprint = rec.affected_sprint_ids[0] if rec.affected_sprint_ids else item.assigned_sprint

    def _apply_parallelize_items(self, state: ProjectState, rec: Recommendation) -> None:
        for dep in state.dependencies:
            if dep.predecessor_item_id in rec.affected_item_ids and dep.successor_item_id in rec.affected_item_ids:
                dep.lag_days = max(0, dep.lag_days - 1)

    def _apply_rebalance_sprint_load(self, state: ProjectState, rec: Recommendation) -> None:
        resource_id = rec.affected_resource_ids[0] if rec.affected_resource_ids else None
        if not resource_id:
            return
        for item in state.work_items:
            if item.item_id in rec.affected_item_ids:
                item.assigned_resource = resource_id

    def _apply_remove_dependency_bottleneck(self, state: ProjectState, rec: Recommendation) -> None:
        for dep in state.dependencies:
            if dep.predecessor_item_id in rec.affected_item_ids or dep.successor_item_id in rec.affected_item_ids:
                dep.lag_days = max(0, dep.lag_days - 1)

    def _apply_add_resource_skill(self, state: ProjectState, rec: Recommendation) -> None:
        resource_id = rec.affected_resource_ids[0] if rec.affected_resource_ids else None
        if resource_id is None:
            return
        for resource in state.team:
            if resource.resource_id == resource_id:
                # Use the required skill if provided in the recommendation simulation params
                req_skill = None
                try:
                    req_skill = rec.simulation_params.get("required_skill")
                except Exception:
                    req_skill = None
                if req_skill:
                    resource.primary_skill = req_skill


class EngineRunner:
    """Runs the full engine pipeline on a ProjectState with seed=42."""

    SEED: int = MONTE_CARLO_SEED

    def run(self, state: ProjectState, simulation_count: int = 1000) -> UpstreamEngineOutputs:
        """
        Run: MetricsEngine → DependencyGraphEngine → CriticalPathEngine →
             SpilloverAnalysisEngine → ForecastEngine → MonteCarloEngine(seed=42) →
             ImpactScoringEngine → RiskEngine
        Return UpstreamEngineOutputs.
        """
        metrics = MetricsEngine(state).calculate()
        dag = DependencyGraphEngine(state).build_dag()
        cp_result = CriticalPathEngine(state, dag).analyze()
        spillover = SpilloverAnalysisEngine(state, metrics.average_item_effort).analyze()
        forecast = ForecastEngine(state, metrics, cp_result, spillover).calculate()
        monte_carlo = MonteCarloEngine(
            project_state=state,
            metrics=metrics,
            cp_result=cp_result,
            spillover=spillover,
            simulation_count=simulation_count,
            seed=self.SEED,
        ).calculate()
        impact_scores = ImpactScoringEngine(state, dag).score()
        risk_result = RiskEngine(
            project_state=state,
            metrics=metrics,
            cp_result=cp_result,
            dag=dag,
            spillover=spillover,
            forecast=forecast,
            monte_carlo=monte_carlo,
            impact_scores=impact_scores,
        ).analyze()
        return UpstreamEngineOutputs(
            metrics=metrics,
            dag=dag,
            cp_result=cp_result,
            spillover=spillover,
            forecast=forecast,
            monte_carlo=monte_carlo,
            impact_scores=impact_scores,
            risk_result=risk_result,
        )


class SimulationEngineV2:
    SEED: int = MONTE_CARLO_SEED

    def __init__(
        self,
        project_state: ProjectState,
        baseline: UpstreamEngineOutputs,
        simulation_count: int = 1000,
    ):
        self.project_state = project_state
        self.baseline = baseline
        self.simulation_count = simulation_count
        self.applicator = ActionApplicator()
        self.runner = EngineRunner()

    def simulate(self, recommendation: Recommendation) -> SimulationResult:
        """Deep clone → apply → re-run pipeline → compute deltas."""
        cloned_state = self.project_state.model_copy(deep=True)
        self.applicator.apply(cloned_state, recommendation)
        simulated = self.runner.run(cloned_state, simulation_count=self.simulation_count)
        return self._compute_result([recommendation.recommendation_id], simulated)

    def simulate_scenario(self, recommendations: List[Recommendation]) -> SimulationResult:
        """Deep clone → apply all (sorted by ID) → re-run pipeline → compute deltas."""
        cloned_state = self.project_state.model_copy(deep=True)
        self.applicator.apply_many(cloned_state, recommendations)
        simulated = self.runner.run(cloned_state, simulation_count=self.simulation_count)
        return self._compute_result([r.recommendation_id for r in sorted(recommendations, key=lambda r: r.recommendation_id)], simulated)

    def _compute_result(
        self,
        rec_ids: List[str],
        simulated: UpstreamEngineOutputs,
    ) -> SimulationResult:
        """Compute delta fields. baseline comes from self.baseline."""
        baseline_metrics = BaselineMetrics(
            on_time_probability=self.baseline.monte_carlo.on_time_probability,
            expected_delay_days=self.baseline.forecast.expected_delay_days,
            overall_risk_score=self.baseline.risk_result.overall_risk_score,
            schedule_risk=self.baseline.risk_result.schedule_risk.score,
            resource_risk=self.baseline.risk_result.resource_risk.score,
            critical_path_hours=self.baseline.cp_result.critical_path_duration_hours,
        )
        simulated_metrics = SimulatedMetrics(
            on_time_probability=simulated.monte_carlo.on_time_probability,
            expected_delay_days=simulated.forecast.expected_delay_days,
            overall_risk_score=simulated.risk_result.overall_risk_score,
            schedule_risk=simulated.risk_result.schedule_risk.score,
            resource_risk=simulated.risk_result.resource_risk.score,
            critical_path_hours=simulated.cp_result.critical_path_duration_hours,
        )
        delta_on_time_probability = simulated_metrics.on_time_probability - baseline_metrics.on_time_probability
        delta_expected_delay_days = baseline_metrics.expected_delay_days - simulated_metrics.expected_delay_days
        # Compute spillover delta as the change in total predicted spillover items
        try:
            baseline_spill = sum(self.baseline.spillover.predicted_spillover_by_sprint.values())
        except Exception:
            baseline_spill = 0.0
        try:
            simulated_spill = sum(simulated.spillover.predicted_spillover_by_sprint.values())
        except Exception:
            simulated_spill = 0.0
        delta_spillover_risk = float(baseline_spill - simulated_spill)
        delta_risk_score = baseline_metrics.overall_risk_score - simulated_metrics.overall_risk_score
        # Projected velocity delta (positive means velocity recovered)
        try:
            baseline_velocity = float(self.baseline.forecast.projected_velocity)
        except Exception:
            baseline_velocity = 0.0
        try:
            simulated_velocity = float(simulated.forecast.projected_velocity)
        except Exception:
            simulated_velocity = 0.0
        delta_projected_velocity = simulated_velocity - baseline_velocity
        is_positive_impact = (
            delta_on_time_probability > 0
            or delta_expected_delay_days > 0
            or delta_risk_score > 0
        )
        return SimulationResult(
            recommendation_ids=rec_ids,
            baseline_metrics=baseline_metrics,
            simulated_metrics=simulated_metrics,
            delta_on_time_probability=round(delta_on_time_probability, 4),
            delta_expected_delay_days=round(delta_expected_delay_days, 4),
            delta_spillover_risk=round(delta_spillover_risk, 4),
            delta_risk_score=round(delta_risk_score, 4),
            delta_projected_velocity=round(delta_projected_velocity, 2),
            seed_used=self.SEED,
            is_positive_impact=is_positive_impact,
            summary=(
                f"Applied {len(rec_ids)} recommendation(s); "
                f"on-time probability delta={round(delta_on_time_probability, 4)}"
            ),
        )
