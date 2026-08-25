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
	@echo "make demo         - Cyclone Landfall in Ganjam (use for the optimizer toggle)"
	@echo "make demo-all     - a landfall in every district (use to show coverage)"
	@echo "make demo-district D=9 - one district by id"
	@echo "make districts    - list the 16 seeded districts"
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

# Ganjam only, full scale. USE THIS FOR THE OPTIMIZER TOGGLE: the Greedy vs
# Optimized delta is a scarcity effect, so it needs enough incidents competing
# for the same units. A small or thinly-spread board makes the two strategies
# agree and the toggle shows nothing.
demo:
	$(DC_EXEC) python -m seed.scenario cyclone_landfall --district 1

# Every seeded district gets its own landfall. USE THIS TO SHOW COVERAGE: the
# dashboard's district picker then has a live board wherever it is pointed,
# instead of one populated district and fifteen empty ones.
demo-all:
	$(DC_EXEC) python -m seed.scenario cyclone_landfall --all --reports 320 --duration 6

# One named district, e.g.  make demo-district D=9   (Visakhapatnam)
demo-district:
	$(DC_EXEC) python -m seed.scenario cyclone_landfall --district $(D)

districts:
	$(DC_EXEC) python -m seed.scenario --list-districts

flood:
	$(DC_EXEC) python -m seed.scenario flash_flood --district 1

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
