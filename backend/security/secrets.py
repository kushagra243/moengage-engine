"""
Encrypted-at-rest secret storage.

Master key lives in the macOS Keychain (service "moengage-engine"). If the
Keychain is unavailable (non-mac, CI) we fall back to data/secret.key with
mode 0600. Ciphertext is stored in the ordinary settings table prefixed with
"enc:v1:" so the rest of the app keeps using get_setting/set_setting.
"""
from __future__ import annotations
import base64
import os
import subprocess
import sys
import threading
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from .redact import register_secret_value

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
KEY_FILE = os.path.join(DATA_DIR, "secret.key")
KEYCHAIN_SERVICE = "moengage-engine"
KEYCHAIN_ACCOUNT = "master-key"
PREFIX = "enc:v1:"

# Settings keys whose values are secrets. Anything listed here is encrypted at
# rest, redacted from logs and never returned by /api/settings.
SECRET_KEYS = frozenset({
    "moengage_cookies",
    "moengage_access_token",
    "moengage_refresh_token",
    "moengage_api_key",
    "moengage_data_api_key",
    "moengage_segmentation_key",
    "moengage_campaign_key",
    "moengage_inform_key",
    "moengage_custom_segment_key",
    "llm_api_key",
    "market_alpha_vantage_key",
    "market_finnhub_key",
    "market_newsapi_key",
    "market_cryptopanic_key",
    # legacy
    "gemini_api_key",
})


def is_secret_key(key: str) -> bool:
    return key in SECRET_KEYS or key.endswith("_api_key") or key.endswith("_secret") or key.endswith("_token")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:3] + "…" + "*" * 6 + "…" + value[-2:]


class SecretStore:
    def __init__(self):
        self._fernet: Optional[Fernet] = None
        self._lock = threading.Lock()
        self.backend = "uninitialised"

    # ── key management ────────────────────────────────────────────────────
    def _keychain_get(self) -> Optional[str]:
        if sys.platform != "darwin":
            return None
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            return None
        return None

    def _keychain_set(self, key: str) -> bool:
        if sys.platform != "darwin":
            return False
        try:
            out = subprocess.run(
                ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
                 "-l", "moengage-engine local secret key", "-w", key],
                capture_output=True, text=True, timeout=5,
            )
            return out.returncode == 0
        except Exception:
            return False

    def _file_get(self) -> Optional[str]:
        try:
            with open(KEY_FILE, "r") as f:
                return f.read().strip() or None
        except FileNotFoundError:
            return None

    def _file_set(self, key: str) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        os.chmod(KEY_FILE, 0o600)

    def _load_key(self) -> str:
        if os.environ.get("MOE_ENGINE_SECRET_KEY"):
            self.backend = "env"
            return os.environ["MOE_ENGINE_SECRET_KEY"]
        k = self._keychain_get()
        if k:
            self.backend = "keychain"
            return k
        k = self._file_get()
        if k:
            self.backend = "file"
            return k
        # generate
        k = Fernet.generate_key().decode()
        if self._keychain_set(k):
            self.backend = "keychain"
        else:
            self._file_set(k)
            self.backend = "file"
        return k

    def _f(self) -> Fernet:
        if self._fernet is None:
            with self._lock:
                if self._fernet is None:
                    self._fernet = Fernet(self._load_key().encode())
        return self._fernet

    # ── encrypt / decrypt ─────────────────────────────────────────────────
    def encrypt(self, plaintext: str) -> str:
        if plaintext is None or plaintext == "":
            return ""
        register_secret_value(plaintext)
        return PREFIX + self._f().encrypt(plaintext.encode()).decode()

    def decrypt(self, stored: str) -> str:
        if not stored:
            return ""
        if not stored.startswith(PREFIX):
            # legacy plaintext value: return as-is (caller should re-save to encrypt)
            register_secret_value(stored)
            return stored
        try:
            pt = self._f().decrypt(stored[len(PREFIX):].encode()).decode()
        except InvalidToken:
            return ""
        register_secret_value(pt)
        return pt

    def is_encrypted(self, stored: str) -> bool:
        return bool(stored) and stored.startswith(PREFIX)

    def status(self) -> dict:
        self._f()
        return {"backend": self.backend, "key_file_present": os.path.exists(KEY_FILE)}


secret_store = SecretStore()
