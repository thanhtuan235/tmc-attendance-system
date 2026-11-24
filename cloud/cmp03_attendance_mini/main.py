"""CMP-03 mini API for receiving final attendance events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool, StrictInt, StrictStr

from packages.contracts import AttendanceEvent
from packages.contracts.attendance_event import SCHEMA_VERSION


class _IncomingEvent(BaseModel):
    event_id: StrictStr
    station_id: StrictStr
    station_seq: StrictInt
    gate_id: StrictStr
    did: StrictStr | None
    seq: StrictInt | None
    direction: Literal["IN", "OUT"]
    status: Literal["OPENED", "UNCONFIRMED", "FAILED"]
    credential_type: Literal["DEVICE_BOUND", "WEB_TOKEN"]
    snapshot_tier: Literal["FRESH", "STALE", "EXPIRED"]
    needs_review: StrictBool
    occurred_at: StrictStr
    ack_at: StrictStr | None = None
    reason_code: StrictStr | None = None
    schema_version: StrictInt = SCHEMA_VERSION


class _EventBatch(BaseModel):
    events: list[_IncomingEvent]


def _validate_event(item: _IncomingEvent) -> AttendanceEvent:
    event = AttendanceEvent.from_dict(dict(item))
    if not all((event.event_id.strip(), event.station_id.strip(), event.gate_id.strip())):
        raise ValueError("event_id, station_id and gate_id must be non-empty")
    if event.schema_version != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    if event.station_seq < 0 or (
        event.seq is not None and not 0 <= event.seq < 2**63
    ):
        raise ValueError("invalid station_seq or seq")
    for timestamp in (event.occurred_at, event.ack_at):
        if timestamp is not None and datetime.fromisoformat(timestamp).utcoffset() != timedelta(0):
            raise ValueError("timestamps must be ISO 8601 UTC")
    return event


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": message})


def create_app(db_path: str | Path, *, batch_size: int = 100) -> FastAPI:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with closing(sqlite3.connect(db_path)) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS attendance_events (
                    event_id TEXT NOT NULL PRIMARY KEY,
                    did TEXT,
                    seq INTEGER,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_attendance_events_did_seq
                    ON attendance_events (did, seq);
                """
            )
        yield

    app = FastAPI(title="CMP-03 mini", lifespan=lifespan)

    # FastAPI may report body parsing errors as either validation errors or HTTP 400.
    @app.exception_handler(RequestValidationError)
    @app.exception_handler(400)
    async def invalid_request(_request: Request, _exc: Exception) -> JSONResponse:
        return _bad_request("invalid event batch")

    @app.post("/events")
    def receive_events(batch: _EventBatch):
        if len(batch.events) > batch_size:
            return _bad_request(f"batch exceeds {batch_size} events")
        try:
            events = [_validate_event(item) for item in batch.events]
        except ValueError as exc:
            return _bad_request(str(exc))

        with closing(sqlite3.connect(db_path)) as db, db:
            accepted = db.executemany(
                """
                INSERT INTO attendance_events (event_id, did, seq, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                [
                    (event.event_id, event.did, event.seq, json.dumps(event.to_dict()))
                    for event in events
                ],
            ).rowcount
        return {"accepted": accepted, "duplicates": len(events) - accepted}

    @app.get("/events/count")
    def count_events() -> dict[str, int]:
        with closing(sqlite3.connect(db_path)) as db:
            count, = db.execute("SELECT COUNT(*) FROM attendance_events").fetchone()
        return {"count": count}

    return app


app = create_app("cmp03_events.db")
