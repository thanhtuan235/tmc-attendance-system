"""Durable storage for the last accepted CMP-02 snapshot."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from ..ports import DeviceEntry, DeviceKeySnapshot, ISnapshotStore
from ..snapshot_acceptance_service import SnapshotAcceptanceService


class SnapshotStore(ISnapshotStore):
    def __init__(
        self,
        db_path: str | Path = "edge_cache.db",
        pinned_root_pub_path: str | Path = "runtime-files/pinned_root_pub.pem",
    ) -> None:
        self._db_path = Path(db_path)
        acceptance = SnapshotAcceptanceService(pinned_root_pub_path)
        self._verify = lru_cache(maxsize=1)(acceptance.verify)
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS device_key_snapshot (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    version INTEGER NOT NULL,
                    raw BLOB NOT NULL
                )
            """)

    def current(self) -> DeviceKeySnapshot | None:
        with closing(sqlite3.connect(self._db_path)) as db:
            row = db.execute(
                "SELECT version, raw FROM device_key_snapshot WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return None
        snapshot = self._verify(row[1])
        if snapshot is None or snapshot.version != row[0]:
            return None
        return snapshot

    def load(self, raw: bytes) -> bool:
        snapshot = self._verify(raw)
        if snapshot is None:
            return False
        with closing(sqlite3.connect(self._db_path)) as db, db:
            result = db.execute(
                """
                INSERT INTO device_key_snapshot (singleton, version, raw) VALUES (1, ?, ?)
                ON CONFLICT (singleton) DO UPDATE SET version = excluded.version, raw = excluded.raw
                WHERE excluded.version > device_key_snapshot.version
                """,
                (snapshot.version, raw),
            )
            return result.rowcount == 1

    def lookup(self, did: str) -> DeviceEntry | None:
        snapshot = self.current()
        return snapshot.find(did) if snapshot is not None else None

    def is_revoked(self, did: str) -> bool:
        snapshot = self.current()
        if snapshot is None:
            return False
        device = snapshot.find(did)
        return device is not None and (
            device.status == "REVOKED" or device.employee_ref in snapshot.revoked_employees
        )
