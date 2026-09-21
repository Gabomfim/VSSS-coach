"""Typed vector-field primitives used by the baseline and future GP trees."""

from __future__ import annotations

import math

from .model import BallState, MovingState, Segment, Vec2


def predicted_position(obj: MovingState, horizon: float) -> Vec2:
    horizon = max(0.0, horizon)
    return obj.position + obj.velocity * horizon + obj.acceleration * (0.5 * horizon * horizon)


def closest_approach(robot: MovingState, obstacle: MovingState, horizon: float) -> tuple[float, float]:
    relative_position = obstacle.position - robot.position
    relative_velocity = obstacle.velocity - robot.velocity
    time = max(
        0.0,
        min(horizon, -relative_position.dot(relative_velocity) / (relative_velocity.norm_sq() + 1e-9)),
    )
    relative_acceleration = obstacle.acceleration - robot.acceleration
    separation = relative_position + relative_velocity * time + relative_acceleration * (0.5 * time * time)
    return time, separation.norm()


def point_field(
    robot: MovingState,
    obj: MovingState,
    radial: float,
    tangential: float = 0.0,
    horizon: float = 0.0,
) -> Vec2:
    direction = (predicted_position(obj, horizon) - robot.position).unit()
    return direction * radial + direction.perpendicular() * tangential


def obstacle_field(
    robot: MovingState,
    obstacle: MovingState,
    safe_distance: float,
    horizon: float,
    radial_gain: float,
    circular_gain: float,
) -> Vec2:
    time, distance = closest_approach(robot, obstacle, horizon)
    distance_risk = 1.0 / (1.0 + math.exp(10.0 * (distance - safe_distance)))
    time_risk = math.exp(-time / max(horizon, 1e-6))
    risk = distance_risk * time_risk
    direction = (obstacle.position - robot.position).unit()
    nominal_side = 1.0 if direction.cross(robot.velocity) >= 0.0 else -1.0
    return risk * (-radial_gain * direction + circular_gain * direction.perpendicular(nominal_side))


def segment_field(
    position: Vec2,
    segment: Segment,
    influence_distance: float,
    normal_gain: float,
    tangential_gain: float = 0.0,
    tangential_side: float = 1.0,
) -> Vec2:
    closest = segment.closest_point(position)
    away = position - closest
    distance = away.norm()
    if distance >= influence_distance:
        return Vec2()
    activation = 1.0 - distance / max(influence_distance, 1e-9)
    return activation * (
        away.unit() * normal_gain
        + segment.tangent() * (tangential_gain * tangential_side)
    )


def ball_approach_field(
    robot: MovingState,
    ball: BallState,
    goal_target: Vec2,
    prediction_horizon: float,
    behind_distance: float,
    approach_gain: float,
    shot_gain: float,
    orbit_gain: float,
) -> Vec2:
    future_ball = predicted_position(ball, prediction_horizon)
    shot_direction = (goal_target - future_ball).unit()
    approach_point = future_ball - shot_direction * behind_distance
    to_ball = future_ball - robot.position
    proximity = math.exp(-to_ball.norm_sq() / (2.0 * max(behind_distance, 1e-3) ** 2))
    approach = (approach_point - robot.position).unit()
    alignment_error = to_ball.unit().cross(shot_direction)
    orbit = to_ball.unit().perpendicular() * alignment_error
    return approach * (approach_gain * (1.0 - proximity)) + shot_direction * (shot_gain * proximity) + orbit * orbit_gain


def predictive_interception_field(
    robot: MovingState,
    ball: BallState,
    ally_goal_x: float,
    goal_width: float,
    attack_sign: float,
    horizon: float,
    line_offset: float,
    lateral_margin: float,
    minimum_ball_speed: float,
) -> tuple[Vec2, Vec2 | None]:
    """Point toward the future crossing of a goal-side defensive line."""
    toward_velocity = -attack_sign * ball.velocity.x
    toward_acceleration = -attack_sign * ball.acceleration.x
    if toward_velocity < minimum_ball_speed and (
        toward_acceleration <= 0.0
        or toward_velocity + toward_acceleration * max(0.0, horizon) < minimum_ball_speed
    ):
        return Vec2(), None
    line_x = ally_goal_x + attack_sign * line_offset
    crossing_time = interception_crossing_time(ball, line_x, horizon)
    if not math.isfinite(crossing_time) or crossing_time <= 0.0:
        return Vec2(), None
    prediction_time = min(max(0.0, crossing_time), max(0.0, horizon))
    half_opening = max(0.02, goal_width / 2.0 - lateral_margin)
    target = Vec2(
        line_x,
        max(-half_opening, min(half_opening,
            ball.position.y + ball.velocity.y * prediction_time
            + 0.5 * ball.acceleration.y * prediction_time * prediction_time)),
    )
    return (target - robot.position).unit(), target


def interception_crossing_time(ball: BallState, line_x: float, horizon: float) -> float:
    """Return the earliest positive constant-acceleration crossing time."""
    del horizon  # Kept in the API because callers bound the resulting prediction.
    c = ball.position.x - line_x
    a = 0.5 * ball.acceleration.x
    b = ball.velocity.x
    roots: list[float] = []
    if abs(a) > 1e-8:
        discriminant = b * b - 4.0 * a * c
        if discriminant >= 0.0:
            root = math.sqrt(discriminant)
            roots.extend(((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)))
    elif abs(b) > 1e-8:
        roots.append(-c / b)
    positive = [value for value in roots if value > 0.0 and math.isfinite(value)]
    return min(positive) if positive else math.inf


def interception_speed(
    base_speed: float,
    target_distance: float,
    crossing_time: float,
    ball: BallState,
    attack_sign: float,
    time_margin: float,
    urgency_gain: float,
    velocity_gain: float,
    acceleration_gain: float,
    maximum_speed: float,
    minimum_time: float = 0.01,
) -> float:
    """Reachability speed with ball velocity and acceleration feed-forward."""
    available = max(minimum_time, crossing_time - time_margin)
    toward_acceleration = max(0.0, -attack_sign * ball.acceleration.x)
    urgent = (
        urgency_gain * target_distance / available
        + velocity_gain * ball.velocity.norm()
        + acceleration_gain * toward_acceleration
    )
    return max(0.0, min(maximum_speed, max(base_speed, urgent)))


def _robot_reachable_distance(
    elapsed: float,
    initial_speed: float,
    maximum_speed: float,
    maximum_acceleration: float,
    reaction_delay: float,
) -> float:
    moving_time = max(0.0, elapsed - reaction_delay)
    initial_speed = max(0.0, min(maximum_speed, initial_speed))
    acceleration_time = (maximum_speed - initial_speed) / max(maximum_acceleration, 1e-9)
    accelerated_time = min(moving_time, acceleration_time)
    return (
        initial_speed * moving_time
        + 0.5 * maximum_acceleration * accelerated_time * accelerated_time
        + max(0.0, moving_time - acceleration_time) * (maximum_speed - initial_speed)
    )


def earliest_reachable_interception_field(
    robot: MovingState,
    ball: BallState,
    ally_goal_x: float,
    goal_width: float,
    attack_sign: float,
    horizon: float,
    minimum_ball_speed: float,
    maximum_robot_speed: float,
    maximum_robot_acceleration: float = 2.50,
    reaction_delay: float = 0.15,
    contact_radius: float = 0.065,
    samples: int = 120,
) -> tuple[Vec2, Vec2 | None, float]:
    """Target the earliest physically reachable point of a goal-bound shot."""
    toward_velocity = -attack_sign * ball.velocity.x
    toward_acceleration = -attack_sign * ball.acceleration.x
    if toward_velocity < minimum_ball_speed and toward_acceleration <= 0.0:
        return Vec2(), None, math.inf
    goal_time = interception_crossing_time(ball, ally_goal_x, horizon)
    if not math.isfinite(goal_time) or goal_time <= 0.0:
        return Vec2(), None, math.inf
    goal_y = (
        ball.position.y + ball.velocity.y * goal_time
        + 0.5 * ball.acceleration.y * goal_time * goal_time
    )
    if abs(goal_y) > goal_width / 2.0:
        return Vec2(), None, math.inf
    last_time = min(goal_time, max(0.0, horizon))
    if last_time <= 0.0:
        return Vec2(), None, math.inf
    fallback = None
    for index in range(1, max(2, samples) + 1):
        elapsed = last_time * index / max(2, samples)
        target = predicted_position(ball, elapsed)
        fallback = target
        reachable = _robot_reachable_distance(
            elapsed, robot.velocity.norm(), maximum_robot_speed,
            maximum_robot_acceleration, reaction_delay,
        )
        if (target - robot.position).norm() <= reachable + contact_radius:
            return (target - robot.position).unit(), target, elapsed
    # Chasing the horizon point is still preferable to waiting at a goal line.
    return (fallback - robot.position).unit(), fallback, last_time


def ally_goal_ball_corridor_field(
    robot: MovingState,
    ball: BallState,
    ally_goal_x: float,
    goal_width: float,
    attack_sign: float,
    horizon: float,
    minimum_ball_speed: float,
) -> tuple[Vec2, Vec2 | None]:
    """Attract a defender onto the segment between a goal-bound ball and goal."""
    goal_time = interception_crossing_time(ball, ally_goal_x, horizon)
    if (
        ball.velocity.x * attack_sign >= -minimum_ball_speed
        or not math.isfinite(goal_time)
        or goal_time <= 0.0
    ):
        return Vec2(), None
    crossing_y = (
        ball.position.y + ball.velocity.y * goal_time
        + 0.5 * ball.acceleration.y * goal_time * goal_time
    )
    if abs(crossing_y) > goal_width / 2.0:
        return Vec2(), None
    corridor = Segment(Vec2(ally_goal_x, 0.0), ball.position)
    target = corridor.closest_point(robot.position)
    return (target - robot.position).unit(), target


def univector_ball_field(
    robot: MovingState,
    ball: BallState,
    goal_target: Vec2,
    spiral_radius: float = 0.11,
    spiral_smoothing: float = 0.08,
) -> Vec2:
    """IFAC'08 move-to-goal field: reach the ball aligned with the shot direction.

    This implements the two-hyperbolic-spiral composition in Eqs. (2) and (4)
    of Lim et al. The coordinate frame is centered at the ball and its +x axis
    points from the ball toward the desired point in the opponent goal.
    """
    shot = (goal_target - ball.position).unit()
    if shot.norm_sq() < 1e-12:
        shot = Vec2(1.0, 0.0)
    # Operational VSSS implementations build the canonical +x axis opposite
    # the desired final kick direction.  The resulting field approaches the
    # ball along ``shot`` after it is transformed back to world coordinates.
    canonical_x = shot * -1.0
    canonical_y = canonical_x.perpendicular()
    relative = robot.position - ball.position
    x, y = relative.dot(canonical_x), relative.dot(canonical_y)
    de = max(spiral_radius, 1e-6)
    kr = max(spiral_smoothing, 0.0)

    def spiral(px: float, py: float, clockwise: bool) -> Vec2:
        rho = math.hypot(px, py)
        theta = math.atan2(py, px)
        bend = (
            math.pi / 2.0 * (2.0 - (de + kr) / (rho + kr))
            if rho > de
            else math.pi / 2.0 * math.sqrt(rho / de)
        )
        # Preserve the numerical CW/CCW convention used by the reference VSSS
        # implementation; the canonical transform handles the world frame.
        phi = theta + bend if clockwise else theta - bend
        return Vec2(math.cos(phi), math.sin(phi))

    if y < -de:
        local = spiral(x, y - de, clockwise=True)
    elif y >= de:
        local = spiral(x, y + de, clockwise=False)
    else:
        yl, yr = y + de, y - de
        left = spiral(x, y - de, clockwise=False)
        right = spiral(x, y + de, clockwise=True)
        # The signed weight printed in Eq. (4) is a known typographical issue.
        # Both maintained VSSS implementations found online use magnitudes to
        # reproduce the field shown in the paper's Figure 3.
        local = (left * abs(yl) + right * abs(yr)) * (1.0 / (2.0 * de))
        if local.norm_sq() < 1e-12:
            local = Vec2(1.0, 0.0)
        else:
            local = local.unit()
    return canonical_x * local.x + canonical_y * local.y
