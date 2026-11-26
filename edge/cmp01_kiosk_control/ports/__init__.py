"""Hợp đồng chung của CMP-01 (T1-02, tag v0.1-interfaces).

Đổi bất kỳ file nào trong package này = đổi hợp đồng của cả nhóm:
phải mở PR riêng và T1 duyệt.
"""

from .clock import IClock
from .cloud_client import ICloudEventClient
from .dto import (
    CredentialType,
    DeviceEntry,
    DeviceKeySnapshot,
    Direction,
    FreshnessDecision,
    GateAck,
    OutboxRecord,
    QrPayload,
    ReasonCode,
    ScanRequest,
    ScanResult,
    ScanStatus,
    SnapshotTier,
    TxStatus,
    VerifyOutcome,
)
from .gate_scan import IGateScan
from .outbox import IOutboxRepository
from .qr_verifier import IQrVerifier
from .replay_guard import IReplayGuard
from .snapshot import ISnapshotFreshnessPolicy, ISnapshotStore
from .turnstile import ITurnstileGateway, TurnstileGatewayFactory

__all__ = [
    "IClock", "ICloudEventClient", "IGateScan", "IOutboxRepository", "IQrVerifier",
    "IReplayGuard", "ISnapshotFreshnessPolicy", "ISnapshotStore", "ITurnstileGateway",
    "TurnstileGatewayFactory",
    "CredentialType", "DeviceEntry", "DeviceKeySnapshot", "Direction", "FreshnessDecision",
    "GateAck", "OutboxRecord", "QrPayload", "ReasonCode", "ScanRequest", "ScanResult",
    "ScanStatus", "SnapshotTier", "TxStatus", "VerifyOutcome",
]