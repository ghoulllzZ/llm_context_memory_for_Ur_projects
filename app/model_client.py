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

    def generate_context_pack_markdown(
        self,
        project: dict[str, Any],
        template_type: str,
        task_goal: str,
        knowledge_units: list[dict[str, Any]],
    ) -> str | None:
        if not (self.enabled and self.config.llm_api_url and knowledge_units):
            return None
        payload = {
            "project_name": project.get("name") or "Untitled Project",
            "template_type": template_type,
            "task_goal": task_goal,
            "knowledge_units": [
                {
                    "type": unit.get("type"),
                    "title": unit.get("title"),
                    "body": unit.get("body"),
                    "stability": unit.get("stability"),
                }
                for unit in knowledge_units[:18]
            ],
        }
        response = self._request(
            {
                "model": self.config.llm_text_model,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Generate a reusable research context pack for the next LLM session. "
                            "Return Markdown only, no code fences. "
                            "Do deep synthesis instead of copying the chat wording. "
                            "Stay grounded in the provided knowledge; if something is missing, say it is not explicitly established. "
                            "Use this structure: Context Pack -> 0) Project Name -> 1) Research Goals and Problem -> 2) Core Method and Technical Route -> 3) Key Objects, Structured Elements, and Inputs/Outputs -> 4) Metrics, Experiments, and Validation -> 5) Current Conclusions, Open Questions, and Next Steps -> 6) Implementation and Reproduction Notes -> 7) Instructions for the Next LLM -> End of Context Pack."
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            }
        )
        if not response:
            return None
        try:
            return str(response["choices"][0]["message"]["content"]).strip()
        except (KeyError, TypeError):
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
