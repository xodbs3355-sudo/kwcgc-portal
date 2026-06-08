# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

도시가스 공사 준공서류 검토 포털 — a Flask web app that lets gas-construction
subcontractors upload completion documents (준공서류) and have them reviewed
automatically by Google Gemini. Each document type is checked against
user-entered project info (공사명/준공일자/준공금액 etc.) and against a
unit-price table (연간단가표), then results are shown per-document with
OK / NG / WARN / SKIP verdicts. The UI and nearly all code comments are in
Korean — keep new comments and user-facing strings in Korean to match.

## Commands

```bash
# Run locally (defaults to 127.0.0.1:5000, debug off)
python app.py
# Windows convenience launcher (installs deps + runs)
run.bat

# Install deps
pip install -r requirements.txt

# Production (Railway) entrypoint
gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120
```

There is **no test suite, linter, or CI** configured. Verify changes by
running the app and exercising the affected route manually.

### Key environment variables
- `GEMINI_API_KEY` — without it, `config.USE_MOCK` is True and reviews return
  placeholder WARN rows (`reviewer.MOCK_RESULT`). Set it to exercise real AI.
- `GEMINI_MODEL` — defaults to `gemini-2.5-flash`.
- `FLASK_SECRET_KEY`, `FLASK_DEBUG`, `PORT` — runtime config.
- `PROMPTS_FILE`, `UNIT_PRICES_FILE`, `USAGE_FILE`, `PROJECTS_DIR`,
  `REVIEWS_DIR` — override persistence paths (see Persistence below).

Diagnostics: `GET /status` reports whether AI is enabled and the key/model in
use; `POST /warmup` pre-warms the Gemini connection.

## Architecture

This is a plain Flask app (no blueprints, no ORM, no database). `app.py` holds
**every route**; domain logic lives in single-purpose top-level modules that
`app.py` imports directly. The `.streamlit/` dir is vestigial — the app is
Flask, not Streamlit.

### Request/data flow
1. **Login** (`auth.py`) — hardcoded `COMPANIES` dict of company→password.
   `공무` is the sole admin (`ADMIN_USERS`). No real user DB.
2. **Upload** (`/upload/<doc_id>`) — files are read into memory and persisted
   as a pickle of `{doc_id: [(filename, bytes), ...]}` in a temp dir keyed by a
   per-session `sess_id`. A sidecar `<sess_id>.names.json` stores just the
   filenames so pages that don't need the bytes avoid deserializing the (large)
   pickle — see `load_uploaded` vs `load_file_names` in `app.py`.
3. **Review** (`/review`) — fans out one `reviewer.review_document` call per
   document across a `ThreadPoolExecutor` (one worker per doc). Results stored
   in `session["review_results"]`, also auto-saved via `project_store` and used
   to build a `chat` context.
4. **Result** (`/result`) — aggregates per-document verdicts into counts and an
   overall pass/fail. Reviewers can manually flip an item to OK; see Overrides.
5. **Share** (`/r/<review_id>`) — auth-free read-only result page; the UUID
   itself is the access control. Linked via QR code embedded in the merged PDF.

### The reviewer (`reviewer.py`) — the core
`review_document(doc_id, ...)` dispatches per document type:
- **`attachment_only` docs** (see `documents.py`, e.g. doc04/doc10) — skip the
  AI call entirely, just confirm files are attached.
- **doc01 (준공계)** — *hybrid*: Gemini only **extracts** structured data; all
  comparison/judgement is done in Python (`_doc01_apply_rules`,
  `_project_name_match`, `_date_match`, `_amount_match`). When editing matching
  rules (e.g. how strictly 공사명 must match, date normalization, 금액
  cross-check against the computed final cost), edit these Python helpers, **not
  the prompt**.
- **doc05** (`_TYPE_ID_DOCS`) — one merged PDF may contain several document
  types; the reviewer identifies each file's type and aggregates missing types.
- **doc06** — special multi-file combined call.
- **Other docs** — generic per-file AI review, parallelized across files.

`config.USE_MOCK` short-circuits real calls when no API key is present. Gemini
429s are retried once with a parsed `retry_delay`. Every call records token
usage via `usage_store`.

### Documents registry (`documents.py`)
`DOCUMENTS` is the single source of truth: ordered list of dicts with `id`,
`num`, `name`, `condition`, `default_skip`, `attachment_only`. Many places
iterate it. Note `id` and display `num` differ (e.g. `doc10` is shown as #5) —
don't assume `doc0N` == position N.

### Prompts (`prompts_store.py` + `prompt_defaults.py`)
Two-layer with **versioning**. `prompt_defaults.PROMPTS` holds the built-in
default per doc_id (committed to the repo); admins can override per-document via
`/admin/prompts`, saved to `prompts.json`. Each saved override records the
`base_version` it was edited against — if you bump a default's version in
`prompt_defaults.py`, stale admin overrides are auto-ignored and the new default
takes over (`_effective_saved_text` / `get_effective_prompt`). Prompts contain a
`{project_info}` placeholder that `reviewer._build_prompt` fills in.

### Unit prices (`unit_prices_store.py`)
Year-keyed price table (`{year: {material: {length: price}, ...단일항목}}`).
`compute_final_cost` derives the expected 준공금액 from material × length (1–10m)
+ optional PLP/land-fee additions, and the doc01 amount check compares the
document's figure against it. The price contract year runs 5/1→4/30, so
`default_applicable_year` rolls over in May, not January. Road-material combo
values map to internal keys via `ROAD_MATERIAL_MAP`.

### Other modules
- `chat.py` — in-memory (`CHAT_STORE`) multi-turn Q&A over the review results +
  extracted PDF text; context built at review time, cleared on logout.
- `output.py` — `merge_attachments_to_pdf` merges all attachments into one PDF
  with an embedded QR linking to the share page (uses `static/fonts/NanumGothic.ttf`).
- `project_store.py` — auto-saves each review to `/data/projects/<id>/`
  (meta.json + files.pkl), keyed by hash of company+공사명, 60-day TTL, lazy
  cleanup on listing.
- `share_store.py` — persists shareable review payloads, 90-day TTL.
- `usage_store.py` — appends one JSONL line per Gemini call; powers `/admin/usage`.

### Persistence model
No database. Mutable state lives in two places:
- **Per-session uploads**: pickle in the OS temp dir (`kwcgc_uploads/`).
- **App data**: JSON / JSONL / pickle files under `/data` when that dir exists
  (Railway Volume), else `./data` locally. Every store module resolves its path
  the same way and accepts an env-var override. `/data` is the production
  persistence assumption — don't write app data elsewhere.

## Conventions & gotchas
- **Verdict vocabulary** is fixed: `OK` / `NG` / `WARN` / `SKIP`, and result
  rows are dicts with **Korean keys** (`항목`, `결과`, `추출값`, `비고`). Aggregation
  logic in `app.py` and templates keys off these exact strings.
- **Admin gating**: admin routes check `_is_admin()` (company in `ADMIN_USERS`),
  injected into all templates as `is_admin`.
- **Manual overrides**: reviewers can mark an item OK via `/review/override`;
  stored in `session["manual_overrides"]` and applied by `_apply_overrides`,
  which preserves `원본_결과`. Don't mutate stored results in place.
- **Resilience pattern**: secondary work (chat-context build, project autosave,
  usage logging) is wrapped in bare `try/except` so a failure never blocks the
  actual review. Preserve this when touching `/review`.
- **Static cache-busting**: CSS/JS are served with 1-year immutable caching and
  busted via `?v=<mtime>` (`asset_version`), computed **once at boot**. Editing
  `style.css`/`app.js` requires a Flask restart locally to see changes (prod
  restarts on each git push). `static/pdfjs/` is a vendored PDF.js viewer.
- **Deployment**: Railway, auto-deploy on git push to the deployed branch.

## Repo-specific workflow
Active development happens on the branch `claude/claude-md-docs-RCrMS`; push
there. The remote is `xodbs3355-sudo/kwcgc-portal`.
