"""ECDSA P-256 verifier for DEVICE_BOUND QR credentials."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ..ports.clock import IClock
from ..ports.dto import (
    DeviceKeySnapshot,
    QrPayload,
    ReasonCode,
    VerifyOutcome,
)
from ..ports.qr_verifier import IQrVerifier


class DeviceBoundQrVerifier(IQrVerifier):
    """Decode and verify version 1 device-bound QR payloads."""

    def __init__(
        self,
        clock: Optional[IClock] = None,
        *,
        window_past_sec: int = 30,
        window_future_sec: int = 5,
    ) -> None:
        if window_past_sec < 0 or window_future_sec < 0:
            raise ValueError("QR verification windows must be non-negative")
        self._clock = clock
        self._window_past_sec = window_past_sec
        self._window_future_sec = window_future_sec

    @property
    def version(self) -> int:
        return 1

    def decode(self, raw: bytes) -> Optional[QrPayload]:
        """Decode strict Base64 JSON and validate the QR payload fields."""
        if not isinstance(raw, (bytes, bytearray)):
            return None
        try:
            decoded = base64.b64decode(bytes(raw), validate=True)
            data = json.loads(decoded.decode("utf-8"))
        except (
            binascii.Error,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return None

        if not isinstance(data, dict):
            return None
        required = {"v", "did", "iat", "seq", "sig"}
        if not required.issubset(data):
            return None

        v = data["v"]
        did = data["did"]
        iat = data["iat"]
        seq = data["seq"]
        sig = data["sig"]
        if (
            not isinstance(v, int)
            or isinstance(v, bool)
            or v != 1
            or not isinstance(did, str)
            or not did
            or not isinstance(iat, int)
            or isinstance(iat, bool)
            or iat < 0
            or not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq < 0
            or not isinstance(sig, str)
            or not sig
        ):
            return None

        try:
            signature = base64.b64decode(sig, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not signature:
            return None

        return QrPayload(v=v, did=did, iat=iat, seq=seq, sig=signature)

    def verify(
        self,
        payload: QrPayload,
        snapshot: Optional[DeviceKeySnapshot],
        now: Optional[datetime] = None,
    ) -> VerifyOutcome:
        """Apply timestamp, device, revocation, and signature checks in order."""
        if not isinstance(payload, QrPayload):
            return VerifyOutcome(ok=False, reason_code=ReasonCode.MALFORMED)
        if (
            payload.v != self.version
            or not isinstance(payload.did, str)
            or not payload.did
            or not isinstance(payload.iat, int)
            or isinstance(payload.iat, bool)
            or payload.iat < 0
            or not isinstance(payload.seq, int)
            or isinstance(payload.seq, bool)
            or payload.seq < 0
            or not isinstance(payload.sig, bytes)
            or not payload.sig
        ):
            return VerifyOutcome(ok=False, reason_code=ReasonCode.MALFORMED)

        current = now
        if current is None:
            current = self._clock.now() if self._clock is not None else datetime.now(timezone.utc)
        if not isinstance(current, datetime):
            return VerifyOutcome(ok=False, reason_code=ReasonCode.MALFORMED)

        now_epoch = self._datetime_to_epoch(current)
        if payload.iat < now_epoch - self._window_past_sec:
            return VerifyOutcome(ok=False, reason_code=ReasonCode.QR_EXPIRED)
        if payload.iat > now_epoch + self._window_future_sec:
            return VerifyOutcome(ok=False, reason_code=ReasonCode.FUTURE)

        if snapshot is None:
            return VerifyOutcome(ok=False, reason_code=ReasonCode.UNKNOWN_DEVICE)

        device = snapshot.find(payload.did)
        if device is None:
            return VerifyOutcome(ok=False, reason_code=ReasonCode.UNKNOWN_DEVICE)
        if (
            device.status != "ACTIVE"
            or device.employee_ref in snapshot.revoked_employees
        ):
            return VerifyOutcome(
                ok=False,
                reason_code=ReasonCode.DEVICE_REVOKED,
                device=device,
            )

        try:
            public_key = serialization.load_pem_public_key(
                device.public_key_pem.encode("utf-8")
            )
        except (ValueError, TypeError):
            return VerifyOutcome(
                ok=False,
                reason_code=ReasonCode.BAD_SIGNATURE,
                device=device,
            )
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            return VerifyOutcome(
                ok=False,
                reason_code=ReasonCode.BAD_SIGNATURE,
                device=device,
            )
        if not isinstance(public_key.curve, ec.SECP256R1):
            return VerifyOutcome(
                ok=False,
                reason_code=ReasonCode.BAD_SIGNATURE,
                device=device,
            )

        try:
            public_key.verify(
                payload.sig,
                payload.canonical(),
                ec.ECDSA(hashes.SHA256()),
            )
        except (InvalidSignature, ValueError, TypeError):
            return VerifyOutcome(
                ok=False,
                reason_code=ReasonCode.BAD_SIGNATURE,
                device=device,
            )

        return VerifyOutcome(ok=True, device=device)

    @staticmethod
    def _datetime_to_epoch(value: datetime) -> int:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return int(value.timestamp())
