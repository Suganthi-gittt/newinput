# Metrics Engine Metric Catalog

## Architecture Role
The Metrics Engine is the authoritative factual data layer for the project intelligence stack. It consumes parsed workbook state and exposes deterministic metric groups that downstream engines can consume without introducing forecasting, scoring, or recommendation logic.

```text
Workbook
  ↓
Workbook Parser
  ↓
ProjectState
  ↓
Metrics Engine
  ↓
ProjectMetrics
  ↓
Forecast Engine
  ↓
Risk Engine
  ↓
Recommendation Engine
  ↓
Scenario Simulation Engine
```

## Design Principles
- Every metric is derived directly from ProjectState workbook fields.
- Every metric is deterministic, reproducible, and traceable.
- The engine exposes facts and derived analytics only; it does not score risk, recommend actions, or forecast outcomes.
- Historical series are preferred over single-point summaries.

## Metric Group Catalog

### ExecutiveMetrics
- Facts: total items, completed items, blocked items, remaining effort, current sprint number, completed sprint count.
- Derived analytics: completion percentage, overall health score.
- Formula: completion_pct = completed_items / total_items; overall_health_score = weighted combination of completion, blocker pressure, and remaining effort.
- Workbook fields used: work item status, effort remaining, sprint status.
- Dependencies: none beyond the work-item and sprint slices.
- Assumptions: completion is based on the status field from ProjectState.
- Downstream consumers: dashboards, forecast, risk, recommendation.

### WorkMetrics
- Facts: total effort, remaining effort, completed effort, per-sprint/per-module/per-developer effort totals.
- Derived analytics: average item effort.
- Formula: total_effort = sum(estimated_effort_hrs), remaining_effort = sum(remaining_effort_hrs), completed_effort = sum(estimated_effort_hrs for completed items), average_item_effort = total_effort / total_items.
- Workbook fields used: estimated_effort_hrs, remaining_effort_hrs, status, assigned_sprint, assigned_resource.
- Dependencies: work item slice plus sprint/resource assignments.
- Assumptions: each work item contributes exactly once to the aggregates.
- Downstream consumers: forecast, recommendation, simulation.

### SprintMetrics
- Facts: planned items, completed items, planned effort, actual effort, variance, carry-in/out counts and hours, blocker impact hours.
- Derived analytics: completion percentage, execution efficiency score, planning efficiency score.
- Formula: completion_pct = sprint completion rate when present else completed_items / planned_items; execution_efficiency_score = actual_effort / planned_effort; planning_efficiency_score = 1 - abs(variance) / planned_effort.
- Workbook fields used: Sprint.planned_velocity_hrs, SprintActual values, work item assignment to sprint, work item status.
- Dependencies: sprint definitions, sprint actuals, work-item assignments.
- Assumptions: sprint actuals are optional; missing actuals produce zeroed historical values.
- Downstream consumers: forecast, risk, scenario simulation.

### HistoricalMetrics
- Facts: planned effort, actual effort, variance, carry-in/out counts, carryover count, scope-change hours, blocker impact hours.
- Derived analytics: completion rate, velocity trend percentage, time series arrays for velocity, carryover, completion, variance, blocker trend, and planning trend.
- Formula: aggregate sums and averages over actuals, with trend computed from the first and last recorded sprint velocity values.
- Workbook fields used: SprintActual fields.
- Dependencies: historical sprint actuals only.
- Assumptions: historical series are built only from recorded actuals.
- Downstream consumers: forecasting, reporting, trend reviews.

### VelocityMetrics
- Facts: per-sprint velocity series.
- Derived analytics: average velocity, median velocity, variance, standard deviation, stability score, best/worst sprint velocity, trend percentage.
- Formula: average_velocity = mean(velocity_by_sprint), velocity_stability_score = 1 - std_dev / average_velocity.
- Workbook fields used: SprintActual.actual_effort_hrs.
- Dependencies: historical sprint actuals.
- Assumptions: zero/negative values are excluded from velocity calculations.
- Downstream consumers: forecast, scenario simulation.

### ResourceMetrics
- Facts: team size, allocation, availability, assigned effort, completed effort, remaining effort by developer.
- Derived analytics: estimation accuracy, workload balance score, allocation efficiency, knowledge concentration, team-level underutilization count.
- Formula: estimation_accuracy = mean(1 - abs(actual - estimate) / estimate) over completed or actualized work items.
- Workbook fields used: Resource allocation/availability, work item assigned_resource, actual_effort_hrs, remaining_effort_hrs, current_estimate_hrs.
- Dependencies: work items and resource definitions.
- Assumptions: developer metrics remain factual and exclude any recommendation-style labels.
- Downstream consumers: recommendation, risk, staffing analysis.

### DependencyMetrics
- Facts: dependency count, critical-path flags, lag days, predecessor/successor relationships.
- Derived analytics: critical dependency density, cross-team dependency percentage, bottleneck count, critical path length, dependency clusters, blocked dependency chain count, external dependency count.
- Formula: dependency_clusters is computed from connected dependency links in the workbook graph.
- Workbook fields used: Dependency records and work item membership.
- Dependencies: dependency graph + work-item references.
- Assumptions: the engine uses the workbook dependency graph directly; it does not infer missing relationships.
- Downstream consumers: critical path, forecast, recommendation, simulation.

### BlockerMetrics
- Facts: blocker count by severity, active blocker count, severity distribution, recurring categories, resolution days, dependency-related and preventable blocker counts.
- Derived analytics: estimated blocker velocity impact, trend score.
- Formula: trend score is derived from the ratio of active blockers to total blockers; impact uses workbook blocker severity and resolution state.
- Workbook fields used: Blocker severity, status, raised/actual resolution dates, category.
- Dependencies: blocker collection only.
- Assumptions: resolved blockers contribute to historical metrics but not to active-blocker counts.
- Downstream consumers: risk, recommendation.

### PlanningMetrics
- Facts: scope-change flags, carryover counts, actual variance, completion rates.
- Derived analytics: planning accuracy, story sizing consistency, carryover trend, scope volatility, sprint predictability, schedule confidence, delivery stability, commitment reliability, backlog churn.
- Formula: scope_volatility = count(scope_changed items) / total_items; planning_accuracy = 1 - total_variance / total_planned_effort.
- Workbook fields used: SprintActual variance, completion_rate, carryover_count, work item scope-change flags, estimates.
- Dependencies: sprint actuals plus work items.
- Assumptions: planning metrics remain observational and factual.
- Downstream consumers: risk, forecast, scenario simulation.

### QualityMetrics
- Facts: defect counts, reopened work count, scope-change flags.
- Derived analytics: defect density, rework percentage, requirement volatility, scope creep score.
- Formula: defect_density = defects / total_items; rework_percentage = reopened_work_count / total_items.
- Workbook fields used: work item type, status, scope-change flags.
- Dependencies: work items only.
- Assumptions: quality metrics are based on workbook-supported fields only.
- Downstream consumers: risk, recommendation.

### RiskInputMetrics
- Facts: blocker density, dependency density, resource overload score, planning accuracy, velocity stability, carryover rate, scope volatility.
- Formula: these are factual projections from the metric groups above; no independent scoring is performed.
- Workbook fields used: metrics emitted by the engine itself, derived from ProjectState.
- Dependencies: blocker, dependency, resource, planning, velocity, and historical metric groups.
- Assumptions: these are context bundles for downstream engines and not business decisions.
- Downstream consumers: RiskEngine.

### ForecastInputMetrics
- Facts: remaining effort, remaining story count, capacity hours, utilization percentage, blocker impact hours, dependency density.
- Formula: capacity_hours is the sum of each resource's daily capacity times sprint working days for the remaining sprint horizon.
- Workbook fields used: resources, sprint working days, remaining effort, blocker impact, dependency count.
- Dependencies: resource, sprint, blocker, dependency, and work-item slices.
- Assumptions: remaining capacity is based on the workbook schedule and resource capacity fields.
- Downstream consumers: ForecastEngine.

### RecommendationInputMetrics
- Facts: developer allocation percentages, sprint planned velocity, open blocker IDs, critical dependency IDs, carryover/variance signal sprint IDs.
- Formula: these are factual bundles passed to downstream engines for their own decision logic.
- Workbook fields used: team allocation, sprint planned velocity, blocker IDs, dependency IDs, historical actuals.
- Dependencies: actuals, blockers, dependencies, team, sprints.
- Assumptions: no recommendations are generated inside the metrics engine.
- Downstream consumers: RecommendationEngine.
