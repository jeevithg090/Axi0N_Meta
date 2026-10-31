"""Owner login (WS4): passphrase hash, session cookie, CSRF, Origin, rate limit and loopback."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from rulecam.api.app import AppServices, create_app, services_from_settings
from rulecam.api.auth import (
    CLIENT_IP_HEADER,
    CSRF_HEADER,
    PROXY_SECRET_HEADER,
    SESSION_COOKIE,
    ClientAddress,
    LoginLimiter,
    OwnerAuth,
    SessionSigner,
)
from rulecam.api.passphrase import PassphraseHash, hash_passphrase, passphrase_problem
from rulecam.bus import Bus
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain
from rulecam.replay.cache import ReplayCache
from tests.fakes import FakeClock

PASSPHRASE = "correct horse battery staple"  # noqa: S105 - a test passphrase
SECRET = "s" * 40
UI = "https://rulecam.vercel.app"
API = "https://rulecam-api-8000.proxy.runpod.net"
PROXY = ("100.64.0.9", 4000)  # what uvicorn sees behind the RunPod proxy
FAST = {"n": 2**4, "r": 8, "p": 1}  # cheap scrypt for tests; production uses 2**17
HASH = hash_passphrase(PASSPHRASE, **FAST)


def make_services(tmp_path: Path, clock: FakeClock, **auth: object) -> AppServices:
    bus = Bus()
    origins = frozenset({UI})
    options: dict[str, object] = {"passphrase_hash": HASH, "session_secret": SECRET, **auth}
    return AppServices(
        bus=bus,
        cache=ReplayCache(tmp_path / "replay", "replay", clock=clock, bus=bus),
        chain_root=tmp_path / "chain",
        site="site",
        viewer_origins=origins,
        auth=OwnerAuth(clock=clock, allowed_origins=origins, **options),  # type: ignore[arg-type]
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def services(tmp_path: Path, clock: FakeClock) -> AppServices:
    return make_services(tmp_path, clock)


def browser(services: AppServices, base_url: str = API) -> TestClient:
    """The UI's browser, as the API sees it through Vercel and the RunPod proxy."""
    return TestClient(create_app(services), base_url=base_url, client=PROXY)


def log_in(client: TestClient) -> str:
    reply = client.post("/auth/login", json={"passphrase": PASSPHRASE}, headers={"Origin": UI})
    assert reply.status_code == 200, reply.text
    token = reply.json()["csrf_token"]
    assert isinstance(token, str)
    return token


def writes(token: str | None = None, origin: str | None = UI) -> dict[str, str]:
    headers = {"Origin": origin} if origin else {}
    if token is not None:
        headers[CSRF_HEADER] = token
    return headers


# --- passphrase -----------------------------------------------------------------------------


def test_hash_round_trip_and_defaults() -> None:
    parsed = PassphraseHash.parse(HASH)
    assert parsed.verify(PASSPHRASE)
    assert not parsed.verify("wrong horse battery staple")
    assert not parsed.verify("")
    assert not parsed.verify("x" * 2000)
    assert parsed.line() == HASH
    assert HASH.startswith("scrypt:16:8:1:")
    assert "$" not in HASH
    assert hash_passphrase(PASSPHRASE, **FAST) != HASH  # fresh salt every time
    strong = PassphraseHash.parse(hash_passphrase(PASSPHRASE))
    assert (strong.n, strong.r, strong.p) == (2**17, 8, 1)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "argon2id:1:2:3:4:5",
        "scrypt:16:8:1:salt",
        "scrypt:x:8:1:AAAAAAAAAAAAAAAAAAAAAA:AAAA",
        "scrypt:15:8:1:AAAAAAAAAAAAAAAAAAAAAA:" + "A" * 43,  # not a power of two
        "scrypt:1048576:16:1:AAAAAAAAAAAAAAAAAAAAAA:" + "A" * 43,  # too much memory
        "scrypt:16:8:1:AAAA:" + "A" * 43,  # short salt
        "scrypt:16:8:1:AAAAAAAAAAAAAAAAAAAAAA:AAAA",  # short key
    ],
)
def test_bad_hash_lines_are_refused(line: str) -> None:
    with pytest.raises(ValueError, match="scrypt"):
        PassphraseHash.parse(line)


def test_passphrase_rules() -> None:
    assert passphrase_problem("short") is not None
    assert passphrase_problem(" leading space here") is not None
    assert passphrase_problem("x" * 2000) is not None
    assert passphrase_problem(PASSPHRASE) is None


# --- sessions, limiter, client address ------------------------------------------------------


def test_session_tokens(clock: FakeClock) -> None:
    signer = SessionSigner(SECRET, HASH)
    token, session = signer.issue(clock.now(), 60)
    assert signer.read(token, clock.now()) == session
    body, _, sig = token.partition(".")
    assert signer.read(f"{body}.{sig[:-2]}AA", clock.now()) is None
    assert signer.read("nodot", clock.now()) is None
    assert signer.read("é.é", clock.now()) is None
    assert signer.read(token + "x" * 2000, clock.now()) is None
    assert SessionSigner(SECRET, "another hash").read(token, clock.now()) is None
    assert SessionSigner("t" * 40, HASH).read(token, clock.now()) is None
    forged_body = "e30"  # "{}" correctly signed is still not a session
    assert signer.read(f"{forged_body}.{signer._sign(forged_body)}", clock.now()) is None
    clock.advance(61)
    assert signer.read(token, clock.now()) is None


def test_limiter_per_client_and_total() -> None:
    limiter = LoginLimiter(per_client=2, total=3, window_s=60)
    assert limiter.attempt("a", 0) == 0
    assert limiter.attempt("a", 1) == 0
    assert limiter.attempt("a", 2) == pytest.approx(58)
    assert limiter.attempt("b", 3) == 0
    assert limiter.attempt("c", 4) == pytest.approx(56)  # the total is spent
    assert limiter.attempt("a", 61) == 0  # the first attempt left the window


def test_limiter_bounds_its_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rulecam.api.auth.MAX_TRACKED_CLIENTS", 2)
    limiter = LoginLimiter(per_client=1, total=100, window_s=60)
    for name in ("a", "b", "c"):
        assert limiter.attempt(name, 0) == 0
    assert limiter.attempt("a", 1) == 0  # "a" was forgotten to make room


def conn(headers: dict[str, str], client: tuple[str, int] = PROXY) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": client})


def test_client_address_trusts_the_ui_only_with_the_secret() -> None:
    forwarded = {CLIENT_IP_HEADER: "198.51.100.7", PROXY_SECRET_HEADER: "k" * 32}
    assert ClientAddress("k" * 32)(conn(forwarded)) == "198.51.100.7"
    assert ClientAddress("k" * 32)(conn({**forwarded, PROXY_SECRET_HEADER: "nope"})) == PROXY[0]
    assert ClientAddress("k" * 32)(conn({CLIENT_IP_HEADER: "198.51.100.7"})) == PROXY[0]
    assert ClientAddress("k" * 32)(conn({**forwarded, CLIENT_IP_HEADER: "evil"})) == PROXY[0]
    assert ClientAddress(None)(conn(forwarded)) == PROXY[0]
    assert ClientAddress()(Request({"type": "http", "headers": [], "client": None})) == ""


# --- login, me, logout ----------------------------------------------------------------------


def test_login_sets_a_strict_host_only_cookie(services: AppServices) -> None:
    with browser(services) as client:
        assert client.get("/auth/me").json() == {
            "owner": False,
            "via": None,
            "csrf_token": None,
            "expires_at": None,
            "login_enabled": True,
        }
        reply = client.post("/auth/login", json={"passphrase": PASSPHRASE}, headers=writes())
        assert reply.status_code == 200
        cookie = reply.headers["set-cookie"]
        assert cookie.startswith(f"{SESSION_COOKIE}=")
        for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=43200"):
            assert flag in cookie, flag
        assert "Domain" not in cookie  # host-only: the UI's own host behind the Vercel rewrite
        assert reply.headers["cache-control"] == "no-store"
        assert PASSPHRASE not in reply.text
        me = client.get("/auth/me")
        assert me.headers["cache-control"] == "no-store"
        body = me.json()
        assert body["owner"] is True
        assert body["via"] == "session"
        assert body["csrf_token"] == reply.json()["csrf_token"]
        assert body["expires_at"] == "2026-10-31T00:00:00Z"


def test_wrong_passphrase_and_bad_requests(services: AppServices) -> None:
    with browser(services) as client:
        bad = client.post("/auth/login", json={"passphrase": "not it at all"}, headers=writes())
        assert bad.status_code == 401
        assert "set-cookie" not in bad.headers
        assert client.post("/auth/login", json={"passphrase": ""}).status_code == 422
        assert client.post("/auth/login", json={"pass": PASSPHRASE}).status_code == 422
        foreign = client.post(
            "/auth/login",
            json={"passphrase": PASSPHRASE},
            headers=writes(origin="https://evil.example"),
        )
        assert foreign.status_code == 403
    with browser(services, base_url="http://rulecam-api-8000.proxy.runpod.net") as plain:
        assert plain.post("/auth/login", json={"passphrase": PASSPHRASE}).status_code == 403


def test_login_is_rate_limited(services: AppServices, clock: FakeClock) -> None:
    with browser(services) as client:
        for _ in range(5):
            assert (
                client.post("/auth/login", json={"passphrase": "nope nope nope"}).status_code == 401
            )
        limited = client.post("/auth/login", json={"passphrase": PASSPHRASE})
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"
        clock.advance(61)
        assert client.post("/auth/login", json={"passphrase": PASSPHRASE}).status_code == 200


def test_login_off_without_settings(tmp_path: Path, clock: FakeClock) -> None:
    services = make_services(tmp_path, clock, passphrase_hash=None, session_secret=None)
    with browser(services) as client:
        assert client.get("/auth/me").json()["login_enabled"] is False
        assert client.post("/auth/login", json={"passphrase": PASSPHRASE}).status_code == 503
        client.cookies.set(SESSION_COOKIE, "forged.token")
        assert client.put("/mode", json={"mode": "live"}, headers=writes("x")).status_code == 403


@pytest.mark.parametrize(
    ("hash_line", "secret"),
    [(HASH, "too short"), ("scrypt:garbage", SECRET), (HASH, None), (None, SECRET)],
)
def test_misconfigured_login_stays_off(hash_line: str | None, secret: str | None) -> None:
    auth = OwnerAuth(clock=FakeClock(), passphrase_hash=hash_line, session_secret=secret)
    assert auth.login_enabled is False


def test_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        owner_passphrase_hash=HASH,
        session_secret=SECRET,
        session_ttl_s=600,
        ui_proxy_secret="k" * 32,
    )
    auth = OwnerAuth.from_settings(settings, clock=FakeClock(), allowed_origins=frozenset({UI}))
    assert auth.login_enabled
    assert (
        OwnerAuth.from_settings(
            Settings(_env_file=None), clock=FakeClock(), allowed_origins=frozenset()
        ).login_enabled
        is False
    )


def test_services_from_settings_wire_login(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rulecam_data_dir=tmp_path,
        owner_passphrase_hash=HASH,
        session_secret=SECRET,
        public_ui_url=UI,
        ui_proxy_secret="k" * 32,
    )
    services = services_from_settings(settings, "s")
    assert services.auth is not None
    assert services.auth.login_enabled
    with TestClient(create_app(services), base_url=API, client=PROXY) as client:
        assert client.post("/pair/token", headers=writes("x")).status_code == 403
        assert client.post("/correction", json={}, headers=writes("x")).status_code == 403
        token = log_in(client)
        assert client.put("/mode", json={"mode": "live"}, headers=writes(token)).status_code == 200
        # Pair-a-Phone's own owner check is replaced too: a session may pair a phone.
        assert client.post("/pair/token", headers=writes(token)).status_code == 201
        assert client.post("/correction", json={}, headers=writes(token)).status_code == 422


def test_session_expires(services: AppServices, clock: FakeClock) -> None:
    with browser(services) as client:
        token = log_in(client)
        clock.advance(12 * 3600 + 1)
        assert client.get("/auth/me").json()["owner"] is False
        assert client.put("/mode", json={"mode": "live"}, headers=writes(token)).status_code == 403


def test_logout_revokes_the_session(services: AppServices) -> None:
    with browser(services) as client:
        token = log_in(client)
        stolen = client.cookies.get(SESSION_COOKIE)
        assert client.post("/auth/logout", headers=writes()).status_code == 403  # no CSRF
        reply = client.post("/auth/logout", headers=writes(token))
        assert reply.status_code == 204
        assert "Max-Age=0" in reply.headers["set-cookie"]
        assert client.get("/auth/me").json()["owner"] is False
        assert stolen is not None
        client.cookies.set(SESSION_COOKIE, stolen)
        assert client.get("/auth/me").json()["owner"] is False
        assert client.put("/mode", json={"mode": "live"}, headers=writes(token)).status_code == 403
        assert client.post("/auth/logout").status_code == 204  # no session: just clears


# --- what a session may do ------------------------------------------------------------------


def test_writes_need_csrf_and_the_ui_origin(services: AppServices) -> None:
    with browser(services) as client:
        assert client.put("/mode", json={"mode": "live"}, headers=writes()).status_code == 403
        token = log_in(client)

        def put(headers: dict[str, str]) -> int:
            return int(client.put("/mode", json={"mode": "live"}, headers=headers).status_code)

        assert put(writes()) == 403  # no CSRF header
        assert put(writes("wrong")) == 403
        assert put(writes(token, origin=None)) == 403
        assert put(writes(token, origin="https://evil.example")) == 403
        mode_before = str(services.cache.mode)
        assert put(writes(token)) == 200
    assert (mode_before, str(services.cache.mode)) == ("replay", "live")


def test_reads_need_a_session_but_no_csrf(services: AppServices, clock: FakeClock) -> None:
    EventChain(services.chain_root, "site", clock=clock).append("plan", {"p": 1})
    with browser(services) as client:
        assert client.get("/evidence/2026-10-30/chain").status_code == 403
        log_in(client)
        assert client.get("/evidence/2026-10-30/chain").status_code == 200
        foreign = client.get(
            "/evidence/2026-10-30/chain", headers={"Origin": "https://evil.example"}
        )
        assert foreign.status_code == 403


def test_tunnel_without_session_is_refused(services: AppServices) -> None:
    with browser(services) as client:
        for method, path in (
            ("PUT", "/mode"),
            ("POST", "/plans/p1/answers"),
            ("POST", "/plans/requests"),
            ("GET", "/plans/p1/questions"),
        ):
            assert client.request(method, path, json={}, headers=writes("x")).status_code == 403, (
                path
            )


def test_loopback_owner_still_works(services: AppServices) -> None:
    with TestClient(
        create_app(services), base_url="http://localhost", client=("127.0.0.1", 1)
    ) as client:
        me = client.get("/auth/me").json()
        assert me["owner"] is True
        assert me["via"] == "loopback"
        assert me["csrf_token"] is None
        assert client.put("/mode", json={"mode": "live"}).status_code == 200


def test_websocket_with_a_session(services: AppServices) -> None:
    services.viewer_origins = frozenset()  # only a session (or loopback) may watch now
    with browser(services) as client:
        ws_url = API.replace("https", "wss", 1) + "/ws"
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(ws_url) as ws:
            ws.receive_json()
        log_in(client)
        with client.websocket_connect(ws_url) as ws:
            threading.Timer(0.2, lambda: services.cache.set_mode("record")).start()
            assert ws.receive_json()["topic"] == "mode.changed"
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(ws_url, headers={"Origin": "https://evil.example"}) as ws,
        ):
            ws.receive_json()
