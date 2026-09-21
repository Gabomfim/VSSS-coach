"""Fast role-specific training trials with shared scenarios and dense losses."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics
from typing import Iterable

from .fields import (
    ally_goal_ball_corridor_field, earliest_reachable_interception_field,
    interception_speed,
)
from .model import BallState, RobotState, Vec2


BOUNDS = {
    "spiral_radius": (0.04, 0.30),
    "spiral_smoothing": (0.01, 0.30),
    "near_speed": (0.15, 1.20),
    "far_speed": (0.80, 2.50),
    "near_distance": (0.03, 0.30),
    "far_distance": (0.20, 1.40),
    "intercept_horizon": (0.20, 2.50),
    "intercept_offset": (0.045, 0.09),
    "contact_spin_delay": (0.02, 0.30),
    "intercept_margin": (0.00, 0.12),
    "intercept_gain": (0.20, 3.00),
    "intercept_blend": (0.00, 1.00),
    "intercept_min_speed": (0.02, 0.30),
    "intercept_velocity_gain": (0.00, 1.20),
    "intercept_acceleration_gain": (0.00, 0.30),
    "intercept_time_margin": (0.00, 0.35),
    "intercept_urgency_gain": (0.50, 1.50),
    "defensive_corridor_gain": (0.00, 3.00),
}


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: int
    ball: tuple[float, float]
    player: tuple[float, float]
    ball_velocity: tuple[float, float]
    duration: float = 5.0
    player_yaw: float = 0.0


@dataclass(frozen=True, slots=True)
class RoleCandidate:
    candidate_id: str
    spiral_radius: float
    spiral_smoothing: float
    near_speed: float
    far_speed: float
    near_distance: float
    far_distance: float
    intercept_horizon: float = 1.20
    intercept_offset: float = 0.06
    contact_spin_delay: float = 0.10
    intercept_margin: float = 0.04
    intercept_gain: float = 1.20
    intercept_blend: float = 0.75
    intercept_min_speed: float = 0.08
    intercept_velocity_gain: float = 0.45
    intercept_acceleration_gain: float = 0.08
    intercept_time_margin: float = 0.10
    intercept_urgency_gain: float = 1.00
    defensive_corridor_gain: float = 1.00


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def speed_profile(candidate: RoleCandidate, distance: float) -> float:
    span = max(1e-6, candidate.far_distance - candidate.near_distance)
    x = _clip((distance - candidate.near_distance) / span, 0.0, 1.0)
    smooth = x * x * (3.0 - 2.0 * x)
    return candidate.near_speed + (candidate.far_speed - candidate.near_speed) * smooth


def reachable_distance(
    elapsed: float,
    maximum_speed: float = 1.70,
    maximum_acceleration: float = 2.50,
    reaction_delay: float = 0.15,
) -> float:
    """Conservative distance available to a robot starting from rest."""
    moving_time = max(0.0, elapsed - reaction_delay)
    acceleration_time = maximum_speed / max(maximum_acceleration, 1e-9)
    if moving_time <= acceleration_time:
        return 0.5 * maximum_acceleration * moving_time * moving_time
    acceleration_distance = 0.5 * maximum_acceleration * acceleration_time * acceleration_time
    return acceleration_distance + maximum_speed * (moving_time - acceleration_time)


def scenario_is_reachable(
    scenario: Scenario,
    maximum_speed: float = 1.70,
    maximum_acceleration: float = 2.50,
    reaction_delay: float = 0.15,
    safety_margin: float = 0.04,
) -> bool:
    """Whether a defender can reach some ball-trajectory point before the goal."""
    bx, by = scenario.ball
    px, py = scenario.player
    vx, vy = scenario.ball_velocity
    if vx >= -1e-9:
        return False
    goal_time = (-0.75 - bx) / vx
    available_time = min(scenario.duration, goal_time)
    if available_time <= reaction_delay:
        return False
    # Constant initial velocity reaches the goal sooner than the rolling ball,
    # making this filter conservative with respect to TraveSim friction.
    samples = 160
    for index in range(1, samples + 1):
        elapsed = available_time * index / samples
        ball_x, ball_y = bx + vx * elapsed, by + vy * elapsed
        required = math.hypot(ball_x - px, ball_y - py) + safety_margin
        if required <= reachable_distance(
            elapsed, maximum_speed, maximum_acceleration, reaction_delay
        ) + 0.065:
            return True
    return False


def generate_scenarios(
    role: str,
    count: int,
    seed: int,
    duration: float = 5.0,
    feasible_only: bool = True,
    maximum_speed: float = 1.70,
    maximum_acceleration: float = 2.50,
    reaction_delay: float = 0.15,
    safety_margin: float = 0.04,
) -> list[Scenario]:
    """Generate one reproducible scenario list shared by every candidate."""
    rng = random.Random(seed)
    scenarios: list[Scenario] = []
    attempts = 0
    while len(scenarios) < count:
        index = len(scenarios)
        attempts += 1
        proposal_index = attempts - 1
        if attempts > max(1000, count * 500):
            raise RuntimeError("could not sample enough physically reachable scenarios")
        # Latin-style bins cover the field while retaining random variation.
        bx = -0.65 + 1.30 * ((proposal_index % 10 + rng.random()) / 10.0)
        by = -0.55 + 1.10 * (((proposal_index * 7) % 10 + rng.random()) / 10.0)
        px = -0.68 + 1.36 * (((proposal_index * 3) % 10 + rng.random()) / 10.0)
        py = -0.58 + 1.16 * (((proposal_index * 9) % 10 + rng.random()) / 10.0)
        # Cycle accepted scenarios through all kick magnitudes. Rejected
        # geometry is resampled without silently removing fast shots.
        force_bin = index % 5
        speed = (
            0.75 + 1.25 * force_bin / 4.0
            if role == "goalkeeper"
            else 0.25 + 1.00 * force_bin / 4.0
        )
        if role in {"goalkeeper", "defender"}:
            target_bin = (proposal_index * 7) % 10
            target_y = -0.18 + 0.36 * ((target_bin + rng.random()) / 10.0)
            dx, dy = -0.75 - bx, target_y - by
            if role == "goalkeeper":
                # Vary the goalkeeper along its line, not its distance from
                # goal. Cross-track variation trains a different maneuver and
                # made short shots begin before the line was acquired.
                px = -0.69
                py = rng.uniform(-0.18, 0.18)
        else:
            target_y = rng.uniform(-0.17, 0.17)
            dx, dy = 0.75 - bx, target_y - by
        norm = max(1e-9, math.hypot(dx, dy))
        scenario = Scenario(
            index, (bx, by), (px, py), (speed * dx / norm, speed * dy / norm),
            duration, math.pi / 2.0 if role == "goalkeeper" else 0.0,
        )
        if (
            feasible_only
            and role in {"goalkeeper", "defender"}
            and not scenario_is_reachable(
                scenario, maximum_speed, maximum_acceleration, reaction_delay, safety_margin
            )
        ):
            continue
        scenarios.append(scenario)
    return scenarios


def evaluate_episode(role: str, candidate: RoleCandidate, scenario: Scenario) -> dict[str, float]:
    """Deterministic point-mass proxy; physical Webots validation uses the same artifacts."""
    bx, by = scenario.ball
    px, py = scenario.player
    bvx, bvy = scenario.ball_velocity
    dt, radius = 1.0 / 60.0, 0.065
    min_distance, contact_time, redirected = 10.0, scenario.duration, 0.0
    goal = 0.0
    steps = max(1, round(scenario.duration / dt))
    for step in range(steps):
        threat_goal_x = -0.75 if role in {"goalkeeper", "defender"} else 0.75
        threat = role in {"goalkeeper", "defender"} and bvx < -candidate.intercept_min_speed
        line_x = threat_goal_x + candidate.intercept_offset
        ball_ax, ball_ay = -0.06 * bvx, -0.06 * bvy
        c = bx - line_x
        discriminant = bvx * bvx - 2.0 * ball_ax * c
        roots = []
        if threat and abs(ball_ax) > 1e-8 and discriminant >= 0.0:
            root = math.sqrt(discriminant)
            roots = [(-bvx - root) / ball_ax, (-bvx + root) / ball_ax]
        elif threat and abs(bvx) > 1e-8:
            roots = [-c / bvx]
        positive_roots = [value for value in roots if value > 0.0]
        time_to_line = min(positive_roots) if positive_roots else 0.0
        prediction_time = _clip(time_to_line, 0.0, candidate.intercept_horizon)
        half_opening = max(0.02, 0.20 - candidate.intercept_margin)
        intercept_y = _clip(
            by + bvy * prediction_time + 0.5 * ball_ay * prediction_time * prediction_time,
            -half_opening, half_opening,
        )
        pursuit_x = bx if role == "attacker" else max(-0.70, bx - 0.08)
        pursuit_y = by
        dx, dy = pursuit_x - px, pursuit_y - py
        control_distance = math.hypot(dx, dy)
        if role == "defender" and threat:
            ball_state = BallState(Vec2(bx, by), Vec2(bvx, bvy), Vec2(ball_ax, ball_ay))
            robot_state = RobotState(position=Vec2(px, py), robot_id=0)
            intercept, target, intercept_time = earliest_reachable_interception_field(
                robot_state, ball_state, -0.75, 0.40, 1.0,
                candidate.intercept_horizon, candidate.intercept_min_speed, 1.70,
            )
            corridor, _ = ally_goal_ball_corridor_field(
                robot_state, ball_state, -0.75, 0.40, 1.0,
                candidate.intercept_horizon, candidate.intercept_min_speed,
            )
            base = Vec2(dx, dy).unit()
            blend = candidate.intercept_blend
            combined = (
                base * (1.0 - blend)
                + intercept * (blend * candidate.intercept_gain)
                + corridor * candidate.defensive_corridor_gain
            ).unit()
            dx, dy = combined.x, combined.y
            if target is not None:
                control_distance = (target - robot_state.position).norm()
            time_to_line = intercept_time if target is not None else 0.0
        elif threat and time_to_line > 0.0:
            blend = candidate.intercept_blend
            dx = (1.0 - blend) * dx + blend * candidate.intercept_gain * (line_x - px)
            dy = (1.0 - blend) * dy + blend * candidate.intercept_gain * (intercept_y - py)
        distance = control_distance
        player_speed = speed_profile(candidate, distance)
        if threat and time_to_line > 0.0 and math.isfinite(time_to_line):
            player_speed = interception_speed(
                player_speed, distance, time_to_line,
                BallState(Vec2(bx, by), Vec2(bvx, bvy), Vec2(ball_ax, ball_ay)),
                1.0, candidate.intercept_time_margin,
                candidate.intercept_urgency_gain, candidate.intercept_velocity_gain,
                candidate.intercept_acceleration_gain, 1.70, dt,
            )
        player_speed = min(player_speed, 1.70)
        motion_norm = math.hypot(dx, dy)
        if motion_norm > 1e-9:
            px += player_speed * dx / motion_norm * dt
            py += player_speed * dy / motion_norm * dt
        bx += bvx * dt + 0.5 * ball_ax * dt * dt
        by += bvy * dt + 0.5 * ball_ay * dt * dt
        bvx *= 0.999
        bvy *= 0.999
        separation = math.hypot(bx - px, by - py)
        min_distance = min(min_distance, separation)
        if separation <= radius:
            contact_time = min(contact_time, step * dt)
            desired_x = 1.0 if role == "attacker" else 1.0
            bvx = abs(bvx) * desired_x + 0.35
            bvy *= 0.35
            redirected = 1.0
        if bx <= -0.75 and abs(by) <= 0.20:
            goal = 1.0 if role != "attacker" else 0.0
            break
        if bx >= 0.75 and abs(by) <= 0.20:
            goal = 1.0 if role == "attacker" else 0.0
            break
        if abs(by) > 0.65 or abs(bx) > 0.85:
            break
    desired_success = goal if role == "attacker" else 1.0 - goal
    losses = {
        "outcome": 1.0 - desired_success,
        "interception": _clip(min_distance / 0.75, 0.0, 1.0),
        "reaction_time": _clip(contact_time / scenario.duration, 0.0, 1.0),
        "redirection": 1.0 - redirected,
    }
    losses["total"] = 0.70 * losses["outcome"] + 0.12 * losses["interception"] + 0.08 * losses["reaction_time"] + 0.10 * losses["redirection"]
    return losses


def evaluate_trial(role: str, candidate: RoleCandidate, scenarios: Iterable[Scenario]) -> dict[str, object]:
    episodes = [evaluate_episode(role, candidate, scenario) for scenario in scenarios]
    components = {key: statistics.fmean(row[key] for row in episodes) for key in episodes[0]}
    return {"candidate_id": candidate.candidate_id, "loss": components["total"], "loss_components": components, "episodes": episodes}


def _evaluate(payload: tuple[str, RoleCandidate, list[Scenario]]) -> dict[str, object]:
    return evaluate_trial(*payload)


def random_candidate(candidate_id: str, rng: random.Random, centre: RoleCandidate | None = None, sigma: float = 0.18) -> RoleCandidate:
    values = {}
    for key, (low, high) in BOUNDS.items():
        base = getattr(centre, key) if centre else (low + high) / 2.0
        values[key] = _clip(base + rng.gauss(0.0, sigma * (high - low)), low, high)
    values["far_speed"] = max(values["near_speed"], values["far_speed"])
    values["far_distance"] = max(values["near_distance"] + 0.01, values["far_distance"])
    return RoleCandidate(candidate_id, **values)


def sample_distribution(candidate_id: str, rng: random.Random, mean: dict[str, float], sigma: dict[str, float]) -> RoleCandidate:
    values = {key: _clip(rng.gauss(mean[key], sigma[key]), *bounds) for key, bounds in BOUNDS.items()}
    values["far_speed"] = max(values["near_speed"], values["far_speed"])
    values["far_distance"] = max(values["near_distance"] + 0.01, values["far_distance"])
    return RoleCandidate(candidate_id, **values)


def update_adaptive_diagonal_es(
    ranked: list[dict[str, object]], candidates: dict[str, RoleCandidate],
    previous_sigma: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Weighted diagonal covariance adaptation for bounded continuous genes."""
    elite_count = max(2, len(ranked) // 4)
    elite = [candidates[str(row["candidate_id"])] for row in ranked[:elite_count]]
    raw_weights = [math.log(elite_count + 0.5) - math.log(index + 1) for index in range(elite_count)]
    total_weight = sum(raw_weights)
    weights = [weight / total_weight for weight in raw_weights]
    mean = {key: sum(weight * getattr(candidate, key) for weight, candidate in zip(weights, elite)) for key in BOUNDS}
    sigma = {}
    for key, (low, high) in BOUNDS.items():
        variance = sum(weight * (getattr(candidate, key) - mean[key]) ** 2 for weight, candidate in zip(weights, elite))
        floor = 0.01 * (high - low)
        sigma[key] = max(floor, 0.75 * previous_sigma[key] + 0.25 * math.sqrt(max(variance, floor * floor)))
    return mean, sigma


def surrogate_refine_mean(
    ranked: list[dict[str, object]], candidates: dict[str, RoleCandidate],
    mean: dict[str, float], sigma: dict[str, float], learning_rate: float = 0.35,
) -> dict[str, float]:
    """Take a ridge-linear local-model step; Webots itself remains non-differentiable."""
    keys = list(BOUNDS)
    rows = [candidates[str(row["candidate_id"])] for row in ranked]
    losses = [float(row["loss"]) for row in ranked]
    y_mean = statistics.fmean(losses)
    features = [[(getattr(candidate, key) - mean[key]) / max(sigma[key], 1e-9) for key in keys] for candidate in rows]
    size = len(keys)
    matrix = [[sum(row[i] * row[j] for row in features) + (0.25 if i == j else 0.0) for j in range(size)] for i in range(size)]
    vector = [sum(row[i] * (loss - y_mean) for row, loss in zip(features, losses)) for i in range(size)]
    # Gauss-Jordan solve of the small ridge system avoids a heavy numeric dependency.
    augmented = [matrix[i] + [vector[i]] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        if abs(scale) < 1e-12:
            continue
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [value - factor * base for value, base in zip(augmented[row], augmented[column])]
    gradient = [augmented[index][-1] for index in range(size)]
    norm = math.sqrt(sum(value * value for value in gradient)) or 1.0
    return {
        key: _clip(mean[key] - learning_rate * sigma[key] * gradient[index] / norm, *BOUNDS[key])
        for index, key in enumerate(keys)
    }


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> Path:
    run_dir = Path(args.output) / (args.run_name or datetime.now().strftime("role-%Y%m%d-%H%M%S"))
    generations_dir = run_dir / "generations"
    replays_dir = run_dir / "replays"
    checkpoints_dir = run_dir / "checkpoints"
    if run_dir.exists() and not args.resume:
        raise FileExistsError(f"run already exists: {run_dir}; use --resume")
    generations_dir.mkdir(parents=True, exist_ok=args.resume)
    replays_dir.mkdir(exist_ok=args.resume)
    checkpoints_dir.mkdir(exist_ok=args.resume)
    if not (run_dir / "config.json").exists():
        atomic_json(run_dir / "config.json", vars(args))
    rng = random.Random(args.seed)
    mean = {key: (low + high) / 2.0 for key, (low, high) in BOUNDS.items()}
    sigma = {key: args.initial_sigma * (high - low) for key, (low, high) in BOUNDS.items()}
    population = [sample_distribution(f"g0000-c{i:04d}", rng, mean, sigma) for i in range(args.candidates)]
    for generation in range(args.generations):
        completed_generation = generations_dir / f"generation-{generation:04d}.json"
        if completed_generation.exists():
            # Replay the optimizer transition and RNG draws for each completed
            # generation. Reading only the latest optimizer-state and seeding
            # each candidate anew silently changed the pending population.
            rows = json.loads(completed_generation.read_text(encoding="utf-8"))
            candidate_by_id = {str(row["candidate_id"]): RoleCandidate(**row["parameters"]) for row in rows}
            mean, sigma = update_adaptive_diagonal_es(rows, candidate_by_id, sigma)
            if args.optimizer == "hybrid-surrogate-es":
                mean = surrogate_refine_mean(rows, candidate_by_id, mean, sigma, args.surrogate_learning_rate)
            if generation + 1 < args.generations:
                population = [sample_distribution(f"g{generation+1:04d}-c{i:04d}", rng, mean, sigma) for i in range(args.candidates)]
            continue
        scenarios = generate_scenarios(
            args.role, args.scenarios, args.seed + generation * 1009,
            args.scenario_duration, args.feasible_scenarios,
            args.feasibility_max_speed, args.feasibility_max_acceleration,
            args.feasibility_reaction_delay, args.feasibility_safety_margin,
        )
        generation_checkpoint = checkpoints_dir / f"generation-{generation:04d}"
        generation_checkpoint.mkdir(exist_ok=True)
        population_path = generation_checkpoint / "population.json"
        if population_path.exists():
            population = [RoleCandidate(**row) for row in json.loads(population_path.read_text(encoding="utf-8"))]
        else:
            atomic_json(population_path, [asdict(candidate) for candidate in population])
        results = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(generation_checkpoint.glob("g*-c*.json"))]
        completed_ids = {str(row["candidate_id"]) for row in results}
        atomic_json(run_dir / "live.json", {"status": "running", "generation": generation, "completed": len(results), "total": len(population)})
        if args.backend == "travesim":
            from .role_travesim import RoleTraveSimConfig, run_physical_trial
            physical_config = RoleTraveSimConfig(
                project_root=str(Path(args.travesim_root).resolve()),
                webots=str(Path(args.webots).expanduser().resolve()),
                webots_mode=args.webots_mode,
                port_base=args.port_base,
                timeout=args.physical_timeout,
                retries=args.physical_retries,
                client_sync_delay_ms=args.client_sync_delay_ms,
            )
            tasks = [
                (args.role, candidate, scenarios, index, physical_config)
                for index, candidate in enumerate(population)
                if candidate.candidate_id not in completed_ids
            ]
            evaluator = run_physical_trial
        else:
            tasks = [(args.role, candidate, scenarios) for candidate in population if candidate.candidate_id not in completed_ids]
            evaluator = _evaluate
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(evaluator, task) for task in tasks]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                atomic_json(generation_checkpoint / f"{result['candidate_id']}.json", result)
                atomic_json(run_dir / "live.json", {"status": "running", "generation": generation, "completed": len(results), "total": len(population), "best_loss_so_far": min(float(row["loss"]) for row in results)})
        results.sort(key=lambda row: float(row["loss"]))
        best_frames = results[0].pop("frames", [])
        for result in results[1:]:
            result.pop("frames", None)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in population}
        rows = [{**row, "parameters": asdict(candidate_by_id[str(row["candidate_id"])])} for row in results]
        atomic_json(replays_dir / f"generation-{generation:04d}.json", {
            "generation": generation,
            "role": args.role,
            "candidate_id": rows[0]["candidate_id"],
            "loss": rows[0]["loss"],
            "parameters": rows[0]["parameters"],
            "frames": best_frames,
        })
        atomic_json(generations_dir / f"generation-{generation:04d}.json", rows)
        atomic_json(run_dir / "ranking.json", rows)
        atomic_json(run_dir / "scenarios.json", [asdict(item) for item in scenarios])
        mean, sigma = update_adaptive_diagonal_es(results, candidate_by_id, sigma)
        if args.optimizer == "hybrid-surrogate-es":
            mean = surrogate_refine_mean(results, candidate_by_id, mean, sigma, args.surrogate_learning_rate)
        atomic_json(run_dir / "optimizer-state.json", {"optimizer": args.optimizer, "generation": generation, "mean": mean, "sigma": sigma})
        atomic_json(run_dir / "live.json", {"status": "generation_complete", "generation": generation, "completed": len(population), "total": len(population), "best_candidate": rows[0]["candidate_id"], "best_loss": rows[0]["loss"]})
        if generation + 1 < args.generations:
            population = [sample_distribution(f"g{generation+1:04d}-c{i:04d}", rng, mean, sigma) for i in range(args.candidates)]
    final_rows = json.loads((generations_dir / f"generation-{args.generations - 1:04d}.json").read_text(encoding="utf-8"))
    atomic_json(run_dir / "live.json", {"status": "complete", "generation": args.generations - 1, "completed": args.candidates, "total": args.candidates, "best_candidate": final_rows[0]["candidate_id"], "best_loss": final_rows[0]["loss"]})
    return run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coach Silvs role-specific multi-scenario trials")
    parser.add_argument("--role", choices=("goalkeeper", "defender", "attacker"), required=True)
    parser.add_argument("--candidates", type=int, default=32)
    parser.add_argument("--scenarios", type=int, default=60)
    parser.add_argument("--scenario-duration", type=float, default=5.0)
    parser.add_argument(
        "--feasible-scenarios", action=argparse.BooleanOptionalAction, default=True,
        help="reject defensive trials with no physically reachable interception",
    )
    parser.add_argument("--feasibility-max-speed", type=float, default=1.70)
    parser.add_argument("--feasibility-max-acceleration", type=float, default=2.50)
    parser.add_argument("--feasibility-reaction-delay", type=float, default=0.15)
    parser.add_argument("--feasibility-safety-margin", type=float, default=0.04)
    parser.add_argument("--generations", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--backend", choices=("proxy", "travesim"), default="proxy")
    parser.add_argument("--travesim-root", default=".")
    parser.add_argument(
        "--webots",
        default="webots",
    )
    parser.add_argument("--webots-mode", choices=("fast", "realtime"), default="fast")
    parser.add_argument("--port-base", type=int, default=31000)
    parser.add_argument("--physical-timeout", type=float, default=900.0)
    parser.add_argument("--physical-retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true", help="continue an existing run and reuse candidate checkpoints")
    parser.add_argument("--client-sync-delay-ms", type=int, default=2)
    parser.add_argument("--optimizer", choices=("adaptive-diagonal-es", "hybrid-surrogate-es"), default="hybrid-surrogate-es")
    parser.add_argument("--surrogate-learning-rate", type=float, default=0.35)
    parser.add_argument("--initial-sigma", type=float, default=0.18, help="initial standard deviation as a fraction of each parameter range")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="role-runs")
    parser.add_argument("--run-name")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if min(args.candidates, args.scenarios, args.generations, args.workers) < 1 or args.scenario_duration <= 0:
        raise SystemExit("candidate, scenario, generation, worker counts and duration must be positive")
    if args.backend == "travesim":
        if args.role not in {"defender", "goalkeeper"}:
            raise SystemExit("the physical backend currently supports --role defender and --role goalkeeper")
        if not Path(args.webots).expanduser().is_file():
            raise SystemExit(f"Webots executable not found: {args.webots}")
        if not (Path(args.travesim_root) / "worlds" / "Match3v3.wbt").is_file():
            raise SystemExit(f"TraveSim root is invalid: {args.travesim_root}")
    print(run(args).resolve())


if __name__ == "__main__":
    main()
