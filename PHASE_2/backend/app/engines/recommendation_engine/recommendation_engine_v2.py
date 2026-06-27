from __future__ import annotations

from typing import List, Optional

from app.domain.models import ProjectState
from app.engines.recommendations.candidate_generator import CandidateGenerator
from app.engines.recommendations.impact_estimator import ImpactEstimator
from app.engines.recommendations.models import (
    Recommendation,
    RecommendationCandidate,
    ScoringWeights,
    SimulationResult,
    UpstreamEngineOutputs,
)
from app.engines.recommendations.priority_engine import PriorityEngine
from app.engines.recommendations.signal_detectors import (
    BlockerDetector,
    CapacityDetector,
    CriticalPathDetector,
    ScheduleDetector,
    SprintDetector,
)
from app.engines.recommendations.simulation_engine_v2 import EngineRunner, SimulationEngineV2


class RecommendationEngineV2:
    """
    Orchestrates the full V2 pipeline.
    Computes upstream once per instance (cached).
    """

    def __init__(
        self,
        project_state: ProjectState,
        simulation_count: int = 1000,
        scoring_weights: Optional[ScoringWeights] = None,
    ):
        self.project_state = project_state
        self.simulation_count = simulation_count
        self.scoring_weights = scoring_weights or ScoringWeights()
        self._upstream: Optional[UpstreamEngineOutputs] = None
        self._cached_recommendations: List[Recommendation] = []

    def generate(self, top_n: int = 10) -> List[Recommendation]:
        """
        Full pipeline:
        1. Compute upstream (with seed=42)
        2. Detect signals (all five detectors)
        3. Generate candidates
        4. Estimate impacts
        5. Score and rank
        6. Return top_n
        """
        upstream = self._compute_upstream()
        signals = []
        signals.extend(BlockerDetector(self.project_state, upstream.cp_result, upstream.dag, upstream.impact_scores).detect())
        signals.extend(CapacityDetector(self.project_state, upstream.metrics, upstream.cp_result, upstream.impact_scores).detect())
        signals.extend(SprintDetector(self.project_state, upstream.metrics, upstream.spillover, upstream.forecast).detect())
        signals.extend(CriticalPathDetector(self.project_state, upstream.cp_result, upstream.dag, upstream.impact_scores).detect())
        signals.extend(ScheduleDetector(self.project_state, upstream.forecast, upstream.monte_carlo, upstream.risk_result, upstream.metrics).detect())

        candidates = CandidateGenerator(self.project_state, upstream).generate(signals)
        impact_estimates = {candidate.recommendation_id: ImpactEstimator(self.project_state, upstream).estimate(candidate) for candidate in candidates}
        ranked = PriorityEngine(upstream, self.scoring_weights).score_and_rank(candidates, impact_estimates)

        actionable = [rec for rec in ranked if rec.affected_item_ids or rec.affected_resource_ids or rec.affected_blocker_ids]
        actionable = self._deduplicate(actionable)
        self._cached_recommendations = actionable[:top_n]
        return list(self._cached_recommendations)

    def simulate(self, recommendation_id: str) -> SimulationResult:
        """
        Find recommendation by ID in cached generate() results.
        If generate() not called yet, call it first.
        Run SimulationEngineV2.simulate().
        """
        if not self._cached_recommendations:
            self.generate()
        recommendation = next((rec for rec in self._cached_recommendations if rec.recommendation_id == recommendation_id), None)
        if recommendation is None:
            raise KeyError(f"Recommendation {recommendation_id} not found")
        upstream = self._compute_upstream()
        engine = SimulationEngineV2(self.project_state, upstream, simulation_count=self.simulation_count)
        return engine.simulate(recommendation)

    def simulate_scenario(self, recommendation_ids: List[str]) -> SimulationResult:
        """
        Resolve all recommendation_ids from cache.
        Run SimulationEngineV2.simulate_scenario().
        """
        if not self._cached_recommendations:
            self.generate()
        recommendations = [rec for rec in self._cached_recommendations if rec.recommendation_id in set(recommendation_ids)]
        if not recommendations:
            raise KeyError("No matching recommendations found")
        upstream = self._compute_upstream()
        engine = SimulationEngineV2(self.project_state, upstream, simulation_count=self.simulation_count)
        return engine.simulate_scenario(recommendations)

    def _compute_upstream(self) -> UpstreamEngineOutputs:
        """
        Run EngineRunner.run(self.project_state).
        Cache result in self._upstream.
        """
        if self._upstream is None:
            self._upstream = EngineRunner().run(self.project_state, simulation_count=self.simulation_count)
        return self._upstream

    def _deduplicate(self, recommendations: List[Recommendation]) -> List[Recommendation]:
        seen = set()
        deduped: List[Recommendation] = []
        for rec in recommendations:
            if rec.recommendation_id in seen:
                continue
            seen.add(rec.recommendation_id)
            deduped.append(rec)
        return deduped
