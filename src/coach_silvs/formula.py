"""Serializable and safely executable scalar GP expressions for vector fields."""

from __future__ import annotations

import math
import random
from typing import Any, Mapping


FEATURES = ("distance", "time_remaining", "goal_difference", "attack_sign")


def evaluate_expression(expression: Mapping[str, Any] | None, context: Mapping[str, float]) -> float:
    """Evaluate a bounded expression tree without Python eval()."""
    if not expression:
        return 1.0
    if "const" in expression:
        value = float(expression["const"])
    elif "feature" in expression:
        value = float(context.get(str(expression["feature"]), 0.0))
    else:
        op = expression.get("op")
        args = expression.get("args", [])
        if op == "add":
            value = sum(evaluate_expression(arg, context) for arg in args)
        elif op == "mul":
            value = math.prod(evaluate_expression(arg, context) for arg in args)
        elif op == "neg":
            value = -evaluate_expression(expression.get("arg"), context)
        elif op == "tanh":
            value = math.tanh(evaluate_expression(expression.get("arg"), context))
        elif op == "clip":
            inner = evaluate_expression(expression.get("arg"), context)
            value = max(float(expression.get("min", -3.0)), min(float(expression.get("max", 3.0)), inner))
        else:
            value = 1.0
    return value if math.isfinite(value) else 0.0


def expression_text(expression: Mapping[str, Any] | None) -> str:
    if not expression:
        return "1"
    if "const" in expression:
        return f"{float(expression['const']):.4g}"
    if "feature" in expression:
        return str(expression["feature"])
    op = expression.get("op")
    if op in {"add", "mul"}:
        symbol = " + " if op == "add" else " · "
        return "(" + symbol.join(expression_text(arg) for arg in expression.get("args", [])) + ")"
    if op == "neg":
        return f"-({expression_text(expression.get('arg'))})"
    if op == "tanh":
        return f"tanh({expression_text(expression.get('arg'))})"
    if op == "clip":
        return f"clip({expression_text(expression.get('arg'))}, {expression.get('min', -3)}, {expression.get('max', 3)})"
    return "1"


def expression_stats(expression: Mapping[str, Any] | None) -> tuple[int, int, int]:
    if not expression:
        return 1, 1, 0
    children = list(expression.get("args", []))
    if "arg" in expression:
        children.append(expression["arg"])
    child_stats = [expression_stats(child) for child in children]
    depth = 1 + max((item[0] for item in child_stats), default=0)
    nodes = 1 + sum(item[1] for item in child_stats)
    constants = int("const" in expression) + sum(item[2] for item in child_stats)
    return depth, nodes, constants


def random_expression(rng: random.Random, max_depth: int, max_constant: float) -> dict[str, Any]:
    """Create a stable GP gain centered around one with state-dependent terms."""
    feature = rng.choice(FEATURES)
    coefficient = rng.uniform(-min(1.0, max_constant), min(1.0, max_constant))
    inner: dict[str, Any] = {
        "op": "add",
        "args": [
            {"const": 1.0},
            {"op": "mul", "args": [{"const": coefficient}, {"feature": feature}]},
        ],
    }
    if max_depth >= 4 and rng.random() < 0.5:
        inner = {"op": "tanh", "arg": inner}
    return {"op": "clip", "min": -3.0, "max": 3.0, "arg": inner}


def formula_block(expression: Mapping[str, Any]) -> dict[str, Any]:
    depth, nodes, constants = expression_stats(expression)
    return {
        "expression": dict(expression),
        "text": expression_text(expression),
        "depth": depth,
        "nodes": nodes,
        "constants": constants,
    }
