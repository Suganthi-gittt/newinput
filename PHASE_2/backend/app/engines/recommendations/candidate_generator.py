from __future__ import annotations

from typing import Any, Dict, List

from app.domain.models import ProjectState
from app.engines.recommendations.models import (
    OpportunitySignal,
    RecommendationAction,
    RecommendationCandidate,
    SignalCategory,
    UpstreamEngineOutputs,
    stable_id,
)


class CandidateGenerator:
    def __init__(self, project_state: ProjectState, upstream: UpstreamEngineOutputs) -> None:
        self.project_state = project_state
        self.upstream = upstream

    def generate(self, signals: List[OpportunitySignal]) -> List[RecommendationCandidate]:
        emitted: Dict[str, RecommendationCandidate] = {}
        for signal in signals:
            if signal.category == SignalCategory.BLOCKER:
                for candidate in self._from_blocker_signal(signal):
                    self._deduplicate(emitted, candidate)
            elif signal.category == SignalCategory.CAPACITY:
                for candidate in self._from_capacity_signal(signal):
                    self._deduplicate(emitted, candidate)
            elif signal.category == SignalCategory.SPRINT:
                for candidate in self._from_sprint_signal(signal):
                    self._deduplicate(emitted, candidate)
            elif signal.category == SignalCategory.CRITICAL_PATH:
                for candidate in self._from_critical_path_signal(signal):
                    self._deduplicate(emitted, candidate)
            elif signal.category == SignalCategory.SCHEDULE:
                for candidate in self._from_schedule_signal(signal):
                    self._deduplicate(emitted, candidate)

        return [candidate for candidate in emitted.values() if self._check_feasibility(candidate)]

    def _from_blocker_signal(self, signal: OpportunitySignal) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        blocker_ids = signal.affected_blocker_ids or []
        if blocker_ids:
            blocker_id = blocker_ids[0]

            # Look up actual blocker from project state for rich, actionable context
            blocker = next(
                (b for b in self.project_state.blockers if b.blocker_id == blocker_id),
                None,
            )
            category = blocker.category.value if blocker else "Unknown"
            owner = (blocker.owner or "Unassigned") if blocker else "Unassigned"
            impacted = signal.affected_item_ids
            impacted_summary = ", ".join(impacted[:3]) + (f" (+{len(impacted) - 3} more)" if len(impacted) > 3 else "")

            # Title: short, actionable, shown on the card
            title = f"Resolve {category} blocker — {blocker_id} (Owner: {owner})"

            # Description: truncated escalation notes + impacted items
            raw_notes = (blocker.description or "") if blocker else ""
            short_notes = (raw_notes[:200] + "…") if len(raw_notes) > 200 else raw_notes
            description = (
                f"{short_notes} | Blocking {len(impacted)} item(s): {impacted_summary}"
                if short_notes
                else f"{blocker_id} is blocking {len(impacted)} item(s): {impacted_summary}"
            )

            candidates.append(self._build_candidate(
                action_type=RecommendationAction.RESOLVE_BLOCKER,
                title=title,
                description=description,
                affected_item_ids=signal.affected_item_ids,
                affected_resource_ids=[],
                affected_sprint_ids=signal.affected_sprint_ids,
                affected_blocker_ids=[blocker_id],
                root_signal_id=signal.signal_id,
                simulation_params={"target_blocker_id": blocker_id},
                feasibility_checks={"blocker_active": True},
            ))
        for item_id in signal.affected_item_ids[:1]:
            candidates.append(self._build_candidate(
                action_type=RecommendationAction.ADVANCE_ITEM_TO_EARLIER_SPRINT,
                title=f"Advance item ({item_id})",
                description=f"Advance work item {item_id} to an earlier sprint",
                affected_item_ids=[item_id],
                affected_resource_ids=[],
                affected_sprint_ids=signal.affected_sprint_ids,
                affected_blocker_ids=blocker_ids,
                root_signal_id=signal.signal_id,
                simulation_params={"target_item_id": item_id},
                feasibility_checks={"has_capacity": True},
            ))
        return candidates

    def _from_capacity_signal(self, signal: OpportunitySignal) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        if signal.affected_resource_ids:
            resource_id = signal.affected_resource_ids[0]
            item_id = signal.affected_item_ids[0] if signal.affected_item_ids else ""
            candidates.append(self._build_candidate(
                action_type=RecommendationAction.REASSIGN_ITEM,
                title=f"Reassign item ({item_id or resource_id})",
                description=f"Reassign work to resource {resource_id}",
                affected_item_ids=signal.affected_item_ids,
                affected_resource_ids=[resource_id],
                affected_sprint_ids=signal.affected_sprint_ids,
                affected_blocker_ids=signal.affected_blocker_ids,
                root_signal_id=signal.signal_id,
                simulation_params={"target_resource_id": resource_id, "target_item_id": item_id},
                feasibility_checks={"resource_exists": True, "has_capacity": True},
            ))
        return candidates

    def _from_sprint_signal(self, signal: OpportunitySignal) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        if signal.affected_item_ids:
            item_id = signal.affected_item_ids[0]
            candidates.append(self._build_candidate(
                action_type=RecommendationAction.ADVANCE_ITEM_TO_EARLIER_SPRINT,
                title=f"Advance item ({item_id})",
                description=f"Advance sprint-bound item {item_id}",
                affected_item_ids=[item_id],
                affected_resource_ids=[],
                affected_sprint_ids=signal.affected_sprint_ids,
                affected_blocker_ids=signal.affected_blocker_ids,
                root_signal_id=signal.signal_id,
                simulation_params={"target_item_id": item_id},
                feasibility_checks={"has_capacity": True},
            ))
        return candidates

    def _from_critical_path_signal(self, signal: OpportunitySignal) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        for item_id in signal.affected_item_ids[:2]:
            candidates.append(self._build_candidate(
                action_type=RecommendationAction.ADVANCE_ITEM_TO_EARLIER_SPRINT,
                title=f"Advance item ({item_id})",
                description=f"Protect critical path item {item_id}",
                affected_item_ids=[item_id],
                affected_resource_ids=[],
                affected_sprint_ids=signal.affected_sprint_ids,
                affected_blocker_ids=signal.affected_blocker_ids,
                root_signal_id=signal.signal_id,
                simulation_params={"target_item_id": item_id},
                feasibility_checks={"has_capacity": True},
            ))
        return candidates

    def _from_schedule_signal(self, signal: OpportunitySignal) -> List[RecommendationCandidate]:
        candidates: List[RecommendationCandidate] = []
        if not signal.affected_item_ids:
            return candidates
        
        # Get the context flag to understand which type of schedule issue this is
        flag = signal.context.get("flag", "SCHEDULE_GAP") if signal.context else "SCHEDULE_GAP"
        schedule_gap_hours = float(signal.context.get("schedule_gap_hours", 0.0)) if signal.context else 0.0
        
        # Generate SPLIT_ITEM candidate for the first item
        item_id = signal.affected_item_ids[0]
        candidates.append(self._build_candidate(
            action_type=RecommendationAction.SPLIT_ITEM,
            title=f"Split item ({item_id})",
            description=f"Split work item {item_id} to reduce schedule pressure",
            affected_item_ids=[item_id],
            affected_resource_ids=[],
            affected_sprint_ids=signal.affected_sprint_ids,
            affected_blocker_ids=signal.affected_blocker_ids,
            root_signal_id=signal.signal_id,
            simulation_params={"target_item_id": item_id},
            feasibility_checks={"item_large_enough": True},
        ))
        
        # Generate REBALANCE_SPRINT_LOAD candidate
        candidates.append(self._build_candidate(
            action_type=RecommendationAction.REBALANCE_SPRINT_LOAD,
            title="Rebalance sprint load",
            description=f"Rebalance work items across sprints to address schedule pressure (gap: {schedule_gap_hours:.1f}h)",
            affected_item_ids=signal.affected_item_ids[:2],  # Top 2 items
            affected_resource_ids=[],
            affected_sprint_ids=signal.affected_sprint_ids,
            affected_blocker_ids=signal.affected_blocker_ids,
            root_signal_id=signal.signal_id,
            simulation_params={"gap_hours": schedule_gap_hours},
            feasibility_checks={"has_future_sprints": True},
        ))
        
        # Generate ADD_RESOURCE_SKILL candidate when gap is large
        if schedule_gap_hours > 20.0:
            # Try to infer required skill from the top affected item for a richer candidate
            required_skill = "General"
            if signal.affected_item_ids:
                item_id = signal.affected_item_ids[0]
                item = next((wi for wi in self.project_state.work_items if wi.item_id == item_id), None)
                if item and getattr(item, "required_skill", None):
                    required_skill = item.required_skill

            candidates.append(self._build_candidate(
                action_type=RecommendationAction.ADD_RESOURCE_SKILL,
                title="Add resource capacity",
                description=f"Add resources or increase capacity to close schedule gap ({schedule_gap_hours:.1f}h)",
                affected_item_ids=signal.affected_item_ids[:1],
                affected_resource_ids=[],
                affected_sprint_ids=signal.affected_sprint_ids,
                affected_blocker_ids=signal.affected_blocker_ids,
                root_signal_id=signal.signal_id,
                simulation_params={"gap_hours": schedule_gap_hours, "required_skill": required_skill},
                feasibility_checks={"budget_available": True},
            ))
        
        return candidates

    def _deduplicate(self, existing: Dict[str, RecommendationCandidate], new: RecommendationCandidate) -> None:
        existing_candidate = existing.get(new.recommendation_id)
        if existing_candidate is None:
            existing[new.recommendation_id] = new
            return
        merged_ids = sorted(set(existing_candidate.supporting_signal_ids) | set(new.supporting_signal_ids))
        existing[existing_candidate.recommendation_id] = RecommendationCandidate(
            recommendation_id=existing_candidate.recommendation_id,
            action_type=existing_candidate.action_type,
            title=existing_candidate.title,
            description=existing_candidate.description,
            affected_item_ids=existing_candidate.affected_item_ids,
            affected_resource_ids=existing_candidate.affected_resource_ids,
            affected_sprint_ids=existing_candidate.affected_sprint_ids,
            affected_blocker_ids=existing_candidate.affected_blocker_ids,
            root_cause_signal_id=existing_candidate.root_cause_signal_id,
            supporting_signal_ids=merged_ids,
            simulation_params=existing_candidate.simulation_params,
            feasibility_checks=existing_candidate.feasibility_checks,
        )

    def _check_feasibility(self, candidate: RecommendationCandidate) -> bool:
        return all(candidate.feasibility_checks.values()) if candidate.feasibility_checks else True

    def _build_candidate(
        self,
        *,
        action_type: RecommendationAction,
        title: str,
        description: str,
        affected_item_ids: List[str],
        affected_resource_ids: List[str],
        affected_sprint_ids: List[str],
        affected_blocker_ids: List[str],
        root_signal_id: str,
        simulation_params: Dict[str, Any],
        feasibility_checks: Dict[str, bool],
    ) -> RecommendationCandidate:
        target_ids = list(affected_item_ids) + list(affected_resource_ids) + list(affected_sprint_ids) + list(affected_blocker_ids)
        return RecommendationCandidate(
            recommendation_id=stable_id(action_type.value, target_ids),
            action_type=action_type,
            title=title,
            description=description,
            affected_item_ids=affected_item_ids,
            affected_resource_ids=affected_resource_ids,
            affected_sprint_ids=affected_sprint_ids,
            affected_blocker_ids=affected_blocker_ids,
            root_cause_signal_id=root_signal_id,
            supporting_signal_ids=[root_signal_id],
            simulation_params=simulation_params,
            feasibility_checks=feasibility_checks,
        )
