from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import AppConfig
from .service import KnowledgeBaseService


class KnowledgeBaseHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, service: KnowledgeBaseService):
        super().__init__(server_address, RequestHandlerClass)
        self.service = service
        self.static_dir = Path(__file__).resolve().parent / "static"


class KnowledgeBaseHandler(BaseHTTPRequestHandler):
    server: KnowledgeBaseHTTPServer

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/overview":
            return self._handle_json(self.server.service.get_overview())
        if parsed.path == "/api/projects":
            return self._handle_json(self.server.service.list_projects())
        if parsed.path == "/api/import-batches":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["50"])[0])
            return self._handle_json(self.server.service.list_import_batches(limit=limit))
        if parsed.path == "/api/knowledge-units":
            params = parse_qs(parsed.query)
            status = params.get("review_status", [None])[0]
            return self._handle_json(self.server.service.list_knowledge_units(review_status=status))
        if parsed.path == "/api/context-packs":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["50"])[0])
            return self._handle_json(self.server.service.list_context_packs(limit=limit))
        if parsed.path == "/api/search":
            params = parse_qs(parsed.query)
            return self._handle_json(self.server.service.search(params.get("q", [""])[0], params.get("project_id", [None])[0]))
        if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/timeline"):
            project_id = parsed.path.split("/")[3]
            return self._handle_json(self.server.service.get_project_timeline(project_id))
        return self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json_body()
        try:
            if parsed.path == "/api/projects":
                return self._handle_json(self.server.service.create_project(payload), status=HTTPStatus.CREATED)
            if parsed.path == "/api/ingest/page-session":
                return self._handle_json(self.server.service.ingest_page_session(payload), status=HTTPStatus.ACCEPTED)
            if parsed.path == "/api/ingest/chatgpt-export":
                return self._handle_json(self.server.service.ingest_chatgpt_export(payload), status=HTTPStatus.ACCEPTED)
            if parsed.path == "/api/extraction/runs":
                return self._handle_json(self.server.service.run_extraction(payload), status=HTTPStatus.ACCEPTED)
            if parsed.path == "/api/reviews":
                return self._handle_json(self.server.service.review(payload))
            if parsed.path == "/api/context-packs/generate":
                return self._handle_json(self.server.service.generate_context_pack(payload), status=HTTPStatus.CREATED)
            if parsed.path.startswith("/api/conversations/") and parsed.path.endswith("/projects"):
                conversation_id = parsed.path.split("/")[3]
                return self._handle_json(self.server.service.assign_conversation_projects(conversation_id, payload))
            raise ValueError(f"Unknown route: {parsed.path}")
        except ValueError as exc:
            self._handle_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._handle_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/projects/"):
                project_id = parsed.path.split("/")[3]
                return self._handle_json(self.server.service.delete_project(project_id))
            if parsed.path.startswith("/api/knowledge-units/"):
                knowledge_id = parsed.path.split("/")[3]
                return self._handle_json(self.server.service.delete_knowledge_unit(knowledge_id))
            raise ValueError(f"Unknown route: {parsed.path}")
        except ValueError as exc:
            self._handle_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._handle_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        raw_body = self.rfile.read(length)
        if not raw_body:
            return {}
        return json.loads(raw_body.decode("utf-8"))

    def _handle_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

    def _serve_static(self, path: str) -> None:
        relative_path = path.lstrip("/") or "index.html"
        if relative_path == "":
            relative_path = "index.html"
        target = (self.server.static_dir / relative_path).resolve()
        if not str(target).startswith(str(self.server.static_dir.resolve())) or not target.exists() or target.is_dir():
            target = self.server.static_dir / "index.html"
        content = target.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(target))
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", f"{mime_type or 'text/plain'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


def create_server(config: AppConfig | None = None) -> tuple[KnowledgeBaseHTTPServer, KnowledgeBaseService]:
    app_config = config or AppConfig.from_env()
    service = KnowledgeBaseService(app_config)
    server = KnowledgeBaseHTTPServer((app_config.host, app_config.port), KnowledgeBaseHandler, service)
    return server, service


def run() -> None:
    server, service = create_server()
    address = server.server_address
    print(f"Knowledge base running on http://{address[0]}:{address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
