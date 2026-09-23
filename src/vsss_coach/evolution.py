"""Parallel evolutionary tournament orchestration and reproducible run artifacts."""

from __future__ import annotations

import argparse
from collections import deque
import copy
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import itertools
import json
import math
import os
from pathlib import Path
import random
import re
import time
from typing import Any, Iterable
from functools import partial

from .controller import StrategyConfig
from .formations import FORMATION_NAMES
from .formula import formula_block as executable_formula_block, random_expression
from .rules import ROBOCORE_VSSS_2025, RULESETS, get_ruleset
from .wandb_tracking import WandbTracker
from .travesim_backend import TraveSimConfig, run_travesim_match


GENOME_KEYS = (
    "ball_prediction_horizon",
    "collision_horizon",
    "behind_ball_distance",
    "ball_spiral_radius",
    "ball_spiral_smoothing",
    "ball_spiral_radius_0",
    "ball_spiral_radius_1",
    "ball_spiral_radius_2",
    "ball_spiral_smoothing_0",
    "ball_spiral_smoothing_1",
    "ball_spiral_smoothing_2",
    "ball_goal_min_speed",
    "shot_clearance_radius",
    "shot_clearance_gain",
    "corner_gate_goal_distance",
    "corner_gate_crossing_band",
    "corner_gate_player_angle",
    "corner_gate_retreat_gain",
    "ball_return_gain_0",
    "ball_return_gain_1",
    "ball_return_gain_2",
    "ball_return_power_0",
    "ball_return_power_1",
    "ball_return_power_2",
    "return_ball_clearance_radius",
    "return_ball_bypass_gain",
    "goal_triangle_margin",
    "goal_triangle_clearance_gain",
    "goal_triangle_backward_gain",
    "approach_gain",
    "approach_gain_0",
    "approach_gain_1",
    "approach_gain_2",
    "shot_gain",
    "orbit_gain",
    "obstacle_radial_gain",
    "obstacle_circular_gain",
    "ally_obstacle_radial_gain",
    "ally_obstacle_circular_gain",
    "enemy_obstacle_radial_gain",
    "enemy_obstacle_circular_gain",
    "wall_gain",
    "heading_gain",
)

GENOME_BOUNDS = {
    "ball_spiral_radius": (0.04, 0.30),
    "ball_spiral_smoothing": (0.01, 0.30),
    "ball_spiral_radius_0": (0.04, 0.30),
    "ball_spiral_radius_1": (0.04, 0.30),
    "ball_spiral_radius_2": (0.04, 0.30),
    "ball_spiral_smoothing_0": (0.01, 0.30),
    "ball_spiral_smoothing_1": (0.01, 0.30),
    "ball_spiral_smoothing_2": (0.01, 0.30),
    "approach_gain_0": (0.20, 3.00),
    "approach_gain_1": (0.20, 3.00),
    "approach_gain_2": (0.20, 3.00),
    "ball_goal_min_speed": (0.02, 1.50),
    "shot_clearance_radius": (0.08, 0.30),
    "shot_clearance_gain": (0.30, 3.00),
    "corner_gate_goal_distance": (0.15, 0.75),
    "corner_gate_crossing_band": (0.03, 0.25),
    "corner_gate_player_angle": (0.00, 0.60),
    "corner_gate_retreat_gain": (0.10, 1.50),
    "ball_return_gain_0": (0.20, 3.00),
    "ball_return_gain_1": (0.20, 3.00),
    "ball_return_gain_2": (0.20, 3.00),
    "ball_return_power_0": (1.00, 2.00),
    "ball_return_power_1": (1.00, 2.00),
    "ball_return_power_2": (1.00, 2.00),
    "return_ball_clearance_radius": (0.08, 0.25),
    "return_ball_bypass_gain": (0.30, 3.00),
    "goal_triangle_margin": (0.01, 0.12),
    "goal_triangle_clearance_gain": (0.60, 3.00),
    "goal_triangle_backward_gain": (0.10, 1.20),
}

def remove_legacy_role_fields(formula: dict[str, Any]) -> None:
    formula.pop("roles", None)
    formula.pop("role_evolution", None)
    fields = formula.get("fields", {})
    if not isinstance(fields, dict):
        return
    for group in fields.values():
        if not isinstance(group, dict):
            continue
        group.pop("attacker_ball", None)
        group.pop("defender_ball", None)


def ensure_player_ball_fields(formula: dict[str, Any], robots_per_team: int = 3) -> None:
    """Migrate one legacy ball block into independently breedable player blocks."""
    fields = formula.get("fields", {})
    strategy = formula.get("field_strategy", "shared")
    existing = formula.get("player_ball_fields")
    blocks = list(existing) if isinstance(existing, list) else []
    for robot_id in range(robots_per_team):
        if robot_id < len(blocks) and isinstance(blocks[robot_id], dict):
            continue
        group_name = "shared" if strategy == "shared" else f"ally_{robot_id}"
        group = fields.get(group_name, {}) if isinstance(fields, dict) else {}
        block = group.get("ball", executable_formula_block({"const": 1.0})) if isinstance(group, dict) else executable_formula_block({"const": 1.0})
        if robot_id < len(blocks):
            blocks[robot_id] = copy.deepcopy(block)
        else:
            blocks.append(copy.deepcopy(block))
    formula["player_ball_fields"] = blocks[:robots_per_team]


def bounded_gene(key: str, value: float, maximum: float) -> float:
    minimum, upper = GENOME_BOUNDS.get(key, (-maximum, maximum))
    value = max(minimum, min(upper, value))
    if key.startswith("ball_return_power_"):
        return 1.0 if value < 1.5 else 2.0
    return value


@dataclass(slots=True)
class Candidate:
    candidate_id: str
    generation: int
    genome: dict[str, float]
    formula: dict[str, Any]
    parents: tuple[str, ...] = ()
    score: float = 0.0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0

    def reset_score(self) -> None:
        self.score = 0.0
        self.wins = self.draws = self.losses = 0
        self.goals_for = self.goals_against = 0


@dataclass(frozen=True, slots=True)
class MatchTask:
    match_id: str
    generation: int
    home: dict[str, Any]
    away: dict[str, Any]
    repetition: int
    seed: int
    task_index: int = 0
    match_duration: float | None = None


def parse_match_duration_curriculum(value: str | None) -> tuple[float, ...]:
    """Expand ``10x30s,10x1m,5x5m`` into per-match seconds."""
    if not value:
        return ()
    schedule: list[float] = []
    for raw_stage in value.split(","):
        stage = raw_stage.strip().lower()
        match = re.fullmatch(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*([sm]?)", stage)
        if not match:
            raise argparse.ArgumentTypeError(
                f"invalid curriculum stage {raw_stage!r}; use COUNTxDURATION, e.g. 10x30s,10x1m"
            )
        count = int(match.group(1))
        duration = float(match.group(2)) * (60.0 if match.group(3) == "m" else 1.0)
        if count < 1 or duration <= 0:
            raise argparse.ArgumentTypeError("curriculum counts and durations must be positive")
        schedule.extend([duration] * count)
    return tuple(schedule)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_remaining_seconds(elapsed: float, completed: int, total: int) -> float | None:
    """Estimate remaining wall time from observed parallel match throughput."""
    if completed <= 0 or total <= completed:
        return 0.0 if total <= completed else None
    return max(0.0, elapsed / completed * (total - completed))


def default_webots_executable() -> str:
    user_install = Path.home() / "Applications" / "Webots-R2025b.app" / "Contents" / "MacOS" / "webots"
    if user_install.exists():
        return str(user_install)
    return "/Applications/Webots.app/Contents/MacOS/webots"


def atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def remove_match_artifacts(matches_dir: str | Path, match_id: str) -> None:
    """Remove an incomplete attempt before reusing the match identifier."""
    directory = Path(matches_dir)
    for suffix in (".json", ".summary.json", ".live.jsonl", ".live.meta.json"):
        (directory / f"{match_id}{suffix}").unlink(missing_ok=True)


def run_match_with_retries(
    task: MatchTask,
    *,
    match_runner: Any,
    matches_dir: str | Path,
    max_retries: int,
) -> dict[str, Any]:
    """Retry a failed match under the same ID, replacing partial recordings."""
    attempts = max_retries + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        remove_match_artifacts(matches_dir, task.match_id)
        try:
            result = match_runner(task)
            result["attempt"] = attempt
            result["retries"] = attempt - 1
            return result
        except Exception as error:
            last_error = error
            remove_match_artifacts(matches_dir, task.match_id)
    raise RuntimeError(
        f"match {task.match_id} failed after {attempts} attempt(s): {last_error}"
    ) from last_error


FIELD_OBJECTS = ("ball", "ally", "enemy", "wall_bottom", "wall_top", "wall_left", "wall_right", "ally_goal", "enemy_goal")


def formula_block(limits: argparse.Namespace, rng: random.Random) -> dict[str, Any]:
    expression = random_expression(rng, min(limits.max_depth, 5), limits.max_constant)
    block = executable_formula_block(expression)
    if (
        block["depth"] > limits.max_depth
        or block["nodes"] > limits.max_nodes
        or block["constants"] > limits.max_constants
    ):
        block = executable_formula_block({"const": 1.0})
    # Independent x/y trees let evolution change direction, not only magnitude.
    block["components"] = {
        "x": copy.deepcopy(block),
        "y": executable_formula_block(random_expression(rng, min(limits.max_depth, 5), limits.max_constant)),
    }
    return block


def goal_post_escape_description() -> dict[str, Any]:
    """Serializable documentation for the fixed, non-evolved post recovery."""
    config = StrategyConfig()
    return {
        "priority": "absolute_before_goal_escape",
        "evolved": False,
        "activation": (
            f"distance_to_post <= {config.goal_post_escape_radius:.2f} m and "
            f"robot_speed <= {config.goal_post_stall_speed:.2f} m/s for "
            f">= {config.goal_post_stall_duration:.2f} s"
        ),
        "deactivation": f"distance_to_post >= {config.goal_post_release_radius:.2f} m",
        "maximum_stall_displacement_m": config.goal_post_stall_displacement,
        "vector": "reduced_speed * unit(target_inside_field_and_toward_goal_centre - robot_position)",
        "max_speed_m_s": config.goal_post_escape_speed,
        "tangent_gain": config.goal_post_tangent_gain,
    }


def attacking_corner_escape_description() -> dict[str, Any]:
    config = StrategyConfig()
    return {
        "priority": "absolute_after_goal_recovery",
        "evolved": False,
        "activation": "near side wall, outside goal mouth, and inside opponent final-zone pocket",
        "depth_m": config.attacking_corner_depth,
        "wall_band_m": config.attacking_corner_wall_band,
        "vector": "speed * unit(-0.45 * attack_direction + direction_to_centre)",
        "speed_m_s": config.attacking_corner_escape_speed,
    }


def corner_shot_gate_description() -> dict[str, Any]:
    config = StrategyConfig()
    return {
        "evolved": True,
        "activation": "ball ray reaches a corner band near the opponent goal",
        "goal_distance_m": config.corner_gate_goal_distance,
        "crossing_band_m": config.corner_gate_crossing_band,
        "same_side_player_angle_rad": config.corner_gate_player_angle,
        "wrong_side_vector": "-attack_direction * retreat_gain",
        "retreat_gain": config.corner_gate_retreat_gain,
        "mirrored_with_attack_sign": True,
    }


def default_formula(limits: argparse.Namespace, rng: random.Random) -> dict[str, Any]:
    if limits.field_strategy == "individual":
        fields = {
            f"ally_{robot_id}": {object_name: formula_block(limits, rng) for object_name in FIELD_OBJECTS}
            for robot_id in range(limits.robots_per_team)
        }
    else:
        fields = {"shared": {object_name: formula_block(limits, rng) for object_name in FIELD_OBJECTS}}
    formula = {
        "field_strategy": limits.field_strategy,
        "ball_field_model": "ifac2008_univector_rotated",
        "ball_field": {
            "source": "Lim et al., IFAC 2008, equations (2) and (4)",
            "shared_structure_by_all_players": True,
            "independent_parameters_by_player": True,
            "canonical_axis": "-unit(opponent_goal_center - ball_position)",
            "desired_kick_direction": "unit(opponent_goal_center - ball_position)",
            "parameters_by_player": ["ball_spiral_radius_i", "ball_spiral_smoothing_i", "approach_gain_i"],
        },
        "self_field": "excluded",
        "goal_escape_override": {
            "priority": "absolute",
            "activation": "abs(x) > field_length / 2 and abs(y) < goal_width / 2",
            "deactivation": "robot center returns inside field",
            "vector": "max_nominal_speed * unit(inside_field_mouth_centre - robot_position)",
        },
        "goal_post_escape_override": goal_post_escape_description(),
        "attacking_corner_escape_override": attacking_corner_escape_description(),
        "corner_shot_gate": corner_shot_gate_description(),
        "goal_triangle_clearance": {
            "priority": "before evolved tactical fields",
            "activation": "robot lies inside triangle(ball, opponent_left_post, opponent_right_post)",
            "vector": "nearest_lateral_exit + backward_component",
            "evolved_parameters": ["margin", "lateral_gain", "backward_gain"],
        },
        "fields": fields,
        "depth": max(block["depth"] for group in fields.values() for block in group.values()),
        "nodes": sum(block["nodes"] for group in fields.values() for block in group.values()),
        "constants": sum(block["constants"] for group in fields.values() for block in group.values()),
    }
    formula.pop("player_ball_fields", None)
    return formula


def random_candidate(index: int, generation: int, rng: random.Random, limits: argparse.Namespace) -> Candidate:
    base = asdict(StrategyConfig())
    genome = {key: bounded_gene(key, base[key] * rng.uniform(0.65, 1.35), limits.max_constant) for key in GENOME_KEYS}
    for player in range(3):
        genome[f"ball_return_power_{player}"] = rng.choice((1.0, 2.0))
    formula = default_formula(limits, rng)
    return Candidate(f"g{generation:04d}-c{index:04d}", generation, genome, formula)


def baseline_candidate(field_strategy: str = "shared", robots_per_team: int = 3) -> Candidate:
    """Fixed quality reference using the original deterministic strategy."""
    base = asdict(StrategyConfig())
    genome = {key: float(base[key]) for key in GENOME_KEYS}
    players = ("shared",) if field_strategy == "shared" else tuple(
        f"ally_{robot_id}" for robot_id in range(robots_per_team)
    )
    fields: dict[str, dict[str, Any]] = {}
    for player in players:
        fields[player] = {}
        for object_name in FIELD_OBJECTS:
            block = executable_formula_block({"const": 1.0})
            block["components"] = {
                "x": executable_formula_block({"const": 1.0}),
                "y": executable_formula_block({"const": 1.0}),
            }
            fields[player][object_name] = block
    formula = {
        "field_strategy": field_strategy,
        "ball_field_model": "ifac2008_univector_rotated",
        "ball_field": {
            "source": "Lim et al., IFAC 2008, equations (2) and (4)",
            "shared_structure_by_all_players": True,
            "independent_parameters_by_player": True,
            "canonical_axis": "-unit(opponent_goal_center - ball_position)",
            "desired_kick_direction": "unit(opponent_goal_center - ball_position)",
        },
        "self_field": "excluded",
        "fixed_baseline": True,
        "baseline_model": "original_deterministic",
        "source": "VSSS Coach original deterministic vector-field strategy",
        "goal_post_escape_override": goal_post_escape_description(),
        "attacking_corner_escape_override": attacking_corner_escape_description(),
        "corner_shot_gate": corner_shot_gate_description(),
        "goal_triangle_clearance": {
            "priority": "before evolved tactical fields",
            "activation": "robot lies inside triangle(ball, opponent_left_post, opponent_right_post)",
            "vector": "nearest_lateral_exit + backward_component",
            "evolved_parameters": ["margin", "lateral_gain", "backward_gain"],
        },
        "fields": fields,
        "depth": 1,
        "nodes": sum(len(group) for group in fields.values()),
        "constants": sum(len(group) for group in fields.values()),
    }
    formula.pop("player_ball_fields", None)
    return Candidate("baseline", -1, genome, formula)


def load_baseline_from_run(run_path: str | Path) -> Candidate:
    """Load the best archived candidate from another simulation as a baseline."""
    directory = Path(run_path)
    ranking_path = directory / "ranking.json"
    if ranking_path.exists():
        rows = json.loads(ranking_path.read_text(encoding="utf-8"))
    else:
        generation_files = sorted((directory / "generations").glob("generation-*.json"))
        if not generation_files:
            raise SystemExit(f"baseline source has no ranking: {directory}")
        rows = json.loads(generation_files[-1].read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"baseline source ranking is empty: {directory}")
    source = candidate_from_payload(rows[0])
    source.candidate_id = "baseline"
    source.generation = -1
    source.parents = (rows[0].get("candidate_id", "unknown"),)
    source.score = source.wins = source.draws = source.losses = 0
    source.goals_for = source.goals_against = 0
    source.formula = copy.deepcopy(source.formula)
    for player in range(3):
        source.genome.setdefault(f"ball_spiral_radius_{player}", source.genome.get("ball_spiral_radius", 0.11))
        source.genome.setdefault(f"ball_spiral_smoothing_{player}", source.genome.get("ball_spiral_smoothing", 0.08))
        source.genome.setdefault(f"approach_gain_{player}", source.genome.get("approach_gain", 1.0))
    source.formula["fixed_baseline"] = True
    source.formula["baseline_source_run"] = str(directory)
    source.formula.setdefault("goal_post_escape_override", goal_post_escape_description())
    source.formula.setdefault("attacking_corner_escape_override", attacking_corner_escape_description())
    source.formula.setdefault("corner_shot_gate", corner_shot_gate_description())
    remove_legacy_role_fields(source.formula)
    source.formula["ball_field_model"] = "ifac2008_univector_rotated"
    source.formula["ball_field"] = {
        "source": "Lim et al., IFAC 2008, equations (2) and (4)",
        "shared_structure_by_all_players": True,
        "independent_parameters_by_player": True,
        "canonical_axis": "-unit(opponent_goal_center - ball_position)",
        "desired_kick_direction": "unit(opponent_goal_center - ball_position)",
    }
    source.formula.pop("player_ball_fields", None)
    source.formula.pop("ball_player_specialization", None)
    return source


def baseline_variant(index: int, generation: int, base: Candidate, rng: random.Random, limits: argparse.Namespace) -> Candidate:
    """Create a candidate that varies only baseline scalar parameters."""
    candidate = Candidate(
        f"g{generation:04d}-c{index:04d}", generation,
        dict(base.genome), copy.deepcopy(base.formula), (base.candidate_id,),
    )
    mutate(candidate, "gaussian", 1.0, float(getattr(limits, "mutation_scale", 0.12)), limits, rng, preserve_formula=True)
    remove_legacy_role_fields(candidate.formula)
    candidate.formula.pop("player_ball_fields", None)
    candidate.formula.pop("ball_player_specialization", None)
    return candidate


def candidate_payload(candidate: Candidate) -> dict[str, Any]:
    return asdict(candidate)


def candidate_from_payload(payload: dict[str, Any]) -> Candidate:
    data = dict(payload)
    data.pop("rank", None)
    defaults = asdict(StrategyConfig())
    data["genome"] = {
        key: float(data.get("genome", {}).get(key, defaults[key])) for key in GENOME_KEYS
    }
    data["parents"] = tuple(data.get("parents", ()))
    formula = data.setdefault("formula", {})
    remove_legacy_role_fields(formula)
    fields = formula.get("fields", {})
    count = len(fields) if formula.get("field_strategy") == "individual" else 3
    formula["ball_field_model"] = "ifac2008_univector_rotated"
    formula.setdefault("ball_field", {
        "source": "Lim et al., IFAC 2008, equations (2) and (4)",
        "shared_by_all_players": True,
        "canonical_axis": "-unit(opponent_goal_center - ball_position)",
        "desired_kick_direction": "unit(opponent_goal_center - ball_position)",
    })
    formula.pop("player_ball_fields", None)
    formula.pop("ball_player_specialization", None)
    return Candidate(**data)


def completed_match(matches_dir: Path, match_id: str) -> dict[str, Any] | None:
    """Return a complete archived match, ignoring partial or corrupt output."""
    path = matches_dir / f"{match_id}.json"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    required = {"match_id", "home", "away", "home_goals", "away_goals", "frames"}
    if result.get("match_id") != match_id or not required.issubset(result):
        return None
    if result.get("backend") == "travesim" and not result.get("frames", [{}])[-1].get("finished"):
        return None
    return result


def mock_match(task: MatchTask) -> dict[str, Any]:
    """Deterministic, visually plausible match used for orchestration and UI."""
    rng = random.Random(task.seed)

    def strength(candidate: dict[str, Any]) -> float:
        genome = candidate["genome"]
        attack = 0.5 * genome["approach_gain"] + 0.8 * genome["shot_gain"] + 0.2 * genome["orbit_gain"]
        defense = 0.45 * genome["obstacle_radial_gain"] + 0.25 * genome["wall_gain"]
        complexity = 0.002 * candidate["formula"]["nodes"]
        return attack + defense - complexity

    home_strength = strength(task.home)
    away_strength = strength(task.away)
    home_goals = max(0, round(1.4 + 0.8 * (home_strength - away_strength) + rng.gauss(0.0, 1.0)))
    away_goals = max(0, round(1.4 + 0.8 * (away_strength - home_strength) + rng.gauss(0.0, 1.0)))

    def clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))

    duration = float(task.match_duration or 600.0)
    frames = []
    for step in range(90):
        phase = 2.0 * math.pi * step / 89.0
        # A smooth end-to-end exchange replaces the former figure-eight demo.
        # Each robot tracks the play while retaining a recognizable role.
        ball_x = 0.60 * math.sin(phase)
        ball_y = 0.18 * math.sin(phase + 0.55) + 0.05 * math.sin(3.0 * phase)
        yellow = [
            [-0.64, clamp(0.55 * ball_y, -0.18, 0.18)],
            [clamp(ball_x - 0.30, -0.50, 0.08), clamp(ball_y + 0.18, -0.48, 0.48)],
            [clamp(ball_x - 0.11, -0.48, 0.52), clamp(ball_y - 0.08, -0.50, 0.50)],
        ]
        blue = [
            [0.64, clamp(0.55 * ball_y, -0.18, 0.18)],
            [clamp(ball_x + 0.30, -0.08, 0.50), clamp(ball_y - 0.18, -0.48, 0.48)],
            [clamp(ball_x + 0.11, -0.52, 0.48), clamp(ball_y + 0.08, -0.50, 0.50)],
        ]
        frames.append({
            "step": step,
            "time_remaining": duration * (1.0 - step / 89.0),
            "goals_yellow": home_goals if step == 89 else round(home_goals * step / 89.0),
            "goals_blue": away_goals if step == 89 else round(away_goals * step / 89.0),
            "ball": [ball_x, ball_y],
            "yellow": yellow,
            "blue": blue,
        })
    return {
        "match_id": task.match_id,
        "generation": task.generation,
        "home": task.home["candidate_id"],
        "away": task.away["candidate_id"],
        "home_goals": home_goals,
        "away_goals": away_goals,
        "repetition": task.repetition,
        "seed": task.seed,
        "backend": "mock",
        "match_duration": duration,
        "frames": frames,
    }


def tasks_for_generation(candidates: list[Candidate], generation: int, repetitions: int, seed: int) -> list[MatchTask]:
    tasks: list[MatchTask] = []
    for pair_index, (home, away) in enumerate(itertools.combinations(candidates, 2)):
        for repetition in range(repetitions):
            match_id = f"g{generation:04d}-m{pair_index:06d}-r{repetition}"
            tasks.append(MatchTask(match_id, generation, candidate_payload(home), candidate_payload(away), repetition, seed + pair_index * 1009 + repetition, len(tasks)))
    return tasks


def apply_match_duration_curriculum(
    tasks: list[MatchTask], schedule: tuple[float, ...], global_offset: int, fallback: float,
) -> list[MatchTask]:
    """Attach durations deterministically; the final stage persists if games remain."""
    if not schedule:
        return [replace(task, match_duration=fallback) for task in tasks]
    return [
        replace(task, match_duration=schedule[min(global_offset + index, len(schedule) - 1)])
        for index, task in enumerate(tasks)
    ]


def challenge_tasks(current: Candidate, archived: list[Candidate], generation: int, repetitions: int, seed: int) -> list[MatchTask]:
    """Build optional matches between this generation's champion and archived champions."""
    tasks: list[MatchTask] = []
    for index, opponent in enumerate(archived):
        for repetition in range(repetitions):
            match_id = f"g{generation:04d}-challenge-g{opponent.generation:04d}-r{repetition}"
            tasks.append(MatchTask(match_id, generation, candidate_payload(current), candidate_payload(opponent), repetition, seed + index * 1009 + repetition, len(tasks)))
    return tasks


def apply_result(candidate: Candidate, own_goals: int, opponent_goals: int, draw_penalty: float) -> None:
    candidate.goals_for += own_goals
    candidate.goals_against += opponent_goals
    candidate.score += own_goals - opponent_goals
    if own_goals > opponent_goals:
        candidate.wins += 1
        candidate.score += 3.0
    elif own_goals < opponent_goals:
        candidate.losses += 1
        candidate.score -= 3.0
    else:
        candidate.draws += 1
        candidate.score -= draw_penalty


def behavior_metrics(
    frames: list[dict[str, Any]], team: str, *, stationary_speed: float = 0.02,
    stationary_grace: float = 2.0, stuck_window: float = 3.0, stuck_radius: float = 0.03,
) -> dict[str, float]:
    """Measure robot-seconds of undesirable behavior from physical telemetry."""
    histories: list[deque[tuple[float, float, float]]] = []
    stationary_since: list[float | None] = []
    totals = {"stationary_seconds": 0.0, "stuck_seconds": 0.0, "goal_seconds": 0.0}
    previous_time: float | None = None
    for frame in frames:
        now = float(frame.get("time", 0.0))
        dt = 0.0 if previous_time is None else max(0.0, min(0.25, now - previous_time))
        previous_time = now
        robots = frame.get(team, [])
        while len(histories) < len(robots):
            histories.append(deque())
            stationary_since.append(None)
        for index, robot in enumerate(robots):
            if not isinstance(robot, dict):
                continue
            x, y = float(robot.get("x", 0.0)), float(robot.get("y", 0.0))
            speed = math.hypot(float(robot.get("vx", 0.0)), float(robot.get("vy", 0.0)))
            if speed < stationary_speed:
                stationary_since[index] = now if stationary_since[index] is None else stationary_since[index]
                if now - stationary_since[index] >= stationary_grace:
                    totals["stationary_seconds"] += dt
            else:
                stationary_since[index] = None
            history = histories[index]
            history.append((now, x, y))
            while len(history) > 1 and now - history[0][0] > stuck_window:
                history.popleft()
            if history and now - history[0][0] >= stuck_window * 0.9:
                if math.hypot(x - history[0][1], y - history[0][2]) < stuck_radius and speed >= stationary_speed:
                    totals["stuck_seconds"] += dt
            if abs(x) > ROBOCORE_VSSS_2025.field_length / 2 and abs(y) < ROBOCORE_VSSS_2025.goal_width / 2:
                totals["goal_seconds"] += dt
    return totals


def apply_behavior_penalty(candidate: Candidate, result: dict[str, Any], team: str, args: argparse.Namespace) -> None:
    if result.get("backend") != "travesim":
        return
    behavior = result.get("behavior", {})
    metrics = behavior.get(team) if isinstance(behavior, dict) else None
    if not isinstance(metrics, dict):
        metrics = behavior_metrics(
            result.get("frames", []), team,
            stationary_speed=args.stationary_speed,
            stationary_grace=args.stationary_grace,
            stuck_window=args.stuck_window,
            stuck_radius=args.stuck_radius,
        )
    penalty = float(metrics.get("penalty", (
        metrics["stationary_seconds"] * args.stationary_penalty
        + metrics["stuck_seconds"] * args.stuck_penalty
        + metrics["goal_seconds"] * args.goal_penalty
    )))
    candidate.stationary_seconds += metrics["stationary_seconds"]
    candidate.stuck_seconds += metrics["stuck_seconds"]
    candidate.goal_seconds += metrics["goal_seconds"]
    candidate.behavior_penalty += penalty
    candidate.score -= penalty


def annotate_behavior(result: dict[str, Any], args: argparse.Namespace) -> None:
    """Calculate physical penalties once so live ranking can use compact metadata."""
    if result.get("backend") != "travesim" or isinstance(result.get("behavior"), dict):
        return
    result["behavior"] = {}
    for team in ("yellow", "blue"):
        metrics = behavior_metrics(
            result.get("frames", []), team,
            stationary_speed=args.stationary_speed,
            stationary_grace=args.stationary_grace,
            stuck_window=args.stuck_window,
            stuck_radius=args.stuck_radius,
        )
        metrics["penalty"] = (
            metrics["stationary_seconds"] * args.stationary_penalty
            + metrics["stuck_seconds"] * args.stuck_penalty
            + metrics["goal_seconds"] * args.goal_penalty
        )
        result["behavior"][team] = metrics


def match_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Return match metadata without the large replay frame array."""
    frames = result.get("frames", [])
    return {
        **{key: value for key, value in result.items() if key != "frames"},
        "frame_count": len(frames) if isinstance(frames, list) else 0,
        "finished": bool(frames and frames[-1].get("finished")) if isinstance(frames, list) else False,
    }


def evaluate(candidates: list[Candidate], results: Iterable[dict[str, Any]], draw_penalty: float, args: argparse.Namespace | None = None) -> list[Candidate]:
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for candidate in candidates:
        candidate.reset_score()
    for result in results:
        home = by_id[result["home"]]
        away = by_id[result["away"]]
        apply_result(home, result["home_goals"], result["away_goals"], draw_penalty)
        apply_result(away, result["away_goals"], result["home_goals"], draw_penalty)
    return sorted(candidates, key=lambda candidate: (candidate.score, candidate.wins, candidate.goals_for - candidate.goals_against, -candidate.draws), reverse=True)


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return float(value.get("x", 0.0)), float(value.get("y", 0.0))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    return 0.0, 0.0


def player_contributions(results: Iterable[dict[str, Any]]) -> dict[tuple[str, int], dict[str, float]]:
    """Attribute goals to the last touching ally and saves to near-goal reversals."""
    totals: dict[tuple[str, int], dict[str, float]] = {}
    touch_radius = ROBOCORE_VSSS_2025.robot_max_side / math.sqrt(2.0) + ROBOCORE_VSSS_2025.ball_radius + 0.015
    for result in results:
        frames = result.get("frames", [])
        if not isinstance(frames, list) or len(frames) < 2:
            continue
        previous = frames[0]
        previous_ball = _point(previous.get("ball"))
        previous_scores = (int(previous.get("goals_yellow", 0)), int(previous.get("goals_blue", 0)))
        previous_velocity: tuple[float, float] | None = None
        last_touch: tuple[str, int] | None = None
        for frame in frames[1:]:
            contacts: list[tuple[float, str, int]] = []
            for color in ("yellow", "blue"):
                team_id = result["home"] if color == "yellow" else result["away"]
                for index, robot in enumerate(previous.get(color, [])):
                    position = _point(robot)
                    distance = math.hypot(position[0] - previous_ball[0], position[1] - previous_ball[1])
                    if distance <= touch_radius:
                        contacts.append((distance, team_id, index))
            if contacts:
                _, touching_team, touching_player = min(contacts)
                last_touch = (touching_team, touching_player)
            ball = _point(frame.get("ball"))
            dt = max(1e-6, float(frame.get("time", 0.0)) - float(previous.get("time", 0.0)))
            velocity = ((ball[0] - previous_ball[0]) / dt, (ball[1] - previous_ball[1]) / dt)
            period = max(1, int(frame.get("period", 1)))
            scores = (int(frame.get("goals_yellow", 0)), int(frame.get("goals_blue", 0)))
            for color_index, color in enumerate(("yellow", "blue")):
                team_id = result["home"] if color == "yellow" else result["away"]
                robots = previous.get(color, [])
                if not robots:
                    continue
                nearest = min(range(len(robots)), key=lambda index: math.hypot(_point(robots[index])[0] - previous_ball[0], _point(robots[index])[1] - previous_ball[1]))
                row = totals.setdefault((team_id, nearest), {"goals": 0.0, "defenses": 0.0})
                scoring_counter = color_index if period % 2 == 1 else 1 - color_index
                scored = scores[scoring_counter] - previous_scores[scoring_counter]
                if scored > 0 and last_touch is not None and last_touch[0] == team_id:
                    scorer = totals.setdefault(last_touch, {"goals": 0.0, "defenses": 0.0})
                    scorer["goals"] += scored
                    last_touch = None
                attack_sign = (-1.0 if color == "yellow" else 1.0) * (1.0 if period % 2 == 1 else -1.0)
                own_goal_x = -attack_sign * ROBOCORE_VSSS_2025.field_length / 2.0
                if previous_velocity is not None:
                    def enters_own_goal(position: tuple[float, float], vector: tuple[float, float]) -> bool:
                        if vector[0] * attack_sign >= -0.02:
                            return False
                        crossing_time = (own_goal_x - position[0]) / vector[0]
                        crossing_y = position[1] + vector[1] * crossing_time
                        return crossing_time > 0.0 and abs(crossing_y) <= ROBOCORE_VSSS_2025.goal_width / 2.0

                    allied_contacts = [contact for contact in contacts if contact[1] == team_id]
                    if allied_contacts:
                        _, _, defender = min(allied_contacts)
                        if enters_own_goal(previous_ball, previous_velocity) and not enters_own_goal(ball, velocity):
                            defense_row = totals.setdefault((team_id, defender), {"goals": 0.0, "defenses": 0.0})
                            defense_row["defenses"] += 1.0
            previous, previous_ball, previous_scores, previous_velocity = frame, ball, scores, velocity
    return totals


def _player_block(candidate: Candidate, player: int) -> dict[str, Any]:
    ensure_player_ball_fields(candidate.formula, 3)
    return candidate.formula["player_ball_fields"][player]


def specialize_ball_players(
    population: list[Candidate], ranking: list[Candidate], results: Iterable[dict[str, Any]], rng: random.Random,
) -> None:
    """Breed goalkeeper, scorer and assistant ball fields from role evidence."""
    contributions = player_contributions(results)
    all_players = [(candidate, player) for candidate in ranking for player in range(3)]
    goalkeepers = sorted(all_players, key=lambda item: contributions.get((item[0].candidate_id, item[1]), {}).get("defenses", 0.0), reverse=True)
    attackers = sorted(all_players, key=lambda item: contributions.get((item[0].candidate_id, item[1]), {}).get("goals", 0.0), reverse=True)
    assistants = [(ranking[0], player) for player in range(3)]
    pools = (goalkeepers, attackers, assistants)
    labels = ("goalkeeper", "attacker", "assistant")
    for child in population:
        lineage = []
        ensure_player_ball_fields(child.formula, 3)
        for target, (pool, label) in enumerate(zip(pools, labels)):
            first = pool[0] if pool else (ranking[0], target)
            second = pool[1] if len(pool) > 1 else first
            if label == "assistant":
                first, second = rng.sample(assistants, 2)
            parent_a, source_a = first
            parent_b, source_b = second
            block_a, block_b = _player_block(parent_a, source_a), _player_block(parent_b, source_b)
            crossed = copy.deepcopy(block_a)
            if isinstance(block_a.get("components"), dict) and isinstance(block_b.get("components"), dict):
                crossed["components"]["y"] = copy.deepcopy(block_b["components"].get("y", block_a["components"].get("y")))
            elif rng.random() < 0.5:
                crossed = copy.deepcopy(block_b)
            child.formula["player_ball_fields"][target] = crossed
            gain_a = parent_a.genome[f"ball_return_gain_{source_a}"]
            gain_b = parent_b.genome[f"ball_return_gain_{source_b}"]
            child.genome[f"ball_return_gain_{target}"] = bounded_gene(f"ball_return_gain_{target}", (gain_a + gain_b) / 2.0, 10.0)
            child.genome[f"ball_return_power_{target}"] = rng.choice((
                parent_a.genome[f"ball_return_power_{source_a}"], parent_b.genome[f"ball_return_power_{source_b}"],
            ))
            lineage.append({
                "role": label, "player": target + 1,
                "parents": [f"{parent_a.candidate_id}:player-{source_a + 1}", f"{parent_b.candidate_id}:player-{source_b + 1}"],
                "selection_metric": "defenses" if label == "goalkeeper" else "goals" if label == "attacker" else "best_team",
                "parent_performance": [
                    contributions.get((parent_a.candidate_id, source_a), {"goals": 0.0, "defenses": 0.0}),
                    contributions.get((parent_b.candidate_id, source_b), {"goals": 0.0, "defenses": 0.0}),
                ],
            })
        child.formula["ball_player_specialization"] = lineage


def choose_parent(ranking: list[Candidate], strategy: str, rng: random.Random) -> Candidate:
    if strategy == "rank":
        weights = list(range(len(ranking), 0, -1))
        return rng.choices(ranking, weights=weights, k=1)[0]
    if strategy == "tournament":
        return max(rng.sample(ranking, k=min(3, len(ranking))), key=lambda item: item.score)
    return rng.choice(ranking[: max(2, len(ranking) // 2)])


def breed(a: Candidate, b: Candidate, strategy: str, child_id: str, generation: int, rng: random.Random) -> Candidate:
    if strategy == "blend":
        alpha = rng.random()
        genome = {key: alpha * a.genome[key] + (1.0 - alpha) * b.genome[key] for key in GENOME_KEYS}
    elif strategy == "uniform":
        genome = {key: (a.genome[key] if rng.random() < 0.5 else b.genome[key]) for key in GENOME_KEYS}
    else:  # module
        split = rng.randint(1, len(GENOME_KEYS) - 1)
        genome = {key: (a.genome[key] if index < split else b.genome[key]) for index, key in enumerate(GENOME_KEYS)}
    genome = {key: bounded_gene(key, value, 10.0) for key, value in genome.items()}
    formula = copy.deepcopy(a.formula if rng.random() < 0.5 else b.formula)
    return Candidate(child_id, generation, genome, formula, (a.candidate_id, b.candidate_id))


def mutate(candidate: Candidate, strategy: str, rate: float, scale: float, limits: argparse.Namespace, rng: random.Random, preserve_formula: bool = False) -> None:
    for key in GENOME_KEYS:
        if rng.random() >= rate:
            continue
        if key.startswith("ball_return_power_"):
            candidate.genome[key] = rng.choice((1.0, 2.0))
        elif strategy == "reset":
            candidate.genome[key] = rng.uniform(-limits.max_constant, limits.max_constant)
        elif strategy == "point":
            candidate.genome[key] += rng.choice((-1.0, 1.0)) * scale
        else:
            candidate.genome[key] += rng.gauss(0.0, scale)
        candidate.genome[key] = bounded_gene(key, candidate.genome[key], limits.max_constant)
    if not preserve_formula and strategy in {"subtree", "mixed"} and rng.random() < rate:
        groups = list(candidate.formula["fields"].values())
        block = rng.choice(list(rng.choice(groups).values()))
        replacement = formula_block(limits, rng)
        block.clear()
        block.update(replacement)
        candidate.formula["depth"] = max(item["depth"] for group in groups for item in group.values())
        candidate.formula["nodes"] = sum(item["nodes"] for group in groups for item in group.values())


def survive(ranking: list[Candidate], count: int, strategy: str, elite_count: int, rng: random.Random) -> list[Candidate]:
    elite_count = min(elite_count, count, len(ranking)) if strategy == "elitism" else 0
    survivors = ranking[:elite_count]
    pool = ranking[elite_count:]
    while len(survivors) < count and pool:
        selected = choose_parent(pool, "rank" if strategy == "rank" else "tournament", rng)
        survivors.append(selected)
        pool.remove(selected)
    return survivors


def next_generation(ranking: list[Candidate], args: argparse.Namespace, generation: int, rng: random.Random, results: Iterable[dict[str, Any]] = ()) -> list[Candidate]:
    results = list(results)
    survivor_count = max(2, round(args.competitors * args.survival_fraction))
    survivors = survive(ranking, survivor_count, args.survival, args.elite_count, rng)
    next_population: list[Candidate] = []
    elite_count = min(args.elite_count, len(survivors)) if args.survival == "elitism" else 0
    for index, survivor in enumerate(survivors[:elite_count]):
        clone = Candidate(f"g{generation:04d}-c{index:04d}", generation, dict(survivor.genome), copy.deepcopy(survivor.formula), (survivor.candidate_id,))
        next_population.append(clone)
    while len(next_population) < args.competitors:
        parent_a = choose_parent(survivors, args.parent_selection, rng)
        parent_b = choose_parent(survivors, args.parent_selection, rng)
        child = breed(parent_a, parent_b, args.breeding, f"g{generation:04d}-c{len(next_population):04d}", generation, rng)
        mutate(child, args.mutation, args.mutation_rate, args.mutation_scale, args, rng, preserve_formula=args.simulation_mode == "baseline-variants")
        next_population.append(child)
    for child in next_population:
        child.formula["ball_field_model"] = "ifac2008_univector_rotated"
        child.formula.pop("player_ball_fields", None)
        child.formula.pop("ball_player_specialization", None)
        child.formula.pop("stationary_ball_mutation", None)
    return next_population


def ranking_payload(ranking: list[Candidate]) -> list[dict[str, Any]]:
    return [{"rank": index + 1, **candidate_payload(candidate)} for index, candidate in enumerate(ranking)]


def run_distributed_matches(tasks: list[MatchTask], config: TraveSimConfig, args: argparse.Namespace, run_dir: Path) -> Iterable[dict[str, Any]]:
    from .distributed import request_json
    token = args.coordinator_token or os.environ.get("VSSS_COACH_ADMIN_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("distributed execution requires --coordinator-token or VSSS_COACH_ADMIN_TOKEN")
    jobs = [{"id": task.match_id, "generation": task.generation, "payload": {"task": asdict(task), "config": asdict(config)}} for task in tasks]
    request_json(args.coordinator_url + "/api/v1/jobs", token, "POST", {"jobs": jobs}, timeout=120)
    delivered: set[str] = set()
    while len(delivered) < len(tasks):
        state = request_json(args.coordinator_url + "/api/v1/results", token, "POST", {
            "generation": tasks[0].generation, "exclude": sorted(delivered),
        })
        coordinator_status = request_json(args.coordinator_url + "/api/v1/status", token)
        atomic_json(run_dir / "distributed.json", {**coordinator_status, "generation": tasks[0].generation, "updated_at": utc_now()})
        failed = [job for job in state["jobs"] if job["status"] == "failed"]
        if failed:
            raise RuntimeError(f"distributed matches failed: {failed[0]['id']}: {failed[0]['error']}")
        for job in state["jobs"]:
            if job["status"] == "complete" and job["id"] not in delivered:
                delivered.add(job["id"])
                yield job["result"]
        if len(delivered) < len(tasks):
            time.sleep(args.coordinator_poll_interval)


def run_evolution(args: argparse.Namespace) -> Path:
    duration_schedule = parse_match_duration_curriculum(getattr(args, "match_duration_curriculum", None))
    args.match_duration_schedule_seconds = list(duration_schedule)
    resume_run = getattr(args, "resume_run", None)
    run_dir = Path(resume_run) if resume_run else Path(args.output) / (args.run_name or datetime.now().strftime("run-%Y%m%d-%H%M%S"))
    matches_dir = run_dir / "matches"
    generations_dir = run_dir / "generations"
    if resume_run:
        if not matches_dir.is_dir() or not generations_dir.is_dir():
            raise SystemExit(f"invalid run directory for resume: {run_dir}")
    else:
        matches_dir.mkdir(parents=True, exist_ok=False)
        generations_dir.mkdir()
    rules = get_ruleset(args.ruleset)
    if args.backend == "travesim" and not duration_schedule and not math.isclose(args.match_duration, rules.regulation_duration):
        raise SystemExit(
            f"TraveSim matches must follow {rules.name}: "
            f"{rules.periods} x {rules.period_duration:.0f} s = {rules.regulation_duration:.0f} s"
        )
    run_config = {**vars(args), "competition_rules": rules.to_dict()}
    run_config.pop("coordinator_token", None)
    if resume_run:
        run_config["resumed_at"] = utc_now()
    atomic_json(run_dir / "config.json", run_config)
    tracker = WandbTracker(args, run_config)
    if tracker.run is not None:
        atomic_json(run_dir / "wandb.json", {"run_id": tracker.run.id, "updated_at": utc_now()})
    travesim_config: TraveSimConfig | None = None
    if args.backend == "travesim":
        root = Path(args.travesim_root).resolve()
        travesim_config = TraveSimConfig(
            project_root=str(root),
            webots=args.webots_executable,
            webots_mode=args.webots_mode,
            match_duration=args.match_duration,
            timeout=args.match_timeout,
            port_base=args.travesim_port_base,
            ruleset=args.ruleset,
            field_strategy=args.field_strategy,
            formation=args.formation,
            position_sigma=args.placement_position_sigma,
            position_limit=args.placement_position_limit,
            angle_sigma_degrees=args.placement_angle_sigma_degrees,
            angle_limit_degrees=args.placement_angle_limit_degrees,
            client_sync_delay_ms=args.external_client_delay_ms,
            telemetry_start_timeout=args.telemetry_start_timeout,
            telemetry_stall_timeout=args.telemetry_stall_timeout,
            live_matches_dir=str(matches_dir.resolve()),
        )
        match_runner = partial(run_travesim_match, config=travesim_config)
    else:
        match_runner = mock_match
    resilient_match_runner = partial(
        run_match_with_retries,
        match_runner=match_runner,
        matches_dir=str(matches_dir),
        max_retries=args.match_retries,
    )
    rng = random.Random(args.seed)
    start_generation = 0
    if resume_run:
        live = json.loads((run_dir / "live.json").read_text(encoding="utf-8"))
        if live.get("status") == "complete":
            tracker.finish(run_dir, args.wandb_log_matches)
            return run_dir
        start_generation = int(live.get("generation", 0))
        population = [candidate_from_payload(item) for item in json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))]
        baseline = candidate_from_payload(json.loads((run_dir / "baseline.json").read_text(encoding="utf-8")))
        if live.get("status") == "generation_complete" and start_generation + 1 < args.generations:
            completed_ranking = [candidate_from_payload(item) for item in json.loads((generations_dir / f"generation-{start_generation:04d}.json").read_text(encoding="utf-8"))]
            completed_results = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(matches_dir.glob(f"g{start_generation:04d}-m*-r*.json"))
                if ".summary." not in path.name
            ]
            start_generation += 1
            population = next_generation(completed_ranking, args, start_generation, rng, completed_results)
    else:
        baseline = load_baseline_from_run(args.baseline_from) if args.baseline_from else baseline_candidate(args.field_strategy, args.robots_per_team)
        if args.simulation_mode == "baseline-variants":
            population = [baseline_variant(index, 0, baseline, rng, args) for index in range(args.competitors)]
        else:
            population = [random_candidate(index, 0, rng, args) for index in range(args.competitors)]
        atomic_json(run_dir / "baseline.json", candidate_payload(baseline))
        atomic_json(run_dir / "candidates.json", [candidate_payload(candidate) for candidate in population])
    best_history: list[float] = []
    archived_champions: list[Candidate] = []
    for archived_generation in range(start_generation):
        generation_file = generations_dir / f"generation-{archived_generation:04d}.json"
        if generation_file.exists():
            previous = [candidate_from_payload(item) for item in json.loads(generation_file.read_text(encoding="utf-8"))]
            if previous:
                archived_champions.append(copy.deepcopy(previous[0]))
                best_history.append(previous[0].score)
    stopped_reason = "generation_limit"
    run_started_at = time.monotonic()

    for generation in range(start_generation, args.generations):
        atomic_json(run_dir / "candidates.json", [candidate_payload(candidate) for candidate in population])
        tasks = tasks_for_generation(population, generation, args.matches_per_pair, args.seed + generation * 100_003)
        tasks = apply_match_duration_curriculum(
            tasks, duration_schedule, generation * len(tasks), args.match_duration,
        )
        generation_started_at = time.monotonic()
        planned_total_matches = args.generations * len(tasks)
        completed_before_generation = generation * len(tasks)
        elapsed_before_generation = time.monotonic() - run_started_at
        prior_seconds_per_match = elapsed_before_generation / completed_before_generation if completed_before_generation else None
        existing_results = [completed_match(matches_dir, task.match_id) for task in tasks]
        results = [result for result in existing_results if result is not None]
        completed_ids = {result["match_id"] for result in results}
        pending_tasks = [task for task in tasks if task.match_id not in completed_ids]
        atomic_json(run_dir / "live.json", {
            "status": "running", "generation": generation, "completed_matches": len(results),
            "total_matches": len(tasks), "completed_matches_overall": completed_before_generation + len(results),
            "planned_matches_overall": planned_total_matches,
            "generation_eta_seconds": prior_seconds_per_match * len(pending_tasks) if prior_seconds_per_match is not None else None,
            "run_eta_seconds": estimate_remaining_seconds(elapsed_before_generation, completed_before_generation, planned_total_matches),
            "updated_at": utc_now(),
        })
        if args.execution == "distributed":
            if args.backend != "travesim" or travesim_config is None:
                raise SystemExit("distributed execution currently requires --backend travesim")
            result_stream = run_distributed_matches(pending_tasks, travesim_config, args, run_dir)
            executor_context = None
        else:
            try:
                executor_context = ProcessPoolExecutor(max_workers=args.workers)
            except (OSError, PermissionError):
                executor_context = ThreadPoolExecutor(max_workers=args.workers)
            result_stream = executor_context.map(resilient_match_runner, pending_tasks, chunksize=1)
        try:
            for completed, result in enumerate(result_stream, start=len(results) + 1):
                results.append(result)
                atomic_json(matches_dir / f"{result['match_id']}.json", result)
                atomic_json(matches_dir / f"{result['match_id']}.summary.json", match_summary(result))
                (matches_dir / f"{result['match_id']}.live.jsonl").unlink(missing_ok=True)
                (matches_dir / f"{result['match_id']}.live.meta.json").unlink(missing_ok=True)
                if completed % max(1, args.dashboard_update_every) == 0 or completed == len(tasks):
                    now = time.monotonic()
                    overall_completed = generation * len(tasks) + completed
                    atomic_json(run_dir / "live.json", {
                        "status": "running",
                        "generation": generation,
                        "completed_matches": completed,
                        "total_matches": len(tasks),
                        "completed_matches_overall": overall_completed,
                        "planned_matches_overall": planned_total_matches,
                        "generation_eta_seconds": estimate_remaining_seconds(now - generation_started_at, completed, len(tasks)),
                        "run_eta_seconds": estimate_remaining_seconds(now - run_started_at, overall_completed, planned_total_matches),
                        "last_match": result["match_id"],
                        "updated_at": utc_now(),
                    })
        finally:
            if executor_context is not None:
                executor_context.shutdown(wait=True, cancel_futures=True)

        ranking = evaluate(population, results, args.draw_penalty, args)
        payload = ranking_payload(ranking)
        atomic_json(generations_dir / f"generation-{generation:04d}.json", payload)
        formulas_payload = {
            candidate.candidate_id: candidate.formula for candidate in ranking
        }
        atomic_json(generations_dir / f"formulas-{generation:04d}.json", formulas_payload)
        atomic_json(run_dir / "formulas.json", formulas_payload)
        atomic_json(run_dir / "ranking.json", payload)
        atomic_json(run_dir / "candidates.json", [candidate_payload(candidate) for candidate in population])
        best_history.append(ranking[0].score)
        tracker.log_generation(generation, ranking, len(tasks), run_dir)
        baseline_path = run_dir / "baseline-evaluations.json"
        existing_baseline_rows: list[dict[str, Any]] = []
        if baseline_path.exists():
            existing_baseline_rows = json.loads(baseline_path.read_text(encoding="utf-8"))
            existing_baseline_rows = [row for row in existing_baseline_rows if row.get("generation") != generation]
        generation_baseline_rows: list[dict[str, Any]] = []
        for repetition in range(args.baseline_matches):
            task = MatchTask(
                f"g{generation:04d}-baseline-r{repetition}", generation,
                candidate_payload(ranking[0]), candidate_payload(baseline), repetition,
                args.seed + generation * 300_007 + repetition, repetition,
                tasks[0].match_duration if tasks else args.match_duration,
            )
            result = completed_match(matches_dir, task.match_id) or resilient_match_runner(task)
            atomic_json(matches_dir / f"{result['match_id']}.json", result)
            atomic_json(matches_dir / f"{result['match_id']}.summary.json", match_summary(result))
            (matches_dir / f"{result['match_id']}.live.jsonl").unlink(missing_ok=True)
            (matches_dir / f"{result['match_id']}.live.meta.json").unlink(missing_ok=True)
            generation_baseline_rows.append({
                "generation": generation,
                "champion": ranking[0].candidate_id,
                "baseline": baseline.candidate_id,
                "repetition": repetition,
                "champion_goals": int(result["home_goals"]),
                "baseline_goals": int(result["away_goals"]),
                "goal_difference": int(result["home_goals"]) - int(result["away_goals"]),
                "match_id": result["match_id"],
            })
        atomic_json(baseline_path, [*existing_baseline_rows, *generation_baseline_rows])
        tracker.log_baseline(generation, generation_baseline_rows)
        if args.archive_challenges and archived_champions:
            challenge_rows = []
            for task in challenge_tasks(ranking[0], archived_champions, generation, args.challenge_matches, args.seed + generation * 200_003):
                result = completed_match(matches_dir, task.match_id) or resilient_match_runner(task)
                challenge_rows.append({
                    "generation": generation, "current": ranking[0].candidate_id,
                    "archived_generation": next(item.generation for item in archived_champions if item.candidate_id == task.away["candidate_id"]),
                    "archived": task.away["candidate_id"], "goals_for": result["goals_blue"],
                    "goals_against": result["goals_yellow"], "goal_difference": result["goals_blue"] - result["goals_yellow"],
                })
            atomic_json(run_dir / "challenges.json", challenge_rows)
            tracker.log_challenges(generation, challenge_rows)
        archived_champions.append(copy.deepcopy(ranking[0]))
        completed_overall = (generation + 1) * len(tasks)
        atomic_json(run_dir / "live.json", {"status": "generation_complete", "generation": generation, "completed_matches": len(tasks), "total_matches": len(tasks), "completed_matches_overall": completed_overall, "planned_matches_overall": planned_total_matches, "generation_eta_seconds": 0.0, "run_eta_seconds": estimate_remaining_seconds(time.monotonic() - run_started_at, completed_overall, planned_total_matches), "best_candidate": ranking[0].candidate_id, "best_score": ranking[0].score, "updated_at": utc_now()})

        if args.early_stopping != "none" and len(best_history) > args.patience:
            reference = max(best_history[: -args.patience])
            recent = max(best_history[-args.patience :])
            if recent - reference < args.min_delta:
                stopped_reason = f"early_stopping:{args.early_stopping}"
                break
        if generation + 1 < args.generations:
            population = next_generation(ranking, args, generation + 1, rng, results)

    atomic_json(run_dir / "live.json", {"status": "complete", "generation": generation, "generation_eta_seconds": 0.0, "run_eta_seconds": 0.0, "best_candidate": ranking[0].candidate_id, "best_score": ranking[0].score, "stopped_reason": stopped_reason, "updated_at": utc_now()})
    tracker.finish(run_dir, args.wandb_log_matches)
    return run_dir


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ruleset",
        choices=tuple(RULESETS),
        default=ROBOCORE_VSSS_2025.name,
        help="physical and match rules recorded with the experiment",
    )
    parser.add_argument("--competitors", type=int, default=16)
    parser.add_argument("--matches-per-pair", type=int, default=5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--execution", choices=("local", "distributed"), default="local")
    parser.add_argument("--coordinator-url", default="http://127.0.0.1:8090")
    parser.add_argument("--coordinator-token")
    parser.add_argument("--coordinator-poll-interval", type=float, default=3.0)
    parser.add_argument("--generations", type=int, default=30)
    parser.add_argument(
        "--simulation-mode", choices=("evolution", "baseline-variants"), default="evolution",
        help="baseline-variants freezes field formulas and evolves only scalar genome parameters",
    )
    parser.add_argument("--baseline-from", help="run directory whose best candidate becomes the fixed baseline")
    parser.add_argument("--archive-challenges", action="store_true", help="evaluate each champion against champions from previous generations")
    parser.add_argument("--challenge-matches", type=int, default=1, help="repetitions per archived champion challenge")
    parser.add_argument("--baseline-matches", type=int, default=1, help="matches between each generation champion and the fixed baseline")
    parser.add_argument("--breeding", choices=("module", "uniform", "blend"), default="module")
    parser.add_argument("--parent-selection", choices=("tournament", "rank", "top-half"), default="tournament")
    parser.add_argument("--survival", choices=("elitism", "tournament", "rank"), default="elitism")
    parser.add_argument("--elite-count", type=int, default=2)
    parser.add_argument("--survival-fraction", type=float, default=0.5)
    parser.add_argument("--mutation", choices=("gaussian", "point", "reset", "subtree", "mixed"), default="mixed")
    parser.add_argument("--mutation-rate", type=float, default=0.15)
    parser.add_argument("--mutation-scale", type=float, default=0.12)
    parser.add_argument("--early-stopping", choices=("none", "patience", "plateau"), default="patience")
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--min-delta", type=float, default=0.5)
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--max-nodes", type=int, default=127)
    parser.add_argument("--max-constants", type=int, default=16)
    parser.add_argument("--max-constant", type=float, default=10.0)
    parser.add_argument("--max-evaluation-ms", type=float, default=2.0)
    parser.add_argument("--draw-penalty", type=float, default=1.0)
    parser.add_argument(
        "--field-strategy",
        choices=("individual", "shared"),
        default="shared",
        help="individual learns one field set per ally; shared reuses one set for every ally",
    )
    parser.add_argument("--robots-per-team", type=int, choices=(3, 5), default=3)
    parser.add_argument(
        "--formation",
        choices=("random", *FORMATION_NAMES),
        default="random",
        help="initial/reset formation; random samples the catalogue at every placement",
    )
    parser.add_argument("--placement-position-sigma", type=float, default=0.012)
    parser.add_argument("--placement-position-limit", type=float, default=0.025)
    parser.add_argument("--placement-angle-sigma-degrees", type=float, default=3.0)
    parser.add_argument("--placement-angle-limit-degrees", type=float, default=7.0)
    parser.add_argument("--backend", choices=("mock", "travesim"), default="mock")
    parser.add_argument("--travesim-root", default=".")
    parser.add_argument("--webots-executable", default=default_webots_executable())
    parser.add_argument(
        "--webots-mode",
        choices=("realtime", "fast"),
        default="fast",
        help="fast uses a per-step client sync delay; realtime follows wall-clock simulation",
    )
    parser.add_argument("--external-client-delay-ms", type=int, default=2)
    parser.add_argument("--travesim-port-base", type=int, default=21000)
    parser.add_argument("--match-duration", type=float, default=ROBOCORE_VSSS_2025.regulation_duration)
    parser.add_argument(
        "--match-duration-curriculum",
        help=("per-match training curriculum, e.g. 10x30s,10x1m,5x5m; "
              "after the listed matches the final duration remains active"),
    )
    parser.add_argument(
        "--ball-stall-speed", type=float, default=0.02,
        help="dashboard stationary-ball speed threshold in m/s",
    )
    parser.add_argument(
        "--ball-stall-duration", type=float, default=5.0,
        help="continuous stationary-ball seconds before highlighting a match in red",
    )
    parser.add_argument("--match-timeout", type=float, default=1800.0)
    parser.add_argument("--telemetry-start-timeout", type=float, default=60.0, help="retry when Webots produces no first frame")
    parser.add_argument("--telemetry-stall-timeout", type=float, default=120.0, help="retry when an active replay stops growing")
    parser.add_argument(
        "--match-retries",
        type=int,
        default=2,
        help="retry a failed match under the same ID, replacing partial recordings",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="runs")
    parser.add_argument("--run-name")
    parser.add_argument("--dashboard-update-every", type=int, default=1)
    parser.add_argument("--wandb-mode", choices=("disabled", "offline", "online"), default="disabled")
    parser.add_argument("--wandb-project", default="vsss-coach")
    parser.add_argument("--wandb-entity")
    parser.add_argument("--wandb-group")
    parser.add_argument("--wandb-log-matches", action="store_true", help="upload all match replay JSON files as an artifact")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parallel evolutionary tournament for VSSS Coach")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run an evolutionary experiment")
    add_run_arguments(run_parser)
    resume_parser = subparsers.add_parser("resume", help="resume an interrupted run and repair incomplete matches")
    resume_parser.add_argument("--run", required=True, help="existing run directory")
    resume_parser.add_argument("--match-retries", type=int, help="override retries stored in the run configuration")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "resume":
        run_dir = Path(args.run)
        try:
            saved = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as error:
            raise SystemExit(f"cannot resume {run_dir}: invalid or missing config.json") from error
        saved.pop("competition_rules", None)
        saved["command"] = "run"
        saved["resume_run"] = str(run_dir)
        saved.setdefault("match_retries", 2)
        saved.setdefault("simulation_mode", "evolution")
        saved.setdefault("baseline_from", None)
        saved.setdefault("telemetry_start_timeout", 60.0)
        saved.setdefault("telemetry_stall_timeout", 120.0)
        if args.match_retries is not None:
            saved["match_retries"] = args.match_retries
        wandb_metadata = run_dir / "wandb.json"
        if wandb_metadata.exists():
            saved["wandb_run_id"] = json.loads(wandb_metadata.read_text(encoding="utf-8")).get("run_id")
        args = argparse.Namespace(**saved)
    else:
        args.resume_run = None
        args.wandb_run_id = None
    if args.competitors < 2 or args.workers < 1 or args.matches_per_pair < 1 or args.baseline_matches < 0 or args.match_retries < 0 or args.telemetry_start_timeout <= 0 or args.telemetry_stall_timeout <= 0:
        raise SystemExit("competitors >= 2, workers >= 1, matches-per-pair >= 1, baseline-matches >= 0, and match-retries >= 0 are required")
    run_dir = run_evolution(args)
    print(run_dir.resolve())


if __name__ == "__main__":
    main()
