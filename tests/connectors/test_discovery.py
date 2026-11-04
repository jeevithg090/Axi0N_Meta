"""ONVIF discovery and media calls against canned device replies (no network)."""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest

from rulecam.connectors import discovery
from rulecam.connectors.discovery import (
    FoundDevice,
    device_service_url,
    onvif_streams,
    parse_probe_matches,
    probe_message,
    scan,
    security_header,
)
from rulecam.connectors.errors import DiscoveryError

PROBE_MATCHES = b"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
<SOAP-ENV:Body><d:ProbeMatches><d:ProbeMatch>
<wsa:EndpointReference><wsa:Address>urn:uuid:cam-1234</wsa:Address></wsa:EndpointReference>
<d:Types>dn:NetworkVideoTransmitter</d:Types>
<d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/IPC_Front%20Door
 onvif://www.onvif.org/hardware/DS-2CD2143G2 onvif://www.onvif.org/location/city/shop</d:Scopes>
<d:XAddrs>http://192.168.1.64/onvif/device_service http://[fe80::1]/onvif/device_service</d:XAddrs>
</d:ProbeMatch></d:ProbeMatches></SOAP-ENV:Body></SOAP-ENV:Envelope>"""

DEVICE_INFO = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><s:Body><tds:GetDeviceInformationResponse>
<tds:Manufacturer>HIKVISION</tds:Manufacturer><tds:Model>DS-2CD2143G2</tds:Model>
<tds:FirmwareVersion>V5.7.3</tds:FirmwareVersion></tds:GetDeviceInformationResponse>
</s:Body></s:Envelope>"""

CAPABILITIES = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><GetCapabilitiesResponse>
<Capabilities><tt:Media><tt:XAddr>http://192.168.1.64/onvif/Media</tt:XAddr></tt:Media>
</Capabilities></GetCapabilitiesResponse></s:Body></s:Envelope>"""

PROFILES = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<s:Body><trt:GetProfilesResponse>
<trt:Profiles token="Profile_1" fixed="true"><tt:Name>mainStream</tt:Name>
<tt:VideoEncoderConfiguration token="VEC1"><tt:Encoding>H265</tt:Encoding>
<tt:Resolution><tt:Width>2560</tt:Width><tt:Height>1440</tt:Height></tt:Resolution>
<tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit></tt:RateControl>
</tt:VideoEncoderConfiguration></trt:Profiles>
<trt:Profiles token="Profile_2"><tt:Name>subStream</tt:Name></trt:Profiles>
</trt:GetProfilesResponse></s:Body></s:Envelope>"""


def stream_uri(token: str) -> str:
    return f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body><GetStreamUriResponse><MediaUri>
<tt:Uri>rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/{token[-1]}01</tt:Uri>
</MediaUri></GetStreamUriResponse></s:Body></s:Envelope>"""


FAULT = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><s:Fault>
<s:Reason><s:Text xml:lang="en">Sender not Authorized</s:Text></s:Reason></s:Fault>
</s:Body></s:Envelope>"""


def onvif_transport(requests: list[str], *, auth_ok: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        requests.append(body)
        if not auth_ok:
            return httpx.Response(400, text=FAULT)
        if "GetDeviceInformation" in body:
            return httpx.Response(200, text=DEVICE_INFO)
        if "GetCapabilities" in body:
            return httpx.Response(200, text=CAPABILITIES)
        if "GetProfiles" in body:
            assert str(request.url) == "http://192.168.1.64/onvif/Media"
            return httpx.Response(200, text=PROFILES)
        if "GetStreamUri" in body:
            token = body.split("<ProfileToken>")[1].split("<")[0]
            return httpx.Response(200, text=stream_uri(token))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_probe_message_is_an_onvif_probe() -> None:
    message = probe_message("abc").decode()
    assert "uuid:abc" in message
    assert "dn:NetworkVideoTransmitter" in message


def test_parse_probe_matches() -> None:
    [device] = parse_probe_matches(PROBE_MATCHES, "192.168.1.64")
    assert device.address == "192.168.1.64"
    assert device.via == "onvif"
    assert device.xaddr == "http://192.168.1.64/onvif/device_service"
    assert device.name == "IPC Front Door"
    assert device.hardware == "DS-2CD2143G2"
    assert device.endpoint == "urn:uuid:cam-1234"
    assert parse_probe_matches(b"not xml", "1.2.3.4") == []


def test_security_header_digest() -> None:
    nonce = b"0123456789abcdef"
    created = "2026-11-04T00:00:00.000Z"
    header = security_header("ad<min", "pw", nonce=nonce, created=created)
    expected = base64.b64encode(hashlib.sha1(nonce + created.encode() + b"pw").digest()).decode()  # noqa: S324
    assert expected in header
    assert "ad&lt;min" in header
    assert "pw<" not in header


def test_onvif_streams_lists_profiles_without_credentials() -> None:
    requests: list[str] = []
    http = httpx.Client(transport=onvif_transport(requests))
    device = onvif_streams("192.168.1.64", "admin", "pw", http=http)
    assert device.manufacturer == "HIKVISION"
    assert device.xaddr == "http://192.168.1.64/onvif/device_service"
    main, sub = device.profiles
    assert (main.name, main.encoding, main.width, main.height, main.fps) == (
        "mainStream",
        "H265",
        2560,
        1440,
        25.0,
    )
    assert main.rtsp_url == "rtsp://192.168.1.64:554/Streaming/Channels/101"
    assert sub.rtsp_url == "rtsp://192.168.1.64:554/Streaming/Channels/201"
    assert all("UsernameToken" in body for body in requests)
    assert all(">pw<" not in body for body in requests)


def test_onvif_refused_credentials() -> None:
    http = httpx.Client(transport=onvif_transport([], auth_ok=False))
    with pytest.raises(DiscoveryError, match="user name or password"):
        onvif_streams("http://192.168.1.64/onvif/device_service", "admin", "bad", http=http)


def test_onvif_unreachable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.Client(transport=httpx.MockTransport(fail))
    with pytest.raises(DiscoveryError, match="could not reach"):
        onvif_streams("10.0.0.9", None, None, http=http)


def test_device_service_url() -> None:
    assert device_service_url("10.0.0.9") == "http://10.0.0.9/onvif/device_service"
    assert device_service_url("10.0.0.9:8080") == "http://10.0.0.9:8080/onvif/device_service"
    assert device_service_url("https://x/onvif/d") == "https://x/onvif/d"


def test_rtsp_sweep_refuses_public_networks() -> None:
    with pytest.raises(DiscoveryError):
        discovery.rtsp_sweep("8.8.8.8")


def test_scan_merges_onvif_and_port_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    onvif = FoundDevice(address="192.168.1.64", via="onvif", xaddr="http://192.168.1.64/x")
    monkeypatch.setattr(discovery, "local_ipv4", lambda: "192.168.1.10")
    monkeypatch.setattr(discovery, "ws_discover", lambda timeout_s: [onvif])
    monkeypatch.setattr(
        discovery,
        "rtsp_sweep",
        lambda local_ip: [
            FoundDevice(address="192.168.1.64", via="rtsp-port", rtsp_ports=[554]),
            FoundDevice(address="192.168.1.7", via="rtsp-port", rtsp_ports=[8554]),
        ],
    )
    result = scan(0.5)
    assert result.subnet == "192.168.1.0/24"
    assert [(d.address, d.via, d.rtsp_ports) for d in result.devices] == [
        ("192.168.1.7", "rtsp-port", [8554]),
        ("192.168.1.64", "onvif", [554]),
    ]


def test_scan_without_a_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_probe(timeout_s: float) -> list[FoundDevice]:
        msg = "could not send the ONVIF probe"
        raise DiscoveryError(msg)

    monkeypatch.setattr(discovery, "local_ipv4", lambda: None)
    monkeypatch.setattr(discovery, "ws_discover", no_probe)
    result = scan(0.5)
    assert result.devices == []
    assert result.subnet is None
    assert len(result.notes) == 2
