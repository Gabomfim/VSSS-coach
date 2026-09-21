import json
import sys
from pathlib import Path
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from coach_silvs.dashboard import DashboardHandler, HTML, REPLAY_FPS


class DashboardTests(unittest.TestCase):
    def test_canvases_preserve_official_field_aspect_ratio(self) -> None:
        self.assertIn("aspect-ratio:15/13", HTML)
        self.assertEqual(HTML.count('width="750" height="650"'), 2)

    def test_replay_controls_are_present(self) -> None:
        for control_id in ("generations", "candidatePairs", "matches", "play", "followLive", "firstPeriod", "secondPeriod", "speed", "timeline"):
            self.assertIn(f'id="{control_id}"', HTML)

    def test_replay_navigation_filters_generation_pair_and_game(self) -> None:
        self.assertIn("function configureReplayMenus", HTML)
        self.assertIn("function pairKey", HTML)
        self.assertIn("api('/api/match-catalog')", HTML)
        self.assertIn("generations.onchange", HTML)
        self.assertIn("candidatePairs.onchange", HTML)

    def test_replay_buttons_are_below_canvas_score_and_timeline(self) -> None:
        canvas = HTML.index('id="match"')
        score = HTML.index('id="recordedScore"')
        timeline = HTML.index('id="timeline"')
        play = HTML.index('id="play"')
        self.assertLess(canvas, score)
        self.assertLess(score, timeline)
        self.assertLess(timeline, play)
        self.assertLess(play, HTML.index('id="goalEvents"'))

    def test_replay_uses_recorded_time_and_can_jump_to_second_period(self) -> None:
        self.assertIn("function jumpToPeriod", HTML)
        self.assertIn("2º tempo ainda não foi gravado", HTML)
        self.assertIn("playbackTime+=(now-lastTick)/1000*Number(speed.value)", HTML)

    def test_replay_accumulates_time_between_sampled_frames(self) -> None:
        self.assertIn("playbackTime=0", HTML)
        self.assertIn("<=playbackTime", HTML)
        self.assertIn("playbackTime=frameTime(matchData.frames[frameIndex])", HTML)
        self.assertIn("frameIndex>=matchData.frames.length-1){frameIndex=0", HTML)

    def test_replay_uses_recorded_frames_at_thirty_fps_without_interpolation(self) -> None:
        self.assertEqual(REPLAY_FPS, 30)
        self.assertNotIn("function blendEntity", HTML)
        self.assertNotIn("function visualFrame", HTML)
        self.assertIn("const f=matchData.frames[frameIndex],x=match.getContext", HTML)
        self.assertIn("frameTime(matchData.frames[frameIndex+1])<=playbackTime", HTML)

    def test_mock_replay_uses_frame_steps_at_thirty_fps(self) -> None:
        """Regression: sparse mock match clocks must not freeze video playback."""
        frame_time = HTML.index("function frameTime")
        frame_time_end = HTML.index("function matchElapsedTime")
        implementation = HTML[frame_time:frame_time_end]
        step_branch = implementation.index("Number(frame.step)/30")
        regulation_clock_branch = implementation.index("frame?.time_remaining")
        self.assertLess(step_branch, regulation_clock_branch)
        self.assertIn("if(Number.isFinite(frame?.time))", implementation)

    def test_goal_navigation_uses_match_clock_not_video_clock(self) -> None:
        self.assertIn("function matchElapsedTime", HTML)
        self.assertIn("eventTime=matchElapsedTime(frame)", HTML)
        self.assertIn("matchElapsedTime(frames[i])<=targetTime", HTML)

    def test_each_wall_field_is_local_and_independent_in_dashboard(self) -> None:
        self.assertIn("function wallGeometry", HTML)
        for wall in ("wall_left", "wall_right", "wall_bottom", "wall_top"):
            self.assertIn(f"object==='{wall}'", HTML)
        self.assertIn(".10/1.5*(w-40)", HTML)
        self.assertIn(".10/1.3*(h-40)", HTML)
        self.assertIn("distance<limit", HTML)
        self.assertIn("object.startsWith('wall_')", HTML)

    def test_replay_sampling_preserves_thirty_real_frames_per_second(self) -> None:
        frames = [{"time": index / 100.0} for index in range(101)]
        sampled = DashboardHandler.sample_frames(frames)
        self.assertGreaterEqual(len(sampled), 30)
        self.assertLessEqual(len(sampled), 32)
        self.assertIs(sampled[-1], frames[-1])

    def test_replay_can_jump_to_exact_goal_frames(self) -> None:
        self.assertIn('id="goalEvents"', HTML)
        self.assertIn("function goalMoments", HTML)
        self.assertIn('data-goal-frame="${event.index}"', HTML)
        self.assertIn("frameIndex=Number(button.dataset.goalFrame)", HTML)
        self.assertIn("uiState.followLive=false", HTML)
        self.assertIn("${event.team}, ${event.period}º tempo ${minutes}:${seconds}", HTML)
        self.assertNotIn("⚽ ${index+1} —", HTML)
        self.assertIn("const targetTime=Math.max(0,event.time-3);let preIndex=0", HTML)

    def test_replay_stops_at_the_last_frame_and_live_refresh_does_not_interrupt_playback(self) -> None:
        self.assertIn("if(matchData.live){animationId=requestAnimationFrame(animate);return}setPlaying(false)", HTML)
        self.assertNotIn("(frameIndex+1)%matchData.frames.length", HTML)
        self.assertIn("else if(matchData?.live)await refreshLiveMatch()", HTML)

    def test_manual_replay_selection_disables_live_following(self) -> None:
        self.assertIn("followLive:true", HTML)
        self.assertIn("followLive&&!uiState.followLive", HTML)
        self.assertIn("uiState.followLive=false;setPlaying", HTML)
        self.assertIn("uiState.followLive=false;frameIndex=Number(timeline.value)", HTML)

    def test_shared_and_individual_fields_can_select_players_and_sources(self) -> None:
        self.assertIn('id="playerControl"', HTML)
        self.assertIn('id="fieldPlayers"', HTML)
        self.assertIn('id="fieldObjects"', HTML)
        self.assertIn('id="fieldSources"', HTML)
        self.assertIn("architecture==='shared'?'shared':uiState.player", HTML)
        self.assertIn("`${object}_${i}`", HTML)

    def test_dashboard_draws_shared_rotated_ifac_ball_field(self) -> None:
        self.assertIn("function univectorBallVector", HTML)
        self.assertIn("ifac2008_univector_rotated", HTML)
        self.assertIn("opponent_goal_center - ball_position", HTML)
        self.assertIn("|y-dₑ|", HTML)
        self.assertIn("independent_parameters_by_player:true", HTML)
        self.assertIn("function playerBallParameters", HTML)
        self.assertIn("ball_spiral_radius_${i}", HTML)
        self.assertIn("label:`Jogador ${index+1}`", HTML)
        self.assertNotIn("function roleBallVector", HTML)
        self.assertIn("último aliado que tocou fisicamente na bola", HTML)
        self.assertIn("bola projetava entrada no gol aliado", HTML)

    def test_vector_direction_uses_arrowheads_and_origin_markers(self) -> None:
        self.assertIn("function drawArrow", HTML)
        self.assertIn("ponta triangular mostra o sentido", HTML)
        self.assertIn("x.closePath();x.fill()", HTML)
        self.assertIn("fieldColors", HTML)
        self.assertIn('id="fieldMeaning"', HTML)

    def test_goal_escape_override_is_visible_and_takes_priority(self) -> None:
        self.assertIn("goal_escape", HTML)
        self.assertIn("function insideGoal", HTML)
        self.assertIn("function goalEscapeVector", HTML)
        self.assertIn("overrideActive?'goal_escape':object", HTML)
        self.assertIn("OVERRIDE ATIVO", HTML)
        self.assertIn("goal_escape_override", HTML)

    def test_dashboard_exposes_every_deterministic_and_evolved_safety_field(self) -> None:
        for field in ("wall_decluster", "goal_triangle_clearance", "shot_clearance", "corner_shot_gate", "goal_post_escape", "attacking_corner_escape", "goal_escape"):
            self.assertIn(f"'{field}'", HTML)
        self.assertIn("function deterministicSafetyVector", HTML)
        self.assertIn("object==='wall_decluster'?'deterministic_base_field':'deterministic_priority_override'", HTML)
        self.assertIn("function wallDeclusterVector", HTML)
        self.assertIn("sum_neighbors[(0.8 * tangent_away + 0.4 * inward)", HTML)
        self.assertIn("function goalTriangleVector", HTML)
        self.assertIn("function drawGoalTriangle", HTML)
        self.assertIn("inside_triangle ? lateral_nearest_exit + backward_component : 0", HTML)
        self.assertIn("inside_field_mouth_centre - robot_position", HTML)
        self.assertIn("os cantos internos não são destinos estáveis", HTML)
        self.assertIn("function cornerApproachSide", HTML)
        self.assertIn("wrong_side ? -attack_direction * retreat_gain : normal_ball_field", HTML)

    def test_vector_grid_is_symmetric_and_keeps_arrows_inside_canvas(self) -> None:
        self.assertIn("function fieldGrid", HTML)
        self.assertIn("columns=13,rows=11,inset=65", HTML)
        self.assertIn("(w-2*inset)*column/(columns-1)", HTML)
        self.assertIn("const grid=effectiveObject==='goal_escape'?goalEscapeGrid(w,h):clearanceActive?shotClearanceGrid(w,h,state):fieldGrid(w,h)", HTML)
        self.assertIn("for(const[px,py]of grid)", HTML)

    def test_ally_goal_is_repulsive_and_default_ally_source_is_not_self(self) -> None:
        self.assertIn("if(object==='ally_goal')", HTML)
        self.assertIn("return[-scale*gain*dx/n,-scale*gain*dy/n]", HTML)
        self.assertIn("Repulsão e proteção do gol aliado", HTML)
        self.assertIn("selectedIndex(source)!==selectedIndex(uiState.player)", HTML)

    def test_dashboard_evaluates_stored_gp_expression(self) -> None:
        self.assertIn("function evaluateExpression", HTML)
        self.assertIn("fieldVector(object,candidate.genome,block,px,py", HTML)
        self.assertIn("formula_expression:block?.expression", HTML)
        self.assertIn("fator GP neste ponto", HTML)

    def test_dashboard_draws_base_and_formula_scaled_vectors(self) -> None:
        self.assertIn("fieldVector(object,candidate.genome,null,px,py", HTML)
        self.assertIn("fieldVector(object,candidate.genome,block,px,py", HTML)
        self.assertIn("fator GP neste ponto", HTML)
        self.assertIn("tracejado cinza: base, colorido: fórmula aplicada", HTML)

    def test_match_replay_draws_a_ball_trajectory(self) -> None:
        self.assertIn("frameIndex-14", HTML)
        self.assertIn("const coords=q=>Array.isArray(q)?q:[q?.x,q?.y]", HTML)
        self.assertIn("trail.forEach", HTML)

    def test_recorded_match_draws_both_goal_frames_and_posts(self) -> None:
        self.assertIn('aria-label="Traves dos gols"', HTML)
        self.assertEqual(HTML.count('class="goal-post"'), 4)
        self.assertIn('class="goal-frame"', HTML)
        self.assertIn('id="leftGoal"', HTML)
        self.assertIn('id="rightGoal"', HTML)
        self.assertIn("function updateGoalColors", HTML)
        self.assertIn("leftTeam=second?'yellow':'blue'", HTML)

    def test_completed_run_counts_recorded_match_files(self) -> None:
        self.assertIn("runComplete?catalog.length", HTML)
        self.assertIn("completedGames}/${totalGames}", HTML)

    def test_refresh_keeps_explicit_ui_selection_state(self) -> None:
        self.assertIn("const uiState=", HTML)
        self.assertIn("function setOptions", HTML)
        self.assertIn("uiState.object=fieldObjects.value", HTML)
        self.assertIn("uiState.match!==loadedMatchId", HTML)

    def test_replay_shows_official_period_countdown(self) -> None:
        self.assertIn('id="periodLabel"', HTML)
        self.assertIn("function matchClock", HTML)
        self.assertIn("Number(configData.match_duration)", HTML)
        self.assertIn("periodDuration=totalDuration/periods", HTML)
        self.assertIn("api('/api/config')", HTML)

    def test_replay_time_recalculates_vector_fields(self) -> None:
        self.assertIn('id="fieldStatus"', HTML)
        self.assertIn("function fieldTargets", HTML)
        self.assertIn("initialAttackSign=isAway?1:-1", HTML)
        self.assertIn("attackSign=period%2===0?initialAttackSign:-initialAttackSign", HTML)

    def test_dashboard_uses_recorded_ball_velocity_for_clearance(self) -> None:
        self.assertIn("recordedVx=Number(current.ball?.vx)", HTML)
        self.assertIn("return{bx,by,vx:recordedVx,vy:recordedVy}", HTML)
        self.assertIn("replayEntities(state)", HTML)
        self.assertIn("worldToField(entities.frame.ball", HTML)
        self.assertIn("cinza: base, colorido: fórmula aplicada", HTML)
        self.assertIn("block?.components", HTML)
        self.assertIn("fieldVector=formulaFieldVector", HTML)
        self.assertIn("fieldMeaning.style.cssText='height:42px;min-height:42px;overflow:hidden", HTML)
        self.assertIn("fieldStatus.style.cssText='height:24px;min-height:24px;overflow:hidden", HTML)
        self.assertIn("function readableVectorFormula", HTML)
        self.assertIn("Fx = (", HTML)
        self.assertIn("tempo normalizado", HTML)
        self.assertIn("height:42px;min-height:42px;overflow:hidden", HTML)
        self.assertIn("white-space:nowrap", HTML)
        self.assertIn("penalidades comportamentais", HTML)
        self.assertIn("item.remove()", HTML)
        self.assertIn("scoreLabel.textContent", HTML)
        self.assertIn(";drawField()}", HTML)
        self.assertIn("goal_difference:state.goalDifference", HTML)

    def test_ball_field_clears_recorded_goal_bound_shot_corridor(self) -> None:
        self.assertIn("function ballHeadingToEnemyGoal", HTML)
        self.assertIn("function shotClearanceVector", HTML)
        self.assertIn("function shotClearanceGrid", HTML)
        self.assertIn("function drawShotCorridor", HTML)
        self.assertIn("speed<threshold", HTML)
        self.assertIn("Math.abs(crossingY)<=goalWidth/2", HTML)
        self.assertIn("object==='ball'&&ballHeadingToEnemyGoal(state,g)", HTML)
        self.assertIn("shotClearanceVector(px,py,w,h,state,g)||[0,0]", HTML)
        self.assertIn("DESOBSTRUÇÃO DE CHUTE ATIVA", HTML)
        self.assertIn("shot_clearance_active:clearanceActive", HTML)
        self.assertIn("threshold evoluído", HTML)
        self.assertIn("ball_speed_m_s:speed", HTML)
        self.assertIn("threshold_m_s:params.threshold", HTML)
        self.assertIn("radius_m:params.radius", HTML)
        self.assertIn("gain:params.gain", HTML)

    def test_vector_fields_are_limited_to_selected_match_candidates(self) -> None:
        self.assertIn("function configureMatchCandidates", HTML)
        self.assertIn("const ids=[matchData?.home,matchData?.away]", HTML)
        self.assertIn("candidateData=Array.isArray(matchData.candidates)?matchData.candidates:[]", HTML)
        self.assertNotIn("api('/api/candidates')", HTML)
        self.assertIn("matchData?.live", HTML)

    def test_match_response_uses_archived_generation_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            generations = run / "generations"
            generations.mkdir()
            (generations / "generation-0002.json").write_text(
                '[{"candidate_id":"a","genome":{}},{"candidate_id":"b","genome":{}},'
                '{"candidate_id":"c","genome":{}}]',
                encoding="utf-8",
            )
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            payload = {"generation": 2, "home": "a", "away": "c"}
            candidates = handler.match_candidates(payload)
            self.assertEqual([candidate["candidate_id"] for candidate in candidates], ["a", "c"])

    def test_match_catalog_groups_completed_and_live_games(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            matches = run / "matches"
            matches.mkdir()
            (matches / "g0000-m0.json").write_text(
                '{"match_id":"g0000-m0","generation":0,"home":"a","away":"b","repetition":0,'
                '"frames":[{"time":1,"finished":true}]}',
                encoding="utf-8",
            )
            (matches / "g0001-m0.live.meta.json").write_text(
                '{"match_id":"g0001-m0","generation":1,"home":"c","away":"d","repetition":0}',
                encoding="utf-8",
            )
            (matches / "g0001-m0.live.jsonl").write_text(
                '{"type":"frame","time":1,"goals_yellow":0,"goals_blue":0}\n', encoding="utf-8"
            )
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            catalog = handler.read_match_catalog()
            self.assertEqual([item["match_id"] for item in catalog], ["g0000-m0", "g0001-m0"])
            self.assertFalse(catalog[0]["live"])
            self.assertTrue(catalog[1]["live"])

    def test_match_catalog_hides_empty_live_and_crashed_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            matches = run / "matches"
            matches.mkdir()
            (matches / "empty.live.meta.json").write_text(
                '{"match_id":"empty","generation":0,"home":"a","away":"b"}', encoding="utf-8"
            )
            (matches / "empty.live.jsonl").write_text("", encoding="utf-8")
            (matches / "crashed.json").write_text(
                '{"match_id":"crashed","backend":"travesim","frames":[]}', encoding="utf-8"
            )
            (matches / "crashed.summary.json").write_text(
                '{"match_id":"crashed","backend":"travesim","frame_count":0,"finished":false}', encoding="utf-8"
            )
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run

            self.assertEqual(handler.read_match_catalog(), [])

    def test_match_catalog_marks_a_stationary_ball_for_dropdown_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            matches = run / "matches"
            matches.mkdir()
            (run / "config.json").write_text(
                '{"ball_stall_speed":0.02,"ball_stall_duration":5.0}', encoding="utf-8"
            )
            (matches / "stalled.live.meta.json").write_text(
                '{"match_id":"stalled","generation":0,"home":"a","away":"b"}', encoding="utf-8"
            )
            frames = "\n".join(
                json.dumps({"type": "frame", "time": second, "ball": [0.1, -0.1]})
                for second in range(7)
            )
            (matches / "stalled.live.jsonl").write_text(frames + "\n", encoding="utf-8")
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run

            catalog = handler.read_match_catalog()

            self.assertTrue(catalog[0]["ball_stalled"])
            self.assertGreaterEqual(catalog[0]["ball_stall_seconds"], 5.0)
            self.assertGreaterEqual(catalog[0]["ball_stationary_seconds"], 5.0)

    def test_dashboard_plots_stationary_ball_mean_and_standard_deviation(self) -> None:
        self.assertIn('id="ballStallChart"', HTML)
        self.assertIn("function ballStallGenerationStats", HTML)
        self.assertIn("function drawBallStallChart", HTML)
        self.assertIn("ball_stationary_seconds??item.ball_stall_seconds", HTML)
        self.assertIn("standardDeviation:Math.sqrt(variance)", HTML)
        self.assertIn("± desvio padrão", HTML)
        self.assertIn("drawBallStallChart();", HTML)

    def test_stationary_ball_diagnostics_do_not_decorate_replay_menus(self) -> None:
        self.assertNotIn("item.ball_stalled?'🔴 '", HTML)
        self.assertNotIn("background:#b4232f", HTML)
        self.assertNotIn("bola parada ${Number(item.ball_stall_seconds||0).toFixed(1)}s", HTML)

    def test_match_replay_shows_goal_difference(self) -> None:
        self.assertIn("Saldo da casa", HTML)
        self.assertIn('id="recordedScore"', HTML)
        self.assertIn('class="score-row"', HTML)
        self.assertIn('class="score-period">Final', HTML)

    def test_replay_identifies_candidates_by_team_color(self) -> None:
        self.assertNotIn('id="teamLegend"', HTML)
        self.assertNotIn("function renderTeamLegend", HTML)
        self.assertIn("team-yellow", HTML)
        self.assertIn("team-blue", HTML)
        self.assertIn("`🟨 ${matchData.home", HTML)
        self.assertIn("`🟦 ${matchData.away", HTML)

    def test_replay_scoreboard_shows_both_periods_together(self) -> None:
        self.assertIn("function periodScoreText", HTML)
        self.assertIn('class="score-period">1º tempo', HTML)
        self.assertIn('class="score-period">2º tempo', HTML)
        self.assertIn("recordedScore.innerHTML=periodScoreText", HTML)
        self.assertIn("secondStarted", HTML)

    def test_dashboard_shows_generation_and_run_eta(self) -> None:
        self.assertIn("function formatDuration", HTML)
        self.assertIn("live.generation_eta_seconds", HTML)
        self.assertIn("live.run_eta_seconds", HTML)
        self.assertIn("Fim da geração", HTML)
        self.assertIn("Fim de tudo", HTML)
        self.assertIn("Number(live.generation)+1", HTML)
        self.assertIn("em andamento", HTML)

    def test_partial_supervisor_telemetry_is_available_as_live_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            matches = run / "matches"
            matches.mkdir()
            (matches / "m1.live.meta.json").write_text(
                '{"match_id":"m1","match_duration":600,"home":"a","away":"b"}', encoding="utf-8"
            )
            (matches / "m1.live.jsonl").write_text(
                '{"type":"frame","time":125,"goals_yellow":2,"goals_blue":1}\n', encoding="utf-8"
            )
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            payload = handler.read_live_match("m1")
            self.assertTrue(payload["live"])
            self.assertEqual(payload["frames"][0]["time_remaining"], 475.0)

    def test_running_ranking_is_calculated_from_completed_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            matches = run / "matches"
            matches.mkdir()
            (run / "live.json").write_text('{"status":"running","generation":0}', encoding="utf-8")
            (run / "config.json").write_text('{"draw_penalty":2}', encoding="utf-8")
            (run / "candidates.json").write_text(
                '[{"candidate_id":"a"},{"candidate_id":"b"}]', encoding="utf-8"
            )
            (matches / "game.json").write_text(
                '{"generation":0,"home":"a","away":"b","home_goals":2,"away_goals":1}',
                encoding="utf-8",
            )
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            ranking = handler.read_ranking()
            self.assertEqual([row["candidate_id"] for row in ranking], ["a", "b"])
            self.assertEqual(ranking[0]["score"], 4.0)
            self.assertEqual(ranking[0]["wins"], 1)
            self.assertEqual(ranking[1]["losses"], 1)

    def test_running_ranking_includes_latest_provisional_live_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            matches = run / "matches"
            matches.mkdir()
            (run / "live.json").write_text('{"status":"running","generation":1}', encoding="utf-8")
            (run / "config.json").write_text('{"draw_penalty":1}', encoding="utf-8")
            (run / "candidates.json").write_text(
                '[{"candidate_id":"a"},{"candidate_id":"b"}]', encoding="utf-8"
            )
            (matches / "game.live.meta.json").write_text(
                '{"generation":1,"home":"a","away":"b"}', encoding="utf-8"
            )
            (matches / "game.live.jsonl").write_text(
                '{"type":"frame","time":12,"goals_yellow":3,"goals_blue":1,"finished":false}\n',
                encoding="utf-8",
            )
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            ranking = handler.read_ranking()
            self.assertEqual(ranking[0]["candidate_id"], "a")
            self.assertEqual(ranking[0]["score"], 5.0)
            self.assertEqual(ranking[0]["provisional_matches"], 1)

    def test_dashboard_marks_provisional_ranking_rows(self) -> None:
        self.assertIn("r.provisional_matches?' ●':''", HTML)
        self.assertIn("placar provisório", HTML)

    def test_live_match_only_parses_appended_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            matches = run / "matches"
            matches.mkdir()
            (matches / "m1.live.meta.json").write_text(
                '{"match_id":"m1","match_duration":600}', encoding="utf-8"
            )
            stream = matches / "m1.live.jsonl"
            stream.write_text('{"type":"frame","time":1}\n', encoding="utf-8")
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            handler.live_cache = {}
            first = handler.read_live_match("m1")
            with stream.open("a", encoding="utf-8") as output:
                output.write('{"type":"frame","time":2}\n')
            second = handler.read_live_match("m1")
            self.assertEqual(len(first["frames"]), 1)
            self.assertEqual(len(second["frames"]), 2)

    def test_eta_falls_back_to_previous_generation_throughput(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            matches = run / "matches"
            matches.mkdir()
            (run / "config.json").write_text(
                '{"competitors":3,"matches_per_pair":1,"generations":2}', encoding="utf-8"
            )
            old = time.time() - 60
            (run / "config.json").touch()
            import os
            os.utime(run / "config.json", (old, old))
            (run / "live.json").write_text(
                '{"status":"running","generation":1,"completed_matches":0,'
                '"generation_eta_seconds":null,"run_eta_seconds":null}', encoding="utf-8"
            )
            for index in range(3):
                (matches / f"g0000-m{index:06d}-r0.json").write_text("{}", encoding="utf-8")
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            status = handler.read_live_status()
            self.assertGreater(status["generation_eta_seconds"], 0)
            self.assertGreater(status["run_eta_seconds"], 0)
            self.assertEqual(status["completed_matches_overall"], 3)

    def test_ranking_can_be_loaded_for_selected_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            generations = run / "generations"
            generations.mkdir()
            expected = [{"rank": 1, "candidate_id": "g0002-c0003", "score": 7.0}]
            (generations / "generation-0002.json").write_text(json.dumps(expected), encoding="utf-8")
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            self.assertEqual(handler.read_ranking(2), expected)

    def test_frontend_requests_ranking_for_selected_generation(self) -> None:
        self.assertIn("'/api/ranking'+(uiState.generation!==''", HTML)
        self.assertIn("loadMatch();refresh()", HTML)

    def test_eta_is_estimated_before_first_match_from_duration_and_workers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "matches").mkdir()
            (run / "config.json").write_text(
                '{"competitors":4,"matches_per_pair":1,"generations":5,"workers":4,"match_duration":600}',
                encoding="utf-8",
            )
            (run / "live.json").write_text(
                '{"status":"running","generation":1,"completed_matches":0,'
                '"generation_eta_seconds":null,"run_eta_seconds":null}', encoding="utf-8"
            )
            handler = object.__new__(DashboardHandler)
            handler.run_dir = run
            status = handler.read_live_status()
            self.assertEqual(status["eta_source"], "live_telemetry_progress")
            self.assertEqual(status["generation_eta_seconds"], 900.0)
            self.assertEqual(status["run_eta_seconds"], 4500.0)

    def test_live_following_tracks_newest_match_without_overlapping_refreshes(self) -> None:
        self.assertIn("let refreshing=false", HTML)
        self.assertIn("if(refreshing)return", HTML)
        self.assertIn("configureReplayMenus(uiState.followLive)", HTML)
        self.assertIn("configureReplayMenus(true)", HTML)
        self.assertIn("async function refreshLiveMatch", HTML)
        self.assertIn("else if(matchData?.live)await refreshLiveMatch()", HTML)
        self.assertIn("if(matchData.live){animationId=requestAnimationFrame(animate);return}", HTML)

    def test_self_field_is_visibly_excluded(self) -> None:
        self.assertIn("selfExcluded=object==='ally'", HTML)
        self.assertIn("Campo próprio excluído", HTML)


if __name__ == "__main__":
    unittest.main()
