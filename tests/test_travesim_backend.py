import random
import sys
from pathlib import Path
import tempfile
import unittest
import re

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from coach_silvs.formations import place_both_teams
from coach_silvs.travesim_backend import _team_scores, _telemetry_frames, render_match_world


class TraveSimBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        robots = []
        for team, x in (("Yellow", 0.3), ("Blue", -0.3)):
            for robot_id in range(3):
                robots.append(
                    "GenericVssRobot {\n"
                    f"  translation {x} {0.45 - robot_id * 0.45} 0.0025\n"
                    f'  name "{team}Robot{robot_id}"\n'
                    f'  teamName "{team.lower()}"\n'
                    f"  robotNumber {robot_id}\n"
                    "}\n"
                )
        # A minimal contract fixture keeps unit tests independent from a local
        # TraveSim checkout while exercising the real template transformation.
        self.template = (
            "#VRML_SIM R2025a utf8\n"
            "WorldInfo { basicTimeStep 10 }\n"
            "VssReferee {\n  robotsPerTeam 3\n}\n"
            "VssBall { translation 0 0 0.022 }\n"
            + "".join(robots)
        )

    def test_world_contains_private_ports_telemetry_and_noisy_formation(self) -> None:
        placements = place_both_teams(random.Random(7), yellow_side="right", formation="balanced")
        rendered = render_match_world(
            self.template,
            placements,
            Path("/tmp/real-match.jsonl"),
            3.0,
            22000,
            22001,
            22002,
            22003,
            client_sync_delay_ms=2,
        )
        self.assertIn("replacer_port 22000", rendered)
        self.assertIn("yellow_team_port 22001", rendered)
        self.assertIn("blue_team_port 22002", rendered)
        self.assertIn("multicast_port 22003", rendered)
        self.assertIn('telemetry_path "/tmp/real-match.jsonl"', rendered)
        self.assertIn("match_duration 3.000000", rendered)
        self.assertIn("external_client_delay_ms 2", rendered)
        self.assertIn('vision_interface_address "127.0.0.1"', rendered)
        for team in ("Yellow", "Blue"):
            for robot_id in range(3):
                self.assertIn(f'name "{team}Robot{robot_id}"', rendered)
        for team_key, team_name in (("yellow", "Yellow"), ("blue", "Blue")):
            for placement in placements[team_key]:
                block = re.search(
                    r"GenericVssRobot \{(?:(?!\nGenericVssRobot \{).)*?"
                    + f'name "{team_name}Robot{placement.robot_id}"'
                    + r"(?:(?!\nGenericVssRobot \{).)*?\n\}",
                    rendered,
                    re.DOTALL,
                )
                self.assertIsNotNone(block)
                expected = f"translation {placement.position.x:.9f} {placement.position.y:.9f} 0.0025"
                self.assertIn(expected, block.group(0))

    def test_telemetry_parser_adds_regulation_countdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            path.write_text(
                '{"type":"frame","time":0.25,"finished":false}\n'
                '{"type":"frame","time":1.0,"finished":true}\n',
                encoding="utf-8",
            )
            frames = _telemetry_frames(path, 1.0)
        self.assertEqual(frames[0]["time_remaining"], 0.75)
        self.assertEqual(frames[-1]["time_remaining"], 0.0)

    def test_team_scores_follow_teams_after_halftime_side_switch(self) -> None:
        frames = [
            {"period": 1, "goals_yellow": 1, "goals_blue": 2},
            {"period": 2, "goals_yellow": 4, "goals_blue": 3},
        ]
        self.assertEqual(_team_scores(frames), (2, 5))


if __name__ == "__main__":
    unittest.main()
