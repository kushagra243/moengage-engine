#!/usr/bin/env bash
# One-shot local setup after `git clone`. Idempotent; safe to re-run.
#   ./setup.sh            → venv, deps, Claude Code check, provider config, start server
#   ./setup.sh --no-start → everything except starting the server
#   ./setup.sh --openrouter → configure the OpenRouter provider instead of Claude Code (key entered later in Settings)
set -euo pipefail
cd "$(dirname "$0")"

START=1; PROVIDER="claude_cli"; MODEL="claude-sonnet-5"
for a in "$@"; do
  case "$a" in
    --no-start) START=0 ;;
    --openrouter) PROVIDER="openrouter"; MODEL="anthropic/claude-sonnet-4.5" ;;
    --model=*) MODEL="${a#--model=}" ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }
step() { printf '\n\033[1m[%s]\033[0m %s\n' "$1" "$2"; }

step 1/6 "Python"
PY=""
for c in python3.12 python3.11 python3.13 python3.14 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then warn "Python ≥3.10 not found; installing python@3.12 with Homebrew"; brew install python@3.12 >/dev/null; PY="$(brew --prefix)/opt/python@3.12/bin/python3.12"; else die "Python ≥3.10 not found and Homebrew is missing. Install from https://www.python.org/downloads/ and re-run."; fi
fi
ok "$($PY --version) at $PY"

step 2/6 "Virtualenv + dependencies"
if [ ! -x .venv/bin/python3 ]; then "$PY" -m venv .venv; ok "created .venv"; else ok ".venv exists"; fi
.venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1 || true
.venv/bin/python -m pip install -q -r requirements.txt
ok "dependencies installed"
mkdir -p data/logs data/captures
.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from backend.database import init_db; init_db(); from backend.security import secret_store; print('  ✓ database initialised; secret backend:', secret_store.status()['backend'])"

step 3/6 "Model provider: $PROVIDER"
if [ "$PROVIDER" = "claude_cli" ]; then
  if ! command -v claude >/dev/null 2>&1; then
    if command -v npm >/dev/null 2>&1; then warn "Claude Code not found; installing"; npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 && ok "installed Claude Code" || warn "npm install failed; install manually: npm install -g @anthropic-ai/claude-code"; else warn "Claude Code not found and npm missing (brew install node). Install then re-run, or use --openrouter."; fi
  else ok "Claude Code $(claude --version 2>/dev/null | head -1)"; fi
  if command -v claude >/dev/null 2>&1; then
    if claude -p "reply with ok" --output-format json 2>/dev/null | grep -q '"is_error":false'; then ok "Claude Code login is valid"
    else warn "Claude Code login missing or expired → run:  claude login   (then re-run ./setup.sh or Test LLM in Settings)"; fi
  fi
fi
.venv/bin/python cli.py set llm_provider "$PROVIDER" >/dev/null
.venv/bin/python cli.py set llm_model "$MODEL" >/dev/null
[ "$PROVIDER" = "openrouter" ] && .venv/bin/python cli.py set llm_base_url https://openrouter.ai/api/v1 >/dev/null
ok "engine set to provider=$PROVIDER model=$MODEL"
[ "$PROVIDER" = "openrouter" ] && warn "paste your OpenRouter key in Settings → LLM (or: ./cli.py set-key llm)"

step 4/6 "Tests"
if .venv/bin/python -m pytest -q tests >/dev/null 2>&1; then ok "test suite passes"; else warn "some tests failed; run .venv/bin/python -m pytest -q tests"; fi

step 5/6 "MoEngage access (do this in the browser console after start)"
echo "  Settings → Integration: choose your dashboard region, paste the DevTools 'Request Headers'"
echo "  of any dashboard request into 'Session cookies', turn Demo/mock mode off, Save."
echo "  Discovery + a read-only probe run automatically; results appear in the Integration tab."
echo "  Optional (stable path): Workspace ID + Data / Segmentation / Campaigns API keys."

step 6/6 "Start"
if [ "$START" = "1" ]; then
  echo "  http://127.0.0.1:${PORT:-8080}   (Ctrl-C stops; re-run ./setup.sh or python3 start.py to start again)"
  exec .venv/bin/python start.py
else
  ok "skipped start (run: python3 start.py)"
fi
