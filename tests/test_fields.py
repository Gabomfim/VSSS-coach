import math
from dataclasses import replace
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from coach_silvs.controller import StrategyConfig, VectorFieldStrategy
from coach_silvs.fields import closest_approach, segment_field, univector_ball_field
from coach_silvs.model import BallState, RobotState, Segment, Vec2, WorldState


class GeometryTests(unittest.TestCase):
    def test_closest_point_is_clamped_to_segment(self) -> None:
        segment = Segment(Vec2(0.0, 0.0), Vec2(1.0, 0.0))
        self.assertEqual(segment.closest_point(Vec2(2.0, 1.0)), Vec2(1.0, 0.0))

    def test_segment_field_pushes_away(self) -> None:
        field = segment_field(Vec2(0.5, 0.1), Segment(Vec2(0.0, 0.0), Vec2(1.0, 0.0)), 0.2, 1.0)
        self.assertGreater(field.y, 0.0)

    def test_segment_field_is_zero_outside_wall_influence_region(self) -> None:
        wall = Segment(Vec2(0.0, 0.0), Vec2(1.0, 0.0))
        self.assertEqual(segment_field(Vec2(0.5, 0.21), wall, 0.2, 1.0), Vec2())

    def test_closest_approach_detects_crossing(self) -> None:
        robot = RobotState(position=Vec2(0.0, 0.0), velocity=Vec2(1.0, 0.0))
        obstacle = RobotState(position=Vec2(1.0, 1.0), velocity=Vec2(0.0, -1.0))
        time_to_cpa, distance = closest_approach(robot, obstacle, 2.0)
        self.assertAlmostEqual(time_to_cpa, 1.0, places=6)
        self.assertAlmostEqual(distance, 0.0, places=6)

    def test_univector_ball_field_is_finite_and_changes_with_lateral_position(self) -> None:
        ball = BallState(position=Vec2(0.0, 0.0))
        goal = Vec2(0.75, 0.0)
        above = univector_ball_field(RobotState(position=Vec2(-0.2, 0.15)), ball, goal)
        below = univector_ball_field(RobotState(position=Vec2(-0.2, -0.15)), ball, goal)
        self.assertTrue(above.is_finite() and below.is_finite())
        self.assertNotAlmostEqual(above.y, below.y)

    def test_univector_rotates_with_ball_to_goal_axis(self) -> None:
        ball = BallState(position=Vec2(0.0, 0.0))
        horizontal = univector_ball_field(
            RobotState(position=Vec2(-0.20, 0.08)), ball, Vec2(0.75, 0.0),
        )
        vertical = univector_ball_field(
            RobotState(position=Vec2(-0.08, -0.20)), ball, Vec2(0.0, 0.75),
        )
        self.assertAlmostEqual(vertical.x, -horizontal.y, places=6)
        self.assertAlmostEqual(vertical.y, horizontal.x, places=6)

    def test_univector_approaches_ball_with_goal_aligned_final_heading(self) -> None:
        ball = BallState(position=Vec2(0.0, 0.0))
        goal = Vec2(0.75, 0.0)
        behind = univector_ball_field(RobotState(position=Vec2(-0.20, 0.0)), ball, goal)
        in_front = univector_ball_field(RobotState(position=Vec2(0.20, 0.0)), ball, goal)
        self.assertGreater(behind.x, 0.99)
        self.assertLess(in_front.x, -0.99)


class StrategyTests(unittest.TestCase):
    @staticmethod
    def world_with_three_allies() -> WorldState:
        return WorldState(
            ball=BallState(position=Vec2(0.4, 0.0)),
            allies=(
                RobotState(position=Vec2(0.0, 0.0), robot_id=0),
                RobotState(position=Vec2(0.08, 0.0), robot_id=1),
                RobotState(position=Vec2(-0.08, 0.0), robot_id=2),
            ),
            enemies=(RobotState(position=Vec2(0.0, 0.08), robot_id=0),),
            field_length=1.5,
            field_width=1.3,
            goal_width=0.4,
            goal_depth=0.1,
        )

    def test_wheel_commands_are_finite_and_bounded(self) -> None:
        robot = RobotState(position=Vec2(-0.5, 0.0), robot_id=0, orientation=0.0)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)),
            allies=(robot,),
            enemies=(),
            field_length=1.5,
            field_width=1.3,
            goal_width=0.4,
            goal_depth=0.1,
        )
        strategy = VectorFieldStrategy(StrategyConfig(max_wheel_speed=20.0))
        left, right = strategy.wheel_command(world, 0)
        self.assertTrue(math.isfinite(left) and math.isfinite(right))
        self.assertLessEqual(abs(left), 20.0)
        self.assertLessEqual(abs(right), 20.0)

    def test_self_field_is_excluded_in_both_strategies(self) -> None:
        world = self.world_with_three_allies()
        duplicate_self = RobotState(position=Vec2(0.01, 0.01), robot_id=0)
        duplicated = replace(world, allies=(*world.allies, duplicate_self))
        for field_strategy in VectorFieldStrategy.FIELD_STRATEGIES:
            strategy = VectorFieldStrategy(field_strategy=field_strategy)
            self.assertEqual(strategy.nominal_velocity(world, 0), strategy.nominal_velocity(duplicated, 0))

    def test_ally_and_enemy_gains_are_independent(self) -> None:
        world = self.world_with_three_allies()
        ally_only = VectorFieldStrategy(StrategyConfig(
            ally_obstacle_radial_gain=5.0,
            ally_obstacle_circular_gain=0.0,
            enemy_obstacle_radial_gain=0.0,
            enemy_obstacle_circular_gain=0.0,
        )).nominal_velocity(world, 0)
        enemy_only = VectorFieldStrategy(StrategyConfig(
            ally_obstacle_radial_gain=0.0,
            ally_obstacle_circular_gain=0.0,
            enemy_obstacle_radial_gain=5.0,
            enemy_obstacle_circular_gain=0.0,
        )).nominal_velocity(world, 0)
        self.assertNotEqual(ally_only, enemy_only)

    def test_wall_decluster_field_separates_allies_and_pushes_them_inward(self) -> None:
        config = StrategyConfig(
            wall_cluster_distance=0.14,
            wall_cluster_neighbor_distance=0.24,
            wall_cluster_tangent_gain=0.8,
            wall_cluster_inward_gain=0.4,
        )
        strategy = VectorFieldStrategy(config)
        robot = RobotState(position=Vec2(0.10, 0.61), robot_id=0)
        neighbor = RobotState(position=Vec2(0.18, 0.60), robot_id=1)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)),
            allies=(robot, neighbor), enemies=(), field_length=1.5,
            field_width=1.3, goal_width=0.4, goal_depth=0.1,
        )

        field = strategy.wall_decluster_field(world, robot)

        self.assertLess(field.x, 0.0)
        self.assertLess(field.y, 0.0)
        self.assertTrue(field.is_finite())

    def test_wall_decluster_field_is_local_to_a_shared_wall_cluster(self) -> None:
        strategy = VectorFieldStrategy(StrategyConfig(
            wall_cluster_distance=0.14,
            wall_cluster_neighbor_distance=0.20,
        ))
        robot = RobotState(position=Vec2(0.10, 0.61), robot_id=0)
        far_along_wall = RobotState(position=Vec2(0.50, 0.61), robot_id=1)
        away_from_wall = RobotState(position=Vec2(0.15, 0.30), robot_id=2)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)),
            allies=(robot, far_along_wall, away_from_wall), enemies=(),
            field_length=1.5, field_width=1.3, goal_width=0.4, goal_depth=0.1,
        )

        self.assertEqual(strategy.wall_decluster_field(world, robot), Vec2())

    def test_player_bypasses_ball_before_returning_behind_it(self) -> None:
        robot = RobotState(position=Vec2(0.20, 0.01), robot_id=0)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)), allies=(robot,), enemies=(),
            field_length=1.5, field_width=1.3, goal_width=0.4, goal_depth=0.1,
            attack_sign=1.0,
        )
        strategy = VectorFieldStrategy(
            StrategyConfig(return_ball_clearance_radius=0.12, return_ball_bypass_gain=1.2),
        )
        field = strategy.player_ball_field(world, robot, Vec2(1.0, 0.0))
        self.assertGreater(abs(field.y), abs(field.x))
        self.assertGreater(field.y, 0.0)

    def test_player_returns_behind_ball_after_clearing_its_corridor(self) -> None:
        robot = RobotState(position=Vec2(0.20, 0.25), robot_id=0)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)), allies=(robot,), enemies=(),
            field_length=1.5, field_width=1.3, goal_width=0.4, goal_depth=0.1,
            attack_sign=1.0,
        )
        strategy = VectorFieldStrategy(
            StrategyConfig(behind_ball_distance=0.25, ball_return_gain_0=1.0),
        )
        field = strategy.player_ball_field(world, robot, Vec2(1.0, 0.0))
        self.assertLess(field.x, 0.0)

    def test_all_players_use_same_base_ball_field_when_behind_ball(self) -> None:
        first = RobotState(position=Vec2(-0.20, 0.0), robot_id=0)
        second = RobotState(position=Vec2(-0.20, 0.0), robot_id=1)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)), allies=(first, second), enemies=(),
            field_length=1.5, field_width=1.3, goal_width=0.4, goal_depth=0.1,
            attack_sign=1.0,
        )
        strategy = VectorFieldStrategy()
        base = Vec2(1.0, 0.2)
        self.assertEqual(strategy.player_ball_field(world, first, base), base)
        self.assertEqual(strategy.player_ball_field(world, second, base), base)

    def test_linear_and_quadratic_return_laws_have_different_strengths(self) -> None:
        robot_linear = RobotState(position=Vec2(0.30, 0.25), robot_id=0)
        robot_quadratic = RobotState(position=Vec2(0.30, 0.25), robot_id=1)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)), allies=(robot_linear, robot_quadratic), enemies=(),
            field_length=1.5, field_width=1.3, goal_width=0.4, goal_depth=0.1, attack_sign=1.0,
        )
        strategy = VectorFieldStrategy(StrategyConfig(
            ball_return_gain_0=1.0, ball_return_power_0=1.0,
            ball_return_gain_1=1.0, ball_return_power_1=2.0,
        ))
        linear = strategy.player_ball_field(world, robot_linear, Vec2())
        quadratic = strategy.player_ball_field(world, robot_quadratic, Vec2())
        self.assertGreater(linear.norm(), quadratic.norm())

    def test_enemy_goal_triangle_clearance_pushes_robots_out_and_mirrors(self) -> None:
        robot = RobotState(position=Vec2(0.30, 0.01), robot_id=0)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)), allies=(robot,), enemies=(),
            field_length=1.5, field_width=1.3, goal_width=0.4, goal_depth=0.1,
            attack_sign=1.0,
        )
        strategy = VectorFieldStrategy(StrategyConfig(goal_triangle_margin=0.04))
        field = strategy.goal_triangle_clearance_field(world, robot)
        self.assertIsNotNone(field)
        self.assertGreater(field.y, 0.0)
        self.assertLess(field.x, 0.0)

        mirrored_robot = replace(robot, position=Vec2(-0.30, -0.01))
        mirrored = replace(
            world, ball=BallState(position=Vec2(0.0, 0.0)),
            allies=(mirrored_robot,), attack_sign=-1.0,
        )
        mirrored_field = strategy.goal_triangle_clearance_field(mirrored, mirrored_robot)
        self.assertIsNotNone(mirrored_field)
        self.assertLess(mirrored_field.y, 0.0)
        self.assertGreater(mirrored_field.x, 0.0)

    def test_enemy_goal_triangle_clearance_is_zero_behind_ball_or_outside_triangle(self) -> None:
        behind = RobotState(position=Vec2(-0.10, 0.0), robot_id=0)
        wide = RobotState(position=Vec2(0.30, 0.30), robot_id=1)
        world = WorldState(
            ball=BallState(position=Vec2(0.0, 0.0)), allies=(behind, wide), enemies=(),
            field_length=1.5, field_width=1.3, goal_width=0.4, goal_depth=0.1,
            attack_sign=1.0,
        )
        strategy = VectorFieldStrategy()
        self.assertIsNone(strategy.goal_triangle_clearance_field(world, behind))
        self.assertIsNone(strategy.goal_triangle_clearance_field(world, wide))

    def test_controller_evaluates_candidate_formula_from_match_state(self) -> None:
        world = replace(
            self.world_with_three_allies(),
            time_remaining=300.0,
            goals_for=1,
            goals_against=2,
            metadata={"match_duration": 600.0},
        )
        formula = {"fields": {"shared": {"ball": {
            "expression": {"op": "add", "args": [
                {"const": 1.0}, {"feature": "time_remaining"},
            ]}
        }}}}
        strategy = VectorFieldStrategy(field_strategy="shared", formula=formula)
        self.assertAlmostEqual(strategy.formula_gain("ball", world, 0, 0.0), 1.5)
        self.assertEqual(strategy.formula_gain("wall", world, 0, 0.0), 1.0)

    def test_ball_attraction_is_disabled_only_for_a_shot_entering_enemy_goal(self) -> None:
        strategy = VectorFieldStrategy(StrategyConfig(ball_goal_min_speed=0.05))
        base = self.world_with_three_allies()
        scoring_shot = replace(base, ball=BallState(position=Vec2(0.0, 0.05), velocity=Vec2(0.4, 0.02)))
        wide_shot = replace(base, ball=BallState(position=Vec2(0.0, 0.19), velocity=Vec2(0.4, 0.10)))
        slow_ball = replace(base, ball=BallState(position=Vec2(0.0, 0.0), velocity=Vec2(0.01, 0.0)))
        backwards = replace(base, ball=BallState(position=Vec2(0.0, 0.0), velocity=Vec2(-0.4, 0.0)))
        mirrored = replace(base, attack_sign=-1.0, ball=BallState(position=Vec2(0.0, -0.05), velocity=Vec2(-0.4, -0.02)))

        self.assertTrue(strategy.ball_is_heading_to_enemy_goal(scoring_shot))
        self.assertTrue(strategy.ball_is_heading_to_enemy_goal(mirrored))
        self.assertFalse(strategy.ball_is_heading_to_enemy_goal(wide_shot))
        self.assertFalse(strategy.ball_is_heading_to_enemy_goal(slow_ball))
        self.assertFalse(strategy.ball_is_heading_to_enemy_goal(backwards))

        magnitude_trigger = replace(
            base,
            ball=BallState(position=Vec2(0.0, -0.65), velocity=Vec2(0.04, 0.045)),
        )
        self.assertTrue(strategy.ball_is_heading_to_enemy_goal(magnitude_trigger))

    def test_robot_in_front_of_goal_bound_ball_is_pushed_out_of_shot_corridor(self) -> None:
        strategy = VectorFieldStrategy(StrategyConfig(shot_clearance_radius=0.14, max_nominal_speed=1.7))
        base = self.world_with_three_allies()
        ball = BallState(position=Vec2(0.0, 0.0), velocity=Vec2(0.4, 0.0))
        in_front = RobotState(position=Vec2(0.3, 0.04), robot_id=0)
        behind = RobotState(position=Vec2(-0.2, 0.02), robot_id=0)
        outside = RobotState(position=Vec2(0.3, 0.16), robot_id=0)
        world = replace(base, ball=ball)

        clearance = strategy.shot_clearance_field(world, in_front)
        self.assertIsNotNone(clearance)
        self.assertGreater(clearance.y, 0.0)
        self.assertAlmostEqual(clearance.x, 0.0)
        self.assertIsNone(strategy.shot_clearance_field(world, behind))
        self.assertIsNone(strategy.shot_clearance_field(world, outside))

        mirrored = replace(world, attack_sign=-1.0, ball=replace(ball, velocity=Vec2(-0.4, 0.0)))
        mirrored_robot = replace(in_front, position=Vec2(-0.3, -0.04))
        mirrored_clearance = strategy.shot_clearance_field(mirrored, mirrored_robot)
        self.assertIsNotNone(mirrored_clearance)
        self.assertLess(mirrored_clearance.y, 0.0)

    def test_corner_approach_allows_only_the_same_side_finisher_and_mirrors(self) -> None:
        strategy = VectorFieldStrategy(StrategyConfig(corner_gate_player_angle=0.05))
        base = self.world_with_three_allies()
        right_corner_ball = BallState(position=Vec2(0.50, -0.15), velocity=Vec2(0.30, -0.084))
        world = replace(base, ball=right_corner_ball)
        self.assertEqual(strategy.corner_approach_side(world), -1.0)
        same_side = RobotState(position=Vec2(0.40, -0.27), robot_id=0)
        opposite_side = RobotState(position=Vec2(0.40, 0.02), robot_id=1)
        self.assertIsNone(strategy.corner_approach_yield(world, same_side))
        self.assertEqual(strategy.corner_approach_yield(world, opposite_side), Vec2(-0.55, 0.0))

        mirrored_ball = BallState(position=Vec2(-0.50, 0.15), velocity=Vec2(-0.30, 0.084))
        mirrored = replace(base, attack_sign=-1.0, ball=mirrored_ball)
        self.assertEqual(strategy.corner_approach_side(mirrored), -1.0)
        mirrored_same = replace(same_side, position=Vec2(-0.40, 0.27))
        mirrored_wrong = replace(opposite_side, position=Vec2(-0.40, -0.02))
        self.assertIsNone(strategy.corner_approach_yield(mirrored, mirrored_same))
        self.assertEqual(strategy.corner_approach_yield(mirrored, mirrored_wrong), Vec2(0.55, 0.0))

        far_ball = replace(right_corner_ball, position=Vec2(0.0, -0.15))
        self.assertIsNone(strategy.corner_approach_side(replace(base, ball=far_ball)))

    def test_goal_escape_overrides_all_fields_only_while_inside_goal(self) -> None:
        strategy = VectorFieldStrategy(StrategyConfig(max_nominal_speed=1.7))
        base = self.world_with_three_allies()
        right_goal = replace(base, allies=(replace(base.allies[0], position=Vec2(0.80, 0.0)),))
        left_goal = replace(base, allies=(replace(base.allies[0], position=Vec2(-0.80, 0.0)),))
        outside_mouth = replace(base, allies=(replace(base.allies[0], position=Vec2(0.80, 0.30)),))
        self.assertEqual(strategy.nominal_velocity(right_goal, 0), Vec2(-1.7, 0.0))
        self.assertEqual(strategy.nominal_velocity(left_goal, 0), Vec2(1.7, 0.0))
        self.assertIsNone(strategy.goal_escape_velocity(outside_mouth, outside_mouth.allies[0]))
        self.assertNotEqual(strategy.nominal_velocity(outside_mouth, 0), Vec2(-1.7, 0.0))

        upper_corner_robot = replace(base.allies[0], position=Vec2(0.82, 0.16))
        upper_corner = replace(base, allies=(upper_corner_robot,))
        corner_escape = strategy.goal_escape_velocity(upper_corner, upper_corner_robot)
        self.assertIsNotNone(corner_escape)
        self.assertLess(corner_escape.x, 0.0)
        self.assertLess(corner_escape.y, 0.0)

    def test_goal_post_escape_requires_dwell_and_uses_hysteresis(self) -> None:
        config = StrategyConfig(
            goal_post_escape_radius=0.13,
            goal_post_release_radius=0.19,
            goal_post_stall_speed=0.05,
            goal_post_stall_duration=0.30,
        )
        strategy = VectorFieldStrategy(config)
        base = self.world_with_three_allies()
        post = Vec2(base.field_length / 2.0, base.goal_width / 2.0)
        robot = RobotState(position=post + Vec2(-0.03, 0.02), velocity=Vec2(), robot_id=0, orientation=0.0)

        initial = replace(base, allies=(robot,), metadata={"timestamp": 1.0})
        self.assertIsNone(strategy.goal_post_escape_velocity(initial, robot))
        waiting = replace(initial, metadata={"timestamp": 1.29})
        self.assertIsNone(strategy.goal_post_escape_velocity(waiting, robot))
        active = replace(initial, metadata={"timestamp": 1.31})
        velocity = strategy.goal_post_escape_velocity(active, robot)
        self.assertIsNotNone(velocity)
        self.assertLess(velocity.x, 0.0)
        self.assertLessEqual(velocity.norm(), config.goal_post_escape_speed)

        # It remains active outside the activation radius, then releases only
        # after crossing the larger hysteresis radius.
        between = replace(robot, position=post + Vec2(-0.15, 0.0), velocity=Vec2(0.2, 0.0))
        self.assertIsNotNone(strategy.goal_post_escape_velocity(active, between))
        released = replace(robot, position=post + Vec2(-0.21, 0.0), velocity=Vec2(0.2, 0.0))
        self.assertIsNone(strategy.goal_post_escape_velocity(active, released))

    def test_goal_post_escape_does_not_activate_while_robot_is_moving(self) -> None:
        strategy = VectorFieldStrategy()
        base = self.world_with_three_allies()
        post = Vec2(base.field_length / 2.0, base.goal_width / 2.0)
        robot = RobotState(position=post, velocity=Vec2(0.2, 0.0), robot_id=0)
        first = replace(base, allies=(robot,), metadata={"timestamp": 1.0})
        moving_robot = replace(robot, position=post + Vec2(-0.10, 0.0))
        later = replace(base, allies=(moving_robot,), metadata={"timestamp": 2.0})
        self.assertIsNone(strategy.goal_post_escape_velocity(first, robot))
        self.assertIsNone(strategy.goal_post_escape_velocity(later, moving_robot))

    def test_goal_post_escape_activates_when_wheels_slip_without_progress(self) -> None:
        strategy = VectorFieldStrategy()
        base = self.world_with_three_allies()
        post = Vec2(base.field_length / 2.0, base.goal_width / 2.0)
        first_robot = RobotState(position=post + Vec2(-0.02, 0.01), velocity=Vec2(0.12, 0.0), robot_id=0)
        slipping_robot = replace(first_robot, position=first_robot.position + Vec2(0.005, 0.0))
        first = replace(base, allies=(first_robot,), metadata={"timestamp": 1.0})
        later = replace(base, allies=(slipping_robot,), metadata={"timestamp": 1.31})
        self.assertIsNone(strategy.goal_post_escape_velocity(first, first_robot))
        escape = strategy.goal_post_escape_velocity(later, slipping_robot)
        self.assertIsNotNone(escape)
        self.assertLess(escape.x, 0.0)
        self.assertLess(escape.y, 0.0)

    def test_attacking_corner_escape_opens_the_goal_lane_and_mirrors(self) -> None:
        strategy = VectorFieldStrategy()
        base = self.world_with_three_allies()
        upper_right = RobotState(position=Vec2(0.68, 0.58), robot_id=0)
        velocity = strategy.attacking_corner_escape_velocity(base, upper_right)
        self.assertIsNotNone(velocity)
        self.assertLess(velocity.x, 0.0)
        self.assertLess(velocity.y, 0.0)

        mirrored = replace(base, attack_sign=-1.0)
        lower_left = replace(upper_right, position=Vec2(-0.68, -0.58))
        mirrored_velocity = strategy.attacking_corner_escape_velocity(mirrored, lower_left)
        self.assertIsNotNone(mirrored_velocity)
        self.assertGreater(mirrored_velocity.x, 0.0)
        self.assertGreater(mirrored_velocity.y, 0.0)

        midfield = replace(upper_right, position=Vec2(0.0, 0.58))
        self.assertIsNone(strategy.attacking_corner_escape_velocity(base, midfield))

    def test_vector_formula_components_can_change_direction(self) -> None:
        world = self.world_with_three_allies()
        formula = {"fields": {"shared": {"ball": {
            "expression": {"const": 1.0},
            "components": {"x": {"expression": {"const": 0.0}}, "y": {"expression": {"const": 1.0}}},
        }}}}
        strategy = VectorFieldStrategy(field_strategy="shared", formula=formula)
        factors = strategy.formula_components("ball", world, 0, 0.4)
        self.assertEqual(factors, Vec2(0.0, 1.0))

    def test_ball_univector_uses_independent_parameters_for_each_player(self) -> None:
        config = StrategyConfig(
            ball_spiral_radius_0=0.05,
            ball_spiral_radius_1=0.25,
            ball_spiral_smoothing_0=0.02,
            ball_spiral_smoothing_1=0.20,
            approach_gain_0=0.40,
            approach_gain_1=2.00,
            obstacle_radial_gain=0.0,
            obstacle_circular_gain=0.0,
            ally_obstacle_radial_gain=0.0,
            ally_obstacle_circular_gain=0.0,
            enemy_obstacle_radial_gain=0.0,
            enemy_obstacle_circular_gain=0.0,
            wall_gain=0.0,
        )
        strategy = VectorFieldStrategy(config)
        world = self.world_with_three_allies()
        shared_position = Vec2(0.0, 0.15)
        world = replace(world, allies=tuple(
            replace(robot, position=shared_position) for robot in world.allies
        ))
        first = strategy.nominal_velocity(world, 0)
        second = strategy.nominal_velocity(world, 1)
        self.assertNotAlmostEqual(first.norm(), second.norm(), places=3)
        self.assertNotAlmostEqual(first.unit().x, second.unit().x, places=3)



if __name__ == "__main__":
    unittest.main()
