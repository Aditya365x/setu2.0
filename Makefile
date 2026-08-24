# SETU — the thirty highest-ROI minutes of the build.
#
# `make reset` and `make demo` matter most: being able to return to a clean,
# known state in five seconds is what lets you rehearse ten times instead of
# three, and rehearsal is what wins the presentation parameter.
#
# No make on your machine? Every target below is one docker compose command,
# so run it directly. On Windows: `winget install GnuWin32.Make`.

COMPOSE ?= docker compose
DC_EXEC  = $(COMPOSE) exec -T api
PSQL     = $(COMPOSE) exec -T db psql -U setu -d setu

.PHONY: help up down logs seed demo flood reset load test psql health

help:
	@echo "make up      - bring the whole stack up (offline-capable)"
	@echo "make seed    - Ganjam boundary, shelters, resources, population, pincodes"
	@echo "make demo    - run the Cyclone Landfall scenario end to end"
	@echo "make flood   - run the shorter Flash Flood scenario"
	@echo "make reset   - clean slate, KEEPS seed data"
	@echo "make test    - pytest (solver tests are the important ones)"
	@echo "make load    - locust: 500 reports/min, print p95"
	@echo "make health  - is the API up?"

up:
	$(COMPOSE) up --build -d
	@echo "waiting for the API..."
	@until curl -sf http://localhost:8000/health >/dev/null 2>&1; do sleep 1; done
	@echo "dashboard   http://localhost:5173"
	@echo "citizen PWA http://localhost:5174"
	@echo "API docs    http://localhost:8000/docs"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api worker

health:
	@curl -s http://localhost:8000/health

seed:
	$(DC_EXEC) python -m seed.load

demo:
	$(DC_EXEC) python -m seed.scenario cyclone_landfall

flood:
	$(DC_EXEC) python -m seed.scenario flash_flood

# Operational tables only. See backend/seed/reset.sql for why this is a DELETE
# in foreign-key order and emphatically not TRUNCATE ... CASCADE.
reset:
	@$(PSQL) -v ON_ERROR_STOP=1 -tA < backend/seed/reset.sql

test:
	$(DC_EXEC) python -m pytest tests -q

load:
	$(DC_EXEC) locust -f tests/locustfile.py --headless -u 50 -r 10 -t 5m --host http://localhost:8000

psql:
	$(COMPOSE) exec db psql -U setu -d setu
