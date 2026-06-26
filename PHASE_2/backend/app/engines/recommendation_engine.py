from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.domain.models import ProjectState, Resource, WorkItem, Blocker, SprintActual, WorkItemStatus, BlockerCategory
from app.engines.metrics_engine import MetricsEngine, ProjectMetrics

BLOCKER_CATEGORY_HINTS = {
    "Lab Issue": (
        "Escalate to lab operations immediately. "
        "Identify an alternate lab slot or remote testing option."
    ),
    "License Unavailable": (
        "Raise procurement request today with priority flag. "
        "Identify an open-source or trial alternative immediately."
    ),
    "External Team Dependency": (
        "Schedule alignment meeting with external team today. "
        "Escalate to program manager if no response within 48h. "
        "Request partial deliverable to unblock critical items."
    ),
    "Awaiting Validation": (
        "Send formal validation request with hard deadline. "
        "Identify whether partial validation can unblock "
        "highest priority items immediately."
    ),
    "Tool Issue": (
        "Raise IT/DevOps ticket with critical priority. "
        "Identify manual workaround to keep work moving."
    ),
    "Hardware / Procurement": (
        "Expedite procurement order and explore alternate suppliers. "
        "Identify if simulation or emulation can substitute "
        "until hardware arrives."
    ),
    "Environment": (
        "Raise with DevOps team for immediate restoration. "
        "Identify shared or local environment as backup."
    ),
    "People Dependency": (
        "Escalate to resource manager or delivery head. "
        "Request a deputy with decision authority."
    ),
    "Approval Pending": (
        "Send formal approval request with business impact. "
        "Escalate one level if no response in 24 hours."
    ),
    "Other": (
        "Review blocker with team lead to classify correctly. "
        "Assign a single owner with clear deadline."
    ),
}

BLOCKER_CATEGORY_ACTIONS = {
    "External Team Dependency": [
        "Escalate dependency owner",
        "Request interim specification",
        "Identify parallel work streams",
    ],
    "Hardware / Procurement": [
        "Expedite procurement",
        "Engage alternate supplier",
        "Use simulation hardware",
        "Advance software-only tasks",
    ],
    "Specification": [
        "Request provisional specification",
        "Escalate through program management",
        "Freeze dependent requirements",
        "Implement assumptions-based branch",
    ],
    "Vendor": [
        "Request provisional supplier commitment",
        "Escalate through program management",
        "Freeze dependent requirements",
        "Implement assumptions-based branch",
    ],
    "Resource": [
        "Assign senior engineer",
        "Pair-program resolution",
        "Time-box investigation",
        "Escalate after defined duration",
    ],
    "Other": [
        "Review the blocker with the team",
        "Assign a clear owner and deadline",
        "Identify escalation path",
        "Track progress daily",
    ],
}
from app.engines.dependency_engine import DependencyGraphEngine, DependencyDAG
from app.engines.critical_path_engine import CriticalPathEngine, CriticalPathResult
from app.engines.spillover_engine import SpilloverAnalysisEngine, SpilloverAnalysis
from app.engines.forecast_engine import ForecastEngine, ForecastResult
from app.engines.monte_carlo_engine import MonteCarloEngine, MonteCarloResult
from app.engines.impact_scoring_engine import ImpactScoringEngine, RiskScores
from app.engines.risk_engine import RiskEngine, RiskResult
from app.engines.simulation_engine import SimulationAction, SimulationEngine
from app.api.models_phase3 import (
    RecommendationType,
)


class RecommendationError(Exception):
    pass


@dataclass
class RecommendationCandidate:
    recommendation_id: str
    type: RecommendationType
    action: str
    target_ids: List[str]
    details: Dict[str, Any]
    reason: str
    implementation_effort: str
    confidence: str
    priority_score: float = 0.0
    baseline_probability: float = 0.0
    baseline_delay_days: float = 0.0
    baseline_risk_score: float = 0.0
    after_probability: float = 0.0
    after_delay_days: float = 0.0
    after_risk_score: float = 0.0
    expected_probability_gain: float = 0.0
    expected_delay_gain_days: float = 0.0
    expected_risk_reduction: float = 0.0
    raw_expected_probability_gain: float = 0.0
    raw_expected_risk_reduction: float = 0.0
    impact_confidence: str = "Medium"
    impact_classification: str = "Positive Impact"
    impact_level: str = "Medium"
    business_impact: str = ""
    impact_summary: str = ""
    category: Optional[str] = None
    recommended_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "type": self.type.value,
            "action": self.action,
            "target_ids": self.target_ids,
            "details": self.details,
            "reason": self.reason,
            "implementation_effort": self.implementation_effort,
            "confidence": self.confidence,
            "priority_score": round(self.priority_score, 2),
            "baseline_probability": round(self.baseline_probability, 4),
            "baseline_delay_days": round(self.baseline_delay_days, 2),
            "baseline_risk_score": round(self.baseline_risk_score, 2),
            "after_probability": round(self.after_probability, 4),
            "after_delay_days": round(self.after_delay_days, 2),
            "after_risk_score": round(self.after_risk_score, 2),
            "expected_probability_gain": round(self._display_probability_gain(), 4),
            "expected_delay_gain_days": round(self.expected_delay_gain_days, 2),
            "expected_risk_reduction": round(self._display_risk_reduction(), 2),
            "impact_level": self.impact_level,
            "impact_confidence": self.impact_confidence,
            "impact_classification": self.impact_classification,
            "business_impact": self.business_impact,
            "impact_summary": self.impact_summary,
            "category": self.category,
            "recommended_actions": self.recommended_actions,
        }


class RecommendationEngine:
    # Noise thresholds set from repeated baseline Monte Carlo runs:
    # - probability noise span ~0.004
    # - risk noise span ~0.16
    PROBABILITY_NOISE_THRESHOLD = 0.005
    RISK_NOISE_THRESHOLD = 0.2

    def __init__(
        self,
        project_state: ProjectState,
        metrics: ProjectMetrics,
        cp_result: CriticalPathResult,
        dag: DependencyDAG,
        spillover: SpilloverAnalysis,
        forecast: ForecastResult,
        monte_carlo: MonteCarloResult,
        risk_result: RiskResult,
        simulation_count: int = 1000,
    ):
        self.project_state = project_state
        self.metrics = metrics
        self.cp_result = cp_result
        self.dag = dag
        self.spillover = spillover
        self.forecast = forecast
        self.monte_carlo = monte_carlo
        self.risk_result = risk_result
        self.impact_scores = ImpactScoringEngine(project_state, dag).score()
        self.simulation_count = simulation_count
        self.work_items = {wi.item_id: wi for wi in project_state.work_items}
        self.resources = {r.resource_id: r for r in project_state.team}
        self.blockers = [b for b in project_state.blockers if not b.actual_resolution_date]
        self.current_sprint = self._find_current_sprint()
        self.baseline_metrics = self._baseline_summary()
        self._cached_candidates: List[RecommendationCandidate] = []

    def generate_recommendations(self) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []

        candidates.extend(self._generate_blocker_recommendations())
        candidates.extend(self._generate_resource_recommendations())
        candidates.extend(self._generate_reassignment_recommendations())
        candidates.extend(self._generate_reduce_scope_recommendations())
        candidates.extend(self._generate_parallelize_recommendations())
        candidates.extend(self._generate_move_blocker_items_recommendations())
        candidates.extend(self._generate_split_task_recommendations())
        candidates.extend(self._generate_cp_optimization_recommendations())

        for candidate in candidates:
            candidate.baseline_probability = self.baseline_metrics["probability"]
            candidate.baseline_delay_days = self.baseline_metrics["delay_days"]
            candidate.baseline_risk_score = self.baseline_metrics["risk_score"]
            self._simulate_candidate(candidate)
            self._score_candidate(candidate)

        result = sorted(candidates, key=lambda c: c.priority_score, reverse=True)
        self._cached_candidates = result
        return result

    def simulate_recommendation(self, recommendation_id: str) -> RecommendationCandidate:
        candidate = self._find_candidate_by_id(recommendation_id)
        if not candidate:
            raise RecommendationError(f"Recommendation {recommendation_id} not found")
        candidate.baseline_probability = self.baseline_metrics["probability"]
        candidate.baseline_delay_days = self.baseline_metrics["delay_days"]
        candidate.baseline_risk_score = self.baseline_metrics["risk_score"]
        self._simulate_candidate(candidate)
        self._score_candidate(candidate)
        return candidate

    def simulate_scenario(self, recommendation_ids: List[str]) -> Dict[str, Any]:
        baseline = self._baseline_summary()
        actions = []
        for rec_id in recommendation_ids:
            candidate = self._find_candidate_by_id(rec_id)
            if not candidate:
                raise RecommendationError(f"Recommendation {rec_id} not found")
            actions.append(self._candidate_to_simulation_action(candidate))

        simulation_engine = SimulationEngine(
            project_state=self.project_state,
            metrics=self.metrics,
            dag=self.dag,
            cp_result=self.cp_result,
            spillover=self.spillover,
            forecast=self.forecast,
            monte_carlo=self.monte_carlo,
            risk_result=self.risk_result,
            simulation_count=self.simulation_count,
        )
        result = simulation_engine.simulate_recommendation_actions(actions)
        return {
            "baseline": baseline,
            "scenario": {
                "probability": result.simulated_probability,
                "delay_days": result.simulated_delay_days,
                "risk_score": result.simulated_risk_score,
            },
            "recommendation_ids": recommendation_ids,
            "probability_gain": round(result.simulated_probability - baseline["probability"], 4),
            "delay_reduction": round(baseline["delay_days"] - result.simulated_delay_days, 2),
            "risk_reduction": round(baseline["risk_score"] - result.simulated_risk_score, 2),
            "action_reasons": result.action_reasons,
        }

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------
    def _generate_blocker_recommendations(self) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        if not self.blockers:
            return candidates

        for blocker in sorted(
            self.blockers,
            key=lambda b: self._blocker_priority(b),
            reverse=True,
        )[:2]:
            target_ids = [blocker.blocker_id]
            reason = self._blocker_reason(blocker)
            category_value = getattr(blocker, "category", BlockerCategory.OTHER).value
            action = (
                f"Resolve blocker {blocker.blocker_id} using category-specific actions for {category_value}"
            )
            candidates.append(
                RecommendationCandidate(
                    recommendation_id=self._stable_id(RecommendationType.RESOLVE_BLOCKER, target_ids),
                    type=RecommendationType.RESOLVE_BLOCKER,
                    action=action,
                    target_ids=target_ids,
                    details={
                        "blocker_id": blocker.blocker_id,
                        "severity": blocker.severity.value,
                        "category": getattr(blocker, "category", BlockerCategory.OTHER).value,
                        "category_action": BLOCKER_CATEGORY_HINTS.get(
                            getattr(blocker, "category", BlockerCategory.OTHER).value,
                            BLOCKER_CATEGORY_HINTS["Other"],
                        ),
                        "affected_items": blocker.impacted_item_ids,
                        "cp_items_affected": self._count_critical_path_involvement(blocker),
                        "downstream_count": self._count_downstream(blocker),
                    },
                    category=getattr(blocker, "category", BlockerCategory.OTHER).value,
                    recommended_actions=self._blocker_recommendation_actions(blocker),
                    reason=reason,
                    implementation_effort="Low",
                    confidence="High",
                )
            )
        return candidates

    def _generate_resource_recommendations(self) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        demand_by_skill = self._build_skill_demand()
        capacity_by_skill = self._build_skill_capacity()

        if not demand_by_skill:
            return candidates

        bottlenecks = []
        for skill, demand in demand_by_skill.items():
            capacity = capacity_by_skill.get(skill, 0.0)
            ratio = demand / max(capacity, 1.0)
            bottlenecks.append((ratio, skill, demand, capacity))

        bottlenecks.sort(reverse=True)
        top_ratio, top_skill, top_demand, top_capacity = bottlenecks[0]
        if top_ratio > 1.1:
            resource_role = self._suggest_role_for_skill(top_skill)
            action = f"Add {resource_role} with {top_skill} skill"
            reason = (
                f"{top_skill} demand is {top_demand:.1f}h while available matching capacity is only "
                f"{top_capacity:.1f}h. This creates a skill bottleneck."
            )
            candidates.append(
                RecommendationCandidate(
                    recommendation_id=self._stable_id(RecommendationType.ADD_RESOURCE, []),
                    type=RecommendationType.ADD_RESOURCE,
                    action=action,
                    target_ids=[],
                    details={
                        "role": resource_role,
                        "skill": top_skill,
                        "demand_hours": round(top_demand, 1),
                        "capacity_hours": round(top_capacity, 1),
                    },
                    reason=reason,
                    implementation_effort="Medium",
                    confidence="Medium",
                )
            )
        return candidates

    def _generate_reassignment_recommendations(self) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        for item in self.project_state.work_items:
            if item.status == item.status.COMPLETED:
                continue
            if not item.assigned_resource:
                continue
            assigned = self.resources.get(item.assigned_resource)
            if not assigned:
                continue
            required_skill = item.required_skill
            if required_skill in {assigned.primary_skill, assigned.secondary_skill}:
                continue

            match = self._find_alternate_resource(required_skill, item.assigned_resource)
            if not match:
                continue
            action = (
                f"Reassign {item.item_id} from {assigned.name} to {match.name}"
            )
            reason = (
                f"{item.item_id} requires {required_skill} but is assigned to {assigned.name} "
                f"whose skills are {assigned.primary_skill}/{assigned.secondary_skill}. "
                f"{match.name} has matching skill and available capacity."
            )
            candidates.append(
                RecommendationCandidate(
                    recommendation_id=self._stable_id(RecommendationType.REASSIGN_WORK, [item.item_id]),
                    type=RecommendationType.REASSIGN_WORK,
                    action=action,
                    target_ids=[item.item_id],
                    details={
                        "from": assigned.name,
                        "to": match.name,
                        "skill": required_skill,
                    },
                    reason=reason,
                    implementation_effort="Low",
                    confidence="High",
                )
            )
            if len(candidates) >= 2:
                break
        return candidates

    def _generate_reduce_scope_recommendations(self) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        scored_items: List[Tuple[float, WorkItem]] = []
        for item in self.project_state.work_items:
            if item.status in {item.status.COMPLETED, item.status.DONE}:
                continue
            if item.current_estimate_hrs < 30.0:
                continue
            if item.priority == item.priority.CRITICAL:
                continue

            size_score = min(item.current_estimate_hrs / 120.0, 1.0)
            not_cp_score = 1.0 if item.item_id not in self.cp_result.items_on_critical_path else 0.0
            priority_score = {
                item.priority.LOW: 1.0,
                item.priority.MEDIUM: 0.6,
                item.priority.HIGH: 0.3,
                item.priority.CRITICAL: 0.0,
            }.get(item.priority, 0.5)
            score = 0.40 * size_score + 0.30 * not_cp_score + 0.30 * priority_score
            scored_items.append((score, item))

        scored_items.sort(reverse=True, key=lambda x: x[0])
        for score, item in scored_items[:2]:
            deferred_hours = round(item.current_estimate_hrs * 0.40, 1)
            core_hours = round(item.current_estimate_hrs * 0.60, 1)
            action = f"Reduce scope of item {item.item_id} to core deliverable"
            reason = (
                f"{item.item_id} can retain {core_hours}h of core scope and defer {deferred_hours}h. "
                f"This reduces schedule risk while preserving the highest-value work."
            )
            candidates.append(
                RecommendationCandidate(
                    recommendation_id=self._stable_id(RecommendationType.REDUCE_ITEM_SCOPE, [item.item_id]),
                    type=RecommendationType.REDUCE_ITEM_SCOPE,
                    action=action,
                    target_ids=[item.item_id],
                    details={
                        "item_id": item.item_id,
                        "original_estimate_hours": item.current_estimate_hrs,
                        "core_hours": core_hours,
                        "deferred_hours": deferred_hours,
                        "default_reduction_pct": 40,
                        "priority": item.priority.value,
                        "on_critical_path": item.item_id in self.cp_result.items_on_critical_path,
                        "pm_adjustable": True,
                    },
                    reason=reason,
                    implementation_effort="Low",
                    confidence="High",
                )
            )
        return candidates

    def _generate_parallelize_recommendations(self) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        pairs: List[Tuple[float, WorkItem, WorkItem, int]] = []

        # Find adjacent dependency pairs on critical path with zero lag
        for pred_id, successors in self.dag.graph.items():
            for succ_id in successors:
                dependency_lag = self.dag.lag_days_map.get((pred_id, succ_id), 0)
                pred_item = self.work_items.get(pred_id)
                succ_item = self.work_items.get(succ_id)
                if not pred_item or not succ_item:
                    continue
                if pred_item.status in {pred_item.status.COMPLETED, pred_item.status.DONE}:
                    continue
                if succ_item.status in {succ_item.status.COMPLETED, succ_item.status.DONE}:
                    continue
                if dependency_lag != 0:
                    continue
                if pred_id not in self.cp_result.items_on_critical_path or succ_id not in self.cp_result.items_on_critical_path:
                    continue

                estimated_days_saved = min(pred_item.remaining_effort_hrs, succ_item.remaining_effort_hrs) / 8.0
                pairs.append((estimated_days_saved, pred_item, succ_item, dependency_lag))

        pairs.sort(reverse=True, key=lambda x: x[0])
        for estimated_days_saved, pred_item, succ_item, _ in pairs[:2]:
            action = (
                f"Parallelize {pred_item.item_id} and {succ_item.item_id} to reduce handoff delay"
            )
            reason = (
                f"{pred_item.item_id} and {succ_item.item_id} are adjacent critical-path tasks with zero lag. "
                f"Running them in parallel can save about {estimated_days_saved:.1f} days."
            )
            candidates.append(
                RecommendationCandidate(
                    recommendation_id=self._stable_id(RecommendationType.PARALLELIZE_TASKS, [pred_item.item_id, succ_item.item_id]),
                    type=RecommendationType.PARALLELIZE_TASKS,
                    action=action,
                    target_ids=[pred_item.item_id, succ_item.item_id],
                    details={
                        "predecessor_id": pred_item.item_id,
                        "successor_id": succ_item.item_id,
                        "estimated_days_saved": round(estimated_days_saved, 2),
                        "required_additional_resource": True,
                    },
                    reason=reason,
                    implementation_effort="Medium",
                    confidence="Medium",
                )
            )
        return candidates

    def _generate_move_blocker_items_recommendations(self) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        if not self.blockers:
            return candidates

        top_blocker = sorted(
            self.blockers,
            key=lambda b: self._blocker_priority(b),
            reverse=True,
        )[0]

        advanceable_items = [
            item.item_id
            for item in self.project_state.work_items
            if item.status == item.status.NOT_STARTED
            and item.item_id not in self.cp_result.items_on_critical_path
            and item.item_id not in top_blocker.impacted_item_ids
        ][:3]

        if not advanceable_items:
            return candidates

        advancement_hours = sum(
            next((wi.current_estimate_hrs for wi in self.project_state.work_items if wi.item_id == item_id), 0.0)
            for item_id in advanceable_items
        )
        notes = getattr(top_blocker, "notes", "") or ""
        impact_idx = notes.upper().find("IMPACT:")
        notes_context = (
            notes[impact_idx:impact_idx+120].strip() if impact_idx >= 0 else notes[:120].strip()
        )
        action = f"Move blocked items forward by starting {len(advanceable_items)} ready items"
        reason = (
            f"Top blocker {top_blocker.blocker_id} is delaying key work. "
            f"Starting ready items that are not on the critical path can keep progress moving."
        )
        candidates.append(
            RecommendationCandidate(
                recommendation_id=self._stable_id(RecommendationType.MOVE_BLOCKER_ITEMS, advanceable_items),
                type=RecommendationType.MOVE_BLOCKER_ITEMS,
                action=action,
                target_ids=advanceable_items,
                details={
                    "blocker_id": top_blocker.blocker_id,
                    "blocker_category": getattr(top_blocker, "category", BlockerCategory.OTHER).value,
                    "blocked_items_count": len(top_blocker.impacted_item_ids),
                    "advanceable_items": advanceable_items,
                    "advanceable_hours": round(advancement_hours, 1),
                    "blocker_notes_context": notes_context,
                },
                reason=reason,
                implementation_effort="Low",
                confidence="High",
            )
        )
        return candidates

    def _generate_split_task_recommendations(self) -> List[RecommendationCandidate]:
        scored_items: List[Tuple[float, WorkItem]] = []
        for item in self.project_state.work_items:
            if item.status in {item.status.COMPLETED, item.status.DONE}:
                continue
            if item.current_estimate_hrs < 40.0:
                continue
            score = self._split_candidate_score(item)
            scored_items.append((score, item))

        scored_items.sort(reverse=True, key=lambda x: x[0])
        candidates: List[RecommendationCandidate] = []
        for score, item in scored_items[:2]:
            splits = max(1, int(item.current_estimate_hrs / 40.0))
            action = f"Split task {item.item_id} into {splits + 1} subtasks"
            reason = (
                f"{item.item_id} is a large {item.current_estimate_hrs:.1f}h task on the critical path. "
                f"Splitting it can reduce serial execution and improve schedule predictability."
            )
            candidates.append(
                RecommendationCandidate(
                    recommendation_id=self._stable_id(RecommendationType.SPLIT_TASK, [item.item_id]),
                    type=RecommendationType.SPLIT_TASK,
                    action=action,
                    target_ids=[item.item_id],
                    details={
                        "item_id": item.item_id,
                        "estimate_hours": item.current_estimate_hrs,
                        "recommended_splits": splits,
                    },
                    reason=reason,
                    implementation_effort="Medium",
                    confidence="Medium",
                )
            )
        return candidates

    def _generate_cp_optimization_recommendations(self) -> List[RecommendationCandidate]:
        items = [
            self.work_items[item_id]
            for item_id in self.cp_result.items_on_critical_path
            if item_id in self.work_items and self.work_items[item_id].status != self.work_items[item_id].status.COMPLETED
        ]
        if not items:
            return []

        items.sort(key=lambda wi: wi.remaining_effort_hrs, reverse=True)
        top_items = items[:3]
        action = f"Optimize critical path items: {', '.join(i.item_id for i in top_items)}"
        reason = (
            f"These critical path items represent the largest remaining work on the schedule and are highest risk. "
            f"Targeting them can reduce the overall project delay."
        )
        return [
            RecommendationCandidate(
                recommendation_id=self._stable_id(RecommendationType.CRITICAL_PATH_OPTIMIZATION, [i.item_id for i in top_items]),
                type=RecommendationType.CRITICAL_PATH_OPTIMIZATION,
                action=action,
                target_ids=[i.item_id for i in top_items],
                details={
                    "critical_items": [i.item_id for i in top_items],
                    "total_remaining_hours": sum(i.remaining_effort_hrs for i in top_items),
                },
                reason=reason,
                implementation_effort="Medium",
                confidence="Medium",
            )
        ]

    # ------------------------------------------------------------------
    # Simulation helpers
    # ------------------------------------------------------------------
    def _simulate_candidate(self, candidate: RecommendationCandidate) -> None:
        action = self._candidate_to_simulation_action(candidate)
        simulation_engine = SimulationEngine(
            project_state=self.project_state,
            metrics=self.metrics,
            dag=self.dag,
            cp_result=self.cp_result,
            spillover=self.spillover,
            forecast=self.forecast,
            monte_carlo=self.monte_carlo,
            risk_result=self.risk_result,
            simulation_count=self.simulation_count,
        )
        result = simulation_engine.simulate_recommendation_actions([action])
        candidate.after_probability = result.simulated_probability
        candidate.after_delay_days = result.simulated_delay_days
        candidate.after_risk_score = result.simulated_risk_score
        candidate.raw_expected_probability_gain = round(
            candidate.after_probability - candidate.baseline_probability, 4
        )
        candidate.expected_probability_gain = candidate.raw_expected_probability_gain
        candidate.expected_delay_gain_days = round(
            candidate.baseline_delay_days - candidate.after_delay_days, 2
        )
        candidate.raw_expected_risk_reduction = round(
            candidate.baseline_risk_score - candidate.after_risk_score, 2
        )
        candidate.expected_risk_reduction = candidate.raw_expected_risk_reduction
        self._apply_noise_thresholds(candidate)
        self._populate_impact_metadata(candidate)

    def _candidate_to_simulation_action(self, candidate: RecommendationCandidate):
        return SimulationAction(
            action_id=candidate.recommendation_id,
            action_type=candidate.type.value,
            target_ids=candidate.target_ids,
            details=candidate.details,
            impact_reason=candidate.reason or candidate.impact_summary,
        )

    def _apply_candidate(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        if candidate.type == RecommendationType.RESOLVE_BLOCKER:
            self._apply_resolve_blocker(clone, candidate)
        elif candidate.type == RecommendationType.ADD_RESOURCE:
            self._apply_add_resource(clone, candidate)
        elif candidate.type == RecommendationType.REASSIGN_WORK:
            self._apply_reassign_work(clone, candidate)
        elif candidate.type == RecommendationType.REDUCE_ITEM_SCOPE:
            self._apply_reduce_item_scope(clone, candidate)
        elif candidate.type == RecommendationType.PARALLELIZE_TASKS:
            self._apply_parallelize_tasks(clone, candidate)
        elif candidate.type == RecommendationType.MOVE_BLOCKER_ITEMS:
            self._apply_move_blocker_items(clone, candidate)
        elif candidate.type == RecommendationType.SPLIT_TASK:
            self._apply_split_task(clone, candidate)
        elif candidate.type == RecommendationType.CRITICAL_PATH_OPTIMIZATION:
            self._apply_critical_path_optimization(clone, candidate)
        else:
            raise RecommendationError(f"Unsupported recommendation type: {candidate.type}")

    def _recalculate_summary(self, state: ProjectState) -> Dict[str, float]:
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
            simulation_count=self.simulation_count,
            seed=42,
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
        return {
            "probability": monte_carlo.on_time_probability,
            "delay_days": forecast.expected_delay_days,
            "risk_score": risk_result.overall_risk_score,
        }

    def _baseline_summary(self) -> Dict[str, float]:
        return {
            "probability": self.monte_carlo.on_time_probability,
            "delay_days": self.forecast.expected_delay_days,
            "risk_score": self.risk_result.overall_risk_score,
        }

    # ------------------------------------------------------------------
    # Virtual change implementations
    # ------------------------------------------------------------------
    def _apply_resolve_blocker(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        if not candidate.target_ids:
            return
        blocker_id = candidate.target_ids[0]
        matching = [b for b in clone.blockers if b.blocker_id == blocker_id]
        if not matching:
            return
        blocker = matching[0]
        blocker.status = BlockerStatus.RESOLVED
        blocker.actual_resolution_date = datetime.utcnow()
        self._unblock_impacted_items(clone, blocker)

    def _unblock_impacted_items(self, clone: ProjectState, blocker: Blocker) -> None:
        for impacted_item_id in getattr(blocker, "impacted_item_ids", []) or []:
            item = next((wi for wi in clone.work_items if wi.item_id == impacted_item_id), None)
            if item and item.status == WorkItemStatus.BLOCKED:
                item.status = (
                    WorkItemStatus.IN_PROGRESS
                    if item.progress_pct > 0.0 or item.actual_effort_hrs > 0.0
                    else WorkItemStatus.NOT_STARTED
                )

    def _apply_add_resource(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        skill = candidate.details.get("skill")
        role = candidate.details.get("role") or "Engineer"
        new_resource_id = f"RECR-{len(clone.team)+1}"
        clone.team.append(
            Resource(
                resource_id=new_resource_id,
                name=f"Recommended {role}",
                role=role,
                primary_skill=skill or "General",
                secondary_skill=None,
                skill_level=self._find_best_skill_level(skill),
                allocation_pct=0.1,
                availability_pct=1.0,
            )
        )
        # New resource already appended above as availability_pct=1.0, allocation_pct=0.1
        # Increase allocation to reflect full contribution
        new_resource = clone.team[-1]
        new_resource.allocation_pct = min(1.0, new_resource.allocation_pct + 0.9)

    def _apply_reassign_work(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        if not candidate.target_ids:
            return
        item_id = candidate.target_ids[0]
        item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
        if not item:
            return
        to_name = candidate.details.get("to")
        if not to_name:
            return
        new_resource = next((r for r in clone.team if r.name == to_name), None)
        old_resource = next((r for r in clone.team if r.resource_id == item.assigned_resource), None)
        if new_resource:
            item.assigned_resource = new_resource.resource_id
            if old_resource:
                old_resource.allocation_pct = max(0.0, old_resource.allocation_pct - 0.1)
            new_resource.allocation_pct = min(1.0, new_resource.allocation_pct + 0.1)

    def _apply_reduce_item_scope(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        if not candidate.target_ids:
            return
        item_id = candidate.target_ids[0]
        item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
        if not item:
            return
        core_hours = float(candidate.details.get("core_hours", item.current_estimate_hrs))
        reduction = max(0.0, item.current_estimate_hrs - core_hours)
        item.current_estimate_hrs = max(0.0, core_hours)
        item.remaining_effort_hrs = max(0.0, item.remaining_effort_hrs - reduction)
        item.is_scope_changed = True
        item.scope_change_reason = (
            f"Sprint Whisperer scope reduction — core: {item.current_estimate_hrs}h retained, "
            f"{reduction:.1f}h deferred post-release"
        )

    def _apply_parallelize_tasks(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        if not candidate.target_ids or len(candidate.target_ids) < 2:
            return
        pred_id, succ_id = candidate.target_ids[0], candidate.target_ids[1]
        successor = next((wi for wi in clone.work_items if wi.item_id == succ_id), None)
        if not successor:
            return
        reduction = successor.remaining_effort_hrs * 0.25
        successor.remaining_effort_hrs = max(0.0, successor.remaining_effort_hrs - reduction)
        successor.current_estimate_hrs = max(0.0, successor.current_estimate_hrs - reduction)

    def _apply_move_blocker_items(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        for item_id in candidate.details.get("advanceable_items", []) or []:
            item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
            if item and item.status == item.status.NOT_STARTED:
                item.status = item.status.IN_PROGRESS

    def _apply_split_task(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        if not candidate.target_ids:
            return
        item_id = candidate.target_ids[0]
        item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
        if not item:
            return
        reduction = item.current_estimate_hrs * 0.20
        item.current_estimate_hrs = max(1.0, item.current_estimate_hrs - reduction)
        item.remaining_effort_hrs = max(0.0, item.remaining_effort_hrs - reduction)

    def _apply_critical_path_optimization(self, clone: ProjectState, candidate: RecommendationCandidate) -> None:
        if not candidate.target_ids:
            return
        for item_id in candidate.target_ids:
            item = next((wi for wi in clone.work_items if wi.item_id == item_id), None)
            if not item:
                continue
            reduction = item.current_estimate_hrs * 0.15
            item.current_estimate_hrs = max(1.0, item.current_estimate_hrs - reduction)
            item.remaining_effort_hrs = max(0.0, item.remaining_effort_hrs - reduction)

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------
    def _find_candidate_by_id(self, recommendation_id: str) -> Optional[RecommendationCandidate]:
        candidates = self._cached_candidates if self._cached_candidates else self.generate_recommendations()
        for candidate in candidates:
            if candidate.recommendation_id == recommendation_id:
                return candidate
        return None

    def _score_candidate(self, candidate: RecommendationCandidate) -> None:
        prob_gain = candidate.expected_probability_gain
        delay_gain = max(0.0, candidate.expected_delay_gain_days)
        risk_gain = max(0.0, candidate.expected_risk_reduction)
        confidence_score = self._confidence_value(candidate.confidence)
        effort_score = self._effort_value(candidate.implementation_effort)

        delay_norm = min(delay_gain / 30.0, 1.0)
        priority = (
            0.35 * prob_gain
            + 0.30 * delay_norm
            + 0.20 * (risk_gain / 100.0)
            + 0.10 * confidence_score
            + 0.05 * effort_score
        )

        candidate.priority_score = max(0.0, min(100.0, priority * 100.0))

    def _populate_impact_metadata(self, candidate: RecommendationCandidate) -> None:
        candidate.impact_level = self._determine_impact_level(candidate)
        candidate.impact_confidence = self._determine_impact_confidence(candidate)
        candidate.impact_classification = self._determine_impact_classification(candidate)
        candidate.business_impact = self._describe_business_impact(candidate)
        candidate.impact_summary = self._build_impact_summary(candidate)

    def _apply_noise_thresholds(self, candidate: RecommendationCandidate) -> None:
        probability_gain = candidate.raw_expected_probability_gain
        risk_reduction = candidate.raw_expected_risk_reduction

        if abs(probability_gain) <= self.PROBABILITY_NOISE_THRESHOLD:
            candidate.expected_probability_gain = 0.0
        if abs(risk_reduction) <= self.RISK_NOISE_THRESHOLD:
            candidate.expected_risk_reduction = 0.0

    def _determine_impact_confidence(self, candidate: RecommendationCandidate) -> str:
        if (
            abs(candidate.raw_expected_probability_gain) <= self.PROBABILITY_NOISE_THRESHOLD
            and abs(candidate.raw_expected_risk_reduction) <= self.RISK_NOISE_THRESHOLD
        ):
            return "Low"
        if (
            abs(candidate.raw_expected_probability_gain) <= self.PROBABILITY_NOISE_THRESHOLD * 2
            or abs(candidate.raw_expected_risk_reduction) <= self.RISK_NOISE_THRESHOLD * 2
        ):
            return "Medium"
        return "High"

    def _determine_impact_classification(self, candidate: RecommendationCandidate) -> str:
        if (
            abs(candidate.raw_expected_probability_gain) <= self.PROBABILITY_NOISE_THRESHOLD
            and abs(candidate.raw_expected_risk_reduction) <= self.RISK_NOISE_THRESHOLD
        ):
            return "Negligible Impact"
        if candidate.expected_probability_gain < 0 or candidate.expected_risk_reduction < 0:
            return "Negative Impact"
        return "Positive Impact"

    def _determine_impact_level(self, candidate: RecommendationCandidate) -> str:
        if (
            candidate.expected_probability_gain >= 0.10
            or candidate.expected_delay_gain_days >= 5.0
            or candidate.expected_risk_reduction >= 10.0
        ):
            return "High"
        if (
            candidate.expected_probability_gain >= 0.05
            or candidate.expected_delay_gain_days >= 2.0
            or candidate.expected_risk_reduction >= 5.0
        ):
            return "Medium"
        return "Low"

    def _describe_business_impact(self, candidate: RecommendationCandidate) -> str:
        impact_text = {
            RecommendationType.RESOLVE_BLOCKER: "Resolves a blocker affecting critical path work and reduces downstream schedule risk.",
            RecommendationType.ADD_RESOURCE: "Adds capacity to relieve a skill bottleneck and improve on-time delivery.",
            RecommendationType.REASSIGN_WORK: "Aligns the right skill to the right task to improve execution speed and schedule predictability.",
            RecommendationType.REDUCE_ITEM_SCOPE: "Reduces scope risk on lower-priority work to protect the delivery date.",
            RecommendationType.PARALLELIZE_TASKS: "Enables parallel execution of adjacent tasks to shorten the effective critical path.",
            RecommendationType.MOVE_BLOCKER_ITEMS: "Keeps progress moving by advancing ready work around an existing blocker.",
            RecommendationType.SPLIT_TASK: "Breaks up a large task to improve predictability and reduce execution variance.",
            RecommendationType.CRITICAL_PATH_OPTIMIZATION: "Targets high-risk critical path items to reduce overall project delay.",
        }
        default_text = "Improves project delivery confidence and lowers schedule risk."
        return impact_text.get(candidate.type, default_text)

    def _build_impact_summary(self, candidate: RecommendationCandidate) -> str:
        if candidate.impact_classification == "Negligible Impact":
            return "Expected impact is within Monte Carlo noise and is therefore negligible."

        probability_gain_pct = round(candidate.expected_probability_gain * 100.0, 1)
        delay_gain_days = round(candidate.expected_delay_gain_days, 1)
        if probability_gain_pct > 0 or delay_gain_days > 0:
            return (
                f"Expected to increase on-time delivery probability by {probability_gain_pct:.1f}% "
                f"and reduce expected delay by {delay_gain_days:.1f} days."
            )
        return "Expected to provide modest schedule risk reduction."

    def _display_probability_gain(self) -> float:
        return self.expected_probability_gain

    def _display_risk_reduction(self) -> float:
        return self.expected_risk_reduction

    def _confidence_value(self, confidence: str) -> float:
        return {"High": 1.0, "Medium": 0.7, "Low": 0.4}.get(confidence, 0.5)

    def _effort_value(self, effort: str) -> float:
        return {"Low": 1.0, "Medium": 0.6, "High": 0.3}.get(effort, 0.5)

    # ------------------------------------------------------------------
    # Utility and scoring
    # ------------------------------------------------------------------
    def _stable_id(self, rec_type: RecommendationType, target_ids: List[str]) -> str:
        key = f"{rec_type.value}::{':'.join(sorted(target_ids))}"
        return "REC-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper()

    def _find_current_sprint(self):
        for sprint in self.project_state.sprints:
            if sprint.status.value == "In Progress":
                return sprint
        for sprint in self.project_state.sprints:
            if sprint.status.value == "Not Started":
                return sprint
        return None

    def _build_skill_demand(self) -> Dict[str, float]:
        demand: Dict[str, float] = {}
        for item in self.project_state.work_items:
            if item.status in {item.status.DONE, item.status.COMPLETED}:
                continue
            demand[item.required_skill] = demand.get(item.required_skill, 0.0) + max(
                item.remaining_effort_hrs, item.current_estimate_hrs
            )
        return demand

    def _build_skill_capacity(self) -> Dict[str, float]:
        capacity: Dict[str, float] = {}
        for resource in self.project_state.team:
            available_hours = resource.availability_pct * resource.allocation_pct * 8.0 * 5.0
            capacity[resource.primary_skill] = capacity.get(resource.primary_skill, 0.0) + available_hours
            if resource.secondary_skill:
                capacity[resource.secondary_skill] = capacity.get(resource.secondary_skill, 0.0) + available_hours * 0.5
        return capacity

    def _suggest_role_for_skill(self, skill: str) -> str:
        roles = [r.role for r in self.project_state.team if r.primary_skill == skill]
        return roles[0] if roles else f"{skill} Engineer"

    def _find_best_skill_level(self, skill: Optional[str]) -> str:
        levels = [r.skill_level.value for r in self.project_state.team if r.primary_skill == skill]
        return levels[0] if levels else "Mid"

    def _find_alternate_resource(self, required_skill: str, current_resource_id: str) -> Optional[Resource]:
        candidates = []
        for resource in self.project_state.team:
            if resource.resource_id == current_resource_id:
                continue
            if required_skill in {resource.primary_skill, resource.secondary_skill} and resource.availability_pct > 0.2:
                candidates.append(resource)
        candidates.sort(key=lambda r: (r.allocation_pct, -r.availability_pct))
        return candidates[0] if candidates else None

    def _descope_score(self, item: WorkItem) -> float:
        priority_value = {
            item.priority.CRITICAL: 0.0,
            item.priority.HIGH: 0.3,
            item.priority.MEDIUM: 0.6,
            item.priority.LOW: 1.0,
        }.get(item.priority, 0.5)
        effort_norm = min(item.current_estimate_hrs / 80.0, 1.0)
        dependency_count = len(self.dag.graph.get(item.item_id, [])) + len(self.dag.reverse_graph.get(item.item_id, []))
        dep_norm = 1.0 - min(dependency_count / 5.0, 1.0)
        return 0.35 * priority_value + 0.30 * effort_norm + 0.25 * dep_norm + 0.10 * (1.0 if item.status == item.status.NOT_STARTED else 0.0)

    def _split_candidate_score(self, item: WorkItem) -> float:
        critical_bonus = 1.0 if item.item_id in self.cp_result.items_on_critical_path else 0.5
        effort_norm = min(item.current_estimate_hrs / 120.0, 1.0)
        dependency_count = len(self.dag.graph.get(item.item_id, [])) + len(self.dag.reverse_graph.get(item.item_id, []))
        dep_norm = min(dependency_count / 5.0, 1.0)
        return 0.40 * critical_bonus + 0.35 * effort_norm + 0.25 * dep_norm

    def _blocker_priority(self, blocker: Blocker) -> float:
        severity_value = {
            blocker.severity.CRITICAL: 1.0,
            blocker.severity.HIGH: 0.8,
            blocker.severity.MEDIUM: 0.5,
            blocker.severity.LOW: 0.2,
        }.get(blocker.severity, 0.4)
        affected_items = len(blocker.impacted_item_ids)
        downstream = self._count_downstream(blocker)
        cp_involvement = self._count_critical_path_involvement(blocker)
        proximity = 1.0 if self._is_blocker_in_current_sprint(blocker) else 0.5
        return (
            0.25 * severity_value
            + 0.20 * min(cp_involvement / max(1, affected_items), 1.0)
            + 0.20 * min(downstream / 10.0, 1.0)
            + 0.20 * min(affected_items / 10.0, 1.0)
            + 0.15 * proximity
        )

    def _blocker_reason(self, blocker: Blocker) -> str:
        downstream = self._count_downstream(blocker)
        cp_items = self._count_critical_path_involvement(blocker)
        category = getattr(blocker, "category", None)
        category_value = getattr(category, "value", "Other")
        notes = getattr(blocker, "notes", None) or ""
        hint = BLOCKER_CATEGORY_HINTS.get(category_value, BLOCKER_CATEGORY_HINTS["Other"])
        notes_context = ""
        if notes:
            impact_idx = notes.upper().find("IMPACT:")
            notes_context = (
                notes[impact_idx:impact_idx+120].strip() if impact_idx >= 0 else notes[:120].strip()
            )
        return (
            f"Blocks {len(blocker.impacted_item_ids)} items directly "
            f"and {downstream} downstream items, "
            f"including {cp_items} on the critical path. "
            f"Severity: {blocker.severity.value}. "
            f"Category: {category_value}. "
            f"{notes_context + ' ' if notes_context else ''}"
            f"Recommended action: {hint}"
        )

    def _blocker_recommendation_actions(self, blocker: Blocker) -> List[str]:
        category = getattr(blocker, "category", BlockerCategory.OTHER)
        actions = BLOCKER_CATEGORY_ACTIONS.get(category.value)
        if actions:
            return actions

        if category == BlockerCategory.HARDWARE:
            return BLOCKER_CATEGORY_ACTIONS.get("Hardware / Procurement", [])
        if category == BlockerCategory.VENDOR:
            return BLOCKER_CATEGORY_ACTIONS.get("Vendor", [])
        return BLOCKER_CATEGORY_ACTIONS.get("Other", [])

    def _count_downstream(self, blocker: Blocker) -> int:
        downstream = 0
        for item_id in blocker.impacted_item_ids:
            downstream += len(self.dag.transitive_closure.get(item_id, []))
        return downstream

    def _count_critical_path_involvement(self, blocker: Blocker) -> int:
        return sum(1 for item_id in blocker.impacted_item_ids if item_id in self.cp_result.items_on_critical_path)

    def _is_blocker_in_current_sprint(self, blocker: Blocker) -> bool:
        if not self.current_sprint:
            return False
        sprint_id = self.current_sprint.sprint_id
        sprint_name = self.current_sprint.sprint_name
        for item_id in blocker.impacted_item_ids:
            work_item = self.work_items.get(item_id)
            if not work_item:
                continue
            if work_item.assigned_sprint in {sprint_id, sprint_name}:
                return True
        return False
