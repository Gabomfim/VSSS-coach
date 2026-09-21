"""Run a real TraveSim experiment and its dashboard as one process group."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .dashboard import DashboardHandler, DashboardServer


def mark_run_failed(run_dir: Path, return_code: int) -> None:
    path = run_dir / "live.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    payload.update({
        "status": "failed",
        "failure": f"evolution process exited with status {return_code}",
        "updated_at": datetime.now().astimezone().isoformat(),
    })
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def evolution_command(args: argparse.Namespace, extra: list[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "coach_silvs.evolution",
        "run",
        *extra,
        "--backend",
        "travesim",
        "--output",
        str(args.output),
        "--run-name",
        args.run_name,
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start real TraveSim evolution and a live Coach Silvs dashboard",
    )
    parser.add_argument("--output", type=Path, default=Path("runs"))
    parser.add_argument("--run-name", help="defaults to a unique travesim-live timestamp")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    parser.add_argument("--background", action="store_true", help="detach dashboard and simulations from this terminal")
    parser.add_argument("--background-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "evolution_args",
        nargs=argparse.REMAINDER,
        help="evolution options after --; backend/output/run-name are enforced",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.run_name:
        args.run_name = datetime.now().strftime("travesim-live-%Y%m%d-%H%M%S")
    args.output.mkdir(parents=True, exist_ok=True)
    pid_path = args.output / f"{args.run_name}.pid"
    log_path = args.output / f"{args.run_name}.log"
    if args.background and not args.background_child:
        forwarded = [item for item in sys.argv[1:] if item != "--background"]
        separator = forwarded.index("--") if "--" in forwarded else len(forwarded)
        forwarded.insert(separator, "--background-child")
        with log_path.open("ab") as log:
            child = subprocess.Popen(
                [sys.executable, "-m", "coach_silvs.live", *forwarded],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        pid_path.write_text(f"{child.pid}\n", encoding="utf-8")
        print(f"Simulações em background (PID {child.pid})")
        print(f"Dashboard: http://{args.host}:{args.port}")
        print(f"Log: {log_path.resolve()}")
        print(f"Parar: kill {child.pid}")
        return
    extra = args.evolution_args
    if extra[:1] == ["--"]:
        extra = extra[1:]
    run_dir = args.output / args.run_name
    if run_dir.exists():
        raise SystemExit(f"Run directory already exists: {run_dir}")

    process = subprocess.Popen(evolution_command(args, extra))
    deadline = time.monotonic() + args.startup_timeout
    while not run_dir.is_dir():
        return_code = process.poll()
        if return_code is not None:
            raise SystemExit(f"Real TraveSim evolution exited with status {return_code}")
        if time.monotonic() >= deadline:
            process.terminate()
            raise SystemExit(f"Timed out waiting for run directory: {run_dir}")
        time.sleep(0.1)

    handler = type("LiveRunDashboardHandler", (DashboardHandler,), {"run_dir": run_dir.resolve()})
    server = DashboardServer((args.host, args.port), handler)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    print(f"Dashboard TraveSim real: http://{args.host}:{args.port}", flush=True)
    print(f"Run físico: {run_dir.resolve()}", flush=True)
    try:
        server.timeout = 1.0
        while process.poll() is None:
            server.handle_request()
        mark_run_failed(run_dir, process.returncode)
        print(f"Evolução física falhou com status {process.returncode}; dashboard mantido para diagnóstico.", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if args.background_child and pid_path.exists():
            try:
                if int(pid_path.read_text(encoding="utf-8").strip()) == os.getpid():
                    pid_path.unlink()
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    main()
