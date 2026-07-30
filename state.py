"""Persisted snapshot of what we have already told the user about.

Lives on a mounted volume, not inside the image: if this file is lost, every
currently-active fire looks brand new and the user gets a burst of stale alerts.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field

from fogos import Fire

logger = logging.getLogger(f"fogosptalerts.{__name__}")

SCHEMA_VERSION = 2


@dataclass
class State:
    fires: dict[str, Fire] = field(default_factory=dict)
    first_seen: dict[str, int] = field(default_factory=dict)
    threads: dict[str, str] = field(default_factory=dict)
    last_heartbeat: int = 0
    initialized: bool = False

    def thread_id(self, fire_id: str, domain: str) -> str:
        """Stable Message-ID for a fire, so mail clients group its updates."""
        return self.threads.setdefault(fire_id, f"<fogo-{fire_id}@{domain}>")

    def prune(self, live_ids: set[str]) -> None:
        for fire_id in set(self.first_seen) - live_ids:
            self.first_seen.pop(fire_id, None)
            self.threads.pop(fire_id, None)


def load(path: str) -> State:
    if not os.path.exists(path):
        logger.info("No state file at %s — first run", path)
        return State()

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("State file at %s is unreadable (%s) — starting fresh", path, exc)
        return State()

    if raw.get("version") != SCHEMA_VERSION:
        logger.warning("State schema %s is not v%d — starting fresh", raw.get("version"), SCHEMA_VERSION)
        return State()

    fires: dict[str, Fire] = {}
    for fire_id, payload in (raw.get("fires") or {}).items():
        try:
            fires[fire_id] = Fire.from_dict(payload)
        except TypeError as exc:
            logger.warning("Dropping unreadable state entry %s: %s", fire_id, exc)

    logger.info("Loaded %d tracked fire(s) from %s", len(fires), path)
    return State(
        fires=fires,
        first_seen={k: int(v) for k, v in (raw.get("first_seen") or {}).items()},
        threads=dict(raw.get("threads") or {}),
        last_heartbeat=int(raw.get("last_heartbeat") or 0),
        initialized=bool(raw.get("initialized")),
    )


def save(path: str, state: State) -> None:
    """Write atomically — a half-written state file is worse than none."""
    payload = {
        "version": SCHEMA_VERSION,
        "initialized": state.initialized,
        "updated_at": int(time.time()),
        "last_heartbeat": state.last_heartbeat,
        "first_seen": state.first_seen,
        "threads": state.threads,
        "fires": {fire_id: fire.to_dict() for fire_id, fire in state.fires.items()},
    }

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=".state-", suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except OSError:
        os.unlink(handle.name)
        raise
