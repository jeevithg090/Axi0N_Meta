"""One-command RunPod deploy for Rulecam (WS5; docs/deploy.md "One-command deploy").

`up` brings the whole stack up, idempotently, through the RunPod REST API (v2):

1. the network volume for the API pod (CPU pods can only persist to a network volume), reused
   by name or created in a data center that has the CPU flavor in stock and offers volumes;
2. RunPod secrets for the keys found in the local secrets directory (default `~/.runpod`), so
   pods reference them as `{{ RUNPOD_SECRET_<name> }}` and no key value is ever in a pod's
   config, in argv or in this script's output;
3. the Glimmer GPU pod (llama.cpp + Muse Glimmer 30B, `LLAMA_API_KEY`) and the SAM GPU pod
   (ghcr.io/jeevithg090/rulecam-sam), each tried on several GPU types and data centers from the
   live stock catalog until one places (RunPod places exactly one GPU type per create call);
   or, with `--one-gpu`, a single 48 GB pod (ghcr.io/jeevithg090/rulecam-gpu) running both,
   Glimmer on 8080 and SAM on 8090 (docs/deploy.md "One GPU per site");
4. the API CPU pod (8 vCPU, ghcr.io/jeevithg090/rulecam-api, or the documented
   `runpod/base` + `git archive` fallback with `--api-source archive`), wired to the GPU
   pods' proxy URLs, with `RULECAM_GPU_USD_PER_HOUR` set to their price (the cost meter's GPU
   share) and `RULECAM_IDLE_STOP_GPU_PODS` to their names;
5. optionally (`--vercel`) the UI's `NEXT_PUBLIC_RULECAM_API` and a production redeploy.

A pod that already exists (by name) is reused: started when stopped, patched when its image or
env drifted, left alone when it matches. `--dry-run` reads the account (GETs only) and prints
every write it would make, with secrets redacted. `down` stops the pods (`--terminate --yes`
deletes them; the network volume is never deleted). `status` prints state, cost and health.

Stdlib only, so it runs with any Python 3.11+ without the project's venv. The API key comes
from `RUNPOD_API_KEY` or `~/.runpod/config.toml` (`apikey`) and is never printed. Requests
carry a non-Python User-Agent: Cloudflare in front of the API blocks `Python-urllib/*`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API_BASE = "https://api.runpod.io"
USER_AGENT = "rulecam-deploy/1.0 (+https://github.com/jeevithg090/Axi0N_Meta)"
REDACTED = "<redacted>"
REPO_ROOT = Path(__file__).resolve().parents[2]

GHCR = "ghcr.io/jeevithg090"
API_IMAGE = f"{GHCR}/rulecam-api"
SAM_IMAGE = f"{GHCR}/rulecam-sam"
GPU_IMAGE = f"{GHCR}/rulecam-gpu"  # --one-gpu: Glimmer + SAM image mode on one pod
GLIMMER_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda-b11669"  # Muse Glimmer needs >= b10353
ARCHIVE_IMAGE = "runpod/base:1.4.0-ubuntu2204"  # the manual path (docs/deploy.md "API pod")
GLIMMER_ARGS = (
    "--hf-repo", "meta-models/Muse-Glimmer-30B-GGUF",
    "--hf-file", "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf",
    "--mmproj-url",
    "https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/resolve/main/"
    "mmproj-Muse-Glimmer-30B-Q4_K_M.gguf",
    "-a", "muse-glimmer-30b", "-ngl", "99", "-c", "32768", "-np", "2",
    "--host", "0.0.0.0", "--port", "8080", "--jinja",  # noqa: S104 - behind the RunPod proxy
    "--temp", "1.0", "--top-p", "0.95", "--top-k", "64",
)  # fmt: skip
ARCHIVE_BOOT = 'bash -c "(/workspace/rulecam/deploy/api-boot.sh &) ; exec /start.sh"'

# 48 GB first (Glimmer 30B Q4 with a 32k context, SAM video sessions), then 24 GB cards.
GLIMMER_GPUS = (
    "NVIDIA RTX A6000",
    "NVIDIA L40S",
    "NVIDIA A40",
    "NVIDIA RTX 6000 Ada Generation",
    "NVIDIA L40",
)
SAM_GPUS = (
    "NVIDIA RTX A6000",
    "NVIDIA L40S",
    "NVIDIA A40",
    "NVIDIA L4",
    "NVIDIA RTX A5000",
    "NVIDIA GeForce RTX 4090",
)
# --one-gpu: 48 GB cards only. Glimmer (~24 GB at -c 32768 -np 2) and SAM image mode (<= 8 GB,
# SAM_MEMORY_GUARD_GB) share the GPU with headroom (docs/deploy.md "One GPU per site").
ONE_GPU_GPUS = (
    "NVIDIA RTX A6000",
    "NVIDIA A40",
    "NVIDIA L40S",
    "NVIDIA RTX 6000 Ada Generation",
)
CPU_FLAVORS = ("cpu3c", "cpu3g", "cpu5c", "cpu5g")
API_VCPUS = 8
MIN_CUDA = "12.8"
LEVELS = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
PLACEMENT_FAILURES = frozenset({400, 409, 429, 500, 502, 503})
LIVE = frozenset({"RUNNING", "STARTING", "PROVISIONING"})
MAX_ATTEMPTS = 12
CREATE_PATHS = frozenset({"/v2/pods", "/v2/network-volumes", "/v2/account/secrets"})
POLL_S = 10.0
PRICE_PLACEHOLDER = "<gpu pod $/h>"  # dry run of a pod that does not exist yet


@dataclass(frozen=True)
class SecretSpec:
    """A key read from the local secrets directory and stored as a RunPod secret.

    Attributes:
        file: File name in the secrets directory.
        name: The RunPod secret's name.
        required: Whether `up` refuses to run without it (it is otherwise left to the volume).
    """

    file: str
    name: str
    required: bool = False

    def placeholder(self) -> str:
        """The env value that makes RunPod substitute this secret at boot."""
        return "{{ RUNPOD_SECRET_" + self.name + " }}"


GLIMMER_KEY = SecretSpec("rulecam-glimmer.key", "rulecam_glimmer_api_key", required=True)
SAM_KEY = SecretSpec("rulecam-sam.key", "rulecam_sam_api_key", required=True)
HF_TOKEN = SecretSpec("rulecam-hf.token", "rulecam_hf_token")
META_KEY = SecretSpec("rulecam-meta.key", "rulecam_meta_api_key")
BASETEN_KEY = SecretSpec("rulecam-baseten.key", "rulecam_baseten_api_key")
SESSION_SECRET = SecretSpec("rulecam-session.secret", "rulecam_session_secret")  # owner login
UI_PROXY_SECRET = SecretSpec("rulecam-ui-proxy.secret", "rulecam_ui_proxy_secret")
SECRETS = (GLIMMER_KEY, SAM_KEY, HF_TOKEN, META_KEY, BASETEN_KEY, SESSION_SECRET, UI_PROXY_SECRET)


class DeployError(Exception):
    """A deploy step failed; the message says what to do."""


class ApiError(DeployError):
    """The RunPod API answered with an error status."""

    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        """Record the failed call (never its headers or body)."""
        super().__init__(f"{method} {path} -> {status}: {detail}")
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------------------------
# HTTP


Transport = Callable[[str, str, Mapping[str, Any] | None], Any]


def read_api_key(config: Path) -> str:
    """Return the RunPod API key from `RUNPOD_API_KEY` or runpodctl's config file.

    Raises:
        DeployError: When neither has one.
    """
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if key:
        return key
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"no RUNPOD_API_KEY and {config} is unreadable ({type(exc).__name__})"
        raise DeployError(msg) from None
    key = str(data.get("apikey") or data.get("api_key") or "").strip()
    if not key:
        msg = f"{config} has no apikey; run `runpodctl config --apiKey ...` first"
        raise DeployError(msg)
    return key


def http_transport(base_url: str, api_key: str, *, timeout_s: float = 30.0) -> Transport:
    """Build a JSON transport for the RunPod REST API (Bearer auth, non-Python User-Agent)."""

    def send(method: str, path: str, body: Mapping[str, Any] | None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(  # noqa: S310 - fixed https base (or a test server)
            base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
                raw = response.read()
        except urllib.error.HTTPError as exc:
            route = path.split("?", maxsplit=1)[0]
            raise ApiError(method, route, exc.code, _error_detail(exc)) from None
        except urllib.error.URLError as exc:
            msg = f"{method} {path.split('?', maxsplit=1)[0]}: {exc.reason}"
            raise DeployError(msg) from None
        return json.loads(raw) if raw else None

    return send


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read() or b"{}")
    except ValueError:
        return exc.reason or "error"
    if isinstance(body, dict):
        errors = body.get("errors") or []
        return "; ".join([str(body.get("detail") or body.get("title") or exc.reason), *errors])
    return str(exc.reason)


def offline_transport(method: str, path: str, _body: Mapping[str, Any] | None) -> Any:
    """Answer GETs as an empty account with an unknown catalog (`--dry-run` without a key)."""
    if method != "GET":
        msg = "offline transport only answers GET"
        raise DeployError(msg)
    key = {
        "/v2/pods": "pods",
        "/v2/network-volumes": "networkVolumes",
        "/v2/account/secrets": "secrets",
        "/v2/catalog/gpus": "gpus",
        "/v2/catalog/cpus": "cpus",
        "/v2/catalog/datacenters": "dataCenters",
    }[path.split("?", maxsplit=1)[0]]
    return {key: []}


# ---------------------------------------------------------------------------------------------
# Output


class Reporter:
    """Prints progress and planned calls; every line goes through `redact`."""

    def __init__(self, secret_values: Sequence[str] = (), out: Any = None) -> None:
        """Redact these values wherever they would appear."""
        self.secret_values = [v for v in secret_values if v]
        self.out = out if out is not None else sys.stdout

    def redact(self, text: str) -> str:
        """Replace every known secret value in `text`."""
        for value in self.secret_values:
            text = text.replace(value, REDACTED)
        return text

    def line(self, text: str) -> None:
        """Print one line."""
        print(self.redact(text), file=self.out, flush=True)

    def call(
        self, method: str, path: str, body: Mapping[str, Any] | None, *, planned: bool
    ) -> None:
        """Print an API write, with its body (secret values redacted)."""
        prefix = "would call" if planned else "calling"
        suffix = "" if body is None else " " + json.dumps(redact_body(body), sort_keys=True)
        self.line(f"  {prefix} {method} {path}{suffix}")


def redact_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a request body with secret values replaced (a secret's `value`, inline env keys)."""
    out = dict(body)
    if "value" in out:
        out["value"] = REDACTED
    env = out.get("env")
    if isinstance(env, Mapping):
        out["env"] = {
            k: v if "RUNPOD_SECRET_" in str(v) or not _secret_env_name(k) else REDACTED
            for k, v in env.items()
        }
    return out


def _secret_env_name(name: str) -> bool:
    return any(part in name.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


# ---------------------------------------------------------------------------------------------
# API client


class RunPod:
    """The few RunPod REST v2 calls the deploy needs; writes are skipped in dry-run."""

    def __init__(self, transport: Transport, reporter: Reporter, *, dry_run: bool) -> None:
        """Wrap `transport`; with `dry_run`, writes are printed and answered with a stub."""
        self.transport = transport
        self.reporter = reporter
        self.dry_run = dry_run
        self._fake_ids = 0

    def get(self, path: str, **query: str | int) -> Any:
        """GET `path` with query parameters."""
        suffix = "?" + urllib.parse.urlencode(query) if query else ""
        return self.transport("GET", path + suffix, None)

    def write(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        """POST/PATCH/DELETE: printed and stubbed in dry-run, sent otherwise."""
        self.reporter.call(method, path, body, planned=self.dry_run)
        if not self.dry_run:
            return self.transport(method, path, body)
        if method != "POST" or path not in CREATE_PATHS:
            return {}  # a planned update or action changes nothing locally
        self._fake_ids += 1
        return {"id": f"<new-{self._fake_ids}>", "status": "PROVISIONING", **(body or {})}

    def list_all(self, path: str, key: str) -> list[dict[str, Any]]:
        """GET a paginated list endpoint and return every item."""
        items: list[dict[str, Any]] = []
        cursor = ""
        while True:
            query: dict[str, str | int] = {"limit": 1000}
            if cursor:
                query["cursor"] = cursor
            page = self.get(path, **query) or {}
            items.extend(page.get(key) or [])
            pagination = page.get("pagination") or {}
            cursor = pagination.get("nextCursor") or ""
            if not pagination.get("hasNextPage") or not cursor:
                return items

    def pods(self) -> list[dict[str, Any]]:
        """Every pod on the account."""
        return self.list_all("/v2/pods", "pods")

    def pod(self, pod_id: str) -> dict[str, Any]:
        """One pod, with its runtime block."""
        return dict(self.get(f"/v2/pods/{pod_id}"))

    def volumes(self) -> list[dict[str, Any]]:
        """Every network volume."""
        return self.list_all("/v2/network-volumes", "networkVolumes")

    def secrets(self) -> list[dict[str, Any]]:
        """Every account secret (names only; values are write-only)."""
        return list((self.get("/v2/account/secrets") or {}).get("secrets") or [])

    def catalog(self, kind: str, **query: str | int) -> list[dict[str, Any]]:
        """`/v2/catalog/<kind>` with pod stock (`include=AVAILABILITY&product=POD`)."""
        try:
            body = self.get(f"/v2/catalog/{kind}", include="AVAILABILITY", product="POD", **query)
        except DeployError as exc:
            self.reporter.line(f"  catalog {kind} unavailable ({exc}); trying without stock data")
            return []
        return list((body or {}).get(kind) or [])

    def datacenters_with_volumes(self) -> set[str] | None:
        """Data centers that offer network volumes (`None`: unknown)."""
        try:
            body = self.get("/v2/catalog/datacenters")
        except DeployError:
            return None
        centers = (body or {}).get("dataCenters") or []
        return {dc["id"] for dc in centers if dc.get("networkVolumeTypes")} if centers else None


# ---------------------------------------------------------------------------------------------
# Placement


def ranked_datacenters(entry: Mapping[str, Any]) -> list[str]:
    """Data centers where a catalog entry has stock, most stock first."""
    centers = [
        dc for dc in entry.get("dataCenters") or [] if LEVELS.get(str(dc.get("availability")), 0)
    ]
    centers.sort(key=lambda dc: -LEVELS[str(dc["availability"])])
    return [str(dc["id"]) for dc in centers]


def gpu_attempts(
    catalog: Sequence[Mapping[str, Any]], preferred: Sequence[str], *, limit: int = MAX_ATTEMPTS
) -> list[tuple[str, str | None]]:
    """Order `(gpu type, data center)` create attempts by preference, then stock.

    Without stock data (an empty catalog) each preferred type is tried once with no data center,
    letting the scheduler choose.
    """
    if not catalog:
        return [(gpu, None) for gpu in preferred][:limit]
    by_id = {str(g.get("id")): g for g in catalog}
    attempts: list[tuple[str, str | None]] = []
    for gpu in preferred:
        entry = by_id.get(gpu)
        if entry is not None and entry.get("availability") != "NONE":
            attempts.extend((gpu, dc) for dc in ranked_datacenters(entry))
    return attempts[:limit]


def cpu_datacenters(
    catalog: Sequence[Mapping[str, Any]],
    flavors: Sequence[str],
    *,
    with_volumes: set[str] | None,
    preferred: Sequence[str],
) -> list[str]:
    """Data centers for a new API volume: preferred first, then by CPU stock."""
    ranked: list[str] = []
    by_id = {str(c.get("id")): c for c in catalog}
    for flavor in flavors:
        if flavor in by_id:
            ranked.extend(dc for dc in ranked_datacenters(by_id[flavor]) if dc not in ranked)
    if with_volumes is not None:
        ranked = [dc for dc in ranked if dc in with_volumes]
    ordered = [dc for dc in preferred if not ranked or dc in ranked]
    return ordered + [dc for dc in ranked if dc not in ordered]


def place(
    runpod: RunPod,
    label: str,
    body: Mapping[str, Any],
    attempts: Sequence[tuple[Mapping[str, Any], str | None]],
) -> dict[str, Any]:
    """Create a pod, trying each `(compute block, data center)` until one places.

    Raises:
        DeployError: When every attempt fails (with each attempt's reason).
    """
    failures: list[str] = []
    for compute, datacenter in attempts:
        attempt = {**body, **compute}
        if datacenter:
            attempt["dataCenterIds"] = [datacenter]
        where = f"{next(iter(compute.values())).get('id')} in {datacenter or 'any data center'}"
        try:
            pod = dict(runpod.write("POST", "/v2/pods", attempt))
        except ApiError as exc:
            if exc.status not in PLACEMENT_FAILURES:
                raise
            failures.append(f"{where}: {exc.status} {exc.detail}")
            runpod.reporter.line(f"  {label}: no placement for {where} ({exc.status}); next")
            continue
        verb = "would place" if runpod.dry_run else "placed"
        runpod.reporter.line(f"  {label}: {verb} {where} as {pod.get('id')}")
        return pod
    msg = f"{label}: no GPU/CPU could be placed. Tried:\n    " + "\n    ".join(
        failures or ["(no candidates with stock; see --gpus / --data-center)"]
    )
    raise DeployError(msg)


# ---------------------------------------------------------------------------------------------
# Desired state


@dataclass(frozen=True)
class Options:
    """Everything `up` was asked for."""

    prefix: str = "rulecam"
    image_tag: str = "latest"
    api_source: str = "image"
    with_sam: bool = True
    with_glimmer: bool = True
    one_gpu: bool = False
    runtime: bool = True
    volume_size_gb: int = 20
    gpu_volume_gb: int = 40
    data_centers: tuple[str, ...] = ()
    glimmer_gpus: tuple[str, ...] = GLIMMER_GPUS
    sam_gpus: tuple[str, ...] = SAM_GPUS
    one_gpu_gpus: tuple[str, ...] = ONE_GPU_GPUS
    gpu_usd_per_hour: float | None = None  # overrides the pods' reported price
    cpu_flavors: tuple[str, ...] = CPU_FLAVORS
    cloud: str = "SECURE"
    public_ui_url: str = "https://rulecam.vercel.app"
    public_demo: bool = False  # PUBLIC_DEMO=true: visitors may watch /ws and read plans
    meta_api_base_url: str = "https://api.meta.ai/v1"
    secrets_dir: Path = Path.home() / ".runpod"
    inline_secrets: bool = False
    sync_secrets: bool = False
    ssh_key: Path = Path.home() / ".ssh" / "runpod_ed25519"
    wait: bool = True
    wait_timeout_s: float = 900.0
    vercel: bool = False
    dry_run: bool = False
    registry: str | None = None  # RunPod registry credential id, while the GHCR images are private

    def name(self, role: str) -> str:
        """The pod (or volume) name for a role."""
        return f"{self.prefix}-{role}"


def proxy_url(pod_id: str, port: int) -> str:
    """The RunPod HTTPS proxy URL of a pod port."""
    return f"https://{pod_id}-{port}.proxy.runpod.net"


@dataclass
class SecretRefs:
    """How each secret is referenced in pod env (a placeholder, or the value inline)."""

    refs: dict[str, str] = field(default_factory=dict)

    def env(self, spec: SecretSpec, var: str) -> dict[str, str]:
        """`{var: reference}` when the secret is available, else `{}`."""
        return {var: self.refs[spec.name]} if spec.name in self.refs else {}


def glimmer_body(opts: Options, secrets: SecretRefs) -> dict[str, Any]:
    """Create body of the Glimmer pod (compute block added per attempt)."""
    return {
        "name": opts.name("glimmer"),
        "image": GLIMMER_IMAGE,
        "cmd": list(GLIMMER_ARGS),
        "env": {
            **secrets.env(GLIMMER_KEY, "LLAMA_API_KEY"),
            "LLAMA_CACHE": "/workspace/llama-cache",
        },
        "ports": ["8080/http"],
        "disk": 20,
        "mounts": {"persistent": {"size": opts.gpu_volume_gb, "path": "/workspace"}},
        "cloud": opts.cloud,
    }


def sam_body(opts: Options, secrets: SecretRefs) -> dict[str, Any]:
    """Create body of the SAM pod. 8090 is http only: the proxy keeps the key encrypted."""
    return {
        "name": opts.name("sam"),
        "image": f"{SAM_IMAGE}:{opts.image_tag}",
        **({"registry": opts.registry} if opts.registry else {}),
        "env": {**secrets.env(SAM_KEY, "SAM_API_KEY"), **secrets.env(HF_TOKEN, "HF_TOKEN")},
        "ports": ["8090/http"],
        "disk": 30,
        "mounts": {"persistent": {"size": opts.gpu_volume_gb, "path": "/workspace"}},
        "cloud": opts.cloud,
    }


def gpu_body(opts: Options, secrets: SecretRefs) -> dict[str, Any]:
    """Create body of the one-GPU pod: Glimmer on 8080 and SAM on 8090, both http only.

    The image's entrypoint hands `LLAMA_API_KEY` to llama-server only, `SAM_API_KEY` to the SAM
    server only, and `HF_TOKEN` to the SAM checkpoint download only.
    """
    return {
        "name": opts.name("gpu"),
        "image": f"{GPU_IMAGE}:{opts.image_tag}",
        **({"registry": opts.registry} if opts.registry else {}),
        "env": {
            **secrets.env(GLIMMER_KEY, "LLAMA_API_KEY"),
            **secrets.env(SAM_KEY, "SAM_API_KEY"),
            **secrets.env(HF_TOKEN, "HF_TOKEN"),
        },
        "ports": ["8080/http", "8090/http"],
        "disk": 30,
        "mounts": {"persistent": {"size": opts.gpu_volume_gb, "path": "/workspace"}},
        "cloud": opts.cloud,
    }


def pod_price(pod: Mapping[str, Any]) -> float | None:
    """A pod's price per hour as RunPod reports it (`None` when unknown or 0)."""
    for key in ("cost", "costPerHr", "adjustedCostPerHr"):
        value = pod.get(key)
        if isinstance(value, int | float) and value > 0:
            return float(value)
    return None


def api_env(
    opts: Options,
    secrets: SecretRefs,
    *,
    glimmer_url: str | None,
    sam_url: str | None,
    gpu_usd_per_hour: str | None = None,
    gpu_pods: Sequence[str] = (),
) -> dict[str, str]:
    """Env of the API pod: non-secret settings plus secret references.

    Data and evidence stay at their defaults under /workspace/rulecam (the working directory, on
    the volume); anything not set here can still come from /workspace/rulecam/.env.
    `gpu_usd_per_hour` is the GPU pods' combined price (the cost meter shares it among the
    active cameras); `gpu_pods` are their names, for the idle GPU stop. `PUBLIC_DEMO` is set only
    with `--public-demo`, so a hand-set value in that .env still applies otherwise.
    """
    env = {
        "RULECAM_MODE": "live",
        "RULECAM_RUNTIME": "true" if opts.runtime else "false",
        "RULECAM_HOME": "/workspace/rulecam",
        "META_API_BASE_URL": opts.meta_api_base_url,
        "PUBLIC_UI_URL": opts.public_ui_url,
        **secrets.env(META_KEY, "META_API_KEY"),
        **secrets.env(BASETEN_KEY, "BASETEN_API_KEY"),
        **secrets.env(SESSION_SECRET, "SESSION_SECRET"),
        **secrets.env(UI_PROXY_SECRET, "UI_PROXY_SECRET"),
    }
    if glimmer_url:
        env["GLIMMER_BASE_URL"] = f"{glimmer_url}/v1"
        env.update(secrets.env(GLIMMER_KEY, "GLIMMER_API_KEY"))
    if sam_url:
        env["SAM_BASE_URL"] = sam_url
        env.update(secrets.env(SAM_KEY, "SAM_API_KEY"))
    if opts.public_demo:
        env["PUBLIC_DEMO"] = "true"
    if gpu_usd_per_hour is not None:
        env["RULECAM_GPU_USD_PER_HOUR"] = gpu_usd_per_hour
    if gpu_pods:
        env["RULECAM_IDLE_STOP_GPU_PODS"] = ",".join(gpu_pods)
    return env


def api_body(opts: Options, env: Mapping[str, str], volume_id: str) -> dict[str, Any]:
    """Create body of the API pod (CPU; compute block added per attempt)."""
    body: dict[str, Any] = {
        "name": opts.name("api"),
        "env": dict(env),
        "ports": ["8000/http", "22/tcp"],
        "disk": 20,
        "mounts": {"network": [{"volumeId": volume_id, "path": "/workspace"}]},
        "cloud": "SECURE",  # network volumes attach to Secure Cloud pods only
        "startSsh": True,
    }
    if opts.api_source == "archive":
        body["image"] = ARCHIVE_IMAGE
        body["args"] = ARCHIVE_BOOT
    else:
        body["image"] = f"{API_IMAGE}:{opts.image_tag}"
        if opts.registry:
            body["registry"] = opts.registry
    return body


# ---------------------------------------------------------------------------------------------
# Steps


def find_pod(pods: Sequence[Mapping[str, Any]], name: str) -> dict[str, Any] | None:
    """The pod named `name` that is not terminated, preferring a live one."""
    matches = [dict(p) for p in pods if p.get("name") == name and p.get("status") != "TERMINATED"]
    matches.sort(key=lambda p: p.get("status") not in LIVE)
    return matches[0] if matches else None


def drift(pod: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    """The PATCH body that brings `pod` to `body`'s image, env, ports and command."""
    patch: dict[str, Any] = {}
    if pod.get("image") != body.get("image"):
        patch["image"] = body["image"]
    actual_env = dict(pod.get("env") or {})
    wanted = dict(body.get("env") or {})
    if any(actual_env.get(k) != v for k, v in wanted.items()):
        patch["env"] = {**actual_env, **wanted}
    if sorted(pod.get("ports") or []) != sorted(body.get("ports") or []):
        patch["ports"] = body["ports"]
    if "cmd" in body:
        if list(pod.get("cmd") or []) != list(body["cmd"]):
            patch["cmd"] = body["cmd"]
    elif (pod.get("args") or "") != body.get("args", ""):
        # e.g. a manual pod's `bash -c "(boot.sh &) ; exec /start.sh"`, which the image-based
        # pod must drop ("" clears it) so the image's own entrypoint runs.
        patch["args"] = body.get("args", "")
    return patch


def ensure_pod(
    runpod: RunPod,
    pods: Sequence[Mapping[str, Any]],
    body: Mapping[str, Any],
    attempts: Callable[[], Sequence[tuple[Mapping[str, Any], str | None]]],
) -> dict[str, Any]:
    """Reuse (start or patch) the pod named in `body`, or create it.

    Raises:
        DeployError: When an existing pod cannot be started, or nothing places.
    """
    name = str(body["name"])
    pod = find_pod(pods, name)
    if pod is None:
        runpod.reporter.line(f"{name}: creating")
        return place(runpod, name, body, attempts())
    pod_id = str(pod["id"])
    runpod.reporter.line(f"{name}: exists ({pod_id}, {pod.get('status')})")
    patch = drift(pod, body)
    if patch:
        runpod.reporter.line(f"{name}: updating {', '.join(sorted(patch))}")
        pod = {**pod, **dict(runpod.write("PATCH", f"/v2/pods/{pod_id}", patch) or {})}
    if pod.get("status") == "EXITED":
        try:
            started = runpod.write("POST", f"/v2/pods/{pod_id}/action", {"action": "start"})
            pod = {**pod, **dict(started or {})}
        except ApiError as exc:
            msg = (
                f"{name}: could not start {pod_id} ({exc.status} {exc.detail}). Its GPU may be "
                f"taken; retry later, or `runpod_down.sh --only {name.rsplit('-', 1)[-1]} "
                "--terminate --yes` (drops its volume disk) and run up again."
            )
            raise DeployError(msg) from None
    elif pod.get("status") == "ERROR":
        msg = f"{name}: pod {pod_id} is in ERROR; see its logs in the RunPod console"
        raise DeployError(msg)
    return pod


def ensure_secrets(runpod: RunPod, opts: Options) -> tuple[SecretRefs, list[str]]:
    """Make each locally available key a RunPod secret (or an inline value).

    Returns:
        The references for pod env, and every secret value read (for redaction).

    Raises:
        DeployError: When a required key file is missing.
    """
    values = {s.name: read_secret(opts.secrets_dir / s.file) for s in SECRETS}
    missing = [s.file for s in SECRETS if s.required and not values[s.name]]
    if missing:
        msg = f"missing key files in {opts.secrets_dir}: {', '.join(missing)} (mode 600)"
        raise DeployError(msg)
    for spec in SECRETS:
        if not values[spec.name]:
            runpod.reporter.line(
                f"secret {spec.name}: no {opts.secrets_dir / spec.file}; the pods use the "
                "volume's key file instead, if any"
            )
    refs = SecretRefs()
    present = {s.name: s for s in SECRETS if values[s.name]}
    if opts.inline_secrets:
        refs.refs = {name: values[name] for name in present}
        return refs, list(values.values())
    existing = {str(s.get("name")): str(s.get("id")) for s in runpod.secrets()}
    for name, spec in present.items():
        if name not in existing:
            runpod.reporter.line(f"secret {name}: creating from {spec.file}")
            runpod.write("POST", "/v2/account/secrets", {"name": name, "value": values[name]})
        elif opts.sync_secrets:
            runpod.reporter.line(f"secret {name}: updating from {spec.file}")
            runpod.write("PATCH", f"/v2/account/secrets/{existing[name]}", {"value": values[name]})
        refs.refs[name] = spec.placeholder()
    return refs, list(values.values())


def read_secret(path: Path) -> str:
    """A key file's content without surrounding whitespace ("" when absent)."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def attached_volume(pod: Mapping[str, Any] | None) -> str | None:
    """The network volume id an existing pod mounts, if any."""
    mounts = (pod or {}).get("mounts") or {}
    network = mounts.get("network") or []
    return str(network[0]["volumeId"]) if network and network[0].get("volumeId") else None


def ensure_volume(
    runpod: RunPod, opts: Options, api_pod: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Reuse the existing API pod's volume, else one by name, else create it.

    A new volume goes where the CPU pod can run: a data center with the CPU flavor in stock
    that offers network volumes.
    """
    name = opts.name("api")
    attached = attached_volume(api_pod)
    for volume in runpod.volumes():
        if volume.get("id") == attached or (attached is None and volume.get("name") == name):
            label, where = volume.get("name"), volume.get("dataCenter")
            runpod.reporter.line(f"volume {label}: reusing {volume.get('id')} in {where}")
            return dict(volume)
    catalog = runpod.catalog("cpus", vcpuCount=API_VCPUS)
    centers = cpu_datacenters(
        catalog,
        opts.cpu_flavors,
        with_volumes=runpod.datacenters_with_volumes(),
        preferred=opts.data_centers,
    )
    if not centers and runpod.dry_run:
        centers = ["<data-center>"]
    if not centers:
        msg = "no data center with CPU stock and network volumes; pass --data-center"
        raise DeployError(msg)
    runpod.reporter.line(f"volume {name}: creating {opts.volume_size_gb} GB in {centers[0]}")
    body = {"name": name, "size": opts.volume_size_gb, "dataCenter": centers[0]}
    return dict(runpod.write("POST", "/v2/network-volumes", body))


def gpu_attempt_blocks(
    runpod: RunPod, preferred: Sequence[str]
) -> Callable[[], list[tuple[Mapping[str, Any], str | None]]]:
    """Lazily read GPU stock and turn it into create attempts."""

    def attempts() -> list[tuple[Mapping[str, Any], str | None]]:
        catalog = runpod.catalog("gpus", minCudaVersion=MIN_CUDA)
        return [
            ({"gpu": {"id": gpu, "count": 1, "minCudaVersion": MIN_CUDA}}, dc)
            for gpu, dc in gpu_attempts(catalog, preferred)
        ]

    return attempts


def cpu_attempt_blocks(
    runpod: RunPod, flavors: Sequence[str], datacenter: str
) -> Callable[[], list[tuple[Mapping[str, Any], str | None]]]:
    """Create attempts for the API pod in the volume's data center: stocked flavors first."""

    def attempts() -> list[tuple[Mapping[str, Any], str | None]]:
        by_id = {str(c.get("id")): c for c in runpod.catalog("cpus", vcpuCount=API_VCPUS)}
        stocked = [f for f in flavors if datacenter in ranked_datacenters(by_id.get(f, {}))]
        ordered = stocked + [f for f in flavors if f not in stocked]
        return [({"cpu": {"id": f, "vcpuCount": API_VCPUS}}, datacenter) for f in ordered]

    return attempts


@dataclass
class Stack:
    """What `up` ended with.

    Attributes:
        pods: Pod role (`api`, `glimmer`, `sam` or `gpu`) -> pod.
        urls: Service (`api`, `glimmer`, `sam`) -> proxy URL.
        served_by: Service -> the pod role serving it, when they differ (`--one-gpu`).
    """

    pods: dict[str, dict[str, Any]] = field(default_factory=dict)
    urls: dict[str, str] = field(default_factory=dict)
    served_by: dict[str, str] = field(default_factory=dict)

    def pod_for(self, service: str) -> dict[str, Any]:
        """The pod that serves `service`."""
        return self.pods[self.served_by.get(service, service)]


def gpu_price(runpod: RunPod, stack: Stack, opts: Options) -> str | None:
    """The GPU pods' combined price per hour, for `RULECAM_GPU_USD_PER_HOUR`.

    `--gpu-usd-per-hour` wins; otherwise each pod's reported price (re-read once after a create
    that did not include it). In a dry run a pod that does not exist yet has no price, so the
    plan shows a placeholder. `None` (with a warning) when a real pod's price is unknown.
    """
    if opts.gpu_usd_per_hour is not None:
        return f"{opts.gpu_usd_per_hour:g}"
    if not stack.pods:
        return None
    total = 0.0
    for role, pod in stack.pods.items():
        price = pod_price(pod)
        if price is None and not runpod.dry_run:
            price = pod_price(runpod.pod(str(pod["id"])))
        if price is None:
            if runpod.dry_run:
                return PRICE_PLACEHOLDER
            runpod.reporter.line(
                f"WARNING: {opts.name(role)} reports no price; RULECAM_GPU_USD_PER_HOUR not set "
                "(pass --gpu-usd-per-hour)"
            )
            return None
        total += price
    return f"{round(total, 4):g}"


def warn_superseded(
    runpod: RunPod, pods: Sequence[Mapping[str, Any]], opts: Options, roles: Sequence[str]
) -> None:
    """Point at GPU pods of the other layout that still run (and bill)."""
    for role in roles:
        pod = find_pod(pods, opts.name(role))
        if pod is not None and pod.get("status") in LIVE:
            price = pod_price(pod)
            cost = f" at ${price:.2f}/h" if price else ""
            runpod.reporter.line(
                f"WARNING: {opts.name(role)} ({pod.get('id')}) still runs{cost} and is no longer "
                f"used; stop it: runpod_down.sh --only {role}"
            )


def ensure_gpu_pods(
    runpod: RunPod,
    pods: Sequence[Mapping[str, Any]],
    opts: Options,
    secrets: SecretRefs,
    stack: Stack,
) -> None:
    """The GPU side: one pod for both services (`--one-gpu`), or one pod each."""
    if opts.one_gpu:
        pod = ensure_pod(
            runpod, pods, gpu_body(opts, secrets), gpu_attempt_blocks(runpod, opts.one_gpu_gpus)
        )
        stack.pods["gpu"] = pod
        stack.urls["glimmer"] = proxy_url(str(pod["id"]), 8080)
        stack.urls["sam"] = proxy_url(str(pod["id"]), 8090)
        stack.served_by.update(glimmer="gpu", sam="gpu")
        warn_superseded(runpod, pods, opts, ("glimmer", "sam"))
        return
    if opts.with_glimmer:
        pod = ensure_pod(
            runpod, pods, glimmer_body(opts, secrets), gpu_attempt_blocks(runpod, opts.glimmer_gpus)
        )
        stack.pods["glimmer"], stack.urls["glimmer"] = pod, proxy_url(str(pod["id"]), 8080)
    if opts.with_sam:
        pod = ensure_pod(
            runpod, pods, sam_body(opts, secrets), gpu_attempt_blocks(runpod, opts.sam_gpus)
        )
        stack.pods["sam"], stack.urls["sam"] = pod, proxy_url(str(pod["id"]), 8090)
    warn_superseded(runpod, pods, opts, ("gpu",))


def up(runpod: RunPod, opts: Options) -> Stack:
    """Bring the stack up (see the module docstring).

    Raises:
        DeployError: When `--one-gpu` is combined with `--no-sam` / `--no-glimmer`, or a step
            fails.
    """
    if opts.one_gpu and not (opts.with_sam and opts.with_glimmer):
        msg = "--one-gpu runs Glimmer and SAM together; drop --no-sam / --no-glimmer"
        raise DeployError(msg)
    secrets, values = ensure_secrets(runpod, opts)
    runpod.reporter.secret_values.extend(v for v in values if v)
    pods = runpod.pods()
    volume = ensure_volume(runpod, opts, find_pod(pods, opts.name("api")))
    stack = Stack()
    ensure_gpu_pods(runpod, pods, opts, secrets, stack)
    env = api_env(
        opts,
        secrets,
        glimmer_url=stack.urls.get("glimmer"),
        sam_url=stack.urls.get("sam"),
        gpu_usd_per_hour=gpu_price(runpod, stack, opts),
        gpu_pods=[opts.name(role) for role in stack.pods],
    )
    datacenter = str(volume.get("dataCenter") or "")
    pod = ensure_pod(
        runpod,
        pods,
        api_body(opts, env, str(volume["id"])),
        cpu_attempt_blocks(runpod, opts.cpu_flavors, datacenter),
    )
    stack.pods["api"], stack.urls["api"] = pod, proxy_url(str(pod["id"]), 8000)
    return stack


# ---------------------------------------------------------------------------------------------
# Waiting, archive upload, Vercel


HEALTH_PATHS = {"api": "/health", "glimmer": "/health", "sam": "/health"}


def http_status(url: str, *, timeout_s: float = 5.0) -> int | None:
    """GET `url` (no auth); the status code, or `None` when unreachable."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def wait_until(
    check: Callable[[], bool], *, timeout_s: float, sleep: Callable[[float], None] = time.sleep
) -> bool:
    """Poll `check` every POLL_S until it is true or `timeout_s` passes."""
    deadline = time.monotonic() + timeout_s
    while True:
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(POLL_S)


def wait_healthy(runpod: RunPod, stack: Stack, opts: Options) -> dict[str, bool]:
    """Wait for each pod to run and its /health to answer 200 (model loads take minutes)."""
    healthy: dict[str, bool] = {}
    for role, url in stack.urls.items():
        pod_id = str(stack.pod_for(role)["id"])
        runpod.reporter.line(f"waiting for {role} ({pod_id}) …")
        running = wait_until(
            lambda pod_id=pod_id: runpod.pod(pod_id).get("status") == "RUNNING",
            timeout_s=opts.wait_timeout_s,
        )
        healthy[role] = running and wait_until(
            lambda url=url, role=role: http_status(url + HEALTH_PATHS[role]) == 200,  # noqa: PLR2004
            timeout_s=opts.wait_timeout_s,
        )
        runpod.reporter.line(f"  {role}: {'healthy' if healthy[role] else 'NOT healthy yet'}")
    return healthy


def upload_archive(runpod: RunPod, pod_id: str, opts: Options) -> None:
    """`--api-source archive`: copy `git archive HEAD` to the pod and start the API there.

    Raises:
        DeployError: When the pod has no direct SSH endpoint yet or a command fails.
    """
    if opts.dry_run:
        runpod.reporter.line(
            "  would run: git archive HEAD | ssh <pod> tar -x -C /workspace/rulecam"
        )
        runpod.reporter.line("  would run: ssh <pod> deploy/api-boot.sh (in the background)")
        return
    ssh = (runpod.pod(pod_id).get("ssh") or {}).get("direct") or {}
    if not ssh.get("host") or not ssh.get("port"):
        msg = f"api pod {pod_id} has no direct SSH endpoint yet; run up again in a minute"
        raise DeployError(msg)
    target = [
        "ssh", "-i", str(opts.ssh_key), "-p", str(ssh["port"]),
        "-o", "StrictHostKeyChecking=accept-new", f"{ssh.get('username') or 'root'}@{ssh['host']}",
    ]  # fmt: skip
    archive = subprocess.Popen(  # noqa: S603 - fixed argv
        ["git", "-C", str(REPO_ROOT), "archive", "HEAD"],  # noqa: S607
        stdout=subprocess.PIPE,
    )
    extract = subprocess.run(  # noqa: S603
        [*target, "mkdir -p /workspace/rulecam && tar -x -C /workspace/rulecam"],
        stdin=archive.stdout,
        check=False,
    )
    if archive.wait() != 0 or extract.returncode != 0:
        msg = "uploading the code archive failed"
        raise DeployError(msg)
    start = "setsid nohup /workspace/rulecam/deploy/api-boot.sh >/dev/null 2>&1 </dev/null &"
    if subprocess.run([*target, start], check=False).returncode != 0:  # noqa: S603
        msg = "starting the API over SSH failed"
        raise DeployError(msg)


def vercel_commands(api_url: str) -> list[tuple[list[str], str | None]]:
    """The Vercel CLI calls that point the UI at `api_url` and redeploy production."""
    name = "NEXT_PUBLIC_RULECAM_API"
    return [
        (["vercel", "env", "rm", name, "production", "--yes"], None),
        (["vercel", "env", "add", name, "production"], api_url + "\n"),
        (["vercel", "deploy", "--prod", "--yes"], None),
    ]


def update_vercel(reporter: Reporter, api_url: str, *, dry_run: bool) -> None:
    """Set the UI's API URL and redeploy (NEXT_PUBLIC_* is baked in at build time).

    Raises:
        DeployError: When the Vercel CLI is missing or a call fails.
    """
    cwd = vercel_root(Path.cwd()) or REPO_ROOT
    for argv, stdin in vercel_commands(api_url):
        given = f" <<< {stdin.strip()}" if stdin else ""
        verb = "would run" if dry_run else "running"
        reporter.line(f"  {verb}: {' '.join(argv)}{given} (in {cwd})")
        if dry_run:
            continue
        if shutil.which("vercel") is None:
            msg = "the vercel CLI is not on PATH (npm i -g vercel, then vercel link)"
            raise DeployError(msg)
        result = subprocess.run(argv, input=stdin, text=True, cwd=cwd, check=False)  # noqa: S603
        if result.returncode != 0 and argv[2] != "rm":  # rm fails when the variable is new
            msg = f"`{' '.join(argv)}` failed ({result.returncode})"
            raise DeployError(msg)


def vercel_root(start: Path) -> Path | None:
    """The nearest directory at or above `start` linked to a Vercel project."""
    for path in (start, *start.parents):
        if (path / ".vercel" / "project.json").is_file():
            return path
    return None


# ---------------------------------------------------------------------------------------------
# down / status


ROLES = ("api", "glimmer", "sam", "gpu")


def down(runpod: RunPod, opts: Options, roles: Sequence[str], *, terminate: bool) -> None:
    """Stop (or terminate) the stack's pods; the network volume is always kept."""
    pods = runpod.pods()
    for role in roles:
        pod = find_pod(pods, opts.name(role))
        if pod is None:
            runpod.reporter.line(f"{opts.name(role)}: not found")
            continue
        pod_id = str(pod["id"])
        if terminate:
            runpod.reporter.line(
                f"{opts.name(role)}: terminating {pod_id} (its volume disk goes too)"
            )
            runpod.write("DELETE", f"/v2/pods/{pod_id}")
        elif pod.get("status") in LIVE:
            runpod.reporter.line(f"{opts.name(role)}: stopping {pod_id}")
            runpod.write("POST", f"/v2/pods/{pod_id}/action", {"action": "stop"})
        else:
            runpod.reporter.line(f"{opts.name(role)}: already {pod.get('status')}")
    runpod.reporter.line(
        f"network volume {opts.name('api')} kept (it bills until deleted in the RunPod console)"
    )


PORTS = {"api": (8000,), "glimmer": (8080,), "sam": (8090,), "gpu": (8080, 8090)}


def status_rows(
    runpod: RunPod, opts: Options, *, probe: Callable[[str], int | None] | None = None
) -> Iterator[tuple[str, str, str, str, str]]:
    """`(name, id, status, cost/h, health)` per role; health is probed for running pods.

    The one-GPU pod is `ok` only when both its servers answer. `probe` defaults to
    `http_status`.
    """
    probe = probe or http_status
    pods = runpod.pods()
    for role in ROLES:
        pod = find_pod(pods, opts.name(role))
        if pod is None:
            yield opts.name(role), "-", "absent", "-", "-"
            continue
        pod_id = str(pod["id"])
        health = "-"
        if pod.get("status") == "RUNNING":
            codes = [probe(proxy_url(pod_id, port) + "/health") for port in PORTS[role]]
            down = ",".join(str(code) for code in codes)
            health = "ok" if all(c == 200 for c in codes) else f"down ({down})"  # noqa: PLR2004
        cost = pod.get("cost")
        yield (
            opts.name(role),
            pod_id,
            str(pod.get("status")),
            f"${cost:.3f}" if cost else "-",
            health,
        )


def print_status(runpod: RunPod, opts: Options) -> int:
    """Print the stack's state.

    Exit code 0 only when the API pod and every other pod that exists run and are healthy (a
    two-pod stack has no `gpu` pod, a one-GPU stack no `glimmer` / `sam` pod).
    """
    ok = True
    runpod.reporter.line(f"{'pod':<18} {'id':<16} {'status':<12} {'cost/h':<8} health  url")
    for name, pod_id, state, cost, health in status_rows(runpod, opts):
        role = name.rsplit("-", 1)[-1]
        urls = " ".join(proxy_url(pod_id, p) for p in PORTS[role]) if pod_id != "-" else ""
        runpod.reporter.line(f"{name:<18} {pod_id:<16} {state:<12} {cost:<8} {health:<7} {urls}")
        if pod_id != "-" or role == "api":
            ok = ok and health == "ok"
    return 0 if ok else 1


# ---------------------------------------------------------------------------------------------
# CLI


def _csv(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def parser() -> argparse.ArgumentParser:
    """The command line: `up`, `down`, `status`, each with the common options."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--prefix", default="rulecam", help="pod/volume name prefix")
    common.add_argument("--dry-run", action="store_true", help="print writes instead of sending")
    common.add_argument("--config", type=Path, default=Path.home() / ".runpod" / "config.toml")
    cli = argparse.ArgumentParser(prog="runpod", description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="command", required=True)
    p_up = sub.add_parser("up", parents=[common], help="create or reuse the volume and pods")
    p_up.add_argument("--image-tag", default="latest", help="GHCR tag, e.g. sha-<commit>")
    p_up.add_argument("--api-source", choices=("image", "archive"), default="image")
    p_up.add_argument("--no-sam", action="store_true", help="runtime SAM stays hosted")
    p_up.add_argument("--no-glimmer", action="store_true")
    p_up.add_argument(
        "--one-gpu",
        action="store_true",
        help="one 48 GB pod runs Glimmer (8080) and SAM (8090): rulecam-gpu",
    )
    p_up.add_argument("--no-runtime", action="store_true", help="RULECAM_RUNTIME=false")
    p_up.add_argument("--data-center", type=_csv, default=(), help="preferred DCs for the volume")
    p_up.add_argument("--glimmer-gpus", type=_csv, default=GLIMMER_GPUS)
    p_up.add_argument("--sam-gpus", type=_csv, default=SAM_GPUS)
    p_up.add_argument("--one-gpu-gpus", type=_csv, default=ONE_GPU_GPUS)
    p_up.add_argument(
        "--gpu-usd-per-hour",
        type=float,
        help="RULECAM_GPU_USD_PER_HOUR for the API (default: the GPU pods' reported price)",
    )
    p_up.add_argument("--cloud", choices=("SECURE", "COMMUNITY"), default="SECURE")
    p_up.add_argument("--public-ui-url", default="https://rulecam.vercel.app")
    p_up.add_argument(
        "--public-demo",
        action="store_true",
        help="PUBLIC_DEMO=true: logged-out visitors may watch /ws and read plans (demo sites only)",
    )
    p_up.add_argument("--secrets-dir", type=Path, default=Path.home() / ".runpod")
    p_up.add_argument(
        "--inline-secrets", action="store_true", help="env values, not RunPod secrets"
    )
    p_up.add_argument("--sync-secrets", action="store_true", help="re-upload existing secrets")
    p_up.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh" / "runpod_ed25519")
    p_up.add_argument("--no-wait", action="store_true")
    p_up.add_argument("--wait-timeout", type=float, default=900.0, help="seconds per pod")
    p_up.add_argument("--vercel", action="store_true", help="set NEXT_PUBLIC_RULECAM_API, redeploy")
    p_up.add_argument("--registry", help="RunPod registry credential id for private GHCR images")
    p_down = sub.add_parser("down", parents=[common], help="stop the pods (volume kept)")
    p_down.add_argument("--only", type=_csv, default=ROLES, help="roles: api,glimmer,sam,gpu")
    p_down.add_argument("--terminate", action="store_true", help="delete pods (needs --yes)")
    p_down.add_argument("--yes", action="store_true")
    sub.add_parser("status", parents=[common], help="state, cost and health of the pods")
    return cli


def options(args: argparse.Namespace) -> Options:
    """`Options` from parsed `up` arguments (defaults for the other commands)."""
    if args.command != "up":
        return Options(prefix=args.prefix, dry_run=args.dry_run)
    return Options(
        prefix=args.prefix,
        image_tag=args.image_tag,
        api_source=args.api_source,
        with_sam=not args.no_sam,
        with_glimmer=not args.no_glimmer,
        one_gpu=args.one_gpu,
        runtime=not args.no_runtime,
        data_centers=args.data_center,
        glimmer_gpus=args.glimmer_gpus,
        sam_gpus=args.sam_gpus,
        one_gpu_gpus=args.one_gpu_gpus,
        gpu_usd_per_hour=args.gpu_usd_per_hour,
        cloud=args.cloud,
        public_ui_url=args.public_ui_url,
        public_demo=args.public_demo,
        secrets_dir=args.secrets_dir,
        inline_secrets=args.inline_secrets,
        sync_secrets=args.sync_secrets,
        ssh_key=args.ssh_key,
        wait=not args.no_wait,
        wait_timeout_s=args.wait_timeout,
        vercel=args.vercel,
        dry_run=args.dry_run,
        registry=args.registry,
    )


def make_client(args: argparse.Namespace, reporter: Reporter) -> RunPod:
    """The API client: the real API, or (dry-run without a key) an empty offline account."""
    base = os.environ.get("RUNPOD_REST_URL", API_BASE)
    try:
        transport = http_transport(base, read_api_key(args.config))
    except DeployError:
        if not args.dry_run:
            raise
        reporter.line("no RunPod API key: planning against an empty account")
        transport = offline_transport
    return RunPod(transport, reporter, dry_run=args.dry_run)


def run_up(runpod: RunPod, opts: Options) -> int:
    """`up`, then the archive upload, waits, Vercel and the summary."""
    stack = up(runpod, opts)
    if opts.api_source == "archive":
        upload_archive(runpod, str(stack.pods["api"]["id"]), opts)
    healthy = wait_healthy(runpod, stack, opts) if opts.wait and not opts.dry_run else {}
    if opts.vercel:
        update_vercel(runpod.reporter, stack.urls["api"], dry_run=opts.dry_run)
    runpod.reporter.line("")
    runpod.reporter.line(f"UI       {opts.public_ui_url}")
    for role, url in stack.urls.items():
        runpod.reporter.line(f"{role:<8} {url}")
    cost = sum(float(p.get("cost") or 0) for p in stack.pods.values())
    if cost:
        runpod.reporter.line(f"running cost about ${cost:.2f}/h (runpod_down.sh stops it)")
    gpu = sum(pod_price(p) or 0.0 for role, p in stack.pods.items() if role != "api")
    if gpu:
        runpod.reporter.line(gpu_share_line(gpu))
    return 0 if all(healthy.values()) else 1


def gpu_share_line(gpu_usd_per_hour: float, cameras: Sequence[int] = (1, 4, 6, 8)) -> str:
    """The GPU share per camera-hour for a few cameras-per-GPU counts."""
    shares = ", ".join(
        f"{n} camera{'s' if n > 1 else ''} ${gpu_usd_per_hour / n:.3f}" for n in cameras
    )
    return f"GPU ${gpu_usd_per_hour:.2f}/h per camera-hour: {shares} (eval --cameras-per-gpu)"


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the exit code."""
    args = parser().parse_args(argv)
    reporter = Reporter()
    try:
        runpod = make_client(args, reporter)
        opts = options(args)
        if args.dry_run:
            reporter.line("DRY RUN: reading the account; no writes are sent")
        if args.command == "up":
            return run_up(runpod, opts)
        if args.command == "down":
            if args.terminate and not args.yes and not args.dry_run:
                reporter.line("--terminate deletes the pods and their volume disks; add --yes")
                return 2
            down(runpod, opts, args.only, terminate=args.terminate)
            return 0
        return print_status(runpod, opts)
    except DeployError as exc:
        reporter.line(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
