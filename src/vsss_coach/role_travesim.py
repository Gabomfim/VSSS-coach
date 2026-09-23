"""Persistent physical Webots evaluator for one role candidate and many scenarios."""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from .fields import (
    ally_goal_ball_corridor_field, earliest_reachable_interception_field,
    interception_crossing_time, predictive_interception_field,
)
from .formations import place_both_teams
from .model import BallState, RobotState, Vec2
from .travesim_backend import render_match_world, _telemetry_frames

@dataclass(frozen=True, slots=True)
class RoleTraveSimConfig:
    project_root: str
    webots: str
    webots_mode: str = "fast"
    port_base: int = 31000
    timeout: float = 900.0
    retries: int = 2
    client_sync_delay_ms: int = 2


def _with_ball_acceleration(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate replay frames with a filtered acceleration estimate."""
    previous: dict[str, Any] | None = None
    filtered_x = filtered_y = 0.0
    for frame in frames:
        ball = frame.get("ball", {})
        if previous is None or frame.get("period") != previous.get("period"):
            filtered_x = filtered_y = 0.0
        else:
            elapsed = float(frame.get("time", 0.0)) - float(previous.get("time", 0.0))
            if elapsed > 1e-6:
                old_ball = previous.get("ball", {})
                raw_x = (float(ball.get("vx", 0.0)) - float(old_ball.get("vx", 0.0))) / elapsed
                raw_y = (float(ball.get("vy", 0.0)) - float(old_ball.get("vy", 0.0))) / elapsed
                filtered_x = 0.75 * filtered_x + 0.25 * raw_x
                filtered_y = 0.75 * filtered_y + 0.25 * raw_y
        ball["ax"], ball["ay"] = filtered_x, filtered_y
        previous = frame
    return frames


def _with_role_fields(frames: list[dict[str, Any]], candidate: Any, role: str) -> list[dict[str, Any]]:
    """Persist exact field targets so the dashboard does not reimplement them."""
    for frame in frames:
        robots = frame.get("blue", [])
        if not robots:
            continue
        source = robots[0]
        ball_data = frame.get("ball", {})
        robot = RobotState(
            position=Vec2(float(source.get("x", 0.0)), float(source.get("y", 0.0))),
            velocity=Vec2(float(source.get("vx", 0.0)), float(source.get("vy", 0.0))),
            robot_id=0,
            orientation=float(source.get("orientation", 0.0)),
        )
        ball = BallState(
            position=Vec2(float(ball_data.get("x", 0.0)), float(ball_data.get("y", 0.0))),
            velocity=Vec2(float(ball_data.get("vx", 0.0)), float(ball_data.get("vy", 0.0))),
            acceleration=Vec2(float(ball_data.get("ax", 0.0)), float(ball_data.get("ay", 0.0))),
        )
        if role == "goalkeeper":
            goalkeeper_offset = max(0.045, min(0.09, candidate.intercept_offset))
            _, intercept = predictive_interception_field(
                robot, ball, -0.75, 0.40, 1.0, candidate.intercept_horizon,
                goalkeeper_offset, candidate.intercept_margin,
                candidate.intercept_min_speed,
            )
            line_x = -0.75 + goalkeeper_offset
            intercept_time = interception_crossing_time(ball, line_x, candidate.intercept_horizon)
            corridor = None
        else:
            _, intercept, intercept_time = earliest_reachable_interception_field(
                robot, ball, -0.75, 0.40, 1.0, candidate.intercept_horizon,
                candidate.intercept_min_speed, 1.70,
            )
            _, corridor = ally_goal_ball_corridor_field(
                robot, ball, -0.75, 0.40, 1.0, candidate.intercept_horizon,
                candidate.intercept_min_speed,
            )
        frame[f"{role}_field"] = {
            "intercept": None if intercept is None else {"x": intercept.x, "y": intercept.y},
            "intercept_time": None if not math.isfinite(intercept_time) else intercept_time,
            "corridor": None if corridor is None else {"x": corridor.x, "y": corridor.y},
        }
    return frames

def _run_physical_trial_once(payload: tuple[str, Any, list[Any], int, RoleTraveSimConfig]) -> dict[str, object]:
    role, candidate, scenarios, worker_index, config = payload
    if role not in {"defender", "goalkeeper"}:
        raise ValueError("the physical backend currently supports defender and goalkeeper trials")
    root = Path(config.project_root)
    ports = [config.port_base + worker_index * 4 + i for i in range(4)]
    with tempfile.TemporaryDirectory(prefix=f"coach-role-{candidate.candidate_id}-") as temporary:
        work = Path(temporary)
        scenario_path, results_path, telemetry_path = work / "scenarios.csv", work / "results.jsonl", work / "telemetry.jsonl"
        scenario_path.write_text("".join(
            f"{s.scenario_id},{s.ball[0]},{s.ball[1]},{s.ball_velocity[0]},{s.ball_velocity[1]},{s.player[0]},{s.player[1]},{s.player_yaw},{s.duration}\n" for s in scenarios
        ), encoding="utf-8")
        genome = {
            "ball_spiral_radius_0": candidate.spiral_radius,
            "ball_spiral_smoothing_0": candidate.spiral_smoothing,
            "approach_gain_0": 1.0,
            "role_near_speed": candidate.near_speed,
            "role_far_speed": candidate.far_speed,
            "role_near_distance": candidate.near_distance,
            "role_far_distance": candidate.far_distance,
            "role_intercept_horizon": candidate.intercept_horizon,
            "role_intercept_offset": candidate.intercept_offset,
            "role_contact_spin_delay": candidate.contact_spin_delay,
            "role_intercept_margin": candidate.intercept_margin,
            "role_intercept_gain": candidate.intercept_gain,
            "role_intercept_blend": candidate.intercept_blend,
            "role_intercept_min_speed": candidate.intercept_min_speed,
            "role_intercept_velocity_gain": candidate.intercept_velocity_gain,
            "role_intercept_acceleration_gain": candidate.intercept_acceleration_gain,
            "role_intercept_time_margin": candidate.intercept_time_margin,
            "role_intercept_urgency_gain": candidate.intercept_urgency_gain,
            "role_defensive_corridor_gain": candidate.defensive_corridor_gain,
        }
        candidate_path = work / "candidate.json"
        candidate_path.write_text(json.dumps({"genome": genome, "formula": {"field_strategy": "shared"}}), encoding="utf-8")
        template = (root / "worlds" / "Match3v3.wbt").read_text(encoding="utf-8")
        placements = place_both_teams(random.Random(1), yellow_side="right", formation="balanced", position_sigma=0, position_limit=0, angle_sigma_degrees=0, angle_limit_degrees=0)
        world = render_match_world(template, placements, telemetry_path, 0.0, *ports, client_sync_delay_ms=config.client_sync_delay_ms)
        world = re.sub(r'(  vision_interface_address "127\.0\.0\.1"\n)', rf'\1  role_scenarios_path "{scenario_path.as_posix()}"\n  role_results_path "{results_path.as_posix()}"\n', world, count=1)
        world_path = root / "worlds" / f".coach-role-{os.getpid()}-{worker_index}.wbt"
        world_path.write_text(world, encoding="utf-8")
        common = [sys.executable, "-m", "vsss_coach.client", "--team", "blue", "--vision-port", str(ports[3]), "--vision-interface", "127.0.0.1", "--command-port", str(ports[2]), "--attack-sign", "1", "--match-duration", "100000", "--candidate", str(candidate_path), "--role", role, "--active-robot-id", "0"]
        client = subprocess.Popen(common, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        webots = subprocess.Popen([config.webots, "--batch", f"--mode={config.webots_mode}", "--no-rendering", str(world_path)], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        try:
            webots.wait(timeout=config.timeout)
            if webots.returncode:
                raise RuntimeError(webots.stderr.read().decode(errors="replace")[-4000:])
            if client.poll() is not None and client.returncode:
                error = client.stderr.read().decode(errors="replace")[-4000:]
                raise RuntimeError(f"role controller exited with {client.returncode}: {error}")
        finally:
            if webots.poll() is None:
                os.killpg(webots.pid, signal.SIGTERM)
                try:
                    webots.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(webots.pid, signal.SIGKILL)
                    webots.wait(timeout=5)
            client.terminate()
            try:
                client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                client.kill()
                client.wait(timeout=3)
            world_path.unlink(missing_ok=True)
        rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != len(scenarios):
            raise RuntimeError(f"Webots returned {len(rows)}/{len(scenarios)} role episodes")
        durations = {s.scenario_id: s.duration for s in scenarios}
        episodes=[]
        for row in rows:
            duration=durations[int(row["scenario_id"])]
            loss={"outcome":float(bool(row["goal"])),"interception":min(1.0,float(row["min_distance"])/.75),"reaction_time":min(1.0,float(row["reaction_time"])/duration),"redirection":0.0 if row["redirected"] else 1.0}
            loss["total"]=.70*loss["outcome"]+.12*loss["interception"]+.08*loss["reaction_time"]+.10*loss["redirection"]
            episodes.append(loss)
        components={key:sum(row[key] for row in episodes)/len(episodes) for key in episodes[0]}
        frames = _with_role_fields(
            _with_ball_acceleration(_telemetry_frames(telemetry_path, sum(durations.values()))),
            candidate, role,
        )
        return {"candidate_id":candidate.candidate_id,"loss":components["total"],"loss_components":components,"episodes":episodes,"backend":"travesim","frames":frames}


def run_physical_trial(payload: tuple[str, Any, list[Any], int, RoleTraveSimConfig]) -> dict[str, object]:
    """Retry a complete candidate trial when Webots hangs or crashes."""
    config = payload[-1]
    errors: list[str] = []
    for attempt in range(config.retries + 1):
        try:
            return _run_physical_trial_once(payload)
        except (subprocess.TimeoutExpired, RuntimeError, OSError, ValueError) as error:
            errors.append(f"attempt {attempt + 1}: {error}")
            if attempt >= config.retries:
                raise RuntimeError("Webots role trial failed after retries: " + " | ".join(errors)) from error
    raise AssertionError("unreachable")
