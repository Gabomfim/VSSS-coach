import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from coach_silvs.client import attack_sign_for_time, default_attack_sign, build_parser as build_client_parser
from coach_silvs.evolution import build_parser as build_evolution_parser
from coach_silvs.rules import ROBOCORE_VSSS_2025, get_ruleset


class CompetitionRulesTests(unittest.TestCase):
    def test_official_geometry_and_ball(self) -> None:
        rules = ROBOCORE_VSSS_2025
        self.assertEqual((rules.field_length, rules.field_width), (1.5, 1.3))
        self.assertEqual((rules.goal_width, rules.goal_depth), (0.4, 0.1))
        self.assertAlmostEqual(rules.ball_radius, 0.02135)
        self.assertEqual(rules.ball_mass, 0.046)
        self.assertEqual(rules.robots_max, 3)

    def test_official_regulation_time_is_two_five_minute_periods(self) -> None:
        rules = get_ruleset("robocore-vsss-2025")
        self.assertEqual(rules.periods, 2)
        self.assertEqual(rules.period_duration, 300.0)
        self.assertEqual(rules.regulation_duration, 600.0)

    def test_cli_defaults_record_the_official_ruleset(self) -> None:
        client = build_client_parser().parse_args([])
        evolution = build_evolution_parser().parse_args(["run"])
        self.assertEqual(client.ruleset, ROBOCORE_VSSS_2025.name)
        self.assertEqual(client.match_duration, ROBOCORE_VSSS_2025.regulation_duration)
        self.assertEqual(evolution.ruleset, ROBOCORE_VSSS_2025.name)
        self.assertEqual(evolution.telemetry_start_timeout, 60.0)
        self.assertEqual(evolution.telemetry_stall_timeout, 120.0)
        self.assertEqual(evolution.simulation_mode, "evolution")

    def test_attack_direction_is_mirrored_in_second_period(self) -> None:
        self.assertEqual(default_attack_sign(True), -1.0)
        self.assertEqual(default_attack_sign(False), 1.0)
        self.assertEqual(attack_sign_for_time(1.0, 600.0, 600.0), 1.0)
        self.assertEqual(attack_sign_for_time(1.0, 600.0, 301.0), 1.0)
        self.assertEqual(attack_sign_for_time(1.0, 600.0, 300.0), -1.0)
        self.assertEqual(attack_sign_for_time(-1.0, 600.0, 0.0), 1.0)


if __name__ == "__main__":
    unittest.main()
