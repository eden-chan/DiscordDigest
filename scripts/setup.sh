#!/usr/bin/env bash
set -euo pipefail

echo "[setup] Detecting Python..."
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[setup] Python not found. Please install Python 3.10+ and retry." >&2
  exit 1
fi

USE_UV=0
if command -v uv >/dev/null 2>&1; then
  USE_UV=1
fi

echo "[setup] Creating virtual environment at .venv (if missing)..."
if [ ! -d .venv ]; then
  if [ "$USE_UV" = "1" ]; then
    uv venv
  else
    $PY -m venv .venv
  fi
fi

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "[setup] Virtualenv Python not found at $VENV_PY" >&2
  exit 1
fi

if [ "$USE_UV" = "1" ]; then
  echo "[setup] Installing requirements with uv..."
  uv pip install -r requirements.txt -p "$VENV_PY"
else
  echo "[setup] Bootstrapping pip inside venv..."
  if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    # Try ensurepip, then fall back to get-pip
    if ! "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1; then
      echo "[setup] ensurepip not available; downloading get-pip.py..."
      curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
      "$VENV_PY" /tmp/get-pip.py
    fi
  fi
  echo "[setup] Upgrading build tooling..."
  "$VENV_PY" -m pip install -U pip setuptools wheel

  echo "[setup] Installing requirements..."
  "$VENV_PY" -m pip install -r requirements.txt
fi

echo "[setup] Generating Prisma client and pushing schema (SQLite)..."
"$VENV_PY" -m prisma generate || true
"$VENV_PY" -m prisma db push || true

echo "[setup] Done. Next steps:"
cat <<EOF

1) Activate the venv for this shell:
   source .venv/bin/activate

2) Sync channels to SQLite (requires Bot token in env):
   DISCORD_TOKEN_TYPE=Bot TOKEN='YOUR_BOT_TOKEN' GUILD_ID=1384033183112237208 \\
     python -m digest --sync-channels

3) Launch the TUI:
   python -m tui

EOF

# If this script is being sourced, activate venv automatically
if [ -n "${BASH_SOURCE:-}" ] && [ "${BASH_SOURCE[0]}" != "$0" ]; then
  . .venv/bin/activate || true
elif [ -n "${ZSH_EVAL_CONTEXT:-}" ] && [[ "$ZSH_EVAL_CONTEXT" == *":file"* ]]; then
  . .venv/bin/activate || true
fi
