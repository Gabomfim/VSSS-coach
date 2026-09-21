PYTHON ?= python3.11
TRAVESIM_ROOT ?= ../travesim

.PHONY: install test dashboard role-dashboard strategy-install strategy-test \
	strategy-run-yellow strategy-run-blue strategy-evolve-demo \
	strategy-dashboard-demo strategy-smoke-test strategy-smoke-dashboard \
	strategy-real-live strategy-real-background strategy-distributed-coordinator \
	strategy-distributed-worker

install:
	PIP_USER=0 $(PYTHON) -m pip install -e '.[dev]'

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

dashboard:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.dashboard --help

role-dashboard:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.role_dashboard --help

# Compatibility aliases retained for commands documented in earlier runs.
strategy-install: install

strategy-test: test

strategy-run-yellow:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.client --team yellow

strategy-run-blue:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.client --team blue

strategy-evolve-demo:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.evolution run --competitors 8 --matches-per-pair 5 --workers 4 --generations 5 --run-name demo

strategy-dashboard-demo:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.dashboard --run runs/demo --port 8080

strategy-smoke-test:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.evolution run --competitors 4 --matches-per-pair 1 --workers 2 --generations 2 --breeding module --parent-selection tournament --survival elitism --elite-count 1 --mutation mixed --mutation-rate 0.20 --early-stopping none --max-depth 4 --max-nodes 31 --draw-penalty 1 --field-strategy shared --robots-per-team 3 --backend mock --seed 42 --output runs --run-name smoke-test

strategy-smoke-dashboard:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.dashboard --run runs/smoke-test --port 8080

strategy-distributed-coordinator:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.distributed coordinator --host 127.0.0.1 --port 8090

strategy-distributed-worker:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.distributed worker --coordinator "$${COACH_SILVS_COORDINATOR}" --workers "$${COACH_SILVS_WORKERS:-1}" --travesim-root "$(TRAVESIM_ROOT)"

strategy-real-live:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.live --port 8080 -- --competitors 2 --matches-per-pair 1 --workers 1 --generations 1 --early-stopping none --field-strategy shared --formation random --match-timeout 180 --travesim-root "$(TRAVESIM_ROOT)"

strategy-real-background:
	PYTHONPATH=src $(PYTHON) -m coach_silvs.live --background --port 8080 -- --competitors 2 --matches-per-pair 1 --workers 1 --generations 1 --early-stopping none --field-strategy shared --formation random --match-timeout 180 --travesim-root "$(TRAVESIM_ROOT)"
