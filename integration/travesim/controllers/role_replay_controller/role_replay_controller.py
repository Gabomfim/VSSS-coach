"""Replay recorded VSSS Coach role-trial telemetry inside Webots."""

import json
import os
import sys
import time

from controller import Supervisor


def named_nodes(supervisor):
    children = supervisor.getRoot().getField("children")
    nodes = {}
    for index in range(children.getCount()):
        node = children.getMFNode(index)
        name = node.getField("name")
        if name:
            nodes[name.getSFString()] = node
    return nodes


def place(node, state, z):
    node.getField("translation").setSFVec3f([state["x"], state["y"], z])
    node.getField("rotation").setSFRotation([0, 0, 1, state.get("orientation", 0)])
    node.resetPhysics()


supervisor = Supervisor()
replay_path = sys.argv[14]
with open(replay_path, encoding="utf-8") as stream:
    replay = json.load(stream)

nodes = named_nodes(supervisor)
frames = replay.get("frames", [])
time_step = int(supervisor.getBasicTimeStep())
movie_path = os.environ.get("COACH_ROLE_MOVIE_PATH")
if movie_path:
    supervisor.movieStartRecording(movie_path, 960, 540, 0, 70, 1, False)
for frame in frames:
    place(nodes["VssBall"], frame["ball"], 0.022)
    for team, prefix in (("blue", "BlueRobot"), ("yellow", "YellowRobot")):
        for index, state in enumerate(frame.get(team, [])):
            place(nodes[f"{prefix}{index}"], state, 0.0025)
    if supervisor.step(time_step) == -1:
        break

if movie_path:
    supervisor.movieStopRecording()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if supervisor.movieIsReady() and os.path.isfile(movie_path) and os.path.getsize(movie_path) > 10000:
            break
        time.sleep(0.1)
        if supervisor.step(time_step) == -1:
            break
    if supervisor.movieFailed():
        raise RuntimeError("Webots movie recording failed")
    supervisor.simulationQuit(0)
else:
    supervisor.simulationSetMode(Supervisor.SIMULATION_MODE_PAUSE)
