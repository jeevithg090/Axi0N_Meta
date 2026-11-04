"""Find cameras on the site's network: ONVIF WS-Discovery, an RTSP port sweep, ONVIF stream URIs.

- `ws_discover` sends the ONVIF WS-Discovery probe (SOAP over UDP multicast to
  239.255.255.250:3702, type `NetworkVideoTransmitter`) and lists every camera or NVR that
  answers, with its service address and the name, maker and location it advertises.
- `rtsp_sweep` tries a TCP connection to the usual RTSP ports on every address of this machine's
  /24 network, for cameras that have ONVIF off (many Reolink and Tapo models ship that way).
- `onvif_streams` asks one ONVIF device for its media profiles and each profile's RTSP URI
  (`GetCapabilities`, `GetProfiles`, `GetStreamUri`), signing with a WS-Security
  UsernameToken digest. The returned URIs carry no credentials; the owner's user name and
  password go to the connector store, never into a URL that is shown or logged.

Everything here only works when Rulecam runs on the same network as the cameras (a site box or
laptop), not on a cloud pod. Replies from devices are data: parsed with defusedxml, never run.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import socket
import time
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Final
from urllib.parse import unquote, urlsplit
from xml.etree.ElementTree import Element
from xml.sax.saxutils import escape

import httpx
from defusedxml import ElementTree
from pydantic import BaseModel, Field

from rulecam.connectors.errors import DiscoveryError
from rulecam.connectors.sources import split_credentials
from rulecam.log import get_logger

_log = get_logger(__name__)

MULTICAST: Final = ("239.255.255.250", 3702)
RTSP_PORTS: Final = (554, 8554)
SWEEP_TIMEOUT_S: Final = 0.4
SWEEP_WORKERS: Final = 64
MAX_REPLY_BYTES: Final = 65_535
ONVIF_TIMEOUT_S: Final = 8.0
HTTP_ERROR: Final = 400
SOAP_ENV: Final = "http://www.w3.org/2003/05/soap-envelope"
WSSE: Final = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU: Final = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
DIGEST: Final = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0"
    "#PasswordDigest"
)
B64: Final = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"
)
DEVICE_NS: Final = "http://www.onvif.org/ver10/device/wsdl"
MEDIA_NS: Final = "http://www.onvif.org/ver10/media/wsdl"
SCHEMA_NS: Final = "http://www.onvif.org/ver10/schema"

PROBE_TEMPLATE: Final = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
    'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
    'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    "<e:Header><w:MessageID>uuid:{message_id}</w:MessageID>"
    '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
    '<w:Action e:mustUnderstand="true">'
    "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>"
    "<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>"
    "</e:Envelope>"
)


class FoundDevice(BaseModel):
    """A camera or NVR found on the network."""

    address: str = Field(description="IP address.")
    via: str = Field(description="'onvif' (answered WS-Discovery) or 'rtsp-port'.")
    xaddr: str | None = Field(default=None, description="ONVIF device service URL.")
    name: str | None = None
    hardware: str | None = None
    location: str | None = None
    endpoint: str | None = None
    rtsp_ports: list[int] = Field(default_factory=list)


class ScanResult(BaseModel):
    """`POST /connectors/scan`."""

    devices: list[FoundDevice]
    local_ip: str | None
    subnet: str | None
    took_s: float
    notes: list[str] = Field(default_factory=list)


class OnvifProfile(BaseModel):
    """One media profile of an ONVIF device and its RTSP URI (no credentials in it)."""

    token: str
    name: str
    encoding: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    rtsp_url: str | None = None
    error: str | None = None


class OnvifDevice(BaseModel):
    """`POST /connectors/onvif`: the device and its streams."""

    xaddr: str
    manufacturer: str | None = None
    model: str | None = None
    firmware: str | None = None
    profiles: list[OnvifProfile]


# ---- XML helpers -----------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _all(root: Element, name: str) -> list[Element]:
    return [el for el in root.iter() if _local(el.tag) == name]


def _first(root: Element, name: str) -> Element | None:
    return next((el for el in root.iter() if _local(el.tag) == name), None)


def _text(root: Element, name: str) -> str | None:
    el = _first(root, name)
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def _parse(xml: bytes | str) -> Element:
    try:
        root: Element = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        msg = "the device sent XML that could not be read"
        raise DiscoveryError(msg) from exc
    return root


# ---- WS-Discovery ----------------------------------------------------------------------------


def probe_message(message_id: str | None = None) -> bytes:
    """The WS-Discovery probe for ONVIF video transmitters."""
    return PROBE_TEMPLATE.format(message_id=message_id or uuid.uuid4()).encode()


def _scope(scopes: Iterable[str], kind: str) -> str | None:
    prefix = f"onvif://www.onvif.org/{kind}/"
    for scope in scopes:
        if scope.lower().startswith(prefix):
            return unquote(scope[len(prefix) :]).replace("_", " ") or None
    return None


def parse_probe_matches(xml: bytes, sender: str) -> list[FoundDevice]:
    """The devices in one WS-Discovery `ProbeMatches` reply.

    Args:
        xml: The UDP reply.
        sender: The address it came from (used when no service address names a host).

    Returns:
        One device per `ProbeMatch`; `[]` for anything else.
    """
    try:
        root = _parse(xml)
    except DiscoveryError:
        return []
    devices = []
    for match in _all(root, "ProbeMatch"):
        xaddrs = (_text(match, "XAddrs") or "").split()
        scopes = (_text(match, "Scopes") or "").split()
        xaddr = next((x for x in xaddrs if x.startswith("http")), None)
        host = urlsplit(xaddr).hostname if xaddr else None
        devices.append(
            FoundDevice(
                address=host or sender,
                via="onvif",
                xaddr=xaddr,
                name=_scope(scopes, "name"),
                hardware=_scope(scopes, "hardware"),
                location=_scope(scopes, "location"),
                endpoint=_text(match, "Address"),
            )
        )
    return devices


def ws_discover(timeout_s: float = 3.0) -> list[FoundDevice]:
    """Send the ONVIF probe and collect answers for `timeout_s`.

    Returns:
        The devices that answered, one per endpoint.

    Raises:
        DiscoveryError: When the probe cannot be sent (no network).
    """
    found: dict[str, FoundDevice] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.25)
        try:
            sock.bind(("", 0))
            for _ in range(2):  # UDP: send twice
                sock.sendto(probe_message(), MULTICAST)
        except OSError as exc:
            msg = f"could not send the ONVIF probe: {exc}"
            raise DiscoveryError(msg) from exc
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data, (sender, _) = sock.recvfrom(MAX_REPLY_BYTES)
            except TimeoutError:
                continue
            except OSError:
                break
            for device in parse_probe_matches(data, sender):
                found.setdefault(device.endpoint or device.xaddr or device.address, device)
    return list(found.values())


# ---- RTSP port sweep -------------------------------------------------------------------------


def local_ipv4() -> str | None:
    """This machine's address on its default route (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1: only picks the outgoing interface
            address: str = sock.getsockname()[0]
        except OSError:
            return None
    return None if address.startswith("127.") else address


def _open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=SWEEP_TIMEOUT_S):
            return True
    except OSError:
        return False


def rtsp_sweep(local_ip: str, ports: Iterable[int] = RTSP_PORTS) -> list[FoundDevice]:
    """Addresses on `local_ip`'s private /24 with an RTSP port open.

    Args:
        local_ip: This machine's address.
        ports: Ports to try.

    Returns:
        One device per address with any port open (this machine excluded).

    Raises:
        DiscoveryError: For a public address (only private networks are swept).
    """
    network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    if not network.is_private:
        msg = "only private networks are swept"
        raise DiscoveryError(msg)
    targets = [(str(h), p) for h in network.hosts() if str(h) != local_ip for p in ports]
    with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
        results = list(pool.map(lambda t: _open(*t), targets))
    open_ports: dict[str, list[int]] = {}
    for (host, port), is_open in zip(targets, results, strict=True):
        if is_open:
            open_ports.setdefault(host, []).append(port)
    return [FoundDevice(address=h, via="rtsp-port", rtsp_ports=p) for h, p in open_ports.items()]


def scan(timeout_s: float = 3.0, *, sweep: bool = True) -> ScanResult:
    """WS-Discovery plus (optionally) the RTSP port sweep, merged by address."""
    started = time.monotonic()
    notes: list[str] = []
    local_ip = local_ipv4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        onvif_job = pool.submit(ws_discover, timeout_s)
        sweep_job = pool.submit(rtsp_sweep, local_ip) if sweep and local_ip else None
        try:
            devices = onvif_job.result()
        except DiscoveryError as exc:
            devices, notes = [], [str(exc)]
        swept: list[FoundDevice] = []
        if sweep_job is not None:
            try:
                swept = sweep_job.result()
            except DiscoveryError as exc:
                notes.append(str(exc))
    by_address = {d.address: d for d in devices}
    for device in swept:
        known = by_address.get(device.address)
        if known is None:
            by_address[device.address] = device
        else:
            by_address[device.address] = known.model_copy(update={"rtsp_ports": device.rtsp_ports})
    if local_ip is None:
        notes.append("this machine has no local network address (a cloud server finds nothing)")
    subnet = str(ipaddress.ip_network(f"{local_ip}/24", strict=False)) if local_ip else None
    _log.info("connectors_scan", found=len(by_address), local_ip=local_ip)
    return ScanResult(
        devices=sorted(by_address.values(), key=lambda d: ipaddress.ip_address(d.address)),
        local_ip=local_ip,
        subnet=subnet,
        took_s=round(time.monotonic() - started, 2),
        notes=notes,
    )


# ---- ONVIF media ---------------------------------------------------------------------------


def security_header(username: str, password: str, *, nonce: bytes, created: str) -> str:
    """The WS-Security UsernameToken (password digest) header ONVIF devices expect."""
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()  # noqa: S324 - ONVIF spec
    ).decode()
    return (
        f'<Security s:mustUnderstand="1" xmlns="{WSSE}"><UsernameToken>'
        f"<Username>{escape(username)}</Username>"
        f'<Password Type="{DIGEST}">{digest}</Password>'
        f'<Nonce EncodingType="{B64}">{base64.b64encode(nonce).decode()}</Nonce>'
        f'<Created xmlns="{WSU}">{created}</Created>'
        "</UsernameToken></Security>"
    )


def envelope(body: str, username: str | None, password: str | None) -> str:
    """A SOAP 1.2 envelope, signed when a user name is given."""
    header = ""
    if username:
        created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        header = security_header(username, password or "", nonce=os.urandom(16), created=created)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{SOAP_ENV}">'
        f"<s:Header>{header}</s:Header><s:Body>{body}</s:Body></s:Envelope>"
    )


class OnvifClient:
    """The three ONVIF calls Rulecam needs, over plain HTTP SOAP."""

    def __init__(
        self,
        xaddr: str,
        username: str | None,
        password: str | None,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        """Create the client.

        Args:
            xaddr: The device service URL (`http://<ip>/onvif/device_service`).
            username: The camera account's user name, if it needs one.
            password: Its password.
            http: HTTP client (tests pass a mock transport).
        """
        self._xaddr = xaddr
        self._username = username
        self._password = password
        self._http = http or httpx.Client(timeout=ONVIF_TIMEOUT_S)

    def call(self, url: str, body: str) -> Element:
        """POST one SOAP request and return the parsed reply.

        Raises:
            DiscoveryError: On a network error, an HTTP error (401: wrong user or password) or
                a SOAP fault.
        """
        try:
            reply = self._http.post(
                url,
                content=envelope(body, self._username, self._password).encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
        except httpx.HTTPError as exc:
            msg = f"could not reach {urlsplit(url).netloc}: {type(exc).__name__}"
            raise DiscoveryError(msg) from exc
        root = _parse(reply.content) if reply.content else None
        fault = (
            _text(root, "Text") if root is not None and _first(root, "Fault") is not None else None
        )
        if reply.status_code in (401, 403) or (fault and "auth" in fault.lower()):
            msg = "the camera refused the user name or password"
            raise DiscoveryError(msg)
        if reply.status_code >= HTTP_ERROR or root is None or fault:
            msg = f"the camera answered {reply.status_code}: {fault or 'no reply body'}"
            raise DiscoveryError(msg)
        return root

    def device(self) -> OnvifDevice:
        """The device's maker, model and every profile's RTSP URI."""
        info = self._optional(f'<GetDeviceInformation xmlns="{DEVICE_NS}"/>')
        caps = self.call(
            self._xaddr,
            f'<GetCapabilities xmlns="{DEVICE_NS}"><Category>Media</Category></GetCapabilities>',
        )
        media = _first(caps, "Media")
        media_url = (_text(media, "XAddr") if media is not None else None) or self._xaddr
        profiles = []
        for profile in _all(self.call(media_url, f'<GetProfiles xmlns="{MEDIA_NS}"/>'), "Profiles"):
            profiles.append(self._profile(media_url, profile))
        return OnvifDevice(
            xaddr=self._xaddr,
            manufacturer=_text(info, "Manufacturer") if info is not None else None,
            model=_text(info, "Model") if info is not None else None,
            firmware=_text(info, "FirmwareVersion") if info is not None else None,
            profiles=profiles,
        )

    def _optional(self, body: str) -> Element | None:
        try:
            return self.call(self._xaddr, body)
        except DiscoveryError:
            return None

    def _profile(self, media_url: str, profile: Element) -> OnvifProfile:
        token = profile.get("token") or ""
        encoder = _first(profile, "VideoEncoderConfiguration")
        width = _text(encoder, "Width") if encoder is not None else None
        height = _text(encoder, "Height") if encoder is not None else None
        fps = _text(encoder, "FrameRateLimit") if encoder is not None else None
        base = OnvifProfile(
            token=token,
            name=_text(profile, "Name") or token,
            encoding=_text(encoder, "Encoding") if encoder is not None else None,
            width=int(width) if width and width.isdigit() else None,
            height=int(height) if height and height.isdigit() else None,
            fps=float(fps) if fps and fps.replace(".", "", 1).isdigit() else None,
        )
        body = (
            f'<GetStreamUri xmlns="{MEDIA_NS}"><StreamSetup>'
            f'<Stream xmlns="{SCHEMA_NS}">RTP-Unicast</Stream>'
            f'<Transport xmlns="{SCHEMA_NS}"><Protocol>RTSP</Protocol></Transport>'
            f"</StreamSetup><ProfileToken>{escape(token)}</ProfileToken></GetStreamUri>"
        )
        try:
            uri = _text(self.call(media_url, body), "Uri")
        except DiscoveryError as exc:
            return base.model_copy(update={"error": str(exc)})
        clean = split_credentials(uri)[0] if uri else None
        return base.model_copy(update={"rtsp_url": clean})


def device_service_url(target: str) -> str:
    """The ONVIF device service URL for an address, a host:port or a full URL."""
    text = target.strip()
    if text.startswith(("http://", "https://")):
        return text
    return f"http://{text}/onvif/device_service"


def onvif_streams(
    target: str,
    username: str | None,
    password: str | None,
    *,
    http: httpx.Client | None = None,
) -> OnvifDevice:
    """Ask an ONVIF camera or NVR for its streams (see `OnvifClient.device`)."""
    return OnvifClient(device_service_url(target), username, password, http=http).device()
