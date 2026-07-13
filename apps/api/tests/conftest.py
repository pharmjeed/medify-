import os
from pathlib import Path

db_path = Path("/tmp/medify-test.db")
if db_path.exists():
    db_path.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["DEMO_MODE"] = "true"
os.environ["ENVIRONMENT"] = "test"
os.environ["COOKIE_SECURE"] = "false"
