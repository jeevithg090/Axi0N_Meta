"""`rulecam scorecard --checks auto`: run the checks.yaml criteria that a script can check.

Each check returns a `Check` whose `evidence` says exactly what was observed (a URL and its HTTP
status, a CI job link, a script's JSON line). A check passes only on positive evidence; a check
that could not run, or whose decision is someone else's (a pending owner passphrase, the GHCR
visibility decision), stays unmeasured (`passed: None`) with the reason in its evidence, and is
shown as NOT MEASURED. Nothing here changes the deployment: restarts and budget changes are done
by the pod-side scripts in `scripts/ops/`, whose JSON output is read back (`--restart-evidence`,
`--budget-evidence`).

- `guardrail_tests`: the `guardrails` job of the latest completed `ci` run on main (`gh api`);
- `evidence_routes_private`: on the public API, `/evidence/{day}/chain` is 403 without a session
  and `/hashes` is 200;
- `survives_restart`: `scripts/ops/restart_check.sh` JSON (restart through `start.sh`, plan
  restored);
- `budget_alarm`: `scripts/ops/budget_alarm_check.sh` JSON (low budget, alarm seen, budget
  restored);
- `metrics_dashboard`: `GET /metrics` is 200 with Prometheus `rulecam_*` series;
- `scene_text_firewall`: `/runtime/metrics` → `response.firewall` is `on` (else FAIL: degraded);
- `one_command_deploy`: `runpod_up.sh --dry-run` exits 0; the real run stays pending (GHCR);
- `golden_replay_live`: the site serves the golden run's page, the verify page and the committed
  chain;
- `owner_login`: `/auth/me` → `login_enabled` (pending until the owner sets a passphrase).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from rulecam.eval.scorecard import Check, Checks

DEFAULT_REPO = "jeevithg090/Axi0N_Meta"
CI_WORKFLOW = "ci.yml"
GUARDRAILS_JOB = "guardrails"
METRIC_PREFIX = "rulecam_"
HTTP_OK = 200
HTTP_FORBIDDEN = 403
TIMEOUT_S = 20.0

Runner = Callable[[Sequence[str]], tuple[int, str]]


def run_command(argv: Sequence[str]) -> tuple[int, str]:
    """Run a command (no shell); return its exit code and stdout plus stderr."""
    done = subprocess.run(  # noqa: S603 - fixed argv built by this module or the owner's flag
        list(argv), capture_output=True, text=True, timeout=600, check=False
    )
    return done.returncode, done.stdout + done.stderr


@dataclass
class AutoConfig:
    """Where the checks look.

    Attributes:
        api: The public API base URL (the RunPod proxy), never loopback: privacy is checked as
            the public sees it.
        ui: The public site.
        repo: The GitHub repository whose CI result counts.
        restart_evidence: `restart_check.sh` output, if it was run.
        budget_evidence: `budget_alarm_check.sh` output, if it was run.
        deploy_dry_run: The dry-run command (`None` skips the deploy check).
        golden_chain: The committed golden chain the site must serve byte for byte.
        golden_name: That golden run's name.
    """

    api: str
    ui: str
    repo: str = DEFAULT_REPO
    restart_evidence: Path | None = None
    budget_evidence: Path | None = None
    deploy_dry_run: Sequence[str] | None = ("bash", "scripts/deploy/runpod_up.sh", "--dry-run")
    golden_chain: Path = Path("scenarios/warehouse_driver/chain.jsonl")
    golden_name: str = "warehouse_driver"


def _get(http: httpx.Client, url: str) -> httpx.Response | str:
    try:
        return http.get(url, timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        return f"GET {url} failed: {type(exc).__name__}"


def _unmeasured(why: str) -> Check:
    return Check(passed=None, evidence=why)


def guardrail_tests(config: AutoConfig, run: Runner) -> Check:
    """The `guardrails` job of the latest completed `ci` run on main."""
    base = f"repos/{config.repo}/actions"
    code, out = run(
        [
            "gh", "api",
            f"{base}/workflows/{CI_WORKFLOW}/runs?branch=main&status=completed&per_page=1",
            "--jq", ".workflow_runs[0].id",
        ]
    )  # fmt: skip
    run_id = out.strip()
    if code != 0 or not run_id.isdigit():
        return _unmeasured(f"no CI run found (gh api exit {code})")
    code, out = run(["gh", "api", f"{base}/runs/{run_id}/jobs", "--jq", ".jobs"])
    try:
        jobs = json.loads(out) if code == 0 else []
    except json.JSONDecodeError:
        jobs = []
    job = next((j for j in jobs if j.get("name") == GUARDRAILS_JOB), None)
    if job is None:
        return _unmeasured(f"no `{GUARDRAILS_JOB}` job in CI run {run_id}")
    passed = job.get("conclusion") == "success"
    return Check(passed=passed, evidence=f"CI {job.get('conclusion')}: {job.get('html_url')}")


def _latest_day(http: httpx.Client, api: str) -> str | None:
    reply = _get(http, f"{api}/evidence/days")
    if isinstance(reply, str) or reply.status_code != HTTP_OK:
        return None
    days = reply.json()
    return str(days[-1]) if isinstance(days, list) and days else None


def evidence_routes_private(config: AutoConfig, http: httpx.Client) -> Check:
    """Without a session the public gets hashes, never the chain (with candidate traces)."""
    day = _latest_day(http, config.api)
    if day is None:
        return _unmeasured(f"{config.api}/evidence/days returned no day")
    chain = _get(http, f"{config.api}/evidence/{day}/chain")
    hashes = _get(http, f"{config.api}/evidence/{day}/hashes")
    if isinstance(chain, str) or isinstance(hashes, str):
        return _unmeasured(chain if isinstance(chain, str) else str(hashes))
    n = hashes.json().get("n") if hashes.status_code == HTTP_OK else None
    passed = chain.status_code == HTTP_FORBIDDEN and hashes.status_code == HTTP_OK
    return Check(
        passed=passed,
        evidence=(
            f"public {config.api}/evidence/{day}/chain → {chain.status_code}, "
            f"/hashes → {hashes.status_code} (n={n})"
        ),
    )


def _json_line(path: Path | None, what: str) -> dict[str, object] | str:
    if path is None:
        return f"not run: {what} (pass its JSON output)"
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        data = json.loads(lines[-1])
    except (OSError, IndexError, json.JSONDecodeError) as exc:
        return f"{path}: unreadable ({type(exc).__name__})"
    return data if isinstance(data, dict) else f"{path}: not a JSON object"


def survives_restart(config: AutoConfig) -> Check:
    """`scripts/ops/restart_check.sh` output: healthy again and the approved plan restored."""
    data = _json_line(config.restart_evidence, "scripts/ops/restart_check.sh on the API pod")
    if isinstance(data, str):
        return _unmeasured(data)
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    restored = checks.get("plan_restored") if isinstance(checks, dict) else None
    passed = data.get("passed") is True and restored is True
    return Check(
        passed=passed,
        evidence=(
            f"restart_check.sh {data.get('started_at')}: up after {data.get('up_after_s')} s, "
            f"plan {data.get('plan_restored')} restored={restored} ({data.get('note', '')})"
        ),
    )


def budget_alarm(config: AutoConfig) -> Check:
    """`scripts/ops/budget_alarm_check.sh` output: the alarm showed and the budget came back."""
    data = _json_line(config.budget_evidence, "scripts/ops/budget_alarm_check.sh on the API pod")
    if isinstance(data, str):
        return _unmeasured(data)
    passed = data.get("passed") is True
    return Check(
        passed=passed,
        evidence=(
            f"budget_alarm_check.sh {data.get('started_at')}: budget "
            f"{data.get('test_budget')} $/camera-h → alarm={data.get('alarm_seen')} "
            f"over_budget={data.get('over_budget')}; restored to {data.get('restored_budget')}"
        ),
    )


def metrics_dashboard(config: AutoConfig, http: httpx.Client) -> Check:
    """`GET /metrics` serves Prometheus text with the runtime's series."""
    reply = _get(http, f"{config.api}/metrics")
    if isinstance(reply, str):
        return _unmeasured(reply)
    series = [ln for ln in reply.text.splitlines() if ln.startswith(METRIC_PREFIX)]
    passed = reply.status_code == HTTP_OK and bool(series)
    return Check(
        passed=passed,
        evidence=f"{config.api}/metrics → {reply.status_code}, {len(series)} rulecam_* series",
    )


def _runtime_response(http: httpx.Client, api: str) -> Mapping[str, object] | str:
    reply = _get(http, f"{api}/runtime/metrics")
    if isinstance(reply, str):
        return reply
    if reply.status_code != HTTP_OK:
        return f"{api}/runtime/metrics → {reply.status_code}"
    response = reply.json().get("response")
    return response if isinstance(response, dict) else f"{api}/runtime/metrics has no response"


def scene_text_firewall(config: AutoConfig, http: httpx.Client) -> Check:
    """The live runtime's firewall: `on` passes; `degraded` is reported honestly as a failure."""
    response = _runtime_response(http, config.api)
    if isinstance(response, str):
        return _unmeasured(response)
    state = response.get("firewall")
    reason = response.get("firewall_reason") or ""
    return Check(
        passed=state == "on",
        evidence=f"/runtime/metrics firewall={state}" + (f": {reason}" if reason else ""),
    )


def one_command_deploy(config: AutoConfig, run: Runner) -> Check:
    """The dry run must pass; the criterion itself (a clean account) stays pending."""
    if config.deploy_dry_run is None:
        return _unmeasured("deploy dry run skipped")
    code, out = run(config.deploy_dry_run)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    if code != 0:
        return Check(passed=False, evidence=f"runpod_up.sh --dry-run exit {code}: {tail}")
    return _unmeasured(
        f"dry run passes (exit 0, {tail!s}); a real run on a clean account is pending the "
        "GHCR image visibility decision"
    )


def golden_replay_live(config: AutoConfig, http: httpx.Client) -> Check:
    """The site serves the golden REPLAY page, the verify page and the committed chain."""
    name = config.golden_name
    urls = (
        f"{config.ui}/demo?demo={name}",
        f"{config.ui}/verify/index.html",
        f"{config.ui}/golden/{name}/chain.jsonl",
    )
    replies = [_get(http, url) for url in urls]
    failed = next((r for r in replies if isinstance(r, str)), None)
    if failed is not None:
        return _unmeasured(failed)
    page, verify, chain = (r for r in replies if not isinstance(r, str))
    try:
        committed = hashlib.sha256(config.golden_chain.read_bytes()).hexdigest()
    except OSError:
        return _unmeasured(f"no committed golden chain at {config.golden_chain}")
    served = hashlib.sha256(chain.content).hexdigest() if chain.status_code == HTTP_OK else None
    passed = page.status_code == HTTP_OK and verify.status_code == HTTP_OK and served == committed
    return Check(
        passed=passed,
        evidence=(
            f"{config.ui}/demo?demo={name} → {page.status_code}, /verify/index.html → "
            f"{verify.status_code}, chain sha256 {'matches' if served == committed else 'differs'}"
            f" ({(served or 'none')[:12]})"
        ),
    )


def owner_login(config: AutoConfig, http: httpx.Client) -> Check:
    """Passphrase login is set up on the public API (`/auth/me` → `login_enabled`)."""
    reply = _get(http, f"{config.api}/auth/me")
    if isinstance(reply, str) or reply.status_code != HTTP_OK:
        return _unmeasured(reply if isinstance(reply, str) else f"/auth/me → {reply.status_code}")
    if reply.json().get("login_enabled") is True:
        return Check(passed=True, evidence=f"{config.api}/auth/me → login_enabled=true")
    return _unmeasured(
        f"{config.api}/auth/me → login_enabled=false: pending the owner's passphrase "
        "(`rulecam owner set-passphrase`)"
    )


def run_auto_checks(
    config: AutoConfig, *, http: httpx.Client | None = None, run: Runner = run_command
) -> dict[str, Check]:
    """Run every automated check; each one's evidence says what was observed.

    Args:
        config: Where to look.
        http: The HTTP client (tests pass one with a mock transport).
        run: Runs commands (`gh`, the deploy dry run).

    Returns:
        Check by checks.yaml key.
    """
    client = http or httpx.Client(follow_redirects=True)
    try:
        return {
            "guardrail_tests": guardrail_tests(config, run),
            "evidence_routes_private": evidence_routes_private(config, client),
            "survives_restart": survives_restart(config),
            "budget_alarm": budget_alarm(config),
            "metrics_dashboard": metrics_dashboard(config, client),
            "scene_text_firewall": scene_text_firewall(config, client),
            "one_command_deploy": one_command_deploy(config, run),
            "golden_replay_live": golden_replay_live(config, client),
            "owner_login": owner_login(config, client),
        }
    finally:
        if http is None:
            client.close()


def merge_checks(manual: Checks | None, auto: Mapping[str, Check]) -> Checks:
    """The checks file's entries with the automated ones on top (an automated result wins)."""
    merged = dict(manual.checks) if manual is not None else {}
    merged.update(auto)
    return Checks(checks=merged)


def checks_yaml(checks: Checks) -> str:
    """The merged checks in checks.yaml form, for `reports/eval/checks-<date>.yaml`."""
    lines = ["# Generated by `rulecam scorecard --checks auto`; evidence is what was observed."]
    for key, check in sorted(checks.checks.items()):
        data = check.model_dump(exclude_none=True, exclude_defaults=True)
        lines.append(f"{key}: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"
