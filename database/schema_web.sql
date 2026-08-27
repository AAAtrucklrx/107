-- 小蜗 Web API 增量 Schema。
-- 运行库仍为 database/xiaowo.db；该文件由 xiaowo_web.storage.WebStore 幂等加载。

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    auth_mode TEXT NOT NULL CHECK (auth_mode IN ('anonymous', 'demo', 'cas')),
    profile_json TEXT NOT NULL DEFAULT '{}',
    is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    csrf_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    idle_expires_at REAL NOT NULL,
    absolute_expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_expiry
    ON web_sessions(absolute_expires_at, idle_expires_at);

CREATE INDEX IF NOT EXISTS idx_web_sessions_principal
    ON web_sessions(principal_id, created_at DESC);

CREATE TABLE IF NOT EXISTS web_chat_runs (
    run_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('auto', 'web', 'local')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'cancelled', 'failed')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    error_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_web_chat_runs_owner
    ON web_chat_runs(owner_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_web_chat_runs_expiry
    ON web_chat_runs(expires_at);

CREATE TABLE IF NOT EXISTS web_chat_events (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES web_chat_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS web_run_tombstones (
    run_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    expired_at REAL NOT NULL,
    purge_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_run_tombstones_purge
    ON web_run_tombstones(purge_at);

CREATE TABLE IF NOT EXISTS web_conversations (
    conversation_id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_conversations_owner
    ON web_conversations(owner_key, updated_at DESC, conversation_id DESC);

CREATE INDEX IF NOT EXISTS idx_web_conversations_expiry
    ON web_conversations(expires_at);

CREATE TABLE IF NOT EXISTS web_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    run_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content_value TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES web_conversations(conversation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_web_messages_conversation
    ON web_messages(conversation_id, created_at ASC, message_id ASC);

CREATE TABLE IF NOT EXISTS answer_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'anonymous' CHECK (namespace IN ('anonymous', 'demo', 'production')),
    category TEXT NOT NULL,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_answer_feedback_expiry
    ON answer_feedback(expires_at);
