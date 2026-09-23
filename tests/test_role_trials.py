import json
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from vsss_coach.role_dashboard import HTML, estimate_eta, render_replay_world
from vsss_coach.client import (
    GoalkeeperSpinTracker, StateEstimator, build_parser as build_client_parser,
)
from vsss_coach.controller import VectorFieldStrategy
from vsss_coach.role_trials import (
    BOUNDS, RoleCandidate, build_parser, evaluate_trial, generate_scenarios, run,
    scenario_is_reachable, speed_profile, surrogate_refine_mean,
)
from vsss_coach.role_travesim import RoleTraveSimConfig, run_physical_trial
from vsss_coach.goalkeeper_compare import compose, synchronize_shots, training_replay
from vsss_coach.fields import (
    ally_goal_ball_corridor_field, earliest_reachable_interception_field,
    interception_crossing_time, interception_speed, predictive_interception_field,
)
from vsss_coach.model import BallState, RobotState, Vec2


class RoleTrialTests(unittest.TestCase):
    def test_comparison_uses_original_training_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "replays"
            source.mkdir()
            recorded = {"candidate_id": "g0000-c0001", "loss": 0.0,
                        "frames": [{"period": 1, "time": 0.0, "ball": {"x": 0.1}},
                                   {"period": 1, "time": 0.1, "ball": {"x": 0.2}}]}
            (source / "generation-0000.json").write_text(json.dumps(recorded), encoding="utf-8")
            replay = training_replay(Path(directory), 0, "g0000-c0001", 1, 1.0)
            self.assertEqual(replay["loss"], 0.0)
            self.assertEqual(len(replay["frames"]), 30)
            self.assertEqual(replay["frames"][-1]["ball"]["x"], 0.2)
            with self.assertRaisesRegex(ValueError, "does not contain"):
                training_replay(Path(directory), 0, "another-candidate", 1, 1.0)

    def test_comparison_rejects_unknown_layout(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported layout"):
            compose(Path("before.mp4"), Path("after.mp4"), Path("out.mp4"), 3.0, "diagonal")

    def test_comparison_shots_are_equal_length_without_interpolation(self) -> None:
        frames = [
            {"period": 1, "time": 0.0, "ball": {"x": 0.0}},
            {"period": 1, "time": 0.4, "ball": {"x": 1.0}},
            {"period": 2, "time": 0.0, "ball": {"x": 2.0}},
            {"period": 2, "time": 0.9, "ball": {"x": 3.0}},
        ]
        aligned = synchronize_shots(frames, 2, 1.0, fps=10)
        self.assertEqual(len(aligned), 20)
        self.assertEqual([row["period"] for row in aligned].count(1), 10)
        self.assertEqual([row["period"] for row in aligned].count(2), 10)
        self.assertEqual(aligned[9]["ball"]["x"], 1.0)
        self.assertEqual(aligned[19]["ball"]["x"], 3.0)

    def test_physical_trial_retries_a_webots_timeout(self) -> None:
        config = RoleTraveSimConfig(".", "webots", retries=2)
        expected = {"candidate_id": "candidate", "loss": 0.1}
        with patch(
            "vsss_coach.role_travesim._run_physical_trial_once",
            side_effect=[__import__("subprocess").TimeoutExpired("webots", 1), expected],
        ) as attempt:
            self.assertEqual(run_physical_trial(("goalkeeper", None, [], 0, config)), expected)
            self.assertEqual(attempt.call_count, 2)

    def test_physical_trial_retries_transient_world_template_read(self) -> None:
        config = RoleTraveSimConfig(".", "webots", retries=1)
        expected = {"candidate_id": "candidate", "loss": 0.1}
        with patch(
            "vsss_coach.role_travesim._run_physical_trial_once",
            side_effect=[ValueError("VssReferee block not found in world template"), expected],
        ) as attempt:
            self.assertEqual(run_physical_trial(("goalkeeper", None, [], 0, config)), expected)
            self.assertEqual(attempt.call_count, 2)

    def test_role_trial_cli_exposes_resume_and_physical_retries(self) -> None:
        args = build_parser().parse_args(["--role", "goalkeeper", "--resume", "--physical-retries", "3"])
        self.assertTrue(args.resume)
        self.assertEqual(args.physical_retries, 3)

    def test_generation_is_reproducible_and_shared(self) -> None:
        first = generate_scenarios("defender", 60, 91)
        second = generate_scenarios("defender", 60, 91)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 60)
        self.assertTrue(all(vx < 0 for vx, _ in (row.ball_velocity for row in first)))

    def test_defender_shots_vary_speed_and_aim_inside_ally_goal(self) -> None:
        scenarios = generate_scenarios("defender", 30, 91)
        speeds = {round((vx * vx + vy * vy) ** 0.5, 2) for vx, vy in (s.ball_velocity for s in scenarios)}
        targets = []
        for scenario in scenarios:
            bx, by = scenario.ball
            vx, vy = scenario.ball_velocity
            target_y = by + vy * ((-0.75 - bx) / vx)
            targets.append(target_y)
        self.assertEqual(len(speeds), 5)
        self.assertEqual(min(speeds), 0.25)
        self.assertEqual(max(speeds), 1.25)
        self.assertTrue(all(-0.18 <= target <= 0.18 for target in targets))
        self.assertGreater(max(targets) - min(targets), 0.25)

    def test_generated_defender_scenarios_are_physically_reachable(self) -> None:
        scenarios = generate_scenarios("defender", 60, 916, duration=3.0)
        self.assertTrue(all(scenario_is_reachable(scenario) for scenario in scenarios))

    def test_goalkeeper_starts_in_front_of_goal_with_faster_shots(self) -> None:
        scenarios = generate_scenarios("goalkeeper", 30, 919, duration=3.0)
        speeds = {
            round((scenario.ball_velocity[0] ** 2 + scenario.ball_velocity[1] ** 2) ** 0.5, 4)
            for scenario in scenarios
        }
        self.assertEqual(speeds, {0.75, 1.0625, 1.375, 1.6875, 2.0})
        self.assertTrue(all(abs(scenario.player[0] + 0.69) < 1e-9 for scenario in scenarios))
        self.assertTrue(all(abs(scenario.player_yaw - math.pi / 2) < 1e-9 for scenario in scenarios))
        self.assertTrue(all(-0.18 <= scenario.player[1] <= 0.18 for scenario in scenarios))
        self.assertTrue(all(scenario_is_reachable(scenario) for scenario in scenarios))

    def test_physical_client_accepts_goalkeeper_role(self) -> None:
        args = build_client_parser().parse_args(["--role", "goalkeeper"])
        self.assertEqual(args.role, "goalkeeper")

    def test_goalkeeper_reverses_on_line_without_turning_around(self) -> None:
        strategy = VectorFieldStrategy()
        robot = RobotState(position=Vec2(-0.60, 0.10), orientation=1.5707963267948966)
        upward = strategy.wheel_command_for_line_motion(robot, Vec2(-0.60, 0.20), 1.0)
        downward = strategy.wheel_command_for_line_motion(robot, Vec2(-0.60, -0.20), 1.0)
        self.assertGreater(upward[0], 0.0)
        self.assertGreater(upward[1], 0.0)
        self.assertLess(downward[0], 0.0)
        self.assertLess(downward[1], 0.0)
        self.assertAlmostEqual(upward[0], upward[1])
        self.assertAlmostEqual(downward[0], downward[1])

    def test_goalkeeper_rotates_in_place_until_parallel_to_goal_line(self) -> None:
        strategy = VectorFieldStrategy()
        target = Vec2(-0.60, 0.20)
        for orientation in (0.0, 0.4, -0.7, math.pi, 2.8):
            left, right = strategy.wheel_command_for_line_motion(
                RobotState(position=Vec2(-0.60, 0.0), orientation=orientation), target, 1.0
            )
            self.assertAlmostEqual(left, -right)

    def test_goalkeeper_corrects_small_heading_drift_while_translating(self) -> None:
        strategy = VectorFieldStrategy()
        left, right = strategy.wheel_command_for_line_motion(
            RobotState(position=Vec2(-0.60, 0.0), orientation=math.pi / 2 + math.radians(3)),
            Vec2(-0.60, 0.20), 1.0,
        )
        self.assertGreater(left, 0.0)
        self.assertGreater(right, 0.0)
        self.assertNotAlmostEqual(left, right)

    def test_goalkeeper_recovers_cross_track_error_before_following_line(self) -> None:
        strategy = VectorFieldStrategy()
        target = Vec2(-0.69, 0.20)
        turning = strategy.wheel_command_for_line_motion(
            RobotState(position=Vec2(-0.55, 0.0), orientation=math.pi / 2), target, 1.0
        )
        self.assertAlmostEqual(turning[0], -turning[1])
        recovering = strategy.wheel_command_for_line_motion(
            RobotState(position=Vec2(-0.55, 0.0), orientation=math.pi), target, 1.0
        )
        self.assertAlmostEqual(recovering[0], recovering[1])
        self.assertGreater(recovering[0], 0.0)

    def test_goalkeeper_interception_line_stays_close_to_goal(self) -> None:
        self.assertEqual(BOUNDS["intercept_offset"], (0.045, 0.09))

    def test_goalkeeper_spin_runs_once_per_contact_until_released(self) -> None:
        tracker = GoalkeeperSpinTracker()
        self.assertTrue(tracker.update(True, 0.0, 1.0))
        self.assertTrue(tracker.update(True, 1.6, 1.0))
        self.assertFalse(tracker.update(True, 3.1, 1.0))
        self.assertFalse(tracker.update(True, 3.1, 1.0))
        self.assertFalse(tracker.update(False, 3.1, 1.0, released=True))
        self.assertTrue(tracker.update(True, 3.1, -1.0))
        self.assertEqual(tracker.direction, -1.0)
        tracker.reset()
        self.assertTrue(tracker.armed)
        self.assertFalse(tracker.active)
        self.assertIsNone(tracker.previous_orientation)

    def test_goalkeeper_spin_waits_for_evolved_contact_delay(self) -> None:
        tracker = GoalkeeperSpinTracker()
        self.assertFalse(tracker.update(True, math.pi / 2, timestamp=1.0, delay_seconds=0.12))
        self.assertTrue(tracker.pending)
        self.assertFalse(tracker.update(False, math.pi / 2, released=True, timestamp=1.10, delay_seconds=0.12))
        self.assertFalse(tracker.armed)
        self.assertTrue(tracker.update(False, math.pi / 2, released=True, timestamp=1.12, delay_seconds=0.12))
        self.assertFalse(tracker.pending)
        self.assertFalse(tracker.update(False, math.pi / 2 + math.pi, timestamp=1.20, delay_seconds=0.12))

    def test_goalkeeper_contact_delay_is_evolved_in_milliseconds(self) -> None:
        self.assertEqual(BOUNDS["contact_spin_delay"], (0.02, 0.30))
        candidate = RoleCandidate("keeper", .1, .1, .4, 1.5, .1, .8, contact_spin_delay=.175)
        self.assertEqual(candidate.contact_spin_delay * 1000, 175)

    def test_impossible_last_moment_shot_is_rejected(self) -> None:
        from vsss_coach.role_trials import Scenario
        impossible = Scenario(0, (-0.70, 0.0), (0.70, 0.50), (-1.25, 0.0), 3.0)
        self.assertFalse(scenario_is_reachable(impossible))

    def test_speed_increases_smoothly_with_distance(self) -> None:
        candidate = RoleCandidate("c", .11, .08, .3, 2.0, .1, 1.0)
        speeds = [speed_profile(candidate, value) for value in (0, .1, .5, 1, 2)]
        self.assertEqual(speeds, sorted(speeds))
        self.assertEqual(speeds[0], .3)
        self.assertEqual(speeds[-1], 2.0)

    def test_predictive_field_targets_future_goal_line_crossing(self) -> None:
        robot = RobotState(position=Vec2(-0.2, 0.3))
        ball = BallState(position=Vec2(0.2, 0.1), velocity=Vec2(-0.5, -0.1))
        direction, target = predictive_interception_field(
            robot, ball, -0.75, 0.4, 1.0, 2.0, 0.12, 0.04, 0.08,
        )
        self.assertIsNotNone(target)
        self.assertAlmostEqual(target.x, -0.63)
        self.assertLess(target.y, ball.position.y)
        self.assertAlmostEqual(direction.norm(), 1.0)

    def test_predictive_field_is_inactive_when_ball_moves_away(self) -> None:
        direction, target = predictive_interception_field(
            RobotState(position=Vec2()), BallState(position=Vec2(), velocity=Vec2(0.5, 0)), -0.75, 0.4,
            1.0, 2.0, 0.12, 0.04, 0.08,
        )
        self.assertEqual(direction, Vec2())
        self.assertIsNone(target)

    def test_acceleration_changes_predicted_crossing_time_and_target(self) -> None:
        accelerating = BallState(Vec2(0.0, 0.0), Vec2(-0.2, 0.1), Vec2(-0.6, 0.2))
        constant = BallState(Vec2(0.0, 0.0), Vec2(-0.2, 0.1), Vec2())
        accelerated_time = interception_crossing_time(accelerating, -0.5, 3.0)
        constant_time = interception_crossing_time(constant, -0.5, 3.0)
        self.assertLess(accelerated_time, constant_time)
        _, target = predictive_interception_field(
            RobotState(position=Vec2(-0.2, 0.0)), accelerating,
            -0.75, 0.4, 1.0, 3.0, 0.25, 0.0, 0.08,
        )
        self.assertIsNotNone(target)
        self.assertGreater(target.y, accelerating.position.y + accelerating.velocity.y * accelerated_time)

    def test_interception_speed_uses_velocity_acceleration_and_reachability(self) -> None:
        slow = BallState(Vec2(), Vec2(-0.2, 0.0), Vec2())
        fast = BallState(Vec2(), Vec2(-1.0, 0.0), Vec2(-0.8, 0.0))
        slow_command = interception_speed(.3, .5, 1.0, slow, 1, .1, 1, .5, .1, 1.7)
        fast_command = interception_speed(.3, .5, 1.0, fast, 1, .1, 1, .5, .1, 1.7)
        self.assertGreater(fast_command, slow_command)
        self.assertLessEqual(fast_command, 1.7)

    def test_defender_targets_earliest_reachable_point_not_goal_line(self) -> None:
        robot = RobotState(position=Vec2(0.15, 0.02), velocity=Vec2())
        ball = BallState(position=Vec2(0.35, 0.0), velocity=Vec2(-0.6, 0.0))
        direction, target, elapsed = earliest_reachable_interception_field(
            robot, ball, -0.75, 0.40, 1.0, 2.5, 0.08, 1.70,
        )
        self.assertIsNotNone(target)
        self.assertGreater(target.x, -0.63)  # old goalkeeper defensive line
        self.assertLess(elapsed, (0.35 - (-0.63)) / 0.6)
        self.assertGreater(direction.norm(), 0.99)

    def test_defensive_corridor_attracts_robot_between_ball_and_ally_goal(self) -> None:
        robot = RobotState(position=Vec2(-0.20, 0.30))
        ball = BallState(position=Vec2(0.30, 0.0), velocity=Vec2(-0.5, 0.0))
        direction, target = ally_goal_ball_corridor_field(
            robot, ball, -0.75, 0.40, 1.0, 3.0, 0.08,
        )
        self.assertIsNotNone(target)
        self.assertAlmostEqual(target.y, 0.0)
        self.assertLess(direction.y, 0.0)
        self.assertGreaterEqual(target.x, -0.75)
        self.assertLessEqual(target.x, ball.position.x)

    def test_acceleration_estimator_filters_using_supplied_simulation_time(self) -> None:
        estimator = StateEstimator()
        self.assertEqual(estimator.acceleration("ball", Vec2(), 0.0), Vec2())
        first = estimator.acceleration("ball", Vec2(1.0, 0.0), 0.1)
        second = estimator.acceleration("ball", Vec2(1.0, 0.0), 0.2)
        self.assertAlmostEqual(first.x, 2.5)
        self.assertAlmostEqual(second.x, 1.875)

    def test_trial_reports_every_loss_component(self) -> None:
        candidate = RoleCandidate("c", .11, .08, .3, 2.0, .1, 1.0)
        result = evaluate_trial("goalkeeper", candidate, generate_scenarios("goalkeeper", 4, 3, 1.0))
        self.assertEqual(set(result["loss_components"]), {"outcome", "interception", "reaction_time", "redirection", "total"})
        self.assertEqual(len(result["episodes"]), 4)

    def test_small_run_writes_dashboard_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = build_parser().parse_args(["--role", "attacker", "--candidates", "4", "--scenarios", "3", "--generations", "2", "--workers", "1", "--output", temporary, "--run-name", "smoke"])
            directory = run(args)
            self.assertEqual(json.loads((directory / "live.json").read_text())["status"], "complete")
            self.assertEqual(len(list((directory / "generations").glob("*.json"))), 2)
            replay = json.loads((directory / "replays" / "generation-0000.json").read_text())
            self.assertEqual(replay["frames"], [])
            self.assertEqual(replay["generation"], 0)

    def test_resume_reuses_exact_population_and_complete_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = build_parser().parse_args([
                "--role", "goalkeeper", "--backend", "proxy", "--candidates", "4",
                "--scenarios", "3", "--generations", "3", "--workers", "1",
                "--output", temporary, "--run-name", "resume-smoke", "--seed", "7",
            ])
            directory = run(args)
            saved = json.loads((directory / "checkpoints" / "generation-0002" / "population.json").read_text())
            args.resume = True
            self.assertEqual(run(args), directory)
            (directory / "generations" / "generation-0002.json").unlink()
            self.assertEqual(run(args), directory)
            resumed = json.loads((directory / "checkpoints" / "generation-0002" / "population.json").read_text())
            self.assertEqual(resumed, saved)

    def test_role_dashboard_has_total_and_component_charts(self) -> None:
        self.assertIn('id="loss"', HTML)
        self.assertIn('id="components"', HTML)
        self.assertIn("Backend", HTML)
        for component in ("outcome", "interception", "reaction_time", "redirection"):
            self.assertIn(component, HTML)

    def test_single_generation_chart_draws_visible_markers(self) -> None:
        self.assertIn("x.arc(q.px,q.py,6", HTML)
        self.assertIn("s.values.length===1?w/2", HTML)

    def test_dashboard_can_replay_best_defender_by_generation(self) -> None:
        self.assertIn('id="replayGeneration"', HTML)
        self.assertIn('id="replayTimeline"', HTML)
        self.assertIn('/api/replay?generation=', HTML)
        self.assertIn('replayIndex+3', HTML)
        self.assertIn('id="openWebots"', HTML)
        self.assertIn("earliest interception", HTML)
        self.assertIn("ball a=", HTML)
        self.assertIn("role+'_field'", HTML)
        self.assertIn("Math.cos(r.orientation||0)", HTML)

    def test_webots_replay_world_uses_recording_and_disables_robot_controllers(self) -> None:
        template = '''#VRML_SIM R2025a utf8
VssReferee {
  robotsPerTeam 3
}
GenericVssRobot {
  controller "vss_robot_controller"
}
'''
        world = render_replay_world(template, Path("/tmp/replay.json"))
        self.assertIn('controller "role_replay_controller"', world)
        self.assertIn('role_scenarios_path "/tmp/replay.json"', world)
        self.assertIn('controller "<none>"', world)

    def test_eta_uses_observed_parallel_throughput(self) -> None:
        config = {"candidates": 16, "generations": 8, "workers": 4, "scenarios": 30, "scenario_duration": 3}
        eta = estimate_eta(config, {"generation": 0, "completed": 4, "status": "running"}, 100, 200, 210)
        self.assertEqual(eta["source"], "observed")
        self.assertAlmostEqual(eta["generation_eta_seconds"], 290)
        self.assertAlmostEqual(eta["run_eta_seconds"], 3090)

    def test_eta_is_zero_after_completion(self) -> None:
        eta = estimate_eta({}, {"status": "complete"}, 0, 0, 1)
        self.assertEqual(eta["generation_eta_seconds"], 0)
        self.assertEqual(eta["run_eta_seconds"], 0)

    def test_surrogate_step_moves_mean_toward_lower_loss_samples(self) -> None:
        mean = {key: (low + high) / 2 for key, (low, high) in BOUNDS.items()}
        sigma = {key: (high - low) * 0.2 for key, (low, high) in BOUNDS.items()}
        low = RoleCandidate("low", **mean)
        high_values = dict(mean)
        high_values["intercept_blend"] += sigma["intercept_blend"]
        high = RoleCandidate("high", **high_values)
        refined = surrogate_refine_mean(
            [{"candidate_id": "low", "loss": 0.2}, {"candidate_id": "high", "loss": 0.8}],
            {"low": low, "high": high}, mean, sigma,
        )
        self.assertLess(refined["intercept_blend"], mean["intercept_blend"])

    def test_physical_backend_options_are_exposed(self) -> None:
        args = build_parser().parse_args([
            "--role", "defender", "--backend", "travesim",
            "--webots-mode", "fast", "--port-base", "32000",
        ])
        self.assertEqual(args.backend, "travesim")
        self.assertEqual(args.port_base, 32000)
        self.assertTrue(args.feasible_scenarios)


if __name__ == "__main__":
    unittest.main()
