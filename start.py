#!/usr/bin/env python3
"""Cross-platform launcher: creates .venv (Python >= 3.10), installs deps, runs on 127.0.0.1 only."""
import os
import shutil
import socket
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
VPY = os.path.join(VENV, "bin", "python3") if os.name != "nt" else os.path.join(VENV, "Scripts", "python.exe")
REQ = os.path.join(ROOT, "requirements.txt")


def find_python():
    if sys.version_info >= (3, 10):
        return sys.executable
    for name in ("python3.12", "python3.11", "python3.13", "python3.14", "python3"):
        p = shutil.which(name)
        if p:
            out = subprocess.run([p, "-c", "import sys; print(sys.version_info >= (3, 10))"], capture_output=True, text=True)
            if out.stdout.strip() == "True":
                return p
    sys.exit("Python 3.10+ is required.")


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main():
    if not os.path.exists(VPY):
        py = find_python()
        print(f"[*] creating virtualenv with {py}")
        subprocess.check_call([py, "-m", "venv", VENV])
        subprocess.check_call([VPY, "-m", "pip", "install", "-q", "--upgrade", "pip"])
        subprocess.check_call([VPY, "-m", "pip", "install", "-q", "-r", REQ])
    if os.path.realpath(sys.executable) != os.path.realpath(VPY):
        os.execv(VPY, [VPY] + sys.argv)
    try:
        import fastapi, uvicorn, cryptography, feedparser  # noqa
    except ImportError:
        subprocess.check_call([VPY, "-m", "pip", "install", "-q", "-r", REQ])
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    while not port_free(port):
        port += 1
    print("=" * 64)
    print("  moengage-engine (local)   http://127.0.0.1:%d" % port)
    print("  bound to loopback only; secrets encrypted; outbound allowlisted")
    print("=" * 64)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
