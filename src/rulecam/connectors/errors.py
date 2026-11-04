"""Errors of the camera connectors."""

from __future__ import annotations

from rulecam.errors import RulecamError


class ConnectorError(RulecamError):
    """A camera could not be added, found or reached."""


class SourceError(ConnectorError):
    """A camera URL or clip is not usable (bad scheme, missing file, no stream found)."""


class UnknownCameraError(ConnectorError):
    """No connected camera has that id."""


class DiscoveryError(ConnectorError):
    """A network scan or an ONVIF request failed."""
