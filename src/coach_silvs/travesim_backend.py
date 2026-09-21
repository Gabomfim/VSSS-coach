"""Real Webots/TraveSim match execution and replay artifact conversion."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

from .formations import RobotPlacement, place_both_teams


@dataclass(frozen=True, slots=True)
class TraveSimConfig:
    project_root: str
    webots: str
    webots_mode: str
    match_duration: float
    timeout: float
    port_base: int
    ruleset: str
    field_strategy: str
    formation: str
    position_sigma: float
    position_limit: float
    angle_sigma_degrees: float
    angle_limit_degrees: float
    client_sync_delay_ms: int
    telemetry_start_timeout: float
    telemetry_stall_timeout: float
    live_matches_dir: str


def _replace_robot_pose(world: str, team: str, placement: RobotPlacement) -> str:
    name = f"{team}Robot{placement.robot_id}"
    # Do not let a match span across adjacent robot nodes. Otherwise replacing
    # Robot1 would accidentally rewrite Robot0's first translation.
    pattern = re.compile(
        r'GenericVssRobot \{(?:(?!\nGenericVssRobot \{).)*?name "'
        + re.escape(name)
        + r'"(?:(?!\nGenericVssRobot \{).)*?\n\}',
        re.DOTALL,
    )
    match = pattern.search(world)
    if match is None:
        raise ValueError(f"robot {name!r} not found in world template")
    block = match.group(0)
    block = re.sub(
        r"(?m)^  translation .+$",
        f"  translation {placement.position.x:.9f} {placement.position.y:.9f} 0.0025",
        block,
        count=1,
    )
    rotation = f"  rotation 0 0 1 {placement.orientation:.9f}"
    if re.search(r"(?m)^  rotation .+$", block):
        block = re.sub(r"(?m)^  rotation .+$", rotation, block, count=1)
    else:
        block = re.sub(r"(?m)^(  translation .+)$", rf"\1\n{rotation}", block, count=1)
    return world[: match.start()] + block + world[match.end() :]


def render_match_world(
    template: str,
    placements: dict[str, object],
    telemetry_path: Path,
    match_duration: float,
    replacer_port: int,
    yellow_port: int,
    blue_port: int,
    vision_port: int,
    client_sync_delay_ms: int = 0,
) -> str:
    """Render a private world with one formation and one endpoint set."""
    referee = (
        "VssReferee {\n"
        "  robotsPerTeam 3\n"
        f"  replacer_port {replacer_port}\n"
        f"  yellow_team_port {yellow_port}\n"
        f"  blue_team_port {blue_port}\n"
        f"  multicast_port {vision_port}\n"
        f"  match_duration {match_duration:.6f}\n"
        f'  telemetry_path "{telemetry_path.as_posix()}"\n'
        f"  external_client_delay_ms {client_sync_delay_ms}\n"
        '  vision_interface_address "127.0.0.1"\n'
        "}"
    )
    world, count = re.subn(r"VssReferee \{.*?\n\}", referee, template, count=1, flags=re.DOTALL)
    if count != 1:
        raise ValueError("VssReferee block not found in world template")
    for team_key, team_name in (("yellow", "Yellow"), ("blue", "Blue")):
        for placement in placements[team_key]:  # type: ignore[union-attr]
            world = _replace_robot_pose(world, team_name, placement)
    return world


def _telemetry_frames(path: Path, match_duration: float) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    if not path.exists():
        return frames
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if frame.get("type") != "frame":
                continue
            frame["time_remaining"] = max(0.0, match_duration - float(frame.get("time", 0.0)))
            frames.append(frame)
    return frames


def _team_scores(frames: list[dict[str, Any]]) -> tuple[int, int]:
    """Convert goal-side counters to yellow/blue team scores after halftime."""
    final = frames[-1]
    final_yellow_side = int(final.get("goals_yellow", 0))
    final_blue_side = int(final.get("goals_blue", 0))
    if int(final.get("period", 1)) < 2:
        return final_yellow_side, final_blue_side
    first_half = next((frame for frame in reversed(frames) if int(frame.get("period", 1)) == 1), None)
    first_yellow = int(first_half.get("goals_yellow", 0)) if first_half else 0
    first_blue = int(first_half.get("goals_blue", 0)) if first_half else 0
    yellow_team = first_yellow + max(0, final_blue_side - first_blue)
    blue_team = first_blue + max(0, final_yellow_side - first_yellow)
    return yellow_team, blue_team


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def run_travesim_match(task: Any, config: TraveSimConfig) -> dict[str, Any]:
    """Run two candidates in Webots and return dashboard-compatible telemetry."""
    match_duration = float(getattr(task, "match_duration", None) or config.match_duration)
    root = Path(config.project_root)
    world_template = (root / "worlds" / "Match3v3.wbt").read_text(encoding="utf-8")
    rng = random.Random(task.seed)
    placements = place_both_teams(
        rng,
        yellow_side="right",
        formation=config.formation,
        position_sigma=config.position_sigma,
        position_limit=config.position_limit,
        angle_sigma_degrees=config.angle_sigma_degrees,
        angle_limit_degrees=config.angle_limit_degrees,
    )
    ports = [config.port_base + task.task_index * 4 + offset for offset in range(4)]
    if ports[-1] > 65535:
        raise RuntimeError("real match port range exceeds 65535; lower --travesim-port-base")

    with tempfile.TemporaryDirectory(prefix=f"coach-silvs-{task.match_id}-") as temporary:
        work = Path(temporary)
        live_matches = Path(config.live_matches_dir)
        live_matches.mkdir(parents=True, exist_ok=True)
        telemetry = live_matches / f"{task.match_id}.live.jsonl"
        live_metadata = live_matches / f"{task.match_id}.live.meta.json"
        live_metadata.write_text(json.dumps({
            "match_id": task.match_id,
            "generation": task.generation,
            "home": task.home["candidate_id"],
            "away": task.away["candidate_id"],
            "repetition": task.repetition,
            "seed": task.seed,
            "backend": "travesim",
            "live": True,
            "match_duration": match_duration,
        }), encoding="utf-8")
        home_candidate = work / "home.json"
        away_candidate = work / "away.json"
        home_candidate.write_text(json.dumps(task.home), encoding="utf-8")
        away_candidate.write_text(json.dumps(task.away), encoding="utf-8")
        world = render_match_world(
            world_template,
            placements,
            telemetry,
            match_duration,
            *ports,
            client_sync_delay_ms=config.client_sync_delay_ms,
        )
        world_path = root / "worlds" / f".coach-silvs-{os.getpid()}-{task.task_index}.wbt"
        world_path.write_text(world, encoding="utf-8")

        common = [
            sys.executable,
            "-m",
            "coach_silvs.client",
            "--vision-port",
            str(ports[3]),
            "--vision-interface",
            "127.0.0.1",
            "--match-duration",
            str(match_duration),
            "--ruleset",
            config.ruleset,
            "--field-strategy",
            config.field_strategy,
        ]
        home = subprocess.Popen(
            [*common, "--team", "yellow", "--command-port", str(ports[1]), "--attack-sign", "-1", "--candidate", str(home_candidate)],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        away = subprocess.Popen(
            [*common, "--team", "blue", "--command-port", str(ports[2]), "--attack-sign", "1", "--candidate", str(away_candidate)],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        webots_log = work / "webots.log"
        webots_error: RuntimeError | None = None
        webots: subprocess.Popen[bytes] | None = None
        try:
            with webots_log.open("wb") as webots_output:
                webots = subprocess.Popen(
                    [
                        config.webots,
                        "--batch",
                        f"--mode={config.webots_mode}",
                        "--no-rendering",
                        "--stdout",
                        "--stderr",
                        str(world_path),
                    ],
                    cwd=root,
                    stdout=webots_output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                started_at = time.monotonic()
                last_growth_at = started_at
                last_size = 0
                while webots.poll() is None:
                    now = time.monotonic()
                    try:
                        current_size = telemetry.stat().st_size
                    except OSError:
                        current_size = 0
                    if current_size > last_size:
                        last_size = current_size
                        last_growth_at = now
                    if current_size == 0 and now - started_at > config.telemetry_start_timeout:
                        _terminate(webots)
                        webots_error = RuntimeError(
                            f"Webots produced no telemetry for {config.telemetry_start_timeout:.1f} s"
                        )
                        break
                    if current_size > 0 and now - last_growth_at > config.telemetry_stall_timeout:
                        _terminate(webots)
                        webots_error = RuntimeError(
                            f"Webots telemetry stalled for {config.telemetry_stall_timeout:.1f} s"
                        )
                        break
                    if now - started_at > config.timeout:
                        _terminate(webots)
                        webots_error = RuntimeError(f"Webots match exceeded {config.timeout:.1f} s")
                        break
                    time.sleep(0.5)
                if webots.returncode != 0:
                    webots_error = webots_error or RuntimeError(f"Webots exited with {webots.returncode}")
        finally:
            client_failures = []
            client_logs: dict[str, str] = {}
            for label, process in (("yellow", home), ("blue", away)):
                if process.poll() is not None:
                    diagnostic = process.stderr.read().decode("utf-8", errors="replace")[-4000:] if process.stderr else ""
                    client_failures.append(f"{label} client exited with {process.returncode}:\n{diagnostic}")
            _terminate(home)
            _terminate(away)
            for label, process in (("yellow", home), ("blue", away)):
                client_logs[label] = process.stderr.read().decode("utf-8", errors="replace")[-2000:] if process.stderr else ""
            world_path.unlink(missing_ok=True)

        output = webots_log.read_text(encoding="utf-8", errors="replace")
        if webots_error is not None:
            raise RuntimeError(f"{webots_error}\n{output[-4000:]}")

        if client_failures:
            raise RuntimeError("\n".join(client_failures))

        frames = _telemetry_frames(telemetry, match_duration)
        if not frames or not frames[-1].get("finished"):
            raise RuntimeError("TraveSim produced no complete supervisor telemetry")
        final = frames[-1]
        home_goals, away_goals = _team_scores(frames)
        return {
            "match_id": task.match_id,
            "generation": task.generation,
            "home": task.home["candidate_id"],
            "away": task.away["candidate_id"],
            "home_goals": home_goals,
            "away_goals": away_goals,
            "repetition": task.repetition,
            "seed": task.seed,
            "backend": "travesim",
            "match_duration": match_duration,
            "yellow_formation": placements["yellow_formation"],
            "blue_formation": placements["blue_formation"],
            "webots_log_tail": output[-4000:],
            "client_log_tail": client_logs,
            "frames": frames,
        }
