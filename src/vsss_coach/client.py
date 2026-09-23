"""UDP/VSSProto adapter for TraveSim."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import socket
import sys
from typing import Any

from .controller import StrategyConfig, VectorFieldStrategy, wrap_angle
from .fields import (
    ally_goal_ball_corridor_field, earliest_reachable_interception_field,
    interception_crossing_time, interception_speed, predictive_interception_field,
    univector_ball_field,
)
from .model import BallState, RobotState, Vec2, WorldState
from .rules import ROBOCORE_VSSS_2025, RULESETS, get_ruleset


@dataclass(slots=True)
class MotionSample:
    velocity: Vec2
    timestamp: float
    acceleration: Vec2 = Vec2()


class StateEstimator:
    def __init__(self) -> None:
        self.samples: dict[str, MotionSample] = {}

    def acceleration(self, key: str, velocity: Vec2, timestamp: float) -> Vec2:
        previous = self.samples.get(key)
        if previous is None:
            self.samples[key] = MotionSample(velocity, timestamp)
            return Vec2()
        elapsed = timestamp - previous.timestamp
        raw = (velocity - previous.velocity) / elapsed if elapsed > 1e-6 else previous.acceleration
        # Differentiating vision velocity is noisy. Preserve the response to a
        # kick while rejecting single-frame spikes before tactical prediction.
        alpha = 0.25
        filtered = previous.acceleration * (1.0 - alpha) + raw * alpha
        self.samples[key] = MotionSample(velocity, timestamp, filtered)
        return filtered


@dataclass(slots=True)
class GoalkeeperSpinTracker:
    active: bool = False
    armed: bool = True
    direction: float = 1.0
    accumulated_angle: float = 0.0
    previous_orientation: float | None = None
    contact_timestamp: float | None = None
    pending: bool = False

    def reset(self) -> None:
        self.active = False
        self.armed = True
        self.accumulated_angle = 0.0
        self.previous_orientation = None
        self.contact_timestamp = None
        self.pending = False

    def update(
        self,
        contact: bool,
        orientation: float,
        preferred_direction: float = 1.0,
        released: bool = False,
        timestamp: float = 0.0,
        delay_seconds: float = 0.0,
    ) -> bool:
        if self.active and self.previous_orientation is not None:
            self.accumulated_angle += abs(wrap_angle(orientation - self.previous_orientation))
            if self.accumulated_angle >= math.pi * 0.95:
                self.active = False
        if released and not self.pending and not self.active:
            self.armed = True
        if contact and self.armed and not self.active and not self.pending:
            self.pending = True
            self.armed = False
            self.contact_timestamp = timestamp
            self.direction = 1.0 if preferred_direction >= 0.0 else -1.0
            self.accumulated_angle = 0.0
        if self.pending and self.contact_timestamp is not None and timestamp - self.contact_timestamp >= max(0.0, delay_seconds):
            self.pending = False
            self.active = True
        self.previous_orientation = orientation
        return self.active


def attack_sign_for_time(initial_sign: float, match_duration: float, time_remaining: float, periods: int = 2) -> float:
    """Mirror the attack direction whenever a new regulation period begins."""
    period_duration = max(match_duration / max(periods, 1), 1e-6)
    elapsed = max(0.0, min(match_duration, match_duration - time_remaining))
    period = min(max(periods - 1, 0), int(elapsed / period_duration))
    return initial_sign if period % 2 == 0 else -initial_sign


def default_attack_sign(yellow_team: bool) -> float:
    """Return TraveSim's first-period attack direction for a team color."""
    return -1.0 if yellow_team else 1.0


def parse_world(
    environment: Any,
    yellow_team: bool,
    attack_sign: float,
    time_remaining: float,
    estimator: StateEstimator,
    timestamp: float,
    match_duration: float = 600.0,
) -> WorldState:
    frame = environment.frame
    allies_proto = frame.robots_yellow if yellow_team else frame.robots_blue
    enemies_proto = frame.robots_blue if yellow_team else frame.robots_yellow

    def robot_state(proto: Any, prefix: str) -> RobotState:
        velocity = Vec2(proto.vx, proto.vy)
        return RobotState(
            position=Vec2(proto.x, proto.y),
            velocity=velocity,
            acceleration=estimator.acceleration(f"{prefix}:{proto.robot_id}", velocity, timestamp),
            robot_id=proto.robot_id,
            orientation=proto.orientation,
            angular_velocity=proto.vorientation,
        )

    ball_velocity = Vec2(frame.ball.vx, frame.ball.vy)
    ball = BallState(
        position=Vec2(frame.ball.x, frame.ball.y),
        velocity=ball_velocity,
        acceleration=estimator.acceleration("ball", ball_velocity, timestamp),
    )
    goals_for = environment.goals_yellow if yellow_team else environment.goals_blue
    goals_against = environment.goals_blue if yellow_team else environment.goals_yellow
    return WorldState(
        ball=ball,
        allies=tuple(robot_state(robot, "ally") for robot in allies_proto),
        enemies=tuple(robot_state(robot, "enemy") for robot in enemies_proto),
        field_length=environment.field.length,
        field_width=environment.field.width,
        goal_width=environment.field.goal_width,
        goal_depth=environment.field.goal_depth,
        goals_for=goals_for,
        goals_against=goals_against,
        time_remaining=time_remaining,
        attack_sign=attack_sign,
        # Safety dwell timers and state estimation both follow simulated time,
        # especially when Webots runs faster than real time.
        metadata={"match_duration": match_duration, "timestamp": match_duration - time_remaining},
    )


def run(args: argparse.Namespace) -> None:
    try:
        from vssproto.simulation.command_pb2 import Command, Commands
        from vssproto.simulation.packet_pb2 import Environment, Packet
    except ImportError as exc:
        raise SystemExit("VSSProto is not installed. Run: python -m pip install -e './strategy'") from exc

    rules = get_ruleset(args.ruleset)
    yellow_team = args.team == "yellow"
    command_port = args.command_port or (20012 if yellow_team else 20013)
    # In TraveSim's first period yellow starts on the right and attacks the
    # blue goal on the left; blue starts on the left and attacks right.
    initial_attack_sign = args.attack_sign if args.attack_sign is not None else default_attack_sign(yellow_team)
    candidate_payload: dict[str, Any] = {}
    if args.candidate:
        candidate_payload = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    config = StrategyConfig.from_json(args.candidate) if args.candidate else StrategyConfig()
    config = StrategyConfig(**{
        **{name: getattr(config, name) for name in config.__dataclass_fields__},
        "max_wheel_speed": args.max_wheel_speed,
    })
    strategy = VectorFieldStrategy(
        config,
        field_strategy=str(candidate_payload.get("formula", {}).get("field_strategy", args.field_strategy)),
        formula=candidate_payload.get("formula"),
    )
    estimator = StateEstimator()
    goalkeeper_spin = GoalkeeperSpinTracker()

    vision_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    vision_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        vision_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    vision_socket.bind(("", args.vision_port))
    membership = socket.inet_aton(args.vision_address) + socket.inet_aton(args.vision_interface)
    vision_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)

    command_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    command_socket.connect((args.command_address, command_port))
    print(
        f"VSSS Coach: {args.team}, rules {rules.name} v{rules.version}, "
        f"vision {args.vision_address}:{args.vision_port}, commands {args.command_address}:{command_port}"
    )
    received_first_frame = False
    previous_ball_position: Vec2 | None = None

    try:
        while True:
            data, _ = vision_socket.recvfrom(65535)
            environment = Environment()
            environment.ParseFromString(data)
            current_ball_position = Vec2(environment.frame.ball.x, environment.frame.ball.y)
            if previous_ball_position is not None and (current_ball_position - previous_ball_position).norm() > 0.15:
                # Role trials teleport the ball between scenarios. Each shot
                # starts with a freshly armed clearance spin and estimator.
                goalkeeper_spin.reset()
                estimator = StateEstimator()
            previous_ball_position = current_ball_position
            if not received_first_frame:
                print(f"VSSS Coach: first vision frame step={environment.step}", file=sys.stderr, flush=True)
                received_first_frame = True
            # Environment.step is the simulator step count. Using simulation
            # time also makes acceleration estimation independent of Webots mode.
            elapsed_simulation = float(environment.step) * args.basic_time_step
            remaining = max(0.0, args.match_duration - elapsed_simulation)
            attack_sign = attack_sign_for_time(initial_attack_sign, args.match_duration, remaining, rules.periods)
            world = parse_world(environment, yellow_team, attack_sign, remaining, estimator, elapsed_simulation, args.match_duration)
            commands = Commands()
            for robot in world.allies:
                if args.role in {"defender", "goalkeeper"} and robot.robot_id == args.active_robot_id:
                    distance = (world.ball.position - robot.position).norm()
                    span = max(1e-6, config.role_far_distance - config.role_near_distance)
                    q = max(0.0, min(1.0, (distance - config.role_near_distance) / span))
                    smooth = q * q * (3.0 - 2.0 * q)
                    speed = config.role_near_speed + (config.role_far_speed - config.role_near_speed) * smooth
                    pursuit = univector_ball_field(
                        robot, world.ball, Vec2(world.attack_sign * world.field_length / 2.0, 0.0),
                        config.ball_spiral_radius_0, config.ball_spiral_smoothing_0,
                    )
                    ally_goal_x = -world.attack_sign * world.field_length / 2.0
                    physical_speed_limit = config.max_wheel_speed * config.wheel_radius
                    if args.role == "goalkeeper":
                        goalkeeper_offset = max(0.045, min(0.09, config.role_intercept_offset))
                        interception, target = predictive_interception_field(
                            robot, world.ball, ally_goal_x, world.goal_width,
                            world.attack_sign, config.role_intercept_horizon,
                            goalkeeper_offset, config.role_intercept_margin,
                            config.role_intercept_min_speed,
                        )
                        line_x = ally_goal_x + world.attack_sign * goalkeeper_offset
                        intercept_time = interception_crossing_time(
                            world.ball, line_x, config.role_intercept_horizon
                        )
                        if target is None:
                            half_opening = max(0.02, world.goal_width / 2.0 - config.role_intercept_margin)
                            target = Vec2(line_x, max(-half_opening, min(half_opening, world.ball.position.y)))
                            interception = (target - robot.position).unit()
                            intercept_time = config.role_intercept_horizon
                        corridor = Vec2()
                    else:
                        interception, target, intercept_time = earliest_reachable_interception_field(
                            robot, world.ball, ally_goal_x, world.goal_width,
                            world.attack_sign, config.role_intercept_horizon,
                            config.role_intercept_min_speed, physical_speed_limit,
                        )
                        corridor, _ = ally_goal_ball_corridor_field(
                            robot, world.ball, ally_goal_x, world.goal_width,
                            world.attack_sign, config.role_intercept_horizon,
                            config.role_intercept_min_speed,
                        )
                    if interception.norm_sq() > 0.0:
                        blend = config.role_intercept_blend
                        pursuit = (
                            interception
                            if args.role == "goalkeeper"
                            else (
                                pursuit * (1.0 - blend)
                                + interception * (blend * config.role_intercept_gain)
                                + corridor * config.role_defensive_corridor_gain
                            ).unit()
                        )
                        target_distance = (target - robot.position).norm() if target else distance
                        speed = interception_speed(
                            speed, target_distance, intercept_time, world.ball,
                            world.attack_sign, config.role_intercept_time_margin,
                            config.role_intercept_urgency_gain,
                            config.role_intercept_velocity_gain,
                            config.role_intercept_acceleration_gain,
                            physical_speed_limit,
                            args.basic_time_step,
                        )
                    speed = min(speed, physical_speed_limit)
                    if args.role == "goalkeeper":
                        ball_distance = (world.ball.position - robot.position).norm()
                        spinning = goalkeeper_spin.update(
                            ball_distance <= 0.065,
                            robot.orientation,
                            preferred_direction=world.ball.position.y - robot.position.y,
                            released=ball_distance >= 0.11,
                            timestamp=elapsed_simulation,
                            delay_seconds=config.role_contact_spin_delay,
                        )
                        if spinning:
                            spin_speed = 0.85 * config.max_wheel_speed * goalkeeper_spin.direction
                            left, right = -spin_speed, spin_speed
                        elif goalkeeper_spin.pending:
                            # Hold the intercept position during the learned
                            # contact dwell; translating would break contact.
                            left, right = 0.0, 0.0
                        else:
                            left, right = strategy.wheel_command_for_line_motion(
                                robot, target, speed, math.pi / 2.0
                            )
                    else:
                        desired = pursuit * speed
                        left, right = strategy.wheel_command_for_velocity(robot, desired)
                elif args.role and robot.robot_id != args.active_robot_id:
                    left, right = 0.0, 0.0
                else:
                    left, right = strategy.wheel_command(world, robot.robot_id)
                commands.robot_commands.append(
                    Command(
                        id=robot.robot_id,
                        yellowteam=yellow_team,
                        wheel_left=left,
                        wheel_right=right,
                    )
                )
            packet = Packet()
            packet.cmd.CopyFrom(commands)
            command_socket.send(packet.SerializeToString())
    except KeyboardInterrupt:
        print("\nVSSS Coach stopped")
    finally:
        command_socket.close()
        vision_socket.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VSSS Coach vector-field client for TraveSim")
    parser.add_argument("--team", choices=("yellow", "blue"), default="yellow")
    parser.add_argument("--vision-address", default="224.0.0.1")
    parser.add_argument("--vision-port", type=int, default=10002)
    parser.add_argument("--vision-interface", default="0.0.0.0")
    parser.add_argument("--command-address", default="127.0.0.1")
    parser.add_argument("--command-port", type=int)
    parser.add_argument("--ruleset", choices=tuple(RULESETS), default=ROBOCORE_VSSS_2025.name)
    parser.add_argument(
        "--match-duration",
        type=float,
        default=ROBOCORE_VSSS_2025.regulation_duration,
        help="active regulation time in seconds; official default is 2 x 300 s (halftime excluded)",
    )
    parser.add_argument(
        "--role", choices=("defender", "goalkeeper"),
        help="specialized physical trial controller",
    )
    parser.add_argument("--active-robot-id", type=int, default=0)
    parser.add_argument("--max-wheel-speed", type=float, default=68.0)
    parser.add_argument("--basic-time-step", type=float, default=0.01, help="Webots step duration in seconds")
    parser.add_argument("--attack-sign", type=float, choices=(-1.0, 1.0))
    parser.add_argument("--candidate", help="JSON file containing a candidate genome")
    parser.add_argument(
        "--field-strategy",
        choices=VectorFieldStrategy.FIELD_STRATEGIES,
        default="shared",
        help="individual: one GP field set per ally; shared: one set reused by every ally",
    )
    return parser


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
