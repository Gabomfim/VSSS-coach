import random
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from vsss_coach.formula import (
    evaluate_expression,
    expression_stats,
    expression_text,
    formula_block,
    random_expression,
)


class FormulaTests(unittest.TestCase):
    def test_expression_is_serializable_readable_and_executable(self) -> None:
        expression = {
            "op": "clip", "min": -3, "max": 3,
            "arg": {"op": "add", "args": [
                {"const": 1},
                {"op": "mul", "args": [{"const": 0.5}, {"feature": "goal_difference"}]},
            ]},
        }
        value = evaluate_expression(expression, {"goal_difference": -1.0})
        self.assertAlmostEqual(value, 0.5)
        self.assertIn("goal_difference", expression_text(expression))
        depth, nodes, constants = expression_stats(expression)
        self.assertGreaterEqual(depth, 3)
        self.assertGreaterEqual(nodes, 5)
        self.assertEqual(constants, 2)

    def test_generated_block_contains_tree_text_and_complexity(self) -> None:
        expression = random_expression(random.Random(7), 5, 10.0)
        block = formula_block(expression)
        self.assertIn("expression", block)
        self.assertIn("text", block)
        self.assertGreater(block["nodes"], 1)
        self.assertGreater(block["depth"], 1)

    def test_unknown_or_non_finite_operations_fail_safe(self) -> None:
        self.assertEqual(evaluate_expression({"op": "unknown"}, {}), 1.0)
        self.assertEqual(evaluate_expression({"const": float("inf")}, {}), 0.0)


if __name__ == "__main__":
    unittest.main()
