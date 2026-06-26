# API/Frontend Audit Summary & Index

**Date:** 2026-06-24  
**Auditor:** GitHub Copilot  
**Scope:** Recommendation API endpoints and frontend integration  
**Time:** ~2 hours audit + 2.5 hours implementation

---

## 📋 Quick Facts

- **9 computed fields** never reach the UI (unused)
- **4 critical fields** hardcoded to fake values (Medium, None, 0.0)
- **1 API model** defined but never used
- **3 helper functions** needed to fix field computation
- **All real data** is already computed upstream — just needs wiring

---

## 📑 Audit Documents

| Document | Purpose | Audience |
|----------|---------|----------|
| **[AUDIT_API_FRONTEND_UNUSED_HARDCODED_FIELDS.md](AUDIT_API_FRONTEND_UNUSED_HARDCODED_FIELDS.md)** | **MAIN AUDIT** — Detailed analysis of all 13 issues with data flow diagrams, file locations, and impact assessment | Engineers, architects |
| **[AUDIT_FIXES_IMPLEMENTATION_GUIDE.md](AUDIT_FIXES_IMPLEMENTATION_GUIDE.md)** | Step-by-step code changes with before/after snippets for all 7 fixes | Developers implementing changes |
| **[AUDIT_QUICK_REFERENCE.md](AUDIT_QUICK_REFERENCE.md)** | One-page summary with tables, before/after API responses, and testing commands | Quick lookup, PR reviewers |

---

## 🔍 The 13 Issues

### CRITICAL (UI Shows Meaningless Values) — 4 Issues

| Issue | Current | Should Be | Why Fixed First |
|-------|---------|-----------|-----------------|
| `baseline_probability` | 0.0 | 0.25-0.75 | Core metric, already computed |
| `baseline_delay_days` | 0.0 | 5-30 | Core metric, already computed |
| `baseline_risk_score` | 0.0 | 10-90 | Core metric, already computed |
| `after_*` (prob/delay/risk) | 0.0 | varies | Needs estimation layer |

**Root Cause:** `_recommendation_to_summary()` never receives baseline metrics from `RecommendationEngineV2._compute_upstream()`

**Severity:** 🔴 **CRITICAL** — UI shows "baseline risk: 0.0" when it should show "18.5 days late"

---

### HIGH (Derived Values Hardcoded) — 4 Issues

| Issue | Current | Should Be | Computation |
|-------|---------|-----------|-------------|
| `implementation_effort` | "Medium" | High/Medium/Low | Estimate from scope size & action type |
| `impact_level` | "Medium" | High/Medium/Low | Compute from estimated_delay_reduction |
| `category` | `None` | Blocker category | Lookup via affected_blocker_ids |
| `impact_evidence` | Missing | SignalEvidence list | Forward from Recommendation model |

**Root Cause:** Lazy hardcoding during development + insufficient API schema mapping

**Severity:** 🟠 **HIGH** — UI cannot differentiate recommendations; evidence chain invisible

---

### MEDIUM (Computed But Unused) — 5 Issues

| Issue | Computed In | Used Where | Impact |
|-------|-------------|-----------|--------|
| `seed_used` (42) | SimulationEngineV2._compute_result() | Nowhere | Audit trail incomplete |
| `is_positive_impact` | SimulationEngineV2._compute_result() | Nowhere | Can't classify impact |
| `summary` | SimulationEngineV2._compute_result() | Nowhere | No human-readable output |
| `schedule_risk` (per-category) | RiskEngine | Dropped at API | Loss of diagnostic data |
| `resource_risk` (per-category) | RiskEngine | Dropped at API | Loss of diagnostic data |

**Root Cause:** Complex simulation engine computes rich data but API layer only forwards subset

**Severity:** 🟡 **MEDIUM** — Explainability/audit trail incomplete

---

### LOW (Dead Code) — 1 Issue

| Issue | File | Status |
|-------|------|--------|
| `RecommendationResult` model | models_phase3.py:379-385 | Defined, never imported, never used |

**Root Cause:** Legacy model from earlier API design phase

**Severity:** 🟢 **LOW** — Technical debt only

---

## 🔧 Implementation Checklist

### Phase 1: Critical Baseline Wiring (Week 1)
```
[ ] Update _recommendation_to_summary() signature to accept baseline_metrics
[ ] Extract baseline from RecommendationEngineV2._compute_upstream()
[ ] Pass baseline to all _recommendation_to_summary() calls
[ ] Test: baseline values now show real numbers (not 0.0)
```

### Phase 2: Derived Field Computation (Week 1)
```
[ ] Add _compute_impact_level() helper
[ ] Add _resolve_category() helper  
[ ] Add _estimate_implementation_effort() helper
[ ] Update _recommendation_to_summary() to use helpers
[ ] Test: effort/impact/category now vary by recommendation
```

### Phase 3: Data Forwarding (Week 2)
```
[ ] Add impact_evidence to API response details
[ ] Update RecommendationSimulationResult model with seed_used, is_positive_impact, summary
[ ] Update both simulate endpoints to return audit fields
[ ] Test: API response includes all fields
```

### Phase 4: Cleanup (Week 2)
```
[ ] Remove RecommendationResult orphaned model
[ ] Update API documentation
[ ] Add integration tests for all new fields
```

---

## 📊 Impact Analysis

### Before Fix
```json
{
  "baseline_probability": 0.0,              ❌ Fake
  "after_probability": 0.0,                 ❌ Fake
  "baseline_delay_days": 0.0,               ❌ Fake
  "after_delay_days": 0.0,                  ❌ Fake
  "baseline_risk_score": 0.0,               ❌ Fake
  "implementation_effort": "Medium",        ❌ Hardcoded
  "impact_level": "Medium",                 ❌ Hardcoded
  "category": null,                         ❌ Hardcoded
  "details": {
    "metadata": {...},
    "impact_evidence": []                   ❌ Dropped
  },
  "seed_used": <missing>,                   ❌ Not exposed
  "is_positive_impact": <missing>,          ❌ Not exposed
  "summary": <missing>                      ❌ Not exposed
}
```

### After Fix
```json
{
  "baseline_probability": 0.34,             ✅ Real (from upstream)
  "after_probability": 0.52,                ✅ Real (estimated/simulated)
  "baseline_delay_days": 18.5,              ✅ Real (from upstream)
  "after_delay_days": 10.3,                 ✅ Real (estimated/simulated)
  "baseline_risk_score": 62.4,              ✅ Real (from upstream)
  "implementation_effort": "High",          ✅ Computed
  "impact_level": "High",                   ✅ Computed
  "category": "Technical Debt",             ✅ Resolved
  "details": {
    "metadata": {...},
    "impact_evidence": [                    ✅ Forwarded
      {"signal_id": "s1", "confidence": "High", ...}
    ]
  },
  "seed_used": 42,                          ✅ Exposed
  "is_positive_impact": true,               ✅ Exposed
  "summary": "Applied 1 recommendation..."  ✅ Exposed
}
```

---

## 🎯 Why This Matters

### User Impact
| Persona | Problem | Consequence |
|---------|---------|-------------|
| **Project Manager** | Baseline shows 0.0 days late (should be 18.5) | Can't understand current situation |
| **Tech Lead** | All recommendations marked "Medium" effort | Can't prioritize based on effort |
| **DevOps** | Category always null | Can't use category-aware workflows |
| **Data Analyst** | Evidence chain invisible | Can't audit recommendation logic |
| **Auditor** | seed_used not exposed | Can't verify Monte Carlo reproducibility |

### Business Impact
- **Trust**: System appears broken when showing "0.0 baseline" + "Medium effort"
- **Decision Quality**: Undifferentiated recommendations harder to prioritize
- **Compliance**: Missing audit trail (seed) for reproducibility verification

---

## 📈 Implementation Effort Estimate

| Phase | Tasks | Estimate | Risk |
|-------|-------|----------|------|
| 1 | Baseline wiring | 45 min | Low |
| 2 | Helper functions | 60 min | Low |
| 3 | Data forwarding | 45 min | Medium |
| 4 | Tests + cleanup | 30 min | Low |
| **Total** | **7 tasks** | **2.5 hrs** | **Low-Medium** |

---

## 🧪 Testing Strategy

### Unit Tests
```python
test_recommendation_to_summary_with_baseline_metrics()
test_compute_impact_level_high_impact()
test_compute_impact_level_low_impact()
test_resolve_category_from_blocker_id()
test_estimate_implementation_effort_high_scope()
```

### Integration Tests
```python
test_get_recommendations_returns_real_baseline_metrics()
test_simulate_recommendation_returns_audit_fields()
test_scenario_simulation_returns_seed_used()
```

### Manual Tests
```bash
curl http://localhost:8000/api/recommendations?session_id=test | jq '.data.recommendations[0].baseline_probability'
# Expected: 0.25-0.75, not 0.0

curl http://localhost:8000/api/recommendations/simulate -d '{"recommendation_id":"xyz"}' | jq '.data.simulation_result.seed_used'
# Expected: 42
```

---

## 🚀 Go/No-Go Decision

### Go Criteria ✅
- All baseline metrics routed from upstream
- All helper functions implemented and tested
- API response includes all new fields
- Frontend can display real values without code changes
- No breaking changes to existing clients

### Current Status
- **Analysis**: ✅ Complete
- **Design**: ✅ Complete  
- **Implementation**: ⏳ Ready to start

---

## 📚 Reference Documents

### For Developers
- [AUDIT_FIXES_IMPLEMENTATION_GUIDE.md](AUDIT_FIXES_IMPLEMENTATION_GUIDE.md) — Code changes with snippets

### For Reviewers
- [AUDIT_QUICK_REFERENCE.md](AUDIT_QUICK_REFERENCE.md) — Before/after API responses
- [AUDIT_API_FRONTEND_UNUSED_HARDCODED_FIELDS.md](AUDIT_API_FRONTEND_UNUSED_HARDCODED_FIELDS.md) — Detailed findings

### For Project Managers
- This document — Executive summary

---

## 📞 Questions?

**Q: Will this break existing frontend code?**  
A: No. Frontend currently expects baseline/after values but displays whatever the API sends. Adding real values (instead of 0.0) just makes the UI show correct information.

**Q: Why wasn't this caught earlier?**  
A: Hardcoded values were acceptable placeholders during development. The audit discovered they were never replaced with real data during integration.

**Q: Can we ship without these fixes?**  
A: Technically yes, but UI shows fake data (0.0 baseline, all "Medium" effort) that undermines user trust and decision quality.

**Q: What's the implementation risk?**  
A: Low — mostly adding new fields and forwarding data that's already computed. No breaking changes to core logic.

---

**Audit Completed:** 2026-06-24  
**Status:** ✅ Analysis Complete → 🚀 Ready for Implementation  
**Next Step:** Begin Phase 1 (Baseline Wiring)
