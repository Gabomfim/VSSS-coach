"""Optional Weights & Biases tracking for evolutionary runs."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Iterable


class WandbTracker:
    def __init__(self, args: Any, config: dict[str, Any]) -> None:
        self.run: Any | None = None
        self.wandb: Any | None = None
        self.resuming = False
        if args.wandb_mode == "disabled":
            return
        try:
            import wandb
        except ImportError as exc:
            raise SystemExit(
                "Weights & Biases support is not installed. Run: "
                "python3.11 -m pip install -e './strategy[dev,wandb]'"
            ) from exc
        self.wandb = wandb
        run_id = getattr(args, "wandb_run_id", None)
        self.resuming = bool(run_id)
        self.run = wandb.init(
            project=args.wandb_project,
            entity=args.wandb_entity,
            name=args.run_name,
            group=args.wandb_group,
            job_type="genetic-programming",
            mode=args.wandb_mode,
            config=config,
            id=run_id,
            resume="must" if run_id and args.wandb_mode == "online" else "allow" if run_id else None,
        )

    def _log(self, payload: dict[str, Any], generation: int) -> None:
        """Append corrections safely when W&B's immutable history is resumed."""
        if self.run is None:
            return
        self.run.log(payload, step=None if self.resuming else generation)

    def log_generation(self, generation: int, ranking: Iterable[Any], match_count: int, run_dir: Path | None = None) -> None:
        if self.run is None or self.wandb is None:
            return
        rows = list(ranking)
        best = rows[0]
        table = self.wandb.Table(
            columns=["rank", "candidate", "score", "wins", "draws", "losses", "goals_for", "goals_against", "goal_difference"],
            data=[
                [index + 1, item.candidate_id, item.score, item.wins, item.draws, item.losses,
                 item.goals_for, item.goals_against, item.goals_for - item.goals_against]
                for index, item in enumerate(rows)
            ],
        )
        genome_columns = ["rank", "candidate", "generation"] + sorted(rows[0].genome)
        genome_table = self.wandb.Table(
            columns=genome_columns,
            data=[
                [index + 1, item.candidate_id, generation]
                + [item.genome[key] for key in sorted(item.genome)]
                for index, item in enumerate(rows)
            ],
        )
        field_rows: list[list[Any]] = []
        for index, item in enumerate(rows):
            fields = item.formula.get("fields", {})
            for player, objects in fields.items():
                for object_name, block in objects.items():
                    field_rows.append([
                        generation, index + 1, item.candidate_id, player, object_name,
                        block.get("depth"), block.get("nodes"), block.get("constants"),
                        block.get("text", "1"), json.dumps(block.get("expression", {"const": 1.0}), sort_keys=True),
                    ])
        field_table = self.wandb.Table(
            columns=["generation", "rank", "candidate", "player", "object", "depth", "nodes", "constants", "formula", "formula_json"],
            data=field_rows,
        )
        self._log({
            "generation": generation,
            "matches/generation": match_count,
            "best/score": best.score,
            "best/wins": best.wins,
            "best/draws": best.draws,
            "best/losses": best.losses,
            "best/goals_for": best.goals_for,
            "best/goals_against": best.goals_against,
            "best/goal_difference": best.goals_for - best.goals_against,
            "population/mean_score": sum(item.score for item in rows) / len(rows),
            "ranking": table,
            "genomes": genome_table,
            "vector_fields": field_table,
        }, generation)
        if run_dir is not None:
            artifact = self.wandb.Artifact(f"{run_dir.name}-generation", type="generation")
            generation_file = run_dir / "generations" / f"generation-{generation:04d}.json"
            if generation_file.exists():
                artifact.add_file(str(generation_file), name=generation_file.name)
                self.run.log_artifact(artifact, aliases=[f"generation-{generation:04d}", "latest"])

    def finish(self, run_dir: Path, log_matches: bool = False) -> None:
        if self.run is None or self.wandb is None:
            return
        artifact = self.wandb.Artifact(f"{run_dir.name}-results", type="vsss-coach-run")
        for name in ("config.json", "live.json", "ranking.json", "candidates.json", "formulas.json", "baseline.json", "baseline-evaluations.json"):
            path = run_dir / name
            if path.exists():
                artifact.add_file(str(path), name=name)
        generations = run_dir / "generations"
        if generations.exists():
            artifact.add_dir(str(generations), name="generations")
        if log_matches and (run_dir / "matches").exists():
            artifact.add_dir(str(run_dir / "matches"), name="matches")
        self.run.log_artifact(artifact)
        self.run.finish()

    def log_challenges(self, generation: int, rows: list[dict[str, Any]]) -> None:
        """Log current-vs-archived champion evaluations as a W&B table."""
        if self.run is None or self.wandb is None or not rows:
            return
        table = self.wandb.Table(
            columns=["generation", "current", "archived_generation", "archived", "goals_for", "goals_against", "goal_difference"],
            data=[[row[key] for key in ("generation", "current", "archived_generation", "archived", "goals_for", "goals_against", "goal_difference")] for row in rows],
        )
        self._log({"generation": generation, "champion_challenges": table}, generation)

    def log_baseline(self, generation: int, rows: list[dict[str, Any]]) -> None:
        """Track the generation champion against the fixed reference strategy."""
        if self.run is None or self.wandb is None or not rows:
            return
        table = self.wandb.Table(
            columns=["generation", "champion", "baseline", "repetition", "champion_goals", "baseline_goals", "goal_difference", "match_id"],
            data=[[row[key] for key in ("generation", "champion", "baseline", "repetition", "champion_goals", "baseline_goals", "goal_difference", "match_id")] for row in rows],
        )
        mean_difference = sum(row["goal_difference"] for row in rows) / len(rows)
        self._log({"generation": generation, "baseline/evaluations": table, "baseline/mean_goal_difference": mean_difference}, generation)
