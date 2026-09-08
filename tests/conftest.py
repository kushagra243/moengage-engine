import os, sys, tempfile, shutil, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

@pytest.fixture(autouse=True, scope="session")
def isolated_db():
    """Point the app at a throwaway data dir so tests never touch data/agent.db or the keychain."""
    tmp = tempfile.mkdtemp(prefix="moe-test-")
    os.environ["MOE_ENGINE_SECRET_KEY"] = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()
    import backend.database as db
    db.DB_PATH = os.path.join(tmp, "agent.db")
    import backend.security.secrets as sec
    sec.DATA_DIR = tmp; sec.KEY_FILE = os.path.join(tmp, "secret.key")
    import importlib; aud = importlib.import_module('backend.security.audit')
    aud.AUDIT_FILE = os.path.join(tmp, "audit.jsonl")
    import backend.moengage.registry as reg
    reg.LEARNED_PATH = os.path.join(tmp, "endpoints.learned.json"); reg.VERIFIED_PATH = os.path.join(tmp, "endpoints.verified.json")
    import backend.market.sources as ms
    ms.CACHE_DIR = os.path.join(tmp, "cache")
    db.init_db()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)
