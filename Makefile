# Rulecam developer commands (task book §3.3). `make check` must pass before every push.
.PHONY: setup fmt lint type test check ui-check api ui openapi preflight golden live-test remotes sync-main

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

openapi:
	uv run rulecam openapi ui/src/api/openapi.json
ifneq ($(UI_EXISTS),)
	cd ui && npx openapi-typescript src/api/openapi.json -o src/api/schema.d.ts
endif

ui:
	cd ui && npm run dev

preflight:
	uv run python -m rulecam.replay.preflight

golden:
	RULECAM_MODE=record uv run rulecam golden record $(SCENARIO)

live-test:
	uv run pytest -m live

# --- GitHub (code + PRs) and Gitea (CI) -------------------------------------------------------
# One-time per clone: make every `git push` go to GitHub AND the Gitea CI mirror.
#   make remotes GITEA=https://gitea.com/<owner>/<repo>.git
GITHUB := https://github.com/jeevithg090/Axi0N_Meta.git

remotes:
ifndef GITEA
	$(error usage: make remotes GITEA=https://gitea.com/<owner>/<repo>.git)
endif
	-git config --unset-all remote.origin.pushurl
	git remote set-url --add --push origin $(GITHUB)
	git remote set-url --add --push origin $(GITEA)
	git remote get-url --all --push origin

# After squash-merging a PR on GitHub, update main on the Gitea mirror so its workflow runs.
sync-main:
	git fetch origin main
	git push origin origin/main:refs/heads/main
