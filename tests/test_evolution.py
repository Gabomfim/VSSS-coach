import argparse
import json
import sys
from pathlib import Path
import random
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from vsss_coach.evolution import MatchTask, apply_match_duration_curriculum, apply_result, baseline_candidate, baseline_variant, behavior_metrics, breed, build_parser, candidate_from_payload, candidate_payload, estimate_remaining_seconds, load_baseline_from_run, mock_match, next_generation, parse_match_duration_curriculum, player_contributions, random_candidate, run_evolution, run_match_with_retries, specialize_ball_players, tasks_for_generation


class EvolutionTests(unittest.TestCase):
    def test_match_duration_curriculum_expands_and_crosses_generations(self) -> None:
        schedule = parse_match_duration_curriculum("10x30s,10x1m,5x5m")
        self.assertEqual(schedule, (30.0,) * 10 + (60.0,) * 10 + (300.0,) * 5)
        candidates = [baseline_candidate("shared", 3), baseline_candidate("shared", 3)]
        candidates[1].candidate_id = "other"
        tasks = tasks_for_generation(candidates, 10, 3, 42)
        assigned = apply_match_duration_curriculum(tasks, schedule, 9, 600.0)
        self.assertEqual([task.match_duration for task in assigned], [30.0, 60.0, 60.0])

    def test_mock_match_uses_task_duration(self) -> None:
        home = candidate_payload(baseline_candidate("shared", 3))
        away = dict(home)
        away["candidate_id"] = "other"
        result = mock_match(MatchTask("short", 0, home, away, 0, 1, match_duration=30.0))
        self.assertEqual(result["match_duration"], 30.0)
        self.assertEqual(result["frames"][0]["time_remaining"], 30.0)
        self.assertEqual(result["frames"][-1]["time_remaining"], 0.0)

    def test_baseline_variant_keeps_gp_fields(self) -> None:
        base = baseline_candidate("shared", 3)
        variant = baseline_variant(0, 0, base, random.Random(9), self.limits())
        self.assertNotEqual(variant.genome, base.genome)
        self.assertEqual(variant.formula["fields"], base.formula["fields"])
        self.assertNotIn("roles", variant.formula)

    def test_resume_keeps_complete_matches_and_replaces_missing_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = build_parser().parse_args([
                "run", "--competitors", "2", "--generations", "1", "--workers", "1",
                "--matches-per-pair", "1", "--baseline-matches", "1",
                "--run-name", "resume-test", "--output", temporary,
            ])
            args.resume_run = None
            args.wandb_run_id = None
            run_dir = run_evolution(args)
            tournament_match = next(path for path in (run_dir / "matches").glob("*.json") if "baseline" not in path.name and ".summary." not in path.name)
            original = json.loads(tournament_match.read_text(encoding="utf-8"))
            tournament_match.unlink()
            (run_dir / "live.json").write_text(json.dumps({"status": "failed", "generation": 0}), encoding="utf-8")

            args.resume_run = str(run_dir)
            resumed = run_evolution(args)
            repaired = json.loads(tournament_match.read_text(encoding="utf-8"))

            self.assertEqual(resumed, run_dir)
            self.assertEqual(repaired["match_id"], original["match_id"])
            self.assertEqual(json.loads((run_dir / "live.json").read_text(encoding="utf-8"))["status"], "complete")
            baseline_rows = json.loads((run_dir / "baseline-evaluations.json").read_text(encoding="utf-8"))
            self.assertEqual(len(baseline_rows), 1)

    def test_failed_match_is_retried_and_partial_recording_is_replaced(self) -> None:
        import tempfile

        attempts = 0
        task = MatchTask("retry-me", 0, {"candidate_id": "home"}, {"candidate_id": "away"}, 0, 1)
        with tempfile.TemporaryDirectory() as temporary:
            partial = Path(temporary) / "retry-me.live.jsonl"

            def flaky_runner(match_task):
                nonlocal attempts
                attempts += 1
                partial.write_text(f"attempt {attempts}", encoding="utf-8")
                if attempts == 1:
                    raise RuntimeError("Webots crashed")
                return {"match_id": match_task.match_id}

            result = run_match_with_retries(
                task, match_runner=flaky_runner, matches_dir=temporary, max_retries=2
            )

            self.assertEqual(attempts, 2)
            self.assertEqual(result["attempt"], 2)
            self.assertEqual(result["retries"], 1)
            self.assertEqual(partial.read_text(encoding="utf-8"), "attempt 2")

    def test_fixed_baseline_uses_original_deterministic_fields(self) -> None:
        candidate = baseline_candidate("shared", 3)
        self.assertEqual(candidate.candidate_id, "baseline")
        self.assertTrue(candidate.formula["fixed_baseline"])
        self.assertEqual(candidate.formula["baseline_model"], "original_deterministic")
        self.assertEqual(candidate.formula["ball_field_model"], "ifac2008_univector_rotated")
        self.assertTrue(candidate.formula["ball_field"]["shared_structure_by_all_players"])
        self.assertTrue(candidate.formula["ball_field"]["independent_parameters_by_player"])
        self.assertNotIn("player_ball_fields", candidate.formula)
        self.assertNotIn("model", candidate.formula["fields"]["shared"]["ball"])
        self.assertIn("ball_goal_min_speed", candidate.genome)
        self.assertIn("shot_clearance_radius", candidate.genome)
        self.assertIn("shot_clearance_gain", candidate.genome)
        self.assertIn("ball_spiral_radius", candidate.genome)
        self.assertIn("ball_spiral_smoothing", candidate.genome)
        for player in range(3):
            self.assertIn(f"ball_spiral_radius_{player}", candidate.genome)
            self.assertIn(f"ball_spiral_smoothing_{player}", candidate.genome)
            self.assertIn(f"approach_gain_{player}", candidate.genome)

    def test_old_candidate_payload_receives_new_clearance_genes(self) -> None:
        payload = candidate_payload(baseline_candidate())
        for key in ("ball_goal_min_speed", "shot_clearance_radius", "shot_clearance_gain", "corner_gate_goal_distance", "corner_gate_crossing_band", "corner_gate_player_angle", "corner_gate_retreat_gain"):
            payload["genome"].pop(key)
        migrated = candidate_from_payload(payload)
        self.assertEqual(migrated.genome["ball_goal_min_speed"], 0.05)
        self.assertEqual(migrated.genome["shot_clearance_radius"], 0.14)
        self.assertEqual(migrated.genome["shot_clearance_gain"], 1.7)
        self.assertEqual(migrated.genome["corner_gate_goal_distance"], 0.45)
        self.assertEqual(migrated.genome["corner_gate_crossing_band"], 0.12)
        self.assertEqual(migrated.genome["corner_gate_player_angle"], 0.05)
        self.assertEqual(migrated.genome["corner_gate_retreat_gain"], 0.55)

    def test_baseline_from_old_run_seeds_player_specific_ball_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = candidate_payload(baseline_candidate())
            for player in range(3):
                source["genome"].pop(f"ball_spiral_radius_{player}")
                source["genome"].pop(f"ball_spiral_smoothing_{player}")
                source["genome"].pop(f"approach_gain_{player}")
            (directory / "ranking.json").write_text(json.dumps([source]), encoding="utf-8")
            baseline = load_baseline_from_run(directory)
            for player in range(3):
                self.assertEqual(baseline.genome[f"ball_spiral_radius_{player}"], baseline.genome["ball_spiral_radius"])
                self.assertEqual(baseline.genome[f"ball_spiral_smoothing_{player}"], baseline.genome["ball_spiral_smoothing"])
                self.assertEqual(baseline.genome[f"approach_gain_{player}"], baseline.genome["approach_gain"])
    def limits(self):
        return argparse.Namespace(
            max_depth=4,
            max_nodes=31,
            max_constants=8,
            max_constant=10.0,
            field_strategy="shared",
            robots_per_team=3,
        )

    def test_draw_is_penalized(self) -> None:
        candidate = random_candidate(0, 0, random.Random(1), self.limits())
        apply_result(candidate, 1, 1, 2.5)
        self.assertEqual(candidate.draws, 1)
        self.assertEqual(candidate.score, -2.5)

    def test_eta_uses_observed_parallel_throughput(self) -> None:
        self.assertIsNone(estimate_remaining_seconds(10.0, 0, 20))
        self.assertEqual(estimate_remaining_seconds(10.0, 5, 20), 30.0)
        self.assertEqual(estimate_remaining_seconds(10.0, 20, 20), 0.0)

    def test_behavior_metrics_detect_stationary_robot_and_goal_occupation(self) -> None:
        frames = [
            {"time": float(t), "yellow": [{"x": 0.8, "y": 0.0, "vx": 0.0, "vy": 0.0}]}
            for t in range(5)
        ]
        metrics = behavior_metrics(frames, "yellow", stationary_grace=2.0)
        self.assertGreater(metrics["stationary_seconds"], 0.0)
        self.assertGreater(metrics["goal_seconds"], 0.0)
        self.assertEqual(metrics["stuck_seconds"], 0.0)

    def test_breeding_preserves_genome_keys(self) -> None:
        rng = random.Random(2)
        first = random_candidate(0, 0, rng, self.limits())
        second = random_candidate(1, 0, rng, self.limits())
        child = breed(first, second, "uniform", "child", 1, rng)
        self.assertEqual(child.genome.keys(), first.genome.keys())

    def test_goal_is_attributed_to_last_ally_that_touched_ball(self) -> None:
        result = {
            "home": "home", "away": "away",
            "frames": [
                {"time": 0.0, "period": 1, "goals_yellow": 0, "goals_blue": 0,
                 "ball": [0.50, 0.0], "yellow": [[0.44, 0.0], [0.0, 0.3], [-0.3, 0.0]], "blue": [[0.7, 0.2]]},
                {"time": 0.1, "period": 1, "goals_yellow": 0, "goals_blue": 0,
                 "ball": [0.65, 0.0], "yellow": [[0.40, 0.0], [0.59, 0.0], [-0.3, 0.0]], "blue": [[0.7, 0.2]]},
                {"time": 0.2, "period": 1, "goals_yellow": 1, "goals_blue": 0,
                 "ball": [0.0, 0.0], "yellow": [[-0.3, 0.0], [-0.2, 0.0], [-0.1, 0.0]], "blue": [[0.3, 0.0]]},
            ],
        }
        contributions = player_contributions([result])
        self.assertEqual(contributions.get(("home", 1), {}).get("goals"), 1.0)
        self.assertEqual(contributions.get(("home", 0), {}).get("goals", 0.0), 0.0)

    def test_defense_requires_ally_touch_to_redirect_goal_bound_ball(self) -> None:
        result = {
            "home": "home", "away": "away",
            "frames": [
                {"time": 0.0, "period": 1, "ball": [0.20, 0.0], "yellow": [[0.0, 0.4], [0.0, -0.4]], "blue": []},
                {"time": 0.1, "period": 1, "ball": [0.40, 0.0], "yellow": [[0.40, 0.06], [0.0, -0.4]], "blue": []},
                {"time": 0.2, "period": 1, "ball": [0.41, 0.12], "yellow": [[0.41, 0.05], [0.0, -0.4]], "blue": []},
            ],
        }
        contributions = player_contributions([result])
        self.assertEqual(contributions.get(("home", 0), {}).get("defenses"), 1.0)
        self.assertEqual(contributions.get(("home", 1), {}).get("defenses", 0.0), 0.0)

    def test_specialized_children_archive_ball_player_lineage(self) -> None:
        rng = random.Random(22)
        ranking = [random_candidate(index, 0, rng, self.limits()) for index in range(2)]
        child = random_candidate(0, 1, rng, self.limits())
        specialize_ball_players([child], ranking, [], rng)
        lineage = child.formula["ball_player_specialization"]
        self.assertEqual([row["role"] for row in lineage], ["goalkeeper", "attacker", "assistant"])
        self.assertEqual(len(child.formula["player_ball_fields"]), 3)

    def test_stationary_ball_results_do_not_change_or_annotate_mutation(self) -> None:
        limits = build_parser().parse_args(["run"])
        ranking = [random_candidate(index, 0, random.Random(index), limits) for index in range(4)]
        ranking[0].formula["stationary_ball_mutation"] = {"severity": 1.0}
        stationary = [{"frames": [
            {"time": 0.0, "ball": [0.0, 0.0]},
            {"time": 0.2, "ball": [0.0, 0.0]},
        ]}]
        children = next_generation(ranking, limits, 1, random.Random(9), stationary)
        self.assertTrue(children)
        self.assertTrue(all("stationary_ball_mutation" not in child.formula for child in children))

    def test_candidates_evolve_positive_player_return_parameters(self) -> None:
        for index in range(4):
            candidate = random_candidate(index, 0, random.Random(index), self.limits())
            for player in range(3):
                self.assertGreater(candidate.genome[f"ball_return_gain_{player}"], 0)
                self.assertIn(candidate.genome[f"ball_return_power_{player}"], {1.0, 2.0})

    def test_old_role_fields_are_removed_during_migration(self) -> None:
        payload = candidate_payload(baseline_candidate("shared", 3))
        payload["formula"]["roles"] = ["attacker", "defender", "attacker"]
        payload["formula"]["fields"]["shared"]["attacker_ball"] = {}
        payload["formula"]["fields"]["shared"]["defender_ball"] = {}
        restored = candidate_from_payload(payload)
        self.assertNotIn("roles", restored.formula)
        self.assertNotIn("attacker_ball", restored.formula["fields"]["shared"])
        self.assertNotIn("defender_ball", restored.formula["fields"]["shared"])

    def test_individual_strategy_builds_one_field_set_per_ally(self) -> None:
        limits = self.limits()
        limits.field_strategy = "individual"
        candidate = random_candidate(0, 0, random.Random(3), limits)
        self.assertEqual(set(candidate.formula["fields"]), {"ally_0", "ally_1", "ally_2"})
        self.assertEqual(candidate.formula["self_field"], "excluded")

    def test_shared_strategy_separates_ally_and_enemy_fields(self) -> None:
        candidate = random_candidate(0, 0, random.Random(4), self.limits())
        fields = candidate.formula["fields"]["shared"]
        self.assertIn("ally", fields)
        self.assertIn("enemy", fields)
        self.assertIsNot(fields["ally"], fields["enemy"])

    def test_mock_replay_animates_robots_inside_the_official_field(self) -> None:
        rng = random.Random(5)
        home = random_candidate(0, 0, rng, self.limits())
        away = random_candidate(1, 0, rng, self.limits())
        result = mock_match(MatchTask("match", 0, candidate_payload(home), candidate_payload(away), 0, 9))
        first, later = result["frames"][0], result["frames"][20]
        self.assertNotEqual(first["yellow"], later["yellow"])
        self.assertNotEqual(first["blue"], later["blue"])
        for frame in result["frames"]:
            self.assertLessEqual(abs(frame["ball"][0]), 0.75)
            self.assertLessEqual(abs(frame["ball"][1]), 0.65)


if __name__ == "__main__":
    unittest.main()
