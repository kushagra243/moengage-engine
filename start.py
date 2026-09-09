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
    # self-repair: if the code no longer imports (e.g. a merged agent change broke it), revert that merge and try again
    chk = subprocess.run([VPY, "-c", "import sys; sys.path.insert(0, %r); import backend.main" % ROOT], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "MOE_IMPORT_CHECK": "1"})
    if chk.returncode != 0:
        print("[!] backend failed to import:\n" + chk.stderr[-1200:])
        lm_path = os.path.join(ROOT, "data", "last_merge.json")
        if os.path.exists(lm_path):
            try:
                import json as _json
                lm = _json.load(open(lm_path))
                if not lm.get("rolled_back"):
                    print("[*] rolling back the last agent code change %s" % lm.get("post_commit"))
                    r = subprocess.run(["git", "revert", "--no-edit", "-m", "1", lm["post_commit"]], cwd=ROOT, capture_output=True, text=True)
                    if r.returncode == 0:
                        lm["rolled_back"] = True; lm["rollback_reason"] = "import failure at startup"; _json.dump(lm, open(lm_path, "w"))
                        chk = subprocess.run([VPY, "-c", "import sys; sys.path.insert(0, %r); import backend.main" % ROOT], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "MOE_IMPORT_CHECK": "1"})
                        print("[*] rollback " + ("succeeded; starting the previous version" if chk.returncode == 0 else "did not fix the import; see the error above"))
                    else:
                        subprocess.run(["git", "revert", "--abort"], cwd=ROOT, capture_output=True)
                        print("[!] automatic rollback failed: " + r.stderr[-400:])
            except Exception as e:
                print("[!] rollback error: %s" % e)
        if chk.returncode != 0:
            sys.exit(1)
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    while not port_free(port):
        port += 1
    print("=" * 64)
    print("  moengage-engine (local)   http://127.0.0.1:%d" % port)
    print("  bound to loopback only; secrets encrypted; outbound allowlisted")
    print("=" * 64)
    # Always loopback. An identity-aware proxy on the same host (tailscale serve, cloudflared) fronts it.
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=False, log_level="info", proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
