from __future__ import annotations

import base64
import io
import json
import queue
import threading
import zipfile
from pathlib import Path
from typing import Any

from .config import AppConfig
from .database import Database
from .extractors import (
    analyze_asset_fallback,
    build_retrieval_chunks,
    canonical_context_pack_id,
    coerce_json,
    combine_message_text,
    derive_project_name,
    estimate_tokens,
    extract_knowledge_units,
    guess_suffix,
    new_id,
    normalize_space,
    now_iso,
    recommend_projects,
    render_context_pack,
    sha256_bytes,
    sha256_text,
    summarize_conversation,
)
from .model_client import OpenAICompatibleClient
from .storage import LocalStorage


class KnowledgeBaseService:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig.from_env()
        self.config.ensure_directories()
        self.database = Database(self.config.data_dir / "knowledge_base.sqlite3")
        self.storage = LocalStorage(self.config)
        self.model_client = OpenAICompatibleClient(self.config)
        self.workspace_id = "default-workspace"
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._ensure_workspace()
        if not self.config.run_jobs_inline:
            self._start_workers()

    def shutdown(self) -> None:
        for _ in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            worker.join(timeout=1)

    def drain_jobs(self) -> None:
        while not self._queue.empty():
            job = self._queue.get_nowait()
            if job:
                self._process_job(job)
            self._queue.task_done()

    def _start_workers(self) -> None:
        worker = threading.Thread(target=self._worker_loop, name="knowledge-base-worker", daemon=True)
        worker.start()
        self._workers.append(worker)

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._process_job(job)
            finally:
                self._queue.task_done()

    def _process_job(self, job: dict[str, Any]) -> None:
        if job["kind"] == "process_import_batch":
            self._process_import_batch(job["import_batch_id"], job["conversation_id"])
        elif job["kind"] == "rerun_extraction":
            self._run_conversation_extraction(job["conversation_id"], job.get("import_batch_id"))

    def _enqueue(self, job: dict[str, Any]) -> None:
        if self.config.run_jobs_inline:
            self._process_job(job)
        else:
            self._queue.put(job)

    def _ensure_workspace(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO workspace (id, name, mode, created_at) VALUES (?, ?, ?, ?)",
                (self.workspace_id, "Personal Workspace", "single_user", now_iso()),
            )

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        if row is None:
            return {}
        return {key: row[key] for key in row.keys()}

    def _normalize_platform(self, platform: str) -> str:
        normalized = (platform or "").strip().lower()
        if normalized not in {"chatgpt", "doubao"}:
            return normalized or "unknown"
        return normalized

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        project_id = new_id()
        name = normalize_space(payload.get("name") or "Untitled Project")
        description = payload.get("description") or ""
        status = payload.get("status") or "active"
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM project WHERE workspace_id = ? AND name = ?",
                (self.workspace_id, name),
            ).fetchone()
            if existing:
                return self._project_with_counts(existing["id"])
            connection.execute(
                "INSERT INTO project (id, workspace_id, name, description, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, self.workspace_id, name, description, status, now, now),
            )
        return self._project_with_counts(project_id)

    def delete_project(self, project_id: str) -> dict[str, Any]:
        project = self.database.fetch_one(
            "SELECT * FROM project WHERE id = ? AND workspace_id = ?",
            (project_id, self.workspace_id),
        )
        if not project:
            raise ValueError("Project not found")
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM review_record WHERE target_type = ? AND target_id = ?",
                ("project", project_id),
            )
            connection.execute("DELETE FROM project WHERE id = ?", (project_id,))
        return {"deleted": True, "project_id": project_id}

    def list_projects(self) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            """
            SELECT p.*, COUNT(DISTINCT cp.conversation_id) AS conversation_count,
                   COUNT(DISTINCT kp.knowledge_id) AS knowledge_count
            FROM project p
            LEFT JOIN conversation_project cp ON cp.project_id = p.id
            LEFT JOIN knowledge_project kp ON kp.project_id = p.id
            WHERE p.workspace_id = ?
            GROUP BY p.id
            ORDER BY p.updated_at DESC, p.created_at DESC
            """,
            (self.workspace_id,),
        )
        return [self._row_to_dict(row) for row in rows]

    def _project_with_counts(self, project_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            """
            SELECT p.*, COUNT(DISTINCT cp.conversation_id) AS conversation_count,
                   COUNT(DISTINCT kp.knowledge_id) AS knowledge_count
            FROM project p
            LEFT JOIN conversation_project cp ON cp.project_id = p.id
            LEFT JOIN knowledge_project kp ON kp.project_id = p.id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (project_id,),
        )
        return self._row_to_dict(row)

    def get_overview(self) -> dict[str, Any]:
        return {
            "projects": self.list_projects(),
            "import_batches": self.list_import_batches(limit=10),
            "pending_knowledge": self.list_knowledge_units(review_status="pending"),
            "context_packs": self.list_context_packs(limit=10),
        }

    def list_import_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT * FROM import_batch ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(row) for row in rows]

    def list_context_packs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT cp.*, p.name AS project_name FROM context_pack cp JOIN project p ON p.id = cp.project_id ORDER BY cp.created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(row) for row in rows]

    def list_knowledge_units(self, review_status: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT ku.*, c.title AS conversation_title FROM knowledge_unit ku "
            "LEFT JOIN conversation c ON c.id = ku.source_conversation_id "
            "WHERE ku.workspace_id = ?"
        )
        params: list[Any] = [self.workspace_id]
        if review_status:
            query += " AND ku.review_status = ?"
            params.append(review_status)
        query += " ORDER BY ku.created_at DESC"
        rows = self.database.fetch_all(query, tuple(params))
        results = []
        for row in rows:
            item = self._row_to_dict(row)
            item["projects"] = self._knowledge_projects(item["id"])
            item["evidence"] = self._knowledge_evidence(item["id"])
            results.append(item)
        return results

    def _knowledge_projects(self, knowledge_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT p.id, p.name, kp.source, kp.confidence FROM knowledge_project kp JOIN project p ON p.id = kp.project_id WHERE kp.knowledge_id = ? ORDER BY p.name",
            (knowledge_id,),
        )
        return [self._row_to_dict(row) for row in rows]

    def _knowledge_evidence(self, knowledge_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            """
            SELECT e.*, m.text_content AS message_text, a.file_name, a.source_url
            FROM evidence_ref e
            LEFT JOIN message m ON m.id = e.message_id
            LEFT JOIN asset a ON a.id = e.asset_id
            WHERE e.knowledge_id = ?
            ORDER BY e.created_at
            """,
            (knowledge_id,),
        )
        items = []
        for row in rows:
            item = self._row_to_dict(row)
            try:
                item["locator_json"] = json.loads(item.get("locator_json") or "{}")
            except json.JSONDecodeError:
                item["locator_json"] = {}
            items.append(item)
        return items

    def delete_knowledge_unit(self, knowledge_id: str) -> dict[str, Any]:
        knowledge = self.database.fetch_one(
            "SELECT * FROM knowledge_unit WHERE id = ? AND workspace_id = ?",
            (knowledge_id, self.workspace_id),
        )
        if not knowledge:
            raise ValueError("Knowledge unit not found")
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM retrieval_chunk WHERE owner_type = ? AND owner_id = ?",
                ("knowledge_unit", knowledge_id),
            )
            connection.execute(
                "DELETE FROM review_record WHERE target_type = ? AND target_id = ?",
                ("knowledge_unit", knowledge_id),
            )
            connection.execute("DELETE FROM knowledge_unit WHERE id = ?", (knowledge_id,))
        return {"deleted": True, "knowledge_id": knowledge_id}

    def ingest_page_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        platform = self._normalize_platform(payload.get("platform", ""))
        conversation = payload.get("conversation") or {}
        if not conversation.get("messages"):
            raise ValueError("conversation.messages is required")
        return self._ingest_conversation_payload(
            platform=platform,
            import_mode="page_session",
            source_ref=conversation.get("source_url") or payload.get("source_ref") or platform,
            conversation_payload=conversation,
            raw_payload=payload,
            manual_project_ids=payload.get("target_project_ids") or [],
        )

    def ingest_chatgpt_export(self, payload: dict[str, Any]) -> dict[str, Any]:
        filename = payload.get("filename") or "chatgpt-export.zip"
        content_base64 = payload.get("content_base64")
        if not content_base64:
            raise ValueError("content_base64 is required")
        file_bytes = base64.b64decode(content_base64)
        import_path = self.storage.save_import_blob(filename, file_bytes)
        conversations = self._parse_chatgpt_export(file_bytes, filename)
        results = []
        for conversation_payload in conversations:
            result = self._ingest_conversation_payload(
                platform="chatgpt",
                import_mode="chatgpt_export",
                source_ref=import_path,
                conversation_payload=conversation_payload,
                raw_payload=conversation_payload,
                manual_project_ids=payload.get("target_project_ids") or [],
            )
            results.append(result)
        return {"file_path": import_path, "conversation_count": len(results), "batches": results}

    def _ingest_conversation_payload(
        self,
        platform: str,
        import_mode: str,
        source_ref: str,
        conversation_payload: dict[str, Any],
        raw_payload: dict[str, Any],
        manual_project_ids: list[str],
    ) -> dict[str, Any]:
        now = now_iso()
        checksum = sha256_text(json.dumps(raw_payload, ensure_ascii=False, sort_keys=True))
        import_batch_id = new_id()
        raw_payload_path = self.storage.save_json_payload("imports", import_batch_id, raw_payload)
        messages = conversation_payload.get("messages") or []
        if not messages:
            raise ValueError("Conversation contains no messages")

        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO import_batch (
                    id, workspace_id, platform, import_mode, source_ref, checksum,
                    raw_payload_path, status, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (import_batch_id, self.workspace_id, platform, import_mode, source_ref, checksum, raw_payload_path, "ingested", "", now, now),
            )
            conversation_id = self._upsert_conversation(connection, platform, conversation_payload, raw_payload_path)
            self._upsert_messages(connection, conversation_id, messages)
            if manual_project_ids:
                self._assign_projects(connection, conversation_id, manual_project_ids, source="manual", confirmed=True)

        self._enqueue({"kind": "process_import_batch", "import_batch_id": import_batch_id, "conversation_id": conversation_id})
        return {
            "import_batch_id": import_batch_id,
            "conversation_id": conversation_id,
            "status": "queued" if not self.config.run_jobs_inline else "completed",
        }

    def _upsert_conversation(self, connection, platform: str, payload: dict[str, Any], raw_payload_path: str) -> str:
        title = normalize_space(payload.get("title") or "Untitled Conversation")
        external_id = payload.get("external_id") or sha256_text(f"{platform}|{title}|{raw_payload_path}")[:24]
        source_url = payload.get("source_url") or ""
        messages = payload.get("messages") or []
        started_at = self._message_timestamp(messages[0], default=now_iso())
        last_message_at = self._message_timestamp(messages[-1], default=started_at)
        now = now_iso()
        existing = connection.execute(
            "SELECT id FROM conversation WHERE platform = ? AND external_id = ?",
            (platform, external_id),
        ).fetchone()
        if existing:
            connection.execute(
                "UPDATE conversation SET title = ?, source_url = ?, raw_payload_path = ?, started_at = ?, last_message_at = ?, updated_at = ? WHERE id = ?",
                (title, source_url, raw_payload_path, started_at, last_message_at, now, existing["id"]),
            )
            return str(existing["id"])
        conversation_id = new_id()
        connection.execute(
            """
            INSERT INTO conversation (
                id, workspace_id, platform, external_id, title, source_url,
                raw_payload_path, started_at, last_message_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, self.workspace_id, platform, external_id, title, source_url, raw_payload_path, started_at, last_message_at, now, now),
        )
        return conversation_id

    def _message_timestamp(self, payload: dict[str, Any], default: str) -> str:
        raw_value = payload.get("created_at") or payload.get("timestamp") or default
        if isinstance(raw_value, (int, float)):
            from datetime import datetime, timezone

            return datetime.fromtimestamp(raw_value, tz=timezone.utc).replace(microsecond=0).isoformat()
        return str(raw_value)

    def _upsert_messages(self, connection, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        external_to_id: dict[str, str] = {}
        now = now_iso()
        ordered = sorted(messages, key=lambda item: (item.get("seq_no") or 0, item.get("created_at") or ""))
        for index, payload in enumerate(ordered, start=1):
            text_content = combine_message_text(payload)
            external_id = payload.get("external_id") or sha256_text(f"{conversation_id}|{index}|{payload.get('role')}|{text_content}")[:32]
            row = connection.execute(
                "SELECT id FROM message WHERE conversation_id = ? AND external_id = ?",
                (conversation_id, external_id),
            ).fetchone()
            message_id = str(row["id"]) if row else new_id()
            metadata_json = coerce_json({
                "code_blocks": payload.get("code_blocks") or [],
                "links": payload.get("links") or [],
                "attachments": payload.get("assets") or [],
            })
            if row:
                connection.execute(
                    "UPDATE message SET role = ?, seq_no = ?, text_content = ?, token_estimate = ?, metadata_json = ?, created_at = ?, updated_at = ? WHERE id = ?",
                    (payload.get("role") or "assistant", payload.get("seq_no") or index, text_content, estimate_tokens(text_content), metadata_json, self._message_timestamp(payload, default=now), now, message_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO message (
                        id, conversation_id, external_id, parent_id, role, seq_no,
                        text_content, token_estimate, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (message_id, conversation_id, external_id, None, payload.get("role") or "assistant", payload.get("seq_no") or index, text_content, estimate_tokens(text_content), metadata_json, self._message_timestamp(payload, default=now), now),
                )
            external_to_id[external_id] = message_id
            self._upsert_assets(connection, message_id, payload.get("assets") or [])

        for payload in ordered:
            external_id = payload.get("external_id") or sha256_text(f"{conversation_id}|{payload.get('seq_no') or 0}|{payload.get('role')}|{combine_message_text(payload)}")[:32]
            parent_external_id = payload.get("parent_external_id")
            if parent_external_id and parent_external_id in external_to_id:
                connection.execute(
                    "UPDATE message SET parent_id = ? WHERE id = ?",
                    (external_to_id[parent_external_id], external_to_id[external_id]),
                )

    def _upsert_assets(self, connection, message_id: str, assets: list[dict[str, Any]]) -> None:
        now = now_iso()
        for payload in assets:
            file_name = payload.get("file_name") or ""
            mime_type = payload.get("mime_type") or "application/octet-stream"
            source_url = payload.get("source_url") or payload.get("url") or ""
            raw_bytes = None
            if payload.get("content_base64"):
                raw_bytes = base64.b64decode(payload["content_base64"])
            elif isinstance(payload.get("content_text"), str):
                raw_bytes = payload["content_text"].encode("utf-8")
            sha_value = payload.get("sha256") or (sha256_bytes(raw_bytes) if raw_bytes else sha256_text(f"{source_url}|{file_name}"))
            local_path = payload.get("local_path") or ""
            size_bytes = payload.get("size_bytes") or (len(raw_bytes) if raw_bytes else 0)
            if raw_bytes:
                suffix = guess_suffix(file_name, mime_type)
                local_path = self.storage.save_asset_bytes(sha_value, suffix, raw_bytes)
            existing = connection.execute(
                "SELECT id FROM asset WHERE message_id = ? AND sha256 = ? AND file_name = ?",
                (message_id, sha_value, file_name),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE asset SET kind = ?, mime_type = ?, local_path = ?, source_url = ?, size_bytes = ?, status = ?, updated_at = ? WHERE id = ?",
                    (payload.get("kind") or "attachment", mime_type, local_path, source_url, size_bytes, "stored" if local_path else "indexed", now, existing["id"]),
                )
                continue
            connection.execute(
                """
                INSERT INTO asset (
                    id, message_id, kind, mime_type, file_name, local_path, source_url,
                    sha256, size_bytes, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id(), message_id, payload.get("kind") or "attachment", mime_type, file_name, local_path, source_url, sha_value, size_bytes, "stored" if local_path else "indexed", now, now),
            )

    def _parse_chatgpt_export(self, content: bytes, filename: str) -> list[dict[str, Any]]:
        if zipfile.is_zipfile(io.BytesIO(content)):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                json_name = next((name for name in archive.namelist() if name.endswith("conversations.json")), next((name for name in archive.namelist() if name.endswith(".json")), None))
                if not json_name:
                    raise ValueError("No JSON conversation file found in export archive")
                raw_json = archive.read(json_name)
        else:
            raw_json = content
        payload = json.loads(raw_json.decode("utf-8"))
        conversations = payload.get("conversations") if isinstance(payload, dict) else payload
        if not isinstance(conversations, list):
            raise ValueError("Unsupported ChatGPT export format")
        normalized: list[dict[str, Any]] = []
        for conversation in conversations:
            mapping = conversation.get("mapping") or {}
            messages = []
            for node_id, node in mapping.items():
                node = node or {}
                message = node.get("message") or {}
                author = (message.get("author") or {}).get("role")
                content_payload = message.get("content") or {}
                parts = content_payload.get("parts") or []
                text_parts = [part for part in parts if isinstance(part, str)]
                text_content = "\n".join(text_parts).strip()
                if not text_content:
                    continue
                messages.append(
                    {
                        "external_id": node_id,
                        "parent_external_id": node.get("parent"),
                        "role": author or "assistant",
                        "seq_no": 0,
                        "text_content": text_content,
                        "created_at": message.get("create_time") or conversation.get("create_time") or now_iso(),
                        "assets": [],
                    }
                )
            messages.sort(key=lambda item: item.get("created_at") or "")
            for index, message in enumerate(messages, start=1):
                message["seq_no"] = index
            normalized.append(
                {
                    "external_id": conversation.get("id") or sha256_text(json.dumps(conversation, sort_keys=True))[:24],
                    "title": conversation.get("title") or filename,
                    "source_url": conversation.get("url") or "",
                    "messages": messages,
                }
            )
        return normalized

    def _process_import_batch(self, import_batch_id: str, conversation_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE import_batch SET status = ?, updated_at = ? WHERE id = ?",
                ("processing", now_iso(), import_batch_id),
            )
        try:
            self._analyze_conversation_assets(conversation_id)
            self._run_conversation_extraction(conversation_id, import_batch_id)
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE import_batch SET status = ?, updated_at = ? WHERE id = ?",
                    ("completed", now_iso(), import_batch_id),
                )
        except Exception as exc:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE import_batch SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                    ("failed", str(exc), now_iso(), import_batch_id),
                )
            raise

    def _analyze_conversation_assets(self, conversation_id: str) -> None:
        assets = self.database.fetch_all(
            """
            SELECT a.*, m.text_content AS message_text
            FROM asset a
            JOIN message m ON m.id = a.message_id
            WHERE m.conversation_id = ?
            ORDER BY m.seq_no, a.created_at
            """,
            (conversation_id,),
        )
        for asset_row in assets:
            asset = self._row_to_dict(asset_row)
            raw_bytes = None
            if asset.get("local_path"):
                path = self.storage.resolve(asset["local_path"])
                if Path(path).exists():
                    raw_bytes = Path(path).read_bytes()
            provider = "heuristic"
            extracted_text = ""
            structured_json: dict[str, Any] = {}
            if raw_bytes and asset.get("kind") == "image":
                vision_result = self.model_client.analyze_image(asset.get("mime_type") or "image/png", raw_bytes)
                if vision_result:
                    provider = "external_model"
                    extracted_text = normalize_space(str(vision_result.get("summary") or json.dumps(vision_result, ensure_ascii=False)))
                    structured_json = vision_result
            if not extracted_text:
                fallback = analyze_asset_fallback(asset, raw_bytes)
                extracted_text = fallback["extracted_text"]
                structured_json = fallback["structured_json"]
            with self.database.transaction() as connection:
                version_row = connection.execute(
                    "SELECT COALESCE(MAX(version_no), 0) AS max_version FROM asset_analysis WHERE asset_id = ?",
                    (asset["id"],),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO asset_analysis (
                        id, asset_id, version_no, parser_type, provider,
                        extracted_text, structured_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        asset["id"],
                        int(version_row["max_version"]) + 1,
                        "multimodal" if asset.get("kind") == "image" else "document",
                        provider,
                        extracted_text,
                        coerce_json(structured_json),
                        "completed" if extracted_text else "failed",
                        now_iso(),
                    ),
                )
                connection.execute(
                    "UPDATE asset SET status = ?, updated_at = ? WHERE id = ?",
                    ("analyzed" if extracted_text else "failed", now_iso(), asset["id"]),
                )

    def _run_conversation_extraction(self, conversation_id: str, import_batch_id: str | None) -> None:
        conversation_row = self.database.fetch_one("SELECT * FROM conversation WHERE id = ?", (conversation_id,))
        if not conversation_row:
            raise ValueError("Conversation not found")
        conversation = self._row_to_dict(conversation_row)
        message_rows = self.database.fetch_all("SELECT * FROM message WHERE conversation_id = ? ORDER BY seq_no", (conversation_id,))
        messages = [self._row_to_dict(row) for row in message_rows]
        analyses = self.database.fetch_all(
            """
            SELECT aa.*, a.id AS asset_id, a.message_id
            FROM asset_analysis aa
            JOIN asset a ON a.id = aa.asset_id
            JOIN message m ON m.id = a.message_id
            WHERE m.conversation_id = ?
            ORDER BY aa.created_at DESC
            """,
            (conversation_id,),
        )
        conversation_text = "\n\n".join(
            [f"Conversation Title: {conversation['title']}"]
            + [f"{message['role']}: {message['text_content']}" for message in messages]
            + [f"Asset Analysis: {row['extracted_text']}" for row in analyses[:8]]
        )
        model_items = self.model_client.extract_knowledge(conversation_text)
        units = model_items or extract_knowledge_units(messages)

        existing_projects = [self._row_to_dict(row) for row in self.database.fetch_all("SELECT * FROM project WHERE workspace_id = ?", (self.workspace_id,))]
        recommended = recommend_projects(conversation["title"], messages, existing_projects)
        if not recommended:
            project = self.create_project({"name": derive_project_name(conversation["title"], messages), "status": "suggested"})
            recommended = [{"project_id": project["id"], "confidence": 0.6}]

        with self.database.transaction() as connection:
            self._assign_projects(
                connection,
                conversation_id,
                [item["project_id"] for item in recommended],
                source="auto",
                confirmed=False,
                confidence_map={item["project_id"]: item["confidence"] for item in recommended},
            )
            project_ids = [row["project_id"] for row in connection.execute("SELECT project_id FROM conversation_project WHERE conversation_id = ?", (conversation_id,)).fetchall()]

            for unit in units:
                knowledge_id = self._upsert_knowledge_unit(connection, conversation_id, import_batch_id, unit)
                self._bind_evidence(connection, knowledge_id, messages, unit)
                for project_id in project_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO knowledge_project (knowledge_id, project_id, source, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                        (knowledge_id, project_id, "auto", 0.7, now_iso()),
                    )

            message_ids = tuple(message["id"] for message in messages)
            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                connection.execute(
                    f"DELETE FROM retrieval_chunk WHERE owner_type = 'message' AND owner_id IN ({placeholders})",
                    message_ids,
                )
            latest_knowledge = connection.execute(
                "SELECT * FROM knowledge_unit WHERE source_conversation_id = ? ORDER BY created_at DESC",
                (conversation_id,),
            ).fetchall()
            chunks = build_retrieval_chunks(conversation, messages, [self._row_to_dict(row) for row in latest_knowledge])
            for chunk in chunks:
                connection.execute(
                    "INSERT INTO retrieval_chunk (id, owner_type, owner_id, text, vector_ref, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), chunk["owner_type"], chunk["owner_id"], chunk["text"], "", coerce_json(chunk["metadata_json"]), now_iso()),
                )

    def _assign_projects(
        self,
        connection,
        conversation_id: str,
        project_ids: list[str],
        source: str,
        confirmed: bool,
        confidence_map: dict[str, float] | None = None,
    ) -> None:
        confidence_map = confidence_map or {}
        timestamp = now_iso()
        for project_id in project_ids:
            connection.execute(
                """
                INSERT OR REPLACE INTO conversation_project (
                    conversation_id, project_id, source, confidence, confirmed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, project_id, source, confidence_map.get(project_id, 1.0 if source == "manual" else 0.5), timestamp if confirmed else None, timestamp),
            )
            if confirmed:
                connection.execute(
                    "UPDATE project SET status = ?, updated_at = ? WHERE id = ?",
                    ("active", timestamp, project_id),
                )

    def _upsert_knowledge_unit(self, connection, conversation_id: str, import_batch_id: str | None, unit: dict[str, Any]) -> str:
        canonical_id = sha256_text(f"{conversation_id}|{unit['type']}|{normalize_space(unit['body']).lower()}")
        existing = connection.execute(
            "SELECT * FROM knowledge_unit WHERE canonical_id = ? ORDER BY version_no DESC LIMIT 1",
            (canonical_id,),
        ).fetchone()
        if existing and existing["body"] == unit["body"] and existing["type"] == unit["type"]:
            return str(existing["id"])
        version_no = (int(existing["version_no"]) + 1) if existing else 1
        knowledge_id = new_id()
        timestamp = now_iso()
        connection.execute(
            """
            INSERT INTO knowledge_unit (
                id, canonical_id, version_no, workspace_id, source_conversation_id, source_import_batch_id,
                type, title, body, stability, review_status, supersedes_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (knowledge_id, canonical_id, version_no, self.workspace_id, conversation_id, import_batch_id, unit["type"], unit["title"], unit["body"], unit.get("stability") or "stable", "pending", existing["id"] if existing else None, timestamp, timestamp),
        )
        return knowledge_id

    def _bind_evidence(self, connection, knowledge_id: str, messages: list[dict[str, Any]], unit: dict[str, Any]) -> None:
        message = None
        if unit.get("message_external_id"):
            message = next((item for item in messages if item.get("external_id") == unit["message_external_id"]), None)
        if not message:
            quote = unit.get("evidence_quote") or unit.get("body") or ""
            message = next((item for item in messages if quote[:40] in item.get("text_content", "")), None)
        locator = {"quote": unit.get("evidence_quote") or unit.get("body")}
        if message:
            span_text = unit.get("evidence_quote") or unit.get("body") or ""
            start = message.get("text_content", "").find(span_text[:80])
            end = start + len(span_text[:80]) if start >= 0 else None
            connection.execute(
                "INSERT INTO evidence_ref (id, knowledge_id, message_id, asset_id, span_start, span_end, locator_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), knowledge_id, message["id"], None, start if start >= 0 else None, end, coerce_json(locator), now_iso()),
            )

    def run_extraction(self, payload: dict[str, Any]) -> dict[str, Any]:
        conversation_ids = payload.get("conversation_ids") or []
        import_batch_ids = payload.get("import_batch_ids") or []
        scheduled = []
        for import_batch_id in import_batch_ids:
            row = self.database.fetch_one("SELECT * FROM import_batch WHERE id = ?", (import_batch_id,))
            if row:
                conversation_row = self.database.fetch_one(
                    "SELECT id FROM conversation WHERE raw_payload_path = ? ORDER BY updated_at DESC LIMIT 1",
                    (row["raw_payload_path"],),
                )
                if conversation_row:
                    conversation_ids.append(conversation_row["id"])
        for conversation_id in list(dict.fromkeys(conversation_ids)):
            self._enqueue({"kind": "rerun_extraction", "conversation_id": conversation_id, "import_batch_id": None})
            scheduled.append(conversation_id)
        return {"scheduled": scheduled}

    def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        target_type = payload.get("target_type")
        target_id = payload.get("target_id")
        action = payload.get("action")
        editor_note = payload.get("editor_note") or ""
        if target_type == "knowledge_unit":
            return self._review_knowledge_unit(target_id, action, payload, editor_note)
        if target_type == "project":
            return self._review_project(target_id, action, payload, editor_note)
        raise ValueError("Unsupported review target")

    def _review_knowledge_unit(self, target_id: str, action: str, payload: dict[str, Any], editor_note: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM knowledge_unit WHERE id = ?", (target_id,))
        if not row:
            raise ValueError("Knowledge unit not found")
        item = self._row_to_dict(row)
        with self.database.transaction() as connection:
            if action == "approve":
                if payload.get("updated_fields"):
                    new_item = dict(item)
                    new_item.update(payload["updated_fields"])
                    target_id = self._create_revised_knowledge(connection, item, new_item, review_status="approved")
                else:
                    connection.execute(
                        "UPDATE knowledge_unit SET review_status = ?, updated_at = ? WHERE id = ?",
                        ("approved", now_iso(), target_id),
                    )
            elif action == "reject":
                connection.execute(
                    "UPDATE knowledge_unit SET review_status = ?, updated_at = ? WHERE id = ?",
                    ("rejected", now_iso(), target_id),
                )
            elif action == "revise":
                revised = dict(item)
                revised.update(payload.get("updated_fields") or {})
                target_id = self._create_revised_knowledge(
                    connection,
                    item,
                    revised,
                    review_status=payload.get("review_status") or "approved",
                )
            else:
                raise ValueError("Unsupported knowledge review action")

            if payload.get("project_ids"):
                for project_id in payload["project_ids"]:
                    connection.execute(
                        "INSERT OR IGNORE INTO knowledge_project (knowledge_id, project_id, source, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                        (target_id, project_id, "manual", 1.0, now_iso()),
                    )
            connection.execute(
                "INSERT INTO review_record (id, target_type, target_id, action, editor_note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), "knowledge_unit", target_id, action, editor_note, now_iso()),
            )
        response = self.database.fetch_one("SELECT * FROM knowledge_unit WHERE id = ?", (target_id,))
        item = self._row_to_dict(response)
        item["projects"] = self._knowledge_projects(target_id)
        item["evidence"] = self._knowledge_evidence(target_id)
        return item

    def _create_revised_knowledge(self, connection, original: dict[str, Any], revised: dict[str, Any], review_status: str) -> str:
        new_knowledge_id = new_id()
        version_no = int(original["version_no"]) + 1
        connection.execute(
            """
            INSERT INTO knowledge_unit (
                id, canonical_id, version_no, workspace_id, source_conversation_id, source_import_batch_id,
                type, title, body, stability, review_status, supersedes_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_knowledge_id, original["canonical_id"], version_no, original["workspace_id"], original["source_conversation_id"], original["source_import_batch_id"], revised.get("type") or original["type"], revised.get("title") or original["title"], revised.get("body") or original["body"], revised.get("stability") or original["stability"], review_status, original["id"], now_iso(), now_iso()),
        )
        for project in self._knowledge_projects(original["id"]):
            connection.execute(
                "INSERT OR IGNORE INTO knowledge_project (knowledge_id, project_id, source, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_knowledge_id, project["id"], project["source"], project["confidence"], now_iso()),
            )
        for evidence in self._knowledge_evidence(original["id"]):
            connection.execute(
                "INSERT INTO evidence_ref (id, knowledge_id, message_id, asset_id, span_start, span_end, locator_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), new_knowledge_id, evidence.get("message_id"), evidence.get("asset_id"), evidence.get("span_start"), evidence.get("span_end"), coerce_json(evidence.get("locator_json") or {}), now_iso()),
            )
        return new_knowledge_id

    def _review_project(self, target_id: str, action: str, payload: dict[str, Any], editor_note: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            if action == "confirm":
                connection.execute(
                    "UPDATE project SET status = ?, updated_at = ? WHERE id = ?",
                    ("active", now_iso(), target_id),
                )
            elif action == "rename":
                connection.execute(
                    "UPDATE project SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                    (payload.get("name"), payload.get("description") or "", now_iso(), target_id),
                )
            else:
                raise ValueError("Unsupported project review action")
            connection.execute(
                "INSERT INTO review_record (id, target_type, target_id, action, editor_note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), "project", target_id, action, editor_note, now_iso()),
            )
        return self._project_with_counts(target_id)

    def assign_conversation_projects(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        project_ids = list(payload.get("project_ids") or [])
        if payload.get("project_name"):
            project = self.create_project({"name": payload["project_name"], "status": "active"})
            project_ids.append(project["id"])
        with self.database.transaction() as connection:
            self._assign_projects(connection, conversation_id, project_ids, source="manual", confirmed=True)
            if payload.get("apply_to_knowledge", True):
                knowledge_rows = connection.execute(
                    "SELECT id FROM knowledge_unit WHERE source_conversation_id = ?",
                    (conversation_id,),
                ).fetchall()
                for knowledge_row in knowledge_rows:
                    for project_id in project_ids:
                        connection.execute(
                            "INSERT OR IGNORE INTO knowledge_project (knowledge_id, project_id, source, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                            (knowledge_row["id"], project_id, "manual", 1.0, now_iso()),
                        )
        return {"conversation_id": conversation_id, "project_ids": project_ids}

    def generate_context_pack(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = payload.get("project_id")
        template_type = payload.get("template_type") or "merge-conclusions"
        task_goal = payload.get("task_goal") or "Continue the project"
        budget_mode = payload.get("budget_mode") or "chars"
        budget_value = int(payload.get("budget_value") or 2400)
        reviewed_only = bool(payload.get("reviewed_only", True))
        project = self._project_with_counts(project_id)
        knowledge_units = self._latest_project_knowledge(project_id, reviewed_only=reviewed_only)
        output_text, selected_units = render_context_pack(project, template_type, task_goal, budget_mode, budget_value, knowledge_units)
        canonical_id = canonical_context_pack_id(project_id, template_type, task_goal)
        existing = self.database.fetch_one("SELECT MAX(version_no) AS max_version FROM context_pack WHERE canonical_id = ?", (canonical_id,))
        version_no = int(existing["max_version"] or 0) + 1
        context_pack_id = new_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO context_pack (
                    id, canonical_id, version_no, project_id, template_type, task_goal,
                    budget_mode, budget_value, reviewed_only, output_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (context_pack_id, canonical_id, version_no, project_id, template_type, task_goal, budget_mode, budget_value, 1 if reviewed_only else 0, output_text, now_iso()),
            )
            for rank, unit in enumerate(selected_units, start=1):
                connection.execute(
                    "INSERT INTO context_pack_item (context_pack_id, knowledge_id, source_rank, inclusion_reason) VALUES (?, ?, ?, ?)",
                    (context_pack_id, unit["id"], rank, f"Matched {template_type} ranking"),
                )
        return {"id": context_pack_id, "canonical_id": canonical_id, "version_no": version_no, "output_text": output_text, "items": selected_units}

    def _latest_project_knowledge(self, project_id: str, reviewed_only: bool) -> list[dict[str, Any]]:
        query = """
            SELECT ku.*
            FROM knowledge_unit ku
            JOIN knowledge_project kp ON kp.knowledge_id = ku.id
            WHERE kp.project_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM knowledge_unit newer
                  WHERE newer.canonical_id = ku.canonical_id AND newer.version_no > ku.version_no
              )
        """
        if reviewed_only:
            query += " AND ku.review_status = 'approved'"
        rows = self.database.fetch_all(query, (project_id,))
        return [self._row_to_dict(row) for row in rows]

    def get_project_timeline(self, project_id: str) -> dict[str, Any]:
        project = self._project_with_counts(project_id)
        conversations = self.database.fetch_all(
            "SELECT c.*, cp.source, cp.confidence, cp.confirmed_at FROM conversation_project cp JOIN conversation c ON c.id = cp.conversation_id WHERE cp.project_id = ? ORDER BY c.last_message_at DESC",
            (project_id,),
        )
        knowledge = self._latest_project_knowledge(project_id, reviewed_only=False)
        context_packs = self.database.fetch_all(
            "SELECT * FROM context_pack WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        return {
            "project": project,
            "conversations": [self._row_to_dict(row) for row in conversations],
            "knowledge_units": knowledge,
            "context_packs": [self._row_to_dict(row) for row in context_packs],
        }

    def search(self, query: str, project_id: str | None = None) -> list[dict[str, Any]]:
        term = f"%{query.lower()}%"
        rows = self.database.fetch_all(
            "SELECT * FROM retrieval_chunk WHERE LOWER(text) LIKE ? ORDER BY created_at DESC LIMIT 50",
            (term,),
        )
        results = []
        for row in rows:
            item = self._row_to_dict(row)
            try:
                item["metadata_json"] = json.loads(item.get("metadata_json") or "{}")
            except json.JSONDecodeError:
                item["metadata_json"] = {}
            if project_id and item["owner_type"] == "knowledge_unit":
                linked = self.database.fetch_one(
                    "SELECT 1 FROM knowledge_project WHERE knowledge_id = ? AND project_id = ?",
                    (item["owner_id"], project_id),
                )
                if not linked:
                    continue
            results.append(item)
        return results
