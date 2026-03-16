from __future__ import annotations

import hashlib
import json
from typing import Any

from .config import AppConfig


class LocalStorage:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.config.ensure_directories()

    def save_json_payload(self, namespace: str, identifier: str, payload: Any) -> str:
        target_dir = self.config.raw_dir / namespace
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{identifier}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path.relative_to(self.config.root_dir))

    def save_import_blob(self, filename: str, content: bytes) -> str:
        safe_name = filename.replace("/", "_").replace("\\", "_")
        digest = hashlib.sha256(content).hexdigest()
        path = self.config.import_dir / f"{digest[:12]}_{safe_name}"
        path.write_bytes(content)
        return str(path.relative_to(self.config.root_dir))

    def save_asset_bytes(self, sha256_hex: str, suffix: str, content: bytes) -> str:
        target_dir = self.config.asset_dir / sha256_hex[:2]
        target_dir.mkdir(parents=True, exist_ok=True)
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}" if suffix else ""
        path = target_dir / f"{sha256_hex}{normalized_suffix}"
        if not path.exists():
            path.write_bytes(content)
        return str(path.relative_to(self.config.root_dir))

    def resolve(self, relative_path: str):
        return self.config.root_dir / relative_path
