"""Exercise the local launcher with synthetic configuration; never start an API."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import ModuleType

import pytest


@pytest.fixture
def launch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mobile_dev_launcher", Path(__file__).with_name("dev_server.py"))
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "RUNTIME", tmp_path / "mobile" / ".runtime")
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr(os, "umask", lambda _: 0o022)
    monkeypatch.setattr(sys, "path", list(sys.path))
    config = ModuleType("config")
    config.DATABASE_PATH = tmp_path / "database" / "xiaowo.db"
    config.CHROMA_PERSIST_DIR = str(tmp_path / "knowledge" / "chroma_db")
    config.YOUNG_TOKEN = "synthetic-existing-school-token"
    config.YOUNG_SNAPSHOT_PATH = tmp_path / "scripts" / "data" / "young_personal" / "young_snapshot.json"
    monkeypatch.setitem(sys.modules, "config", config)
    for name in ("advisor_tools", "program_tools", "course_tools", "lesson_local"):
        module = ModuleType(f"tools.{name}")
        module.COURSE_DB = module._REVIEW_DB = tmp_path / "data" / "course_data.db"
        monkeypatch.setitem(sys.modules, module.__name__, module)
    dotenv = ModuleType("dotenv")
    def load_dotenv(_path):
        os.environ.update({
            "YOUNG_TOKEN": config.YOUNG_TOKEN,
            "LLM_API_KEY": "synthetic-provider-key",
            "XIAOWO_SESSION_VAULT_KEY": "synthetic-existing-browser-vault",
            "XIAOWO_APP_DB_PATH": str(tmp_path / "existing.db"),
        })
    dotenv.load_dotenv = load_dotenv
    monkeypatch.setitem(sys.modules, "dotenv", dotenv)
    web = ModuleType("xiaowo_web.main")
    web.app = object()
    monkeypatch.setitem(sys.modules, "xiaowo_web.main", web)
    uvicorn = ModuleType("uvicorn")
    calls = []
    uvicorn.run = lambda app, **kwargs: calls.append((app, kwargs))
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    return launcher, config, calls


def test_launcher_keeps_every_mutable_database_and_personal_snapshot_isolated(launch):
    launcher, config, calls = launch
    launcher.main()
    runtime = launcher.RUNTIME
    for name in ("XIAOWO_APP_DB_PATH", "XIAOWO_REVIEW_DB_PATH", "XIAOWO_MOBILE_AUTH_DB_PATH",
                 "XIAOWO_CHROMA_DIR", "XIAOWO_PUBLISHED_CHROMA_DIR", "XIAOWO_PUBLISHED_BM25_DIR", "XIAOWO_WEB_EVIDENCE_DIR"):
        assert Path(os.environ[name]).is_relative_to(runtime), name
    assert config.DATABASE_PATH.is_relative_to(runtime)
    assert Path(config.CHROMA_PERSIST_DIR).is_relative_to(runtime)
    assert config.YOUNG_SNAPSHOT_PATH.is_relative_to(runtime)
    assert os.environ["YOUNG_TOKEN"] == config.YOUNG_TOKEN == ""
    assert os.environ["XIAOWO_SESSION_VAULT_KEY"] == ""
    assert os.environ["LLM_API_KEY"] == "synthetic-provider-key"
    for name, attribute in (("advisor_tools", "COURSE_DB"), ("program_tools", "COURSE_DB"),
                            ("course_tools", "_REVIEW_DB"), ("lesson_local", "_REVIEW_DB")):
        assert getattr(sys.modules[f"tools.{name}"], attribute) == runtime / "data/course_data.db"
    assert calls == [(sys.modules["xiaowo_web.main"].app, {"host": "127.0.0.1", "port": 8000, "access_log": False})]


def test_launcher_reuses_private_local_keys_without_using_existing_server_keys(launch):
    launcher, _, _ = launch
    launcher.main()
    key_file = launcher.RUNTIME / "local-keys.json"
    original = json.loads(key_file.read_text())
    assert len(set(original.values())) == 3
    assert min(map(len, original.values())) >= 64
    assert key_file.stat().st_mode & 0o777 == 0o600
    assert launcher.RUNTIME.stat().st_mode & 0o777 == 0o700
    launcher.main()
    assert json.loads(key_file.read_text()) == original
    assert os.environ["XIAOWO_MOBILE_VAULT_KEY"] == original["mobile_vault"]
    assert os.environ["XIAOWO_DATA_KEY"] == original["data"]
    assert os.environ["XIAOWO_SESSION_SECRET"] == original["session"]


def test_launcher_snapshots_only_the_public_catalog_through_a_read_only_connection(launch, monkeypatch):
    launcher, _, _ = launch
    source = launcher.ROOT / "data/course_data.db"
    source.parent.mkdir()
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE courses (name TEXT)")
        conn.execute("INSERT INTO courses VALUES ('Synthetic public course')")
    existing = source.read_bytes()
    private = launcher.ROOT / "database/xiaowo.db"
    private.parent.mkdir()
    private.write_bytes(b"synthetic private student history - must not be copied")
    connect = sqlite3.connect
    observed = []
    def tracked(database, *args, **kwargs):
        conn = connect(database, *args, **kwargs)
        if str(source) in str(database):
            observed.append(str(database))
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("INSERT INTO courses VALUES ('not allowed')")
        return conn
    monkeypatch.setattr(sqlite3, "connect", tracked)
    launcher.main()
    assert observed and "mode=ro" in observed[0]
    destination = launcher.RUNTIME / "data/course_data.db"
    with connect(destination) as conn:
        assert conn.execute("SELECT name FROM courses").fetchall() == [("Synthetic public course",)]
        conn.execute("INSERT INTO courses VALUES ('local-only change')")
    assert source.read_bytes() == existing
    assert not (launcher.RUNTIME / "data/xiaowo.db").exists()
