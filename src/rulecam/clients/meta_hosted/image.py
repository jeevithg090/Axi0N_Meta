"""Muse Image hosted client (`MuseImageClient`) (task P2-01).

Generates synthetic scenes for Rule CI (detectability scenes, lookalike red-teams). It uses the
multi-turn Responses API, the same shape as hosted SAM 3.1 (see `responses.py`), as the image
generation guide describes (read Oct 31 2026, <https://dev.meta.ai/docs/image-generation>):

- `POST /responses` with model `muse-image-1.0`, `stream: false`, one `image_generation` tool
  carrying `size` (a `WxH` string that sets the aspect ratio only) and `output_format: "png"`;
- the prompt is an `input_text` part; up to 10 reference images are `input_image` parts whose
  `file_id` comes from `POST /files`;
- `previous_response_id` chains an edit onto an earlier turn;
- the image is the base64 `result` of the `image_generation_call` output item; the reply `id`
  names the saved file and is returned for chaining.

There is no inpainting: any mask or inpainting argument raises `ValueError` before any call.
A 150 RPM token bucket spaces calls (uploads included). `from_settings` always wraps the
transport in the replay cache, and every call publishes `relay.model_used`.
"""

from __future__ import annotations

import base64
import binascii
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rulecam.bus import Bus
from rulecam.clients.errors import ModelResponseError
from rulecam.clients.meta_hosted.responses import (
    REPLAY_PROVIDER,
    RESPONSES_PATH,
    FileUploader,
    file_part,
    latency_ms,
    live_transport,
    response_body,
)
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import RetryPolicy, Sender, Transport
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

IMAGE_MODEL = "muse-image-1.0"
IMAGE_RPM = 150.0
DEFAULT_SEGMENT = "image"
MAX_REFERENCE_IMAGES = 10
DEFAULT_SIZE = "1024x1024"
OUTPUT_FORMAT = "png"
IMAGE_CALL_TYPE = "image_generation_call"
_SIZE = re.compile(r"[1-9]\d*x[1-9]\d*")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class ImageResult:
    """One generated image.

    Attributes:
        path: Where the PNG was saved.
        response_id: The response id, for `previous_response_id` on a follow-up edit. The file
            name is this id with anything but letters, digits, `_` and `-` replaced by `_`.
    """

    path: Path
    response_id: str


def check_arguments(
    extra: Mapping[str, object], reference_images: Sequence[Path], size: str
) -> None:
    """Reject arguments Muse Image does not support, before any network call.

    Args:
        extra: Keyword arguments beyond the supported ones.
        reference_images: The reference images.
        size: The requested `WxH` size.

    Raises:
        ValueError: For a mask or inpainting argument (Muse Image has no inpainting), any other
            unknown argument, more than 10 references, or a malformed size.
    """
    for key in extra:
        if "mask" in key.lower() or "inpaint" in key.lower():
            msg = f"{key!r} is not supported: Muse Image has no masks or inpainting"
            raise ValueError(msg)
    if extra:
        msg = f"unsupported Muse Image arguments: {sorted(extra)}"
        raise ValueError(msg)
    if len(reference_images) > MAX_REFERENCE_IMAGES:
        msg = f"at most {MAX_REFERENCE_IMAGES} reference images, got {len(reference_images)}"
        raise ValueError(msg)
    if _SIZE.fullmatch(size) is None:
        msg = f"size must be 'WxH', got {size!r}"
        raise ValueError(msg)


def image_request(
    prompt: str,
    *,
    reference_parts: Sequence[Mapping[str, object]],
    size: str,
    previous_response_id: str | None,
    model: str = IMAGE_MODEL,
) -> dict[str, object]:
    """Build the `POST /responses` body for one image turn.

    Args:
        prompt: What to draw.
        reference_parts: `input_image` parts of uploaded references.
        size: `WxH` (aspect ratio only).
        previous_response_id: The turn to edit, or `None`.
        model: The model id.

    Returns:
        The request body.
    """
    content: list[Mapping[str, object]] = [{"type": "input_text", "text": prompt}, *reference_parts]
    body: dict[str, object] = {
        "model": model,
        "input": [{"type": "message", "role": "user", "content": content}],
        "tools": [{"type": "image_generation", "size": size, "output_format": OUTPUT_FORMAT}],
        "stream": False,
    }
    if previous_response_id is not None:
        body["previous_response_id"] = previous_response_id
    return body


def parse_image(body: Mapping[str, object]) -> tuple[str, bytes]:
    """Return the response id and the first generated image's bytes.

    Args:
        body: A completed reply body.

    Returns:
        (response id, image bytes).

    Raises:
        ModelResponseError: If the reply has no id or no `image_generation_call` result.
    """
    response_id = body.get("id")
    if not isinstance(response_id, str) or not response_id:
        msg = f"Muse Image reply has no usable id: {response_id!r}"
        raise ModelResponseError(msg)
    output = body.get("output")
    for item in output if isinstance(output, list) else []:
        if isinstance(item, Mapping) and item.get("type") == IMAGE_CALL_TYPE:
            result = item.get("result")
            if isinstance(result, str) and result:
                try:
                    return response_id, base64.b64decode(result, validate=True)
                except binascii.Error as exc:
                    msg = f"Muse Image result in {response_id} is not base64"
                    raise ModelResponseError(msg) from exc
    msg = f"Muse Image reply {response_id} has no {IMAGE_CALL_TYPE} result"
    raise ModelResponseError(msg)


class MuseImageClient:
    """Muse Image on Meta's hosted Responses API."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        output_dir: Path,
        bus: Bus | None = None,
        model: str = IMAGE_MODEL,
        rpm: float = IMAGE_RPM,
        segment: str = DEFAULT_SEGMENT,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        seed: int | None = None,
    ) -> None:
        """Create the client.

        Args:
            transport: Sends one request mapping and returns the reply mapping.
            clock: Time source.
            output_dir: Where images are saved (`data/images/` in the app).
            bus: Bus for `relay.model_used`; `None` publishes nothing.
            model: The model id.
            rpm: Request budget per minute (the API allows 150).
            segment: Pipeline segment shown on the relay HUD.
            retry: Backoff policy for 429/5xx.
            sleep: Blocking sleep (injected in tests).
            monotonic: Monotonic time source (injected in tests).
            seed: Seed for the retry jitter.
        """
        self._clock = clock
        self._bus = bus
        self._output_dir = output_dir
        self._model = model
        self._segment = segment
        self._monotonic = monotonic
        limiter = TokenBucket(rpm, monotonic=monotonic, sleep=sleep)
        jitter = random.Random(seed)  # noqa: S311 - retry jitter, not cryptography
        self._sender = Sender(transport, limiter, retry or RetryPolicy(), sleep, jitter)
        self._files = FileUploader(self._sender)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Clock,
        bus: Bus | None = None,
        cache: ReplayCache | None = None,
    ) -> MuseImageClient:
        """Build a client whose transport always goes through the replay cache.

        Images are saved under `<RULECAM_DATA_DIR>/images/`.

        Args:
            settings: Application settings.
            clock: Time source.
            bus: Bus for `relay.model_used` (and `mode.changed` of a cache built here).
            cache: The replay cache; by default one is built from `settings`.

        Returns:
            The client.

        Raises:
            ClientConfigError: In `live` or `record` mode without credentials.
        """
        if cache is None:
            cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
        inner = live_transport(settings, required=cache.mode != "replay", what="Muse Image")
        transport = cache.wrap_transport(REPLAY_PROVIDER, IMAGE_MODEL, inner)
        return cls(transport, clock=clock, bus=bus, output_dir=settings.rulecam_data_dir / "images")

    def generate(
        self,
        prompt: str,
        *,
        reference_images: Sequence[Path] = (),
        size: str = DEFAULT_SIZE,
        previous_response_id: str | None = None,
        **unsupported: object,
    ) -> ImageResult:
        """Generate (or, with `previous_response_id`, edit) one image and save it as PNG.

        Args:
            prompt: What to draw.
            reference_images: Up to 10 JPEG/PNG/WebP references, uploaded via the Files API.
            size: `WxH`; sets the aspect ratio only.
            previous_response_id: The earlier turn to edit.
            **unsupported: Anything else; a mask or inpainting argument raises `ValueError`.

        Returns:
            The saved image's path and the response id.

        Raises:
            ValueError: For unsupported arguments, before any network call.
            ModelHTTPError: When the API keeps failing or rejects the request.
            ModelResponseError: When the reply has no image.
        """
        check_arguments(unsupported, reference_images, size)
        start = self._monotonic()
        parts = [file_part(p, self._files.upload(p)) for p in reference_images]
        body = image_request(
            prompt,
            reference_parts=parts,
            size=size,
            previous_response_id=previous_response_id,
            model=self._model,
        )
        result = self._sender.send({"method": "POST", "path": RESPONSES_PATH, "json": body})
        response_id, image = parse_image(response_body(result.reply.body))
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"{_SAFE_ID.sub('_', response_id)}.{OUTPUT_FORMAT}"
        path.write_bytes(image)
        publish_model_used(
            self._bus,
            ModelUsed(
                model=self._model,
                segment=self._segment,
                latency_ms=latency_ms(result, self._monotonic() - start),
                attempts=result.attempts,
            ),
        )
        _log.info("muse_image", response_id=response_id, n_references=len(parts), size=size)
        return ImageResult(path=path, response_id=response_id)
