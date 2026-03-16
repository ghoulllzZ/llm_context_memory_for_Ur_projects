from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workspace (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspace (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_name_workspace
    ON project (workspace_id, name);

CREATE TABLE IF NOT EXISTS import_batch (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    import_mode TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    checksum TEXT NOT NULL,
    raw_payload_path TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspace (id)
);
CREATE INDEX IF NOT EXISTS idx_import_batch_created_at
    ON import_batch (created_at DESC);

CREATE TABLE IF NOT EXISTS conversation (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT DEFAULT '',
    raw_payload_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_message_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (platform, external_id),
    FOREIGN KEY (workspace_id) REFERENCES workspace (id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_workspace
    ON conversation (workspace_id, platform, last_message_at DESC);

CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    external_id TEXT,
    parent_id TEXT,
    role TEXT NOT NULL,
    seq_no INTEGER NOT NULL,
    text_content TEXT NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (conversation_id, external_id),
    FOREIGN KEY (conversation_id) REFERENCES conversation (id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES message (id)
);
CREATE INDEX IF NOT EXISTS idx_message_conversation
    ON message (conversation_id, seq_no);

CREATE TABLE IF NOT EXISTS asset (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_name TEXT DEFAULT '',
    local_path TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    sha256 TEXT DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES message (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_message
    ON asset (message_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_asset_sha256
    ON asset (sha256);

CREATE TABLE IF NOT EXISTS asset_analysis (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    parser_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    extracted_text TEXT NOT NULL,
    structured_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES asset (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_analysis_asset
    ON asset_analysis (asset_id, version_no DESC);

CREATE TABLE IF NOT EXISTS conversation_project (
    conversation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, project_id, source),
    FOREIGN KEY (conversation_id) REFERENCES conversation (id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_unit (
    id TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    workspace_id TEXT NOT NULL,
    source_conversation_id TEXT,
    source_import_batch_id TEXT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    stability TEXT NOT NULL,
    review_status TEXT NOT NULL,
    supersedes_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspace (id),
    FOREIGN KEY (source_conversation_id) REFERENCES conversation (id),
    FOREIGN KEY (source_import_batch_id) REFERENCES import_batch (id),
    FOREIGN KEY (supersedes_id) REFERENCES knowledge_unit (id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_workspace_status
    ON knowledge_unit (workspace_id, review_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_canonical
    ON knowledge_unit (canonical_id, version_no DESC);

CREATE TABLE IF NOT EXISTS knowledge_project (
    knowledge_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, project_id),
    FOREIGN KEY (knowledge_id) REFERENCES knowledge_unit (id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_ref (
    id TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL,
    message_id TEXT,
    asset_id TEXT,
    span_start INTEGER,
    span_end INTEGER,
    locator_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (knowledge_id) REFERENCES knowledge_unit (id) ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES message (id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES asset (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_evidence_knowledge
    ON evidence_ref (knowledge_id);

CREATE TABLE IF NOT EXISTS retrieval_chunk (
    id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    text TEXT NOT NULL,
    vector_ref TEXT DEFAULT '',
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retrieval_owner
    ON retrieval_chunk (owner_type, owner_id);

CREATE TABLE IF NOT EXISTS review_record (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action TEXT NOT NULL,
    editor_note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_pack (
    id TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    template_type TEXT NOT NULL,
    task_goal TEXT NOT NULL,
    budget_mode TEXT NOT NULL,
    budget_value INTEGER NOT NULL,
    reviewed_only INTEGER NOT NULL,
    output_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES project (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_context_pack_project
    ON context_pack (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS context_pack_item (
    context_pack_id TEXT NOT NULL,
    knowledge_id TEXT NOT NULL,
    source_rank INTEGER NOT NULL,
    inclusion_reason TEXT NOT NULL,
    PRIMARY KEY (context_pack_id, knowledge_id),
    FOREIGN KEY (context_pack_id) REFERENCES context_pack (id) ON DELETE CASCADE,
    FOREIGN KEY (knowledge_id) REFERENCES knowledge_unit (id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as connection:
            cursor = connection.execute(query, params)
            return cursor.fetchone()

    def fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as connection:
            cursor = connection.execute(query, params)
            return cursor.fetchall()
