"""
小蜗 — 依赖注入容器
集中管理 DatabaseManager、FAQVectorStore 等全局单例，
避免工具模块通过全局变量 + set_xxx() 传递依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from database.db_manager import DatabaseManager
    from knowledge.vector_store import FAQVectorStore
    from services.cas_client import CASClient
    from tools.api_client import CatalogAPI


class ServiceContainer:
    """
    应用级依赖容器（单例模式）。

    使用方式：
        # 启动时初始化
        container = ServiceContainer()
        container.init_database(db_path, schema_path)
        container.init_vector_store(persist_dir)

        # 在工具中获取
        db = container.db
        store = container.faq_store
    """

    _instance: Optional[ServiceContainer] = None

    def __new__(cls) -> ServiceContainer:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._db: Optional[DatabaseManager] = None
        self._faq_store: Optional[FAQVectorStore] = None
        self._cas_client: Optional[CASClient] = None
        self._catalog_api: Optional[CatalogAPI] = None
        self._initialized = True

    # ── 初始化方法 ──────────────────────────────────

    def init_database(self, db_path, schema_path, seed_sql: str = None):
        """初始化数据库并导入种子数据"""
        from database.db_manager import DatabaseManager

        self._db = DatabaseManager(db_path)
        self._db.init_schema(schema_path)

        if seed_sql:
            existing = self._db.query_one("SELECT COUNT(*) as cnt FROM student_courses")
            if not existing or existing.get("cnt", 0) == 0:
                try:
                    self._db.run_script(seed_sql)
                    from utils.logger import get_logger
                    get_logger("xiaowo.init").info("种子数据已导入")
                except Exception as e:
                    from utils.logger import get_logger
                    get_logger("xiaowo.init").warning(f"种子数据导入: {e}（可能已存在）")

        return self._db

    def init_vector_store(self, persist_dir: str, knowledge_data_dir=None):
        """初始化向量知识库"""
        from knowledge.vector_store import FAQVectorStore

        self._faq_store = FAQVectorStore(persist_dir=persist_dir)

        if knowledge_data_dir and self._faq_store.count() == 0:
            from knowledge.document_loader import load_faq_documents
            from utils.logger import get_logger

            docs = load_faq_documents(knowledge_data_dir)
            if docs:
                self._faq_store.add_documents(docs)
                get_logger("xiaowo.init").info(f"知识库已加载 {len(docs)} 篇文档")

        return self._faq_store

    # ── CAS 认证 ──────────────────────────────────────

    def init_cas_client(self) -> CASClient:
        """初始化 CAS 客户端"""
        from services.cas_client import CASClient
        self._cas_client = CASClient()
        return self._cas_client

    def login(self, username: str, password: str) -> bool:
        """
        CAS 表单登录，同时初始化 CatalogAPI 的认证 session。
        
        Returns:
            True 登录成功
        """
        if self._cas_client is None:
            self.init_cas_client()
        success = self._cas_client.login(username, password)
        if success:
            # 共享 session 给 CatalogAPI
            if self._catalog_api:
                self._catalog_api.set_session(self._cas_client.session)
            # 建立 catalog session
            self._cas_client.login_catalog(username, password)
        return success

    def login_with_ticket(self, ticket: str, service_url: str = None) -> bool:
        """
        CAS 重定向登录：用 CAS ticket 建立教务系统会话。
        推荐方式——用户在科大 CAS 官方页面认证后回调。

        Args:
            ticket: CAS Service Ticket (ST-xxx)
            service_url: 回调地址

        Returns:
            True 登录成功
        """
        if self._cas_client is None:
            self.init_cas_client()
        success = self._cas_client.login_with_ticket(ticket, service_url)
        if success:
            # 共享 session 给 CatalogAPI
            if self._catalog_api:
                self._catalog_api.set_session(self._cas_client.session)
        return success

    # ── Catalog API ────────────────────────────────────

    def init_catalog_api(self, session=None) -> CatalogAPI:
        """初始化 CatalogAPI 客户端"""
        from tools.api_client import CatalogAPI
        # 优先使用 CAS 的 session
        if session is None and self._cas_client and self._cas_client.is_logged_in:
            session = self._cas_client.session
        self._catalog_api = CatalogAPI(session=session)
        return self._catalog_api

    # ── 访问属性 ────────────────────────────────────

    @property
    def cas_client(self) -> CASClient:
        if self._cas_client is None:
            raise RuntimeError("CAS客户端未初始化，请先调用 init_cas_client() 或 login()")
        return self._cas_client

    def has_cas(self) -> bool:
        """安全检查 CAS 是否已登录（不抛异常）"""
        return self._cas_client is not None and self._cas_client.is_logged_in

    def ensure_session(self) -> bool:
        """
        检查 CAS session 是否有效。
        如果已过期或未登录，返回 False。
        """
        if not self.has_cas():
            return False
        # CAS session 通常是 cookie-based，只要 _is_logged_in 为 True 就认为有效
        # 实际 API 调用失败时会在 Tool 层捕获
        return True

    @property
    def catalog_api(self) -> CatalogAPI:
        if self._catalog_api is None:
            # 懒初始化：如果没有 CAS session，创建无认证的 CatalogAPI
            self._catalog_api = self.init_catalog_api()
        return self._catalog_api

    @property
    def db(self) -> DatabaseManager:
        if self._db is None:
            raise RuntimeError("数据库未初始化，请先调用 init_database()")
        return self._db

    @property
    def faq_store(self) -> FAQVectorStore:
        if self._faq_store is None:
            raise RuntimeError("知识库未初始化，请先调用 init_vector_store()")
        return self._faq_store

    # ── 重置（用于测试） ────────────────────────────

    @classmethod
    def reset(cls):
        """重置单例（仅用于测试）"""
        cls._instance = None
