"""squidbrake doctor: one command that says what works and what to do, with no screenshots or log digging."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import connect  # noqa: E402
import hooklog  # noqa: E402

URL, KEY = "https://co.app.example.test", "gw_doctor_key"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("SQUIDBRAKE_HOME", str(tmp_path / ".squidbrake"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(connect.shutil, "which", lambda name: None)
    monkeypatch.setattr(connect, "_cursor_version", lambda: "2.4.1")
    monkeypatch.setattr(connect, "_cursor_hook_errors", lambda: [])
    monkeypatch.setattr(connect, "_user_dir", lambda: tmp_path / "AppData")     # no real VS Code

    class R:
        def __init__(self, code, data=None): self.status_code, self._d = code, data or {}
        def json(self): return self._d
    calls = {"key_ok": True}

    def get(url, headers=None, timeout=None):
        if "pypi.org" in url:
            return R(200, {"info": {"version": "0.0.1"}})
        if url.endswith("/v1/me"):
            return R(200 if calls["key_ok"] else 401)
        return R(200)
    import httpx
    monkeypatch.setattr(httpx, "get", get)
    return tmp_path, calls


def run(*argv):
    try:
        connect.main(["doctor", *argv])
    except SystemExit as e:
        return e.code
    return 0


def connect_cursor(tmp_path):
    (tmp_path / ".cursor").mkdir(exist_ok=True)
    connect.main(["agents", "--agent", "cursor", "--url", URL, "--key", KEY, "--yes"])


def test_hooklog_records_calls_but_not_doctor_tests(home, monkeypatch):
    hooklog.record("cursor", "beforeShellExecution")
    monkeypatch.setenv("SQUIDBRAKE_DOCTOR", "1")
    hooklog.record("codex", "PreToolUse")
    assert hooklog.last_call("cursor") is not None and hooklog.last_call("codex") is None
    text = hooklog.path().read_text(encoding="utf-8")
    assert "beforeShellExecution" in text and KEY not in text


def test_not_connected_says_how_to_connect(home, capsys):
    tmp, _ = home
    (tmp / ".cursor").mkdir()
    assert run() == 1
    out = capsys.readouterr().out
    assert "[X] cursor: not connected" in out and "install command" in out


def test_connected_but_never_used_tells_you_to_restart(home, capsys, monkeypatch):
    tmp, _ = home
    connect_cursor(tmp)
    monkeypatch.setattr(connect, "_run_hook", lambda cmd, ev: (True, ""))
    assert run() == 0
    out = capsys.readouterr().out
    assert "[OK] Dashboard answers: " + URL in out and "[OK] The agents' key works" in out
    assert "[!] cursor: connected and the hook works, but cursor hasn't run it" in out
    assert "Cmd+Q" in out and "(Cursor 2.4.1)" in out and "squidbrake doctor" in out
    assert run("--quick") == 0 and "[OK] cursor: connected; the hook works" in capsys.readouterr().out


def test_working_agent_is_ok(home, capsys, monkeypatch):
    tmp, _ = home
    connect_cursor(tmp)
    monkeypatch.setattr(connect, "_run_hook", lambda cmd, ev: (True, ""))
    time.sleep(0.01)
    hooklog.record("cursor", "beforeShellExecution")          # Cursor ran the hook after it was connected
    assert run() == 0
    out = capsys.readouterr().out
    assert "[OK] cursor: connected, and it used the hook just now" in out and "Everything works." in out


def test_rejected_key_and_a_denying_hook_are_explained(home, capsys, monkeypatch):
    tmp, calls = home
    connect_cursor(tmp)
    calls["key_ok"] = False
    monkeypatch.setattr(connect, "_run_hook", lambda cmd, ev: (False, "the gateway rejected the key"))
    assert run() == 1
    out = capsys.readouterr().out
    assert "[X] The agents' key is rejected" in out and "AGENT key" in out
    assert "[X] cursor: the hook runs but answered: the gateway rejected the key" in out
    assert KEY not in out


def test_run_hook_reads_each_agents_answer(tmp_path):
    py = sys.executable.replace("\\", "/")
    deny = tmp_path / "deny.py"
    deny.write_text('import sys,json; sys.stdin.read(); print(json.dumps({"permission": "deny", "user_message": "nope"}))')
    allow = tmp_path / "allow.py"
    allow.write_text('import sys,json; sys.stdin.read(); print(json.dumps({"permission": "allow"}))')
    assert connect._run_hook(f'"{py}" "{deny}"', {}) == (False, "nope")
    assert connect._run_hook(f'"{py}" "{allow}"', {}) == (True, "")
    assert connect._hook_commands({"hooks": {"x": [{"hooks": [{"command": "/p/python /a/agent_hook.py codex --url u"}]}]}}) == \
        ["/p/python /a/agent_hook.py codex --url u"]
