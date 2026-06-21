"""
Forecast Engine (deterministic)

Produces a single-point forecast based on remaining effort, current velocity,
critical-path sequencing, spillover, and blocker impacts. No Monte Carlo,
no probabilities.
"""
from datetime import datetime, timedelta
from typing import Optional

from app.domain.models import ProjectState, SprintStatus
from app.engines.metrics_engine import ProjectMetrics
from app.engines.critical_path_engine import CriticalPathResult
from app.engines.spillover_engine import SpilloverAnalysis
from app.api.models_phase3 import (
    ForecastResult,
    ForecastDelayBreakdown,
    ForecastScheduleDiagnostics,
    ForecastEffortBreakdown,
)


class ForecastEngine:
    """Deterministic forecast engine.

    High-level approach:
    - Use remaining effort (sum of remaining_effort_hrs) as the work to schedule.
    - Adjust for dependency sequencing by ensuring remaining work is at least
      the critical path duration (hours) — this captures serialisation delays.
    - Add spillover-induced extra work (predicted_spillover_count * avg_item_effort).
    - Project velocity = historical avg velocity per sprint adjusted for active
      blocker impact (velocity reduction factor). No randomness.
    - Compute remaining_sprints = adjusted_remaining_effort / projected_velocity
      and convert to days using project sprint length.
    - Return a single expected finish date (now + days) and derived fields.
    """

    def __init__(
        self,
        project_state: ProjectState,
        metrics: ProjectMetrics,
        cp_result: CriticalPathResult,
        spillover: Optional[SpilloverAnalysis] = None,
    ):
        self.project_state = project_state
        self.metrics = metrics
        self.cp_result = cp_result
        self.spillover = spillover

    def calculate(self) -> ForecastResult:
        """Calculate deterministic forecast and return ForecastResult."""

        # 1) Remaining effort (use metrics which sum remaining_effort_hrs)
        remaining_effort = float(self.metrics.remaining_effort_hours)

        # 2) R2: Account for dependency sequencing using REMAINING critical path effort
        # Use critical_path_remaining_hours (effort still to do on critical path), not full duration
        cp_remaining_hours = float(getattr(self.cp_result, "critical_path_remaining_hours", 0.0) or 0.0)
        adjusted_remaining = max(remaining_effort, cp_remaining_hours)

        # 3) Calculate spillover schedule impact without inflating remaining work.
        avg_item_effort = float(getattr(self.metrics, "average_item_effort", 20.0) or 20.0)
        spillover_hours = 0.0
        predicted_spillover_items = 0.0
        if self.spillover:
            try:
                total_spill = sum(self.spillover.predicted_spillover_by_sprint.values())
                predicted_spillover_items = float(total_spill)
                spillover_hours = float(total_spill) * avg_item_effort
            except Exception:
                predicted_spillover_items = 0.0
                spillover_hours = 0.0

        # 4) Projected velocity (hours per sprint), adjust for blocker impact AND
        # spillover-driven throughput erosion.
        #
        # MODELING NOTE (replaces additive spillover-days term):
        # Spillover does not create a second, parallel block of work that gets
        # added on top of the remaining-effort schedule. It represents capacity
        # mismatch — work re-entering the backlog instead of completing — which
        # reduces how much real progress the team makes per sprint. We therefore
        # fold spillover into projected_velocity as a throughput penalty, the
        # same way blocker_impact already is, rather than as a separate additive
        # days term. This avoids double-counting the same underlying effort against
        # two independently-scaled schedule terms that happen to converge on the
        # same units (hours -> days via the same velocity and sprint length).
        base_velocity = float(self.metrics.actual_avg_velocity or self.metrics.planned_total_velocity or 1.0)
        blocker_impact = float(getattr(self.metrics, "estimated_blocker_velocity_impact", 0.0) or 0.0)

        # spillover_fraction: what share of remaining effort is predicted to be
        # spillover-driven churn, capped at 0.5 so the model never claims the
        # team's effective output collapses to near-zero from spillover alone.
        spillover_fraction = (
            min(0.5, spillover_hours / remaining_effort) if remaining_effort > 0 else 0.0
        )
        # SPILLOVER_VELOCITY_DAMPING: spillover erodes throughput at half the
        # rate implied by its raw fraction — i.e. fully spillover-saturated
        # remaining work (fraction capped at 0.5) costs at most a 25% velocity
        # hit. This is a stated modeling assumption, not a derived constant;
        # recalibrate once delivered-vs-forecast outcome data exists (see
        # RiskEngine weight calibration roadmap item).
        SPILLOVER_VELOCITY_DAMPING = 0.5

        projected_velocity = max(
            base_velocity
            * (1.0 - blocker_impact)
            * (1.0 - spillover_fraction * SPILLOVER_VELOCITY_DAMPING),
            base_velocity * 0.25,
        )

        # 5) Remaining sprints and days.
        # Spillover is now expressed entirely through the eroded projected_velocity
        # above — there is no separate additive spillover-days term. spillover_hours
        # and predicted_spillover_items are retained as diagnostics only (see
        # effort_breakdown / spillover_penalty_hours) and must not be summed into
        # remaining_days_total.
        sprint_days = float(self.project_state.project_info.sprint_duration_days or 14)
        remaining_sprints = adjusted_remaining / projected_velocity if projected_velocity > 0 else float('inf')
        raw_work_days = remaining_sprints * sprint_days
        # spillover_delay_days is kept as a diagnostic estimate of how many of the
        # raw_work_days are attributable to spillover-driven velocity erosion,
        # computed as the difference between the eroded-velocity schedule and what
        # the schedule would have been at blocker-only-adjusted velocity. This is
        # informational and is NOT added into remaining_days_total.
        velocity_without_spillover = max(
            base_velocity * (1.0 - blocker_impact),
            base_velocity * 0.25,
        )
        days_without_spillover = (
            (adjusted_remaining / velocity_without_spillover) * sprint_days
            if velocity_without_spillover > 0
            else 0.0
        )
        spillover_delay_days = max(0.0, raw_work_days - days_without_spillover)
        remaining_days_base_work = raw_work_days
        remaining_days_blocker_loss = max(
            0.0,
            days_without_spillover - (adjusted_remaining / base_velocity * sprint_days if base_velocity > 0 else 0.0),
        )
        remaining_days_total = raw_work_days

        # DIAGNOSTIC: spillover_delay_days can legitimately be 0.0 even when
        # spillover_penalty_hours (and predicted_spillover_items) are large and
        # nonzero. This happens when blocker_impact alone is already severe
        # enough to push velocity_without_spillover down to the same 25% floor
        # that projected_velocity is also clamped to — at that point spillover
        # has no further room to erode velocity, because both terms hit the
        # identical floor. Without this flag, a large spillover_penalty_hours
        # sitting next to a 0.0 spillover_delay_days looks like a bug (two
        # numbers disagreeing) when it is actually blockers fully saturating
        # the velocity floor before spillover is even applied. Surface this
        # explicitly rather than leaving the zero unexplained.
        velocity_floor = base_velocity * 0.25
        velocity_floor_saturated_by_blockers = bool(
            velocity_without_spillover <= velocity_floor + 1e-6 and spillover_hours > 0.0
        )

        # Diagnostic breakdown (keeps original base_velocity-based values for
        # explanation). Updated to match the velocity-erosion spillover model:
        # spillover_days_diag now reports the same diagnostic quantity computed
        # above (days attributable to spillover-driven velocity erosion), rather
        # than an independently-scaled additive term, so this breakdown can no
        # longer silently diverge from remaining_days_total's methodology.
        base_schedule_days = (remaining_effort / base_velocity) * sprint_days if base_velocity > 0 else 0.0
        critical_path_days = 0.0
        if cp_remaining_hours > remaining_effort and base_velocity > 0:
            critical_path_days = ((cp_remaining_hours - remaining_effort) / base_velocity) * sprint_days
        spillover_days_diag = spillover_delay_days
        blocker_days_diag = remaining_days_blocker_loss
        diagnostic_total = base_schedule_days + critical_path_days + spillover_days_diag + blocker_days_diag

        # R1: Timeline Anchoring - calculate progress using workbook schedule dates,
        # not the current wall clock. This keeps forecasts deterministic and tied to
        # the planned project timeline.
        project_start = self.project_state.project_info.forecast_anchor_date()
        days_elapsed = self._calculate_schedule_elapsed_days(sprint_days)
        
        # Expected finish = project_start + elapsed + remaining
        expected_finish = project_start + timedelta(days=days_elapsed + remaining_days_total)

        # R5: Target Date Comparison
        target_end_date = self.project_state.project_info.target_end_date
        # planned window in days between anchor and target
        planned_window_days = float((target_end_date - project_start).days)

        # Use the additive decomposition for expected_delay_days so top-level
        # value matches the delay_breakdown exactly (preserve decimals).
        expected_delay_raw = days_elapsed + remaining_days_total - planned_window_days
        expected_delay_days = float(round(expected_delay_raw, 2))
        on_track = expected_delay_days <= 0

        # 7) Completion percentage (based on total effort and remaining effort)
        total_effort = float(getattr(self.metrics, "total_effort_hours", 0.0) or 0.0)
        if total_effort > 0:
            completion_pct = max(0.0, min(1.0, (total_effort - remaining_effort) / total_effort))
        else:
            completion_pct = 0.0

        # Scope growth explainability
        scope_growth_hours = float(
            sum(
                max(0.0, wi.current_estimate_hrs - wi.estimated_effort_hrs)
                for wi in self.project_state.work_items
            )
        )
        scope_growth_percent = float(round((scope_growth_hours / total_effort * 100.0) if total_effort > 0 else 0.0, 2))
        projected_velocity_per_day = float(projected_velocity / sprint_days if sprint_days > 0 else 0.0)
        scope_impact_days = float(round(scope_growth_hours / projected_velocity_per_day, 2)) if projected_velocity_per_day > 0 else 0.0

        if scope_growth_hours > 0:
            scope_growth_message = (
                f"Project scope has increased by {scope_growth_hours:.1f} hours since baseline, "
                f"contributing approximately {scope_impact_days:.1f} days to the forecast delay."
            )
        else:
            scope_growth_message = "Project scope has not increased since baseline."

        # Explain a zero spillover_delay_days alongside a nonzero
        # spillover_penalty_hours so the two numbers don't look contradictory.
        if velocity_floor_saturated_by_blockers:
            spillover_message = (
                f"Predicted spillover represents {spillover_hours:.1f} hours of "
                f"capacity-mismatch risk, but blocker impact alone has already "
                f"reduced projected velocity to its floor ({velocity_floor:.1f} "
                f"hrs/sprint), leaving no further room for spillover to erode "
                f"velocity. Spillover's contribution to schedule delay is 0.0 days "
                f"in this scenario because blockers are the dominant constraint — "
                f"resolving blockers would re-expose spillover's schedule impact."
            )
        elif spillover_delay_days > 0:
            spillover_message = (
                f"Predicted spillover is reducing effective velocity, contributing "
                f"approximately {spillover_delay_days:.1f} days to the forecast delay."
            )
        else:
            spillover_message = "No material spillover-driven schedule impact predicted."

        return ForecastResult(
            target_end_date=target_end_date,
            expected_finish_date=expected_finish,
            expected_delay_days=float(round(expected_delay_days, 2)),
            remaining_effort_hours=adjusted_remaining,
            completion_percentage=completion_pct,
            projected_velocity=projected_velocity,
            on_track=on_track,
            raw_remaining_effort_hours=remaining_effort,
            critical_path_remaining_hours=cp_remaining_hours,
            predicted_spillover_items=predicted_spillover_items,
            spillover_delay_days=float(round(spillover_delay_days, 2)),
            spillover_penalty_hours=spillover_hours,
            blocker_penalty_hours=max(0.0, base_velocity - projected_velocity) * (adjusted_remaining / projected_velocity if projected_velocity > 0 else 0.0) if projected_velocity > 0 else 0.0,
            forecast_adjusted_effort_hours=adjusted_remaining,
            scope_growth_hours=float(round(scope_growth_hours, 2)),
            scope_growth_percent=scope_growth_percent,
            scope_impact_days=scope_impact_days,
            scope_growth_message=scope_growth_message,
            delay_breakdown={
                "planned_window_days": float(round(planned_window_days, 2)),
                "days_elapsed": float(round(days_elapsed, 2)),
                "remaining_days_total": float(round(remaining_days_total, 2)),
                "remaining_days_base_work": float(round(remaining_days_base_work, 2)),
                "remaining_days_spillover": float(round(spillover_delay_days, 2)),
                "remaining_days_blocker_loss": float(round(remaining_days_blocker_loss, 2)),
                "expected_delay_days": float(round(days_elapsed + remaining_days_total - planned_window_days, 2)),
            },
            schedule_diagnostics={
                "is_additive": False,
                "base_schedule_days": float(round(base_schedule_days, 2)),
                "spillover_days": float(round(spillover_days_diag, 2)),
                "blocker_days": float(round(blocker_days_diag, 2)),
                "critical_path_days": float(round(critical_path_days, 2)),
                "diagnostic_total_days": float(round(diagnostic_total, 2)),
                "velocity_floor_saturated_by_blockers": velocity_floor_saturated_by_blockers,
                "spillover_message": spillover_message,
            },
            effort_breakdown={
                "raw_remaining_effort_hours": float(round(remaining_effort, 2)),
                "critical_path_remaining_hours": float(round(cp_remaining_hours, 2)),
                "spillover_penalty_hours": float(round(spillover_hours, 2)),
                "blocker_penalty_hours": float(round(max(0.0, base_velocity - projected_velocity) * remaining_sprints if projected_velocity > 0 else 0.0, 2)),
                "forecast_adjusted_effort_hours": float(round(adjusted_remaining, 2)),
            },
            forecast_vs_montecarlo_note=(
                "The deterministic forecast applies worst-credible-case assumptions: "
                "full blocker velocity reduction and a capped velocity penalty from "
                "predicted spillover (spillover reduces effective throughput rather "
                "than adding a separate block of schedule time). "
                "Monte Carlo samples the full uncertainty range: spillover impact "
                "between 0-100% of predicted and blocker impact between 0% and the "
                "maximum estimated value. "
                "The on-time probability reflects how often optimistic scenarios occur. "
                "The delay figure reflects the pessimistic single-point estimate. "
                "Both are correct — they answer different questions."
            ),
        )

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