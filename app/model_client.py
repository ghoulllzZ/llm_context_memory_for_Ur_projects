from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

from .config import AppConfig


class OpenAICompatibleClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.llm_api_url and self.config.llm_api_key and self.config.llm_text_model)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled or not self.config.llm_api_url:
            return None
        request = urllib.request.Request(
            self.config.llm_api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.llm_api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None

    def extract_knowledge(self, conversation_text: str) -> list[dict[str, Any]] | None:
        response = self._request(
            {
                "model": self.config.llm_text_model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Extract stable project knowledge from the conversation. "
                            "Return JSON with key 'items'. Each item must contain: "
                            "type, title, body, stability, evidence_quote."
                        ),
                    },
                    {"role": "user", "content": conversation_text[:12000]},
                ],
            }
        )
        if not response:
            return None
        try:
            content = response["choices"][0]["message"]["content"]
            payload = json.loads(content)
            items = payload.get("items", [])
            return [item for item in items if isinstance(item, dict)]
        except (KeyError, TypeError, json.JSONDecodeError):
            return None

    def analyze_image(self, mime_type: str, content: bytes) -> dict[str, Any] | None:
        if not (self.enabled and self.config.llm_vision_model and self.config.llm_api_url):
            return None
        encoded = base64.b64encode(content).decode("ascii")
        response = self._request(
            {
                "model": self.config.llm_vision_model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Analyze the image for research context preservation. "
                            "Return JSON with keys summary, sections, tables, parameters, entities."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analyze this asset for reusable project knowledge."},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{encoded}",
                                },
                            },
                        ],
                    },
                ],
            }
        )
        if not response:
            return None
        try:
            content_text = response["choices"][0]["message"]["content"]
            return json.loads(content_text)
        except (KeyError, TypeError, json.JSONDecodeError):
            return None
