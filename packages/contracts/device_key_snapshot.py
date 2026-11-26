"""Canonical JSON signed by the demo snapshot publisher and verified at Edge."""

import json


def canonical_snapshot(payload: dict) -> bytes:
    """Encode every field except the top-level signature as canonical UTF-8 JSON."""
    unsigned = {name: value for name, value in payload.items() if name != "sig"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
