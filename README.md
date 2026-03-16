# Conversation Knowledge Base

A local-first MVP for capturing ChatGPT and Doubao conversations, extracting project knowledge, reviewing evidence-backed knowledge units, and generating reusable context packs.

## What is implemented

- Python local service with SQLite storage and file-backed raw payload / asset storage.
- Browser-based management UI served from the local service.
- Chrome/Edge MV3 extension for ChatGPT current-page capture and Doubao current-page capture.
- ChatGPT official export import endpoint.
- Rule-based extraction pipeline with optional OpenAI-compatible model hooks.
- Asset ingestion for images, PDFs, and generic attachments with fallback parsing.
- Review queue and context pack generation with a reviewed-only default gate.

## Project structure

- `app/` local service, storage, extraction pipeline, and static dashboard.
- `extension/` MV3 browser extension.
- `tests/` workflow tests.
- `run_server.py` local entrypoint.

## Run locally

1. Start the local service:

```powershell
python run_server.py
```

2. Open the dashboard:

[http://127.0.0.1:8765](http://127.0.0.1:8765)

3. Load the browser extension:

- Open `chrome://extensions` or `edge://extensions`
- Enable `Developer mode`
- Click `Load unpacked`
- Select [`extension`](./extension)

## Optional external model configuration

The service works without external dependencies. If you want to use an OpenAI-compatible API for richer extraction and image analysis, set:

```powershell
$env:LLM_API_URL="https://api.openai.com/v1/chat/completions"
$env:LLM_API_KEY="..."
$env:LLM_TEXT_MODEL="gpt-4.1-mini"
$env:LLM_VISION_MODEL="gpt-4.1-mini"
```

## Supported API routes

- `GET /api/overview`
- `GET /api/projects`
- `GET /api/projects/{id}/timeline`
- `GET /api/import-batches`
- `GET /api/knowledge-units`
- `GET /api/context-packs`
- `GET /api/search?q=...`
- `POST /api/projects`
- `POST /api/ingest/page-session`
- `POST /api/ingest/chatgpt-export`
- `POST /api/extraction/runs`
- `POST /api/reviews`
- `POST /api/context-packs/generate`
- `POST /api/conversations/{id}/projects`

## Git auto-push

This repository is configured to auto-push after each successful `git commit` once an `origin` remote is set. The tracked hook lives in `.githooks/post-commit`, and local git should point `core.hooksPath` to `.githooks`.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

## Notes

- The extraction pipeline is deliberately conservative and defaults to `pending` review status.
- Current-page capture is heuristic and depends on the source platform DOM.
- Asset parsing is best-effort without third-party libraries; external model configuration improves image understanding.
