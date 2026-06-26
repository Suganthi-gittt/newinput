"""
Risk Engine (Phase 3.3)

Converts outputs from forecasting, metrics, and dependency engines into
explainable risk scores and drivers.

The Risk Engine answers: Why is this project at risk?
(Not just: will it miss the date, but WHY)

Key principle: Risk Engine is deterministic, uses only existing engine outputs,
and never invents risks or uses random numbers.
"""

from typing import List, Dict, Tuple, Optional
from pydantic import BaseModel

from app.domain.models import ProjectState, WorkItemStatus, SprintStatus, BlockerSeverity
from app.engines.metrics_engine import ProjectMetrics
from app.engines.critical_path_engine import CriticalPathResult
from app.engines.dependency_engine import DependencyDAG
from app.engines.spillover_engine import SpilloverAnalysis
from app.engines.forecast_engine import ForecastResult
from app.engines.monte_carlo_engine import MonteCarloResult
from app.engines.impact_scoring_engine import RiskScores

from app.api.models_phase3 import (
    RiskLevel,
    RiskDriver,
    SprintRisk,
    RiskExplanation,
    RiskResult,
)


class RiskEngine:
    """
    Analyzes project risk using outputs from existing engines.

    Risk scores are calculated deterministically from:
    - ForecastResult: expected delay, on-time status
    - MonteCarloResult: on-time probability, statistical distribution
    - ProjectMetrics: utilization, velocity, blockers, team allocation
    - CriticalPathResult: critical path length, items on critical path
    - DependencyDAG: dependency count, connectivity
    - SpilloverAnalysis: predicted spillovers, historical patterns
    - RiskScores: item-level risk from blockers and dependencies
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
        impact_scores: RiskScores,
    ):
        self.project_state = project_state
        self.metrics = metrics
        self.cp_result = cp_result
        self.dag = dag
        self.spillover = spillover
        self.forecast = forecast
        self.monte_carlo = monte_carlo
        self.impact_scores = impact_scores
        self.work_items = {wi.item_id: wi for wi in project_state.work_items}

        # Severity mapping and bonus used across dependency/sprint/recommendation logic
        self.SEVERITY_SCORES = {
            BlockerSeverity.CRITICAL: 40.0,
            BlockerSeverity.HIGH: 20.0,
            BlockerSeverity.MEDIUM: 10.0,
            BlockerSeverity.LOW: 5.0,
        }
        self.ADDITIONAL_BLOCKER_BONUS = 3.0

    # Weights for overall risk calculation
        self.weights = {
            "schedule": 0.40,
            "dependency": 0.25,
            "resource": 0.20,
            "scope": 0.15,
        }

    def analyze(self) -> RiskResult:
        """Analyze project risk and return comprehensive RiskResult."""

        # Calculate sub-scores with explanations
        schedule_risk_exp = self._calculate_schedule_risk()
        dependency_risk_exp = self._calculate_dependency_risk()
        self._blocker_resource_pts = 0.0
        resource_risk_exp = self._calculate_resource_risk()
        scope_risk_exp = self._calculate_scope_risk()

        # Extract scores
        schedule_score = schedule_risk_exp.score
        dependency_score = dependency_risk_exp.score
        resource_score = resource_risk_exp.score
        scope_score = scope_risk_exp.score

        # Calculate overall risk using weighted aggregation
        overall_score = (
            self.weights["schedule"] * schedule_score
            + self.weights["dependency"] * dependency_score
            + self.weights["resource"] * resource_score
            + self.weights["scope"] * scope_score
        )

        overall_level = self._score_to_level(overall_score)

        # Collect all risk drivers from sub-scores
        all_drivers = []
        all_drivers.extend(schedule_risk_exp.drivers)
        all_drivers.extend(dependency_risk_exp.drivers)
        all_drivers.extend(resource_risk_exp.drivers)
        all_drivers.extend(scope_risk_exp.drivers)

        category_weights = {
            "SCHEDULE": self.weights["schedule"],
            "DEPENDENCY": self.weights["dependency"],
            "RESOURCE": self.weights["resource"],
            "SCOPE": self.weights["scope"],
        }

        # Rank by effective driver score, with category weighting used as a tie-breaker.
        # This keeps the resulting list ordered by the visible risk score while preserving
        # the category-based emphasis that feeds the overall risk calculation.
        top_drivers = sorted(
            (d for d in all_drivers if d.score > 0.0),
            key=lambda d: (d.score, category_weights.get(d.category, 1.0)),
            reverse=True,
        )[:10]

        # Calculate sprint-level risks
        sprint_risks = self._calculate_sprint_risks()

        blocker_velocity_impact = float(
            getattr(self.metrics, "estimated_blocker_velocity_impact", 0.0) or 0.0
        )
        blocker_schedule_attribution = (
            1.0 if blocker_velocity_impact > 0.10 else blocker_velocity_impact / 0.10
        )
        blocker_schedule_pts = (
            self.weights["schedule"] * schedule_score * blocker_schedule_attribution
        )
        blocker_resource_pts = float(getattr(self, "_blocker_resource_pts", 0.0) or 0.0)
        blocker_risk_concentration = (
            (blocker_schedule_pts + blocker_resource_pts) / overall_score
            if overall_score > 0.0
            else 0.0
        )
        blocker_risk_concentration = max(0.0, min(1.0, blocker_risk_concentration))

        return RiskResult(
            overall_risk_score=overall_score,
            overall_risk_level=overall_level,
            schedule_risk=schedule_risk_exp,
            dependency_risk=dependency_risk_exp,
            resource_risk=resource_risk_exp,
            scope_risk=scope_risk_exp,
            top_risk_drivers=top_drivers,
            sprint_risks=sprint_risks,
            risk_vs_montecarlo_note=(
                "The overall risk score aggregates schedule, dependency, "
                "resource, and scope signals. Monte Carlo on-time probability "
                "reflects schedule probability only. A project can have HIGH "
                "overall risk due to dependency or resource exposure while "
                "still showing high on-time probability if schedule variance "
                "is low. These are complementary signals, not contradictions."
            ),
            blocker_risk_concentration=blocker_risk_concentration,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # SCHEDULE RISK CALCULATION
    # ──────────────────────────────────────────────────────────────────────────

    def _calculate_schedule_risk(self) -> RiskExplanation:
        """
        Calculate schedule risk based on:
        - On-time probability from Monte Carlo
        - Expected delay days
        - Critical path utilization
        """
        drivers: List[RiskDriver] = []
        reasons: List[str] = []
        risk_components = []

        # 1. On-time probability is a confidence modifier on the delay estimate
        on_time_prob = self.monte_carlo.on_time_probability

        # 2. Expected delay (deterministic primary signal)
        delay_days = self.forecast.expected_delay_days
        delay_component = (
            min(100.0, (delay_days / 30.0) * 80.0) if delay_days > 0 else 0.0
        )

        # Spillover can independently increase schedule risk when the forecast has no
        # delay signal of its own, which is how the blocker attribution tests model
        # spillover-driven schedule pressure.
        spillover_component = 0.0
        if self.spillover is not None:
            predicted_spillover_items = sum(
                float(value) for value in getattr(self.spillover, "predicted_spillover_by_sprint", {}).values()
            )
            spillover_component = min(100.0, predicted_spillover_items * 8.0)

        CONFIDENCE_WEIGHT = 0.20
        confidence_modifier = 1.0 + (1.0 - on_time_prob) * CONFIDENCE_WEIGHT
        schedule_primary = (
            min(100.0, delay_component * confidence_modifier)
            if delay_component > 0
            else 0.0
        )

        if schedule_primary <= 0.0 and spillover_component > 0.0:
            schedule_primary = spillover_component

        if on_time_prob < 0.25:
            drivers.append(
                RiskDriver(
                    category="SCHEDULE",
                    score=min(100.0, schedule_primary),
                    title="On-Time Probability (Confidence Modifier)",
                    description=(
                        f"Monte Carlo on-time probability is {on_time_prob*100:.1f}%. "
                        f"This modifies the delay estimate, increasing schedule risk when confidence is low."
                    ),
                    recommendation_hint="Review sprint capacity, identify velocity blockers, "
                    "consider scope reduction or timeline extension.",
                )
            )
            reasons.append(f"On-time probability only {on_time_prob*100:.1f}%")
        elif on_time_prob < 0.50:
            drivers.append(
                RiskDriver(
                    category="SCHEDULE",
                    score=min(100.0, schedule_primary),
                    title="On-Time Probability (Confidence Modifier)",
                    description=(
                        f"Monte Carlo on-time probability is {on_time_prob*100:.1f}%. "
                        f"This signal modifies the delay estimate rather than adding an independent schedule score."
                    ),
                    recommendation_hint="Accelerate critical path items, reduce dependencies.",
                )
            )
            reasons.append(f"On-time probability {on_time_prob*100:.1f}%")
        elif on_time_prob < 0.75:
            drivers.append(
                RiskDriver(
                    category="SCHEDULE",
                    score=min(100.0, schedule_primary),
                    title="On-Time Probability (Confidence Modifier)",
                    description=(
                        f"Monte Carlo on-time probability is {on_time_prob*100:.1f}%. "
                        f"This is used to modulate the current delay estimate rather than being a separate risk measure."
                    ),
                    recommendation_hint="Monitor critical path closely, prepare contingency plans.",
                )
            )
            reasons.append(f"On-time probability {on_time_prob*100:.1f}%")

        if delay_days > 0:
            if delay_days > 30:
                drivers.append(
                    RiskDriver(
                        category="SCHEDULE",
                        score=min(100.0, schedule_primary),
                        title="High Expected Delay",
                        description=(
                            f"Expected delay of {delay_days:.1f} days beyond target end date. "
                            f"Current velocity insufficient to meet committed date."
                        ),
                        recommendation_hint="Increase sprint velocity, reduce scope, or negotiate timeline.",
                    )
                )
                reasons.append(f"Expected delay {delay_days:.1f} days")
            elif delay_days > 10:
                drivers.append(
                    RiskDriver(
                        category="SCHEDULE",
                        score=min(100.0, schedule_primary),
                        title="Moderate Expected Delay",
                        description=(
                            f"Expected delay of {delay_days:.1f} days. "
                            f"At current pace, project will miss target."
                        ),
                        recommendation_hint="Accelerate delivery of critical path items.",
                    )
                )
                reasons.append(f"Expected delay {delay_days:.1f} days")

        # Optional informational spillover driver for explainability only
        if self.forecast.spillover_delay_days > 5.0:
            drivers.append(
                RiskDriver(
                    category="SCHEDULE",
                    score=0.0,
                    title="Spillover Schedule Impact",
                    description=(
                        f"Predicted spillover is contributing approximately "
                        f"{self.forecast.spillover_delay_days:.1f} days to the forecast delay "
                        f"(already included in expected delay above)."
                    ),
                    recommendation_hint="Monitor spillover impact in the forecast and adjust sprint scope or capacity accordingly.",
                )
            )
            reasons.append(
                f"Predicted spillover contributes {self.forecast.spillover_delay_days:.1f} days"
            )

        # 3. Critical path length (duration component)
        cp_remaining_days = self.cp_result.critical_path_remaining_hours / 8.0
        target_remaining_days = (
            self.project_state.project_info.target_end_date
            - self.project_state.project_info.start_date
        ).days
        if target_remaining_days > 0:
            cp_utilization = min(100.0, (cp_remaining_days / target_remaining_days) * 100.0)
            if cp_utilization > 90.0:
                drivers.append(
                    RiskDriver(
                        category="SCHEDULE",
                        score=min(100.0, (cp_utilization - 90.0) * 10.0),
                        title="Tight Critical Path",
                        description=f"Critical path spans {cp_remaining_days:.1f} days, "
                        f"leaving minimal margin ({100.0 - cp_utilization:.1f}%) for delays.",
                        recommendation_hint="Focus on critical path acceleration and blocker resolution.",
                    )
                )

        risk_components = []
        if schedule_primary > 0:
            risk_components.append(schedule_primary)
            if spillover_component > 0.0 and delay_component > 0.0:
                # Combine the forecast delay signal with a separate spillover-based
                # schedule pressure signal when both are present. This prevents
                # the schedule score from saturating prematurely at 100 for large
                # delays while still reflecting additional spillover risk.
                risk_components.append(spillover_component)

        if risk_components:
            schedule_score = sum(risk_components) / len(risk_components)
        else:
            schedule_score = 0.0

        return RiskExplanation(
            score=min(100.0, schedule_score),
            reasons=reasons,
            drivers=drivers,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # DEPENDENCY RISK CALCULATION
    # ──────────────────────────────────────────────────────────────────────────

    def _calculate_dependency_risk(self) -> RiskExplanation:
        """
        Calculate dependency risk based on:
        - Total dependency count
        - Number of items on critical path
        - Dependency chain depth
        - Bottleneck analysis
        - Blocker cascade impact
        """
        drivers: List[RiskDriver] = []
        reasons: List[str] = []
        risk_components = []

        # 1. Total dependency count (normalized)
        dep_count = self.metrics.dependency_count
        total_items = self.metrics.total_items
        dep_ratio = dep_count / total_items if total_items > 0 else 0.0

        # Benchmark: 1.5 deps per item is moderate, 2.5+ is high
        if dep_ratio > 2.5:
            dep_risk = min(100.0, (dep_ratio - 2.5) * 40.0 + 60.0)
            risk_components.append(dep_risk)
            drivers.append(
                RiskDriver(
                    category="DEPENDENCY",
                    score=min(100.0, dep_risk),
                    title="High Dependency Density",
                    description=f"{dep_count} dependencies across {total_items} items "
                    f"({dep_ratio:.2f} deps/item). Complex dependency network increases risk.",
                    recommendation_hint="Simplify dependency structure, decompose complex tasks.",
                )
            )
            reasons.append(f"{dep_count} dependencies ({dep_ratio:.2f} per item)")
        elif dep_ratio > 1.5:
            dep_risk = (dep_ratio - 1.5) * 20.0 + 30.0
            risk_components.append(dep_risk)
            drivers.append(
                RiskDriver(
                    category="DEPENDENCY",
                    score=min(100.0, dep_risk),
                    title="Moderate Dependency Density",
                    description=f"{dep_count} dependencies ({dep_ratio:.2f} per item). "
                    f"Moderate interdependency risk.",
                    recommendation_hint="Review high-degree dependencies for optimization.",
                )
            )
            reasons.append(f"{dep_count} dependencies")

        # 2. Critical path length (number of items on critical path)
        cp_items = len(self.cp_result.items_on_critical_path)
        if cp_items > 10:
            cp_risk = min(100.0, (cp_items - 10) * 5.0 + 50.0)
            risk_components.append(cp_risk)
            drivers.append(
                RiskDriver(
                    category="DEPENDENCY",
                    score=min(100.0, cp_risk),
                    title="Long Critical Path Chain",
                    description=f"{cp_items} items form a critical path chain with zero slack. "
                    f"Any delay cascades through entire chain.",
                    recommendation_hint="Parallelize work, reduce dependency chain length.",
                )
            )
            reasons.append(f"{cp_items} items on critical path")
        elif cp_items > 5:
            cp_risk = (cp_items - 5) * 10.0
            risk_components.append(cp_risk)
            drivers.append(
                RiskDriver(
                    category="DEPENDENCY",
                    score=cp_risk,
                    title="Moderate Critical Path Length",
                    description=f"{cp_items} items on critical path. "
                    f"Limited ability to absorb delays.",
                    recommendation_hint="Monitor critical path items closely.",
                )
            )
            reasons.append(f"{cp_items} items on critical path")

        # 3. Bottleneck analysis (high in-degree items)
        bottlenecks = [
            item_id
            for item_id, in_degree in self.dag.in_degree.items()
            if in_degree >= 5
        ]
        if len(bottlenecks) > 0:
            bottleneck_risk = min(100.0, len(bottlenecks) * 15.0 + 40.0)
            risk_components.append(bottleneck_risk)
            drivers.append(
                RiskDriver(
                    category="DEPENDENCY",
                    score=min(100.0, bottleneck_risk),
                    title="Dependency Bottlenecks",
                    description=f"{len(bottlenecks)} items are bottlenecks "
                    f"(5+ predecessors each). Blocking these items cascades impact.",
                    recommendation_hint="Prioritize bottleneck items, reduce their dependencies.",
                )
            )
            reasons.append(f"{len(bottlenecks)} dependency bottlenecks")

        # 4. Blocker cascade depth
        cascade_depths = list(self.impact_scores.cascade_depth_map.values())
        if cascade_depths:
            max_cascade_depth = max(cascade_depths)
            if max_cascade_depth > 5:
                cascade_risk = min(100.0, (max_cascade_depth - 5) * 15.0 + 60.0)
                risk_components.append(cascade_risk)
                drivers.append(
                    RiskDriver(
                        category="DEPENDENCY",
                        score=min(100.0, cascade_risk),
                        title="Deep Blocker Cascade",
                        description=f"Active blockers impact up to {int(max_cascade_depth)} levels "
                        f"of dependent items through cascade effect.",
                        recommendation_hint="Resolve high-impact blockers immediately.",
                    )
                )

        # 5. Baseline for any active (unresolved) blocker present
        # This is independent of structural dependency signals and ensures
        # a baseline dependency risk when blockers exist in the project state.
        active_blockers = [b for b in self.project_state.blockers if not b.actual_resolution_date]
        if active_blockers:
            # Severity-weighted baseline using configured mapping
            highest_sev = max((b.severity for b in active_blockers), key=lambda s: list(self.SEVERITY_SCORES.keys()).index(s))
            base = self.SEVERITY_SCORES.get(highest_sev, 15.0)
            extra = self.ADDITIONAL_BLOCKER_BONUS * (len(active_blockers) - 1)
            baseline_score = min(100.0, base + extra)
            risk_components.append(baseline_score)
            drivers.append(
                RiskDriver(
                    category="DEPENDENCY",
                    score=baseline_score,
                    title="Active Blocker Present",
                    description=(
                        f"{len(active_blockers)} unresolved blocker(s) present. "
                        f"Highest severity: {highest_sev.value}. Baseline dependency risk applied."
                    ),
                    recommendation_hint="Resolve active blockers to remove baseline dependency exposure.",
                )
            )
            reasons.append(f"{len(active_blockers)} active blocker(s); baseline {baseline_score:.1f}")

        # Average risk components
        if risk_components:
            dependency_score = sum(risk_components) / len(risk_components)
        else:
            dependency_score = 0.0

        return RiskExplanation(
            score=min(100.0, dependency_score),
            reasons=reasons,
            drivers=drivers,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # RESOURCE RISK CALCULATION
    # ──────────────────────────────────────────────────────────────────────────

    def _calculate_resource_risk(self) -> RiskExplanation:
        """
        Calculate resource risk based on:
        - Team utilization percentage
        - Velocity trends (degradation)
        - Resource availability issues
        - Team allocation imbalance
        """
        drivers: List[RiskDriver] = []
        reasons: List[str] = []
        risk_components = []

        # 1. Team utilization
        avg_utilization = self.metrics.avg_allocation_pct * self.metrics.avg_availability_pct
        if avg_utilization > 0.95:
            util_risk = min(100.0, (avg_utilization - 0.95) * 1000.0 + 80.0)
            risk_components.append(util_risk)
            drivers.append(
                RiskDriver(
                    category="RESOURCE",
                    score=min(100.0, util_risk),
                    title="Extreme Team Overload",
                    description=f"Team utilization at {avg_utilization*100:.1f}%. "
                    f"No capacity for handling unexpected work or blockers.",
                    recommendation_hint="Add resources, reduce scope, or extend timeline.",
                )
            )
            reasons.append(f"Team utilization {avg_utilization*100:.1f}%")
        elif avg_utilization > 0.85:
            util_risk = (avg_utilization - 0.85) * 100.0 + 60.0
            risk_components.append(util_risk)
            drivers.append(
                RiskDriver(
                    category="RESOURCE",
                    score=min(100.0, util_risk),
                    title="High Team Overload",
                    description=f"Team utilization at {avg_utilization*100:.1f}%. "
                    f"Limited buffer for unexpected issues.",
                    recommendation_hint="Review sprint capacity planning, consider load balancing.",
                )
            )
            reasons.append(f"Team utilization {avg_utilization*100:.1f}%")

        # 2. Velocity degradation
        if self.metrics.actual_avg_velocity > 0:
            velocity_trend = self._calculate_velocity_trend()
            if velocity_trend < -0.10:  # >10% degradation
                trend_risk = min(100.0, abs(velocity_trend) * 500.0)
                risk_components.append(trend_risk)
                drivers.append(
                    RiskDriver(
                        category="RESOURCE",
                        score=min(100.0, trend_risk),
                        title="Velocity Degradation",
                        description=f"Velocity trend shows {abs(velocity_trend)*100:.1f}% degradation. "
                        f"Team performance declining over time.",
                        recommendation_hint="Investigate cause (burnout, complexity, tooling), "
                        "reduce sprint load or add support.",
                    )
                )
                reasons.append(f"Velocity degrading {abs(velocity_trend)*100:.1f}%")

        # 3. Active blockers impact
        self._blocker_resource_pts = 0.0
        active_blockers = self.metrics.active_blocker_count
        blocker_velocity_impact = float(
            getattr(self.metrics, "estimated_blocker_velocity_impact", 0.0) or 0.0
        )
        VELOCITY_FLOOR_THRESHOLD = 0.75

        if active_blockers > 5:
            if blocker_velocity_impact >= VELOCITY_FLOOR_THRESHOLD:
                drivers.append(
                    RiskDriver(
                        category="RESOURCE",
                        score=0.0,
                        title="Active Blockers (Captured in Schedule Risk)",
                        description=(
                            f"{active_blockers} active blockers noted. "
                            f"Velocity impact ({blocker_velocity_impact:.2f}) is already "
                            f"fully reflected in Schedule risk. No additional Resource score "
                            f"added to avoid double-counting."
                        ),
                        recommendation_hint="Resolve blockers to improve both schedule and resource risk.",
                    )
                )
            else:
                blocker_risk = min(100.0, (active_blockers - 5) * 12.0 + 50.0)
                risk_components.append(blocker_risk)
                self._blocker_resource_pts = min(100.0, blocker_risk)
                drivers.append(
                    RiskDriver(
                        category="RESOURCE",
                        score=min(100.0, blocker_risk),
                        title="High Active Blocker Count (Resource Capacity Risk)",
                        description=(
                            f"{active_blockers} active blockers diverting team capacity. "
                            f"Velocity impact ({blocker_velocity_impact:.2f}) has not saturated "
                            f"the forecast floor, so this represents additional resource risk."
                        ),
                        recommendation_hint="Escalate blocker resolution, add dedicated resources.",
                    )
                )
                reasons.append(f"{active_blockers} active blockers")

        # 4. Resource allocation imbalance
        allocation_variance = self._calculate_allocation_imbalance()
        if allocation_variance > 0.30:
            imbalance_risk = min(100.0, (allocation_variance - 0.30) * 200.0 + 40.0)
            risk_components.append(imbalance_risk)
            drivers.append(
                RiskDriver(
                    category="RESOURCE",
                    score=min(100.0, imbalance_risk),
                    title="Team Allocation Imbalance",
                    description=f"Resource allocation variance {allocation_variance:.2f}. "
                    f"Significant imbalance creates bottlenecks.",
                    recommendation_hint="Rebalance team allocation, redistribute work more evenly.",
                )
            )

        # Average risk components
        if risk_components:
            resource_score = sum(risk_components) / len(risk_components)
        else:
            resource_score = 0.0

        return RiskExplanation(
            score=min(100.0, resource_score),
            reasons=reasons,
            drivers=drivers,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # SCOPE RISK CALCULATION
    # ──────────────────────────────────────────────────────────────────────────

    def _calculate_scope_risk(self) -> RiskExplanation:
        """
        Calculate scope risk based on:
        - Estimate inflation (items with increased estimates)
        - Scope growth (original vs current)
        - Spillover carry-over trends
        - Remaining effort increase
        """
        drivers: List[RiskDriver] = []
        reasons: List[str] = []
        risk_components = []

        # 1. Estimate inflation (items with current > original)
        estimate_increase_count = 0
        total_estimate_inflation = 0.0
        for wi in self.project_state.work_items:
            if wi.current_estimate_hrs > wi.estimated_effort_hrs:
                estimate_increase_count += 1
                total_estimate_inflation += (
                    wi.current_estimate_hrs - wi.estimated_effort_hrs
                )

        if estimate_increase_count > 0:
            inflation_pct = (
                (total_estimate_inflation / self.metrics.total_effort_hours * 100.0)
                if self.metrics.total_effort_hours > 0
                else 0.0
            )
            if inflation_pct > 20.0:
                inflation_risk = min(100.0, (inflation_pct - 20.0) * 2.5 + 60.0)
                risk_components.append(inflation_risk)
                drivers.append(
                    RiskDriver(
                        category="SCOPE",
                        score=min(100.0, inflation_risk),
                        title="Major Estimate Inflation",
                        description=f"{estimate_increase_count} items show estimate inflation. "
                        f"Total inflation {inflation_pct:.1f}% ({total_estimate_inflation:.0f}h). "
                        f"Scope has grown significantly.",
                        recommendation_hint="Audit inflated items, re-negotiate scope with stakeholders.",
                    )
                )
                reasons.append(
                    f"{estimate_increase_count} items with increased estimates "
                    f"({inflation_pct:.1f}% inflation)"
                )
            elif inflation_pct > 10.0:
                inflation_risk = (inflation_pct - 10.0) * 2.0 + 40.0
                risk_components.append(inflation_risk)
                drivers.append(
                    RiskDriver(
                        category="SCOPE",
                        score=min(100.0, inflation_risk),
                        title="Moderate Estimate Inflation",
                        description=f"{estimate_increase_count} items with estimate increases. "
                        f"Total inflation {inflation_pct:.1f}%.",
                        recommendation_hint="Review causes of estimate inflation.",
                    )
                )
                reasons.append(f"Estimate inflation {inflation_pct:.1f}%")

        # 2. Spillover carry-over trend
        historical_carryover = self.spillover.historical_carryover_rate
        if historical_carryover > 3.0:
            carryover_risk = min(100.0, (historical_carryover - 3.0) * 20.0 + 50.0)
            risk_components.append(carryover_risk)
            drivers.append(
                RiskDriver(
                    category="SCOPE",
                    score=min(100.0, carryover_risk),
                    title="High Historical Spillover",
                    description=f"Average {historical_carryover:.1f} items carry over per sprint. "
                    f"Consistent pattern of unfinished work.",
                    recommendation_hint="Improve estimation, reduce sprint scope commitment.",
                )
            )
            reasons.append(
                f"Historical carryover {historical_carryover:.1f} items/sprint"
            )
        elif historical_carryover > 1.5:
            carryover_risk = (historical_carryover - 1.5) * 20.0
            risk_components.append(carryover_risk)
            drivers.append(
                RiskDriver(
                    category="SCOPE",
                    score=min(100.0, carryover_risk),
                    title="Moderate Spillover Pattern",
                    description=f"Average {historical_carryover:.1f} items carry over per sprint.",
                    recommendation_hint="Monitor carryover trend.",
                )
            )
            reasons.append(f"Spillover pattern {historical_carryover:.1f} items/sprint")

        # 3. Blocked items (items blocked due to scope issues or dependencies)
        blocked_items = self.metrics.blocked_items
        if blocked_items > self.metrics.total_items * 0.15:
            blocked_risk = min(
                100.0,
                (blocked_items / self.metrics.total_items - 0.15) * 500.0 + 60.0,
            )
            risk_components.append(blocked_risk)
            drivers.append(
                RiskDriver(
                    category="SCOPE",
                    score=min(100.0, blocked_risk),
                    title="High Blocked Item Rate",
                    description=f"{blocked_items} items ({blocked_items/self.metrics.total_items*100:.1f}%) "
                    f"currently blocked. Scope clarity or dependency resolution needed.",
                    recommendation_hint="Clarify blocked item requirements, resolve dependencies.",
                )
            )

        # 4. Not-started items (potential scope risk)
        not_started = self.metrics.not_started_items
        if not_started > self.metrics.total_items * 0.40:
            not_started_risk = min(
                100.0,
                (not_started / self.metrics.total_items - 0.40) * 300.0 + 50.0,
            )
            risk_components.append(not_started_risk)
            drivers.append(
                RiskDriver(
                    category="SCOPE",
                    score=min(100.0, not_started_risk),
                    title="High Not-Started Item Volume",
                    description=f"{not_started} items ({not_started/self.metrics.total_items*100:.1f}%) "
                    f"not yet started. Large volume of work late in project.",
                    recommendation_hint="Review project cadence, increase sprint capacity.",
                )
            )
            reasons.append(f"{not_started} items not started")

        # Average risk components
        if risk_components:
            scope_score = sum(risk_components) / len(risk_components)
        else:
            scope_score = 0.0

        return RiskExplanation(
            score=min(100.0, scope_score),
            reasons=reasons,
            drivers=drivers,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # SPRINT-LEVEL RISK ANALYSIS
    # ──────────────────────────────────────────────────────────────────────────

    def _calculate_sprint_risks(self) -> List[SprintRisk]:
        """Calculate risk for each sprint."""
        sprint_risks = []

        for sprint in self.project_state.sprints:
            sprint_id = sprint.sprint_number

            # Count blocked and spillover items for this sprint
            sprint_items = [
                wi for wi in self.project_state.work_items
                if wi.assigned_sprint == sprint.sprint_id
            ]
            blocked_count = sum(
                1 for wi in sprint_items if wi.status == WorkItemStatus.BLOCKED
            )

            # Compute severity-weighted blocker exposure for this sprint by
            # cross-referencing active blockers' impacted_item_ids with sprint items.
            active_blockers = [b for b in self.project_state.blockers if not b.actual_resolution_date]
            sprint_item_ids = {wi.item_id for wi in sprint_items}
            severity_base = self.SEVERITY_SCORES
            blocker_exposure = 0.0
            for b in active_blockers:
                if any(item_id in sprint_item_ids for item_id in b.impacted_item_ids):
                    blocker_exposure += severity_base.get(b.severity, 15.0)
            blocker_exposure = min(100.0, blocker_exposure)
            
            # Predicted spillover for this sprint
            predicted_spillover = self.spillover.predicted_spillover_by_sprint.get(
                sprint_id, 0.0
            )

            # Sprint utilization
            sprint_planned_effort = sum(wi.estimated_effort_hrs for wi in sprint_items)
            sprint_capacity = sprint.planned_velocity_hrs if sprint.planned_velocity_hrs > 0 else 100.0
            sprint_utilization = (
                (sprint_planned_effort / sprint_capacity)
                if sprint_capacity > 0
                else 1.0
            )

            # Dependency count in this sprint
            sprint_dep_count = sum(
                1 for dep in self.project_state.dependencies
                if dep.predecessor_item_id in [wi.item_id for wi in sprint_items]
                or dep.successor_item_id in [wi.item_id for wi in sprint_items]
            )

            # Calculate sprint risk score
            sprint_score = self._calculate_single_sprint_risk_score(
                sprint_utilization, blocked_count, predicted_spillover, sprint_dep_count, blocker_exposure
            )

            sprint_risks.append(
                SprintRisk(
                    sprint_id=sprint_id,
                    risk_score=sprint_score,
                    risk_level=self._score_to_level(sprint_score),
                    blocked_items=blocked_count,
                    spillover_items=int(predicted_spillover),
                    overload_pct=min(300.0, sprint_utilization * 100.0),
                    dependency_count=sprint_dep_count,
                )
            )

        return sprint_risks

    def _calculate_single_sprint_risk_score(
        self,
        utilization: float,
        blocked_count: int,
        spillover_count: float,
        dep_count: int,
        blocker_exposure: float = 0.0,
    ) -> float:
        """Calculate risk for a single sprint."""
        components = []

        # Utilization component (high utilization = high risk)
        if utilization > 1.5:
            components.append(min(100.0, (utilization - 1.5) * 50.0 + 80.0))
        elif utilization > 1.0:
            components.append((utilization - 1.0) * 100.0 + 60.0)
        elif utilization > 0.9:
            components.append((utilization - 0.9) * 100.0 + 40.0)

        # Blocked items component
        if blocked_count > 5:
            components.append(min(100.0, (blocked_count - 5) * 10.0 + 50.0))
        elif blocked_count > 0:
            components.append(blocked_count * 10.0)

        # Blocker exposure component: severity-weighted exposure from blockers
        if blocker_exposure > 0.0:
            components.append(min(100.0, blocker_exposure))

        # Spillover component
        if spillover_count > 5:
            components.append(min(100.0, spillover_count * 8.0))
        elif spillover_count > 0:
            components.append(spillover_count * 10.0)

        # Dependency component
        if dep_count > 10:
            components.append(min(100.0, (dep_count - 10) * 5.0 + 50.0))
        elif dep_count > 5:
            components.append((dep_count - 5) * 8.0)

        if components:
            return min(100.0, sum(components) / len(components))
        return 0.0

    # ──────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _score_to_level(score: float) -> RiskLevel:
        """Convert numeric score to RiskLevel."""
        if score <= 20:
            return RiskLevel.LOW
        elif score <= 40:
            return RiskLevel.MODERATE
        elif score <= 60:
            return RiskLevel.HIGH
        elif score <= 80:
            return RiskLevel.VERY_HIGH
        else:
            return RiskLevel.CRITICAL

    def _calculate_velocity_trend(self) -> float:
        """Calculate velocity trend (negative = degrading)."""
        if len(self.project_state.actuals) < 2:
            return 0.0

        # Get last 2 actuals
        sorted_actuals = sorted(
            self.project_state.actuals,
            key=lambda a: a.sprint_number,
        )
        if len(sorted_actuals) < 2:
            return 0.0

        recent = sorted_actuals[-1].actual_effort_hrs
        previous = sorted_actuals[-2].actual_effort_hrs

        if previous <= 0:
            return 0.0

        return (recent - previous) / previous

    def _calculate_allocation_imbalance(self) -> float:
        """Calculate variance in team member allocations."""
        if len(self.project_state.team) <= 1:
            return 0.0

        allocations = [r.allocation_pct for r in self.project_state.team]
        mean = sum(allocations) / len(allocations)
        variance = sum((x - mean) ** 2 for x in allocations) / len(allocations)
        std_dev = variance ** 0.5

        # Normalize by mean to get coefficient of variation
        if mean > 0:
            return std_dev / mean
        return 0.0
