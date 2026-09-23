"""Authenticated persistent match queue for distributed VSSS Coach workers."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .evolution import MatchTask
from .travesim_backend import TraveSimConfig, run_travesim_match


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobQueue:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, generation INTEGER NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', worker_id TEXT, lease_until REAL,
                result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL)""")
            db.execute("CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, capacity INTEGER NOT NULL, last_seen REAL NOT NULL)")

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def submit(self, jobs: list[dict[str, Any]]) -> int:
        inserted = 0
        with self.lock, self.connect() as db:
            for job in jobs:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO jobs(id,generation,payload,updated_at) VALUES(?,?,?,?)",
                    (job["id"], int(job["generation"]), json.dumps(job["payload"]), utc_now()),
                )
                inserted += cursor.rowcount
        return inserted

    def lease(self, worker_id: str, limit: int, seconds: float) -> list[dict[str, Any]]:
        now = time.time()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET status='queued',worker_id=NULL,lease_until=NULL,updated_at=? "
                "WHERE status='leased' AND lease_until<?", (utc_now(), now),
            )
            rows = db.execute("SELECT id,payload FROM jobs WHERE status='queued' ORDER BY id LIMIT ?", (limit,)).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE jobs SET status='leased',worker_id=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?",
                    (worker_id, now + seconds, utc_now(), row["id"]),
                )
            db.commit()
        return [{"id": row["id"], "payload": json.loads(row["payload"])} for row in rows]

    def complete(self, job_id: str, worker_id: str, result: dict[str, Any]) -> bool:
        with self.lock, self.connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET status='complete',result=?,lease_until=NULL,updated_at=? "
                "WHERE id=? AND worker_id=? AND status='leased'",
                (json.dumps(result), utc_now(), job_id, worker_id),
            )
        return bool(cursor.rowcount)

    def fail(self, job_id: str, worker_id: str, error: str, retry: bool) -> bool:
        status = "queued" if retry else "failed"
        with self.lock, self.connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET status=?,error=?,worker_id=NULL,lease_until=NULL,updated_at=? "
                "WHERE id=? AND worker_id=? AND status='leased'",
                (status, error[-4000:], utc_now(), job_id, worker_id),
            )
        return bool(cursor.rowcount)

    def heartbeat(self, worker_id: str, capacity: int) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO workers(id,capacity,last_seen) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET capacity=excluded.capacity,last_seen=excluded.last_seen",
                (worker_id, capacity, time.time()),
            )

    def results(self, generation: int, exclude: set[str] | None = None) -> dict[str, Any]:
        exclude = exclude or set()
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,status,result,error,worker_id,attempts FROM jobs WHERE generation=? ORDER BY id", (generation,),
            ).fetchall()
        return {
            "jobs": [dict(row) | {"result": json.loads(row["result"]) if row["result"] else None} for row in rows if row["id"] not in exclude],
            "counts": {status: sum(row["status"] == status for row in rows) for status in ("queued", "leased", "complete", "failed")},
        }

    def status(self) -> dict[str, Any]:
        with self.connect() as db:
            rows = db.execute("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
            workers = db.execute(
                "SELECT w.id AS worker_id,w.capacity,datetime(w.last_seen,'unixepoch') AS updated_at,"
                "SUM(CASE WHEN j.status='leased' THEN 1 ELSE 0 END) AS active "
                "FROM workers w LEFT JOIN jobs j ON j.worker_id=w.id WHERE w.last_seen>? GROUP BY w.id,w.capacity,w.last_seen",
                (time.time() - 60,),
            ).fetchall()
        return {"counts": {row["status"]: row["count"] for row in rows}, "workers": [dict(row) for row in workers]}


def handler_for(queue: JobQueue, admin_token: str, worker_token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def send(self, status: HTTPStatus, payload: object) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, role: str) -> bool:
            supplied = self.headers.get("Authorization")
            valid = supplied == f"Bearer {admin_token}" or (role == "worker" and supplied == f"Bearer {worker_token}")
            if not valid:
                self.send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return valid

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/v1/status":
                if not self.authorized("worker"): return
                self.send(HTTPStatus.OK, queue.status()); return
            if self.path.startswith("/api/v1/results/"):
                if not self.authorized("admin"): return
                self.send(HTTPStatus.OK, queue.results(int(self.path.rsplit("/", 1)[1]))); return
            self.send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            data = self.payload()
            if self.path == "/api/v1/jobs":
                if not self.authorized("admin"): return
                self.send(HTTPStatus.OK, {"inserted": queue.submit(data.get("jobs", []))}); return
            if self.path == "/api/v1/lease":
                if not self.authorized("worker"): return
                jobs = queue.lease(str(data["worker_id"]), max(1, int(data.get("limit", 1))), float(data.get("lease_seconds", 2100)))
                self.send(HTTPStatus.OK, {"jobs": jobs}); return
            if self.path == "/api/v1/heartbeat":
                if not self.authorized("worker"): return
                queue.heartbeat(str(data["worker_id"]), max(1, int(data.get("capacity", 1))))
                self.send(HTTPStatus.OK, {"ok": True}); return
            if self.path == "/api/v1/results":
                if not self.authorized("admin"): return
                self.send(HTTPStatus.OK, queue.results(int(data["generation"]), set(data.get("exclude", [])))); return
            parts = self.path.strip("/").split("/")
            if len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"]:
                if not self.authorized("worker"): return
                job_id, action = parts[3], parts[4]
                if action == "complete": ok = queue.complete(job_id, str(data["worker_id"]), data["result"])
                elif action == "fail": ok = queue.fail(job_id, str(data["worker_id"]), str(data.get("error", "")), bool(data.get("retry", True)))
                else: self.send(HTTPStatus.NOT_FOUND, {"error": "not found"}); return
                self.send(HTTPStatus.OK if ok else HTTPStatus.CONFLICT, {"ok": ok}); return
            self.send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def log_message(self, format: str, *args: object) -> None:
            return
    return Handler


def request_json(url: str, token: str, method: str = "GET", payload: object | None = None, timeout: float = 60) -> Any:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=data, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def worker_loop(args: argparse.Namespace, slot: int) -> None:
    worker_id = f"{args.worker_name}-{slot}"
    while True:
        job = None
        try:
            request_json(args.coordinator + "/api/v1/heartbeat", args.token, "POST", {"worker_id": worker_id, "capacity": 1})
            leased = request_json(args.coordinator + "/api/v1/lease", args.token, "POST", {
                "worker_id": worker_id, "limit": 1, "lease_seconds": args.lease_seconds,
            })["jobs"]
            if not leased:
                time.sleep(args.poll_interval); continue
            job = leased[0]
            task = MatchTask(**job["payload"]["task"])
            config = TraveSimConfig(**job["payload"]["config"])
            config = replace(
                config,
                project_root=str(args.travesim_root.resolve()),
                webots=args.webots_executable or os.environ.get("WEBOTS_EXECUTABLE") or config.webots,
                live_matches_dir=str((args.replay_dir / worker_id).resolve()),
            )
            Path(config.live_matches_dir).mkdir(parents=True, exist_ok=True)
            result = run_travesim_match(task, config)
            request_json(args.coordinator + f"/api/v1/jobs/{job['id']}/complete", args.token, "POST", {"worker_id": worker_id, "result": result}, timeout=300)
        except (OSError, HTTPError, ValueError, KeyError) as exc:
            if job is not None:
                try: request_json(args.coordinator + f"/api/v1/jobs/{job['id']}/fail", args.token, "POST", {"worker_id": worker_id, "error": repr(exc), "retry": True})
                except OSError: pass
            time.sleep(args.poll_interval)


def coordinator_main(args: argparse.Namespace) -> None:
    admin_token = args.admin_token or os.environ.get("VSSS_COACH_ADMIN_TOKEN", "")
    worker_token = args.worker_token or os.environ.get("VSSS_COACH_WORKER_TOKEN", "")
    if min(len(admin_token), len(worker_token)) < 24: raise SystemExit("Set VSSS_COACH_ADMIN_TOKEN and VSSS_COACH_WORKER_TOKEN with at least 24 characters")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(JobQueue(args.data_dir / "queue.sqlite3"), admin_token, worker_token))
    print(f"Coordinator: http://{args.host}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass


def worker_main(args: argparse.Namespace) -> None:
    args.token = args.token or os.environ.get("VSSS_COACH_WORKER_TOKEN", "")
    if len(args.token) < 24: raise SystemExit("Use --token or VSSS_COACH_WORKER_TOKEN with at least 24 characters")
    args.worker_name = args.worker_name or socket.gethostname()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        list(executor.map(lambda slot: worker_loop(args, slot), range(args.workers)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VSSS Coach distributed physical match queue")
    sub = parser.add_subparsers(dest="command", required=True)
    coordinator = sub.add_parser("coordinator")
    coordinator.add_argument("--host", default="127.0.0.1"); coordinator.add_argument("--port", type=int, default=8090)
    coordinator.add_argument("--data-dir", type=Path, default=Path("runs/distributed")); coordinator.add_argument("--admin-token"); coordinator.add_argument("--worker-token")
    worker = sub.add_parser("worker")
    worker.add_argument("--coordinator", required=True); worker.add_argument("--token"); worker.add_argument("--worker-name")
    worker.add_argument("--workers", type=int, default=1); worker.add_argument("--lease-seconds", type=float, default=2100)
    worker.add_argument("--poll-interval", type=float, default=3)
    worker.add_argument("--travesim-root", type=Path, default=Path(".")); worker.add_argument("--webots-executable")
    worker.add_argument("--replay-dir", type=Path, default=Path("runs/worker-replays"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    (coordinator_main if args.command == "coordinator" else worker_main)(args)


if __name__ == "__main__": main()
