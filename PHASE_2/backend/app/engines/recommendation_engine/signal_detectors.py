from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.domain.models import Blocker, ProjectState, SprintStatus, WorkItemStatus
from app.engines.critical_path_engine import CriticalPathResult
from app.engines.dependency_engine import DependencyDAG
from app.engines.forecast_engine import ForecastResult
from app.engines.impact_scoring_engine import RiskScores
from app.engines.metrics_engine import ProjectMetrics
from app.engines.monte_carlo_engine import MonteCarloResult
from app.engines.risk_engine import RiskResult
from app.engines.spillover_engine import SpilloverAnalysis
from app.engines.recommendations.models import (
    OpportunitySignal,
    SignalCategory,
    SignalEvidence,
    SignalSeverity,
    signal_id,
)


class BlockerDetector:
    def __init__(
        self,
        project_state: ProjectState,
        cp_result: CriticalPathResult,
        dag: DependencyDAG,
        impact_scores: RiskScores,
    ) -> None:
        self.project_state = project_state
        self.cp_result = cp_result
        self.dag = dag
        self.impact_scores = impact_scores

    def detect(self) -> List[OpportunitySignal]:
        signals: List[OpportunitySignal] = []
        active_blockers = [b for b in self.project_state.blockers if not getattr(b, "actual_resolution_date", None)]
        if not active_blockers:
            return signals

        for blocker in active_blockers:
            impacted_ids = list(getattr(blocker, "impacted_item_ids", []) or [])
            cascade_ids = self._cascade_item_ids(impacted_ids)
            blocked_hours = sum(
                float(next((wi.remaining_effort_hrs for wi in self.project_state.work_items if wi.item_id == item_id), 0.0))
                for item_id in impacted_ids
            )
            on_cp = any(item_id in self.cp_result.items_on_critical_path for item_id in impacted_ids)
            severity = SignalSeverity.CRITICAL if on_cp else SignalSeverity.HIGH
            if not on_cp and len(cascade_ids) < 3:
                severity = SignalSeverity.MEDIUM

            context: Dict[str, Any] = {
                "blocker_id": blocker.blocker_id,
                "category": getattr(blocker, "category", None),
                "severity": getattr(blocker, "severity", None),
                "impacted_item_ids": impacted_ids,
                "cascade_item_ids": cascade_ids,
                "blocked_hours": round(blocked_hours, 2),
                "on_critical_path": on_cp,
                "days_until_target_resolution": self._days_until_resolution(blocker),
                "sprint_gate_pct": round(blocked_hours / max(1.0, self._sprint_capacity_hours()), 4),
                "affected_sprint_numbers": self._affected_sprint_numbers(impacted_ids),
            }
            evidence = [
                SignalEvidence(
                    source_engine="critical_path_engine",
                    metric_name="impacted_items_on_cp",
                    metric_value=float(on_cp),
                    threshold=1.0,
                    explanation="Active blocker affects critical path items",
                )
            ]
            signal = OpportunitySignal(
                signal_id=signal_id(SignalCategory.BLOCKER, [blocker.blocker_id]),
                category=SignalCategory.BLOCKER,
                severity=severity,
                affected_item_ids=impacted_ids,
                affected_resource_ids=[],
                affected_sprint_ids=self._affected_sprint_ids(impacted_ids),
                affected_blocker_ids=[blocker.blocker_id],
                evidence=evidence,
                context=context,
                detected_at=datetime.now(timezone.utc).isoformat(),
            )
            signals.append(signal)
        return signals

    def _cascade_item_ids(self, impacted_item_ids: List[str]) -> List[str]:
        cascade: set[str] = set()
        for item_id in impacted_item_ids:
            for descendant in self.dag.transitive_closure.get(item_id, []):
                cascade.add(descendant)
        return sorted(cascade)

    def _days_until_resolution(self, blocker: Blocker) -> int:
        target = getattr(blocker, "target_resolution_date", None)
        raised = getattr(blocker, "raised_date", None)
        if not target or not raised:
            return 0
        return max(0, (target - raised).days)

    def _sprint_capacity_hours(self) -> float:
        sprint = next((s for s in self.project_state.sprints if getattr(s, "status", None) == SprintStatus.IN_PROGRESS), None)
        if sprint:
            return float(getattr(sprint, "planned_velocity_hrs", 0.0) or 0.0)
        return max(1.0, float(sum(getattr(s, "planned_velocity_hrs", 0.0) or 0.0 for s in self.project_state.sprints)))

    def _affected_sprint_numbers(self, affected_item_ids: List[str]) -> List[int]:
        sprint_numbers = []
        for item_id in affected_item_ids:
            work_item = next((wi for wi in self.project_state.work_items if wi.item_id == item_id), None)
            if work_item and getattr(work_item, "assigned_sprint", None):
                sprint = next((s for s in self.project_state.sprints if s.sprint_id == work_item.assigned_sprint), None)
                if sprint is not None:
                    sprint_numbers.append(sprint.sprint_number)
        return sorted(set(sprint_numbers))

    def _affected_sprint_ids(self, affected_item_ids: List[str]) -> List[str]:
        sprint_ids = []
        for item_id in affected_item_ids:
            work_item = next((wi for wi in self.project_state.work_items if wi.item_id == item_id), None)
            if work_item and getattr(work_item, "assigned_sprint", None):
                sprint_ids.append(work_item.assigned_sprint)
        return sorted(set(sprint_ids))


class CapacityDetector:
    def __init__(
        self,
        project_state: ProjectState,
        metrics: ProjectMetrics,
        cp_result: CriticalPathResult,
        impact_scores: RiskScores,
    ) -> None:
        self.project_state = project_state
        self.metrics = metrics
        self.cp_result = cp_result
        self.impact_scores = impact_scores

    def detect(self) -> List[OpportunitySignal]:
        signals: List[OpportunitySignal] = []
        for resource in self.project_state.team:
            load_ratio = self._load_ratio(resource)
            if 0.4 <= load_ratio <= 1.2:
                continue
            if resource.resource_id is None:
                continue
            flag = "OVERLOADED" if load_ratio > 1.2 else "UNDERUTILIZED"
            cp_items_owned = [
                wi.item_id for wi in self.project_state.work_items if wi.assigned_resource == resource.resource_id and wi.item_id in self.cp_result.items_on_critical_path
            ]
            context: Dict[str, Any] = {
                "resource_id": resource.resource_id,
                "load_ratio": round(load_ratio, 4),
                "assigned_remaining_hrs": round(self._assigned_remaining_hours(resource.resource_id), 2),
                "effective_remaining_capacity_hrs": round(self._effective_remaining_capacity(resource), 2),
                "flag": flag,
                "cp_items_owned": cp_items_owned,
                "is_single_owner_of_cp": len(cp_items_owned) > 0 and len(cp_items_owned) == 1,
                "owns_blocked_cp_items": any(item_id in self._blocked_cp_items() for item_id in cp_items_owned),
            }
            evidence = [
                SignalEvidence(
                    source_engine="metrics_engine",
                    metric_name="load_ratio",
                    metric_value=load_ratio,
                    threshold=1.2,
                    explanation="Resource load ratio exceeds the planned threshold",
                )
            ]
            severity = SignalSeverity.HIGH if context["owns_blocked_cp_items"] else SignalSeverity.MEDIUM
            if flag == "UNDERUTILIZED":
                severity = SignalSeverity.LOW
            signal = OpportunitySignal(
                signal_id=signal_id(SignalCategory.CAPACITY, [resource.resource_id]),
                category=SignalCategory.CAPACITY,
                severity=severity,
                affected_item_ids=[wi.item_id for wi in self.project_state.work_items if wi.assigned_resource == resource.resource_id],
                affected_resource_ids=[resource.resource_id],
                affected_sprint_ids=self._resource_sprint_ids(resource.resource_id),
                affected_blocker_ids=[],
                evidence=evidence,
                context=context,
                detected_at=datetime.now(timezone.utc).isoformat(),
            )
            signals.append(signal)
        return signals

    def _load_ratio(self, resource: Any) -> float:
        """
        Compute load ratio from developer metrics in ProjectMetrics.
        Load ratio = assigned_remaining_hrs / effective_remaining_capacity
        
        Consumes directly from upstream metrics engine to avoid duplication.
        """
        # Find this developer in the metrics
        dev_metric = next(
            (dm for dm in self.metrics.resource_metrics.developer_metrics 
             if dm.resource_id == resource.resource_id),
            None
        )
        
        if dev_metric is None:
            # Fallback if developer not found in metrics (shouldn't happen)
            return 0.0
        
        # Use the developer's remaining effort from metrics
        assigned_hours = dev_metric.remaining_effort_hours
        
        # Capacity: use forecast_input_metrics for remaining capacity calculation
        # This accounts for remaining sprints and resource availability/allocation
        remaining_sprints = float(
            self.metrics.forecast_input_metrics.remaining_sprints or 1
        )
        capacity_per_sprint = float(
            resource.daily_capacity_hrs or 0.0 
        ) * (self.project_state.project_info.sprint_duration_days or 10)
        
        # Apply resource availability and allocation
        availability = float(getattr(resource, "availability_pct", 1.0) or 1.0)
        allocation = float(getattr(resource, "allocation_pct", 1.0) or 1.0)
        effective_capacity = capacity_per_sprint * remaining_sprints * availability * allocation
        
        return assigned_hours / max(effective_capacity, 1.0)

    def _assigned_remaining_hours(self, resource_id: str) -> float:
        """
        Get remaining hours assigned to resource from developer metrics.
        
        Consumes from ProjectMetrics.resource_metrics instead of recalculating.
        """
        dev_metric = next(
            (dm for dm in self.metrics.resource_metrics.developer_metrics 
             if dm.resource_id == resource_id),
            None
        )
        return dev_metric.remaining_effort_hours if dev_metric else 0.0

    def _effective_remaining_capacity(self, resource: Any) -> float:
        """
        Calculate effective remaining capacity using forecast_input_metrics.
        
        Consumes from ProjectMetrics.forecast_input_metrics to avoid duplication
        of capacity calculations already done by MetricsEngine.
        """
        remaining_sprints = float(
            self.metrics.forecast_input_metrics.remaining_sprints or 1
        )
        capacity_per_sprint = float(
            resource.daily_capacity_hrs or 0.0 
        ) * (self.project_state.project_info.sprint_duration_days or 10)
        
        # Apply resource availability and allocation
        availability = float(getattr(resource, "availability_pct", 1.0) or 1.0)
        allocation = float(getattr(resource, "allocation_pct", 1.0) or 1.0)
        effective_capacity = capacity_per_sprint * remaining_sprints * availability * allocation
        
        return max(1.0, effective_capacity)

    def _blocked_cp_items(self) -> List[str]:
        active_blockers = [b for b in self.project_state.blockers if not getattr(b, "actual_resolution_date", None)]
        blocked_items = set()
        for blocker in active_blockers:
            blocked_items.update(getattr(blocker, "impacted_item_ids", []) or [])
        return [item_id for item_id in blocked_items if item_id in self.cp_result.items_on_critical_path]

    def _resource_sprint_ids(self, resource_id: str) -> List[str]:
        sprint_ids = []
        for wi in self.project_state.work_items:
            if wi.assigned_resource == resource_id:
                sprint_ids.append(wi.assigned_sprint)
        return sorted(set(sprint_ids))


class SprintDetector:
    def __init__(
        self,
        project_state: ProjectState,
        metrics: ProjectMetrics,
        spillover: SpilloverAnalysis,
        forecast: ForecastResult,
    ) -> None:
        self.project_state = project_state
        self.metrics = metrics
        self.spillover = spillover
        self.forecast = forecast

    def detect(self) -> List[OpportunitySignal]:
        signals: List[OpportunitySignal] = []
        
        # Consume sprint metrics from ProjectMetrics instead of recalculating
        for sprint_metric in self.metrics.sprint_metrics:
            sprint = next(
                (s for s in self.project_state.sprints if s.sprint_id == sprint_metric.sprint_id),
                None
            )
            
            if sprint is None or getattr(sprint, "status", None) == SprintStatus.COMPLETED:
                continue
            
            # Use utilization from metrics instead of recalculating
            utilization_ratio = sprint_metric.completion_pct
            planned_hours = sprint_metric.planned_effort_hours
            capacity_hours = sprint_metric.planned_effort_hours
            
            # Detect under/overloaded sprints
            flag = None
            if utilization_ratio < 0.5 and sprint_metric.sprint_number != self.metrics.current_sprint_number:
                flag = "UNDERLOADED"
            elif utilization_ratio > 1.1:
                flag = "OVERLOADED"
            else:
                continue
            
            # Get blocked hours from blocker metrics if available
            blocked_hours = 0.0
            blocked_items = [
                wi.item_id for wi in self.project_state.work_items
                if getattr(wi, "assigned_sprint", None) == sprint_metric.sprint_id
                and getattr(wi, "status", None) == WorkItemStatus.BLOCKED
            ]
            for item_id in blocked_items:
                wi = next(
                    (w for w in self.project_state.work_items if w.item_id == item_id),
                    None
                )
                if wi:
                    blocked_hours += float(getattr(wi, "remaining_effort_hrs", 0.0) or 0.0)
            
            # Get spillover probability from spillover analysis
            spillover_prob = 0.0
            if self.spillover:
                spill_by_sprint = getattr(self.spillover, "predicted_spillover_by_sprint", {}) or {}
                if isinstance(spill_by_sprint, dict):
                    spillover_prob = float(spill_by_sprint.get(sprint_metric.sprint_number, 0.0))
            
            context: Dict[str, Any] = {
                "sprint_id": sprint_metric.sprint_id,
                "sprint_number": sprint_metric.sprint_number,
                "flag": flag,
                "utilization_ratio": round(utilization_ratio, 4),
                "planned_hours": round(planned_hours, 2),
                "capacity_hours": round(capacity_hours, 2),
                "actual_effort_hours": round(sprint_metric.actual_effort_hours, 2),
                "blocked_hours": round(blocked_hours, 2),
                "blocked_pct": round(blocked_hours / max(planned_hours, 1.0), 4),
                "spillover_probability": round(spillover_prob, 4),
                "is_cp_sprint": self._is_cp_sprint(sprint_metric.sprint_id),
            }
            
            evidence = [
                SignalEvidence(
                    source_engine="spillover_engine",
                    metric_name="predicted_spillover",
                    metric_value=spillover_prob,
                    threshold=0.6,
                    explanation="Sprint spillover risk is elevated",
                )
            ]
            
            signals.append(
                OpportunitySignal(
                    signal_id=signal_id(SignalCategory.SPRINT, [sprint_metric.sprint_id]),
                    category=SignalCategory.SPRINT,
                    severity=SignalSeverity.MEDIUM,
                    affected_item_ids=self._items_in_sprint(sprint_metric.sprint_id),
                    affected_resource_ids=[],
                    affected_sprint_ids=[sprint_metric.sprint_id],
                    affected_blocker_ids=[],
                    evidence=evidence,
                    context=context,
                    detected_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        
        return signals

    def _items_in_sprint(self, sprint_id: str) -> List[str]:
        return [wi.item_id for wi in self.project_state.work_items if getattr(wi, "assigned_sprint", None) == sprint_id]

    def _is_cp_sprint(self, sprint_id: str) -> bool:
        """Check if any CP items are assigned to this sprint."""
        return any(
            wi.item_id in self.forecast.items_on_critical_path 
            if hasattr(self.forecast, 'items_on_critical_path') else False
            for wi in self.project_state.work_items
            if getattr(wi, "assigned_sprint", None) == sprint_id
        )


class CriticalPathDetector:
    def __init__(
        self,
        project_state: ProjectState,
        cp_result: CriticalPathResult,
        dag: DependencyDAG,
        impact_scores: RiskScores,
    ) -> None:
        self.project_state = project_state
        self.cp_result = cp_result
        self.dag = dag
        self.impact_scores = impact_scores

    def detect(self) -> List[OpportunitySignal]:
        signals: List[OpportunitySignal] = []
        active_blockers = [b for b in self.project_state.blockers if not getattr(b, "actual_resolution_date", None)]
        blocked_cp_items = []
        for blocker in active_blockers:
            for item_id in getattr(blocker, "impacted_item_ids", []) or []:
                if item_id in self.cp_result.items_on_critical_path:
                    blocked_cp_items.append(item_id)
        if blocked_cp_items:
            signal = OpportunitySignal(
                signal_id=signal_id(SignalCategory.CRITICAL_PATH, sorted(set(blocked_cp_items))),
                category=SignalCategory.CRITICAL_PATH,
                severity=SignalSeverity.CRITICAL,
                affected_item_ids=sorted(set(blocked_cp_items)),
                affected_resource_ids=[],
                affected_sprint_ids=self._affected_sprint_ids(sorted(set(blocked_cp_items))),
                affected_blocker_ids=[b.blocker_id for b in active_blockers if any(item_id in getattr(b, 'impacted_item_ids', []) or [] for item_id in blocked_cp_items)],
                evidence=[
                    SignalEvidence(
                        source_engine="critical_path_engine",
                        metric_name="cp_at_risk",
                        metric_value=float(len(blocked_cp_items)),
                        threshold=1.0,
                        explanation="Critical path items are affected by active blockers",
                    )
                ],
                context={
                    "cp_nodes": sorted(set(blocked_cp_items)),
                    "cp_remaining_hours": round(self._cp_remaining_hours(sorted(set(blocked_cp_items))), 2),
                    "cp_single_owners": self._cp_single_owners(sorted(set(blocked_cp_items))),
                    "cp_blocked_items": sorted(set(blocked_cp_items)),
                    "near_critical_items": self._near_critical_items(),
                    "dependency_bottleneck_item_ids": self._dependency_bottlenecks(),
                    "flag": "CP_AT_RISK",
                },
                detected_at=datetime.now(timezone.utc).isoformat(),
            )
            signals.append(signal)

        return signals

    def _affected_sprint_ids(self, item_ids: List[str]) -> List[str]:
        sprint_ids = []
        for item_id in item_ids:
            work_item = next((wi for wi in self.project_state.work_items if wi.item_id == item_id), None)
            if work_item and getattr(work_item, "assigned_sprint", None):
                sprint_ids.append(work_item.assigned_sprint)
        return sorted(set(sprint_ids))

    def _cp_remaining_hours(self, item_ids: List[str]) -> float:
        return sum(float(next((wi.remaining_effort_hrs for wi in self.project_state.work_items if wi.item_id == item_id), 0.0)) for item_id in item_ids)

    def _cp_single_owners(self, item_ids: List[str]) -> List[str]:
        owners = []
        for item_id in item_ids:
            work_item = next((wi for wi in self.project_state.work_items if wi.item_id == item_id), None)
            if work_item and getattr(work_item, "assigned_resource", None):
                owners.append(work_item.assigned_resource)
        return sorted(set(owners))

    def _near_critical_items(self) -> List[str]:
        sprint_duration_hours = self.project_state.project_info.sprint_duration_days * 24.0
        threshold = 0.25 * sprint_duration_hours
        near = []
        slack_map = getattr(self.cp_result, "item_slack_map", {}) or {}
        for each in slack_map:
            if slack_map[each] <= threshold:
                near.append(each)
        return sorted(near)

    def _dependency_bottlenecks(self) -> List[str]:
        reverse_counts: Dict[str, int] = {}
        for node, successors in self.dag.graph.items():
            for successor in successors:
                if successor in self.cp_result.items_on_critical_path:
                    reverse_counts[successor] = reverse_counts.get(successor, 0) + 1
        return [item_id for item_id, count in sorted(reverse_counts.items()) if count >= 3]


class ScheduleDetector:
    """
    Detects schedule-related signals by consuming ForecastResult directly.
    
    This detector avoids recalculating schedule gaps, velocity trends, or other
    forecast-related metrics. Instead, it consumes the output from ForecastEngine,
    which is the single source of truth for schedule forecasts.
    """
    
    def __init__(
        self,
        project_state: ProjectState,
        forecast: ForecastResult,
        monte_carlo: MonteCarloResult,
        risk_result: RiskResult,
        metrics: ProjectMetrics,
    ) -> None:
        self.project_state = project_state
        self.forecast = forecast
        self.monte_carlo = monte_carlo
        self.risk_result = risk_result
        self.metrics = metrics

    def _schedule_gap_hours(self) -> float:
        """
        Extract schedule gap from ForecastResult breakdown.
        
        Consumes from ForecastResult.delay_breakdown instead of recalculating.
        """
        if hasattr(self.forecast, "delay_breakdown") and self.forecast.delay_breakdown:
            return float(self.forecast.delay_breakdown.expected_delay_days * 8.0)
        
        # Fallback: use expected_delay_days directly
        expected_delay = float(getattr(self.forecast, "expected_delay_days", 0.0) or 0.0)
        return max(0.0, expected_delay * 8.0)

    def _velocity_trend(self) -> Optional[float]:
        """
        Extract velocity trend from velocity_metrics in ProjectMetrics.
        
        Consumes from ProjectMetrics.velocity_metrics instead of recalculating.
        """
        return float(getattr(self.metrics.velocity_metrics, "velocity_trend_pct", None) or 0.0)

    def _highest_effort_not_started_items(self, limit: int = 3) -> List[str]:
        """Find highest-effort not-started items to populate as affected items."""
        not_started_items = []
        for wi in self.project_state.work_items:
            status = getattr(wi, "status", None)
            # Skip if already started or completed
            if status in (WorkItemStatus.IN_PROGRESS, WorkItemStatus.COMPLETED, 
                         WorkItemStatus.DONE, WorkItemStatus.BLOCKED, WorkItemStatus.SPILLOVER):
                continue
            effort = float(getattr(wi, "current_estimate_hrs", 0.0) or 0.0)
            if effort == 0.0:
                effort = float(wi.remaining_effort_hrs or 0.0)
            if effort > 0.0:
                not_started_items.append((wi.item_id, effort))
        
        # Sort by effort descending and return top N
        not_started_items.sort(key=lambda x: x[1], reverse=True)
        return [item_id for item_id, _ in not_started_items[:limit]]

    def detect(self) -> List[OpportunitySignal]:
        """
        Detect schedule-related signals from ForecastResult.
        
        Key signals:
        - SCHEDULE_GAP: when expected delay > 0
        - VELOCITY_CONCERN: when velocity is degrading or uncertain
        - SCOPE_CREEP: when scope inflation is detected
        """
        signals: List[OpportunitySignal] = []
        
        # Extract schedule gap from forecast breakdown
        schedule_gap_hours = self._schedule_gap_hours()
        velocity_trend = self._velocity_trend()
        scope_growth_hours = float(getattr(self.forecast, "scope_growth_hours", 0.0) or 0.0)
        
        # Get affected items
        affected_items = self._highest_effort_not_started_items(limit=3)
        
        # Signal 1: Schedule gap (primary signal)
        if self.forecast.expected_delay_days > 0:
            delay_breakdown = self.forecast.delay_breakdown if hasattr(self.forecast, "delay_breakdown") else None
            remaining_days_base = float(delay_breakdown.remaining_days_base_work) if delay_breakdown else 0.0
            remaining_days_blocker = float(delay_breakdown.remaining_days_blocker_loss) if delay_breakdown else 0.0
            remaining_days_spillover = float(delay_breakdown.remaining_days_spillover) if delay_breakdown else 0.0
            
            context: Dict[str, Any] = {
                "schedule_gap_hours": schedule_gap_hours,
                "expected_delay_days": round(self.forecast.expected_delay_days, 2),
                "on_track": self.forecast.on_track,
                "flag": "SCHEDULE_AT_RISK",
                "delay_breakdown": {
                    "base_work_days": round(remaining_days_base, 2),
                    "blocker_loss_days": round(remaining_days_blocker, 2),
                    "spillover_days": round(remaining_days_spillover, 2),
                },
                "scope_growth_hours": round(scope_growth_hours, 2),
                "velocity_trend_pct": round(velocity_trend, 2) if velocity_trend else 0.0,
            }
            
            evidence = [
                SignalEvidence(
                    source_engine="forecast_engine",
                    metric_name="expected_delay_days",
                    metric_value=self.forecast.expected_delay_days,
                    threshold=0.0,
                    explanation="Forecast indicates schedule delay",
                )
            ]
            
            if velocity_trend and velocity_trend < 0:
                evidence.append(SignalEvidence(
                    source_engine="metrics_engine",
                    metric_name="velocity_trend_pct",
                    metric_value=velocity_trend,
                    threshold=0.0,
                    explanation="Velocity is degrading over time",
                ))
                context["velocity_degrading"] = True
            
            signals.append(
                OpportunitySignal(
                    signal_id=signal_id(SignalCategory.SCHEDULE, ["schedule_gap"]),
                    category=SignalCategory.SCHEDULE,
                    severity=SignalSeverity.HIGH,
                    affected_item_ids=affected_items,
                    affected_resource_ids=[],
                    affected_sprint_ids=[],
                    affected_blocker_ids=[],
                    evidence=evidence,
                    context=context,
                    detected_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        
        # Signal 2: Scope creep (secondary signal if scope is growing)
        if scope_growth_hours > 0:
            context: Dict[str, Any] = {
                "scope_inflation_hours": round(scope_growth_hours, 2),
                "scope_inflation_pct": round(
                    (scope_growth_hours / self.forecast.raw_remaining_effort_hours * 100.0)
                    if self.forecast.raw_remaining_effort_hours > 0 else 0.0,
                    2
                ),
                "flag": "SCOPE_CREEP",
            }
            
            signals.append(
                OpportunitySignal(
                    signal_id=signal_id(SignalCategory.SCHEDULE, ["scope_creep"]),
                    category=SignalCategory.SCHEDULE,
                    severity=SignalSeverity.MEDIUM,
                    affected_item_ids=affected_items[:1],  # Top affected item
                    affected_resource_ids=[],
                    affected_sprint_ids=[],
                    affected_blocker_ids=[],
                    evidence=[SignalEvidence(
                        source_engine="forecast_engine",
                        metric_name="scope_growth_hours",
                        metric_value=scope_growth_hours,
                        threshold=0.0,
                        explanation="Scope growth is impacting the schedule",
                    )],
                    context=context,
                    detected_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        
        return signals
