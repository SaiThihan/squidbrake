#!/usr/bin/env sh
# Squidbrake installer for macOS / Linux.
#   curl -fsSL <server>/install.sh | sh
# With SQUIDBRAKE_PILOT and SQUIDBRAKE_PILOT_SERVER set, it also offers to join that pilot (it asks first).
set -e
printf '\nInstalling Squidbrake (brakes for AI agents)...\n\n'

fail() { printf '\n  [X] %s\n\n' "$1"; exit 1; }
new_enough() { [ -n "$1" ] && "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }

# A Python 3.10+: macOS ships 3.9, so look for a newer one before giving up
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python \
         /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  p=$(command -v "$c" 2>/dev/null || true)
  if new_enough "$p"; then PY="$p"; break; fi
done
if command -v pipx >/dev/null 2>&1; then
  PIPX="pipx"
elif command -v brew >/dev/null 2>&1; then
  echo "Installing pipx with Homebrew (it brings its own Python)..."
  brew install pipx >/dev/null || fail "Homebrew couldn't install pipx. Run: brew install pipx  and then this command again."
  PIPX="pipx"
elif [ -n "$PY" ]; then
  "$PY" -m pip install --user --quiet pipx 2>/dev/null || "$PY" -m pip install --user --quiet --break-system-packages pipx
  PIPX="$PY -m pipx"
else
  fail "Squidbrake needs Python 3.10 or newer, and this computer has $(python3 --version 2>/dev/null || echo 'no Python').
      macOS: install Homebrew (https://brew.sh), then run this command again; it does the rest.
      Or install Python from https://www.python.org/downloads/ and run this command again."
fi
(cd /tmp && $PIPX install --force squidbrake ${PY:+--python "$PY"}) || fail "Installing Squidbrake failed (see the lines above)."
$PIPX ensurepath >/dev/null 2>&1 || true

SB="$HOME/.local/bin/squidbrake"
[ -x "$SB" ] || SB=$(command -v squidbrake || true)
[ -n "$SB" ] && "$SB" --version >/dev/null 2>&1 || fail "Squidbrake didn't install. Send the lines above to whoever sent you this link."
printf '\nInstalled: %s\n' "$("$SB" --version)"

if [ -n "$SQUIDBRAKE_URL" ] && [ -n "$SQUIDBRAKE_AGENT_KEY" ]; then
  # a hosted dashboard: nothing to run locally, just route Claude Code through it
  case "$SQUIDBRAKE_AGENT_KEY" in *YOUR_AGENT_KEY*)
    echo "Put your agent key (from your start page) in place of gw_YOUR_AGENT_KEY and run it again."; exit 1 ;; esac
  if ! curl -fsS -m 20 -H "X-Gateway-Key: $SQUIDBRAKE_AGENT_KEY" "$SQUIDBRAKE_URL/v1/me" >/dev/null; then
    echo "Couldn't reach your dashboard with that key. Check the key and run it again."; exit 1
  fi
  printf '\n  [OK] Your dashboard answers.\n'
  if command -v claude >/dev/null 2>&1 || [ -d "$HOME/.claude" ]; then
    "$SB" connect claude-code --url "$SQUIDBRAKE_URL" --key "$SQUIDBRAKE_AGENT_KEY" --yes --hook-only >/dev/null
    printf '  [OK] Claude Code: every tool call (commands, edits, web, MCP) goes through it.\n'
  fi
  # every other coding agent installed here: its terminal commands and file actions (hooks) ...
  "$SB" connect agents --agent all --url "$SQUIDBRAKE_URL" --key "$SQUIDBRAKE_AGENT_KEY" --yes | sed 's/^/  /'
  # ... and its own MCP servers (GitHub, Stripe, databases...) go through it too
  "$SB" connect guard --agent all --url "$SQUIDBRAKE_URL" --key "$SQUIDBRAKE_AGENT_KEY" --yes | sed 's/^/  /'
  printf '\nLast step: quit and reopen your agents (Cursor: Cmd+Q, then open it again), then work as usual.\nYour dashboard: %s/dashboard\nTo use the squidbrake command yourself (squidbrake connect status), open a new terminal window first.\n\n' "$SQUIDBRAKE_URL"
  exit 0
fi

if [ -n "$SQUIDBRAKE_PILOT" ] && [ -n "$SQUIDBRAKE_PILOT_SERVER" ]; then
  "$SB" pilot join "$SQUIDBRAKE_PILOT" --server "$SQUIDBRAKE_PILOT_SERVER" </dev/tty
fi

cat <<'EOF'

Next:
  1. Open a new terminal (so the 'squidbrake' command is found) and run:  squidbrake
     It prints your keys (save them) and opens the dashboard. Keep that window open.
  2. In another terminal, connect Claude Code:  squidbrake connect claude-code
  3. Restart Claude Code and work as usual. Watch it at http://localhost:8080/dashboard

EOF
