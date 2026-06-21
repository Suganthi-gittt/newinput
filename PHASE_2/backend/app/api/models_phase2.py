"""API request/response models for Phase 2 endpoints."""

from typing import Dict, List, Tuple
from datetime import datetime
from pydantic import BaseModel


class MetricsResponse(BaseModel):
    """Response for GET /metrics endpoint."""
    session_id: str
    project_name: str
    
    # Completion
    total_items: int
    completed_items: int
    in_progress_items: int
    blocked_items: int
    completion_pct: float
    
    # Effort
    total_effort_hours: float
    remaining_effort_hours: float
    completed_effort_hours: float
    
    # Velocity
    planned_total_velocity: float
    actual_avg_velocity: float
    velocity_variance: float
    
    # Resources
    team_size: int
    avg_allocation_pct: float
    avg_availability_pct: float
    underutilized_count: int
    
    # Risk
    active_blocker_count: int
    blocker_velocity_impact: float
    
    # Schedule
    current_sprint_number: int
    completed_sprints: int
    
    # Dependencies
    dependency_count: int
    expected_spillover_items: int


class DependenciesResponse(BaseModel):
    """Response for GET /dependencies endpoint."""
    session_id: str
    project_name: str
    
    # DAG structure
    total_items: int
    total_dependencies: int
    has_cycles: bool
    
    # Critical path
    critical_path: List[str]
    critical_path_items: List[str]
    critical_path_duration_hours: float
    critical_path_duration_hours_original: float
    critical_path_growth_hours: float
    critical_path_growth_percent: float
    critical_path_duration_days: float
    critical_path_item_count: int
    
    # Risk assessment
    high_risk_items: List[str]
    medium_risk_items: List[str]
    low_risk_items: List[str]
    
    # Blocker impact
    active_blockers: List[str]
    items_blocked: List[str]
    
    # Slack analysis
    zero_slack_items: List[str]  # Items on critical path


class SpilloverResponse(BaseModel):
    """Response for GET /spillover endpoint."""
    session_id: str
    project_name: str
    
    # High-risk items
    high_spillover_risk_items: List[str]
    high_risk_count: int
    
    # Per-sprint predictions
    predicted_spillover_by_sprint: Dict[int, float]
    confidence_intervals: Dict[int, Tuple[float, float]]
    
    # Capacity analysis
    sprint_utilization_pct: Dict[int, float]
    
    # Historical patterns
    historical_carryover_rate: float
    historical_carryover_std_dev: float
    
    # Summary
    total_expected_spillover: float
    risk_level: str  # "Low", "Medium", "High"
