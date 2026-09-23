# TraveSim integration

VSSS Coach and TraveSim are separate repositories. Commands that use physical
simulation receive the simulator checkout explicitly:

```bash
PYTHONPATH=src python3.11 -m coach_silvs.role_trials \
  --role goalkeeper --backend travesim \
  --travesim-root /path/to/travesim \
  --webots /Applications/Webots.app/Contents/MacOS/webots \
  --candidates 4 --scenarios 10 --generations 1 --workers 4 \
  --run-name goalkeeper-physical-smoke
```

The compatible TraveSim fork must provide the VSSS Coach supervisor additions:
configurable UDP ports, physical match state and telemetry, role-scenario reset
and initial ball velocity, goal attribution, halftime repositioning, and match
recording. These changes belong in TraveSim because they control simulator
physics and the referee.

## Install the 3D replay controller

The replay controller belongs to VSSS Coach because it interprets VSSS Coach
run artifacts. Link or copy it into a local TraveSim checkout:

```bash
ln -s "$PWD/integration/travesim/controllers/role_replay_controller" \
  /path/to/travesim/controllers/role_replay_controller
```

Use an absolute symlink and do not commit it to TraveSim. The role dashboard
and comparison-video command create temporary Webots worlds inside the external
checkout and reference this controller by name.

## Boundary

Do not copy TraveSim worlds, PROTO files, compiled controllers, or upstream
source into VSSS Coach. Conversely, strategy source, optimizers, dashboards,
tests, and run artifacts do not belong in the TraveSim repository.
