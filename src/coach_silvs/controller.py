"""Baseline strategy composition and differential-drive controller."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .fields import obstacle_field, segment_field, univector_ball_field
from .formula import evaluate_expression
from .model import RobotState, Vec2, WorldState


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    ball_prediction_horizon: float = 0.18
    collision_horizon: float = 0.45
    behind_ball_distance: float = 0.11
    ball_spiral_radius: float = 0.11
    ball_spiral_smoothing: float = 0.08
    ball_spiral_radius_0: float = 0.11
    ball_spiral_radius_1: float = 0.11
    ball_spiral_radius_2: float = 0.11
    ball_spiral_smoothing_0: float = 0.08
    ball_spiral_smoothing_1: float = 0.08
    ball_spiral_smoothing_2: float = 0.08
    ball_goal_min_speed: float = 0.05
    shot_clearance_radius: float = 0.14
    shot_clearance_gain: float = 1.7
    corner_gate_goal_distance: float = 0.45
    corner_gate_crossing_band: float = 0.12
    corner_gate_player_angle: float = 0.05
    corner_gate_retreat_gain: float = 0.55
    goal_post_escape_radius: float = 0.13
    goal_post_release_radius: float = 0.19
    goal_post_stall_speed: float = 0.05
    goal_post_stall_duration: float = 0.30
    goal_post_stall_displacement: float = 0.015
    goal_post_escape_speed: float = 0.55
    goal_post_tangent_gain: float = 0.65
    attacking_corner_depth: float = 0.24
    attacking_corner_wall_band: float = 0.15
    attacking_corner_escape_speed: float = 0.65
    robot_safe_distance: float = 0.14
    wall_influence_distance: float = 0.10
    wall_cluster_distance: float = 0.14
    wall_cluster_neighbor_distance: float = 0.24
    wall_cluster_tangent_gain: float = 0.80
    wall_cluster_inward_gain: float = 0.40
    return_ball_clearance_radius: float = 0.13
    return_ball_bypass_gain: float = 1.25
    ball_return_gain_0: float = 1.20
    ball_return_gain_1: float = 1.00
    ball_return_gain_2: float = 0.80
    ball_return_power_0: float = 1.0
    ball_return_power_1: float = 2.0
    ball_return_power_2: float = 1.0
    goal_triangle_margin: float = 0.04
    goal_triangle_clearance_gain: float = 1.40
    goal_triangle_backward_gain: float = 0.35
    approach_gain: float = 1.0
    approach_gain_0: float = 1.0
    approach_gain_1: float = 1.0
    approach_gain_2: float = 1.0
    shot_gain: float = 1.2
    orbit_gain: float = 0.8
    obstacle_radial_gain: float = 1.8
    obstacle_circular_gain: float = 0.65
    ally_obstacle_radial_gain: float = 1.8
    ally_obstacle_circular_gain: float = 0.65
    enemy_obstacle_radial_gain: float = 1.8
    enemy_obstacle_circular_gain: float = 0.65
    wall_gain: float = 2.0
    max_nominal_speed: float = 1.7
    max_wheel_speed: float = 68.0
    wheel_radius: float = 0.025
    wheel_separation: float = 0.055
    heading_gain: float = 5.0
    role_near_speed: float = 0.35
    role_far_speed: float = 1.70
    role_near_distance: float = 0.10
    role_far_distance: float = 0.80
    role_intercept_horizon: float = 1.20
    role_intercept_offset: float = 0.06
    role_contact_spin_delay: float = 0.10
    role_intercept_margin: float = 0.04
    role_intercept_gain: float = 1.20
    role_intercept_blend: float = 0.75
    role_intercept_min_speed: float = 0.08
    role_intercept_velocity_gain: float = 0.45
    role_intercept_acceleration_gain: float = 0.08
    role_intercept_time_margin: float = 0.10
    role_intercept_urgency_gain: float = 1.00
    role_defensive_corridor_gain: float = 1.00

    @classmethod
    def from_json(cls, path: str | Path) -> "StrategyConfig":
        with Path(path).open(encoding="utf-8") as stream:
            payload = json.load(stream)
        genome = payload.get("genome", payload)
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: float(value) for key, value in genome.items() if key in allowed})


class VectorFieldStrategy:
    """Deterministic baseline whose scalar blocks can later be evolved."""

    FIELD_STRATEGIES = ("individual", "shared")

    def __init__(
        self, config: StrategyConfig | None = None, field_strategy: str = "shared",
        formula: dict[str, object] | None = None,
    ) -> None:
        if field_strategy not in self.FIELD_STRATEGIES:
            raise ValueError(f"unknown field strategy: {field_strategy}")
        self.config = config or StrategyConfig()
        self.field_strategy = field_strategy
        self.formula = formula or {}
        self._post_stall_since: dict[int, tuple[float, Vec2]] = {}
        self._post_escape_active: set[int] = set()

    def formula_gain(self, object_name: str, world: WorldState, robot_id: int, distance: float) -> float:
        fields = self.formula.get("fields", {})
        if not isinstance(fields, dict):
            return 1.0
        player = "shared" if self.field_strategy == "shared" else f"ally_{robot_id}"
        group = fields.get(player, {})
        block = group.get(object_name, {}) if isinstance(group, dict) else {}
        expression = block.get("expression") if isinstance(block, dict) else None
        duration = max(float(world.metadata.get("match_duration", 600.0)), 1e-6)
        diagonal = max(math.hypot(world.field_length, world.field_width), 1e-6)
        return evaluate_expression(expression, {
            "distance": max(0.0, min(1.0, distance / diagonal)),
            "time_remaining": max(0.0, min(1.0, world.time_remaining / duration)),
            "goal_difference": math.tanh(world.goal_difference / 3.0),
            "attack_sign": world.attack_sign,
        })

    def formula_components(self, object_name: str, world: WorldState, robot_id: int, distance: float) -> Vec2:
        """Evaluate independent GP x/y components; old scalar blocks remain supported."""
        fields = self.formula.get("fields", {})
        player = "shared" if self.field_strategy == "shared" else f"ally_{robot_id}"
        group = fields.get(player, {}) if isinstance(fields, dict) else {}
        block = group.get(object_name, {}) if isinstance(group, dict) else {}
        if not isinstance(block, dict) or not isinstance(block.get("components"), dict):
            if isinstance(block, dict) and block.get("expression") is not None:
                duration = max(float(world.metadata.get("match_duration", 600.0)), 1e-6)
                diagonal = max(math.hypot(world.field_length, world.field_width), 1e-6)
                gain = evaluate_expression(block.get("expression"), {
                    "distance": max(0.0, min(1.0, distance / diagonal)),
                    "time_remaining": max(0.0, min(1.0, world.time_remaining / duration)),
                    "goal_difference": math.tanh(world.goal_difference / 3.0),
                    "attack_sign": world.attack_sign,
                })
            else:
                gain = self.formula_gain(object_name, world, robot_id, distance)
            return Vec2(gain, gain)
        duration = max(float(world.metadata.get("match_duration", 600.0)), 1e-6)
        diagonal = max(math.hypot(world.field_length, world.field_width), 1e-6)
        context = {"distance": max(0.0, min(1.0, distance / diagonal)), "time_remaining": max(0.0, min(1.0, world.time_remaining / duration)), "goal_difference": math.tanh(world.goal_difference / 3.0), "attack_sign": world.attack_sign}
        components = block["components"]
        return Vec2(evaluate_expression(components.get("x", {}).get("expression"), context), evaluate_expression(components.get("y", {}).get("expression"), context))

    def apply_formula(self, object_name: str, contribution: Vec2, world: WorldState, robot_id: int, distance: float) -> Vec2:
        factors = self.formula_components(object_name, world, robot_id, distance)
        return Vec2(contribution.x * factors.x, contribution.y * factors.y)

    def goal_escape_velocity(self, world: WorldState, robot: RobotState) -> Vec2 | None:
        """Override every tactical field while a robot is physically inside either goal."""
        inside_mouth = abs(robot.position.y) < world.goal_width / 2.0
        beyond_goal_line = abs(robot.position.x) > world.field_length / 2.0
        if not (inside_mouth and beyond_goal_line):
            return None
        # Aim through the centre of the mouth instead of leaving horizontally.
        # The diagonal component prevents the goal's side/back-wall corners
        # from becoming physical local minima.
        inward_sign = -math.copysign(1.0, robot.position.x)
        target = Vec2(
            math.copysign(world.field_length / 2.0 - self.config.goal_post_release_radius, robot.position.x),
            0.0,
        )
        direction = (target - robot.position).unit()
        if direction.norm_sq() < 1e-12:
            direction = Vec2(inward_sign, 0.0)
        return direction * self.config.max_nominal_speed

    def goal_post_escape_velocity(self, world: WorldState, robot: RobotState) -> Vec2 | None:
        """Recover a stalled robot near a goal post with a stateful safety override."""
        half_length = world.field_length / 2.0
        half_goal = world.goal_width / 2.0
        posts = (
            Vec2(-half_length, -half_goal), Vec2(-half_length, half_goal),
            Vec2(half_length, -half_goal), Vec2(half_length, half_goal),
        )
        post = min(posts, key=lambda candidate: (robot.position - candidate).norm())
        offset = robot.position - post
        distance = offset.norm()
        robot_id = robot.robot_id
        now = float(world.metadata.get("timestamp", 0.0))

        if robot_id in self._post_escape_active:
            if distance >= self.config.goal_post_release_radius:
                self._post_escape_active.discard(robot_id)
                self._post_stall_since.pop(robot_id, None)
                return None
        elif distance <= self.config.goal_post_escape_radius:
            stalled_since, stalled_position = self._post_stall_since.setdefault(robot_id, (now, robot.position))
            elapsed = now - stalled_since
            displacement = (robot.position - stalled_position).norm()
            slow_progress = displacement <= self.config.goal_post_stall_displacement
            low_speed = robot.velocity.norm() <= self.config.goal_post_stall_speed
            if elapsed >= self.config.goal_post_stall_duration and (low_speed or slow_progress):
                self._post_escape_active.add(robot_id)
        else:
            self._post_stall_since.pop(robot_id, None)

        if robot_id not in self._post_escape_active:
            return None

        # Aim at a point both inside the playable field and toward the centre
        # of the goal mouth. This prevents either tangent from choosing the
        # narrow exterior corner behind a post.
        inward = Vec2(-math.copysign(1.0, post.x), 0.0)
        mouth_centre = Vec2(0.0, -math.copysign(1.0, post.y))
        escape_target = (
            post
            + inward * self.config.goal_post_release_radius
            + mouth_centre * (self.config.goal_post_release_radius * self.config.goal_post_tangent_gain)
        )
        direction = (escape_target - robot.position).unit()
        proximity = max(0.0, min(1.0, distance / max(self.config.goal_post_release_radius, 1e-6)))
        speed = self.config.goal_post_escape_speed * (0.45 + 0.55 * proximity)
        return direction * speed

    def attacking_corner_escape_velocity(self, world: WorldState, robot: RobotState) -> Vec2 | None:
        """Move robots out of side-wall pockets beside the opponent goal."""
        enemy_x = world.attack_sign * world.field_length / 2.0
        distance_behind_goal = (enemy_x - robot.position.x) * world.attack_sign
        side_wall_distance = world.field_width / 2.0 - abs(robot.position.y)
        outside_mouth = abs(robot.position.y) > world.goal_width / 2.0
        if not (
            -world.goal_depth <= distance_behind_goal <= self.config.attacking_corner_depth
            and side_wall_distance <= self.config.attacking_corner_wall_band
            and outside_mouth
        ):
            return None
        # Retreat slightly from the goal line while moving decisively toward
        # the centre. This opens a shooting lane instead of pushing the ball
        # along the side wall or back out of the attacking corner.
        backward = Vec2(-world.attack_sign, 0.0)
        toward_centre = Vec2(0.0, -math.copysign(1.0, robot.position.y))
        return (backward * 0.45 + toward_centre).unit() * self.config.attacking_corner_escape_speed

    def wall_decluster_field(self, world: WorldState, robot: RobotState) -> Vec2:
        """Separate allies sharing a wall band while guiding them into the field."""
        walls = world.walls()
        wall = min(walls, key=lambda candidate: (robot.position - candidate.closest_point(robot.position)).norm())
        closest = wall.closest_point(robot.position)
        wall_distance = (robot.position - closest).norm()
        activation = max(self.config.wall_cluster_distance, 1e-6)
        if wall_distance >= activation:
            return Vec2()

        tangent = wall.tangent()
        inward = (Vec2() - closest).unit()
        if inward.norm_sq() < 1e-12:
            inward = (Vec2() - robot.position).unit()
        result = Vec2()
        neighbor_limit = max(self.config.wall_cluster_neighbor_distance, 1e-6)
        wall_weight = 1.0 - wall_distance / activation
        for ally in world.allies:
            if ally.robot_id == robot.robot_id:
                continue
            ally_wall_distance = (ally.position - wall.closest_point(ally.position)).norm()
            if ally_wall_distance >= activation:
                continue
            along = (robot.position - ally.position).dot(tangent)
            if abs(along) >= neighbor_limit:
                continue
            side = math.copysign(1.0, along) if abs(along) > 1e-9 else (1.0 if robot.robot_id < ally.robot_id else -1.0)
            neighbor_weight = 1.0 - abs(along) / neighbor_limit
            strength = wall_weight * neighbor_weight
            result += tangent * (side * self.config.wall_cluster_tangent_gain * strength)
            result += inward * (self.config.wall_cluster_inward_gain * strength)
        return result

    def player_ball_field(self, world: WorldState, robot: RobotState, base_field: Vec2) -> Vec2:
        """Use one ball geometry with an evolved return law per robot."""
        attack = Vec2(world.attack_sign, 0.0)
        lateral = Vec2(0.0, 1.0)
        relative = robot.position - world.ball.position
        forward = relative.dot(attack)
        goal_x = world.attack_sign * world.field_length / 2.0
        goal_distance = (goal_x - world.ball.position.x) * world.attack_sign
        if goal_distance <= 1e-6 or forward <= 0.0 or forward >= goal_distance:
            return base_field
        progress = max(0.0, min(1.0, forward / goal_distance))
        index = max(0, min(2, robot.robot_id))
        gain = max(0.0, float(getattr(self.config, f"ball_return_gain_{index}")))
        raw_power = float(getattr(self.config, f"ball_return_power_{index}"))
        power = 1.0 if raw_power < 1.5 else 2.0
        strength = gain * progress ** power
        target = world.ball.position - attack * self.config.behind_ball_distance
        lateral_distance = relative.dot(lateral)
        clearance = max(self.config.return_ball_clearance_radius, 1e-6)
        if abs(lateral_distance) < clearance:
            side = math.copysign(1.0, lateral_distance) if abs(lateral_distance) > 1e-9 else (1.0 if robot.robot_id % 2 == 0 else -1.0)
            bypass = lateral * (side * self.config.return_ball_bypass_gain * strength)
            return bypass - attack * (0.15 * strength)
        return (target - robot.position).unit() * strength

    def goal_triangle_clearance_field(self, world: WorldState, robot: RobotState) -> Vec2 | None:
        """Clear the open triangle between the ball and opponent goal posts."""
        attack = Vec2(world.attack_sign, 0.0)
        relative = robot.position - world.ball.position
        forward = relative.dot(attack)
        goal_x = world.attack_sign * world.field_length / 2.0
        goal_distance = (goal_x - world.ball.position.x) * world.attack_sign
        if goal_distance <= 1e-6 or forward <= 0.0 or forward >= goal_distance:
            return None
        fraction = max(0.0, min(1.0, forward / goal_distance))
        centre_y = world.ball.position.y * (1.0 - fraction)
        half_width = fraction * world.goal_width / 2.0 + max(0.0, self.config.goal_triangle_margin)
        lateral = robot.position.y - centre_y
        if abs(lateral) >= half_width:
            return None
        side = math.copysign(1.0, lateral) if abs(lateral) > 1e-9 else (1.0 if robot.robot_id % 2 == 0 else -1.0)
        strength = self.config.goal_triangle_clearance_gain * (1.0 - abs(lateral) / max(half_width, 1e-6))
        outward = Vec2(0.0, side * strength)
        backward = attack * (-self.config.goal_triangle_backward_gain * (1.0 - fraction))
        return (outward + backward).limited(self.config.max_nominal_speed)

    def ball_is_heading_to_enemy_goal(self, world: WorldState) -> bool:
        """Return whether the current ball ray will cross the opponent goal mouth."""
        velocity = world.ball.velocity
        forward_speed = velocity.x * world.attack_sign
        if forward_speed <= 0.0 or velocity.norm() < self.config.ball_goal_min_speed:
            return False
        goal_x = world.attack_sign * world.field_length / 2.0
        time_to_line = (goal_x - world.ball.position.x) / velocity.x
        if time_to_line <= 0.0 or not math.isfinite(time_to_line):
            return False
        crossing_y = world.ball.position.y + velocity.y * time_to_line
        return math.isfinite(crossing_y) and abs(crossing_y) <= world.goal_width / 2.0

    def shot_clearance_field(self, world: WorldState, robot: RobotState) -> Vec2 | None:
        """Push a robot laterally out of a goal-bound ball's forward corridor."""
        if not self.ball_is_heading_to_enemy_goal(world):
            return None
        direction = world.ball.velocity.unit()
        relative = robot.position - world.ball.position
        forward = relative.dot(direction)
        goal_x = world.attack_sign * world.field_length / 2.0
        distance_to_goal = (goal_x - world.ball.position.x) / direction.x
        if forward <= 0.0 or forward >= distance_to_goal:
            return None
        lateral = direction.cross(relative)
        radius = max(self.config.shot_clearance_radius, 1e-6)
        if abs(lateral) >= radius:
            return None
        # Select the shortest exit side. An id-based tie break avoids an
        # undefined direction when the robot is exactly on the shot ray.
        side = math.copysign(1.0, lateral) if abs(lateral) > 1e-9 else (1.0 if robot.robot_id % 2 == 0 else -1.0)
        strength = max(0.0, self.config.shot_clearance_gain) * (1.0 - abs(lateral) / radius)
        return direction.perpendicular(side) * strength

    def corner_approach_side(self, world: WorldState) -> float | None:
        """Return +1 for the attacking left corner and -1 for the right."""
        velocity = world.ball.velocity
        if velocity.norm() < self.config.ball_goal_min_speed or velocity.x * world.attack_sign <= 0.0:
            return None
        goal_x = world.attack_sign * world.field_length / 2.0
        goal_distance = (goal_x - world.ball.position.x) * world.attack_sign
        if not 0.0 < goal_distance <= self.config.corner_gate_goal_distance:
            return None
        time_to_line = (goal_x - world.ball.position.x) / velocity.x
        if time_to_line <= 0.0 or not math.isfinite(time_to_line):
            return None
        crossing_y = world.ball.position.y + velocity.y * time_to_line
        lateral_crossing = crossing_y * world.attack_sign
        corner_distance = abs(abs(lateral_crossing) - world.goal_width / 2.0)
        if not math.isfinite(corner_distance) or corner_distance > self.config.corner_gate_crossing_band:
            return None
        return math.copysign(1.0, lateral_crossing)

    def corner_approach_yield(self, world: WorldState, robot: RobotState) -> Vec2 | None:
        """Make a robot on the opposite side yield to a same-side finisher."""
        side = self.corner_approach_side(world)
        if side is None:
            return None
        relative = robot.position - world.ball.position
        longitudinal = relative.x * world.attack_sign
        lateral = relative.y * world.attack_sign
        player_angle = math.atan2(lateral, abs(longitudinal) + 1e-9)
        if side * player_angle >= self.config.corner_gate_player_angle:
            return None
        return Vec2(-world.attack_sign * self.config.corner_gate_retreat_gain, 0.0)

    def nominal_velocity(self, world: WorldState, robot_id: int) -> Vec2:
        robot = next(robot for robot in world.allies if robot.robot_id == robot_id)
        post_escape = self.goal_post_escape_velocity(world, robot)
        if post_escape is not None:
            return post_escape
        goal_escape = self.goal_escape_velocity(world, robot)
        if goal_escape is not None:
            return goal_escape
        corner_escape = self.attacking_corner_escape_velocity(world, robot)
        if corner_escape is not None:
            return corner_escape
        triangle_clearance = self.goal_triangle_clearance_field(world, robot)
        if triangle_clearance is not None:
            return triangle_clearance
        goal = world.enemy_goal()
        # Always aim through the centre of the goal. Targeting the nearest post
        # when the ball was outside the mouth made several robots converge on
        # the same side-wall pocket and push the ball back out.
        goal_target = Vec2(goal.start.x, 0.0)
        # Every allied robot uses the IFAC'08 structure, with independently
        # evolved geometric parameters and gain for its player index.
        # The paper's local +x axis is rotated on every control tick from the
        # current ball position to the current opponent-goal centre.
        ball_field = univector_ball_field(
            robot,
            world.ball,
            goal_target,
            getattr(self.config, f"ball_spiral_radius_{robot_id}", self.config.ball_spiral_radius),
            getattr(self.config, f"ball_spiral_smoothing_{robot_id}", self.config.ball_spiral_smoothing),
        ) * getattr(self.config, f"approach_gain_{robot_id}", self.config.approach_gain)
        # A goal-bound shot changes the ball contribution from attraction to a
        # lateral clearance field only for robots standing in front of it.
        clearance = self.shot_clearance_field(world, robot)
        if self.ball_is_heading_to_enemy_goal(world):
            field = clearance or Vec2()
        elif (corner_yield := self.corner_approach_yield(world, robot)) is not None:
            field = corner_yield
        else:
            field = ball_field

        # A robot never receives the vector field generated by its own state.
        for ally in world.allies:
            if ally.robot_id == robot_id:
                continue
            contribution = obstacle_field(
                robot,
                ally,
                self.config.robot_safe_distance,
                self.config.collision_horizon,
                self.config.ally_obstacle_radial_gain,
                self.config.ally_obstacle_circular_gain,
            )
            field += self.apply_formula("ally", contribution, world, robot_id, (ally.position - robot.position).norm())

        for enemy in world.enemies:
            contribution = obstacle_field(
                robot,
                enemy,
                self.config.robot_safe_distance,
                self.config.collision_horizon,
                self.config.enemy_obstacle_radial_gain,
                self.config.enemy_obstacle_circular_gain,
            )
            field += self.apply_formula("enemy", contribution, world, robot_id, (enemy.position - robot.position).norm())

        wall_names = ("wall_bottom", "wall_top", "wall_left", "wall_right")
        for wall_name, wall in zip(wall_names, world.walls()):
            contribution = segment_field(
                robot.position,
                wall,
                self.config.wall_influence_distance,
                self.config.wall_gain,
            )
            distance = (robot.position - wall.closest_point(robot.position)).norm()
            group = self.formula.get("fields", {}).get("shared" if self.field_strategy == "shared" else f"ally_{robot_id}", {})
            field += self.apply_formula(wall_name if wall_name in group else "wall", contribution, world, robot_id, distance)

        # This non-evolved base field removes local minima created when allies
        # share the same narrow strip beside a wall.
        field += self.wall_decluster_field(world, robot)

        ally_goal = world.ally_goal().closest_point(robot.position)
        enemy_goal = world.enemy_goal().closest_point(robot.position)
        ally_distance = (ally_goal - robot.position).norm()
        enemy_distance = (enemy_goal - robot.position).norm()
        field += self.apply_formula("ally_goal", (robot.position - ally_goal).unit() * 0.25, world, robot_id, ally_distance)
        field += self.apply_formula("enemy_goal", (enemy_goal - robot.position).unit() * 0.25, world, robot_id, enemy_distance)

        urgency = 1.0 + 0.15 * max(0, -world.goal_difference)
        return (field * urgency).limited(self.config.max_nominal_speed)

    def wheel_command(self, world: WorldState, robot_id: int) -> tuple[float, float]:
        robot = next(robot for robot in world.allies if robot.robot_id == robot_id)
        desired = self.nominal_velocity(world, robot_id)
        return self.wheel_command_for_velocity(robot, desired)

    def wheel_command_for_velocity(self, robot: RobotState, desired: Vec2) -> tuple[float, float]:
        """Convert an externally selected role velocity through the same safety limits."""
        if not desired.is_finite() or desired.norm() < 1e-9:
            return 0.0, 0.0

        desired_heading = math.atan2(desired.y, desired.x)
        heading_error = wrap_angle(desired_heading - robot.orientation)
        linear_speed = desired.norm() * max(0.0, math.cos(heading_error))
        angular_speed = self.config.heading_gain * heading_error
        left = (linear_speed - 0.5 * self.config.wheel_separation * angular_speed) / self.config.wheel_radius
        right = (linear_speed + 0.5 * self.config.wheel_separation * angular_speed) / self.config.wheel_radius
        maximum = max(abs(left), abs(right), self.config.max_wheel_speed)
        if maximum > self.config.max_wheel_speed:
            scale = self.config.max_wheel_speed / maximum
            left *= scale
            right *= scale
        if not math.isfinite(left) or not math.isfinite(right):
            return 0.0, 0.0
        return left, right

    def wheel_command_for_line_motion(
        self,
        robot: RobotState,
        target: Vec2,
        speed: float,
        axis_heading: float = math.pi / 2.0,
        alignment_tolerance: float = math.radians(5.0),
        cross_track_tolerance: float = 0.015,
    ) -> tuple[float, float]:
        """Recover onto a line, align to its axis, then drive along it."""
        axis = Vec2(math.cos(axis_heading), math.sin(axis_heading))
        normal_heading = axis_heading - math.pi / 2.0
        normal = Vec2(math.cos(normal_heading), math.sin(normal_heading))
        displacement = target - robot.position
        cross_track_error = displacement.dot(normal)
        recovering_line = abs(cross_track_error) > cross_track_tolerance
        motion_heading = normal_heading if recovering_line else axis_heading
        motion_error = cross_track_error if recovering_line else displacement.dot(axis)
        forward_error = wrap_angle(motion_heading - robot.orientation)
        reverse_error = wrap_angle(motion_heading + math.pi - robot.orientation)
        if abs(forward_error) <= abs(reverse_error):
            heading_error, orientation_sign = forward_error, 1.0
        else:
            heading_error, orientation_sign = reverse_error, -1.0
        requested = 0.0 if abs(motion_error) < 0.005 else math.copysign(abs(speed), motion_error)
        # Never let a large translation command consume the wheel-speed budget
        # needed to restore alignment. Once parallel, retain angular feedback
        # while driving so collisions and the clearance spin cannot leave the
        # goalkeeper travelling diagonally across its goal line.
        aligned = abs(heading_error) <= alignment_tolerance
        linear_speed = requested * orientation_sign * max(0.0, math.cos(heading_error)) if aligned else 0.0
        angular_speed = self.config.heading_gain * heading_error
        left = (linear_speed - 0.5 * self.config.wheel_separation * angular_speed) / self.config.wheel_radius
        right = (linear_speed + 0.5 * self.config.wheel_separation * angular_speed) / self.config.wheel_radius
        maximum = max(abs(left), abs(right), self.config.max_wheel_speed)
        if maximum > self.config.max_wheel_speed:
            scale = self.config.max_wheel_speed / maximum
            left *= scale
            right *= scale
        return (left, right) if math.isfinite(left) and math.isfinite(right) else (0.0, 0.0)
