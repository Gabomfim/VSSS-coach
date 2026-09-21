"""Competition rulesets used as reproducible simulator defaults."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CompetitionRules:
    name: str
    version: str
    revision_date: str
    source_url: str
    field_length: float
    field_width: float
    wall_height: float
    wall_thickness: float
    corner_triangle_leg: float
    center_circle_radius: float
    marking_width: float
    goal_width: float
    goal_depth: float
    goal_area_width: float
    goal_area_depth: float
    goal_area_arc_along_goal_line: float
    goal_area_arc_perpendicular: float
    ball_diameter: float
    ball_mass: float
    robots_min: int
    robots_max: int
    robot_max_side: float
    robot_uniform_max_side: float
    periods: int
    period_duration: float
    halftime_duration: float
    extra_time_periods: int
    extra_time_period_duration: float
    golden_goal: bool
    ready_timeout: float
    walkover_timeout: float
    technical_timeouts_per_team: int
    technical_timeout_duration: float
    goalkeeper_possession_timeout: float
    stationary_ball_timeout: float
    attacker_proximity: float

    @property
    def regulation_duration(self) -> float:
        return self.periods * self.period_duration

    @property
    def ball_radius(self) -> float:
        return self.ball_diameter / 2.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ROBOCORE_VSSS_2025 = CompetitionRules(
    name="robocore-vsss-2025",
    version="3.0",
    revision_date="2025-05-27",
    source_url="https://robocore-eventos.s3.sa-east-1.amazonaws.com/public/RoboCore_Futebol_Mini_VSS_Regras.pdf",
    field_length=1.50,
    field_width=1.30,
    wall_height=0.05,
    wall_thickness=0.025,
    corner_triangle_leg=0.07,
    center_circle_radius=0.20,
    marking_width=0.003,
    goal_width=0.40,
    goal_depth=0.10,
    goal_area_width=0.70,
    goal_area_depth=0.15,
    goal_area_arc_along_goal_line=0.20,
    goal_area_arc_perpendicular=0.05,
    ball_diameter=0.0427,
    ball_mass=0.046,
    robots_min=1,
    robots_max=3,
    robot_max_side=0.075,
    robot_uniform_max_side=0.080,
    periods=2,
    period_duration=300.0,
    halftime_duration=300.0,
    extra_time_periods=2,
    extra_time_period_duration=180.0,
    golden_goal=True,
    ready_timeout=300.0,
    walkover_timeout=180.0,
    technical_timeouts_per_team=2,
    technical_timeout_duration=120.0,
    goalkeeper_possession_timeout=5.0,
    stationary_ball_timeout=5.0,
    attacker_proximity=0.08,
)

RULESETS = {ROBOCORE_VSSS_2025.name: ROBOCORE_VSSS_2025}


def get_ruleset(name: str) -> CompetitionRules:
    try:
        return RULESETS[name]
    except KeyError as exc:
        raise ValueError(f"unknown ruleset: {name}") from exc
