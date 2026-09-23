import argparse
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from vsss_coach.evolution import build_parser
from vsss_coach.wandb_tracking import WandbTracker


class WandbTrackingTests(unittest.TestCase):
    def test_disabled_tracker_never_requires_the_sdk(self) -> None:
        tracker = WandbTracker(argparse.Namespace(wandb_mode="disabled"), {})
        self.assertIsNone(tracker.run)

    def test_wandb_tracking_defines_live_genome_and_vector_field_tables(self) -> None:
        source = Path(__file__).parents[1] / "src" / "vsss_coach" / "wandb_tracking.py"
        contents = source.read_text(encoding="utf-8")
        self.assertIn('"genomes": genome_table', contents)
        self.assertIn('"vector_fields": field_table', contents)
        self.assertIn("field_rows", contents)
        self.assertIn('"formula", "formula_json"', contents)
        self.assertIn('"formulas.json"', contents)

    def test_cli_defaults_to_disabled_and_safe_upload_scope(self) -> None:
        args = build_parser().parse_args(["run"])
        self.assertEqual(args.wandb_mode, "disabled")
        self.assertEqual(args.wandb_project, "vsss-coach")
        self.assertFalse(args.wandb_log_matches)


if __name__ == "__main__":
    unittest.main()
