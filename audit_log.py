"""
Tamper-evident audit log.

Every cross-check appends one record to an append-only, hash-chained log: each
entry stores the hash of the previous entry, so any later edit, deletion or
re-ordering breaks the chain and is detectable by verify_chain(). This gives a
defensible, forensic record of what ShipShield decided and on what basis.

Privacy: the record is deliberately PII-light. It stores the verdict, the per-layer
status, the failed indicator codes and the data sources used — NOT email bodies,
NOT raw IBANs, NOT account-holder names. (Tamper-*evidence* detects edits; for
true immutability, point LOG_PATH at WORM/append-only storage in production.)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).parent / "audit_log.jsonl"
GENESIS = "0" * 64


def _canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def _last_hash() -> str:
    if not LOG_PATH.exists():
        return GENESIS
    last = None
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line.strip()
    return json.loads(last)["entry_hash"] if last else GENESIS


def append(record: dict) -> dict:
    """Append a record; returns the stored entry (with prev_hash + entry_hash)."""
    prev = _last_hash()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prev_hash": prev,
        "record": record,
    }
    entry["entry_hash"] = _hash(prev, _canonical(entry))
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def verify_chain() -> dict:
    """Walk the log and confirm the hash chain is intact.
    Returns {valid, entries, broken_at} (broken_at is the 1-based line of the first break)."""
    if not LOG_PATH.exists():
        return {"valid": True, "entries": 0, "broken_at": None}
    prev = GENESIS
    n = 0
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n += 1
            e = json.loads(line)
            stored = e.pop("entry_hash", None)
            if e.get("prev_hash") != prev:
                return {"valid": False, "entries": n, "broken_at": n}
            if _hash(prev, _canonical(e)) != stored:
                return {"valid": False, "entries": n, "broken_at": n}
            prev = stored
    return {"valid": True, "entries": n, "broken_at": None}


def tail(limit: int = 50) -> list:
    """Return the most recent entries (newest last)."""
    if not LOG_PATH.exists():
        return []
    rows = [json.loads(l) for l in LOG_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[-limit:]
