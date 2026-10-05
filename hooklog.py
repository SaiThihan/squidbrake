"""
A one-line note each time an agent runs Squidbrake's hook, so `squidbrake doctor` can tell "connected, but the agent
never called it" from "connected and working". Only which agent, which hook event and when: never the command, the
file, the input or the key. Kept in ~/.squidbrake/hooks.log (SQUIDBRAKE_HOME moves it), about 250 KB at most.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 250_000


def path() -> Path:
    return Path(os.getenv("SQUIDBRAKE_HOME") or Path.home() / ".squidbrake") / "hooks.log"


def record(agent: str, event: str) -> None:
    """Never raises: a hook must not fail because of its own bookkeeping."""
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > MAX_BYTES:
            p.replace(p.with_name("hooks.log.1"))
        line = {"t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "agent": agent, "event": event[:60]}
        if os.getenv("SQUIDBRAKE_DOCTOR"):
            line["doctor"] = True
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except Exception:
        pass


def last_call(agent: str) -> datetime | None:
    """When the agent itself (not a doctor test) last ran the hook."""
    found = None
    for p in (path().with_name("hooks.log.1"), path()):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if d.get("agent") == agent and not d.get("doctor"):
                found = datetime.fromisoformat(d["t"])
    return found
