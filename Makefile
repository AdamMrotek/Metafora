# metafora.care — one entry point for running and checking the app.
#
# The backend is a single Python process (FastAPI + Pipecat); the SFU and the
# two frontends are the only other things that run. `make dev` starts all four
# in one terminal; `make api` / `make sfu` / `make web` / `make dash` are the
# same four if you'd rather have a terminal each.

SHELL := /bin/bash
.DEFAULT_GOAL := help

API_PORT ?= 3000
WEB_PORT ?= 5173
DASH_PORT ?= 5174
SFU_PORT ?= 7880
DEV_PORTS := $(API_PORT) $(WEB_PORT) $(DASH_PORT) $(SFU_PORT)

C_API := \033[36m
C_SFU := \033[35m
C_WEB := \033[32m
C_DASH := \033[33m
C_DIM := \033[2m
C_OFF := \033[0m

.PHONY: help setup install dev api sfu web stop restart ports \
        check test test-pg test-e2e lint typecheck imports build contracts e2e dash \
        logs latency safety doctor clean guard-env \
        sleep wake fly-status guard-fly

## ---- running ---------------------------------------------------------------

dev: guard-env ## Start SFU + backend + both frontends together (ctrl-c stops all)
	@printf '%b\n' "$(C_DIM)sfu :$(SFU_PORT)   api :$(API_PORT)   web :$(WEB_PORT)   dash :$(DASH_PORT)$(C_OFF)"
	@printf '%b\n' "$(C_DIM)patient http://localhost:$(WEB_PORT)   clinician http://localhost:$(DASH_PORT)$(C_OFF)"
	@echo
	@set -m; \
	trap 'trap - INT TERM EXIT; printf "\nstopping...\n"; kill -TERM %1 %2 %3 %4 2>/dev/null; wait 2>/dev/null; exit 0' INT TERM EXIT; \
	livekit-server --dev 2>&1 | awk '{ print "$(C_SFU)[sfu]$(C_OFF) " $$0; fflush() }' & \
	sleep 1; \
	uv run python -m services.core.app 2>&1 | awk '{ print "$(C_API)[api]$(C_OFF) " $$0; fflush() }' & \
	npm run --silent dev:call 2>&1 | awk '{ print "$(C_WEB)[web]$(C_OFF) " $$0; fflush() }' & \
	npm run --silent dev:dashboard 2>&1 | awk '{ print "$(C_DASH)[dash]$(C_OFF) " $$0; fflush() }' & \
	wait

api: guard-env ## Backend only — FastAPI + Pipecat, on :3000
	uv run python -m services.core.app

sfu: ## LiveKit SFU only, on :7880 (dev credentials)
	livekit-server --dev

web: ## Patient portal only — Vite, on :5173
	npm run dev:call

dash: ## Clinician portal only — Vite, on :5174. Needs SUPABASE_URL + SUPABASE_ANON_KEY
	npm run dev:dashboard

stop: ## Kill whatever is listening on the dev ports
	@for port in $(DEV_PORTS); do \
	  pids=$$(lsof -ti tcp:$$port -sTCP:LISTEN 2>/dev/null); \
	  if [ -z "$$pids" ]; then \
	    printf '  :%-5s free\n' "$$port"; \
	  else \
	    printf '  :%-5s killing %s\n' "$$port" "$$(echo $$pids | tr '\n' ' ')"; \
	    kill $$pids 2>/dev/null || true; \
	    sleep 1; \
	    pids=$$(lsof -ti tcp:$$port -sTCP:LISTEN 2>/dev/null); \
	    [ -n "$$pids" ] && kill -9 $$pids 2>/dev/null || true; \
	  fi; \
	done

restart: stop dev ## Free the ports, then start everything

ports: ## Show what is holding the dev ports
	@out=$$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR==1 || /:($(API_PORT)|$(WEB_PORT)|$(SFU_PORT)) /'); \
	if [ "$$(echo "$$out" | wc -l)" -gt 1 ]; then echo "$$out"; else echo "  all dev ports free"; fi

## ---- setup -----------------------------------------------------------------

setup: install ## Install everything and create .env from the example
	@test -f .env || { cp .env.example .env; echo "  created .env — put your GROQ_API_KEY in it"; }

install: ## uv sync + npm install + the browser `make test-e2e` drives
	uv sync
	npm install
	uv run playwright install chromium

doctor: ## Check the prerequisites are in place
	@ok=0; \
	for bin in uv node npm livekit-server jq; do \
	  if command -v $$bin >/dev/null 2>&1; then \
	    printf '  \033[32mok\033[0m    %-16s %s\n' "$$bin" "$$(command -v $$bin)"; \
	  else \
	    printf '  \033[31mmissing\033[0m %-13s\n' "$$bin"; ok=1; \
	  fi; \
	done; \
	if [ -f .env ]; then \
	  if grep -qE '^GROQ_API_KEY=.+' .env; then printf '  \033[32mok\033[0m    %-16s GROQ_API_KEY set\n' ".env"; \
	  else printf '  \033[31mmissing\033[0m %-13s GROQ_API_KEY is empty\n' ".env"; ok=1; fi; \
	else printf '  \033[31mmissing\033[0m %-13s run: make setup\n' ".env"; ok=1; fi; \
	test -d .venv && printf '  \033[32mok\033[0m    %-16s\n' ".venv" || printf '  \033[31mmissing\033[0m %-13s run: make install\n' ".venv"; \
	exit $$ok

## ---- checks ----------------------------------------------------------------

check: test lint typecheck ## Everything CI runs except the schema (make test-pg) and browser (make test-e2e) tests

test: ## pytest — no API key, no LiveKit needed
	uv run pytest

test-e2e: guard-env ## Browser tests — real Chromium, real SFU, real backend
	uv run pytest -m browser

test-pg: ## Schema tests against a throwaway Postgres in Docker
	@docker inspect metafora-pg >/dev/null 2>&1 \
		|| docker run -d --name metafora-pg -e POSTGRES_PASSWORD=postgres \
			-p 55432:5432 postgres:17 >/dev/null
	@docker start metafora-pg >/dev/null 2>&1 || true
	@until docker exec metafora-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
	TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/postgres \
		uv run pytest -m postgres

lint: ## ruff
	uv run ruff check .

typecheck: ## Frontend + the generated contracts
	npm run typecheck

build: ## Production build of both frontends
	npm run build:call
	npm run build:dashboard

contracts: ## Regenerate shared/contracts from the pydantic models
	uv run python scripts/gen_contracts.py

e2e: guard-env ## Drive a real call — needs a live backend. usage: make e2e ROOM=<roomName>
	@test -n "$(ROOM)" || { echo "usage: make e2e ROOM=<roomName>"; exit 1; }
	uv run python tests/e2e/patient.py $(ROOM)

## ---- session logs ----------------------------------------------------------

logs: ## Tail the most recent session log
	@f=$$(ls -t logs/*.jsonl 2>/dev/null | head -1); \
	test -n "$$f" || { echo "no session logs yet"; exit 0; }; \
	printf '%b\n' "$(C_DIM)$$f$(C_OFF)"; tail -f "$$f"

latency: ## Per-turn latency across every session
	@jq -c 'select(.type=="latency.turn")' logs/*.jsonl 2>/dev/null || echo "no session logs yet"

safety: ## Every safety decision across every session
	@jq -c 'select(.type=="safety.scanned")' logs/*.jsonl 2>/dev/null || echo "no session logs yet"

clean: ## Remove caches and build output (session logs are kept)
	rm -rf .pytest_cache .ruff_cache frontend/call/dist frontend/dashboard/dist
	find . -name __pycache__ -type d -not -path './.venv/*' -not -path './node_modules/*' -prune -exec rm -rf {} +

## ---- fly.io ----------------------------------------------------------------
#
# The machine bills per second it runs, and most seconds nobody is calling. So
# it is switched off by hand, between demos, and switched back on before the
# next one. Deliberately by hand: `fly.toml` keeps both `auto_stop_machines`
# and `auto_start_machines` off, because neither half of that decision can be
# made by the proxy. It cannot stop us safely — a call in progress is outbound
# to LiveKit on both ends, so a machine four minutes into an interview looks
# to the proxy like one that has served nothing since `POST /session`, and it
# would hang up on a patient mid-sentence. And once nothing stops us
# automatically, nothing needs to start us automatically either.
#
# The cost of that symmetry is that `make sleep` is a real off switch: the app
# is *down*, and stays down until `make wake`. Someone who follows the link
# meanwhile gets an error, not a slow page. Sleep it when nobody has the link;
# wake it before anyone does. `make fly-status` if you are unsure which it is.
#
# Stopping is a SIGTERM, which is the same drain a deploy does — every call in
# progress hears the assistant sign off (kill_timeout = 30s in fly.toml).

FLY_APP  ?= metafora
FLY_HOST ?= $(FLY_APP).fly.dev

sleep: guard-fly ## Stop the fly.io machines — the app is down until `make wake`
	@ids=$$(flyctl machines list --app $(FLY_APP) --json | jq -r '.[].id'); \
	test -n "$$ids" || { echo "  no machines on $(FLY_APP)"; exit 0; }; \
	for id in $$ids; do \
	  printf '  stopping %s ' "$$id"; \
	  flyctl machine stop "$$id" --app $(FLY_APP) >/dev/null && echo "ok" || { echo "failed"; exit 1; }; \
	done; \
	printf '%b\n' "$(C_DIM)stopped — https://$(FLY_HOST) is down until: make wake$(C_OFF)"

wake: guard-fly ## Start the fly.io machines and wait for /health (run before a demo)
	@ids=$$(flyctl machines list --app $(FLY_APP) --json | jq -r '.[].id'); \
	test -n "$$ids" || { echo "  no machines on $(FLY_APP) — deploy first"; exit 1; }; \
	for id in $$ids; do \
	  printf '  starting %s ' "$$id"; \
	  flyctl machine start "$$id" --app $(FLY_APP) >/dev/null && echo "ok" || { echo "failed"; exit 1; }; \
	done; \
	printf '  waiting for https://$(FLY_HOST)/health '; \
	for i in $$(seq 1 60); do \
	  if curl -fsS --max-time 5 "https://$(FLY_HOST)/health" >/dev/null 2>&1; then \
	    printf '\n'; printf '%b\n' "$(C_DIM)up: https://$(FLY_HOST)$(C_OFF)"; exit 0; \
	  fi; \
	  printf '.'; sleep 2; \
	done; \
	printf '\n  never became healthy — flyctl logs --app $(FLY_APP)\n'; exit 1

fly-status: guard-fly ## What the fly.io machines are doing right now
	@flyctl status --app $(FLY_APP)

## ---- internals -------------------------------------------------------------

guard-fly:
	@command -v flyctl >/dev/null 2>&1 || { echo "flyctl not installed — brew install flyctl"; exit 1; }
	@command -v jq >/dev/null 2>&1 || { echo "jq not installed — brew install jq"; exit 1; }
	@flyctl status --app $(FLY_APP) >/dev/null 2>&1 || { echo "cannot reach app $(FLY_APP) — flyctl auth login"; exit 1; }

guard-env:
	@test -f .env || { echo "missing .env — run: make setup"; exit 1; }
	@grep -qE '^GROQ_API_KEY=.+' .env || { echo "GROQ_API_KEY is empty in .env"; exit 1; }

help: ## Show this help
	@echo "metafora.care"
	@echo
	@awk 'BEGIN { FS = ":.*## " } \
	     /^## ----/ { sub(/^## ---- /, ""); sub(/ *-*$$/, ""); printf "\n\033[1m%s\033[0m\n", $$0; next } \
	     /^[a-zA-Z0-9_-]+:.*## / { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo
