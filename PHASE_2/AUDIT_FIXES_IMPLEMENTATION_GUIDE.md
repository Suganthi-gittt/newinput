# API/Frontend Audit: Implementation Fixes

**Date:** 2026-06-24  
**Based On:** [AUDIT_API_FRONTEND_UNUSED_HARDCODED_FIELDS.md](AUDIT_API_FRONTEND_UNUSED_HARDCODED_FIELDS.md)

---

## Fix #1: Route Baseline Metrics from Upstream to API

### Problem
```python
# GET /api/recommendations returns this (WRONG):
baseline_probability=0.0,
baseline_delay_days=0.0,
baseline_risk_score=0.0,
```

The project-wide baseline is already computed in `RecommendationEngineV2._compute_upstream()` but not passed to the API conversion function.

### Solution

**File:** `app/api/routes/recommendations.py`

**Change 1: Update function signature to accept baseline metrics**

```python
# BEFORE (line 38):
def _recommendation_to_summary(rec) -> RecommendationSummary:

# AFTER:
def _recommendation_to_summary(
    rec,
    baseline_metrics: Optional[Dict[str, float]] = None
) -> RecommendationSummary:
    """
    Convert internal Recommendation to API RecommendationSummary.
    
    Args:
        rec: Recommendation object from engine
        baseline_metrics: Project-wide baseline (on_time_probability, expected_delay_days, overall_risk_score)
                         If None, uses computed estimates from rec.estimated_* fields
    """
    if baseline_metrics is None:
        baseline_metrics = {
            "on_time_probability": 0.0,
            "expected_delay_days": 0.0,
            "overall_risk_score": 0.0,
        }
```

**Change 2: Use real baseline values instead of hardcoding to 0.0**

```python
# BEFORE (lines 53-59):
baseline_probability=0.0,
after_probability=0.0,
expected_probability_gain=0.0,
baseline_delay_days=0.0,
after_delay_days=0.0,
expected_delay_gain_days=rec.estimated_delay_reduction_days,
baseline_risk_score=0.0,

# AFTER:
baseline_probability=round(baseline_metrics.get("on_time_probability", 0.0), 4),
after_probability=round(
    baseline_metrics.get("on_time_probability", 0.0) + rec.estimated_risk_reduction / 100.0,
    4
),  # Rough estimate: prob + small risk improvement delta
expected_probability_gain=round(
    (baseline_metrics.get("on_time_probability", 0.0) + rec.estimated_risk_reduction / 100.0)
    - baseline_metrics.get("on_time_probability", 0.0),
    4
),
baseline_delay_days=round(baseline_metrics.get("expected_delay_days", 0.0), 2),
after_delay_days=round(
    baseline_metrics.get("expected_delay_days", 0.0) - rec.estimated_delay_reduction_days,
    2
),
expected_delay_gain_days=round(rec.estimated_delay_reduction_days, 2),
baseline_risk_score=round(baseline_metrics.get("overall_risk_score", 0.0), 2),
```

**Change 3: Update endpoint to compute and pass baseline metrics**

```python
# BEFORE (line 94):
@router.get("/recommendations")
async def get_recommendations(
    session_id: str = Query(..., description="Session ID"),
    top_n: int = Query(5, description="Number of recommendations to return"),
):
    try:
        session_id = session_id.strip()
        recommendation_engine = _build_engine(session_id)
        candidates = recommendation_engine.generate(top_n=top_n)
        response = RecommendationResponse(
            session_id=session_id,
            project_name=recommendation_engine.project_state.project_info.project_name,
            recommendations=[_recommendation_to_summary(rec) for rec in candidates],  # ← No baseline passed
        )

# AFTER:
@router.get("/recommendations")
async def get_recommendations(
    session_id: str = Query(..., description="Session ID"),
    top_n: int = Query(5, description="Number of recommendations to return"),
):
    try:
        session_id = session_id.strip()
        recommendation_engine = _build_engine(session_id)
        candidates = recommendation_engine.generate(top_n=top_n)
        
        # NEW: Compute upstream baseline once
        upstream = recommendation_engine._compute_upstream()  # Cached computation
        baseline_metrics = {
            "on_time_probability": round(upstream.monte_carlo.on_time_probability, 4),
            "expected_delay_days": round(upstream.forecast.expected_delay_days, 2),
            "overall_risk_score": round(upstream.risk_result.overall_risk_score, 2),
        }
        
        response = RecommendationResponse(
            session_id=session_id,
            project_name=recommendation_engine.project_state.project_info.project_name,
            recommendations=[
                _recommendation_to_summary(rec, baseline_metrics) 
                for rec in candidates
            ],
        )
```

---

## Fix #2: Compute Real impact_level (Not "Medium")

### Problem
```python
# Currently (line 62):
impact_level="Medium",  # Always
```

### Solution

**File:** `app/api/routes/recommendations.py`

**Add helper function:**

```python
def _compute_impact_level(estimated_delay_reduction: float) -> str:
    """
    Classify impact level based on delay reduction magnitude.
    
    Thresholds calibrated from Monte Carlo noise floor (±0.5 days typical).
    """
    if estimated_delay_reduction >= 5.0:      # Significant reduction
        return "High"
    elif estimated_delay_reduction >= 2.0:    # Moderate reduction
        return "Medium"
    else:                                      # Minimal/noise
        return "Low"
```

**Use in _recommendation_to_summary():**

```python
# BEFORE (line 62):
impact_level="Medium",

# AFTER:
impact_level=_compute_impact_level(rec.estimated_delay_reduction_days),
```

---

## Fix #3: Resolve Real category (From Blocker Lookup)

### Problem
```python
# Currently (line 69):
category=None,  # Always None
```

### Solution

**File:** `app/api/routes/recommendations.py`

**Add helper function:**

```python
def _resolve_category(
    project_state: ProjectState,
    affected_blocker_ids: List[str]
) -> Optional[str]:
    """
    Resolve category of first blocker in recommendation.
    
    If multiple blockers, returns the most severe category.
    Categories: "Technical Debt", "Team Capacity", "External Dependency", etc.
    """
    if not affected_blocker_ids:
        return None
    
    # Get first blocker (or could prioritize by severity)
    first_blocker_id = affected_blocker_ids[0]
    for blocker in project_state.blockers:
        if blocker.id == first_blocker_id:
            return blocker.category
    
    return None
```

**Use in _recommendation_to_summary():**

```python
# BEFORE (line 69):
category=None,

# AFTER:
category=_resolve_category(
    recommendation_engine.project_state,  # Pass from caller
    rec.affected_blocker_ids
),
```

**Update function signature to receive project_state:**

```python
# BEFORE:
def _recommendation_to_summary(
    rec,
    baseline_metrics: Optional[Dict[str, float]] = None
) -> RecommendationSummary:

# AFTER:
def _recommendation_to_summary(
    rec,
    baseline_metrics: Optional[Dict[str, float]] = None,
    project_state: Optional[ProjectState] = None,
) -> RecommendationSummary:
```

**Update endpoint call:**

```python
# In get_recommendations():
recommendations=[
    _recommendation_to_summary(
        rec,
        baseline_metrics,
        recommendation_engine.project_state  # ← Add this
    )
    for rec in candidates
],
```

---

## Fix #4: Compute Real implementation_effort (Not "Medium")

### Problem
```python
# Currently (line 51):
implementation_effort="Medium",  # Always
```

### Solution

**File:** `app/api/routes/recommendations.py`

**Add helper function:**

```python
def _estimate_implementation_effort(
    action_type: RecommendationAction,
    affected_item_ids: List[str],
    affected_resource_ids: List[str],
    affected_blocker_ids: List[str],
) -> str:
    """
    Estimate implementation effort based on scope and action type.
    
    High: Multiple items, resource changes, blocker resolution
    Medium: Single item, reassignment
    Low: Item descope, priority change
    """
    scope_count = (
        len(affected_item_ids) +
        len(affected_resource_ids) +
        len(affected_blocker_ids)
    )
    
    # Blocker resolution is high-effort
    if action_type == RecommendationAction.RESOLVE_BLOCKER and len(affected_blocker_ids) > 0:
        return "High"
    
    # Resource changes are medium-effort
    if action_type == RecommendationAction.ADD_RESOURCE_SKILL and len(affected_resource_ids) > 0:
        return "High"
    
    # Multiple items = more effort
    if scope_count > 3:
        return "High"
    elif scope_count > 1:
        return "Medium"
    else:
        return "Low"
```

**Use in _recommendation_to_summary():**

```python
# BEFORE (line 51):
implementation_effort="Medium",

# AFTER:
implementation_effort=_estimate_implementation_effort(
    rec.action_type,
    rec.affected_item_ids,
    rec.affected_resource_ids,
    rec.affected_blocker_ids,
),
```

---

## Fix #5: Expose impact_evidence in API Details

### Problem
```python
# Currently (line 48-53):
details={
    "affected_item_ids": rec.affected_item_ids,
    "affected_resource_ids": rec.affected_resource_ids,
    "affected_sprint_ids": rec.affected_sprint_ids,
    "affected_blocker_ids": rec.affected_blocker_ids,
    "metadata": rec.metadata,
    # ← impact_evidence is MISSING
}
```

### Solution

**File:** `app/api/routes/recommendations.py`

**Update details dict:**

```python
# BEFORE:
details={
    "affected_item_ids": rec.affected_item_ids,
    "affected_resource_ids": rec.affected_resource_ids,
    "affected_sprint_ids": rec.affected_sprint_ids,
    "affected_blocker_ids": rec.affected_blocker_ids,
    "metadata": rec.metadata,
}

# AFTER:
details={
    "affected_item_ids": rec.affected_item_ids,
    "affected_resource_ids": rec.affected_resource_ids,
    "affected_sprint_ids": rec.affected_sprint_ids,
    "affected_blocker_ids": rec.affected_blocker_ids,
    "metadata": rec.metadata,
    "impact_evidence": [
        {
            "signal_id": sig.signal_id,
            "signal_type": sig.signal_type.value,
            "confidence": sig.confidence.value,
            "details": sig.details,
        }
        for sig in rec.impact_evidence
    ] if rec.impact_evidence else [],
}
```

---

## Fix #6: Expose Unused SimulationResult Fields

### Problem
```python
# Currently in simulate_recommendation() [line 130-148]:
# These fields are NEVER returned:
# - simulation_result.seed_used
# - simulation_result.is_positive_impact
# - simulation_result.summary
```

### Solution

**File:** `app/api/models_phase3.py`

**Update RecommendationSimulationResult model to include optional audit fields:**

```python
# BEFORE:
class RecommendationSimulationResult(BaseModel):
    session_id: str
    project_name: str
    recommendation_id: Optional[str]
    baseline_probability: float
    after_probability: float
    probability_gain: float
    baseline_delay_days: float
    after_delay_days: float
    delay_reduction_days: float
    baseline_risk_score: float
    after_risk_score: float
    risk_reduction: float
    delta_spillover_risk: Optional[float] = Field(None)
    delta_projected_velocity: Optional[float] = Field(None)
    scenario_recommendation_ids: Optional[List[str]] = Field(None)

# AFTER:
class RecommendationSimulationResult(BaseModel):
    session_id: str
    project_name: str
    recommendation_id: Optional[str]
    baseline_probability: float
    after_probability: float
    probability_gain: float
    baseline_delay_days: float
    after_delay_days: float
    delay_reduction_days: float
    baseline_risk_score: float
    after_risk_score: float
    risk_reduction: float
    delta_spillover_risk: Optional[float] = Field(None)
    delta_projected_velocity: Optional[float] = Field(None)
    scenario_recommendation_ids: Optional[List[str]] = Field(None)
    
    # NEW FIELDS — Audit trail
    seed_used: int = Field(..., description="Monte Carlo seed for reproducibility")
    is_positive_impact: bool = Field(..., description="True if recommendation improves any metric")
    summary: str = Field(..., description="Human-readable summary of simulation result")
```

**File:** `app/api/routes/recommendations.py`

**Update both simulate endpoints to return these fields:**

```python
# In simulate_recommendation() — BEFORE:
response = RecommendationSimulationResponse(
    session_id=session_id,
    project_name=recommendation_engine.project_state.project_info.project_name,
    simulation_result=RecommendationSimulationResult(
        session_id=session_id,
        project_name=recommendation_engine.project_state.project_info.project_name,
        recommendation_id=simulation_result.recommendation_ids[0] if simulation_result.recommendation_ids else None,
        baseline_probability=simulation_result.baseline_metrics.on_time_probability,
        after_probability=simulation_result.simulated_metrics.on_time_probability,
        probability_gain=simulation_result.delta_on_time_probability,
        baseline_delay_days=simulation_result.baseline_metrics.expected_delay_days,
        after_delay_days=simulation_result.simulated_metrics.expected_delay_days,
        delay_reduction_days=simulation_result.delta_expected_delay_days,
        baseline_risk_score=simulation_result.baseline_metrics.overall_risk_score,
        after_risk_score=simulation_result.simulated_metrics.overall_risk_score,
        risk_reduction=simulation_result.delta_risk_score,
        delta_spillover_risk=simulation_result.delta_spillover_risk,
        delta_projected_velocity=simulation_result.delta_projected_velocity,
        scenario_recommendation_ids=simulation_result.recommendation_ids,
    ),
)

# AFTER:
response = RecommendationSimulationResponse(
    session_id=session_id,
    project_name=recommendation_engine.project_state.project_info.project_name,
    simulation_result=RecommendationSimulationResult(
        session_id=session_id,
        project_name=recommendation_engine.project_state.project_info.project_name,
        recommendation_id=simulation_result.recommendation_ids[0] if simulation_result.recommendation_ids else None,
        baseline_probability=simulation_result.baseline_metrics.on_time_probability,
        after_probability=simulation_result.simulated_metrics.on_time_probability,
        probability_gain=simulation_result.delta_on_time_probability,
        baseline_delay_days=simulation_result.baseline_metrics.expected_delay_days,
        after_delay_days=simulation_result.simulated_metrics.expected_delay_days,
        delay_reduction_days=simulation_result.delta_expected_delay_days,
        baseline_risk_score=simulation_result.baseline_metrics.overall_risk_score,
        after_risk_score=simulation_result.simulated_metrics.overall_risk_score,
        risk_reduction=simulation_result.delta_risk_score,
        delta_spillover_risk=simulation_result.delta_spillover_risk,
        delta_projected_velocity=simulation_result.delta_projected_velocity,
        scenario_recommendation_ids=simulation_result.recommendation_ids,
        # NEW FIELDS:
        seed_used=simulation_result.seed_used,
        is_positive_impact=simulation_result.is_positive_impact,
        summary=simulation_result.summary,
    ),
)
```

**Apply same change to simulate_scenario() endpoint:**

```python
# Same pattern as above, but for scenario_recommendation_ids instead of single recommendation_id
```

---

## Fix #7: Remove Dead Code

### Remove Orphaned RecommendationResult Model

**File:** `app/api/models_phase3.py`

```python
# DELETE lines 379-385:
# class RecommendationResult(BaseModel):
#     session_id: str = Field(..., description="Session ID")
#     project_name: str = Field(..., description="Project name")
#     baseline_probability: float = Field(..., ge=0.0, le=1.0, description="Baseline on-time probability")
#     baseline_delay_days: float = Field(..., description="Baseline expected delay")
#     baseline_risk_score: float = Field(..., description="Baseline overall risk score")
#     recommendations: List[RecommendationSummary] = Field(default_factory=list, description="Ranked recommendations")
```

---

## Summary of Changes

| File | Changes | Priority |
|------|---------|----------|
| `app/api/routes/recommendations.py` | Add 4 helper functions + update 2 endpoints + pass baseline metrics | **CRITICAL** |
| `app/api/models_phase3.py` | Add 3 fields to RecommendationSimulationResult + remove dead model | **CRITICAL** |
| `Frontend/src/pages/Dashboard.jsx` | Update display to show real values (no code changes needed once API fixed) | N/A (automatic) |

---

## Testing Checklist

```bash
# Run existing test suites
pytest -q PHASE_2/backend/tests/test_recommendation*.py

# Check endpoints return non-zero baseline values
curl -s "http://localhost:8000/api/recommendations?session_id=test" | jq '.data.recommendations[0].baseline_probability'
# Should return: 0.25-0.75 (not 0.0)

# Check simulate endpoint returns audit fields
curl -s -X POST "http://localhost:8000/api/recommendations/simulate" \
  -d '{"recommendation_id":"abc123"}' | jq '.data.simulation_result.seed_used'
# Should return: 42

# Check impact_level varies
curl -s "http://localhost:8000/api/recommendations?session_id=test" | jq '.data.recommendations[].impact_level'
# Should return: ["High", "Medium", "Low"] (not all "Medium")

# Check category is resolved
curl -s "http://localhost:8000/api/recommendations?session_id=test" | jq '.data.recommendations[0].category'
# Should return: "Technical Debt" (not null)

# Check impact_evidence is included
curl -s "http://localhost:8000/api/recommendations?session_id=test" | jq '.data.recommendations[0].details.impact_evidence'
# Should return: List of signal objects (not empty)
```

---

## Implementation Order

1. **Fix #1** (baseline metrics routing) — Foundational, enables others
2. **Fix #5** (impact_evidence) — Simple data forwarding
3. **Fix #2, #3, #4** (computed fields) — Add helper functions
4. **Fix #6** (audit fields) — Model update + endpoint changes
5. **Fix #7** (cleanup) — Remove dead code

---

**Status:** Ready for implementation  
**Estimated Effort:** 2-3 hours (all fixes combined)  
**Risk Level:** Low (mostly adding fields, few breaking changes)
