"""Render fair, shot-synchronized Webots comparisons of role-trial candidates."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import time

from .role_dashboard import render_replay_world
from .role_trials import RoleCandidate, generate_scenarios
from .role_travesim import RoleTraveSimConfig, run_physical_trial


def choose_candidates(run_dir: Path) -> tuple[RoleCandidate, RoleCandidate, int]:
    generation_files = sorted((run_dir / "generations").glob("generation-*.json"))
    if not generation_files:
        raise ValueError("generation 1 has not completed")
    before_row = json.loads(generation_files[0].read_text(encoding="utf-8"))[0]
    best_row = min(
        (json.loads(path.read_text(encoding="utf-8"))[0] for path in generation_files),
        key=lambda row: (float(row["loss"]), row["candidate_id"]),
    )
    best_generation = int(str(best_row["candidate_id"])[1:5])
    return (
        RoleCandidate(**before_row["parameters"]),
        RoleCandidate(**best_row["parameters"]),
        best_generation,
    )


def synchronize_shots(frames: list[dict], shot_count: int, duration: float, fps: int = 30) -> list[dict]:
    """Use actual frames, hold the last one after an early goal; never interpolate."""
    grouped: dict[int, list[dict]] = {}
    for frame in frames:
        grouped.setdefault(int(frame["period"]), []).append(frame)
    if len(grouped) != shot_count:
        raise ValueError(f"expected {shot_count} recorded shots, found {len(grouped)}")
    aligned: list[dict] = []
    for period in range(1, shot_count + 1):
        shot = sorted(grouped[period], key=lambda frame: float(frame["time"]))
        frame_index = 0
        for tick in range(round(duration * fps)):
            timestamp = tick / fps
            while frame_index + 1 < len(shot) and float(shot[frame_index + 1]["time"]) <= timestamp:
                frame_index += 1
            frame = deepcopy(shot[frame_index])
            frame["period"] = period
            frame["time"] = timestamp
            aligned.append(frame)
    return aligned


def training_replay(run_dir: Path, generation: int, candidate_id: str,
                    shot_count: int, shot_duration: float) -> dict:
    """Use the winning candidate's original recorded trial, not a new simulation."""
    source = run_dir / "replays" / f"generation-{generation:04d}.json"
    replay = json.loads(source.read_text(encoding="utf-8"))
    if replay.get("candidate_id") != candidate_id:
        raise ValueError(f"{source} does not contain {candidate_id}")
    replay["frames"] = synchronize_shots(replay["frames"], shot_count, shot_duration)
    return replay


def record_webots(replay: Path, movie: Path, travesim_root: Path, webots: Path) -> None:
    template = (travesim_root / "worlds" / "Match3v3.wbt").read_text(encoding="utf-8")
    world = render_replay_world(template, replay.resolve())
    world = world.replace("basicTimeStep 10", "basicTimeStep 30", 1)
    world_path = travesim_root / "worlds" / f".coach-compare-{os.getpid()}-{time.time_ns()}.wbt"
    world_path.write_text(world, encoding="utf-8")
    environment = os.environ.copy()
    environment["COACH_ROLE_MOVIE_PATH"] = str(movie.resolve())
    try:
        subprocess.run(
            [str(webots), "--batch", "--mode=fast", "--stdout", "--stderr", str(world_path)],
            cwd=travesim_root, env=environment, timeout=900, check=True,
        )
    finally:
        world_path.unlink(missing_ok=True)
    if not movie.is_file() or movie.stat().st_size < 10000:
        raise RuntimeError(f"Webots did not produce a usable 3D movie: {movie}")


def movie_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def compose(before: Path, after: Path, target: Path, duration: float,
            layout: str = "horizontal", fps: int = 30) -> None:
    if layout not in {"horizontal", "vertical"}:
        raise ValueError(f"unsupported layout: {layout}")
    before_scale = duration / movie_duration(before)
    after_scale = duration / movie_duration(after)
    stack = "hstack" if layout == "horizontal" else "vstack"
    filter_graph = (
        f"[0:v]setpts=(PTS-STARTPTS)*{before_scale:.12f},fps={fps},"
        f"tpad=stop_mode=clone:stop_duration=1,trim=duration={duration:.6f},"
        "scale=960:540,drawtext=text='BEFORE':fontcolor=white:"
        "fontsize=38:box=1:boxcolor=black@0.65:x=28:y=25[left];"
        f"[1:v]setpts=(PTS-STARTPTS)*{after_scale:.12f},fps={fps},"
        f"tpad=stop_mode=clone:stop_duration=1,trim=duration={duration:.6f},"
        "scale=960:540,drawtext=text='AFTER':fontcolor=white:"
        "fontsize=38:box=1:boxcolor=black@0.65:x=28:y=25[right];"
        f"[left][right]{stack}=inputs=2[v]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(before), "-i", str(after), "-filter_complex", filter_graph,
         "-map", "[v]", "-an", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(target)],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--travesim-root", type=Path, default=Path("."))
    parser.add_argument("--webots", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--layout", choices=("horizontal", "vertical"), default="horizontal")
    parser.add_argument("--source", choices=("training", "rerun"), default="training",
                        help="recorded training trial (default) or fresh physics rerun")
    args = parser.parse_args()
    args.run = args.run.resolve()
    args.output = (args.output or args.run / "comparison-before-after").resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads((args.run / "config.json").read_text(encoding="utf-8"))
    role = config["role"]
    if role not in {"goalkeeper", "defender", "attacker"}:
        raise ValueError(f"unsupported role: {role}")
    webots = (args.webots or Path(config["webots"])).resolve()
    before, after, after_generation = choose_candidates(args.run)
    shot_count = int(config["scenarios"])
    shot_duration = float(config["scenario_duration"])
    scenarios = None
    physical = None
    if args.source == "rerun":
        scenarios = generate_scenarios(
            role, shot_count, int(config["seed"]), shot_duration,
            bool(config["feasible_scenarios"]), float(config["feasibility_max_speed"]),
            float(config["feasibility_max_acceleration"]),
            float(config["feasibility_reaction_delay"]),
            float(config["feasibility_safety_margin"]),
        )
        physical = RoleTraveSimConfig(str(args.travesim_root.resolve()), str(webots),
                                      "fast", 45000, 360, 2, 2)
    metadata = {"role": role, "before": before.candidate_id, "after": after.candidate_id,
                "after_generation": after_generation + 1, "scenario_count": shot_count,
                "shot_duration_seconds": shot_duration, "fps": 30,
                "layout": args.layout, "source": args.source}
    (args.output / "comparison.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    prefix = "training-" if args.source == "training" else ""
    for index, (label, candidate) in enumerate((("before", before), ("after", after))):
        replay_path = args.output / f"{prefix}{label}-replay.json"
        if not replay_path.exists():
            if args.source == "training":
                replay = training_replay(args.run, 0 if label == "before" else after_generation,
                                         candidate.candidate_id, shot_count, shot_duration)
            else:
                result = run_physical_trial((role, candidate, scenarios, index, physical))
                replay = {"role": role, "candidate_id": candidate.candidate_id,
                          "generation": 0 if label == "before" else after_generation,
                          "loss": result["loss"], "parameters": asdict(candidate),
                          "frames": synchronize_shots(result["frames"], shot_count, shot_duration)}
            replay_path.write_text(json.dumps(replay), encoding="utf-8")
        movie_path = args.output / f"{prefix}{label}-webots.mp4"
        if not movie_path.exists():
            record_webots(replay_path, movie_path, args.travesim_root.resolve(), webots)
    compose(args.output / f"{prefix}before-webots.mp4", args.output / f"{prefix}after-webots.mp4",
            args.output / f"{role}-before-after-{args.layout}.mp4",
            shot_count * shot_duration, args.layout)
    print((args.output / f"{role}-before-after-{args.layout}.mp4").resolve(), flush=True)


if __name__ == "__main__":
    main()
