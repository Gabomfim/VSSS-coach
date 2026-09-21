# Contributing

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
PIP_USER=0 python3.11 -m pip install -e '.[dev]'
make test
```

Most strategy, optimizer, and dashboard tests do not need Webots. Physical
tests require a separate compatible TraveSim checkout; pass its path with
`--travesim-root` or `TRAVESIM_ROOT`.

## Changes

- Keep simulator-independent vector mathematics in `fields.py` and `model.py`.
- Add tests for changed mathematical or evolutionary behavior.
- Keep safety overrides deterministic and outside evolved expressions.
- Do not commit experiment runs, recordings, credentials, virtual environments,
  or a TraveSim checkout.
- Update `README.md` when commands, parameters, fitness semantics, or the
  TraveSim integration contract change.

Run `make test` before committing. Physical changes should additionally be
validated with a small seeded TraveSim smoke trial.

