# Rulecam developer commands (task book §3.3). `make check` must pass before every push.
.PHONY: setup fmt lint type test check ui-check api ui preflight golden live-test remotes sync-main

UI_EXISTS := $(wildcard ui/package.json)

setup:
	uv sync --group dev
	uv run pre-commit install
ifneq ($(UI_EXISTS),)
	cd ui && npm ci
endif

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy src tests

test:
	uv run pytest --cov --cov-report=json --cov-report=term-missing:skip-covered
	uv run python scripts/coverage_floors.py coverage.json

check: lint type test
ifneq ($(UI_EXISTS),)
	$(MAKE) ui-check
endif

ui-check:
	cd ui && npm run lint && npm run typecheck && npm test -- --run

api:
	uv run uvicorn rulecam.api.app:app --reload

ui:
	cd ui && npm run dev

preflight:
	uv run python -m rulecam.replay.preflight

golden:
	RULECAM_MODE=record uv run rulecam golden record $(SCENARIO)

live-test:
	uv run pytest -m live

# --- GitHub (code + PRs) and GitLab (CI) ------------------------------------------------------
# One-time per clone: make every `git push` go to GitHub AND the GitLab CI mirror.
#   make remotes GITLAB=https://gitlab.com/<namespace>/<project>.git
GITHUB := https://github.com/jeevithg090/Axi0N_Meta.git

remotes:
ifndef GITLAB
	$(error usage: make remotes GITLAB=https://gitlab.com/<namespace>/<project>.git)
endif
	git remote set-url --push origin $(GITHUB)
	git remote set-url --add --push origin $(GITLAB)
	git remote get-url --all --push origin

# After squash-merging a PR on GitHub, update main on the GitLab mirror so its pipeline runs.
sync-main:
	git fetch origin main
	git push origin origin/main:refs/heads/main
