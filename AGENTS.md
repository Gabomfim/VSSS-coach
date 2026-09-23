# AGENTS.md

## Scope

These instructions apply to the standalone VSSS Coach repository. TraveSim is
an external dependency and must not be vendored into this repository.

## Architecture

- Keep vector-field mathematics independent from sockets, protobuf, and Webots.
- Keep VSSProto translation in `src/coach_silvs/client.py`.
- Keep differential-drive conversion and safety limits in `controller.py`.
- Access TraveSim only through explicit paths and protocol/integration modules.
- Evolved expressions produce nominal vectors; deterministic safety limits stay
  outside the genome.

## Development

- Target Python 3.10 or newer and use type hints.
- Add tests for mathematical behavior before changing field primitives.
- Protect divisions and normalizations against zero denominators.
- Never allow NaN or infinite wheel commands to reach the simulator.
- Do not commit run outputs, recordings, caches, virtual environments, or a
  TraveSim checkout.

## Verification

Run `make test` from the repository root. Physical smoke tests additionally
require a compatible TraveSim checkout, Webots, and VSSProto.

