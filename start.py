#!/usr/bin/env python3
import os
import sys
import subprocess
import socket

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(PROJECT_ROOT, ".venv")
VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python3")
REQUIREMENTS = os.path.join(PROJECT_ROOT, "requirements.txt")

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def find_available_port(start_port: int = 8080) -> int:
    port = start_port
    while is_port_in_use(port):
        port += 1
    return port

def setup_virtualenv():
    """Ensure a virtualenv exists and has required packages installed"""
    if not os.path.exists(VENV_PYTHON):
        print(f"[*] Creating local Python virtual environment in {VENV_DIR}...")
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])

    try:
        # Check if uvicorn & fastapi are importable
        subprocess.check_call([VENV_PYTHON, "-c", "import fastapi, uvicorn, requests, pydantic"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[*] Installing required dependencies from requirements.txt...")
        subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip", "setuptools"])
        subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "-r", REQUIREMENTS])
        print("[+] Dependencies successfully installed.")

def main():
    # If not running inside the local venv, re-exec inside it
    if sys.executable != VENV_PYTHON and os.path.exists(VENV_PYTHON):
        os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)

    # If venv doesn't exist yet, build it
    if not os.path.exists(VENV_PYTHON):
        setup_virtualenv()
        os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)

    import uvicorn
    from backend.database import init_db

    # Initialize SQLite database
    init_db()

    port = find_available_port(8080)
    print("\n" + "="*60)
    print("🚀 MoEngage Autonomous AI Agent (Gemini Powered)")
    print("="*60)
    print(f"[*] Local Web UI running at: http://localhost:{port}")
    print(f"[*] API Docs running at:    http://localhost:{port}/docs")
    print(f"[*] Database location:      {os.path.join(PROJECT_ROOT, 'data', 'agent.db')}")
    print("="*60 + "\n")

    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    main()
