-- Public Web evidence review and publishing state. Runtime DB is gitignored.

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    idempotency_key TEXT NOT NULL UNIQUE,
    snapshot_hash TEXT NOT NULL,
    evidence_span_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'retry', 'done', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at REAL NOT NULL,
    lease_owner TEXT,
    lease_expires_at REAL,
    last_error_code TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_claim
    ON ingestion_jobs(status, available_at, lease_expires_at, created_at);

-- Terminal ingestion jobs can be compacted without losing the content-hash
-- idempotency boundary. Tombstones are intentionally retained indefinitely.
CREATE TABLE IF NOT EXISTS ingestion_tombstones (
    idempotency_key TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    snapshot_hash TEXT NOT NULL,
    evidence_span_hash TEXT NOT NULL,
    terminal_status TEXT NOT NULL CHECK (terminal_status IN ('done', 'dead')),
    completed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS refetch_jobs (
    job_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    item_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    original_snapshot_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'retry', 'done', 'dead')),
    outcome TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at REAL NOT NULL,
    lease_owner TEXT,
    lease_expires_at REAL,
    last_error_code TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (item_id) REFERENCES review_items(item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_refetch_jobs_claim
    ON refetch_jobs(status, available_at, lease_expires_at, created_at);

CREATE TABLE IF NOT EXISTS web_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    normalized_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    content_path TEXT,
    content_type TEXT NOT NULL,
    fetched_at TEXT,
    removed_at REAL,
    created_at REAL NOT NULL,
    UNIQUE(namespace, snapshot_hash)
);

CREATE TABLE IF NOT EXISTS review_items (
    item_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    snapshot_id TEXT NOT NULL,
    title TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('campus', 'general')),
    category TEXT NOT NULL CHECK (category IN ('announcement', 'dynamic_service', 'policy', 'stable_general')),
    ttl_days INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'draft', 'in_review', 'approved', 'pending_publish', 'publish_failed',
        'active', 'rejected', 'expired', 'revoked'
    )),
    current_version INTEGER NOT NULL DEFAULT 1,
    active_generation_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES web_snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_review_items_queue
    ON review_items(namespace, status, updated_at DESC, item_id DESC);

CREATE TABLE IF NOT EXISTS review_versions (
    version_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('raw', 'model', 'human', 'approved')),
    content_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    actor_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(item_id, version_number, kind),
    FOREIGN KEY (item_id) REFERENCES review_items(item_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_chunks (
    chunk_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    content_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'pending' CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    -- Compatibility projection kept for older readers; new code uses
    -- approval_status and always keeps this value in sync.
    approved INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0, 1)),
    approved_by TEXT,
    approved_at REAL,
    expires_at REAL,
    UNIQUE(version_id, position),
    FOREIGN KEY (item_id) REFERENCES review_items(item_id) ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES review_versions(version_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_review_chunks_item
    ON review_chunks(item_id, position);

CREATE TABLE IF NOT EXISTS review_audit (
    audit_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    actor_key TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT,
    request_id TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_audit_object
    ON review_audit(namespace, object_type, object_id, created_at);

CREATE TABLE IF NOT EXISTS publish_generations (
    generation_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    status TEXT NOT NULL CHECK (status IN ('building', 'verified', 'active', 'orphan', 'failed')),
    manifest_path TEXT,
    manifest_hash TEXT,
    created_at REAL NOT NULL,
    activated_at REAL,
    orphaned_at REAL
);

CREATE TABLE IF NOT EXISTS publish_jobs (
    job_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    generation_id TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'retry', 'done', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at REAL NOT NULL,
    lease_owner TEXT,
    lease_expires_at REAL,
    last_error_code TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (generation_id) REFERENCES publish_generations(generation_id)
);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_claim
    ON publish_jobs(status, available_at, lease_expires_at, created_at);

CREATE TABLE IF NOT EXISTS publish_job_items (
    job_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (job_id, item_id),
    FOREIGN KEY (job_id) REFERENCES publish_jobs(job_id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES review_items(item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_publish_job_items_item
    ON publish_job_items(item_id, job_id);

CREATE TABLE IF NOT EXISTS publish_documents (
    generation_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    content_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (generation_id, document_id),
    FOREIGN KEY (generation_id) REFERENCES publish_generations(generation_id),
    FOREIGN KEY (item_id) REFERENCES review_items(item_id)
);

CREATE TABLE IF NOT EXISTS active_index_state (
    namespace TEXT PRIMARY KEY CHECK (namespace IN ('demo', 'production')),
    generation_id TEXT NOT NULL,
    previous_generation_id TEXT,
    activated_at REAL NOT NULL,
    FOREIGN KEY (generation_id) REFERENCES publish_generations(generation_id)
);

CREATE TABLE IF NOT EXISTS source_trust_proposals (
    proposal_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    item_id TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'exported', 'rejected')),
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (item_id) REFERENCES review_items(item_id) ON DELETE CASCADE
);

-- User-submitted campus tools are deliberately separate from config/links.yaml.
-- The YAML file remains the trust boundary for verified official links.
CREATE TABLE IF NOT EXISTS campus_tool_applications (
    application_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    applicant_principal_id TEXT NOT NULL,
    applicant_auth_mode TEXT NOT NULL CHECK (applicant_auth_mode IN ('demo', 'cas')),
    applicant_name_snapshot TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL CHECK (category IN ('study', 'life', 'information', 'community', 'other')),
    submitted_url TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    decision_reason TEXT,
    reviewed_by TEXT,
    reviewed_at REAL,
    request_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(namespace, applicant_principal_id, request_id)
);

CREATE INDEX IF NOT EXISTS idx_campus_tool_applications_queue
    ON campus_tool_applications(namespace, status, updated_at DESC, application_id DESC);

CREATE INDEX IF NOT EXISTS idx_campus_tool_applications_owner
    ON campus_tool_applications(namespace, applicant_principal_id, created_at DESC, application_id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_campus_tool_applications_pending_url
    ON campus_tool_applications(namespace, normalized_url)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS campus_tools (
    tool_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    application_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL CHECK (category IN ('study', 'life', 'information', 'community', 'other')),
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'unpublished')),
    published_by TEXT NOT NULL,
    published_at REAL NOT NULL,
    unpublished_by TEXT,
    unpublished_at REAL,
    unpublish_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (application_id) REFERENCES campus_tool_applications(application_id)
);

CREATE INDEX IF NOT EXISTS idx_campus_tools_directory
    ON campus_tools(namespace, status, category, published_at DESC, tool_id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_campus_tools_active_url
    ON campus_tools(namespace, normalized_url)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS campus_tool_audit (
    audit_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    actor_key TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK (object_type IN ('application', 'tool', 'notification')),
    object_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    reason TEXT,
    request_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(namespace, actor_key, request_id)
);

CREATE INDEX IF NOT EXISTS idx_campus_tool_audit_object
    ON campus_tool_audit(namespace, object_type, object_id, created_at DESC);

CREATE TABLE IF NOT EXISTS user_notifications (
    notification_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK (namespace IN ('demo', 'production')),
    recipient_principal_id TEXT NOT NULL,
    notification_type TEXT NOT NULL CHECK (notification_type IN ('tool_approved', 'tool_rejected', 'tool_unpublished')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    application_id TEXT NOT NULL,
    tool_id TEXT,
    read_at REAL,
    created_at REAL NOT NULL,
    FOREIGN KEY (application_id) REFERENCES campus_tool_applications(application_id),
    FOREIGN KEY (tool_id) REFERENCES campus_tools(tool_id)
);

CREATE INDEX IF NOT EXISTS idx_user_notifications_recipient
    ON user_notifications(namespace, recipient_principal_id, read_at, created_at DESC);
