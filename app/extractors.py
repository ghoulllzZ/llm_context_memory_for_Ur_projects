from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any


KNOWLEDGE_TYPES = {
    "Decision": ["决定", "采用", "选用", "confirmed", "decide", "decision", "we will", "结论"],
    "Constraint": ["必须", "约束", "限制", "不能", "requirement", "constraint", "should not", "must"],
    "Metric": ["指标", "accuracy", "precision", "recall", "f1", "metric", "评价", "评分", "loss"],
    "Method": ["方法", "流程", "pipeline", "approach", "algorithm", "method", "步骤"],
    "ExperimentSetup": ["实验", "dataset", "参数", "batch", "epoch", "temperature", "seed", "配置"],
    "CodeDesign": ["api", "schema", "函数", "类", "模块", "service", "database", "表", "代码", "接口"],
    "PromptTemplate": ["prompt", "提示词", "template", "system prompt", "user prompt"],
    "RejectedOption": ["不采用", "否决", "reject", "deprecated", "不要", "放弃", "discard"],
    "TODO": ["todo", "待办", "next step", "follow-up", "下一步", "需要补充"],
    "ReferenceLead": ["http://", "https://", "paper", "doi", "参考", "文献", "citation"],
}

TEMPLATE_WEIGHTS = {
    "continue-paper-writing": {
        "Method": 6,
        "Metric": 5,
        "Constraint": 5,
        "Decision": 4,
        "ReferenceLead": 4,
        "RejectedOption": 3,
        "TODO": 3,
        "ExperimentSetup": 3,
    },
    "continue-experiment-design": {
        "ExperimentSetup": 6,
        "Metric": 6,
        "Method": 5,
        "Constraint": 5,
        "Decision": 4,
        "RejectedOption": 4,
        "TODO": 3,
    },
    "merge-conclusions": {
        "Decision": 6,
        "Constraint": 5,
        "Metric": 4,
        "Method": 4,
        "RejectedOption": 4,
        "CodeDesign": 3,
        "TODO": 2,
    },
}

GENERIC_TITLES = {"new chat", "新对话", "chatgpt", "doubao", "untitled", "未命名"}
STOPWORDS = {
    "the", "and", "that", "with", "from", "into", "about", "this", "have", "will", "your", "what", "when",
    "项目", "对话", "系统", "需要", "继续", "可以", "一个", "以及", "对于", "进行", "作为", "我们", "研究",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value).strip("-")
    return cleaned.lower()[:48] or "project"


def combine_message_text(message: dict[str, Any]) -> str:
    body = normalize_space(str(message.get("text_content") or message.get("text") or ""))
    blocks = []
    for block in message.get("code_blocks", []) or []:
        language = block.get("language") or "text"
        code = block.get("code") or ""
        if code:
            blocks.append(f"```{language}\n{code}\n```")
    links = []
    for link in message.get("links", []) or []:
        href = link.get("href") or link.get("url")
        label = link.get("text") or href
        if href:
            links.append(f"[{label}]({href})")
    sections = [section for section in [body, "\n\n".join(blocks), "\n".join(links)] if section]
    return "\n\n".join(sections).strip()


def sentence_candidates(text: str) -> list[str]:
    working = text.replace("\r", "\n")
    chunks = re.split(r"[\n]+", working)
    candidates: list[str] = []
    sentence_pattern = re.compile(r".+?(?:[\u3002\uff01\uff1f!?;\uff1b]|\.(?=\s|$)|$)")
    for chunk in chunks:
        chunk = chunk.strip(" -*\t")
        if not chunk:
            continue
        parts = [match.group(0).strip() for match in sentence_pattern.finditer(chunk)]
        filtered_parts = [part for part in parts if 8 <= len(part) <= 240]
        if filtered_parts:
            candidates.extend(filtered_parts)
        elif 12 <= len(chunk) <= 240:
            candidates.append(chunk)
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        normalized = normalize_space(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result

def classify_text(text: str) -> str | None:
    lower = text.lower()
    scores: dict[str, int] = {}
    for knowledge_type, keywords in KNOWLEDGE_TYPES.items():
        score = 0
        for keyword in keywords:
            if keyword in lower or keyword in text:
                score += 1
        if score:
            scores[knowledge_type] = score
    if re.search(r"\b(f1|accuracy|precision|recall|auc|bleu|rouge|loss|metric|score)\b", lower):
        scores["Metric"] = scores.get("Metric", 0) + 2
    if lower.startswith("todo") or "todo:" in lower:
        scores["TODO"] = scores.get("TODO", 0) + 2
    if not scores:
        return None
    priority = {"Decision": 4, "Method": 3, "Metric": 2, "TODO": 1}
    return max(scores.items(), key=lambda item: (item[1], priority.get(item[0], 0)))[0]


def infer_stability(knowledge_type: str, text: str) -> str:
    lower = text.lower()
    if knowledge_type == "TODO":
        return "active"
    if any(token in lower for token in ["draft", "brainstorm", "猜测", "也许", "可能", "maybe"]):
        return "tentative"
    return "stable"


def make_title(knowledge_type: str, text: str) -> str:
    short = re.sub(r"[`*_#>\-]", "", text).strip()
    if len(short) > 72:
        short = short[:69].rstrip() + "..."
    return f"{knowledge_type}: {short}"


def summarize_conversation(title: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    merged = " ".join((message.get("text_content") or "")[:500] for message in messages[:6])
    top_terms = extract_keywords(f"{title} {merged}")[:6]
    recent = messages[-3:] if len(messages) >= 3 else messages
    highlights = [normalize_space(message.get("text_content", ""))[:180] for message in recent if message.get("text_content")]
    return {
        "summary": normalize_space(" ".join(highlights))[:500],
        "keywords": top_terms,
        "message_count": len(messages),
    }


def extract_keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,8}", text)
    filtered = [word for word in words if word.lower() not in STOPWORDS]
    counts = Counter(word.lower() for word in filtered)
    return [item[0] for item in counts.most_common(12)]


def derive_project_name(title: str, messages: list[dict[str, Any]]) -> str:
    title = normalize_space(title)
    if title and title.lower() not in GENERIC_TITLES and len(title) >= 4:
        return title[:60]
    keywords = extract_keywords(" ".join(message.get("text_content", "") for message in messages[:8]))
    if keywords:
        return "Project " + " ".join(keyword.capitalize() for keyword in keywords[:3])
    return "Suggested Research Project"


def recommend_projects(title: str, messages: list[dict[str, Any]], existing_projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conversation_text = f"{title} {' '.join(message.get('text_content', '') for message in messages[:10])}".lower()
    title_keywords = set(extract_keywords(title))
    scored: list[dict[str, Any]] = []
    for project in existing_projects:
        name = str(project["name"])
        project_tokens = set(extract_keywords(name))
        overlap = len(project_tokens & title_keywords)
        if name.lower() in conversation_text:
            overlap += 2
        if overlap:
            scored.append({"project_id": project["id"], "confidence": min(0.95, 0.45 + overlap * 0.12)})
    scored.sort(key=lambda item: item["confidence"], reverse=True)
    return scored[:3]


def extract_knowledge_units(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    for message in messages:
        text = message.get("text_content") or ""
        for candidate in sentence_candidates(text):
            knowledge_type = classify_text(candidate)
            if not knowledge_type:
                continue
            extracted.append(
                {
                    "type": knowledge_type,
                    "title": make_title(knowledge_type, candidate),
                    "body": candidate,
                    "stability": infer_stability(knowledge_type, candidate),
                    "evidence_quote": candidate[:180],
                    "message_external_id": message.get("external_id"),
                }
            )
    if not extracted and messages:
        summary = summarize_conversation("", messages)
        fallback_body = summary["summary"] or (messages[-1].get("text_content") or "")[:180]
        if fallback_body:
            extracted.append(
                {
                    "type": "Method",
                    "title": make_title("Method", fallback_body),
                    "body": fallback_body,
                    "stability": "tentative",
                    "evidence_quote": fallback_body,
                    "message_external_id": messages[-1].get("external_id"),
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in extracted:
        key = sha256_text(f"{item['type']}|{item['body']}")
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped[:32]


def decode_bytes(raw_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def extract_text_from_pdf_bytes(raw_bytes: bytes) -> str:
    decoded = raw_bytes.decode("latin-1", errors="ignore")
    strings = re.findall(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff ,.;:_/()\-]{8,}", decoded)
    cleaned = [normalize_space(item) for item in strings]
    unique: list[str] = []
    seen: set[str] = set()
    for item in cleaned:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return "\n".join(unique[:200])


def parse_structured_text(text: str) -> dict[str, Any]:
    sections = []
    parameters = []
    tables = 0
    entities = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or re.match(r"^\d+[.)]\s+", stripped):
            sections.append(stripped.lstrip("# "))
        if "|" in stripped and stripped.count("|") >= 2:
            tables += 1
        parameter_match = re.match(r"^([A-Za-z][A-Za-z0-9_ /-]{1,30}|[\u4e00-\u9fff]{2,12})\s*[:=]\s*(.{1,80})$", stripped)
        if parameter_match:
            parameters.append({"name": parameter_match.group(1).strip(), "value": parameter_match.group(2).strip()})
        if stripped.startswith("http://") or stripped.startswith("https://"):
            entities.append(stripped)
    return {
        "sections": sections[:20],
        "parameters": parameters[:30],
        "tables": tables,
        "entities": entities[:20],
    }


def guess_suffix(file_name: str, mime_type: str) -> str:
    if file_name and "." in file_name:
        return "." + file_name.rsplit(".", 1)[1]
    return mimetypes.guess_extension(mime_type or "") or ""


def analyze_asset_fallback(asset: dict[str, Any], raw_bytes: bytes | None) -> dict[str, Any]:
    mime_type = asset.get("mime_type", "")
    kind = asset.get("kind", "attachment")
    if raw_bytes:
        if mime_type == "application/pdf" or asset.get("file_name", "").lower().endswith(".pdf"):
            text = extract_text_from_pdf_bytes(raw_bytes)
        elif mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}:
            text = decode_bytes(raw_bytes)
        elif kind == "image":
            text = f"Image asset {asset.get('file_name') or asset.get('source_url') or asset.get('id')} captured for later review."
        else:
            text = decode_bytes(raw_bytes[:20000])
    else:
        text = f"Remote asset reference: {asset.get('source_url') or asset.get('file_name') or asset.get('id')}"
    structured = parse_structured_text(text)
    structured["kind"] = kind
    structured["mime_type"] = mime_type
    return {
        "extracted_text": text[:20000],
        "structured_json": structured,
        "status": "completed" if text else "failed",
    }


def build_retrieval_chunks(conversation: dict[str, Any], messages: list[dict[str, Any]], knowledge_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for message in messages:
        text = normalize_space(message.get("text_content", ""))
        if text:
            chunks.append(
                {
                    "owner_type": "message",
                    "owner_id": message["id"],
                    "text": text[:1200],
                    "metadata_json": {"conversation_id": conversation["id"], "role": message.get("role")},
                }
            )
    for unit in knowledge_units:
        chunks.append(
            {
                "owner_type": "knowledge_unit",
                "owner_id": unit["id"],
                "text": f"{unit['type']} {unit['title']} {unit['body']}"[:1400],
                "metadata_json": {"type": unit["type"], "review_status": unit.get("review_status", "pending")},
            }
        )
    return chunks


TEMPLATE_FOCUS_NOTES = {
    "continue-paper-writing": "Prioritize research goals, method details, experiment setup, metric interpretation, and writing-ready conclusions.",
    "continue-experiment-design": "Prioritize variables, evaluation criteria, experimental steps, parameter settings, and reproducibility requirements.",
    "merge-conclusions": "Prioritize validated conclusions, conflicts, consensus boundaries, and concrete next actions across conversations.",
}


def rank_knowledge_units(template_type: str, knowledge_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weights = TEMPLATE_WEIGHTS.get(template_type, TEMPLATE_WEIGHTS["merge-conclusions"])
    ranked = []
    for unit in knowledge_units:
        score = weights.get(unit["type"], 1)
        score += 1 if unit.get("stability") == "stable" else 0
        ranked.append((score, unit))
    ranked.sort(key=lambda item: (item[0], item[1].get("created_at", "")), reverse=True)
    return [item[1] for item in ranked]


def _unit_key(unit: dict[str, Any]) -> str:
    return str(unit.get("id") or sha256_text(f"{unit.get('type', '')}|{unit.get('body', '')}"))


def _unit_cost(unit: dict[str, Any]) -> int:
    title = normalize_space(str(unit.get("title") or ""))
    body = normalize_space(str(unit.get("body") or ""))
    return max(40, len(title) + len(body) + 24)


def select_context_pack_units(
    template_type: str,
    budget_mode: str,
    budget_value: int,
    knowledge_units: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ranked = rank_knowledge_units(template_type, knowledge_units)
    if not ranked:
        return []
    if budget_mode != "chars":
        return ranked[: max(6, min(len(ranked), int(budget_value or 10), 14))]

    soft_budget = max(1200, int(budget_value or 3600))
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    consumed = 0
    weights = TEMPLATE_WEIGHTS.get(template_type, TEMPLATE_WEIGHTS["merge-conclusions"])
    type_order = list(weights) + [unit["type"] for unit in ranked if unit["type"] not in weights]

    for knowledge_type in type_order:
        candidate = next((unit for unit in ranked if unit.get("type") == knowledge_type and _unit_key(unit) not in selected_keys), None)
        if not candidate:
            continue
        candidate_cost = _unit_cost(candidate)
        if selected and consumed + candidate_cost > int(soft_budget * 0.72):
            continue
        selected.append(candidate)
        selected_keys.add(_unit_key(candidate))
        consumed += candidate_cost

    for unit in ranked:
        unit_key = _unit_key(unit)
        if unit_key in selected_keys:
            continue
        unit_cost = _unit_cost(unit)
        if selected and consumed + unit_cost > soft_budget:
            continue
        selected.append(unit)
        selected_keys.add(unit_key)
        consumed += unit_cost
        if len(selected) >= 14 or consumed >= soft_budget:
            break

    return selected[:14]


def _group_knowledge_units(knowledge_units: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for unit in knowledge_units:
        grouped.setdefault(str(unit.get("type") or "Unknown"), []).append(unit)
    return grouped


def _compact_unit_text(unit: dict[str, Any], max_chars: int = 120) -> str:
    text = normalize_space(str(unit.get("body") or unit.get("title") or ""))
    text = text.rstrip(" .;,")
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def _summarize_units(units: list[dict[str, Any]], max_items: int = 3, max_chars: int = 280) -> str:
    snippets: list[str] = []
    seen: set[str] = set()
    for unit in units:
        snippet = _compact_unit_text(unit)
        if not snippet:
            continue
        identity = snippet.lower()
        if identity in seen:
            continue
        seen.add(identity)
        snippets.append(snippet)
        if len(snippets) >= max_items:
            break
    if not snippets:
        return "Not explicitly established in the current material."
    summary = "; ".join(snippets)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip("; ") + "..."
    return summary


def _keyword_summary(project_name: str, task_goal: str, knowledge_units: list[dict[str, Any]]) -> str:
    keywords = extract_keywords(" ".join([project_name, task_goal] + [str(unit.get("body") or "") for unit in knowledge_units]))
    if not keywords:
        return "Not explicitly established in the current material."
    return " / ".join(keywords[:8])


def limit_context_pack_text(text: str, budget_mode: str, budget_value: int) -> str:
    if budget_mode != "chars" or len(text) <= budget_value:
        return text
    suffix = "\n\n[Context pack truncated to fit the current budget.]"
    head_budget = max(160, budget_value - len(suffix))
    clipped = text[:head_budget].rstrip()
    cut = clipped.rfind("\n")
    if cut >= int(head_budget * 0.7):
        clipped = clipped[:cut].rstrip()
    return clipped + suffix


def render_context_pack(
    project: dict[str, Any],
    template_type: str,
    task_goal: str,
    budget_mode: str,
    budget_value: int,
    knowledge_units: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    selected = select_context_pack_units(template_type, budget_mode, budget_value, knowledge_units)
    if not selected:
        sections = [
            "# Context Pack (Editable Working Draft for LLM Handoff)",
            "",
            "> No approved knowledge units matched this request yet, so the system cannot build a reliable research context pack.",
            "> Approve the extracted knowledge first, or uncheck reviewed_only to generate a draft pack.",
            "",
            "- No approved knowledge units matched this request yet.",
        ]
        return "\n".join(sections), selected

    grouped = _group_knowledge_units(selected)
    decisions = grouped.get("Decision", [])
    constraints = grouped.get("Constraint", [])
    metrics = grouped.get("Metric", [])
    methods = grouped.get("Method", [])
    experiment = grouped.get("ExperimentSetup", [])
    code_design = grouped.get("CodeDesign", [])
    prompt_templates = grouped.get("PromptTemplate", [])
    rejected = grouped.get("RejectedOption", [])
    todos = grouped.get("TODO", [])
    references = grouped.get("ReferenceLead", [])
    project_name = normalize_space(str(project.get("name") or "Untitled Project"))
    normalized_goal = normalize_space(task_goal or "Continue the project") or "Continue the project"

    sections = [
        "# Context Pack (Editable Working Draft for LLM Handoff)",
        "",
        "> Purpose: preserve the project's current research goals, methods, evaluation criteria, constraints, conclusions, and next steps as reusable context for the next LLM session.",
        "> Note: this is not a chat log. It is a synthesized research handoff reconstructed from reviewed project knowledge.",
        "",
        "---",
        "",
        "## 0) Project Name",
        "",
        f"* Project: {project_name}",
        "",
        "## 1) Research Goals and Problem",
        "",
        "### 1.1 Current Task",
        f"* Current task: {normalized_goal}",
        f"* Research keywords: {_keyword_summary(project_name, normalized_goal, selected)}",
        "",
        "### 1.2 Confirmed Goals",
        f"* The project is currently focused on: {_summarize_units(decisions + methods + metrics, max_items=4, max_chars=320)}",
        "",
        "### 1.3 Core Difficulties and Constraints",
        f"* Current constraints, risks, or boundaries: {_summarize_units(constraints + rejected + todos, max_items=4, max_chars=320)}",
        "",
        "## 2) Core Method and Technical Route",
        "",
        "### 2.1 Main Method",
        f"* Main working route: {_summarize_units(methods + experiment + decisions, max_items=4, max_chars=320)}",
        "",
        "### 2.2 Confirmed Decisions",
        f"* Confirmed decisions: {_summarize_units(decisions, max_items=4, max_chars=320)}",
        "",
        "### 2.3 Rejected Options or Guardrails",
        f"* Things not to do or boundaries to respect: {_summarize_units(rejected + constraints, max_items=4, max_chars=300)}",
        "",
        "## 3) Key Objects, Structured Elements, and Inputs/Outputs",
        "",
        "### 3.1 Research Objects / Inputs",
        f"* Main inputs or study objects: {_summarize_units(experiment + references + methods, max_items=4, max_chars=320)}",
        "",
        "### 3.2 Structured Elements",
        f"* Data structures, modules, interfaces, or prompt assets: {_summarize_units(code_design + prompt_templates + experiment, max_items=4, max_chars=320)}",
        "",
        "### 3.3 Expected Outputs",
        f"* Expected outputs or deliverables: {_summarize_units(metrics + todos + decisions, max_items=4, max_chars=320)}",
        "",
        "## 4) Metrics, Experiments, and Validation",
        "",
        "### 4.1 Metrics / Success Criteria",
        f"* Metrics or acceptance criteria currently in scope: {_summarize_units(metrics + constraints, max_items=4, max_chars=320)}",
        "",
        "### 4.2 Experimental Setup / Parameters",
        f"* Experimental setup, parameters, or process requirements: {_summarize_units(experiment + methods + constraints, max_items=4, max_chars=320)}",
        "",
        "### 4.3 Evidence and Reference Leads",
        f"* Reusable references or evidence leads: {_summarize_units(references, max_items=3, max_chars=260)}",
        "",
        "## 5) Current Conclusions, Open Questions, and Next Steps",
        "",
        "### 5.1 Confirmed Conclusions",
        f"* Current validated conclusions: {_summarize_units(decisions + methods + metrics, max_items=4, max_chars=320)}",
        "",
        "### 5.2 Open Questions / TODO",
        f"* Issues that still need work: {_summarize_units(todos + constraints + rejected, max_items=4, max_chars=320)}",
        "",
        "### 5.3 Recommended Next Step",
        f"* Continue around '{normalized_goal}', while keeping the confirmed decisions, constraints, and metrics stable unless new evidence appears.",
        "",
        "## 6) Implementation and Reproduction Notes",
        "",
        f"* Implementation focus: {_summarize_units(code_design + methods + experiment, max_items=4, max_chars=320)}",
        f"* Reproduction notes: {_summarize_units(constraints + references + todos, max_items=4, max_chars=320)}",
        "",
        "## 7) Instructions for the Next LLM",
        "",
        "* Do not treat this file as a chat transcript. Treat it as the project's current research state.",
        "* If a new suggestion conflicts with a confirmed decision, metric, or constraint, explain the conflict before proposing a revision.",
        f"* Template focus: {TEMPLATE_FOCUS_NOTES.get(template_type, TEMPLATE_FOCUS_NOTES['merge-conclusions'])}",
        f"* Current goal for the next turn: {normalized_goal}",
        "",
        "# End of Context Pack",
    ]
    return limit_context_pack_text("\n".join(sections), budget_mode, budget_value), selected

def canonical_context_pack_id(project_id: str, template_type: str, task_goal: str) -> str:
    return sha256_text(f"{project_id}|{template_type}|{normalize_space(task_goal.lower())}")


def coerce_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
