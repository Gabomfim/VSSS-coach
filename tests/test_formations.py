import math
import random
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from vsss_coach.formations import FORMATION_NAMES, place_both_teams, place_team


class FormationTests(unittest.TestCase):
    def test_catalogue_contains_distinct_tactical_patterns(self) -> None:
        self.assertEqual(
            set(FORMATION_NAMES),
            {"balanced", "defensive", "offensive", "wide", "compact", "diagonal"},
        )

    def test_seed_makes_random_placement_reproducible(self) -> None:
        self.assertEqual(place_both_teams(random.Random(12)), place_both_teams(random.Random(12)))

    def test_human_error_is_bounded_and_robots_remain_in_own_half(self) -> None:
        _, left = place_team(random.Random(2), "left", "balanced")
        _, right = place_team(random.Random(2), "right", "balanced")
        for placement in left:
            self.assertGreaterEqual(placement.position.x, -0.71)
            self.assertLessEqual(placement.position.x, -0.205)
            self.assertLessEqual(abs(placement.position.y), 0.61)
            self.assertLessEqual(abs(placement.orientation), math.radians(7.0))
        for placement in right:
            self.assertGreaterEqual(placement.position.x, 0.205)
            self.assertLessEqual(abs(placement.position.y), 0.61)
            self.assertLessEqual(abs(placement.orientation - math.pi), math.radians(7.0))

    def test_random_mode_selects_a_catalogued_formation_for_each_team(self) -> None:
        placement = place_both_teams(random.Random(42), formation="random")
        self.assertIn(placement["yellow_formation"], FORMATION_NAMES)
        self.assertIn(placement["blue_formation"], FORMATION_NAMES)
        self.assertEqual(len(placement["yellow"]), 3)
        self.assertEqual(len(placement["blue"]), 3)


if __name__ == "__main__":
    unittest.main()
