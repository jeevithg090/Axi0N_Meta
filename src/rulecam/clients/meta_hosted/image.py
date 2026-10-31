"""Muse Image hosted client (`MuseImageClient`) (task P2-01).

Generates synthetic scenes, lookalike red-teams, and visual posters.
Features:
- Fixed 150 RPM limiter.
- Supports up to 10 reference images (`reference_images`); raises `ValueError` if > 10.
- Rejects mask or inpainting parameters with `ValueError` (Muse Image does not support inpainting).
- Sequential editing via `previous_response_id`.
- Saves generated images to disk (default `data/images/`).
- Publishes telemetry on `relay.model_used`.
"""

from __future__ import annotations

import base64
import hashlib
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import HttpxTransport, RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache, get_cache

_log = get_logger(__name__)

DEFAULT_MODEL = "muse-image"
IMAGE_GENERATION_PATH = "/images/generations"
IMAGE_RPM = 150.0
DEFAULT_SEGMENT = "image"
MAX_REFERENCE_IMAGES = 10
DEFAULT_SIZE = "1024x1024"
DISALLOWED_MASK_KEYS = frozenset(
    {"mask", "mask_image", "mask_file", "inpaint", "inpainting", "masks"}
)


@dataclass(frozen=True)
class ImageResult:
    """The result of an image generation call.

    Attributes:
        path: Path where the generated image was saved on disk.
        response_id: The API response/generation identifier.
    """

    path: Path
    response_id: str


def _validate_generate_args(
    kwargs: Mapping[str, Any], reference_images: Sequence[Path] | None
) -> None:
    for key in kwargs:
        if key in DISALLOWED_MASK_KEYS or "mask" in key or "inpaint" in key:
            msg = (
                f"Parameter {key!r} is not allowed: Muse Image does not support masks or inpainting"
            )
            raise ValueError(msg)
    if reference_images is not None and len(reference_images) > MAX_REFERENCE_IMAGES:
        msg = (
            f"At most {MAX_REFERENCE_IMAGES} reference images allowed, got {len(reference_images)}"
        )
        raise ValueError(msg)


def _encode_reference_images(reference_images: Sequence[Path] | None) -> list[str]:
    if not reference_images:
        return []
    return [base64.b64encode(path.read_bytes()).decode("ascii") for path in reference_images]


def _sanitize_response_id(raw_id: str, prompt: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", raw_id)
    if not cleaned:
        cleaned = f"img_{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"
    return cleaned


def _extract_image_bytes(body: dict[str, Any], prompt: str) -> tuple[str, bytes]:
    raw_id = str(body.get("id") or body.get("response_id") or "")
    resp_id = _sanitize_response_id(raw_id, prompt)
    image_bytes: bytes | None = None

    if "data" in body and isinstance(body["data"], list) and body["data"]:
        item = body["data"][0]
        if isinstance(item, dict) and "b64_json" in item and isinstance(item["b64_json"], str):
            image_bytes = base64.b64decode(item["b64_json"])
    elif "b64_json" in body and isinstance(body["b64_json"], str):
        image_bytes = base64.b64decode(body["b64_json"])
    elif "image_bytes" in body and isinstance(body["image_bytes"], str):
        image_bytes = base64.b64decode(body["image_bytes"])

    if image_bytes is None:
        msg = f"Muse image response contained no b64_json image data: {body!r}"
        raise ModelResponseError(msg)
    return resp_id, image_bytes


class MuseImageClient:
    """Muse Image client on Meta's hosted API."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        bus: Bus | None = None,
        cache: ReplayCache | None = None,
        output_dir: Path | None = None,
        model: str = DEFAULT_MODEL,
        rpm: float = IMAGE_RPM,
        segment: str = DEFAULT_SEGMENT,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        seed: int | None = None,
    ) -> None:
        """Create the Muse Image client.

        Args:
            transport: The HTTP transport callable.
            clock: Time source.
            bus: Event bus for `relay.model_used`.
            cache: Replay cache.
            output_dir: Directory where generated images are saved (default `data/images`).
            model: Model identifier.
            rpm: Maximum RPM (enforced at 150 RPM).
            segment: Telemetry pipeline segment.
            retry: Retry policy.
            sleep: Sleep callable.
            monotonic: Monotonic clock callable.
            seed: Seed for retry jitter.
        """
        self._clock = clock
        self._bus = bus
        self._cache = cache
        self._output_dir = output_dir or Path("data/images")
        self._model = model
        self._segment = segment
        self._sleep = sleep
        self._monotonic = monotonic

        limiter = TokenBucket(rpm, monotonic=monotonic, sleep=sleep)
        jitter = random.Random(seed)  # noqa: S311 - retry jitter
        self._sender = Sender(transport, limiter, retry or RetryPolicy(), sleep, jitter)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Clock,
        bus: Bus | None = None,
        cache: ReplayCache | None = None,
        output_dir: Path | None = None,
    ) -> MuseImageClient:
        """Create client configured from application settings.

        Args:
            settings: Runtime settings.
            clock: Clock source.
            bus: Bus for relay events.
            cache: Replay cache.
            output_dir: Output image destination.

        Returns:
            A configured MuseImageClient.

        Raises:
            ClientConfigError: When credentials or URLs are missing in live/record modes.
        """
        if settings.rulecam_mode == "replay" and cache is None:
            cache = get_cache()

        if settings.rulecam_mode != "replay" and (
            not settings.meta_api_base_url or settings.meta_api_key is None
        ):
            msg = "META_API_BASE_URL and META_API_KEY are required for hosted Muse Image"
            raise ClientConfigError(msg)

        base_url = settings.meta_api_base_url or "https://api.meta.ai/v1"
        api_key = settings.meta_api_key.get_secret_value() if settings.meta_api_key else "replay"
        raw_transport = HttpxTransport(base_url, api_key)
        transport = (
            cache.wrap_transport("meta", DEFAULT_MODEL, raw_transport) if cache else raw_transport
        )
        dest = output_dir or (settings.rulecam_data_dir / "images")
        return cls(transport, clock=clock, bus=bus, cache=cache, output_dir=dest)

    def generate(
        self,
        prompt: str,
        *,
        reference_images: Sequence[Path] | None = None,
        size: str = DEFAULT_SIZE,
        previous_response_id: str | None = None,
        **kwargs: Any,
    ) -> ImageResult:
        """Generate an image from a prompt, with optional references and previous response chaining.

        Args:
            prompt: Text prompt describing the scene.
            reference_images: Optional list of reference image paths (maximum 10).
            size: Desired image dimensions (sets aspect ratio).
            previous_response_id: Optional previous response ID for sequential edits.
            **kwargs: Extra parameters; any mask or inpainting parameters will raise ValueError.

        Returns:
            ImageResult containing the saved image Path and response ID.

        Raises:
            ValueError: If mask/inpainting arguments are provided, or references > 10.
            ModelHTTPError: If the API fails.
            ModelResponseError: If the API response cannot be decoded.
        """
        _validate_generate_args(kwargs, reference_images)

        req_json: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "size": size,
        }
        if previous_response_id:
            req_json["previous_response_id"] = previous_response_id
        if reference_images:
            req_json["reference_images"] = _encode_reference_images(reference_images)

        started = self._monotonic()
        req: dict[str, object] = {
            "method": "POST",
            "path": IMAGE_GENERATION_PATH,
            "json": req_json,
        }
        reply = self._sender.send(req)
        latency_ms = max(0, round((self._monotonic() - started) * 1000))

        body = reply.reply.body
        if not isinstance(body, dict):
            msg = f"Unexpected response from Muse Image API: {body!r}"
            raise ModelResponseError(msg)

        response_id, image_bytes = _extract_image_bytes(body, prompt)

        self._output_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._output_dir / f"{response_id}.png"
        file_path.write_bytes(image_bytes)

        publish_model_used(
            self._bus,
            ModelUsed(
                model=self._model,
                segment=self._segment,
                latency_ms=latency_ms,
                attempts=reply.attempts,
            ),
        )

        return ImageResult(path=file_path, response_id=response_id)
