"""
小蜗 — 数据库管理模块
封装 SQLite 操作，提供建表、查询、数据导入功能
支持线程本地连接池，避免频繁创建/销毁连接
"""

import sqlite3
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from utils.logger import get_logger

log = get_logger("xiaowo.db")


class DatabaseManager:
    """
    SQLite 数据库管理器。

    特性：
    - 线程本地连接：每个线程复用自己的连接，避免并发问题
    - 自动 dict 行：查询结果直接返回 dict 列表
    - 上下文管理器：支持 with 语句
    """

    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._ensure_db_dir()
        self._local = threading.local()
        log.info(f"数据库已连接: {self.db_path}")

    def _ensure_db_dir(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def get_conn(self) -> sqlite3.Connection:
        """获取线程本地连接（懒创建）"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn = conn
        return conn

    def init_schema(self, schema_path: Path = None):
        """初始化数据库表结构"""
        if schema_path is None:
            from config import SCHEMA_PATH
            schema_path = SCHEMA_PATH
        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()
        conn = self.get_conn()
        conn.executescript(sql)
        # 轻量迁移：为老库补充后加列（新库由 schema.sql 直接建全；重复列报错即忽略）
        for ddl in (
            "ALTER TABLE student_grades ADD COLUMN score_text TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> int:
        """执行写操作，返回 lastrowid"""
        conn = self.get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid

    def executemany(self, sql: str, params_list: list[tuple]):
        conn = self.get_conn()
        conn.executemany(sql, params_list)
        conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """执行读操作，返回 dict 列表"""
        conn = self.get_conn()
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def run_script(self, sql: str):
        """执行多语句 SQL 脚本（如 seed data）"""
        conn = self.get_conn()
        conn.executescript(sql)
        conn.commit()

    @contextmanager
    def transaction(self):
        """
        单事务上下文：正常退出时提交，异常时整体回滚。

        支持嵌套：仅最外层事务执行 commit/rollback，
        内层事务的写操作交由外层统一提交，避免破坏事务边界。
        """
        conn = self.get_conn()
        outer = not conn.in_transaction
        try:
            yield conn
            if outer:
                conn.commit()
        except Exception:
            if outer:
                conn.rollback()
            raise

    def table_exists(self, table_name: str) -> bool:
        row = self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None

    def close(self):
        """关闭线程本地连接"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
            log.debug("数据库连接已关闭")