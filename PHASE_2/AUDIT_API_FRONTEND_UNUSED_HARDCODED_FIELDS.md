# API/Frontend Audit Report: Unused & Hardcoded Fields

**Date:** 2026-06-24  
**Scope:** Recommendation API endpoints and frontend integration  
**Status:** CRITICAL — UI showing fake data, real data available but not wired

---

## Executive Summary

The recommendation endpoints (`GET /api/recommendations`, `POST /api/recommendations/simulate`, `POST /api/recommendations/scenario`) suffer from:

1. **9 computed fields that never reach the UI** — correctly calculated but discarded before API response
2. **4 critical fields hardcoded to fake values** — UI displays "Medium", "None", "0.0" despite real data being available
3. **1 API model never used** — `RecommendationResult` defined but orphaned

**Impact:** 
- UI displays meaningless values for effort, impact level, and baseline risk
- Audit trail fields (`seed_used`, `is_positive_impact`) not available for validation
- Evidence backing each recommendation is not exposed for explainability

---

## Part A: Unused Fields (Computed, Never Reach API/UI)

### A1. SimulationResult Fields

**File:** [app/engines/recommendations/models.py](app/engines/recommendations/models.py#L169-L182)

```python
@dataclass(frozen=True)
class SimulationResult:
    # ✅ These fields are computed in SimulationEngineV2._compute_result()
    seed_used: int                          # UNUSED — always 42
    is_positive_impact: bool                # UNUSED — computed line 291
    summary: str                            # UNUSED — computed line 294
    
    # ⚠️ Per-category metrics computed but only top-level used
    baseline_metrics.schedule_risk: float   # UNUSED
    baseline_metrics.resource_risk: float   # UNUSED
    simulated_metrics.schedule_risk: float  # UNUSED
    simulated_metrics.resource_risk: float  # UNUSED
    
    # ✅ Only these fields are forwarded to API response (simulate endpoint)
    delta_on_time_probability
    delta_expected_delay_days
    delta_risk_score
    delta_projected_velocity
    delta_spillover_risk
```

**Computation:** [simulation_engine_v2.py#L270-L295](app/engines/recommendations/simulation_engine_v2.py#L270-L295)

**Usage in API:** [routes/recommendations.py#L130-L148](app/api/routes/recommendations.py#L130-L148)

```python
# API maps ONLY these fields — all others ignored
baseline_probability=simulation_result.baseline_metrics.on_time_probability,
after_probability=simulation_result.simulated_metrics.on_time_probability,
# ... but NOT:
# seed_used, is_positive_impact, summary
# baseline_metrics.schedule_risk, resource_risk
```

**Problem:** 
- `seed_used=42` is audit-trail data proving determinism but never returned
- `is_positive_impact` could inform UI conditionals (color coding, sort order)
- `schedule_risk` and `resource_risk` per-category breakdowns computed but discarded
- `summary` field provides human-readable justification never exposed

---

### A2. Recommendation.impact_evidence (Never in API Response)

**File:** [app/engines/recommendations/models.py#L104-L122](app/engines/recommendations/models.py#L104-L122)

```python
@dataclass
class Recommendation:
    # ✅ Computed by ImpactEstimator with full signal evidence
    impact_evidence: List[SignalEvidence] = field(default_factory=list)
    
    # This contains raw evidence backing each impact estimate
    # Example: SignalEvidence(signal_id, signal_type, confidence, details, ...)
```

**Conversion to API:** [routes/recommendations.py#L40-L62](app/api/routes/recommendations.py#L40-L62)

```python
def _recommendation_to_summary(rec) -> RecommendationSummary:
    # impact_evidence is NEVER included in details dict!
    details={
        "affected_item_ids": rec.affected_item_ids,
        "affected_resource_ids": rec.affected_resource_ids,
        "affected_sprint_ids": rec.affected_sprint_ids,
        "affected_blocker_ids": rec.affected_blocker_ids,
        "metadata": rec.metadata,  # ← includes metadata but NOT impact_evidence
    }
```

**Problem:** 
- Explainability is lost — UI cannot show "this recommendation gains X days because of signals: S1, S3"
- Frontend only sees `impact_summary` (description copy) not actual evidence chain
- Signal detectors work correctly but signal evidence never propagates to UI

---

### A3. RecommendationResult Model (Defined, Never Used)

**File:** [models_phase3.py#L379-L385](app/api/models_phase3.py#L379-L385)

```python
class RecommendationResult(BaseModel):
    """API response model — DEFINED BUT NEVER USED ANYWHERE"""
    session_id: str
    project_name: str
    baseline_probability: float
    baseline_delay_days: float
    baseline_risk_score: float
    recommendations: List[RecommendationSummary]
```

**Search Results:**
- ❌ Not imported in any route file
- ❌ Not used in any endpoint response
- ❌ Appears only in `__all__` comments in models_phase3.py

**Why it exists:** Appears to be a legacy model from an earlier API design (possibly Phase 2 or incomplete refactoring).

**What's used instead:** 
- `RecommendationResponse` wraps recommendations but fields are built inline
- `RecommendationSummary` for individual recommendations

---

## Part B: Hardcoded/Missing Fields (UI Shows Fake Data)

### B1. implementation_effort (Always "Medium")

**File:** [routes/recommendations.py#L51](app/api/routes/recommendations.py#L51)

```python
def _recommendation_to_summary(rec) -> RecommendationSummary:
    return RecommendationSummary(
        # ❌ HARDCODED — never reflects real data
        implementation_effort="Medium",  # Line 51
```

**Real data available in:**
- `rec.metadata` — can contain effort classification
- Recommendation engine could estimate effort based on:
  - Number of affected items
  - Blocker resolution complexity
  - Resource reallocation scope

**Frontend Impact:**
```jsx
// Dashboard.jsx shows this meaningless value
<div>Effort: <span className="text-white font-semibold">{rec.implementation_effort}</span></div>
// Always displays: "Effort: Medium"
```

---

### B2. impact_level (Always "Medium")

**File:** [routes/recommendations.py#L62](app/api/routes/recommendations.py#L62)

```python
def _recommendation_to_summary(rec) -> RecommendationSummary:
    return RecommendationSummary(
        # ❌ HARDCODED — derived only from estimated_delay_reduction_days
        impact_level="Medium",  # Line 62
```

**Calculation Available:**
```python
# In older recommendation_engine.py (lines 892-901):
def _determine_impact_level(self, candidate: RecommendationCandidate) -> str:
    """Returns High/Medium/Low based on risk_reduction and delay_reduction"""
    # Logic exists but not called in V2 → API pathway
```

**Could be computed from:**
```
if rec.estimated_delay_reduction_days > threshold_high:
    impact_level = "High"
elif rec.estimated_delay_reduction_days > threshold_medium:
    impact_level = "Medium"
else:
    impact_level = "Low"
```

---

### B3. category (Always None)

**File:** [routes/recommendations.py#L69](app/api/routes/recommendations.py#L69)

```python
def _recommendation_to_summary(rec) -> RecommendationSummary:
    return RecommendationSummary(
        # ❌ ALWAYS None — blocker category context lost
        category=None,  # Line 69
```

**Real data available in:**
```python
# From Recommendation model:
rec.affected_blocker_ids: List[str]  # Can look up blocker categories

# In ProjectState.blockers:
for blocker_id in rec.affected_blocker_ids:
    blocker = project_state.get_blocker(blocker_id)
    if blocker:
        return blocker.category  # "Technical Debt", "Team Capacity", "External Dependency", etc.
```

**Frontend Impact:**
- UI cannot group/filter recommendations by blocker category
- Category-aware action suggestions not possible (field exists but always `None`)
- Blocker context lost even though recommendations are often blocker-specific

---

### B4. Baseline Fields Hardcoded to 0.0 (Should Use Project Baseline)

**File:** [routes/recommendations.py#L53-59](app/api/routes/recommendations.py#L53-L59)

```python
def _recommendation_to_summary(rec) -> RecommendationSummary:
    return RecommendationSummary(
        # ❌ ALL HARDCODED — real project baseline already computed
        baseline_probability=0.0,        # Line 53
        after_probability=0.0,          # Line 54
        expected_probability_gain=0.0,  # Line 55
        baseline_delay_days=0.0,        # Line 57
        after_delay_days=0.0,           # Line 58
        expected_delay_gain_days=rec.estimated_delay_reduction_days,  # Only this is real!
        baseline_risk_score=0.0,        # Line 63
        after_risk_score=0.0,           # Line 64
```

**Real Data Available:**
```python
# In RecommendationEngineV2._compute_upstream() [line 106+]:
upstream = EngineRunner().run(self.project_state)

# This produces:
upstream.monte_carlo.on_time_probability        # ← REAL baseline
upstream.forecast.expected_delay_days           # ← REAL baseline
upstream.risk_result.overall_risk_score         # ← REAL baseline
```

**Why This Is Critical:**
- List endpoint (`GET /api/recommendations`) should NOT require simulation
- The `/simulate` endpoint needs per-recommendation deltas
- Baseline is **project-wide** and **already computed once** upstream
- Every recommendation should show how it moves FROM this baseline TO the after-state

**Example Payload Should Be:**

```json
// GET /api/recommendations returns this:
{
  "baseline_probability": 0.34,         // ← from monte_carlo.on_time_probability
  "baseline_delay_days": 18.5,          // ← from forecast.expected_delay_days
  "baseline_risk_score": 62.4,          // ← from risk_result.overall_risk_score
  "recommendations": [
    {
      "recommendation_id": "abc123",
      "title": "Resolve Database Blocker",
      "baseline_probability": 0.34,      // ← same as above (or per-card?)
      "estimated_delay_reduction_days": 8.2,
      // ... needs simulation for exact impact
    }
  ]
}
```

**Simulated Response:**
```json
// POST /api/recommendations/simulate returns this:
{
  "baseline_probability": 0.34,
  "after_probability": 0.52,            // ← computed from simulation
  "probability_gain": 0.18,
  "baseline_delay_days": 18.5,
  "after_delay_days": 10.3,             // ← computed from simulation
  "delay_reduction_days": 8.2,
  // ... this endpoint correctly uses simulation
}
```

---

## Part C: Data Flow Analysis

### Current Flow (Broken)

```
RecommendationEngineV2.generate()
    ↓
Recommendation objects (with real data)
    ├── impact_evidence: List[SignalEvidence]  ← LOST
    ├── estimated_delay_reduction_days: 8.2
    └── metadata: {...}
    ↓
_recommendation_to_summary() [API conversion]
    ├── impact_level = "Medium"  ← HARDCODED
    ├── implementation_effort = "Medium"  ← HARDCODED
    ├── category = None  ← HARDCODED
    ├── baseline_probability = 0.0  ← HARDCODED
    ├── baseline_delay_days = 0.0  ← HARDCODED
    ├── baseline_risk_score = 0.0  ← HARDCODED
    └── [drops all of above from details]
    ↓
RecommendationSummary (passed to API)
    ↓
Frontend UI
    └── Shows fake data ("Medium", "0.0", "None")
```

### Desired Flow (After Fix)

```
RecommendationEngineV2.generate()
    ↓
UpstreamEngineOutputs (computed once)
    ├── monte_carlo.on_time_probability: 0.34
    ├── forecast.expected_delay_days: 18.5
    ├── risk_result.overall_risk_score: 62.4
    └── [cached for all recommendations]
    ↓
Recommendation objects
    ├── impact_evidence (with signals)
    ├── estimated_delay_reduction_days
    └── affected_blocker_ids: [blocker_1, blocker_2]
    ↓
_recommendation_to_summary(rec, baseline_metrics)
    ├── impact_level = compute_from(estimated_delay_reduction_days)
    ├── implementation_effort = estimate_from(metadata)
    ├── category = resolve_from(affected_blocker_ids)
    ├── baseline_probability = baseline_metrics.on_time_probability
    ├── baseline_delay_days = baseline_metrics.expected_delay_days
    ├── baseline_risk_score = baseline_metrics.overall_risk_score
    └── details.impact_evidence = rec.impact_evidence  ← NEW
    ↓
RecommendationSummary (passed to API)
    ↓
Frontend UI
    └── Shows real data
```

---

## Part D: Unused API Models

| Model | File | Status | Why Unused |
|-------|------|--------|-----------|
| `RecommendationResult` | models_phase3.py:379-385 | Orphaned | Never imported; `RecommendationResponse` used instead |
| ~~`RecommendationCandidate`~~ | engine models | Deprecated | Replaced by `Recommendation` in v2 |

---

## Part E: Field-by-Field Remediation Plan

| Field | Current | Should Be | Effort | Data Source |
|-------|---------|-----------|--------|-------------|
| `implementation_effort` | "Medium" (hardcoded) | High/Medium/Low | Low | Estimate from item count + blocker complexity |
| `impact_level` | "Medium" (hardcoded) | High/Medium/Low | Low | Compute from `estimated_delay_reduction_days` vs thresholds |
| `category` | `None` (hardcoded) | Blocker category | Medium | Lookup via `affected_blocker_ids` → `ProjectState.blockers` |
| `baseline_probability` | 0.0 (hardcoded) | Actual baseline | Low | `upstream.monte_carlo.on_time_probability` (project-wide) |
| `baseline_delay_days` | 0.0 (hardcoded) | Actual baseline | Low | `upstream.forecast.expected_delay_days` (project-wide) |
| `baseline_risk_score` | 0.0 (hardcoded) | Actual baseline | Low | `upstream.risk_result.overall_risk_score` (project-wide) |
| `after_probability` | 0.0 (hardcoded) | Estimate only | Medium | Requires simulation (estimate from delay reduction?) |
| `after_delay_days` | 0.0 (hardcoded) | Estimate only | Medium | Compute from baseline - estimated_reduction |
| `after_risk_score` | 0.0 (hardcoded) | Estimate only | Medium | Compute from baseline - estimated_reduction |
| `seed_used` | Computed, unused | Returned in simulate | Low | Already in `SimulationResult` — expose in API |
| `is_positive_impact` | Computed, unused | Returned in simulate | Low | Already in `SimulationResult` — expose in API |
| `summary` | Computed, unused | Returned in simulate | Low | Already in `SimulationResult` — expose in API |
| `impact_evidence` | Computed, dropped | Include in details | Low | Already in `Recommendation.impact_evidence` |
| `schedule_risk`, `resource_risk` | Computed, dropped | Optional field | Medium | Include in baseline/simulated metrics breakdown (optional) |

---

## Part F: Frontend Impact Examples

### Current (Fake Data)

```jsx
// Dashboard.jsx:750
<div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 text-sm text-slate-400">
  <div>Effort: <span className="text-white font-semibold">{rec.implementation_effort}</span></div>
  {/* Displays: "Effort: Medium" ← MEANINGLESS */}
  
  <div>Confidence: <span className="text-white font-semibold">{rec.confidence}</span></div>
  {/* Displays: "High/Medium/Low" ← Real */}
  
  <div>Priority: <span className="text-white font-semibold">{Math.round(rec.priority_score)}</span></div>
  {/* Displays: 75 ← Real */}
  
  <div>Impact: <span className="text-white font-semibold">{rec.impact_confidence || '—'}</span></div>
  {/* Displays: "High" ← Real (from confidence) */}
</div>

// Card header badge (line ~725) would show:
// Impact: Medium ← HARDCODED FAKE
```

### After Fix (Real Data)

```jsx
<div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 text-sm text-slate-400">
  <div>Effort: <span className="text-white font-semibold">{rec.implementation_effort}</span></div>
  {/* Displays: "High" ← Computed from metadata */}
  
  <div>Impact Level: <span className="text-white font-semibold">{rec.impact_level}</span></div>
  {/* Displays: "High" ← Computed from estimated_delay_reduction_days */}
  
  <div>Category: <span className="text-white font-semibold">{rec.category || 'General'}</span></div>
  {/* Displays: "Technical Debt" ← From blocker lookup */}
</div>

// Baseline metrics row (NEW):
<div className="mt-2 text-sm text-slate-300">
  Baseline: {rec.baseline_probability.toFixed(2)} on-time, {rec.baseline_delay_days.toFixed(1)} days late
  {/* Displays: "Baseline: 0.34 on-time, 18.5 days late" ← Real project baseline */}
</div>
```

---

## Summary Table

| Issue | Count | Severity | Fix Priority |
|-------|-------|----------|--------------|
| **Unused computed fields** | 9 | Medium | Low (informational/audit) |
| **Hardcoded to 0.0** | 6 | **HIGH** | **CRITICAL** |
| **Hardcoded to "Medium"** | 2 | **HIGH** | **CRITICAL** |
| **Hardcoded to None** | 1 | Medium | High |
| **Orphaned models** | 1 | Low | Low |

---

## Recommended Actions

### Phase 1: Critical (Week 1)
- [ ] Route baseline metrics (probability, delay, risk) from upstream instead of hardcoding to 0.0
- [ ] Compute `impact_level` from estimated impact values
- [ ] Resolve `category` from blocker lookups

### Phase 2: High (Week 2)
- [ ] Expose `impact_evidence` in API response details
- [ ] Expose `seed_used`, `is_positive_impact`, `summary` in simulate endpoint

### Phase 3: Cleanup (Week 3)
- [ ] Remove unused `RecommendationResult` model
- [ ] Delete per-category `schedule_risk`/`resource_risk` from BaselineMetrics if not needed
- [ ] Add test coverage for all three improvements

---

## Files to Modify

1. [app/api/routes/recommendations.py](app/api/routes/recommendations.py) — API layer (primary)
2. [app/engines/recommendations/recommendation_engine_v2.py](app/engines/recommendations/recommendation_engine_v2.py) — Pass upstream baseline to API conversion
3. [app/api/models_phase3.py](app/api/models_phase3.py) — Update schemas (optional field additions)
4. [Frontend/src/pages/Dashboard.jsx](../../Frontend/src/pages/Dashboard.jsx) — Display real values

---

**Report Complete**  
Next: See [AUDIT_DETAILED_FIXES.md](AUDIT_DETAILED_FIXES.md) for implementation steps
