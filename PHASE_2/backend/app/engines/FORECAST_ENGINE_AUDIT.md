# Forecast Engine Audit Notes

## Deterministic scope
The forecast engine remains a deterministic delivery prediction engine. It consumes ProjectMetrics, CriticalPathResult, SpilloverAnalysis, and ProjectState, and it does not generate recommendations or perform simulation.

## Core formulas
- Projected velocity = base_velocity * (1 - blocker_impact) * (1 - spillover_fraction * 0.5), floored at 25% of base velocity.
- Remaining days = adjusted_remaining / projected_velocity * sprint_days.
- Delay decomposition = elapsed_days + remaining_days_total - planned_window_days.
- Confidence score is derived from measurable signals: velocity stability, planning accuracy, estimation variance, carryover consistency, blocker volatility, dependency density, and historical stability.

## Structured outputs
The forecast result now exposes:
- ForecastConfidence
- ForecastDriver[]
- ForecastEvidence[]
- ForecastAssumptions
- ForecastExplanation

These objects are intended for UI, AI, and downstream engine reuse without re-deriving the same metrics.
