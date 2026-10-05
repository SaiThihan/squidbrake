"""
One pre-execution hook for the coding agents that have hooks, besides Claude Code (claude_hook.py):

  python agent_hook.py cursor       Cursor        beforeShellExecution, beforeReadFile   (~/.cursor/hooks.json)
  python agent_hook.py gemini-cli   Gemini CLI    BeforeTool                             (~/.gemini/settings.json)
  python agent_hook.py codex        Codex CLI     PreToolUse                             (~/.codex/hooks.json)
  python agent_hook.py vscode       VS Code       PreToolUse (Copilot agent mode)        (~/.copilot/hooks/)
  python agent_hook.py antigravity  Antigravity   PreToolUse                             (~/.gemini/config/hooks.json)

Each reads the agent's JSON on stdin, sends the action to Squidbrake, waits while a person decides if it's held, and
answers in that agent's format. Shell commands are sent as "Bash", reads as "Read", writes and edits as "Write" / "Edit",
so the same rules apply whichever agent ran them. MCP tools are left to `connect guard`, which routes the agent's MCP
servers through Squidbrake (so they aren't recorded twice).

Installed by `squidbrake connect agents`. Settings come as arguments: --url, --key (like claude_hook.py).
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import time

import httpx

try:  # what a command will change, and an undo for what it destroys (both best effort, never fatal)
    import effects
    import runs
    import undo
except Exception:  # pragma: no cover
    effects = runs = undo = None
try:
    import hooklog   # which agent ran the hook and when, for `squidbrake doctor`
except Exception:  # pragma: no cover
    hooklog = None

AGENTS = ("cursor", "gemini-cli", "codex", "vscode", "antigravity")


def _arg(flag: str, env: str, default: str = "") -> str:
    if flag in sys.argv[1:-1]:
        return sys.argv[sys.argv.index(flag) + 1]
    return os.getenv(env, default)


URL = _arg("--url", "GATEWAY_URL", "http://localhost:8080").rstrip("/")
KEY = _arg("--key", "GATEWAY_API_KEY")
FAIL_OPEN = os.getenv("GATEWAY_FAIL_OPEN", "").lower() in ("1", "true", "yes")
MAX_WAIT = float(os.getenv("GATEWAY_MAX_WAIT", "540"))   # under the 600 s timeout `connect agents` sets


# --------------------------------------------------------------------------- what the agent wants to do

def normalize(name: str, args) -> tuple[str, dict] | None:
    """(name, input) the way Squidbrake's rules see it, or None for actions left to other paths (MCP tools)."""
    args = args if isinstance(args, dict) else {"value": args}
    low = name.lower()
    if low.startswith("mcp"):
        return None
    lower = {k.lower(): v for k, v in args.items()}
    cmd = next((lower[k] for k in ("command", "commandline", "command_line", "cmd") if k in lower), None)
    if cmd is not None:
        if isinstance(cmd, list):         # e.g. Codex: ["bash", "-lc", "rm -rf ..."]; keep the quoting
            cmd = shlex.join(str(c) for c in cmd)
        out = {"command": str(cmd)}
        cwd = next((lower[k] for k in ("cwd", "workdir", "directory") if k in lower), None)
        if cwd:
            out["cwd"] = cwd
        return "Bash", out
    path = next((v for k, v in lower.items() if k in ("file_path", "filepath", "path", "absolute_path", "absolutepath",
                                                        "targetfile", "target_file", "file")), None)
    if any(w in low for w in ("read", "view", "open")) and path:
        return "Read", {"file_path": path}
    if any(w in low for w in ("edit", "replace", "patch", "str_replace", "multi_edit")):
        return "Edit", {**args, **({"file_path": path} if path else {})}
    if any(w in low for w in ("write", "create")):
        return "Write", {**args, **({"file_path": path} if path else {})}
    return name, args


def parse(agent: str, ev: dict) -> tuple[tuple[str, dict] | None, str | None]:
    """-> ((name, input) or None, session id)"""
    if agent == "cursor":
        session = ev.get("conversation_id")
        if ev.get("hook_event_name") == "beforeShellExecution":
            return ("Bash", {k: v for k, v in (("command", ev.get("command")), ("cwd", ev.get("cwd"))) if v}), session
        if ev.get("hook_event_name") == "beforeReadFile":
            return ("Read", {"file_path": ev.get("file_path")}), session
        return normalize(str(ev.get("tool_name", "unknown")), ev.get("tool_input")), session
    if agent == "antigravity":
        call = ev.get("toolCall") or {}
        return normalize(str(call.get("name", "unknown")), call.get("args")), ev.get("conversationId")
    return normalize(str(ev.get("tool_name", "unknown")), ev.get("tool_input")), ev.get("session_id")


# --------------------------------------------------------------------------- answers, in each agent's format

def answer(agent: str, allow: bool, message: str = "", stop: bool = False) -> None:
    if agent == "cursor":
        out = {"permission": "allow"} if allow else {"permission": "deny", "user_message": message, "agent_message": message}
    elif agent in ("gemini-cli", "antigravity"):
        out = {"decision": "allow"} if allow else {"decision": "deny", "reason": message}
        if stop and agent == "gemini-cli":
            out.update({"continue": False, "stopReason": message})
    elif agent == "codex":
        if allow:                     # Codex: no output means "carry on"
            sys.exit(0)
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": message}}
    else:                             # vscode
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow" if allow else "deny",
                                      **({} if allow else {"permissionDecisionReason": message})}}
        if stop:
            out.update({"continue": False, "stopReason": message})
    print(json.dumps(out))
    sys.exit(0)


def unreachable(url: str, e: httpx.HTTPError, what: str = "action") -> str:
    """Why everything is blocked, in words a person can act on (the agent passes it on). Same text in claude_hook.py."""
    status = getattr(getattr(e, "response", None), "status_code", None)
    if "rejected" in str(e):
        why = f"Squidbrake's dashboard at {url} rejected this computer's key (it may have been removed)"
    elif status in (502, 503, 504) or isinstance(e, (httpx.ConnectError, httpx.TimeoutException)):
        why = f"Squidbrake's dashboard at {url} isn't answering: it may be switched off, restarting, or deleted"
    else:
        why = f"Squidbrake at {url} is unreachable or misconfigured ({e})"
    return (f"{why}. Every {what} must go through it, so this was blocked. Tell the user: start it again (or ask "
            f"whoever runs it). If it was removed on purpose, take Squidbrake out of this computer's agents with: "
            f"squidbrake connect all --remove")


def check(agent: str, name: str, inp: dict, session: str | None) -> None:
    body = {"name": name, "kind": "agent_hook", "input": inp, "source": agent, "session_id": session}
    line = inp.get("command") if name == "Bash" and effects is not None else None
    cwd = inp.get("cwd") or os.getcwd()
    if line:
        try:
            if found := effects.predict(line, cwd):
                body.setdefault("metadata", {})["effects"] = found
        except Exception:
            pass
        try:  # what `make clean` / `npm run x` / `bash x.sh` runs underneath, so the gateway checks that too
            if found := runs.expand(line, cwd):
                body.setdefault("metadata", {})["runs"] = found
        except Exception:
            pass
    try:
        with httpx.Client(base_url=URL, headers={"X-Gateway-Key": KEY}, timeout=15) as http:
            r = http.post("/v1/events", json=body)
            if r.status_code == 401:
                raise httpx.HTTPError("the gateway rejected the key")
            r.raise_for_status()
            d = r.json()
            end = time.monotonic() + MAX_WAIT
            while d["decision"] == "review" and time.monotonic() < end:
                wait = min(25.0, end - time.monotonic())
                r = http.get(f"/v1/events/{d['event_id']}/decision", params={"wait": wait}, timeout=wait + 10)
                r.raise_for_status()
                d = r.json()
            if d["decision"] == "allow":
                # these agents don't report results back: mark the call done so it isn't left "pending"
                http.post(f"/v1/events/{d['event_id']}/result", json={"output": {"result": f"not reported by {agent}"}})
    except httpx.HTTPError as e:
        if FAIL_OPEN:
            answer(agent, True)
        answer(agent, False, unreachable(URL, e))
    if d["decision"] == "allow":
        if line and (kept := undo.snapshot(line, cwd)):
            print(undo.describe(kept), file=sys.stderr)
        answer(agent, True)
    if d["decision"] == "review":
        answer(agent, False, "Squidbrake: nobody approved this in time. Ask the user to approve it in the dashboard, then try again.")
    by, note = d.get("decided_by"), d.get("decision_note")
    if d.get("rule_id") in ("emergency-stop", "session-stop") or (note or "").startswith("The session was stopped"):
        answer(agent, False, f"Squidbrake: {note or d.get('reason')}. Stop working and tell the user.", stop=True)
    if by and by != "timeout":
        answer(agent, False, f"Squidbrake: rejected by {by}." + (f' Note: "{note}".' if note else "")
               + " Don't retry it; ask the user how to proceed.")
    answer(agent, False, f"Squidbrake blocked this (rule '{d.get('rule_id')}'): {(d.get('reason') or '').rstrip('.')}. "
                         "Don't try to work around it.")


def main() -> None:
    agent = sys.argv[1] if len(sys.argv) > 1 else ""
    if agent not in AGENTS:
        sys.exit(f"usage: agent_hook.py {{{'|'.join(AGENTS)}}} --url URL --key KEY")
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        answer(agent, True)
    if hooklog is not None:
        hooklog.record(agent, str(ev.get("hook_event_name") or (ev.get("toolCall") or {}).get("name") or ev.get("tool_name") or ""))
    action, session = parse(agent, ev)
    if action is None:
        answer(agent, True)
    check(agent, action[0], action[1], session)


if __name__ == "__main__":
    main()
