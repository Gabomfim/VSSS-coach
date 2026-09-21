# Coach Silvs

Vector-field strategies, role-specific training, evolutionary optimization,
experiment dashboards, and VSSS tooling. TraveSim is an external simulator:
this repository connects to a separate TraveSim checkout through VSSProto and
the `--travesim-root` option.

The separation is intentional:

- **Coach Silvs** owns player strategy, candidate representations, optimizers,
  trials, telemetry processing, dashboards, distributed workers, and reports;
- **TraveSim** owns the Webots world, robot physics, network protocol, and
  referee/supervisor implementation.

See [TraveSim integration](integration/travesim/README.md) for the compatible
simulator setup and the replay controller used by the 3D dashboards.

## Data flow

```text
TraveSim vision multicast
        |
        v
VSSProto Environment -> state estimator -> vector fields
        |                                      |
        |                                      v
        |                              nominal velocity
        |                                      |
        |                                      v
        +------------------------- differential controller
                                               |
                                               v
                                  VSSProto wheel commands
```

The current implementation is a deterministic baseline for validating the integration. Genetic programming can replace the scalar expressions and composition weights while preserving the same typed inputs and outputs.

Goal-post recovery is a fixed safety layer and is never evolved. If a robot is
within `0.13 m` of one of the four posts and remains below `0.05 m/s` for
`0.30 s`, it temporarily overrides every tactical field. The recovery combines
repulsion from the post, motion back into the playable field, and motion toward
the centre of the goal mouth. Slip is also detected when displacement stays
below `0.015 m` during the dwell interval despite wheel/physics jitter. Its maximum speed is
reduced to `0.55 m/s` near the post. Hysteresis keeps the override active until
the robot reaches `0.19 m` from the post, preventing rapid switching. This
override has priority over the existing straight-line escape from inside a
goal; neither safety rule belongs to the genetic-programming genome.

Inside either goal, the goal-wall field no longer exits only perpendicular to
the goal line. It targets a point inside the field on the mouth centreline, so
the side and back walls guide the chassis diagonally away from internal corners.
This field remains inactive outside the physical goal volume.

A second fixed override prevents attackers from accumulating in the side-wall
pockets beside the opponent goal. Inside the final `0.24 m`, within `0.15 m` of
a side wall and outside the goal mouth, it commands a `0.65 m/s` diagonal
retreat toward the field centre. Ball approach now aims through the centre of
the enemy goal rather than its nearest post. The dashboard exposes the evolved
fields and all three deterministic overrides separately.

The ball contribution includes an evolved shot-clearance gate. Its genome
contains the minimum ball-speed magnitude (`ball_goal_min_speed`), corridor
radius (`shot_clearance_radius`) and lateral gain (`shot_clearance_gain`). When
the measured speed exceeds the candidate threshold and its trajectory projects
through the opponent goal mouth, attraction is disabled. A robot inside the
forward corridor receives a lateral vector toward the nearest edge; robots
behind the ball or already outside it receive no ball contribution. Collision,
wall, ally, enemy and goal fields remain active. The initial values are
`0.05 m/s`, `0.14 m` and `1.7`, bounded during evolution to `[0.02, 1.50]`,
`[0.08, 0.30]` and `[0.30, 3.00]`, respectively.

Near either opponent goal corner, an evolved lateral finishing gate assigns the
attempt to robots on the same side of the ball. It projects the ball ray onto
the goal line in the current attacking frame. If the ray reaches a corner band,
same-side robots retain their normal ball field; opposite-side robots replace
ball attraction with a retreat opposite to the attack direction. The rule is
mirrored automatically at halftime. Evolved parameters are goal distance
(`corner_gate_goal_distance`, default `0.45 m`), corner crossing band
(`corner_gate_crossing_band`, `0.12 m`), required player-side angle
(`corner_gate_player_angle`, `0.05 rad`) and retreat gain
(`corner_gate_retreat_gain`, `0.55`).

## Install

From the repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
PIP_USER=0 python3.11 -m pip install -e '.[dev]'
```

`make install` already runs the last command; do not run both. Some
machines have `pip` globally configured with `user = true`. `PIP_USER=0`
temporarily disables that setting because `--user` installs are incompatible
with virtual environments.

If installation still reports a user-site error, inspect the source with:

```bash
python -m pip config list -v
env | grep '^PIP_'
```

You may also disable an exported setting for the current terminal:

```bash
unset PIP_USER
```

## Run

Start the simulator from its own checkout in one terminal:

```bash
webots /path/to/travesim/worlds/Match3v3.wbt
```

Start one team in another terminal:

```bash
coach-silvs --team yellow
```

Select the vector-field architecture explicitly with `--field-strategy`:

```bash
coach-silvs --team yellow --field-strategy individual
coach-silvs --team yellow --field-strategy shared
```

Both architectures exclude the controlled robot from its own vector-field
composition. `individual` assigns an independent GP field set to each ally.
`shared` reuses one field set for every ally, while preserving different
learned blocks for allied robots and enemy robots.

For the blue team:

```bash
coach-silvs --team blue
```

The defaults follow TraveSim:

| Stream | Address | Port |
|---|---:|---:|
| Vision multicast | `224.0.0.1` | `10002` |
| Yellow commands | `127.0.0.1` | `20012` |
| Blue commands | `127.0.0.1` | `20013` |

Use `coach-silvs --help` to override endpoints, match duration, wheel limits, and attack direction.

## Official environment and match rules

The default `robocore-vsss-2025` ruleset reproduces RoboCore Mini/VSSS rules
version 3.0, revised on 2025-05-27. Its source of truth is
`coach_silvs/rules.py`, and every evolutionary run records a complete copy in
`config.json`.

Main SI-unit defaults are: a `1.50 x 1.30 m` field, `0.40 x 0.10 m` goals,
`0.05 m` walls, a `42.7 mm`/`46 g` ball, and at most three robots per team.
Regulation time is two 300-second periods; the 300-second halftime is not
included in `--match-duration`. Required elimination matches use two
180-second extra-time periods and then golden goal. Goalkeeper possession and
a stationary ball are limited to five seconds.

At halftime the teams change sides. The client preserves the initial attack
direction in the first period and negates `attack_sign` in the second. Fields
whose meaning depends on the attacking side (ball approach, allies, enemies,
allied goal, and enemy goal) are mirrored horizontally. Local wall repulsion is
not inverted because it depends only on distance to each wall. The dashboard
applies the same rule while the recorded-match timeline moves.

```bash
coach-silvs --team yellow --ruleset robocore-vsss-2025
coach-silvs-evolve run --ruleset robocore-vsss-2025 --matches-per-pair 5
```

The five round-robin repetitions and draw penalty are experimental fitness
choices, not changes to the official match rules. The official rules are
available from the [RoboCore rules page](https://events.robocore.net/rules).

### Currículo por duração das partidas

Treinos podem começar com episódios curtos e aumentar gradualmente a duração.
O currículo abaixo agenda globalmente 10 partidas de 30 segundos, 10 de um
minuto e 5 de cinco minutos:

```bash
PYTHONPATH=src python3.11 -m coach_silvs.evolution run \
  --competitors 2 \
  --generations 25 \
  --matches-per-pair 1 \
  --baseline-matches 0 \
  --workers 1 \
  --early-stopping none \
  --field-strategy shared \
  --formation random \
  --backend travesim \
  --webots-mode fast \
  --match-duration-curriculum '10x30s,10x1m,5x5m' \
  --run-name curriculo-10x30-10x60-5x300 \
  --output runs
```

Com dois candidatos há exatamente uma partida por geração; portanto, esse
comando produz 25 gerações e 25 partidas. Em populações maiores, o currículo
continua na ordem global das partidas, atravessando os limites de geração. Se
houver mais partidas que as 25 declaradas, a última duração (cinco minutos)
permanece ativa. A duração efetiva fica registrada em cada JSON e é usada pelo
Webots, replay, troca de lados, progresso e estimativas do dashboard. Essas
durações reduzidas são um modo de treinamento, não partidas regulamentares.

## Test

```bash
python -m unittest discover -s tests -v
```

## Role-specific multi-scenario trials

Goalkeeper, defender, and attacker skills can be trained independently. One
candidate trial keeps a worker alive while evaluating the same reproducible
scenario list; every candidate in a generation receives identical ball/player
spawns and initial ball velocities. The default proxy backend is intended for
optimizer development. Defender trials can also use full Webots physics; each
worker keeps one simulator alive for every repositioning of one candidate.

```bash
PYTHONPATH=src python3.11 -m coach_silvs.role_trials \
  --role defender --candidates 32 --scenarios 60 \
  --scenario-duration 5 --generations 20 --workers 4 \
  --optimizer hybrid-surrogate-es --surrogate-learning-rate 0.35 \
  --run-name defender-32x60

PYTHONPATH=src python3.11 -m coach_silvs.role_dashboard \
  --run role-runs/defender-32x60 --port 8100
```

Minimal physical smoke test (rebuild the supervisor first):

```bash
make -C /path/to/travesim/controllers/referee_controller

PYTHONPATH=src python3.11 -m coach_silvs.role_trials \
  --role defender --backend travesim \
  --candidates 2 --scenarios 2 --scenario-duration 2 \
  --generations 1 --workers 2 --webots-mode fast \
  --travesim-root /path/to/travesim \
  --webots /Applications/Webots.app/Contents/MacOS/webots \
  --run-name defender-physics-smoke

PYTHONPATH=src python3.11 -m coach_silvs.role_dashboard \
  --run role-runs/defender-physics-smoke --port 8100
```

This backend supports defender and goalkeeper environments. It runs headless,
records physical telemetry, and reports the same component-loss schema as the
proxy backend. After ranking a generation, only the physical replay of its best
defender is retained. The role dashboard provides a generation selector,
play/pause controls, and a timeline for these winning trials. Its
`Open replay in 3D Webots` button launches the selected winner in the full 3D
field; start the dashboard with valid `--travesim-root` and `--webots` paths.

To compare the best candidate from generation 1 against the best candidate
across all completed generations, render their original recorded training
trajectories in Webots and produce a synchronized video. The command reads the role
(goalkeeper, defender, or attacker), Webots path, scenario seed and trial
duration from the selected run's `config.json`. Every shot has the same length
on both sides, including those that end early. No new physics trial is run by
default, so the video depicts the exact training result and its recorded loss.
A `comparison-before-after` directory is created inside the run; aligned
replays and raw Webots videos are cached there so changing the layout does not
rerun the rendering. Use `--source rerun` only when a fresh physics evaluation
is explicitly wanted; its result may differ from the original training result.

```bash
PYTHONPATH=src python3.11 -m coach_silvs.goalkeeper_compare \
  --run role-runs/goalkeeper-delayed-spin-32x30-16g-20260915 \
  --layout horizontal

PYTHONPATH=src python3.11 -m coach_silvs.goalkeeper_compare \
  --run role-runs/defender-earliest-corridor-16x30-8g-20260914 \
  --layout vertical
```

`horizontal` puts BEFORE on the left and AFTER on the right (1920 × 540);
`vertical` puts BEFORE on top and AFTER below (960 × 1080). Use any role-trial
run directory in `--run`, or specify `--webots`, `--travesim-root`, and
`--output` to override their defaults. The video is saved as
`<role>-before-after-<layout>.mp4` in the comparison directory. FFmpeg and
Webots must be installed.

Defender scenarios use ten aim bands across the ally goal and five initial kick
speeds from 0.25 to 1.25 m/s. The supervisor applies the equivalent rolling
impulse for 30 ms after each reset so Webots does not erase or immediately
dissipate the requested motion.

Goalkeeper scenarios place the active robot at varied positions along its line,
4.5 to 9 cm in front of the goal, across the legal goal mouth. Their five kick
speeds are deliberately harder: 0.75, 1.0625, 1.375, 1.6875, and 2.0 m/s. The
goalkeeper always targets the predicted crossing point on its evolved line in
front of the goal; when the ball is no longer a threat, it returns to that line
instead of following the ball into the field. Run the physical environment by
replacing `--role defender` with `--role goalkeeper` in the command above.

The goalkeeper uses a dedicated differential-drive conversion. Its chassis
axis stays parallel to the goal line and the same aligned wheel pair runs
forward or backward according to the lateral target error, so changing from an
upper to a lower interception does not cause a 180-degree steering turn. On the
first ball contact, a latched clearance state waits for an evolved 20–300 ms
dwell, then commands one in-place 180-degree spin. The robot holds position
during the dwell. It cannot retrigger until the ball moves at least 11 cm away,
which prevents repeated spinning while the ball remains pressed against the
robot. Both line motion and clearance remain bounded by the physical wheel
speed limit.

Defensive scenario generation rejects impossible shots by default. A scenario
is retained only when a robot starting from rest can reach at least one point
of the ball trajectory before it crosses the goal line. The check conservatively
includes a 150 ms reaction delay, acceleration up to 2.5 m/s², the physical
1.7 m/s speed ceiling, robot/ball contact radius, and a 4 cm safety margin. The
accepted set still cycles through all five kick speeds and is shared unchanged
by every candidate in the generation. These assumptions are exposed as
`--feasibility-max-speed`, `--feasibility-max-acceleration`,
`--feasibility-reaction-delay`, and `--feasibility-safety-margin`. Use
`--no-feasible-scenarios` only for an explicit robustness evaluation after
training, not for the learning curriculum.

The dedicated dashboard plots total loss and its `outcome`, `interception`,
`reaction_time`, and `redirection` components. The velocity magnitude uses a
smooth distance profile: low and controlled near the ball, progressively
faster when the player is far away. Each generation stores its scenario seed,
episode losses, aggregate loss, ranking, and best parameter vector.
When the ball is travelling toward the ally goal, the defender receives an
earliest-reachable interception field. It samples the acceleration-aware ball
trajectory up to the goal crossing and selects the first point that the robot
can physically reach under its acceleration, reaction-delay, contact-radius,
and wheel-speed constraints. If no point is conservatively reachable inside
the prediction horizon, it immediately chases the horizon point rather than
waiting near the goal. The old fixed defensive-line predictor remains
available for goalkeeper training and is not discarded.

An ally-goal corridor field provides the inverse of attacking shot-lane
clearance. For a goal-bound ball, it attracts the defender toward the closest
point on the segment from the ball to the centre of the ally goal. This keeps
the robot between ball and goal while the earliest-interception component moves
it forward to meet the ball. Its evolved `defensive_corridor_gain` controls the
balance against univector pursuit and interception.

The bounded interception
parameters are evolved together with the distance-speed and univector
parameters: horizon, defensive-line offset, post margin, minimum threat speed,
interception gain, blending factor, ball-velocity feed-forward,
ball-acceleration feed-forward, interception-time margin, and reachability
urgency. Ball acceleration is differentiated using simulator time and filtered
with an exponential moving average. The target uses a constant-acceleration
projection. Its requested speed is the maximum of the distance profile and the
speed required to reach the target before the ball, plus the velocity and
acceleration feed-forward terms; it remains capped by the physical wheel-speed
limit. The winning replay draws the ball-to-goal corridor, earliest target,
defender-to-target vector, corridor projection, and filtered ball acceleration,
and lists the complete winning parameter vector.

The default `hybrid-surrogate-es` optimizer combines weighted diagonal
evolution-strategy exploration with a ridge-linear local surrogate fitted to
the candidates already evaluated in the generation. A bounded step opposite
the surrogate loss gradient moves the next population mean toward promising
regions while the evolution strategy retains robustness to discontinuous,
noisy Webots outcomes. This is a sample-efficiency aid, not differentiation
through the simulator. Use `--optimizer adaptive-diagonal-es` for the purely
evolutionary alternative. The artifact and optimizer boundary remain suitable
for a future full CMA-ES implementation without changing the dashboard schema.

### Machine-learning and optimization techniques

The role trainer is **not currently reinforcement learning** and does not use
backpropagation through Webots. It uses black-box, population-based
optimization because contacts, goals, resets, collision responses, and safety
states make the physical objective discontinuous and non-differentiable.

Each candidate is a bounded real-valued parameter vector controlling the
univector field and role controller. Its genes include near/far speed, distance
breakpoints, spiral radius and smoothing, interception horizon and gain,
velocity/acceleration feed-forward, defensive-corridor gain, and goalkeeper
contact-to-spin delay. Candidates are evaluated against the same seeded set of
scenarios in a generation. This common-random-numbers design reduces ranking
noise: candidates see identical ball spawns, robot poses, shot directions, and
initial ball speeds.

The aggregate objective is minimized:

```text
L = 0.70 L_outcome + 0.12 L_interception
  + 0.08 L_reaction + 0.10 L_redirection
```

- `L_outcome` is 1 when a goalkeeper/defender concedes (or when an attacker
  fails to score), otherwise 0. An outcome value of 0 means no goals were
  conceded in the evaluated scenarios; it does not alone prove that every ball
  was touched because a shot may miss.
- `L_interception` is the normalized minimum robot–ball distance.
- `L_reaction` is the normalized time until contact.
- `L_redirection` is 0 when contact redirects the ball and 1 otherwise.

The default optimizer has two coupled stages:

1. **Adaptive diagonal evolution strategy.** The best quarter of the population
   is recombined using logarithmic rank weights. Their weighted mean becomes
   the sampling centre. A separate mutation standard deviation is maintained
   for every gene and updated from elite variance, with a lower exploration
   floor equal to 1% of that gene's allowed range. The next generation is drawn
   from bounded Gaussian distributions around this centre.
2. **Surrogate-assisted refinement.** A local ridge-linear regression is fitted
   to standardized candidate parameters and their measured simulator losses.
   The estimated loss gradient proposes a bounded step for the next population
   mean. The learning rate controls this step. This improves sample efficiency,
   while the evolutionary stage remains responsible for exploration and for
   coping with noisy or discontinuous physics.

This is therefore best described as a **surrogate-assisted adaptive diagonal
evolution strategy for simulator-based optimization**. It is related to
evolutionary computation and derivative-free optimization, rather than genetic
programming in the strict sense: the current role trials evolve numerical
parameters of fixed field/controller equations, not expression-tree structure.
The broader match trainer also supports selection, breeding, elitism, and
Gaussian/structural mutation of vector-field candidates.

For reproducibility, every run records the seed, scenarios, candidate vectors,
component losses, optimizer mean and per-gene scales, rankings, telemetry, and
the winning replay. BEFORE/AFTER comparison videos use the original saved
training trajectories by default; `--source rerun` explicitly performs a new
physical evaluation whose result may differ due to simulator variability.

The mathematical tests do not require Webots or VSSProto.

## Modules

- `model.py`: simulator-independent state and vector types;
- `fields.py`: point, segment, ball, obstacle, and wall fields;
- `controller.py`: field composition and differential-drive conversion;
- `client.py`: UDP/VSSProto adapter and state estimation.

## Genetic programming integration point

An evolved individual should implement the same conceptual operation as `VectorFieldStrategy.command`: receive a `WorldState` and robot ID, then return bounded wheel commands. Prefer evolving the nominal vector or its scalar weights. Do not evolve raw socket code or remove deterministic command validation.

## Parallel evolutionary tournament

The tournament command exposes the population, evolutionary operators, stopping
rules, formula limits, parallelism, and scoring policy in the terminal.

### Simple test to run first

Run this small smoke test from the repository root. It only requires Python
3.10 or newer; package installation and Webots are not required:

```bash
make strategy-smoke-test
```

It runs two generations with four candidates, one match per pair, and two
parallel workers. The mock backend is used, so Webots is not required. A
successful execution ends with output similar to:

```text
OK - melhor candidato: g0001-c0001 | score: 8.0 | ranking: 4
```

The exact winning candidate may change if the seed or evolutionary options are
modified. Confirm that these files were created:

```bash
ls runs/smoke-test
```

Expected entries:

```text
candidates.json  config.json  generations  live.json  matches  ranking.json
```

To inspect the result in the dashboard, without installing the package:

```bash
make strategy-smoke-dashboard
```

Then open <http://127.0.0.1:8080>. Stop the dashboard with `Ctrl+C`.

The recorded-match replay shows the current period and an official countdown.
For `robocore-vsss-2025`, each half counts down from `05:00` to `00:00`; the
second half then restarts at `05:00`. Older recorded runs are supported as long
as their `config.json` is present.

If you prefer not to use the Make target, the equivalent command is:

```bash
coach-silvs-evolve run \
  --competitors 4 \
  --matches-per-pair 1 \
  --workers 2 \
  --generations 2 \
  --breeding module \
  --parent-selection tournament \
  --survival elitism \
  --elite-count 1 \
  --mutation mixed \
  --mutation-rate 0.20 \
  --early-stopping none \
  --max-depth 4 \
  --max-nodes 31 \
  --draw-penalty 1 \
  --field-strategy shared \
  --robots-per-team 3 \
  --backend mock \
  --seed 42 \
  --output runs \
  --run-name smoke-test
```

This smoke test validates configuration parsing, candidate generation,
parallel scheduling, match artifacts, scoring, breeding, mutation, ranking, and
dashboard-compatible output. It does not validate Webots physics or real goals.

```bash
coach-silvs-evolve run \
  --competitors 32 \
  --matches-per-pair 5 \
  --workers 8 \
  --generations 100 \
  --breeding module \
  --parent-selection tournament \
  --survival elitism \
  --elite-count 4 \
  --survival-fraction 0.50 \
  --mutation mixed \
  --mutation-rate 0.15 \
  --mutation-scale 0.12 \
  --early-stopping patience \
  --patience 12 \
  --min-delta 0.5 \
  --max-depth 8 \
  --max-nodes 127 \
  --max-constants 16 \
  --max-constant 10 \
  --max-evaluation-ms 2 \
  --draw-penalty 1 \
  --field-strategy individual \
  --robots-per-team 3 \
  --backend mock \
  --seed 42 \
  --output runs \
  --run-name experiment-001
```

The total number of matches in each generation is:

```text
matches = matches_per_pair * competitors * (competitors - 1) / 2
```

`--workers` controls how many matches are evaluated concurrently. A process pool
is preferred. Restricted environments automatically fall back to a thread pool.

### Evolution options

Field architecture:

- `--field-strategy individual`: each controlled ally evolves its own `ball`,
  `ally`, `enemy`, `wall`, `ally_goal`, and `enemy_goal` formula blocks;
- `--field-strategy shared`: all controlled allies use the same six blocks,
  but `ally` and `enemy` remain separate and can evolve differently;
- `--robots-per-team {3,5}`: determines how many independent ally field sets
  are created when `individual` is selected;
- self-interaction is always excluded and is stored as
  `"self_field": "excluded"` in each candidate artifact.

The selected architecture is saved in `config.json` and in every candidate's
`formula` object, so experiments remain reproducible and the dashboard can show
which sharing policy produced a ranking.

Every field block now stores an executable JSON expression tree and a readable
`text` form. The safe operator set is `add`, `mul`, `neg`, `tanh`, and `clip`;
terminals are constants or the normalized state features `distance`,
`time_remaining`, `goal_difference`, and `attack_sign`. The controller and
dashboard evaluate this same tree. Snapshots are written to `formulas.json` and
`generations/formulas-NNNN.json` in addition to each candidate artifact.

Breeding:

- `module`: exchanges contiguous field modules;
- `uniform`: chooses each scalar block independently from either parent;
- `blend`: interpolates parental scalar parameters.

Parent selection:

- `tournament`: chooses the best of a random sample;
- `rank`: samples with probability proportional to rank;
- `top-half`: samples from the best half.

Survival:

- `elitism`: copies `--elite-count` best candidates unchanged;
- `tournament`: selects survivors through local tournaments;
- `rank`: samples survivors according to rank.

Mutation:

- `gaussian`: adds Gaussian noise to constants;
- `point`: applies a fixed positive or negative displacement;
- `reset`: redraws selected constants;
- `subtree`: changes the structural size metadata;
- `mixed`: combines Gaussian and structural mutation.

Early stopping:

- `none`: always runs every generation;
- `patience`: stops after `--patience` generations without `--min-delta` improvement;
- `plateau`: uses the same plateau criterion and is reserved for a future
  multi-metric implementation.

Formula limits:

- `--max-depth`: maximum expression-tree depth;
- `--max-nodes`: maximum number of primitive and terminal nodes;
- `--max-constants`: maximum ephemeral constants;
- `--max-constant`: absolute value limit for constants;
- `--max-evaluation-ms`: intended runtime budget for one formula evaluation.
- `--match-retries`: number of automatic retries after Webots or a controller
  fails (default: 2). Each retry keeps the match ID, removes its partial replay
  and replaces it with the first complete result; failed attempts never affect
  scores or rankings.
- `--telemetry-start-timeout`: restart a match when Webots does not record its
  first frame within this many seconds (default: 60).
- `--telemetry-stall-timeout`: restart a match when an existing live replay
  stops growing for this many seconds (default: 120).

Resume an interrupted experiment without replaying its completed matches:

```bash
PYTHONPATH=src python3.11 -m coach_silvs.evolution resume \
  --run runs/physical-shared-4x5-original-baseline-2 \
  --match-retries 2
```

The resume command loads the original configuration and candidates, validates
every archived match, deletes incomplete recordings and schedules only missing
or corrupt games. It then continues the remaining generations. When W&B is
enabled, `wandb.json` preserves the remote run ID and resume reconnects to that
same run instead of creating a second experiment. Corrected records are
appended because W&B history is immutable, while the metric keys, summary and
`latest` artifact aliases point to the corrected state. Older artifact versions
remain available in W&B for audit and rollback.

### Baseline variants and transfer between simulations

Use `--simulation-mode baseline-variants` to keep every vector-field formula
fixed and evolve only scalar genome parameters around the baseline:

```bash
PYTHONPATH=src python3.11 -m coach_silvs.evolution run \
  --simulation-mode baseline-variants --competitors 10 --generations 5 \
  --baseline-matches 1 --backend mock --run-name baseline-variants-1
```

To transfer the best candidate from a previous simulation, pass its run
directory with `--baseline-from`. The latest `ranking.json` champion (or the
latest generation snapshot) becomes the fixed baseline. Its formula is
preserved and the source run is recorded in `baseline.json`:

```bash
PYTHONPATH=src python3.11 -m coach_silvs.evolution run \
  --simulation-mode baseline-variants --baseline-from runs/baseline-variants-1 \
  --competitors 10 --generations 5 --backend mock \
  --run-name baseline-variants-2
```

The run directory contains immutable match files, generation snapshots,
candidate genomes, the current ranking, the experiment configuration, and a
small `live.json` status file. This makes runs reproducible and lets the
dashboard read results without sharing Python memory with the tournament.

### Fixed baseline quality tracking

Every generation champion is evaluated against a fixed candidate named
`baseline`. It uses the original deterministic fields for allies, opponents,
walls and goals. Its ball field is also the original deterministic Coach Silvs
field: it predicts the ball position, approaches from behind relative to the
opponent goal, combines approach and shot attraction, and adds a tangential
component to correct the arrival angle.
The baseline is never selected for breeding, survival, mutation or ranking.

`--baseline-matches N` sets the number of champion-versus-baseline matches per
generation (default: 1). Results are stored in `baseline-evaluations.json`, the
fixed candidate in `baseline.json`, and complete replays in `matches/`. In the
dashboard, select the generation and then the pair containing `baseline` to
watch these matches and inspect both candidates' vector fields. W&B receives a
`baseline/evaluations` table and the mean champion goal difference.

### Real TraveSim backend

`--backend mock` remains available for fast orchestration/UI tests. Sporting
evaluation uses `--backend travesim`; each worker receives:

- a private temporary world file;
- unique replacer, yellow, blue, and vision ports;
- an isolated Webots runtime directory;
- one yellow and one blue candidate process;
- supervisor goal detection, score updates, resets, and JSONL telemetry;
- a hard timeout and guaranteed process cleanup.

Webots is the 3D physics simulator underneath TraveSim: it integrates robot and
ball motion, contacts, friction, differential-wheel controllers, and the
referee supervisor. Tournament workers launch it with `--batch --minimize
--no-rendering`; controller output goes to a per-match log instead of the
terminal, so parallel matches do not open useful rendering windows or spam the
desktop.

Every placement chooses `balanced`, `defensive`, `offensive`, `wide`, `compact`,
or `diagonal` and adds bounded Gaussian position/orientation error. Pass a name
with `--formation`, or use `random`. The formation and seed are stored in the
match artifact; goal resets restore that match's sampled formation.

External clients need scheduling time between vision and command reads. The
runner uses Webots `fast` mode with `--external-client-delay-ms 2` by default,
and pins local multicast to loopback so parallel matches do not depend on the
active network interface. Minimal physical smoke test:

```bash
coach-silvs-evolve run \
  --competitors 2 --matches-per-pair 1 --workers 1 --generations 1 \
  --early-stopping none --backend travesim \
  --travesim-root /path/to/travesim \
  --match-timeout 180 \
  --formation random --run-name real-smoke
```

Real TraveSim runs enforce the official 600 active seconds. On macOS 15, use a
compatible R2025b Webots build.

### Weights & Biases

```bash
python3.11 -m pip install -e '.[dev,wandb]'
export WANDB_API_KEY='...'
```

Add `--wandb-mode online --wandb-project coach-silvs` to a run. Each generation
logs score/goal metrics and a ranking Table. Configuration, rankings, candidates,
live state, and generation snapshots are uploaded as an artifact; add
`--wandb-log-matches` only when replay files should also be sent. Use
`--wandb-mode offline` to test locally without credentials or upload.
The `vector_fields` Table includes both the readable formula and its complete
JSON tree, while `formulas.json` is included in the final artifact.

## Monitoring dashboard

O dashboard é um processo independente da simulação: ele apenas lê os arquivos
incrementais da execução e pode ser iniciado, reiniciado ou atualizado em outro
terminal sem interromper a evolução. Assim, mantenha a simulação em background e
aponte várias sessões do dashboard para a mesma pasta `runs/<nome>` (em portas
distintas, se necessário).

O rastreamento opcional do W\&B é atualizado no mesmo ritmo das gerações. As
tabelas `ranking`, `genomes` e `vector_fields` permitem acompanhar, ao vivo,
quais blocos e parâmetros das fórmulas estão sendo selecionados. O artefato
final inclui os arquivos JSON das gerações para auditoria ou reprodução.

For multi-computer execution over a private VPN, see
[`docs/DISTRIBUTED.pt-br.md`](../docs/DISTRIBUTED.pt-br.md). The coordinator
uses an authenticated persistent queue; each remote machine leases no more
matches than its configured worker slots, and expired leases return to the
queue automatically.

### One-command real matches

Start physical TraveSim evolution and its live dashboard together:

```bash
coach-silvs-live \
  --run-name physical-001 \
  --port 8080 \
  -- \
  --competitors 10 \
  --matches-per-pair 5 \
  --workers 2 \
  --generations 10 \
  --field-strategy shared \
  --formation random
```

Open <http://127.0.0.1:8080>. The launcher enforces `--backend travesim`; even
if `--backend mock` is passed after `--`, the final backend remains physical.
The dashboard starts while Webots is running, displays `TRAVESIM REAL`, and
adds each replay as soon as its supervisor telemetry is complete. Press
`Ctrl+C` to stop the dashboard and any still-running match processes.

Use `make strategy-real-live` for one physical match using the official two
five-minute periods. Use `make strategy-real-background` to detach the
dashboard and simulations from the terminal. The background command prints the
PID, dashboard URL, log path, and the `kill` command used to stop it.

Start the dashboard in a second terminal while evolution is running:

```bash
coach-silvs-dashboard \
  --run runs/experiment-001 \
  --host 127.0.0.1 \
  --port 8080
```

Open <http://127.0.0.1:8080>. The dashboard refreshes every three seconds and
shows:

- current generation and progress;
- estimated time remaining for the current generation and for the complete run,
  calculated from the observed match throughput;
- best candidate and score;
- complete ranking with wins, draws, losses, and goal difference;
- selectable match replay with play/pause, timeline, a prominent current and
  final scoreboard, separate first- and second-period scores shown together,
  buttons to jump directly to either period, current goal difference, and
  timestamp-based speeds from 0.5x to 4x;
- clickable goal events that jump to the first recorded frame of each score
  change without allowing live refresh to move the selected frame;
- vector-field inspection by object (`ball`, `ally`, `enemy`, `wall`,
  `wall_decluster`, `ally_goal`, and `enemy_goal`);
- arrows with an origin marker, directional arrowhead, and length proportional
  to the relative vector magnitude;
- a player-perspective selector (`ally_0`, `ally_1`, ...) in both architectures;
- an instance selector for every allied or enemy robot that generates a field;
- the selected formula block and its object-specific parameters.

While a physical match is running, its partial supervisor telemetry is exposed
as a live replay. The dashboard follows the newest frame and recalculates the
selected object field from that frame's ball and robot positions. Candidate
genomes are written before matches begin, so inspection does not wait for a
generation ranking.

`wall_decluster` is a deterministic, non-evolved base field. It activates only
when the selected robot and another ally share the same `0.14 m` wall band and
their separation along the wall is below `0.24 m`. Its `0.8` tangential term
separates the robots, while its `0.4` inward term prevents the resulting motion
from remaining trapped against the boundary. The dashboard evaluates it from
the allies recorded in the selected replay frame.

### Shared rotated IFAC 2008 ball field

All three players use the same move-to-goal univector from Lim et al. (IFAC
2008, equations 2 and 4). Two hyperbolic spirals are blended inside a strip of
half-width `d_e`. Operational VSSS implementations use the magnitudes
`abs(y + d_e)` and `abs(y - d_e)` to reproduce Figure 3, documenting the
signed expression in the paper as a typographical issue. The canonical x axis
is `-unit(opponent_goal_center - ball_position)`; after transforming the local
field back to world coordinates, the robot reaches the ball aligned with the
positive kick direction.
The three players share the same ball-field equation, while
`ball_spiral_radius_i`, `ball_spiral_smoothing_i`, and `approach_gain_i` evolve
independently for each player index `i`. Thus the genetic program preserves one
interpretable univector model while allowing three different approach
geometries and strengths. The dashboard's player selector displays the exact
parameters and resulting field for the selected player.

The paper's local frame is rebuilt on every controller tick. Its origin is the
current ball position and its positive x axis is
`unit(opponent_goal_center - ball_position)`. The local univector is then
rotated back into field coordinates. Consequently it follows the live ball
position, aims at the centre of the opponent goal, and mirrors automatically
after the teams change sides. The dashboard executes the same equations over
the canvas and displays `d_e`, `K_r`, the instantaneous rotation angle, and the
complete formula for the selected candidate.

Mutation rate and scale are controlled only by `--mutation-rate` and
`--mutation-scale`. Stationary-ball measurements never alter mutation and are
kept exclusively as diagnostics.

The monitoring page groups the total time below the configured stationary-ball
speed threshold by generation. Its chart shows the per-match mean and
population standard deviation and refreshes while physical matches are still
being recorded.

Stationary-ball detection is incremental for live JSONL telemetry and cached
for completed recordings, so dashboard polling does not repeatedly parse every
frame. It feeds only the generation chart; replay dropdown options are not
colored or annotated by this diagnostic.

Time estimates are updated after every completed match by default. They become
more stable as matches finish and represent the planned run; early stopping can
therefore make the actual completion earlier than the displayed estimate.
While a generation is running, the ranking is recalculated from every completed
match and refreshed every three seconds; it does not wait for the generation to
finish.

For multi-generation runs, the first ETA of a new generation reuses the
throughput observed in previous generations. Live JSONL telemetry is consumed
incrementally and the live ranking reads compact `*.summary.json` files, so the
three-second polling does not repeatedly parse complete physical replays.
**Ao vivo** follows the newest active match; choosing a replay manually disables
that mode until the button is pressed again.

The dashboard replay uses 30 recorded telemetry frames per second and performs
no positional interpolation. Every displayed ball and robot position is an
actual simulator sample; full-rate telemetry remains stored for analysis.

Physical-match fitness also subtracts auditable behavioral penalties. A robot
is considered stationary below `--stationary-speed` after
`--stationary-grace`; it is considered stuck when it remains within
`--stuck-radius` for `--stuck-window` while still moving; and goal occupation is
measured when its center remains behind either goal line inside the official
goal width. Configure their score cost per robot-second with
`--stationary-penalty`, `--stuck-penalty`, and `--goal-penalty`. The ranking
shows the accumulated penalty, with the three durations available in its
tooltip and JSON API.

Regulation defaults to 600 active seconds: two periods of 300 seconds. At
halftime the supervisor pauses physics, mirrors every robot's initial pose to
the opposite half, rotates it by 180 degrees, places the ball at the center,
clears velocities, and resumes. Goal resets in the second period use the
mirrored poses. Attack direction is derived from simulator steps, remaining
synchronized even in Webots fast mode.

Physical matches default to a 1,800-second wall-clock timeout so the official
600 simulated seconds can finish even under parallel CPU load. Live replay
files are partial matches, not completed results; only finalized match JSON
files advance the generation counter and ranking. If evolution exits, the
launcher marks the dashboard status as `failed` instead of leaving it as
apparently running.

The same command is used for a tournament that is still running and for a
historical run. Point `--run` at any directory that contains `ranking.json`,
`live.json`, and `matches/`. During evolution, newly completed matches appear
automatically. For a past experiment, the stored match files remain available
for replay without rerunning evolution.

In `shared` mode, selecting another ally changes the observed perspective while
the displayed formula block remains `shared`. In `individual` mode, it also
changes the player's formula block. For `ally` and `enemy`, select the concrete
source (`ally_0`, `enemy_2`, and so on). Ball and robot positions come from the
currently selected replay frame, so moving the timeline recomputes the field.
Selecting the observed ally as its own allied source displays the intentionally
excluded self-field with no arrows.

The mock backend records synthetic frames for UI tests without Webots. The real
backend records supervisor telemetry in the same match schema and is identified
prominently in the dashboard.

### Quick demonstration

Terminal 1:

```bash
make strategy-evolve-demo
```

Terminal 2:

```bash
make strategy-dashboard-demo
```
