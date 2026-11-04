"""`/extensions` (owner-only): ask for a new check type, see its proof and review, approve it.

- `POST /extensions` with an `ExtensionRequest`: Muse Code writes the check, the proof and the
  reviewer run, and the stored version comes back (`proposed` or `rejected`, with every reason).
  It can take minutes. 503 when no generator is configured, 502 when the model call fails.
- `GET /extensions`: the newest version of every extension.
- `GET /extensions/{name}`: every version of one, oldest first.
- `POST /extensions/{name}/approve` and `/retire`, optionally with `{"sha256": "<12+ hex>"}` (the
  newest version by default): 409 when the version cannot take that step.

`build_extensions` wires the clients from settings. Muse Spark Open reviews only when
`BASETEN_API_KEY` is set, so without it the Spark 1.3 fallback reviews instead of every review
failing.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelClientError
from rulecam.clients.meta_hosted.muse_code import MuseCodeClient
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.clients.open_weights.spark_open import SparkOpenClient
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.evidence.chain import EventChain
from rulecam.extensions.errors import RegistryError
from rulecam.extensions.generate import CheckGenerator
from rulecam.extensions.registry import ExtensionRecord, ExtensionRegistry, RegistryHook
from rulecam.extensions.review import ExtensionReviewer
from rulecam.extensions.service import ExtensionRequest, ExtensionService
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

OWNER_ROLE = "owner"


class VersionChoice(BaseModel):
    """Which version an approve or retire applies to (the newest when omitted)."""

    model_config = ConfigDict(extra="forbid")

    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{12,64}$")


@dataclass(frozen=True)
class Extensions:
    """The wired extension pieces: the registry, the engine hook and the service."""

    registry: ExtensionRegistry
    hook: RegistryHook
    service: ExtensionService | None

    def router(self, dependencies: Sequence[Any] = ()) -> APIRouter:
        """The `/extensions` router (pass the owner dependency)."""
        return extensions_router(self.service, self.registry, dependencies=dependencies)


def build_extensions(
    settings: Settings, *, cache: ReplayCache, clock: Clock, bus: Bus, chain: EventChain | None
) -> Extensions:
    """Build the registry, the hook and (when any generator is available) the service.

    Args:
        settings: Application settings (`RULECAM_MUSE_CODE_*`, Spark and Baseten keys).
        cache: The replay cache every model call goes through.
        clock: Time source.
        bus: Bus for `relay.model_used`.
        chain: The evidence chain for `rule_change` records.

    Returns:
        The wired pieces; `service` is None only when neither Muse Code nor Spark is set up.
    """
    registry = ExtensionRegistry(settings.rulecam_data_dir / "extensions", clock=clock, chain=chain)
    muse_code = MuseCodeClient(
        cache=cache,
        binary=settings.rulecam_muse_code_bin,
        extra_args=shlex.split(settings.rulecam_muse_code_args),
        timeout_s=settings.rulecam_muse_code_timeout_s,
        model=settings.rulecam_muse_code_model,
    )
    try:
        spark: SparkClient | None = SparkClient.from_settings(
            settings, clock=clock, bus=bus, cache=cache
        )
    except ClientConfigError as exc:
        _log.warning("extensions_without_spark", error=str(exc))
        spark = None
    spark_open = (
        SparkOpenClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
        if settings.baseten_api_key
        else None
    )
    service = (
        ExtensionService(
            CheckGenerator(muse_code, spark),
            ExtensionReviewer(spark_open, spark),
            registry,
            clock=clock,
        )
        if muse_code.available or spark is not None
        else None
    )
    return Extensions(registry=registry, hook=RegistryHook(registry), service=service)


def extensions_router(
    service: ExtensionService | None,
    registry: ExtensionRegistry,
    *,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/extensions` routes.

    Args:
        service: Generates new versions; None answers 503 to `POST /extensions`.
        registry: Where versions are read and decided.
        dependencies: Route dependencies (the owner check).

    Returns:
        The router.
    """
    router = APIRouter(prefix="/extensions", tags=["extensions"], dependencies=list(dependencies))

    @router.post("", response_model=ExtensionRecord)
    def propose(body: ExtensionRequest) -> ExtensionRecord:
        return _propose(service, body)

    @router.get("", response_model=list[ExtensionRecord])
    def list_latest() -> list[ExtensionRecord]:
        return registry.all_latest()

    @router.get("/{name}", response_model=list[ExtensionRecord])
    def versions(name: str) -> list[ExtensionRecord]:
        found = registry.versions(name)
        if not found:
            raise HTTPException(404, f"no extension {name!r}")
        return found

    @router.post("/{name}/approve", response_model=ExtensionRecord)
    def approve(name: str, body: VersionChoice | None = None) -> ExtensionRecord:
        return _decide(registry.approve, registry, name, body)

    @router.post("/{name}/retire", response_model=ExtensionRecord)
    def retire(name: str, body: VersionChoice | None = None) -> ExtensionRecord:
        return _decide(registry.retire, registry, name, body)

    return router


Decision = Callable[..., ExtensionRecord]


def _propose(service: ExtensionService | None, body: ExtensionRequest) -> ExtensionRecord:
    """Run the service, mapping its failures to HTTP answers."""
    if service is None:
        raise HTTPException(503, "no generator: install the Muse Code CLI or set a Spark key")
    try:
        return service.propose(body)
    except ClientConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ModelClientError as exc:
        raise HTTPException(502, f"the model call failed: {str(exc)[:300]}") from exc
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from exc


def _decide(
    decision: Decision, registry: ExtensionRegistry, name: str, body: VersionChoice | None
) -> ExtensionRecord:
    """Approve or retire the chosen version as the owner; 409 when it cannot take that step."""
    sha = _chosen_sha(registry, name, body)
    try:
        return decision(name, sha, by=OWNER_ROLE)
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from exc


def _chosen_sha(registry: ExtensionRegistry, name: str, body: VersionChoice | None) -> str:
    if body is not None and body.sha256 is not None:
        return body.sha256
    latest = registry.latest(name)
    if latest is None:
        raise HTTPException(404, f"no extension {name!r}")
    return latest.sha256
