"""Version-based strategy selector for QR credential verifiers."""

from __future__ import annotations

import base64
import json
from typing import Iterable, Mapping, Optional

from ..ports.qr_verifier import IQrVerifier
from .device_bound_qr_verifier import DeviceBoundQrVerifier


class PortalTokenVerifier(IQrVerifier):
    """Stub verifier for future portal/WEB_TOKEN credential versions."""

    @property
    def version(self) -> int:
        return 2

    def decode(self, raw: bytes) -> None:
        return None

    def verify(self, payload, snapshot, now):
        from ..ports.dto import ReasonCode, VerifyOutcome

        return VerifyOutcome(ok=False, reason_code=ReasonCode.MALFORMED)


class QrVerifierSelector:
    """Select a verifier from the version field in a Base64 JSON QR."""

    def __init__(
        self,
        verifiers: Optional[
            Mapping[int, IQrVerifier] | Iterable[IQrVerifier]
        ] = None,
    ) -> None:
        if verifiers is None:
            mapping: dict[int, IQrVerifier] = {
                1: DeviceBoundQrVerifier(),
                2: PortalTokenVerifier(),
            }
        elif isinstance(verifiers, Mapping):
            mapping = dict(verifiers)
        else:
            mapping = {verifier.version: verifier for verifier in verifiers}
        self._verifiers = mapping
        self.portal_token_verifier = mapping.get(2, PortalTokenVerifier())

    def select(self, raw: bytes) -> IQrVerifier:
        """Return the strategy for v; unsupported versions use the portal stub."""
        try:
            if not isinstance(raw, (bytes, bytearray)):
                return DeviceBoundQrVerifier()
            decoded = base64.b64decode(bytes(raw), validate=True)
            data = json.loads(decoded.decode("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("v"), int):
                version = data["v"]
                if version in self._verifiers:
                    return self._verifiers[version]
        except (base64.binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            pass
        return self.portal_token_verifier

    def select_for_version(self, version: int) -> IQrVerifier:
        """Select a registered verifier directly by version."""
        return self._verifiers.get(version, self.portal_token_verifier)

    def __call__(self, raw: bytes) -> IQrVerifier:
        return self.select(raw)
