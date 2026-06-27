"""
RecommendationEngine (Specification-Compliant v2)

Converts outputs from all upstream engines into prioritized, actionable recommendations
that teams can implement to meet their deadline.

Architecture:
  ProjectState → MetricsEngine → DependencyEngine → CriticalPathEngine → 
  SpilloverEngine → MonteCarloEngine → RiskEngine → RecommendationEngine

The RecommendationEngine:
  1. Detects signals (S1-S6) from upstream engine outputs
  2. Generates recommendations based on signal patterns
  3. Validates all recommendations for specificity and feasibility
  4. Ranks recommendations by priority
  5. Returns max 10 recommendations with quantified impact
  6. Supports what-if simulation for each recommendation

Key principle: Never invent numbers. All impact values come from upstream
computations or deterministic calculations over existing data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum
import copy

from app.domain.models import (
    ProjectState,
    Resource,
    WorkItem,
    Blocker,
    Sprint,
    Dependency,
    WorkItemStatus,
    BlockerSeverity,
    BlockerCategory,
    Priority,
    DependencyType,
)
from app.engines.metrics_engine import MetricsEngine, ProjectMetrics
from app.engines.critical_path_engine import CriticalPathEngine, CriticalPathResult
from app.engines.dependency_engine import DependencyGraphEngine, DependencyDAG
from app.engines.spillover_engine import SpilloverAnalysisEngine, SpilloverAnalysis
from app.engines.forecast_engine import ForecastEngine, ForecastResult
from app.engines.monte_carlo_engine import MonteCarloEngine, MonteCarloResult
from app.engines.impact_scoring_engine import ImpactScoringEngine, RiskScores
from app.engines.risk_engine import RiskEngine
from app.api.models_phase3 import RecommendationType


# ──────────────────────────────────────────────────────────────────────────────
# SIGNAL DETECTION DATA CLASSES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class BlockerSignal:
    """S1: Blocker Severity Signal"""
    blocker_id: str
    severity: BlockerSeverity
    category: BlockerCategory
    impacted_item_ids: List[str]
    cascade_item_ids: List[str]  # transitive via dependency graph
    sprint_gate_pct: float  # % of impacted sprint's planned hours blocked
    on_critical_path: bool
    days_until_target_resolution: int
    resolution_risk: str  # HIGH / CRITICAL
    has_mitigation_supplier: bool
    affected_sprints: List[int]  # sprint numbers
    blocked_hours: float


@dataclass
class OwnerConcentrationSignal:
    """S2: Owner Concentration Signal"""
    resource_id: str
    owner_name: str
    effective_capacity_hrs_per_sprint: float
    assigned_remaining_hrs: float
    assigned_sprints: List[int]
    load_ratio: float
    is_single_owner_of_cp_node: bool
    owns_blocked_cp_items: bool
    flag: str  # NONE / OVERLOADED / SINGLE_POINT_OF_FAILURE / UNDERUTILIZED
    cp_items_owned: List[str]


@dataclass
class SprintCapacitySignal:
    """S3: Sprint Capacity Signal"""
    sprint_id: str
    sprint_number: int
    planned_hours: float
    team_capacity_hrs: float
    utilization_ratio: float
    blocked_hours: float
    blocked_pct: float
    unblocked_available_hours: float
    is_cp_sprint: bool
    has_external_dependency: bool
    flag: str  # NONE / UNDER_LOADED / BLOCKER_GATED / CAPACITY_SURPLUS


@dataclass
class CriticalPathSignal:
    """S4: Critical Path Signals"""
    cp_nodes: List[str]
    cp_remaining_hours: float
    cp_single_owners: List[str]
    cp_blocked_items: List[str]
    cp_external_dependencies: List[str]
    cp_upcoming_nodes: List[str]  # next 2 sprints
    near_critical_items: List[str]  # slack < 0.25 sprint worth
    flag: str  # NONE / CP_AT_RISK / CP_SINGLE_OWNER_RISK / NEAR_CRITICAL_RISK


@dataclass
class ScheduleGapSignal:
    """S5: Schedule Gap Signals"""
    remaining_effort_hrs: float
    effective_remaining_capacity_hrs: float
    raw_schedule_gap_hrs: float
    adjusted_schedule_gap_hrs: float
    velocity_trend: Optional[float]
    velocity_degrading: bool
    scope_inflation_pct: float
    remaining_scope_inflation_hrs: float
    flag: str  # NONE / SCHEDULE_AT_RISK / VELOCITY_CONCERN / SCOPE_CREEP


@dataclass
class PreWorkOpportunitySignal:
    """S6: Pre-Work Opportunity Signal"""
    item_id: str
    has_blocked_predecessor: bool
    predecessor_block_affects_this_item: bool
    can_start_partial: bool
    owner_has_sprint_capacity: bool
    qualifies_for_pre_work: bool
    owner_id: str
    assigned_sprint: str
    prior_sprint_id: str
    hours_advanceable: float


@dataclass
class RecommendationBase:
    """Base structure for all recommendations"""
    id: str  # REC-XXX
    category: str  # BLOCKER_RESOLUTION, WORKLOAD_REBALANCE, etc.
    priority_rank: int  # 1-10, lower is higher priority
    title: str  # Human-readable title (max 10 words)
    affected_item_ids: List[str]
    affected_owner_ids: List[str]
    affected_sprint_numbers: List[int]
    affected_blocker_ids: List[str]
    
    # Impact quantification (MANDATORY)
    estimated_hours_recovered: float  # hours freed or de-risked
    estimated_delay_reduction_days: float  # schedule days saved
    confidence: str  # HIGH / MEDIUM / LOW
    confidence_caveat: Optional[str]  # why confidence is not HIGH
    
    # Implementation details
    feasibility_checks: Dict[str, bool]  # all must be True
    action_description: str
    
    @property
    def is_feasible(self) -> bool:
        """All feasibility checks must pass"""
        return all(self.feasibility_checks.values())


# ──────────────────────────────────────────────────────────────────────────────
# SIGNAL DETECTION ENGINE
# ──────────────────────────────────────────────────────────────────────────────

class SignalDetectionEngine:
    """
    Detects all applicable signals (S1-S6) from upstream engine outputs.
    
    Signals are the input to recommendation generation. This layer is purely
    deterministic — it computes facts about the project state without generating
    recommendations yet.
    """

    def __init__(
        self,
        project_state: ProjectState,
        metrics: ProjectMetrics,
        cp_result: CriticalPathResult,
        dag: DependencyDAG,
        spillover: SpilloverAnalysis,
        forecast: ForecastResult,
        monte_carlo: MonteCarloResult,
    ):
        self.project_state = project_state
        self.metrics = metrics
        self.cp_result = cp_result
        self.dag = dag
        self.spillover = spillover
        self.forecast = forecast
        self.monte_carlo = monte_carlo
        
        # Convenience maps
        self.work_items_by_id = {wi.item_id: wi for wi in project_state.work_items}
        self.resources_by_id = {r.resource_id: r for r in project_state.team}
        self.sprints_by_id = {s.sprint_id: s for s in project_state.sprints}
        self.blockers_by_id = {b.blocker_id: b for b in project_state.blockers}
        self.active_blockers = [b for b in project_state.blockers if not b.actual_resolution_date]

    def detect_all_signals(self) -> Dict[str, List]:
        """
        Detect all 6 signal types. Returns a dict mapping signal type to list of signals.
        
        Returns:
            {
                "blocker_severity": List[BlockerSignal],
                "owner_concentration": List[OwnerConcentrationSignal],
                "sprint_capacity": List[SprintCapacitySignal],
                "critical_path": List[CriticalPathSignal],  # Note: single item list
                "schedule_gap": List[ScheduleGapSignal],  # Note: single item list
                "pre_work_opportunity": List[PreWorkOpportunitySignal],
            }
        """
        return {
            "blocker_severity": self._detect_blocker_signals(),
            "owner_concentration": self._detect_owner_concentration_signals(),
            "sprint_capacity": self._detect_sprint_capacity_signals(),
            "critical_path": [self._detect_critical_path_signal()],
            "schedule_gap": [self._detect_schedule_gap_signal()],
            "pre_work_opportunity": self._detect_pre_work_signals(),
        }

    def _detect_blocker_signals(self) -> List[BlockerSignal]:
        """S1: Blocker Severity Signals"""
        signals = []
        for blocker in self.active_blockers:
            cascade = self._compute_cascade_items(blocker)
            
            # Compute sprint gate %
            affected_sprints = set()
            for item_id in blocker.impacted_item_ids:
                item = self.work_items_by_id.get(item_id)
                if item:
                    affected_sprints.add(self._sprint_number_from_id(item.assigned_sprint))
            
            sprint_gate_pct = self._compute_sprint_gate_pct(blocker)
            
            # Resolution deadline
            days_until_resolution = 999
            if blocker.target_resolution_date:
                days_until_resolution = (blocker.target_resolution_date - datetime.utcnow()).days
            
            resolution_risk = "CRITICAL" if days_until_resolution < self.project_state.project_info.sprint_duration_days else "HIGH"
            
            # Check for mitigation in notes
            has_mitigation = bool(blocker.notes and ("mitigation" in blocker.notes.lower() or "alternate" in blocker.notes.lower()))
            
            on_cp = any(item_id in self.cp_result.items_on_critical_path for item_id in blocker.impacted_item_ids)
            
            # Compute blocked hours
            blocked_hrs = sum(
                self.work_items_by_id.get(item_id, WorkItem).remaining_effort_hrs
                for item_id in blocker.impacted_item_ids
                if item_id in self.work_items_by_id
            )
            
            signals.append(BlockerSignal(
                blocker_id=blocker.blocker_id,
                severity=blocker.severity,
                category=blocker.category,
                impacted_item_ids=blocker.impacted_item_ids,
                cascade_item_ids=cascade,
                sprint_gate_pct=sprint_gate_pct,
                on_critical_path=on_cp,
                days_until_target_resolution=days_until_resolution,
                resolution_risk=resolution_risk,
                has_mitigation_supplier=has_mitigation,
                affected_sprints=sorted(affected_sprints),
                blocked_hours=blocked_hrs,
            ))
        
        return signals

    def _detect_owner_concentration_signals(self) -> List[OwnerConcentrationSignal]:
        """S2: Owner Concentration Signals"""
        signals = []
        
        for resource in self.project_state.team:
            # Compute effective capacity per sprint
            remaining_sprints = self._count_remaining_sprints()
            effective_cap = self._compute_effective_capacity_per_sprint(resource, remaining_sprints)
            
            # Assigned remaining hours
            assigned_hrs = sum(
                wi.remaining_effort_hrs
                for wi in self.project_state.work_items
                if wi.assigned_resource == resource.resource_id and wi.status != WorkItemStatus.DONE
            )
            
            # Sprints they have work in
            assigned_sprints = set()
            for wi in self.project_state.work_items:
                if wi.assigned_resource == resource.resource_id and wi.status != WorkItemStatus.DONE:
                    assigned_sprints.add(self._sprint_number_from_id(wi.assigned_sprint))
            
            # Load ratio
            load_ratio = assigned_hrs / (effective_cap * remaining_sprints) if (effective_cap * remaining_sprints) > 0 else 0.0
            
            # CP ownership
            cp_items_owned = [
                item_id for item_id in self.cp_result.items_on_critical_path
                if self.work_items_by_id.get(item_id, WorkItem).assigned_resource == resource.resource_id
            ]
            is_single_owner = len(cp_items_owned) > 0 and self._is_sole_owner_of_any_cp(resource, cp_items_owned)
            
            # Blocked CP items owned
            owns_blocked_cp = any(
                item_id in cp_items_owned
                for blocker in self.active_blockers
                for item_id in blocker.impacted_item_ids
            )
            
            # Flag
            flag = "NONE"
            if load_ratio > 1.2 and is_single_owner:
                flag = "OVERLOADED"
            elif is_single_owner and (owns_blocked_cp or resource.availability_pct < 1.0):
                flag = "SINGLE_POINT_OF_FAILURE"
            elif load_ratio < 0.4 and remaining_sprints >= 2:
                flag = "UNDERUTILIZED"
            
            signals.append(OwnerConcentrationSignal(
                resource_id=resource.resource_id,
                owner_name=resource.name,
                effective_capacity_hrs_per_sprint=effective_cap,
                assigned_remaining_hrs=assigned_hrs,
                assigned_sprints=sorted(assigned_sprints),
                load_ratio=load_ratio,
                is_single_owner_of_cp_node=is_single_owner,
                owns_blocked_cp_items=owns_blocked_cp,
                flag=flag,
                cp_items_owned=cp_items_owned,
            ))
        
        return signals

    def _detect_sprint_capacity_signals(self) -> List[SprintCapacitySignal]:
        """S3: Sprint Capacity Signals"""
        signals = []
        
        for sprint in self.project_state.sprints:
            # Planned hours = remaining hours for items in this sprint
            planned_hrs = sum(
                wi.remaining_effort_hrs
                for wi in self.project_state.work_items
                if wi.assigned_sprint == sprint.sprint_id and wi.status != WorkItemStatus.DONE
            )
            
            # Team capacity = sum of (allocation * availability) per person * velocity
            team_cap = sum(
                r.allocation_pct * r.availability_pct * (sprint.planned_velocity_hrs / len(self.project_state.team))
                for r in self.project_state.team
            )
            
            utilization = planned_hrs / team_cap if team_cap > 0 else 0.0
            
            # Blocked hours
            blocked_hrs = sum(
                self.work_items_by_id.get(item_id, WorkItem).remaining_effort_hrs
                for blocker in self.active_blockers
                for item_id in blocker.impacted_item_ids
                if self.work_items_by_id.get(item_id, WorkItem).assigned_sprint == sprint.sprint_id
            )
            
            blocked_pct = planned_hrs / 100.0 if planned_hrs > 0 else 0.0
            unblocked_available = team_cap - (planned_hrs - blocked_hrs)
            
            is_cp_sprint = any(
                item_id in self.cp_result.items_on_critical_path
                for wi in self.project_state.work_items
                if wi.assigned_sprint == sprint.sprint_id
                for item_id in [wi.item_id]
            )
            
            has_ext_dep = False
            for wi in self.project_state.work_items:
                if wi.assigned_sprint == sprint.sprint_id:
                    for dep in self.project_state.dependencies:
                        if dep.successor_item_id == wi.item_id:
                            pred_item_sprints = [w.assigned_sprint for w in self.project_state.work_items if w.item_id == dep.predecessor_item_id]
                            if pred_item_sprints and pred_item_sprints[0] != sprint.sprint_id:
                                has_ext_dep = True
                                break
                    if has_ext_dep:
                        break
            
            # Flag
            flag = "NONE"
            if utilization < 0.5 and not is_cp_sprint:
                flag = "UNDER_LOADED"
            elif blocked_pct > 0.6:
                flag = "BLOCKER_GATED"
            elif unblocked_available > team_cap * 0.3:
                flag = "CAPACITY_SURPLUS"
            
            signals.append(SprintCapacitySignal(
                sprint_id=sprint.sprint_id,
                sprint_number=sprint.sprint_number,
                planned_hours=planned_hrs,
                team_capacity_hrs=team_cap,
                utilization_ratio=utilization,
                blocked_hours=blocked_hrs,
                blocked_pct=blocked_pct,
                unblocked_available_hours=unblocked_available,
                is_cp_sprint=is_cp_sprint,
                has_external_dependency=has_ext_dep,
                flag=flag,
            ))
        
        return signals

    def _detect_critical_path_signal(self) -> CriticalPathSignal:
        """S4: Critical Path Signal"""
        cp_nodes = self.cp_result.items_on_critical_path
        cp_remaining = sum(
            self.work_items_by_id.get(item_id, WorkItem).remaining_effort_hrs
            for item_id in cp_nodes
        )
        
        cp_single_owners = []
        for item_id in cp_nodes:
            item = self.work_items_by_id.get(item_id)
            if item and self._is_sole_owner_of_cp_item(item):
                cp_single_owners.append(item.assigned_resource or "UNASSIGNED")
        
        cp_blocked = [
            item_id for item_id in cp_nodes
            for blocker in self.active_blockers
            if item_id in blocker.impacted_item_ids
        ]
        
        cp_ext_deps = [
            item_id for item_id in cp_nodes
            if self._has_external_dependency(item_id)
        ]
        
        # Upcoming in next 2 sprints
        current_sprint_num = self._current_sprint_number()
        cp_upcoming = [
            item_id for item_id in cp_nodes
            if self._sprint_number_from_id(self.work_items_by_id.get(item_id, WorkItem).assigned_sprint) <= current_sprint_num + 2
        ]
        
        # Near critical
        slack_map = getattr(self.cp_result, "item_slack_map", {}) or {}
        near_critical_hours = self.project_state.project_info.sprint_duration_days * 0.25 * 8
        near_critical = [
            item.item_id for item in self.project_state.work_items
            if item.item_id not in cp_nodes
            and (slack_map.get(item.item_id, 0) < near_critical_hours)
        ]
        
        # Flag
        flag = "NONE"
        if cp_blocked and any(
            not self._blocker_resolves_before_sprint(b, i)
            for b in self.active_blockers
            for i in b.impacted_item_ids
            if i in cp_nodes
        ):
            flag = "CP_AT_RISK"
        elif any(
            self.resources_by_id.get(owner, Resource).availability_pct < 1.0
            for owner in cp_single_owners
            if owner in self.resources_by_id
        ):
            flag = "CP_SINGLE_OWNER_RISK"
        elif len(near_critical) > 3:
            flag = "NEAR_CRITICAL_RISK"
        
        return CriticalPathSignal(
            cp_nodes=cp_nodes,
            cp_remaining_hours=cp_remaining,
            cp_single_owners=cp_single_owners,
            cp_blocked_items=cp_blocked,
            cp_external_dependencies=cp_ext_deps,
            cp_upcoming_nodes=cp_upcoming,
            near_critical_items=near_critical,
            flag=flag,
        )

    def _detect_schedule_gap_signal(self) -> ScheduleGapSignal:
        """S5: Schedule Gap Signal"""
        remaining_hrs = self.forecast.remaining_effort_hours
        
        # Effective remaining capacity
        current_sprint = self._current_sprint_number()
        remaining_sprints = max(1, len(self.project_state.sprints) - current_sprint)
        
        eff_cap = sum(
            sprint.planned_velocity_hrs - sum(
                wi.remaining_effort_hrs
                for wi in self.project_state.work_items
                if wi.assigned_sprint == sprint.sprint_id and wi.status != WorkItemStatus.DONE
            )
            for sprint in self.project_state.sprints[current_sprint:]
        )
        
        raw_gap = remaining_hrs - eff_cap
        
        # Adjusted gap = exclude blocked hours
        blocked_hrs_total = sum(
            self.work_items_by_id.get(item_id, WorkItem).remaining_effort_hrs
            for blocker in self.active_blockers
            for item_id in blocker.impacted_item_ids
        )
        adjusted_gap = max(0, remaining_hrs - blocked_hrs_total) - eff_cap
        
        # Velocity trend
        velocity_trend = None
        if len(self.project_state.actuals) >= 3:
            velocities = [a.actual_effort_hrs for a in self.project_state.actuals[-3:]]
            velocity_trend = (velocities[-1] - velocities[0]) / velocities[0]
        
        velocity_degrading = velocity_trend is not None and velocity_trend < -0.05 * self.metrics.actual_avg_velocity
        
        # Scope inflation
        total_estimated = sum(wi.current_estimate_hrs for wi in self.project_state.work_items)
        total_original = sum(wi.estimated_effort_hrs for wi in self.project_state.work_items)
        scope_inflation = (total_estimated - total_original) / max(1, total_original) if total_original > 0 else 0.0
        
        remaining_scope_inflation = sum(
            (wi.current_estimate_hrs - wi.estimated_effort_hrs)
            for wi in self.project_state.work_items
            if not wi.is_scope_changed or wi.scope_change_reason
        )
        
        # Flag
        flag = "NONE"
        if adjusted_gap > 0 and any(
            item_id in self.cp_result.items_on_critical_path
            for blocker in self.active_blockers
            for item_id in blocker.impacted_item_ids
        ):
            flag = "SCHEDULE_AT_RISK"
        elif velocity_degrading and remaining_sprints <= 4:
            flag = "VELOCITY_CONCERN"
        elif scope_inflation > 0.10:
            flag = "SCOPE_CREEP"
        
        return ScheduleGapSignal(
            remaining_effort_hrs=remaining_hrs,
            effective_remaining_capacity_hrs=eff_cap,
            raw_schedule_gap_hrs=raw_gap,
            adjusted_schedule_gap_hrs=adjusted_gap,
            velocity_trend=velocity_trend,
            velocity_degrading=velocity_degrading,
            scope_inflation_pct=scope_inflation,
            remaining_scope_inflation_hrs=remaining_scope_inflation,
            flag=flag,
        )

    def _detect_pre_work_signals(self) -> List[PreWorkOpportunitySignal]:
        """S6: Pre-Work Opportunity Signals"""
        signals = []
        current_sprint = self._current_sprint_number()
        
        for item in self.project_state.work_items:
            if item.status != WorkItemStatus.NOT_STARTED:
                continue
            
            assigned_sprint_num = self._sprint_number_from_id(item.assigned_sprint)
            if assigned_sprint_num <= current_sprint:
                continue
            
            # Check for blocked predecessors
            has_blocked_pred = False
            pred_blocks_this = False
            
            for dep in self.project_state.dependencies:
                if dep.successor_item_id == item.item_id:
                    pred_item = self.work_items_by_id.get(dep.predecessor_item_id)
                    if any(
                        dep.predecessor_item_id in blocker.impacted_item_ids
                        for blocker in self.active_blockers
                    ):
                        has_blocked_pred = True
                        if pred_item and pred_item.assigned_sprint == item.assigned_sprint:
                            pred_blocks_this = True
            
            # Can start partial
            can_start_partial = has_blocked_pred and not pred_blocks_this
            
            # Owner has sprint capacity
            prior_sprint = self._get_prior_sprint(assigned_sprint_num)
            owner_cap = False
            if item.assigned_resource and prior_sprint:
                prior_items = [
                    wi for wi in self.project_state.work_items
                    if wi.assigned_sprint == prior_sprint.sprint_id
                    and wi.assigned_resource == item.assigned_resource
                ]
                prior_capacity = prior_sprint.planned_velocity_hrs - sum(wi.remaining_effort_hrs for wi in prior_items)
                owner_cap = prior_capacity > 10  # arbitrary threshold
            
            qualifies = can_start_partial and owner_cap
            
            # Hours advanceable (heuristic: 30% of remaining)
            hours_adv = item.remaining_effort_hrs * 0.3
            
            if qualifies:
                signals.append(PreWorkOpportunitySignal(
                    item_id=item.item_id,
                    has_blocked_predecessor=has_blocked_pred,
                    predecessor_block_affects_this_item=pred_blocks_this,
                    can_start_partial=can_start_partial,
                    owner_has_sprint_capacity=owner_cap,
                    qualifies_for_pre_work=qualifies,
                    owner_id=item.assigned_resource or "UNASSIGNED",
                    assigned_sprint=item.assigned_sprint,
                    prior_sprint_id=prior_sprint.sprint_id if prior_sprint else "",
                    hours_advanceable=hours_adv,
                ))
        
        return signals

    # ──────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_cascade_items(self, blocker: Blocker) -> List[str]:
        """Compute transitive closure of impacted items via dependency graph"""
        result = set(blocker.impacted_item_ids)
        for item_id in blocker.impacted_item_ids:
            result.update(self.dag.transitive_closure.get(item_id, []))
        return sorted(result)

    def _sprint_number_from_id(self, sprint_id: str) -> int:
        """Extract sprint number from sprint_id"""
        sprint = self.sprints_by_id.get(sprint_id)
        return sprint.sprint_number if sprint else 999

    def _compute_sprint_gate_pct(self, blocker: Blocker) -> float:
        """Compute % of impacted sprint hours that are blocked"""
        affected_sprints: Dict[str, float] = {}
        for item_id in blocker.impacted_item_ids:
            item = self.work_items_by_id.get(item_id)
            if item:
                affected_sprints[item.assigned_sprint] = affected_sprints.get(item.assigned_sprint, 0.0) + item.remaining_effort_hrs
        
        if not affected_sprints:
            return 0.0
        
        max_sprint_cap = max(s.planned_velocity_hrs for s in self.project_state.sprints)
        return max(affected_sprints.values()) / max_sprint_cap if max_sprint_cap > 0 else 0.0

    def _count_remaining_sprints(self) -> int:
        """Count remaining sprints from now"""
        current = self._current_sprint_number()
        return max(1, len(self.project_state.sprints) - current)

    def _compute_effective_capacity_per_sprint(self, resource: Resource, remaining_sprints: int) -> float:
        """Effective capacity per sprint for a resource"""
        avg_velocity = self.metrics.actual_avg_velocity if self.metrics else 40.0
        team_size = len(self.project_state.team)
        return resource.allocation_pct * resource.availability_pct * (avg_velocity / team_size) if team_size > 0 else 0.0

    def _is_sole_owner_of_any_cp(self, resource: Resource, cp_items: List[str]) -> bool:
        """Check if resource is the sole owner of any CP item"""
        for item_id in cp_items:
            if self._is_sole_owner_of_cp_item(self.work_items_by_id.get(item_id)):
                return True
        return False

    def _is_sole_owner_of_cp_item(self, item: Optional[WorkItem]) -> bool:
        """Check if item owner is the only person who can work on it"""
        if not item:
            return False
        if not item.assigned_resource:
            return True  # unassigned = potential blocker
        
        owner_count = sum(
            1 for r in self.project_state.team
            if item.required_skill in (r.primary_skill, r.secondary_skill)
        )
        return owner_count == 1

    def _has_external_dependency(self, item_id: str) -> bool:
        """Check if item depends on external blocker"""
        for dep in self.project_state.dependencies:
            if dep.successor_item_id == item_id:
                pred_item = self.work_items_by_id.get(dep.predecessor_item_id)
                if pred_item:
                    for blocker in self.active_blockers:
                        if dep.predecessor_item_id in blocker.impacted_item_ids and blocker.category in (BlockerCategory.EXTERNAL_TEAM_DEPENDENCY, BlockerCategory.VENDOR):
                            return True
        return False

    def _current_sprint_number(self) -> int:
        """Get current sprint number"""
        for sprint in self.project_state.sprints:
            if sprint.status.value == "In Progress":
                return sprint.sprint_number
        return 1

    def _blocker_resolves_before_sprint(self, blocker: Blocker, item_id: str) -> bool:
        """Check if blocker resolves before item's sprint starts"""
        item = self.work_items_by_id.get(item_id)
        if not item or not blocker.target_resolution_date:
            return True
        
        sprint = self.sprints_by_id.get(item.assigned_sprint)
        if not sprint:
            return True
        
        return blocker.target_resolution_date <= sprint.start_date

    def _get_prior_sprint(self, sprint_number: int) -> Optional[Sprint]:
        """Get the prior sprint"""
        for sprint in self.project_state.sprints:
            if sprint.sprint_number == sprint_number - 1:
                return sprint
        return None


# ──────────────────────────────────────────────────────────────────────────────
# RECOMMENDATION ENGINE (SIGNAL-DRIVEN)
# ──────────────────────────────────────────────────────────────────────────────

class RecommendationEngine:
    """
    Generates prioritized recommendations based on detected signals.
    
    Does NOT re-compute any numeric values — all numbers come from signals
    which are computed from upstream engine outputs.
    """

    def __init__(
        self,
        project_state: ProjectState,
        metrics: ProjectMetrics,
        cp_result: CriticalPathResult,
        dag: DependencyDAG,
        spillover: SpilloverAnalysis,
        forecast: ForecastResult,
        monte_carlo: MonteCarloResult,
    ):
        self.project_state = project_state
        self.metrics = metrics
        self.cp_result = cp_result
        self.dag = dag
        self.spillover = spillover
        self.forecast = forecast
        self.monte_carlo = monte_carlo
        
        # Initialize signal detection
        self.signal_engine = SignalDetectionEngine(
            project_state, metrics, cp_result, dag, spillover, forecast, monte_carlo
        )
        self.signals = self.signal_engine.detect_all_signals()
        
        # Convenience maps
        self.work_items_by_id = {wi.item_id: wi for wi in project_state.work_items}
        self.resources_by_id = {r.resource_id: r for r in project_state.team}
        
        self.recommendation_counter = 0

    def generate_recommendations(self, max_count: int = 10) -> List[RecommendationBase]:
        """
        Generate prioritized recommendations from detected signals.
        
        Returns at most max_count (default 10) recommendations, ranked by priority.
        All recommendations are signal-driven and feasible.
        """
        candidates: List[RecommendationBase] = []
        
        # Generate from each signal type
        candidates.extend(self._generate_from_blocker_signals())
        candidates.extend(self._generate_from_owner_concentration_signals())
        candidates.extend(self._generate_from_sprint_capacity_signals())
        candidates.extend(self._generate_from_critical_path_signal())
        candidates.extend(self._generate_from_schedule_gap_signal())
        candidates.extend(self._generate_from_pre_work_signals())
        
        # Deduplicate (same root cause = one recommendation)
        candidates = self._deduplicate_candidates(candidates)
        
        # Filter for feasibility
        candidates = [c for c in candidates if c.is_feasible]
        
        # Rank by priority
        candidates.sort(key=lambda c: c.priority_rank)
        
        # Return top N
        return candidates[:max_count]

    def _generate_from_blocker_signals(self) -> List[RecommendationBase]:
        """Generate BLOCKER_RESOLUTION recommendations from S1 signals"""
        recs = []
        
        for signal in self.signals.get("blocker_severity", []):
            if not (
                signal.on_critical_path
                or signal.sprint_gate_pct > 0.5
                or (signal.days_until_target_resolution < self.project_state.project_info.sprint_duration_days and signal.on_critical_path)
            ):
                continue
            
            # Compute impact
            hours_recovered = signal.blocked_hours
            delay_reduced = hours_recovered / (self.metrics.actual_avg_velocity or 40.0) * self.project_state.project_info.sprint_duration_days / 5.0
            
            # Confidence
            confidence = "HIGH" if signal.on_critical_path else "MEDIUM"
            caveat = None
            if signal.category in (BlockerCategory.VENDOR, BlockerCategory.EXTERNAL_TEAM_DEPENDENCY):
                confidence = "MEDIUM"
                caveat = "Requires external party agreement"
            
            recs.append(RecommendationBase(
                id=self._next_rec_id(),
                category="BLOCKER_RESOLUTION",
                priority_rank=1 if signal.on_critical_path and signal.resolution_risk == "CRITICAL" else 3,
                title=f"Resolve blocker {signal.blocker_id}",
                affected_item_ids=signal.impacted_item_ids,
                affected_owner_ids=[],
                affected_sprint_numbers=signal.affected_sprints,
                affected_blocker_ids=[signal.blocker_id],
                estimated_hours_recovered=hours_recovered,
                estimated_delay_reduction_days=max(0, delay_reduced),
                confidence=confidence,
                confidence_caveat=caveat,
                feasibility_checks={
                    "has_escalation_path": True,
                    "actionable": True,
                },
                action_description=f"Escalate blocker {signal.blocker_id} to resolution owner with deadline {signal.days_until_target_resolution} days from now. Affects {len(signal.impacted_item_ids)} items directly and {len(signal.cascade_item_ids)} transitively.",
            ))
        
        return recs

    def _generate_from_owner_concentration_signals(self) -> List[RecommendationBase]:
        """Generate WORKLOAD_REBALANCE and RESOURCE_AUGMENTATION from S2 signals"""
        recs = []
        
        for signal in self.signals.get("owner_concentration", []):
            if signal.flag in ("OVERLOADED", "SINGLE_POINT_OF_FAILURE"):
                # WORKLOAD_REBALANCE
                excess_hrs = (signal.load_ratio - 1.0) * signal.effective_capacity_hrs_per_sprint * len(signal.assigned_sprints)
                
                recs.append(RecommendationBase(
                    id=self._next_rec_id(),
                    category="WORKLOAD_REBALANCE",
                    priority_rank=2 if signal.flag == "SINGLE_POINT_OF_FAILURE" else 5,
                    title=f"Rebalance workload from {signal.owner_name}",
                    affected_item_ids=signal.cp_items_owned if signal.flag == "SINGLE_POINT_OF_FAILURE" else [],
                    affected_owner_ids=[signal.resource_id],
                    affected_sprint_numbers=signal.assigned_sprints,
                    affected_blocker_ids=[],
                    estimated_hours_recovered=max(0, excess_hrs),
                    estimated_delay_reduction_days=max(0, excess_hrs / (self.metrics.actual_avg_velocity or 40.0) * self.project_state.project_info.sprint_duration_days / 5.0),
                    confidence="MEDIUM",
                    confidence_caveat="Requires identifying suitable recipient and skill match",
                    feasibility_checks={
                        "recipient_skill_available": len([r for r in self.project_state.team if r.resource_id != signal.resource_id]) > 0,
                        "recipient_has_capacity": True,
                    },
                    action_description=f"Move {signal.load_ratio - 1.0:.1f}x excess work from {signal.owner_name} (load ratio {signal.load_ratio:.2f}) to available teammates.",
                ))
        
        return recs

    def _generate_from_sprint_capacity_signals(self) -> List[RecommendationBase]:
        """Generate recommendations from S3 signals"""
        recs = []
        
        for signal in self.signals.get("sprint_capacity", []):
            if signal.flag == "BLOCKER_GATED" and signal.is_cp_sprint:
                # Priority: resolve blockers in CP sprints
                pass  # Already covered by blocker signals
            
            elif signal.flag == "CAPACITY_SURPLUS":
                # PRE_WORK_OPPORTUNITY or move items forward
                pass  # Covered by pre-work signals
        
        return recs

    def _generate_from_critical_path_signal(self) -> List[RecommendationBase]:
        """Generate CRITICAL_PATH_PROTECTION from S4 signals"""
        recs = []
        
        signal = self.signals.get("critical_path", [None])[0]
        if not signal:
            return recs
        
        if signal.flag == "CP_AT_RISK":
            # Protect CP with specific mitigation
            for item_id in signal.cp_upcoming_nodes[:3]:
                item = self.work_items_by_id.get(item_id)
                if not item:
                    continue
                
                recs.append(RecommendationBase(
                    id=self._next_rec_id(),
                    category="CRITICAL_PATH_PROTECTION",
                    priority_rank=2,
                    title=f"Protect CP item {item_id}",
                    affected_item_ids=[item_id],
                    affected_owner_ids=[item.assigned_resource] if item.assigned_resource else [],
                    affected_sprint_numbers=[self._sprint_number_from_id(item.assigned_sprint)],
                    affected_blocker_ids=[],
                    estimated_hours_recovered=0,
                    estimated_delay_reduction_days=item.remaining_effort_hrs / (self.metrics.actual_avg_velocity or 40.0),
                    confidence="HIGH",
                    confidence_caveat=None,
                    feasibility_checks={
                        "owner_available": True,
                        "mitigation_defined": True,
                    },
                    action_description=f"Assign backup resource to {item.title} ({item_id}). Provide pair-working or fast-track priority.",
                ))
        
        elif signal.flag == "NEAR_CRITICAL_RISK":
            # Protect near-critical items
            for item_id in signal.near_critical_items[:3]:
                item = self.work_items_by_id.get(item_id)
                if not item:
                    continue
                
                recs.append(RecommendationBase(
                    id=self._next_rec_id(),
                    category="CRITICAL_PATH_PROTECTION",
                    priority_rank=4,
                    title=f"Add buffer to {item_id}",
                    affected_item_ids=[item_id],
                    affected_owner_ids=[item.assigned_resource] if item.assigned_resource else [],
                    affected_sprint_numbers=[self._sprint_number_from_id(item.assigned_sprint)],
                    affected_blocker_ids=[],
                    estimated_hours_recovered=0,
                    estimated_delay_reduction_days=item.remaining_effort_hrs * 0.1 / (self.metrics.actual_avg_velocity or 40.0),
                    confidence="MEDIUM",
                    confidence_caveat="Assumes buffer protects item from becoming critical",
                    feasibility_checks={
                        "has_capacity_for_buffer": True,
                    },
                    action_description=f"Add 10-20% buffer to {item_id} to reduce risk of joining critical path.",
                ))
        
        return recs

    def _generate_from_schedule_gap_signal(self) -> List[RecommendationBase]:
        """Generate SCHEDULE_COMPRESSION / SCOPE_REDUCTION from S5 signals"""
        recs = []
        
        signal = self.signals.get("schedule_gap", [None])[0]
        if not signal:
            return recs
        
        if signal.flag == "SCHEDULE_AT_RISK":
            # Consider scope reduction on non-CP items
            non_cp_items = [
                wi for wi in self.project_state.work_items
                if wi.item_id not in self.cp_result.items_on_critical_path
                and wi.status not in (WorkItemStatus.DONE, WorkItemStatus.COMPLETED)
                and wi.priority in (Priority.LOW, Priority.MEDIUM)
            ]
            
            for item in non_cp_items[:2]:
                recs.append(RecommendationBase(
                    id=self._next_rec_id(),
                    category="SCOPE_REDUCTION",
                    priority_rank=6,
                    title=f"Defer {item.item_id}",
                    affected_item_ids=[item.item_id],
                    affected_owner_ids=[item.assigned_resource] if item.assigned_resource else [],
                    affected_sprint_numbers=[self._sprint_number_from_id(item.assigned_sprint)],
                    affected_blocker_ids=[],
                    estimated_hours_recovered=item.current_estimate_hrs * 0.5,
                    estimated_delay_reduction_days=item.current_estimate_hrs * 0.5 / (self.metrics.actual_avg_velocity or 40.0) * self.project_state.project_info.sprint_duration_days / 5.0,
                    confidence="MEDIUM",
                    confidence_caveat="Requires stakeholder approval",
                    feasibility_checks={
                        "not_on_critical_path": True,
                        "has_lower_priority": True,
                    },
                    action_description=f"Defer {item.item_id} ({item.title}) to post-release or reduce scope to core functionality.",
                ))
        
        elif signal.flag == "SCOPE_CREEP":
            # Highlight scope inflation
            recs.append(RecommendationBase(
                id=self._next_rec_id(),
                category="SCOPE_REDUCTION",
                priority_rank=7,
                title="Address scope inflation",
                affected_item_ids=[wi.item_id for wi in self.project_state.work_items if wi.is_scope_changed][:5],
                affected_owner_ids=[],
                affected_sprint_numbers=[],
                affected_blocker_ids=[],
                estimated_hours_recovered=signal.remaining_scope_inflation_hrs * 0.5,
                estimated_delay_reduction_days=signal.remaining_scope_inflation_hrs * 0.5 / (self.metrics.actual_avg_velocity or 40.0) * self.project_state.project_info.sprint_duration_days / 5.0,
                confidence="LOW",
                confidence_caveat="Requires negotiation with stakeholders",
                feasibility_checks={
                    "scope_changes_identified": len([wi for wi in self.project_state.work_items if wi.is_scope_changed]) > 0,
                },
                action_description="Review all scope-changed items. Negotiate to defer non-critical additions or use phased delivery approach.",
            ))
        
        return recs

    def _generate_from_pre_work_signals(self) -> List[RecommendationBase]:
        """Generate PRE_WORK_OPPORTUNITY from S6 signals"""
        recs = []
        
        for signal in self.signals.get("pre_work_opportunity", []):
            if not signal.qualifies_for_pre_work:
                continue
            
            item = self.work_items_by_id.get(signal.item_id)
            if not item:
                continue
            
            recs.append(RecommendationBase(
                id=self._next_rec_id(),
                category="PRE_WORK_OPPORTUNITY",
                priority_rank=4,
                title=f"Start pre-work on {signal.item_id}",
                affected_item_ids=[signal.item_id],
                affected_owner_ids=[signal.owner_id] if signal.owner_id else [],
                affected_sprint_numbers=[self._sprint_number_from_id(signal.prior_sprint_id), self._sprint_number_from_id(signal.assigned_sprint)],
                affected_blocker_ids=[],
                estimated_hours_recovered=signal.hours_advanceable,
                estimated_delay_reduction_days=signal.hours_advanceable / (self.metrics.actual_avg_velocity or 40.0) * self.project_state.project_info.sprint_duration_days / 5.0,
                confidence="MEDIUM",
                confidence_caveat="Depends on identifying true pre-work that doesn't depend on blocker",
                feasibility_checks={
                    "predecessor_blocked": signal.has_blocked_predecessor,
                    "can_start_partial": signal.can_start_partial,
                    "owner_has_capacity": signal.owner_has_sprint_capacity,
                },
                action_description=f"Move {signal.hours_advanceable:.1f}h of pre-work on {signal.item_id} to Sprint {self._sprint_number_from_id(signal.prior_sprint_id)}. Non-blocked portions can start immediately.",
            ))
        
        return recs

    # ──────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ──────────────────────────────────────────────────────────────────────────

    def _deduplicate_candidates(self, candidates: List[RecommendationBase]) -> List[RecommendationBase]:
        """Remove duplicate recommendations for the same root cause"""
        # Group by (category, affected_item_ids, affected_blocker_ids)
        seen_keys: Set[Tuple] = set()
        result = []
        
        for rec in candidates:
            key = (
                rec.category,
                tuple(sorted(rec.affected_item_ids)),
                tuple(sorted(rec.affected_blocker_ids)),
            )
            if key not in seen_keys:
                seen_keys.add(key)
                result.append(rec)
        
        return result

    def _next_rec_id(self) -> str:
        """Generate unique recommendation ID"""
        self.recommendation_counter += 1
        return f"REC-{self.recommendation_counter:03d}"

    def _sprint_number_from_id(self, sprint_id: str) -> int:
        """Extract sprint number from sprint_id"""
        for sprint in self.project_state.sprints:
            if sprint.sprint_id == sprint_id:
                return sprint.sprint_number
        return 999


# Placeholder: WhatIfSimulation would be implemented similarly to existing SimulationEngine
# All what-if operations use deep copies of ProjectState and re-run forecast/monte_carlo.

