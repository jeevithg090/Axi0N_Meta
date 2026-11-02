# Rulecam developer commands (task book §3.3). `make check` must pass before every push.
.PHONY: setup fmt lint type test check ui-check api ui openapi preflight golden live-test \
	eval-replay golden-verify golden-gate verify-page-sync eval-caviar ship-api ship-gpu

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
	cd ui && npm run lint && npm run typecheck && npm test -- --run && npm run test:verify-page

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

# WS0 eval harness in replay mode: validate the eval set and re-score the recorded baseline
# offline (no API, no network). `rulecam eval run` is the live counterpart (see eval/README.md).
eval-replay:
	uv run rulecam eval validate
	uv run rulecam eval score eval/baseline/2026-10-31-live --out $(or $(EVAL_OUT),reports/eval)

# WS6 golden runs: replay every scenarios/<name>.yaml from its committed recording (no network,
# no model); CI runs this on every push. `make verify-page-sync` copies the evidence verify page
# into the site (ui/public/verify), where the golden demo's chain is checked in the browser.
golden-verify:
	@for s in scenarios/*.yaml; do uv run rulecam golden verify "$$(basename "$$s" .yaml)" || exit 1; done

# Add-on 1.2: re-score every golden run with `eval score` and fail on a broken `demo.score` gate
# (a false alert on a negative clip, a missing verified alert, ...). CI runs it after golden-verify.
golden-gate:
	uv run rulecam golden gate

verify-page-sync:
	cp src/rulecam/evidence/verify_page/index.html src/rulecam/evidence/verify_page/chain.js ui/public/verify/

# Add-on 4.1 deploys (docs/deploy.md "Ship the API" / "Ship the GPU code"). ship-api needs
# RULECAM_API_SSH_HOST and RULECAM_API_SSH_PORT; ship-gpu needs a RunPod S3 API key in
# RUNPOD_S3_ACCESS_KEY_ID / RUNPOD_S3_SECRET_ACCESS_KEY. Both ship the committed HEAD (or REF).
ship-api:
	scripts/deploy/ship_api.sh $(or $(REF),HEAD)

ship-gpu:
	scripts/deploy/ship_gpu.sh $(or $(REF),HEAD)


# Add-on 1.3: play every runnable CAVIAR clip (eval/clips/caviar_*) into the API and score it.
# Run ON THE API POD (owner calls are loopback-only); approving each plan replaces the active one.
eval-caviar:
	uv run rulecam eval run $(foreach c,$(notdir $(wildcard eval/clips/caviar_*)),--clip $(c)) \
		--track-store $(or $(TRACK_STORE),data/tracks) $(EVAL_ARGS)
