#!/usr/bin/env python3
"""Run the real Xiaowo API on USB loopback, with separate local data and keys."""
from __future__ import annotations

import json
from contextlib import closing
from importlib import import_module
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "mobile" / ".runtime"


def snapshot_public_catalog(source: Path, destination: Path) -> None:
    """Take a consistent SQLite backup with the source connection read-only."""
    if not source.is_file():
        return
    fd, temporary = tempfile.mkstemp(prefix="course-catalog-", suffix=".db", dir=destination.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_file = RUNTIME / "local-keys.json"
    if not key_file.exists():
        keys = {name: secrets.token_urlsafe(48) for name in ("mobile_vault", "data", "session")}
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(keys, handle)
    keys = json.loads(key_file.read_text())
    sys.path.insert(0, str(ROOT))
    # Read existing provider configuration without displaying or copying its secrets.
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ.update({
        "XIAOWO_ENV": "development", "XIAOWO_AUTH_MODE": "anonymous", "XIAOWO_ADMIN_IDS": "",
        "XIAOWO_PUBLIC_ORIGIN": "http://127.0.0.1:8000",
        "XIAOWO_MOBILE_AUTH_ENABLED": "true", "XIAOWO_MOBILE_DEBUG_LOOPBACK": "true",
        "XIAOWO_MOBILE_VAULT_KEY": keys["mobile_vault"],
        "XIAOWO_MOBILE_AUTH_DB_PATH": str(RUNTIME / "data/mobile_auth.db"),
        "XIAOWO_DATA_KEY": keys["data"], "XIAOWO_SESSION_SECRET": keys["session"],
        "XIAOWO_SESSION_VAULT_KEY": "", "YOUNG_TOKEN": "",
        "XIAOWO_APP_DB_PATH": str(RUNTIME / "data/xiaowo.db"),
        "XIAOWO_REVIEW_DB_PATH": str(RUNTIME / "data/review.db"),
        "XIAOWO_WEB_EVIDENCE_DIR": str(RUNTIME / "data/web-evidence"),
        "XIAOWO_PUBLISHED_CHROMA_DIR": str(RUNTIME / "data/published-chroma"),
        "XIAOWO_PUBLISHED_BM25_DIR": str(RUNTIME / "data/published-bm25"),
        "XIAOWO_CHROMA_DIR": str(RUNTIME / "data/faq-chroma"),
        "XIAOWO_INGESTION_WORKER_ENABLED": "false", "XIAOWO_DEMO_RESET_ENABLED": "false",
        "XIAOWO_WEB_SEARCH_ENABLED": "false", "XIAOWO_DEBUG": "0",
    })
    (RUNTIME / "data").mkdir(mode=0o700, parents=True, exist_ok=True)
    import config
    # Legacy tools have a Python constant for this path. Redirect before importing
    # the application/graph, so this launcher never initializes the user's main DB.
    config.DATABASE_PATH = RUNTIME / "data/xiaowo.db"
    config.CHROMA_PERSIST_DIR = os.environ["XIAOWO_CHROMA_DIR"]
    config.YOUNG_TOKEN = ""
    config.YOUNG_SNAPSHOT_PATH = RUNTIME / "data/young_snapshot.json"
    # These legacy public-catalog tools use module constants rather than config.
    # Snapshot only this public database; never copy the user's main/student DB.
    catalog = RUNTIME / "data/course_data.db"
    snapshot_public_catalog(ROOT / "data/course_data.db", catalog)
    for module, attribute in (("advisor_tools", "COURSE_DB"), ("program_tools", "COURSE_DB"),
                              ("course_tools", "_REVIEW_DB"), ("lesson_local", "_REVIEW_DB")):
        setattr(import_module(f"tools.{module}"), attribute, catalog)
    import uvicorn
    from xiaowo_web.main import app
    print("Local mobile API: http://127.0.0.1:8000 (USB adb reverse only)")
    print("Mobile databases and school snapshots are isolated under mobile/.runtime; model provider configuration is reused.")
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)


if __name__ == "__main__":
    main()
