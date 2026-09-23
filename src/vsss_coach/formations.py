"""Regulation-aware initial formations with bounded human placement error."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random

from .model import Vec2


@dataclass(frozen=True, slots=True)
class Formation:
    name: str
    positions: tuple[Vec2, Vec2, Vec2]


@dataclass(frozen=True, slots=True)
class RobotPlacement:
    robot_id: int
    position: Vec2
    orientation: float


# Templates are defined for a team defending the left goal and attacking +x.
# All robots remain in their own half and outside the centre circle, making the
# same catalogue safe for ordinary initial placement and defensive kickoffs.
FORMATIONS: tuple[Formation, ...] = (
    Formation("balanced", (Vec2(-0.64, 0.00), Vec2(-0.40, 0.23), Vec2(-0.27, -0.13))),
    Formation("defensive", (Vec2(-0.64, 0.00), Vec2(-0.49, 0.21), Vec2(-0.49, -0.21))),
    Formation("offensive", (Vec2(-0.64, 0.00), Vec2(-0.32, 0.25), Vec2(-0.24, -0.12))),
    Formation("wide", (Vec2(-0.64, 0.00), Vec2(-0.38, 0.40), Vec2(-0.30, -0.40))),
    Formation("compact", (Vec2(-0.64, 0.00), Vec2(-0.40, 0.11), Vec2(-0.28, -0.11))),
    Formation("diagonal", (Vec2(-0.64, 0.05), Vec2(-0.44, -0.20), Vec2(-0.27, 0.23))),
)
FORMATIONS_BY_NAME = {formation.name: formation for formation in FORMATIONS}
FORMATION_NAMES = tuple(FORMATIONS_BY_NAME)


def _bounded_gaussian(rng: random.Random, sigma: float, limit: float) -> float:
    return max(-limit, min(limit, rng.gauss(0.0, sigma)))


def select_formation(rng: random.Random, name: str = "random") -> Formation:
    """Choose an explicit formation or sample one uniformly."""
    if name == "random":
        return rng.choice(FORMATIONS)
    try:
        return FORMATIONS_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown formation: {name}") from exc


def place_team(
    rng: random.Random,
    side: str,
    formation: str = "random",
    position_sigma: float = 0.012,
    position_limit: float = 0.025,
    angle_sigma_degrees: float = 3.0,
    angle_limit_degrees: float = 7.0,
) -> tuple[str, tuple[RobotPlacement, ...]]:
    """Return a mirrored formation with small, reproducible human-like error."""
    if side not in {"left", "right"}:
        raise ValueError("side must be 'left' or 'right'")
    selected = select_formation(rng, formation)
    mirror = 1.0 if side == "left" else -1.0
    nominal_orientation = 0.0 if side == "left" else math.pi
    placements = []
    for robot_id, nominal in enumerate(selected.positions):
        x = mirror * nominal.x + _bounded_gaussian(rng, position_sigma, position_limit)
        y = nominal.y + _bounded_gaussian(rng, position_sigma, position_limit)
        # Robot centres stay inside the 1.50 x 1.30 m field and in their half.
        if side == "left":
            x = max(-0.71, min(-0.205, x))
        else:
            x = min(0.71, max(0.205, x))
        y = max(-0.61, min(0.61, y))
        angle_error = math.radians(_bounded_gaussian(rng, angle_sigma_degrees, angle_limit_degrees))
        placements.append(RobotPlacement(robot_id, Vec2(x, y), nominal_orientation + angle_error))
    return selected.name, tuple(placements)


def place_both_teams(
    rng: random.Random,
    yellow_side: str = "left",
    formation: str = "random",
    **noise: float,
) -> dict[str, object]:
    """Select formations independently whenever a random reset is requested."""
    blue_side = "right" if yellow_side == "left" else "left"
    yellow_name, yellow = place_team(rng, yellow_side, formation, **noise)
    blue_name, blue = place_team(rng, blue_side, formation, **noise)
    return {
        "yellow_formation": yellow_name,
        "blue_formation": blue_name,
        "yellow": yellow,
        "blue": blue,
        "ball": Vec2(),
    }
