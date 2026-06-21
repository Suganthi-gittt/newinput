from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.api.models_phase3 import RecommendationType
from app.domain.models import (
    ProjectState,
    Resource,
    Sprint,
    SprintActual,
    WorkItem,
    Blocker,
    SkillLevel,
    SprintStatus,
    WorkItemStatus,
    BlockerStatus,
)
from app.engines.dependency_engine import DependencyGraphEngine, DependencyDAG
from app.engines.critical_path_engine import CriticalPathEngine, CriticalPathResult
from app.engines.impact_scoring_engine import ImpactScoringEngine, RiskScores
from app.engines.metrics_engine import MetricsEngine, ProjectMetrics
from app.engines.monte_carlo_engine import MonteCarloEngine, MonteCarloResult
from app.engines.risk_engine import RiskEngine, RiskResult
from app.engines.spillover_engine import SpilloverAnalysisEngine, SpilloverAnalysis
from app.engines.forecast_engine import ForecastEngine, ForecastResult


class SimulationAction(BaseModel):
    action_id: str = Field(..., description="Recommendation identifier for this action")
    action_type: str = Field(..., description="Action type or recommendation type value")
    target_ids: List[str] = Field(default_factory=list, description="Target entity IDs")
    details: Dict[str, Any] = Field(default_factory=dict, description="Structured details for the action")
    impact_reason: str = Field(..., description="Reason why this action will affect the project")


class SimulationScenario(BaseModel):
    selected_recommendations: List[str] = Field(..., description="Selected recommendation IDs for simulation")


class SimulationResult(BaseModel):
    baseline_finish_date: datetime
    simulated_finish_date: datetime
    baseline_risk_score: float
    simulated_risk_score: float
    baseline_p80_date: datetime
    simulated_p80_date: datetime
    baseline_critical_path_hours: float
    simulated_critical_path_hours: float
    days_recovered: float
    risk_reduction: float
    recommendations_applied: List[str]
    action_reasons: List[str]
    baseline_probability: float
    simulated_probability: float
    baseline_delay_days: float
    simulated_delay_days: float


class SimulationEngine:
    """Runs deterministic simulation of recommendation actions using existing engines."""

    def __init__(
        self,
        project_state: ProjectState,
        metrics: ProjectMetrics,
        dag: DependencyDAG,
        cp_result: CriticalPathResult,
        spillover: SpilloverAnalysis,
        forecast: ForecastResult,
        monte_carlo: MonteCarloResult,
        risk_result: RiskResult,
        simulation_count: int = 1000,
        seed: Optional[int] = None,
    ):
        self.project_state = project_state
        self.metrics = metrics
        self.dag = dag
        self.cp_result = cp_result
        self.spillover = spillover
        self.forecast = forecast
        self.monte_carlo = monte_carlo
        self.risk_result = risk_result
        self.simulation_count = simulation_count
        self.seed = seed

    def simulate_recommendation_actions(
        self,
        actions: List[SimulationAction],
    ) -> SimulationResult:
        """Simulate the applied actions and compare results against baseline."""
        clone = self.project_state.model_copy(deep=True)
        for action in actions:
            self._apply_action(clone, action)

        simulated = self._recalculate_clone(clone)
        return self._build_result(actions, simulated)

    def _apply_action(self, clone: ProjectState, action: SimulationAction) -> None:
        """Apply a single simulation action using deterministic phase 1 effects."""
        action_type = action.action_type

        if action_type == RecommendationType.RESOLVE_BLOCKER.value:
            self._apply_resolve_blocker(clone, action)
        elif action_type == RecommendationType.REDUCE_ITEM_SCOPE.value:
            self._apply_reduce_scope(clone, action)
        elif action_type == RecommendationType.ADD_RESOURCE.value:
            self._apply_add_capacity(clone, action)
        elif action_type == RecommendationType.PARALLELIZE_TASKS.value:
            self._apply_parallelize_work(clone, action)
        elif action_type == RecommendationType.REASSIGN_WORK.value:
            self._apply_reassign_work(clone, action)
        elif action_type == RecommendationType.MOVE_BLOCKER_ITEMS.value:
            self._apply_move_blocker_items(clone, action)
        elif action_type == RecommendationType.SPLIT_TASK.value:
            self._apply_split_task(clone, action)
        elif action_type == RecommendationType.CRITICAL_PATH_OPTIMIZATION.value:
            self._apply_critical_path_optimization(clone, action)
        else:
            # Unsupported action types are ignored for simulation but preserved
            # in the output for traceability.
            return

    def _recalculate_clone(self, clone: ProjectState) -> Dict[str, Any]:
        metrics = MetricsEngine(clone).calculate()
        dag = DependencyGraphEngine(clone).build_dag()
        cp_result = CriticalPathEngine(clone, dag).analyze()
        spillover = SpilloverAnalysisEngine(clone, metrics.average_item_effort).analyze()
        forecast = ForecastEngine(clone, metrics, cp_result, spillover).calculate()
        monte_carlo = MonteCarloEngine(
            project_state=clone,
            metrics=metrics,
            cp_result=cp_result,
            spillover=spillover,
            simulation_count=self.simulation_count,
            seed=self.seed,
        ).calculate()
        impact_scores = ImpactScoringEngine(clone, dag).score()
        risk_result = RiskEngine(
            project_state=clone,
            metrics=metrics,
            cp_result=cp_result,
            dag=dag,
            spillover=spillover,
            forecast=forecast,
            monte_carlo=monte_carlo,
            impact_scores=impact_scores,
        ).analyze()

        return {
            "metrics": metrics,
            "dag": dag,
            "cp_result": cp_result,
            "spillover": spillover,
            "forecast": forecast,
            "monte_carlo": monte_carlo,
            "risk_result": risk_result,
        }

    def _build_result(self, actions: List[SimulationAction], simulated: Dict[str, Any]) -> SimulationResult:
        simulated_forecast: ForecastResult = simulated["forecast"]
        simulated_mc: MonteCarloResult = simulated["monte_carlo"]
        simulated_cp: CriticalPathResult = simulated["cp_result"]
        simulated_risk: RiskResult = simulated["risk_result"]

        baseline_finish_date = self.forecast.expected_finish_date
        simulated_finish_date = simulated_forecast.expected_finish_date
        baseline_p80_date = self.monte_carlo.p80_finish_date
        simulated_p80_date = simulated_mc.p80_finish_date
        baseline_cp_hours = self.cp_result.critical_path_duration_hours
        simulated_cp_hours = simulated_cp.critical_path_duration_hours
        baseline_risk_score = self.risk_result.overall_risk_score
        simulated_risk_score = simulated_risk.overall_risk_score

        return SimulationResult(
            baseline_finish_date=baseline_finish_date,
            simulated_finish_date=simulated_finish_date,
            baseline_risk_score=baseline_risk_score,
            simulated_risk_score=simulated_risk_score,
            baseline_p80_date=baseline_p80_date,
            simulated_p80_date=simulated_p80_date,
            baseline_critical_path_hours=baseline_cp_hours,
            simulated_critical_path_hours=simulated_cp_hours,
            days_recovered=float(round((baseline_finish_date - simulated_finish_date).days, 1)),
            risk_reduction=float(round(baseline_risk_score - simulated_risk_score, 2)),
            recommendations_applied=[action.action_id for action in actions],
            action_reasons=[action.impact_reason for action in actions],
            baseline_probability=self.monte_carlo.on_time_probability,
            simulated_probability=simulated_mc.on_time_probability,
            baseline_delay_days=self.forecast.expected_delay_days,
            simulated_delay_days=simulated_forecast.expected_delay_days,
        )

    def _apply_resolve_blocker(self, clone: ProjectState, action: SimulationAction) -> None:
        if not action.target_ids:
            return
        blocker_id = action.target_ids[0]
        blocker = next((b for b in clone.blockers if b.blocker_id == blocker_id), None)
        if not blocker:
            return
        blocker.status = BlockerStatus.RESOLVED if hasattr(BlockerStatus, "RESOLVED") else blocker.status
        blocker.actual_resolution_date = datetime.utcnow()

    def _apply_reduce_scope(self, clone: ProjectState, action: SimulationAction) -> None:
        if not action.target_ids:
            return
        item_id = action.target_ids[0]
        item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
        if not item:
            return
        core_hours = float(action.details.get("core_hours", item.current_estimate_hrs * 0.6))
        reduction = max(0.0, item.current_estimate_hrs - core_hours)
        item.current_estimate_hrs = max(0.0, core_hours)
        item.remaining_effort_hrs = max(0.0, item.remaining_effort_hrs - reduction)
        item.is_scope_changed = True
        item.scope_change_reason = (
            f"Simulation scope reduction: retained {item.current_estimate_hrs:.1f}h and deferred {reduction:.1f}h."
        )

    def _apply_add_capacity(self, clone: ProjectState, action: SimulationAction) -> None:
        skill = action.details.get("skill", "General")
        role = action.details.get("role", "Capacity Resource")
        resource_id = f"SIM-R-{len(clone.team)+1}"
        skill_level_value = action.details.get("skill_level", None)
        if skill_level_value and skill_level_value in SkillLevel.__members__:
            level = SkillLevel[skill_level_value]
        else:
            level = SkillLevel.MID

        clone.team.append(
            Resource(
                resource_id=resource_id,
                name=f"Simulated {role}",
                role=role,
                primary_skill=skill,
                secondary_skill=None,
                skill_level=level,
                allocation_pct=0.5,
                availability_pct=1.0,
                daily_capacity_hrs=8.0,
            )
        )

        additional_capacity = float(action.details.get("capacity_gain_hours", 20.0))
        for sprint in clone.sprints:
            if sprint.status in {SprintStatus.NOT_STARTED, SprintStatus.IN_PROGRESS}:
                sprint.planned_velocity_hrs += additional_capacity

        existing_actuals = [a.actual_effort_hrs for a in clone.actuals if a.actual_effort_hrs is not None]
        avg_actual_velocity = sum(existing_actuals) / len(existing_actuals) if existing_actuals else additional_capacity
        synthetic_actual_value = max(additional_capacity, avg_actual_velocity + additional_capacity * 0.5)

        synthetic_actual = SprintActual(
            sprint_id=f"SIM-{resource_id}",
            sprint_number=max((s.sprint_number for s in clone.sprints), default=0) + 1,
            planned_effort_hrs=additional_capacity,
            actual_effort_hrs=synthetic_actual_value,
            variance_hrs=0.0,
            tasks_planned=0,
            tasks_completed=0,
            completion_rate=1.0,
            carryover_count=0,
            scope_change_hours=0.0,
            blocker_impact_hrs=0.0,
        )
        clone.actuals.append(synthetic_actual)

    def _apply_parallelize_work(self, clone: ProjectState, action: SimulationAction) -> None:
        if len(action.target_ids) < 2:
            return
        pred_id, succ_id = action.target_ids[0], action.target_ids[1]
        successor = next((wi for wi in clone.work_items if wi.item_id == succ_id), None)
        if successor:
            reduction = successor.current_estimate_hrs * 0.2
            successor.current_estimate_hrs = max(0.0, successor.current_estimate_hrs - reduction)
            successor.remaining_effort_hrs = max(0.0, successor.remaining_effort_hrs - reduction)

        dependency = next(
            (
                d
                for d in clone.dependencies
                if d.predecessor_item_id == pred_id and d.successor_item_id == succ_id
            ),
            None,
        )
        if dependency and dependency.lag_days > 0:
            dependency.lag_days = max(0, dependency.lag_days - 1)

    def _apply_reassign_work(self, clone: ProjectState, action: SimulationAction) -> None:
        if not action.target_ids:
            return
        item_id = action.target_ids[0]
        item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
        if not item:
            return
        new_resource_name = action.details.get("to")
        new_resource = next((r for r in clone.team if r.name == new_resource_name), None)
        if new_resource:
            item.assigned_resource = new_resource.resource_id
            new_resource.allocation_pct = min(1.0, new_resource.allocation_pct + 0.1)

    def _apply_move_blocker_items(self, clone: ProjectState, action: SimulationAction) -> None:
        for item_id in action.details.get("advanceable_items", []) or []:
            item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
            if item and item.status == WorkItemStatus.NOT_STARTED:
                item.status = WorkItemStatus.IN_PROGRESS

    def _apply_split_task(self, clone: ProjectState, action: SimulationAction) -> None:
        if not action.target_ids:
            return
        item_id = action.target_ids[0]
        item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
        if not item:
            return
        reduction = item.current_estimate_hrs * 0.15
        item.current_estimate_hrs = max(1.0, item.current_estimate_hrs - reduction)
        item.remaining_effort_hrs = max(0.0, item.remaining_effort_hrs - reduction)

    def _apply_critical_path_optimization(self, clone: ProjectState, action: SimulationAction) -> None:
        for item_id in action.target_ids:
            item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
            if not item:
                continue
            reduction = item.current_estimate_hrs * 0.15
            item.current_estimate_hrs = max(1.0, item.current_estimate_hrs - reduction)
            item.remaining_effort_hrs = max(0.0, item.remaining_effort_hrs - reduction)
