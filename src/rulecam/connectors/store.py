"""Where connected cameras are remembered: `cameras.json` and, apart, `secrets.json`.

Both files live under `<data>/connectors/` and are written owner-read-only (0600), atomically.
A camera's URL is stored without user info; its user name and password are only in
`secrets.json`, never in a record the API serves and never in a log line.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field, SecretStr

from rulecam.connectors.sources import SourceKind
from rulecam.contracts import Provenance
from rulecam.log import get_logger

_log = get_logger(__name__)

FILE_MODE: Final = 0o600


class CameraRecord(BaseModel):
    """One connected camera as stored (no credentials)."""

    camera_id: str
    name: str
    named_by: str = Field(default="owner", description="'owner', 'spark' or 'source'.")
    kind: SourceKind
    url: str = Field(description="The URL without user info, or the clip's path.")
    loop: bool = True
    provenance: Provenance = Provenance.RECORDED
    created_at: datetime
    blocked: str | None = Field(
        default=None, description="Why the camera was stopped for good (a private space)."
    )


class Credentials(BaseModel):
    """A camera account's user name and password."""

    username: str
    password: SecretStr | None = None

    def secrets(self) -> tuple[str, ...]:
        """Strings to scrub from anything ffmpeg prints."""
        password = self.password.get_secret_value() if self.password else ""
        return tuple(s for s in (self.username, password) if s)


class _Saved(BaseModel):
    next_n: int = 1
    cameras: list[CameraRecord] = Field(default_factory=list)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    tmp.replace(path)
    path.chmod(FILE_MODE)


class CameraStore:
    """Thread-safe, file-backed camera records and credentials."""

    def __init__(self, root: Path) -> None:
        """Load whatever is saved under `root`.

        Args:
            root: The connectors folder (`<data>/connectors`).
        """
        self._cameras_file = root / "cameras.json"
        self._secrets_file = root / "secrets.json"
        self._lock = threading.Lock()
        self._saved = _Saved()
        self._secrets: dict[str, Credentials] = {}
        try:
            if self._cameras_file.is_file():
                self._saved = _Saved.model_validate_json(self._cameras_file.read_text("utf-8"))
            if self._secrets_file.is_file():
                raw = json.loads(self._secrets_file.read_text("utf-8"))
                self._secrets = {k: Credentials.model_validate(v) for k, v in raw.items()}
        except (OSError, ValueError) as exc:  # never let a damaged file stop the API starting
            _log.error("connector_store_unreadable", error=type(exc).__name__)
            for path in (self._cameras_file, self._secrets_file):
                if path.is_file():
                    path.replace(path.with_suffix(".unreadable"))
            self._saved, self._secrets = _Saved(), {}

    def records(self) -> list[CameraRecord]:
        """Every stored camera, oldest first."""
        with self._lock:
            return list(self._saved.cameras)

    def get(self, camera_id: str) -> CameraRecord | None:
        """One camera's record."""
        with self._lock:
            return next((c for c in self._saved.cameras if c.camera_id == camera_id), None)

    def new_id(self) -> str:
        """A camera id never used before here (`cam-<n>`)."""
        with self._lock:
            n = self._saved.next_n
            self._saved = self._saved.model_copy(update={"next_n": n + 1})
            self._flush()
        return f"cam-{n}"

    def put(self, record: CameraRecord, credentials: Credentials | None = None) -> None:
        """Add or replace a camera (and its credentials, when given)."""
        with self._lock:
            others = [c for c in self._saved.cameras if c.camera_id != record.camera_id]
            self._saved = self._saved.model_copy(update={"cameras": [*others, record]})
            if credentials is not None:
                self._secrets[record.camera_id] = credentials
                self._flush_secrets()
            self._flush()

    def remove(self, camera_id: str) -> None:
        """Forget a camera and its credentials."""
        with self._lock:
            cameras = [c for c in self._saved.cameras if c.camera_id != camera_id]
            self._saved = self._saved.model_copy(update={"cameras": cameras})
            if self._secrets.pop(camera_id, None) is not None:
                self._flush_secrets()
            self._flush()

    def credentials(self, camera_id: str) -> Credentials | None:
        """A camera's stored credentials."""
        with self._lock:
            return self._secrets.get(camera_id)

    def _flush(self) -> None:
        _write_private(self._cameras_file, self._saved.model_dump_json(indent=2))

    def _flush_secrets(self) -> None:
        data = {
            k: {
                "username": v.username,
                "password": v.password.get_secret_value() if v.password else None,
            }
            for k, v in self._secrets.items()
        }
        _write_private(self._secrets_file, json.dumps(data, indent=2))
