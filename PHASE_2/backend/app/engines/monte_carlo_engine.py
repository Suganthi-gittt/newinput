"""
Monte Carlo Simulation Engine (Phase 3.2)

Performs probabilistic forecasting using Monte Carlo simulation.
Generates distribution of finish dates based on velocity and work variability.

Key principle: Target End Date is NEVER modified.
The target_end_date is a fixed business commitment used only for probability calculation.
All simulations hold target_end_date constant and generate variable finish_date outcomes.
"""
import random
from datetime import datetime, timedelta
from typing import List, Optional
import statistics

from pydantic import BaseModel

from app.domain.models import ProjectState, SprintStatus
from app.engines.metrics_engine import ProjectMetrics
from app.engines.critical_path_engine import CriticalPathResult
from app.engines.spillover_engine import SpilloverAnalysis
from app.api.models_phase3 import (
    MonteCarloResult,
    MonteCarloStatistics,
    OnTimeRisk,
)



class MonteCarloEngine:
    """Monte Carlo simulation engine for probabilistic forecasting.

    Approach:
    1. For each simulation:
       a) Introduce variability into velocity (normal distribution)
       b) Introduce variability into remaining work (normal distribution)
       c) Apply random blocker impact
       d) Apply random spillover
       e) Calculate expected finish date using modified parameters
    2. Collect all finish dates
    3. Calculate statistics and percentiles
    4. Compute on-time probability (finish_date <= target_end_date)
    5. Assign risk level based on probability

    Important: target_end_date is CONSTANT across all simulations.
    It is a fixed business commitment, never modified by the engine.
    """

    def __init__(
        self,
        project_state: ProjectState,
        metrics: ProjectMetrics,
        cp_result: CriticalPathResult,
        spillover: Optional[SpilloverAnalysis] = None,
        simulation_count: int = 10000,
        velocity_std_dev_pct: float = 0.15,  # 15% std dev around velocity
        remaining_work_std_dev_pct: float = 0.10,  # 10% std dev around remaining work
        seed: int = None,
    ):
        """Initialize Monte Carlo engine.

        Args:
            project_state: Current project state
            metrics: Project metrics (velocity, effort, etc.)
            cp_result: Critical path analysis result
            spillover: Spillover analysis (optional)
            simulation_count: Number of simulations to run (default 10000)
            velocity_std_dev_pct: Standard deviation for velocity variation (0.0-1.0)
            remaining_work_std_dev_pct: Standard deviation for remaining work variation (0.0-1.0)
            seed: Random seed for reproducibility (optional)
        """
        self.project_state = project_state
        self.metrics = metrics
        self.cp_result = cp_result
        self.spillover = spillover
        self.simulation_count = simulation_count
        self.velocity_std_dev_pct = velocity_std_dev_pct
        self.remaining_work_std_dev_pct = remaining_work_std_dev_pct

        if seed is not None:
            random.seed(seed)

    def calculate(self) -> MonteCarloResult:
        """Run Monte Carlo simulation and return results."""

        # Target date is constant (business commitment)
        target_end_date = self.project_state.project_info.target_end_date

        # Collect finish dates from all simulations
        finish_dates: List[datetime] = []

        for _ in range(self.simulation_count):
            finish_date = self._run_simulation()
            finish_dates.append(finish_date)

        # Sort finish dates for percentile calculation
        finish_dates.sort()

        # Calculate statistics
        statistics_obj = self._calculate_statistics(finish_dates, target_end_date)

        # Calculate on-time probability
        on_time_count = sum(1 for fd in finish_dates if fd <= target_end_date)
        on_time_probability = on_time_count / self.simulation_count if self.simulation_count > 0 else 0.0

        # Assign risk level based on probability
        risk_level = self._calculate_risk_level(on_time_probability)

        # Build result (use percentiles from statistics for consistency)
        return MonteCarloResult(
            target_end_date=target_end_date,
            simulation_count=self.simulation_count,
            statistics=statistics_obj,
            on_time_probability=float(round(on_time_probability, 4)),
            on_time_risk_level=risk_level,
            simulations_on_time=on_time_count,
            simulations_late=self.simulation_count - on_time_count,
            most_likely_finish_date=statistics_obj.percentile_50,  # Use from statistics
            best_case_finish_date=statistics_obj.percentile_10,    # Use from statistics
            p80_finish_date=statistics_obj.percentile_80,          # 80% of outcomes ≤ this
            p90_finish_date=statistics_obj.percentile_90,          # 90% of outcomes ≤ this
            p95_finish_date=statistics_obj.percentile_95,          # 95% of outcomes ≤ this
        )

    def _run_simulation(self) -> datetime:
        """Run a single simulation and return the expected finish date."""

        # 1) Base remaining effort from metrics
        base_remaining = float(self.metrics.remaining_effort_hours)

        # 2) Add variation to remaining work (normal distribution)
        std_dev_remaining = base_remaining * self.remaining_work_std_dev_pct
        remaining_work = random.gauss(base_remaining, std_dev_remaining)
        remaining_work = max(0.0, remaining_work)  # Clamp to non-negative

        # 3) Account for critical path sequencing using REMAINING effort
        cp_remaining_hours = float(getattr(self.cp_result, "critical_path_remaining_hours", 0.0) or 0.0)
        adjusted_remaining = max(remaining_work, cp_remaining_hours)

        # 4) Spillover: sample how much of the predicted spillover materializes
        # this trial, then fold it into THIS TRIAL'S velocity as a throughput
        # penalty — not as a separate additive days term.
        #
        # MODELING NOTE (mirrors ForecastEngine's fix — keep both in sync):
        # Spillover does not create a second, parallel block of work added on
        # top of the remaining-effort schedule; it represents capacity mismatch
        # (work re-entering the backlog instead of completing), which reduces
        # how much real progress the team makes per sprint. Previously this engine
        # computed `spillover_delay_days` independently (same units, same scale as
        # `remaining_days`) and added it on top — duplicating the same effort
        # against two convergent schedule terms, randomized fresh on every trial.
        # That widened the simulated spread and shifted the whole distribution
        # later on every run. We now apply the same velocity-erosion model the
        # deterministic ForecastEngine uses, sampled per-trial via spillover_factor
        # so Monte Carlo still captures spillover *variance* (0% to 100% of
        # predicted spillover materializing), just without double counting it.
        avg_item_effort = float(getattr(self.metrics, "average_item_effort", 20.0) or 20.0)
        spillover_hours = 0.0
        spillover_factor = 0.0
        if self.spillover:
            try:
                total_spill = sum(self.spillover.predicted_spillover_by_sprint.values())
                # Sample 0-100% random spillover materialization (sometimes items don't spill)
                spillover_factor = random.uniform(0.0, 1.0)
                spillover_hours = float(total_spill) * avg_item_effort * spillover_factor
            except Exception:
                spillover_hours = 0.0
                spillover_factor = 0.0

        # spillover_fraction: this trial's spillover-driven share of remaining
        # effort, capped at 0.5 so a single trial's velocity can never be driven
        # to near-zero by spillover alone. Mirrors ForecastEngine's cap exactly.
        spillover_fraction = (
            min(0.5, spillover_hours / base_remaining) if base_remaining > 0 else 0.0
        )
        # Must match ForecastEngine.SPILLOVER_VELOCITY_DAMPING — keep these two
        # constants identical or the deterministic and probabilistic forecasts
        # will disagree on how hard spillover bites for the same input data.
        SPILLOVER_VELOCITY_DAMPING = 0.5

        # 5) Base velocity with random variation (normal distribution)
        base_velocity = float(
            self.metrics.actual_avg_velocity or self.metrics.planned_total_velocity or 1.0
        )
        # 6) Sample blocker impact between 0 and max (uniform)
        blocker_impact_max = float(getattr(self.metrics, "estimated_blocker_velocity_impact", 0.0) or 0.0)
        blocker_impact_actual = random.uniform(0.0, blocker_impact_max)

        # Blockers AND spillover both reduce MEAN velocity (consistent with the
        # deterministic forecast). Natural velocity fluctuation is layered on
        # top via the random.gauss draw below.
        mean_velocity = (
            base_velocity
            * (1.0 - blocker_impact_actual)
            * (1.0 - spillover_fraction * SPILLOVER_VELOCITY_DAMPING)
        )
        mean_velocity = max(mean_velocity, base_velocity * 0.25)

        projected_velocity = max(
            random.gauss(
                mean_velocity,
                mean_velocity * self.velocity_std_dev_pct,
            ),
            base_velocity * 0.25,
        )

        # 7) Calculate remaining sprints and days. Spillover's effect is now
        # fully expressed through the eroded projected_velocity above — there
        # is no separate additive spillover_delay_days term added to the
        # schedule. spillover_hours/spillover_factor are retained only as
        # inputs to the erosion calculation, not as a standalone days figure.
        remaining_sprints = adjusted_remaining / projected_velocity if projected_velocity > 0 else float('inf')
        sprint_days = float(self.project_state.project_info.sprint_duration_days or 14)
        remaining_days = remaining_sprints * sprint_days

        # 8) Timeline anchoring (same as Phase 3.1 deterministic forecast)
        project_start = self.project_state.project_info.forecast_anchor_date()
        days_elapsed = self._calculate_schedule_elapsed_days(sprint_days)

        # Expected finish = project_start + elapsed + remaining
        # (spillover already baked into remaining_days via projected_velocity)
        expected_finish = project_start + timedelta(days=days_elapsed + remaining_days)

        return expected_finish

    def _calculate_schedule_elapsed_days(self, sprint_days: float) -> float:
        """Estimate elapsed project time using sprint schedule dates only."""
        completed_sprints = sum(
            1
            for sprint in self.project_state.sprints
            if (
                sprint.status == SprintStatus.COMPLETED
                or (isinstance(sprint.status, str) and sprint.status == SprintStatus.COMPLETED.value)
            )
        )

        days_from_completed = completed_sprints * sprint_days

        current_sprint = next(
            (
                sprint
                for sprint in self.project_state.sprints
                if (
                    sprint.status == SprintStatus.IN_PROGRESS
                    or (isinstance(sprint.status, str) and sprint.status == SprintStatus.IN_PROGRESS.value)
                )
            ),
            None,
        )
        if not current_sprint:
            return days_from_completed

        sprint_window_days = max(
            0.0,
            (current_sprint.end_date - current_sprint.start_date).total_seconds() / (24 * 3600),
        )
        return days_from_completed + min(sprint_window_days, sprint_days)

    def _calculate_statistics(
        self, finish_dates: List[datetime], target_end_date: datetime
    ) -> MonteCarloStatistics:
        """Calculate statistical summary of simulation results."""

        n = len(finish_dates)
        if n == 0:
            raise ValueError("No simulation results to analyze")

        # Calculate percentile indices using consistent method
        # For n items, kth percentile is at index: k * (n - 1)
        # But we'll use simpler approach: int(k * n) for closest element
        p10_idx = int(0.10 * (n - 1))
        p25_idx = int(0.25 * (n - 1))
        p50_idx = int(0.50 * (n - 1))
        p75_idx = int(0.75 * (n - 1))
        p80_idx = int(0.80 * (n - 1))
        p90_idx = int(0.90 * (n - 1))
        p95_idx = int(0.95 * (n - 1))

        p10 = finish_dates[p10_idx]
        p25 = finish_dates[p25_idx]
        p50 = finish_dates[p50_idx]
        p75 = finish_dates[p75_idx]
        p80 = finish_dates[p80_idx]
        p90 = finish_dates[p90_idx]
        p95 = finish_dates[p95_idx]

        # Mean finish date
        timestamps = [fd.timestamp() for fd in finish_dates]
        mean_timestamp = statistics.mean(timestamps)
        mean_finish_date = datetime.fromtimestamp(mean_timestamp)

        # Delay calculations
        mean_delay_days = (mean_finish_date - target_end_date).days
        median_delay_days = (p50 - target_end_date).days

        return MonteCarloStatistics(
            mean_finish_date=mean_finish_date,
            median_finish_date=p50,
            percentile_10=p10,
            percentile_25=p25,
            percentile_50=p50,
            percentile_75=p75,
            percentile_80=p80,
            percentile_90=p90,
            percentile_95=p95,
            mean_delay_days=float(mean_delay_days),
            median_delay_days=float(median_delay_days),
        )

    def _calculate_risk_level(self, on_time_probability: float) -> OnTimeRisk:
        """Determine risk level based on on-time probability.

        >80% = LOW risk (likely to deliver on time)
        60-79% = MEDIUM risk
        40-59% = HIGH risk
        <40% = CRITICAL risk
        """
        if on_time_probability > 0.80:
            return OnTimeRisk.LOW
        elif on_time_probability >= 0.60:
            return OnTimeRisk.MEDIUM
        elif on_time_probability >= 0.40:
            return OnTimeRisk.HIGH
        else:
            return OnTimeRisk.CRITICAL