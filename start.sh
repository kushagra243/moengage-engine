#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=========================================================="
echo " Starting MoEngage Local LLM Agent (Gemini Powered)"
echo "=========================================================="

if [ ! -d ".venv" ]; then
    echo "[*] Creating virtual environment (.venv)..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Check if dependencies are installed
if ! python3 -c "import fastapi, uvicorn, requests, pydantic" >/dev/null 2>&1; then
    echo "[*] Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
fi

python3 start.py
