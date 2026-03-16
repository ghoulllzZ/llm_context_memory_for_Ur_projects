from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.config import AppConfig
from app.service import KnowledgeBaseService


class KnowledgeBaseWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.config = AppConfig(
            root_dir=root,
            data_dir=root / "data",
            raw_dir=root / "data" / "raw",
            asset_dir=root / "data" / "assets",
            import_dir=root / "data" / "imports",
            host="127.0.0.1",
            port=8765,
            run_jobs_inline=True,
            llm_api_url=None,
            llm_api_key=None,
            llm_text_model=None,
            llm_vision_model=None,
        )
        self.service = KnowledgeBaseService(self.config)

    def tearDown(self) -> None:
        self.service.shutdown()
        self.tempdir.cleanup()

    def _sample_page_payload(self) -> dict:
        fake_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nResearch Plan\nmetric: F1=0.82\n"
        return {
            "platform": "chatgpt",
            "conversation": {
                "external_id": "conv-alpha",
                "title": "Alpha Retrieval Project",
                "source_url": "https://chatgpt.com/c/conv-alpha",
                "messages": [
                    {
                        "external_id": "m1",
                        "seq_no": 1,
                        "role": "user",
                        "text_content": "项目 Alpha 需要继续论文写作。必须保持实验指标 F1 为 0.82 以上。",
                        "assets": [],
                    },
                    {
                        "external_id": "m2",
                        "seq_no": 2,
                        "role": "assistant",
                        "text_content": "决定采用 RAG 方法，并使用 BM25 + reranker。TODO: 补充 baseline 对比。",
                        "assets": [
                            {
                                "kind": "pdf",
                                "mime_type": "application/pdf",
                                "file_name": "plan.pdf",
                                "content_base64": base64.b64encode(fake_pdf).decode("ascii"),
                            }
                        ],
                    },
                ],
            },
        }

    def _sample_export_payload(self) -> dict:
        conversations = [
            {
                "id": "conv-alpha",
                "title": "Alpha Retrieval Project",
                "mapping": {
                    "node-1": {
                        "id": "node-1",
                        "parent": None,
                        "message": {
                            "author": {"role": "user"},
                            "create_time": 1700000000,
                            "content": {"parts": ["项目 Alpha 需要继续论文写作。必须保持实验指标 F1 为 0.82 以上。"]},
                        },
                    },
                    "node-2": {
                        "id": "node-2",
                        "parent": "node-1",
                        "message": {
                            "author": {"role": "assistant"},
                            "create_time": 1700000001,
                            "content": {"parts": ["决定采用 RAG 方法，并使用 BM25 + reranker。TODO: 补充 baseline 对比。"]},
                        },
                    },
                },
            }
        ]
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("conversations.json", json.dumps(conversations, ensure_ascii=False))
        return {
            "filename": "chatgpt-export.zip",
            "content_base64": base64.b64encode(memory.getvalue()).decode("ascii"),
        }

    def test_import_review_and_context_pack_workflow(self) -> None:
        result = self.service.ingest_page_session(self._sample_page_payload())
        self.assertEqual(result["status"], "completed")

        imports = self.service.list_import_batches()
        self.assertEqual(imports[0]["status"], "completed")

        projects = self.service.list_projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["status"], "suggested")

        pending = self.service.list_knowledge_units(review_status="pending")
        self.assertGreaterEqual(len(pending), 2)

        for unit in pending:
            self.service.review({"target_type": "knowledge_unit", "target_id": unit["id"], "action": "approve"})

        generated = self.service.generate_context_pack(
            {
                "project_id": projects[0]["id"],
                "template_type": "continue-paper-writing",
                "task_goal": "Continue drafting the methods section.",
                "budget_mode": "chars",
                "budget_value": 2400,
                "reviewed_only": True,
            }
        )
        self.assertIn("Project:", generated["output_text"])
        self.assertIn("RAG", generated["output_text"])

        analyses = self.service.database.fetch_all("SELECT * FROM asset_analysis")
        self.assertEqual(len(analyses), 1)

    def test_unreviewed_knowledge_is_excluded_from_reviewed_context_pack(self) -> None:
        self.service.ingest_page_session(self._sample_page_payload())
        project = self.service.list_projects()[0]

        generated = self.service.generate_context_pack(
            {
                "project_id": project["id"],
                "template_type": "merge-conclusions",
                "task_goal": "Summarize validated conclusions.",
                "budget_mode": "chars",
                "budget_value": 1600,
                "reviewed_only": True,
            }
        )
        self.assertIn("No approved knowledge units matched", generated["output_text"])

    def test_chatgpt_export_merges_into_existing_conversation(self) -> None:
        self.service.ingest_page_session(self._sample_page_payload())
        self.service.ingest_chatgpt_export(self._sample_export_payload())
        conversations = self.service.database.fetch_all("SELECT * FROM conversation")
        self.assertEqual(len(conversations), 1)
        messages = self.service.database.fetch_all("SELECT * FROM message")
        self.assertGreaterEqual(len(messages), 2)

    def test_delete_project_keeps_imported_conversation(self) -> None:
        self.service.ingest_page_session(self._sample_page_payload())
        project = self.service.list_projects()[0]
        result = self.service.delete_project(project["id"])
        self.assertTrue(result["deleted"])
        self.assertEqual(self.service.list_projects(), [])
        conversations = self.service.database.fetch_all("SELECT * FROM conversation")
        self.assertEqual(len(conversations), 1)

    def test_delete_pending_knowledge_unit_removes_it_from_queue(self) -> None:
        self.service.ingest_page_session(self._sample_page_payload())
        pending = self.service.list_knowledge_units(review_status="pending")
        self.assertGreaterEqual(len(pending), 1)
        target_id = pending[0]["id"]
        result = self.service.delete_knowledge_unit(target_id)
        self.assertTrue(result["deleted"])
        remaining = self.service.list_knowledge_units(review_status="pending")
        self.assertFalse(any(item["id"] == target_id for item in remaining))


if __name__ == "__main__":
    unittest.main()
