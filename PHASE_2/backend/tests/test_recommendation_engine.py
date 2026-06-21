import pytest
from datetime import datetime, timedelta

from app.domain.models import (
    ProjectInfo,
    Resource,
    Sprint,
    WorkItem,
    Dependency,
    Blocker,
    SprintActual,
    ProjectState,
    SkillLevel,
    WorkItemType,
    Priority,
    WorkItemStatus,
    SprintStatus,
    BlockerSeverity,
    BlockerStatus,
    BlockerCategory,
    DependencyType,
)
from app.engines.metrics_engine import MetricsEngine
from app.engines.dependency_engine import DependencyGraphEngine
from app.engines.critical_path_engine import CriticalPathEngine
from app.engines.spillover_engine import SpilloverAnalysisEngine
from app.engines.forecast_engine import ForecastEngine
from app.engines.monte_carlo_engine import MonteCarloEngine
from app.engines.impact_scoring_engine import ImpactScoringEngine
from app.engines.risk_engine import RiskEngine
from app.engines.recommendation_engine import RecommendationEngine
from app.api.models_phase3 import RecommendationType


def make_recommendation_project_state() -> ProjectState:
    start_date = datetime(2025, 1, 1)
    target_date = datetime(2025, 3, 1)

    project_info = ProjectInfo(
        project_name="Recommendation Test",
        sponsor="Test Sponsor",
        business_unit="Engineering",
        project_manager="Test PM",
        customer="Test Customer",
        status="Active",
        start_date=start_date,
        target_end_date=target_date,
        sprint_duration_days=14,
        methodology="Agile Scrum",
    )

    team = [
        Resource(
            resource_id="R1",
            name="Alice",
            role="Backend Engineer",
            primary_skill="Python",
            secondary_skill="SQL",
            skill_level=SkillLevel.SENIOR,
            allocation_pct=0.9,
            availability_pct=0.8,
        ),
        Resource(
            resource_id="R2",
            name="Bob",
            role="Tester",
            primary_skill="Testing",
            secondary_skill="Python",
            skill_level=SkillLevel.INTERMEDIATE,
            allocation_pct=0.9,
            availability_pct=0.6,
        ),
        Resource(
            resource_id="R3",
            name="Celine",
            role="Frontend Engineer",
            primary_skill="React",
            secondary_skill="JavaScript",
            skill_level=SkillLevel.MID,
            allocation_pct=0.8,
            availability_pct=0.8,
        ),
    ]

    sprints = [
        Sprint(
            sprint_id="S1",
            sprint_name="Sprint 1",
            sprint_number=1,
            start_date=start_date,
            end_date=start_date + timedelta(days=14),
            working_days=10,
            sprint_goal="Foundation",
            status=SprintStatus.IN_PROGRESS,
            planned_velocity_hrs=160.0,
            carryover_count=1,
        ),
        Sprint(
            sprint_id="S2",
            sprint_name="Sprint 2",
            sprint_number=2,
            start_date=start_date + timedelta(days=14),
            end_date=start_date + timedelta(days=28),
            working_days=10,
            sprint_goal="Development",
            status=SprintStatus.NOT_STARTED,
            planned_velocity_hrs=160.0,
            carryover_count=0,
        ),
    ]

    work_items = [
        WorkItem(
            item_id="WI-01",
            title="Low priority research spike",
            work_type=WorkItemType.SPIKE,
            assigned_sprint="Sprint 2",
            original_sprint="Sprint 2",
            assigned_resource="R3",
            required_skill="React",
            priority=Priority.LOW,
            estimated_effort_hrs=20.0,
            current_estimate_hrs=20.0,
            actual_effort_hrs=0.0,
            remaining_effort_hrs=20.0,
            progress_pct=0.0,
            status=WorkItemStatus.NOT_STARTED,
        ),
        WorkItem(
            item_id="WI-02",
            title="Blocking API",
            work_type=WorkItemType.TASK,
            assigned_sprint="Sprint 1",
            original_sprint="Sprint 1",
            assigned_resource="R2",
            required_skill="SQL",
            priority=Priority.HIGH,
            estimated_effort_hrs=40.0,
            current_estimate_hrs=40.0,
            actual_effort_hrs=0.0,
            remaining_effort_hrs=40.0,
            progress_pct=0.0,
            status=WorkItemStatus.BLOCKED,
        ),
        WorkItem(
            item_id="WI-03",
            title="Critical backend epic",
            work_type=WorkItemType.FEATURE,
            assigned_sprint="Sprint 1",
            original_sprint="Sprint 1",
            assigned_resource="R1",
            required_skill="Python",
            priority=Priority.CRITICAL,
            estimated_effort_hrs=80.0,
            current_estimate_hrs=80.0,
            actual_effort_hrs=10.0,
            remaining_effort_hrs=70.0,
            progress_pct=0.125,
            status=WorkItemStatus.IN_PROGRESS,
        ),
        WorkItem(
            item_id="WI-04",
            title="Backend integration task",
            work_type=WorkItemType.TASK,
            assigned_sprint="Sprint 2",
            original_sprint="Sprint 2",
            assigned_resource="R1",
            required_skill="Python",
            priority=Priority.MEDIUM,
            estimated_effort_hrs=60.0,
            current_estimate_hrs=60.0,
            actual_effort_hrs=0.0,
            remaining_effort_hrs=60.0,
            progress_pct=0.0,
            status=WorkItemStatus.NOT_STARTED,
        ),
    ]

    dependencies = [
        Dependency(
            dependency_id="DEP-01",
            predecessor_item_id="WI-02",
            successor_item_id="WI-03",
            dependency_type=DependencyType.FINISH_TO_START,
            is_on_critical_path=True,
            lag_days=0,
        ),
        Dependency(
            dependency_id="DEP-02",
            predecessor_item_id="WI-03",
            successor_item_id="WI-04",
            dependency_type=DependencyType.FINISH_TO_START,
            is_on_critical_path=True,
            lag_days=0,
        ),
    ]

    blockers = [
        Blocker(
            blocker_id="BLK-01",
            related_item_id="WI-02",
            impacted_item_ids=["WI-02", "WI-03", "WI-04"],
            description="Environment issue blocking Python API deployment",
            severity=BlockerSeverity.HIGH,
            status=BlockerStatus.OPEN,
            owner="DevOps",
            raised_date=start_date,
            target_resolution_date=start_date + timedelta(days=7),
            category=BlockerCategory.OTHER,
        )
    ]

    actuals = [
        SprintActual(
            sprint_id="S0",
            sprint_number=1,
            planned_effort_hrs=160.0,
            actual_effort_hrs=140.0,
            variance_hrs=20.0,
            tasks_planned=10,
            tasks_completed=8,
            completion_rate=0.8,
            carryover_count=2,
        )
    ]

    return ProjectState(
        project_id="REC-TEST",
        project_info=project_info,
        team=team,
        sprints=sprints,
        work_items=work_items,
        dependencies=dependencies,
        blockers=blockers,
        actuals=actuals,
    )


@pytest.fixture
def recommendation_project_state():
    return make_recommendation_project_state()


@pytest.fixture
def recommendation_engine(recommendation_project_state):
    metrics = MetricsEngine(recommendation_project_state).calculate()
    dag = DependencyGraphEngine(recommendation_project_state).build_dag()
    cp = CriticalPathEngine(recommendation_project_state, dag).analyze()
    spill = SpilloverAnalysisEngine(recommendation_project_state, metrics.average_item_effort).analyze()
    forecast = ForecastEngine(recommendation_project_state, metrics, cp, spill).calculate()
    monte_carlo = MonteCarloEngine(
        project_state=recommendation_project_state,
        metrics=metrics,
        cp_result=cp,
        spillover=spill,
        simulation_count=50,
        seed=42,
    ).calculate()
    impact_scores = ImpactScoringEngine(recommendation_project_state, dag).score()
    risk_result = RiskEngine(
        project_state=recommendation_project_state,
        metrics=metrics,
        cp_result=cp,
        dag=dag,
        spillover=spill,
        forecast=forecast,
        monte_carlo=monte_carlo,
        impact_scores=impact_scores,
    ).analyze()
    return RecommendationEngine(
        project_state=recommendation_project_state,
        metrics=metrics,
        cp_result=cp,
        dag=dag,
        spillover=spill,
        forecast=forecast,
        monte_carlo=monte_carlo,
        risk_result=risk_result,
        simulation_count=50,
    )


def test_blocker_recommendations(recommendation_engine):
    candidates = recommendation_engine.generate_recommendations()
    assert any(c.type == RecommendationType.RESOLVE_BLOCKER for c in candidates)
    blocker_recs = [c for c in candidates if c.type == RecommendationType.RESOLVE_BLOCKER]
    assert blocker_recs
    assert blocker_recs[0].target_ids == ["BLK-01"]
    assert "BLK-01" in blocker_recs[0].action
    assert blocker_recs[0].category == BlockerCategory.OTHER.value
    assert blocker_recs[0].recommended_actions
    assert isinstance(blocker_recs[0].recommended_actions, list)
    assert blocker_recs[0].after_probability >= 0.0


def test_resource_recommendations(recommendation_engine):
    candidates = recommendation_engine.generate_recommendations()
    add_recs = [c for c in candidates if c.type == RecommendationType.ADD_RESOURCE]
    assert add_recs
    rec = add_recs[0]
    assert "Python" in rec.action
    assert rec.details["skill"] == "Python"
    assert rec.after_probability >= 0.0


def test_reassignment_recommendations(recommendation_engine):
    candidates = recommendation_engine.generate_recommendations()
    reassign_recs = [c for c in candidates if c.type == RecommendationType.REASSIGN_WORK]
    assert reassign_recs
    rec = reassign_recs[0]
    assert rec.target_ids == ["WI-02"]
    assert "Reassign WI-02" in rec.action


def test_reduce_item_scope_recommendations(recommendation_engine):
    candidates = recommendation_engine.generate_recommendations()
    reduce_recs = [c for c in candidates if c.type == RecommendationType.REDUCE_ITEM_SCOPE]
    assert reduce_recs
    rec = reduce_recs[0]
    assert rec.target_ids
    assert rec.details["priority"] in {Priority.LOW.value, Priority.MEDIUM.value, Priority.HIGH.value}


def test_cp_optimization(recommendation_engine):
    candidates = recommendation_engine.generate_recommendations()
    cp_recs = [c for c in candidates if c.type == RecommendationType.CRITICAL_PATH_OPTIMIZATION]
    assert cp_recs
    rec = cp_recs[0]
    assert "Optimize critical path items" in rec.action
    assert len(rec.details["critical_items"]) >= 1


def test_simulation_and_ranking(recommendation_engine):
    candidates = recommendation_engine.generate_recommendations()
    assert all(c.priority_score >= 0.0 for c in candidates)
    assert candidates[0].priority_score >= candidates[-1].priority_score
    before_prob = candidates[0].baseline_probability
    after_prob = candidates[0].after_probability
    assert 0.0 <= before_prob <= 1.0
    assert 0.0 <= after_prob <= 1.0
    assert isinstance(candidates[0].expected_probability_gain, float)
    assert all(c.impact_confidence in {"High", "Medium", "Low"} for c in candidates)
    assert all(c.impact_classification in {"Positive Impact", "Negative Impact", "Negligible Impact"} for c in candidates)


def test_simulate_scenario(recommendation_engine):
    candidates = recommendation_engine.generate_recommendations()
    rec_ids = [c.recommendation_id for c in candidates[:2]]
    scenario = recommendation_engine.simulate_scenario(rec_ids)
    assert scenario["scenario"]["probability"] >= 0.0
    assert scenario["scenario"]["delay_days"] == scenario["scenario"]["delay_days"]
    assert scenario["recommendation_ids"] == rec_ids
