"""Shared Responses API and Files API helpers for the hosted Meta clients (task P2-01).

Every hosted Meta model except Voice Transcribe is called the same way as SAM 3.1 (verified live
in issue #5): media is uploaded with `POST /files` (multipart, purpose `user_data`, the reply's
`id` is the file id), and the model is called with `POST /responses` and `stream: false`. Output
text is in `output[].content[]` parts of type `output_text`; errors are `{error: {type, message}}`
(handled by `transport.Sender`). Field names follow the official guides, read Oct 31 2026:
<https://dev.meta.ai/docs/file-handling> and
<https://dev.meta.ai/docs/api-reference/responses/schemas>.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.transport import (
    RECORDED_LATENCY_HEADER,
    HttpxTransport,
    Sender,
    SendResult,
    Transport,
)
from rulecam.config import Settings

RESPONSES_PATH = "/responses"
FILES_PATH = "/files"
FILE_PURPOSE = "user_data"
REPLAY_PROVIDER = "meta"
PENDING_STATUSES = frozenset({"queued", "in_progress"})
COMPLETED_STATUS = "completed"
_FILE_ID_CACHE_SIZE = 256

# MIME types the Files API accepts (file-handling guide), by lower-case file suffix.
CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".json": "application/json",
}


def content_type_for(path: Path) -> str:
    """Return the Files API MIME type for `path`, judged by its suffix.

    Args:
        path: The local file.

    Returns:
        The MIME type.

    Raises:
        ValueError: If the Files API does not accept this file type.
    """
    content_type = CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        msg = f"the Files API does not accept {path.suffix or 'files without an extension'}"
        raise ValueError(msg)
    return content_type


class FileUploader:
    """Uploads files with `POST /files` and remembers their ids by content hash.

    The upload request carries the raw bytes, which the replay cache keys by their SHA-256, so a
    replayed upload returns the recorded file id and the later `/responses` call hits the cache.
    """

    def __init__(self, sender: Sender) -> None:
        """Create the uploader.

        Args:
            sender: The client's sender (shared rate limiter and retries).
        """
        self._sender = sender
        self._ids: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def upload(self, path: Path) -> str:
        """Upload a local file (once per distinct content) and return its file id.

        Args:
            path: The file; its suffix picks the MIME type.

        Returns:
            The file id, e.g. `file-842549258569145`.

        Raises:
            ValueError: If the file type is not accepted by the Files API.
            ModelHTTPError: When the upload keeps failing or is rejected.
            ModelResponseError: When the reply has no file id.
        """
        content_type = content_type_for(path)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with self._lock:
            cached = self._ids.get(digest)
            if cached is not None:
                self._ids.move_to_end(digest)
                return cached
            request: dict[str, object] = {
                "method": "POST",
                "path": FILES_PATH,
                "form": {"purpose": FILE_PURPOSE},
                "file": {"filename": path.name, "content_type": content_type, "content": content},
            }
            body = self._sender.send(request).reply.body
            file_id = body.get("id") if isinstance(body, Mapping) else None
            if not isinstance(file_id, str) or not file_id:
                msg = "file upload reply has no id"
                raise ModelResponseError(msg)
            self._ids[digest] = file_id
            if len(self._ids) > _FILE_ID_CACHE_SIZE:
                self._ids.popitem(last=False)
            return file_id


def file_part(path: Path, file_id: str) -> dict[str, object]:
    """Build the `/responses` content part that references an uploaded file.

    Images and videos use their typed parts (`input_image`, `input_video`), audio uses
    `input_audio` with a top-level `file_id`, and everything else (PDF, text) uses `input_file`.

    Args:
        path: The local file the id was uploaded from (its suffix picks the part type).
        file_id: The id returned by `FileUploader.upload`.

    Returns:
        The content part.
    """
    major = content_type_for(path).split("/", 1)[0]
    part_type = {"image": "input_image", "video": "input_video", "audio": "input_audio"}.get(
        major, "input_file"
    )
    return {"type": part_type, "file_id": file_id}


def response_body(body: object) -> Mapping[str, object]:
    """Check a `/responses` reply body and return it as a mapping.

    Args:
        body: The decoded JSON body.

    Returns:
        The body.

    Raises:
        ModelResponseError: If the body is not an object, or its status is terminal but not
            `completed` (`failed`, `incomplete`, `cancelled`).
    """
    if not isinstance(body, Mapping):
        msg = "Responses API reply is not a JSON object"
        raise ModelResponseError(msg)
    status = body.get("status")
    if status is not None and status != COMPLETED_STATUS and status not in PENDING_STATUSES:
        detail = body.get("error") or body.get("incomplete_details") or ""
        msg = f"response {body.get('id')} ended with status {status!r}: {detail}"
        raise ModelResponseError(msg)
    return body


def output_text(body: Mapping[str, object]) -> str:
    """Return the concatenated `output_text` parts of the `message` items of a reply.

    Reasoning summaries and tool-call items are skipped. A top-level `output_text` string is
    also accepted.

    Args:
        body: A completed reply body.

    Returns:
        The output text, possibly empty.
    """
    direct = body.get("output_text")
    if isinstance(direct, str):
        return direct
    texts: list[str] = []
    output = body.get("output")
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, Mapping) or item.get("type", "message") != "message":
            continue
        content = item.get("content")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, Mapping) and part.get("type") == "output_text":
                texts.append(str(part.get("text", "")))
    return "".join(texts)


def cached_tokens(body: Mapping[str, object]) -> int:
    """Return `usage.input_tokens_details.cached_tokens`, or 0 when absent."""
    usage = body.get("usage")
    details = usage.get("input_tokens_details") if isinstance(usage, Mapping) else None
    value = details.get("cached_tokens") if isinstance(details, Mapping) else None
    return value if isinstance(value, int) else 0


@dataclass(frozen=True)
class TimedReply:
    """A reply body with the attempts and latency it took."""

    body: object
    attempts: int
    latency_ms: int


def latency_ms(result: SendResult, elapsed_s: float) -> int:
    """Return the recorded latency on a replay hit, else the measured one, in milliseconds.

    Args:
        result: The send result.
        elapsed_s: Measured wall-clock seconds of the call.

    Returns:
        The latency to report on `relay.model_used`.
    """
    recorded = result.reply.header(RECORDED_LATENCY_HEADER)
    if recorded is not None:
        try:
            return max(0, int(recorded))
        except ValueError:
            pass
    return max(0, round(elapsed_s * 1000))


def live_transport(settings: Settings, *, required: bool, what: str) -> Transport:
    """Return the real HTTP transport, or a no-network stand-in when allowed and unconfigured.

    Args:
        settings: Application settings.
        required: Whether credentials must exist (live and record modes).
        what: The client's name, for the error message.

    Returns:
        The transport to wrap in the replay cache.

    Raises:
        ClientConfigError: If credentials are required but missing.
    """
    if settings.meta_api_base_url and settings.meta_api_key is not None:
        return HttpxTransport(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())
    if required:
        msg = f"META_API_BASE_URL and META_API_KEY are required for {what}"
        raise ClientConfigError(msg)
    return _no_network


def _no_network(request: Mapping[str, object]) -> Mapping[str, object]:
    """Stand-in transport for replay without credentials; reached only after a mode switch."""
    msg = f"no Meta API credentials configured to send {request.get('path')} live"
    raise ClientConfigError(msg)
