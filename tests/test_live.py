import argparse
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from coach_silvs.live import build_parser, evolution_command, mark_run_failed


class LiveLauncherTests(unittest.TestCase):
    def test_launcher_enforces_real_backend_and_run_directory(self) -> None:
        args = argparse.Namespace(output=Path("runs"), run_name="physical")
        command = evolution_command(args, ["--competitors", "2", "--backend", "mock"])
        self.assertEqual(command[-6:], ["--backend", "travesim", "--output", "runs", "--run-name", "physical"])

    def test_background_mode_is_available(self) -> None:
        args = build_parser().parse_args(["--background"])
        self.assertTrue(args.background)

    def test_failed_evolution_is_visible_in_live_status(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "live.json").write_text('{"status":"running"}', encoding="utf-8")
            mark_run_failed(run, 1)
            payload = json.loads((run / "live.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertIn("status 1", payload["failure"])


if __name__ == "__main__":
    unittest.main()
