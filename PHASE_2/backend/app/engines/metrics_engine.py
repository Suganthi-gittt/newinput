"""
Project Metrics Engine

Calculates aggregate project health metrics from ProjectState.
"""

from datetime import datetime
from typing import List, Dict, Any
from pydantic import BaseModel

from app.domain.models import ProjectState, WorkItemStatus, SprintStatus, BlockerSeverity


class ProjectMetrics(BaseModel):
    """Aggregate project health metrics."""
    
    # Completion metrics
    total_items: int
    completed_items: int
    in_progress_items: int
    blocked_items: int
    not_started_items: int
    completion_pct: float
    
    # Work metrics
    total_effort_hours: float
    remaining_effort_hours: float
    completed_effort_hours: float
    average_item_effort: float
    
    # Velocity metrics
    planned_total_velocity: float
    actual_avg_velocity: float
    velocity_variance: float
    velocity_std_dev: float
    
    # Resource metrics
    team_size: int
    avg_allocation_pct: float
    avg_availability_pct: float
    underutilized_resource_count: int
    
    # Risk metrics
    blocker_count_by_severity: Dict[str, int]
    active_blocker_count: int
    estimated_blocker_velocity_impact: float  # 0.0-1.0, reduction factor
    
    # Schedule metrics
    project_start_date: datetime
    project_end_date: datetime
    current_sprint_number: int
    completed_sprints: int
    
    # Dependency metrics
    dependency_count: int
    critical_path_length: int
    
    # Spillover metrics
    #
    # NAMING CLARIFICATION (these two fields answer DIFFERENT questions and
    # are NOT alternative estimates of the same quantity — do not compare
    # them directly, and do not expect them to roughly agree):
    #
    # expected_spillover_items: despite the forward-looking name, this is
    # actually sum(a.carryover_count for a in actuals) — a HISTORICAL total
    # of how many items carried over across sprints that already have
    # recorded actuals (i.e. completed + current in-progress sprint). It is
    # backward-looking. Kept under its original name for backward
    # compatibility with existing callers; see historical_total_carryover_items
    # below for the same value under an accurate name.
    #
    # For the FORWARD-LOOKING prediction of how many items are expected to
    # spill across remaining/future sprints, use
    # SpilloverAnalysisEngine.predicted_spillover_by_sprint (summed) /
    # ForecastResult.predicted_spillover_items instead — that is a model
    # output over future sprints, not a historical tally.
    expected_spillover_items: int
    historical_total_carryover_items: int  # = expected_spillover_items, accurately named
    historical_carryover_rate: float


class MetricsEngine:
    """Calculates project metrics from ProjectState."""
    
    def __init__(self, project_state: ProjectState):
        self.project_state = project_state
    
    def calculate(self) -> ProjectMetrics:
        """Calculate all project metrics."""
        work_items = self.project_state.work_items
        sprints = self.project_state.sprints
        team = self.project_state.team
        blockers = self.project_state.blockers
        actuals = self.project_state.actuals
        
        # Item counts by status
        completed = sum(1 for wi in work_items if wi.status == WorkItemStatus.COMPLETED)
        in_progress = sum(1 for wi in work_items if wi.status == WorkItemStatus.IN_PROGRESS)
        blocked = sum(1 for wi in work_items if wi.status == WorkItemStatus.BLOCKED)
        not_started = sum(1 for wi in work_items if wi.status == WorkItemStatus.NOT_STARTED)
        total = len(work_items)
        
        # Effort metrics
        total_effort = sum(wi.estimated_effort_hrs for wi in work_items)
        completed_effort = sum(
            wi.estimated_effort_hrs for wi in work_items 
            if wi.status == WorkItemStatus.COMPLETED
        )
        remaining_effort = sum(wi.remaining_effort_hrs for wi in work_items)
        avg_item_effort = total_effort / total if total > 0 else 0.0
        
        # Velocity metrics
        planned_velocity = sum(s.planned_velocity_hrs for s in sprints if s.planned_velocity_hrs > 0)
        actual_velocities = [a.actual_effort_hrs for a in actuals if a.actual_effort_hrs > 0]
        actual_avg_velocity = sum(actual_velocities) / len(actual_velocities) if actual_velocities else 0.0
        velocity_variance = self._calculate_variance(actual_velocities)
        velocity_std_dev = velocity_variance ** 0.5 if velocity_variance >= 0 else 0.0
        
        # Resource metrics
        team_size = len(team)
        avg_allocation = sum(r.allocation_pct for r in team) / team_size if team_size > 0 else 0.0
        avg_availability = sum(r.availability_pct for r in team) / team_size if team_size > 0 else 0.0
        underutilized = sum(1 for r in team if r.allocation_pct * r.availability_pct < 0.60)
        
        # Blocker metrics
        blocker_counts = self._count_blockers_by_severity(blockers)
        active_blockers = sum(1 for b in blockers if not b.actual_resolution_date)
        blocker_velocity_impact = self._estimate_blocker_velocity_impact(blockers)
        
        # Schedule metrics
        completed_sprints = sum(1 for s in sprints if s.status == SprintStatus.COMPLETED)
        current_sprint_num = self._get_current_sprint_number(sprints)
        
        # Spillover metrics (HISTORICAL — from sprints with recorded actuals).
        # This is a backward-looking tally of items that have already carried
        # over historically. It is NOT a prediction of future spillover; for
        # that, see SpilloverAnalysisEngine.predicted_spillover_by_sprint,
        # which is a separate, forward-looking model over remaining sprints
        # and will not generally match this historical figure.
        expected_spillover = sum(a.carryover_count for a in actuals)
        historical_carryover_rate = (
            expected_spillover / len(actuals) if len(actuals) > 0 else 0.0
        )
        
        return ProjectMetrics(
            total_items=total,
            completed_items=completed,
            in_progress_items=in_progress,
            blocked_items=blocked,
            not_started_items=not_started,
            completion_pct=completed / total if total > 0 else 0.0,
            total_effort_hours=total_effort,
            remaining_effort_hours=remaining_effort,
            completed_effort_hours=completed_effort,
            average_item_effort=avg_item_effort,
            planned_total_velocity=planned_velocity,
            actual_avg_velocity=actual_avg_velocity,
            velocity_variance=velocity_variance,
            velocity_std_dev=velocity_std_dev,
            team_size=team_size,
            avg_allocation_pct=avg_allocation,
            avg_availability_pct=avg_availability,
            underutilized_resource_count=underutilized,
            blocker_count_by_severity=blocker_counts,
            active_blocker_count=active_blockers,
            estimated_blocker_velocity_impact=blocker_velocity_impact,
            project_start_date=self.project_state.project_info.start_date,
            project_end_date=self.project_state.project_info.target_end_date,
            current_sprint_number=current_sprint_num,
            completed_sprints=completed_sprints,
            dependency_count=len(self.project_state.dependencies),
            critical_path_length=0,  # Will be set by CriticalPathEngine
            expected_spillover_items=expected_spillover,
            historical_total_carryover_items=expected_spillover,
            historical_carryover_rate=historical_carryover_rate,
        )
    
    @staticmethod
    def _calculate_variance(values: List[float]) -> float:
        """Calculate variance of a list of values."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance
    
    @staticmethod
    def _count_blockers_by_severity(blockers) -> Dict[str, int]:
        """Count blockers grouped by severity."""
        counts = {
            "Critical": sum(1 for b in blockers if b.severity == BlockerSeverity.CRITICAL),
            "High": sum(1 for b in blockers if b.severity == BlockerSeverity.HIGH),
            "Medium": sum(1 for b in blockers if b.severity == BlockerSeverity.MEDIUM),
            "Low": sum(1 for b in blockers if b.severity == BlockerSeverity.LOW),
        }
        return counts
    
    @staticmethod
    def _estimate_blocker_velocity_impact(blockers) -> float:
        """Estimate velocity impact from active blockers (0.0-1.0)."""
        # Impact factors per severity
        impact_map = {
            BlockerSeverity.CRITICAL: 0.40,  # 40% velocity reduction
            BlockerSeverity.HIGH: 0.20,      # 20% velocity reduction
            BlockerSeverity.MEDIUM: 0.10,    # 10% velocity reduction
            BlockerSeverity.LOW: 0.05,       # 5% velocity reduction
        }
        
        total_impact = 0.0
        for blocker in blockers:
            if not blocker.actual_resolution_date:  # Active blocker
                total_impact += impact_map.get(blocker.severity, 0.0)
        
        # Cap at 1.0 (100% velocity loss)
        return min(total_impact, 1.0)
    
    @staticmethod
    def _get_current_sprint_number(sprints) -> int:
        """Determine current sprint number based on sprint status."""
        # Find first in-progress or not-started sprint
        for sprint in sprints:
            if sprint.status == SprintStatus.IN_PROGRESS:
                return sprint.sprint_number
            if sprint.status == SprintStatus.NOT_STARTED:
                return sprint.sprint_number
        # If all completed, return last sprint + 1
        return max((s.sprint_number for s in sprints), default=1) + 1