"""A fake RunPod REST v2 API on localhost for the deploy script's tests (no network)."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

PAGE = 2  # pods per page, so the client has to follow cursors


@dataclass
class Call:
    """One request the fake received."""

    method: str
    path: str
    query: dict[str, list[str]]
    body: Any
    user_agent: str
    authorization: str


@dataclass
class FakeAccount:
    """The account state the fake serves."""

    pods: list[dict[str, Any]] = field(default_factory=list)
    volumes: list[dict[str, Any]] = field(default_factory=list)
    secrets: list[dict[str, Any]] = field(default_factory=list)
    gpus: list[dict[str, Any]] = field(default_factory=list)
    cpus: list[dict[str, Any]] = field(default_factory=list)
    datacenters: list[dict[str, Any]] = field(default_factory=list)
    out_of_stock: set[str] = field(default_factory=set)  # GPU ids / CPU flavors that 400
    refuse_start: bool = False
    calls: list[Call] = field(default_factory=list)
    secret_values: dict[str, str] = field(default_factory=dict)

    def writes(self) -> list[Call]:
        """Every non-GET call."""
        return [c for c in self.calls if c.method != "GET"]

    def pod(self, pod_id: str) -> dict[str, Any] | None:
        """A pod by id."""
        return next((p for p in self.pods if p["id"] == pod_id), None)


def stocked_account() -> FakeAccount:
    """An empty account with GPU and CPU stock in two data centers."""
    return FakeAccount(
        gpus=[
            {
                "id": "NVIDIA RTX A6000",
                "availability": "LOW",
                "dataCenters": [
                    {"id": "EU-RO-1", "availability": "LOW"},
                    {"id": "US-KS-2", "availability": "NONE"},
                ],
            },
            {
                "id": "NVIDIA L40S",
                "availability": "HIGH",
                "dataCenters": [
                    {"id": "US-KS-2", "availability": "MEDIUM"},
                    {"id": "EU-RO-1", "availability": "HIGH"},
                ],
            },
            {"id": "NVIDIA L4", "availability": "NONE"},
        ],
        cpus=[
            {"id": "cpu3c", "dataCenters": [{"id": "US-TX-3", "availability": "HIGH"}]},
            {
                "id": "cpu5c",
                "dataCenters": [
                    {"id": "EU-RO-1", "availability": "MEDIUM"},
                    {"id": "US-TX-3", "availability": "LOW"},
                ],
            },
        ],
        datacenters=[
            {"id": "US-TX-3", "networkVolumeTypes": []},
            {"id": "EU-RO-1", "networkVolumeTypes": ["STANDARD"]},
        ],
    )


class FakeRunPod:
    """Serves `FakeAccount` over HTTP on 127.0.0.1 in a background thread."""

    def __init__(self, account: FakeAccount) -> None:
        """Start serving."""
        self.account = account
        self._lock = threading.Lock()
        self._next = 0
        handler = _handler(self)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        """Base URL (paths start with /v2)."""
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}"

    def close(self) -> None:
        """Stop serving."""
        self.server.shutdown()
        self.server.server_close()

    def new_id(self, kind: str) -> str:
        """A fresh id."""
        with self._lock:
            self._next += 1
            return f"{kind}{self._next}"

    def handle(  # noqa: C901, PLR0911, PLR0912 - one flat router
        self, method: str, path: str, query: dict[str, list[str]], body: Any
    ) -> tuple[int, Any]:
        """Route one request; returns (status, JSON body)."""
        acct = self.account
        if path == "/health":
            return 200, {"ok": True}
        if method == "GET" and path == "/v2/pods":
            start = int(query.get("cursor", ["0"])[0])
            page = acct.pods[start : start + PAGE]
            more = start + PAGE < len(acct.pods)
            cursor = str(start + PAGE) if more else None
            return 200, {"pods": page, "pagination": {"nextCursor": cursor, "hasNextPage": more}}
        if method == "POST" and path == "/v2/pods":
            compute = body.get("gpu") or body.get("cpu") or {}
            if compute.get("id") in acct.out_of_stock:
                return 400, {"title": "Bad Request", "status": 400, "detail": "no instances"}
            pod = {
                "id": self.new_id("pod"),
                "status": "RUNNING",
                "cost": 0.5 if "gpu" in body else 0.06,
                "dataCenterId": (body.get("dataCenterIds") or ["ANY"])[0],
                "ssh": {"direct": None},
                **body,
            }
            acct.pods.append(pod)
            return 201, pod
        match = re.fullmatch(r"/v2/pods/([^/]+)(/action)?", path)
        if match:
            found = acct.pod(match[1])
            if found is None:
                return 404, {"title": "Not Found", "status": 404, "detail": "pod not found"}
            pod = found
            if match[2]:
                if body["action"] == "start" and acct.refuse_start:
                    return 409, {"title": "Conflict", "status": 409, "detail": "no free GPU"}
                pod["status"] = {"start": "RUNNING", "stop": "EXITED"}.get(body["action"], "X")
                return 200, pod
            if method == "PATCH":
                pod.update(body)
                return 200, pod
            if method == "DELETE":
                pod["status"] = "TERMINATED"
                return 204, None
            return 200, pod
        if path == "/v2/network-volumes":
            if method == "POST":
                volume = {"id": self.new_id("vol"), "type": "STANDARD", **body}
                acct.volumes.append(volume)
                return 201, volume
            return 200, {"networkVolumes": acct.volumes, "pagination": {"hasNextPage": False}}
        if path.startswith("/v2/account/secrets"):
            if method == "POST":
                acct.secret_values[body["name"]] = body["value"]
                secret = {"id": self.new_id("sec"), "name": body["name"]}
                acct.secrets.append(secret)
                return 201, secret
            if method == "PATCH":
                secret_id = path.rsplit("/", 1)[-1]
                name = next(s["name"] for s in acct.secrets if s["id"] == secret_id)
                acct.secret_values[name] = body["value"]
                return 200, {"id": secret_id, "name": name}
            return 200, {"secrets": acct.secrets}
        catalogs = {
            "/v2/catalog/gpus": ("gpus", acct.gpus),
            "/v2/catalog/cpus": ("cpus", acct.cpus),
            "/v2/catalog/datacenters": ("dataCenters", acct.datacenters),
        }
        if path in catalogs:
            key, items = catalogs[path]
            return 200, {key: items}
        return 404, {"title": "Not Found", "status": 404, "detail": path}


def _handler(fake: FakeRunPod) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            fake.account.calls.append(
                Call(
                    self.command,
                    parts.path,
                    parse_qs(parts.query),
                    body,
                    self.headers.get("User-Agent", ""),
                    self.headers.get("Authorization", ""),
                )
            )
            status, answer = fake.handle(self.command, parts.path, parse_qs(parts.query), body)
            data = b"" if answer is None else json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if status != HTTPStatus.NO_CONTENT:
                self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_DELETE = _serve  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler
