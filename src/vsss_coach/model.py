"""Simulator-independent state and geometry types."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class Vec2:
    x: float = 0.0
    y: float = 0.0

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> "Vec2":
        return Vec2(self.x * scalar, self.y * scalar)

    __rmul__ = __mul__

    def __truediv__(self, scalar: float) -> "Vec2":
        denominator = scalar if abs(scalar) > EPSILON else math.copysign(EPSILON, scalar or 1.0)
        return Vec2(self.x / denominator, self.y / denominator)

    def dot(self, other: "Vec2") -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: "Vec2") -> float:
        return self.x * other.y - self.y * other.x

    def norm_sq(self) -> float:
        return self.dot(self)

    def norm(self) -> float:
        return math.sqrt(self.norm_sq())

    def unit(self) -> "Vec2":
        magnitude = self.norm()
        return self / magnitude if magnitude > EPSILON else Vec2()

    def perpendicular(self, side: float = 1.0) -> "Vec2":
        return Vec2(-side * self.y, side * self.x)

    def limited(self, maximum: float) -> "Vec2":
        magnitude = self.norm()
        return self * (maximum / magnitude) if maximum > 0.0 and magnitude > maximum else self

    def is_finite(self) -> bool:
        return math.isfinite(self.x) and math.isfinite(self.y)


@dataclass(frozen=True, slots=True)
class Segment:
    start: Vec2
    end: Vec2

    def tangent(self) -> Vec2:
        return (self.end - self.start).unit()

    def closest_point(self, point: Vec2) -> Vec2:
        delta = self.end - self.start
        fraction = max(0.0, min(1.0, (point - self.start).dot(delta) / (delta.norm_sq() + EPSILON)))
        return self.start + delta * fraction


@dataclass(frozen=True, slots=True)
class MovingState:
    position: Vec2
    velocity: Vec2 = Vec2()
    acceleration: Vec2 = Vec2()


@dataclass(frozen=True, slots=True)
class BallState(MovingState):
    pass


@dataclass(frozen=True, slots=True)
class RobotState(MovingState):
    robot_id: int = 0
    orientation: float = 0.0
    angular_velocity: float = 0.0


@dataclass(frozen=True, slots=True)
class WorldState:
    ball: BallState
    allies: tuple[RobotState, ...]
    enemies: tuple[RobotState, ...]
    field_length: float
    field_width: float
    goal_width: float
    goal_depth: float
    goals_for: int = 0
    goals_against: int = 0
    time_remaining: float = 0.0
    attack_sign: float = 1.0
    metadata: dict[str, float] = field(default_factory=dict, compare=False)

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    def enemy_goal(self) -> Segment:
        x = self.attack_sign * self.field_length / 2.0
        return Segment(Vec2(x, -self.goal_width / 2.0), Vec2(x, self.goal_width / 2.0))

    def ally_goal(self) -> Segment:
        x = -self.attack_sign * self.field_length / 2.0
        return Segment(Vec2(x, -self.goal_width / 2.0), Vec2(x, self.goal_width / 2.0))

    def walls(self) -> tuple[Segment, ...]:
        half_length = self.field_length / 2.0
        half_width = self.field_width / 2.0
        return (
            Segment(Vec2(-half_length, -half_width), Vec2(half_length, -half_width)),
            Segment(Vec2(-half_length, half_width), Vec2(half_length, half_width)),
            Segment(Vec2(-half_length, -half_width), Vec2(-half_length, half_width)),
            Segment(Vec2(half_length, -half_width), Vec2(half_length, half_width)),
        )
