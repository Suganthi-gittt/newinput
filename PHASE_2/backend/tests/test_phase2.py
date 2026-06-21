"""
Phase 2 Engine Tests

Unit tests for metrics, dependency, critical path, impact scoring, and spillover engines.
"""

import pytest
from datetime import datetime, timedelta

from app.domain.models import (
    ProjectInfo, Resource, Sprint, WorkItem, Dependency, Blocker, SprintActual, ProjectState,
    SkillLevel, WorkItemType, Priority, WorkItemStatus, SprintStatus, BlockerSeverity, BlockerStatus, BlockerCategory, DependencyType
)
from app.engines.metrics_engine import MetricsEngine
from app.engines.dependency_engine import DependencyGraphEngine
from app.engines.critical_path_engine import CriticalPathEngine
from app.engines.impact_scoring_engine import ImpactScoringEngine
from app.engines.spillover_engine import SpilloverAnalysisEngine


@pytest.fixture
def sample_project_state():
    """Create a sample ProjectState for testing."""
    
    # Project info
    start_date = datetime(2025, 1, 1)
    end_date = datetime(2025, 3, 1)
    project_info = ProjectInfo(
        project_name="Test Project",
        sponsor="Test Sponsor",
        business_unit="Engineering",
        project_manager="Test PM",
        customer="Test Customer",
        status="Active",
        start_date=start_date,
        target_end_date=end_date,
        sprint_duration_days=14,
        methodology="Agile Scrum",
    )
    
    # Team
    team = [
        Resource(
            resource_id="R1",
            name="Alice",
            role="Engineer",
            primary_skill="Python",
            secondary_skill="C++",
            skill_level=SkillLevel.SENIOR,
            allocation_pct=1.0,
            availability_pct=1.0,
        ),
        Resource(
            resource_id="R2",
            name="Bob",
            role="Engineer",
            primary_skill="Java",
            secondary_skill="JavaScript",
            skill_level=SkillLevel.MID,
            allocation_pct=0.8,
            availability_pct=0.9,
        ),
    ]
    
    # Sprints
    sprints = [
        Sprint(
            sprint_id="S1",
            sprint_name="Sprint 1",
            sprint_number=1,
            start_date=start_date,
            end_date=start_date + timedelta(days=14),
            working_days=10,
            sprint_goal="Initial setup",
            status=SprintStatus.COMPLETED,
            planned_velocity_hrs=160.0,
            carryover_count=2,
        ),
        Sprint(
            sprint_id="S2",
            sprint_name="Sprint 2",
            sprint_number=2,
            start_date=start_date + timedelta(days=14),
            end_date=start_date + timedelta(days=28),
            working_days=10,
            sprint_goal="Feature development",
            status=SprintStatus.IN_PROGRESS,
            planned_velocity_hrs=160.0,
            carryover_count=1,
        ),
    ]
    
    # Work items
    work_items = [
        WorkItem(
            item_id="WI-001",
            title="Task 1",
            work_type=WorkItemType.TASK,
            assigned_sprint="Sprint 1",
            original_sprint="Sprint 1",
            assigned_resource="R1",
            required_skill=SkillLevel.SENIOR,
            priority=Priority.HIGH,
            estimated_effort_hrs=40.0,
            current_estimate_hrs=40.0,
            actual_effort_hrs=40.0,
            remaining_effort_hrs=0.0,
            progress_pct=1.0,
            status=WorkItemStatus.COMPLETED,
            is_scope_changed=False,
            scope_change_reason=None,
        ),
        WorkItem(
            item_id="WI-002",
            title="Task 2",
            work_type=WorkItemType.TASK,
            assigned_sprint="Sprint 1",
            original_sprint="Sprint 1",
            assigned_resource="R2",
            required_skill=SkillLevel.MID,
            priority=Priority.MEDIUM,
            estimated_effort_hrs=50.0,
            current_estimate_hrs=50.0,
            actual_effort_hrs=30.0,
            remaining_effort_hrs=20.0,
            progress_pct=0.6,
            status=WorkItemStatus.IN_PROGRESS,
            is_scope_changed=False,
            scope_change_reason=None,
        ),
        WorkItem(
            item_id="WI-003",
            title="Task 3",
            work_type=WorkItemType.FEATURE,
            assigned_sprint="Sprint 2",
            original_sprint="Sprint 2",
            assigned_resource="R1",
            required_skill=SkillLevel.SENIOR,
            priority=Priority.HIGH,
            estimated_effort_hrs=60.0,
            current_estimate_hrs=60.0,
            actual_effort_hrs=0.0,
            remaining_effort_hrs=60.0,
            progress_pct=0.0,
            status=WorkItemStatus.NOT_STARTED,
            is_scope_changed=False,
            scope_change_reason=None,
        ),
        WorkItem(
            item_id="WI-004",
            title="Task 4 (Blocked)",
            work_type=WorkItemType.TASK,
            assigned_sprint="Sprint 2",
            original_sprint="Sprint 2",
            assigned_resource=None,
            required_skill=SkillLevel.MID,
            priority=Priority.MEDIUM,
            estimated_effort_hrs=30.0,
            current_estimate_hrs=30.0,
            actual_effort_hrs=0.0,
            remaining_effort_hrs=30.0,
            progress_pct=0.0,
            status=WorkItemStatus.BLOCKED,
            is_scope_changed=False,
            scope_change_reason=None,
        ),
    ]
    
    # Dependencies
    dependencies = [
        Dependency(
            dependency_id="D1",
            predecessor_item_id="WI-001",
            successor_item_id="WI-003",
            dependency_type=DependencyType.FINISH_TO_START,
            is_on_critical_path=True,
            lag_days=0,
            notes="WI-003 depends on WI-001",
        ),
        Dependency(
            dependency_id="D2",
            predecessor_item_id="WI-002",
            successor_item_id="WI-004",
            dependency_type=DependencyType.FINISH_TO_START,
            is_on_critical_path=False,
            lag_days=1,
            notes="WI-004 depends on WI-002",
        ),
    ]
    
    # Blockers
    blockers = [
        Blocker(
            blocker_id="BLK-001",
            related_item_id="WI-004",
            impacted_item_ids=["WI-004"],
            description="Waiting on external review",
            severity=BlockerSeverity.HIGH,
            status=BlockerStatus.OPEN,
            owner="External Team",
            raised_date=start_date,
            target_resolution_date=start_date + timedelta(days=7),
            category=BlockerCategory.EXTERNAL_TEAM_DEPENDENCY,
            notes="Awaiting approval",
        )
    ]
    
    # Sprint actuals
    actuals = [
        SprintActual(
            sprint_id="S1",
            sprint_number=1,
            planned_effort_hrs=160.0,
            actual_effort_hrs=140.0,
            tasks_planned=10,
            tasks_completed=8,
            carryover_count=2,
            notes="Good progress",
        ),
    ]
    
    return ProjectState(
        project_id="TEST-001",
        project_info=project_info,
        team=team,
        sprints=sprints,
        work_items=work_items,
        dependencies=dependencies,
        blockers=blockers,
        actuals=actuals,
    )


class TestMetricsEngine:
    """Tests for MetricsEngine."""
    
    def test_calculate_metrics(self, sample_project_state):
        """Test metrics calculation."""
        engine = MetricsEngine(sample_project_state)
        metrics = engine.calculate()
        
        # Check completion
        assert metrics.total_items == 4
        assert metrics.completed_items == 1
        assert metrics.in_progress_items == 1
        assert metrics.blocked_items == 1
        assert metrics.not_started_items == 1
        assert metrics.completion_pct == 0.25
        
        # Check effort
        assert metrics.total_effort_hours == 180.0
        assert metrics.completed_effort_hours == 40.0
        assert metrics.remaining_effort_hours == 110.0
        
        # Check team
        assert metrics.team_size == 2
        assert metrics.active_blocker_count == 1
    
    def test_velocity_variance(self, sample_project_state):
        """Test velocity variance calculation."""
        engine = MetricsEngine(sample_project_state)
        metrics = engine.calculate()
        
        # With only 1 actual, variance should be 0
        assert metrics.velocity_variance == 0.0


class TestDependencyGraphEngine:
    """Tests for DependencyGraphEngine."""
    
    def test_build_dag(self, sample_project_state):
        """Test DAG construction."""
        engine = DependencyGraphEngine(sample_project_state)
        dag = engine.build_dag()
        
        # Check graph structure
        assert len(dag.all_nodes) == 4
        assert "WI-001" in dag.graph
        assert "WI-003" in dag.graph["WI-001"]
        
        # Check no cycles
        assert not dag.has_cycles
        
        # Check degrees
        assert dag.in_degree["WI-001"] == 0
        assert dag.out_degree["WI-001"] == 1
        assert dag.in_degree["WI-003"] == 1
    
    def test_transitive_closure(self, sample_project_state):
        """Test transitive closure computation."""
        engine = DependencyGraphEngine(sample_project_state)
        dag = engine.build_dag()
        
        # WI-001 should reach WI-003
        assert "WI-003" in dag.transitive_closure["WI-001"]
        
        # WI-003 should reach nothing
        assert len(dag.transitive_closure["WI-003"]) == 0


class TestCriticalPathEngine:
    """Tests for CriticalPathEngine."""
    
    def test_analyze_critical_path(self, sample_project_state):
        """Test critical path analysis."""
        dep_engine = DependencyGraphEngine(sample_project_state)
        dag = dep_engine.build_dag()
        
        cp_engine = CriticalPathEngine(sample_project_state, dag)
        result = cp_engine.analyze()
        
        # Critical path should exist
        assert len(result.critical_path) > 0
        
        # Duration should be reasonable
        assert result.critical_path_duration_hours > 0
        assert result.critical_path_duration_days > 0

    def test_critical_path_growth_metrics(self):
        """Test original and current critical path duration aggregation."""
        start_date = datetime(2025, 1, 1)
        project_info = ProjectInfo(
            project_name="Growth Test",
            sponsor="Test Sponsor",
            business_unit="Engineering",
            project_manager="Test PM",
            customer="Test Customer",
            status="Active",
            start_date=start_date,
            target_end_date=start_date + timedelta(days=30),
            sprint_duration_days=14,
            methodology="Agile Scrum",
        )

        team = [
            Resource(
                resource_id="R1",
                name="Alice",
                role="Engineer",
                primary_skill="Python",
                secondary_skill="None",
                skill_level=SkillLevel.SENIOR,
                allocation_pct=1.0,
                availability_pct=1.0,
            )
        ]

        sprints = [
            Sprint(
                sprint_id="S1",
                sprint_name="Sprint 1",
                sprint_number=1,
                start_date=start_date,
                end_date=start_date + timedelta(days=14),
                working_days=10,
                sprint_goal="Test sprint",
                status=SprintStatus.NOT_STARTED,
                planned_velocity_hrs=80.0,
                carryover_count=0,
            )
        ]

        work_items = [
            WorkItem(
                item_id="WI-001",
                title="First task",
                work_type=WorkItemType.TASK,
                assigned_sprint="S1",
                original_sprint="S1",
                assigned_resource="R1",
                required_skill=SkillLevel.SENIOR,
                priority=Priority.HIGH,
                estimated_effort_hrs=20.0,
                current_estimate_hrs=30.0,
                actual_effort_hrs=0.0,
                remaining_effort_hrs=30.0,
                progress_pct=0.0,
                status=WorkItemStatus.NOT_STARTED,
                is_scope_changed=False,
                scope_change_reason=None,
            ),
            WorkItem(
                item_id="WI-002",
                title="Second task",
                work_type=WorkItemType.TASK,
                assigned_sprint="S1",
                original_sprint="S1",
                assigned_resource="R1",
                required_skill=SkillLevel.SENIOR,
                priority=Priority.HIGH,
                estimated_effort_hrs=10.0,
                current_estimate_hrs=20.0,
                actual_effort_hrs=0.0,
                remaining_effort_hrs=20.0,
                progress_pct=0.0,
                status=WorkItemStatus.NOT_STARTED,
                is_scope_changed=False,
                scope_change_reason=None,
            ),
        ]

        dependencies = [
            Dependency(
                dependency_id="D1",
                predecessor_item_id="WI-001",
                successor_item_id="WI-002",
                dependency_type=DependencyType.FINISH_TO_START,
                is_on_critical_path=True,
                lag_days=0,
                notes="Chain dependency",
            )
        ]

        blockers = []
        actuals = []

        project_state = ProjectState(
            project_id="GROWTH-001",
            project_info=project_info,
            team=team,
            sprints=sprints,
            work_items=work_items,
            dependencies=dependencies,
            blockers=blockers,
            actuals=actuals,
        )

        dag = DependencyGraphEngine(project_state).build_dag()
        cp_result = CriticalPathEngine(project_state, dag).analyze()

        assert cp_result.critical_path == ["WI-001", "WI-002"]
        assert cp_result.critical_path_items == cp_result.critical_path
        assert cp_result.critical_path_duration_hours == 50.0
        assert cp_result.critical_path_duration_hours_original == 30.0
        assert cp_result.critical_path_growth_hours == 20.0
        assert cp_result.critical_path_growth_percent == pytest.approx(66.6666667, rel=1e-3)


class TestImpactScoringEngine:
    """Tests for ImpactScoringEngine."""
    
    def test_score_impacts(self, sample_project_state):
        """Test impact scoring."""
        dep_engine = DependencyGraphEngine(sample_project_state)
        dag = dep_engine.build_dag()
        
        impact_engine = ImpactScoringEngine(sample_project_state, dag)
        risks = impact_engine.score()
        
        # WI-004 should have high risk due to blocker
        assert risks.item_risk_scores["WI-004"] >= 0.5
        
        # Check risk categorization
        assert "WI-004" in risks.high_risk_items or "WI-004" in risks.medium_risk_items
        
        # Check blocker cascade
        assert "BLK-001" in risks.items_impacted_by_blockers


class TestSpilloverAnalysisEngine:
    """Tests for SpilloverAnalysisEngine."""
    
    def test_analyze_spillover(self, sample_project_state):
        """Test spillover analysis."""
        metrics = MetricsEngine(sample_project_state).calculate()
        engine = SpilloverAnalysisEngine(sample_project_state, metrics.average_item_effort)
        analysis = engine.analyze()
        
        # Check basic structure - only in-progress and not-started items are analyzed for spillover
        assert len(analysis.spillover_probability) >= 2  # At least WI-002 and WI-003 should be there
        assert len(analysis.predicted_spillover_by_sprint) >= 1
        
        # All probabilities should be between 0 and 1
        for prob in analysis.spillover_probability.values():
            assert 0.0 <= prob <= 1.0
    
    def test_sprint_utilization(self, sample_project_state):
        """Test sprint utilization calculation."""
        metrics = MetricsEngine(sample_project_state).calculate()
        engine = SpilloverAnalysisEngine(sample_project_state, metrics.average_item_effort)
        analysis = engine.analyze()
        
        # Utilization should be between 0 and 100%
        for sprint_num, util in analysis.sprint_utilization_pct.items():
            assert 0.0 <= util <= 100.0
