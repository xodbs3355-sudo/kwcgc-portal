"""
검토 결과 채팅 — Gemini 기반 자연어 Q&A.

구조:
- 검토 완료 시점: PDF 텍스트 + 검토 결과 → CHAT_STORE 에 chat_id 키로 저장
- 채팅 요청: chat_id 로 컨텍스트 가져와서 멀티턴 호출
- 로그아웃 시: clear(chat_id)

In-memory 보관 — 서버 재시작 시 사라지지만, 로그아웃 시 사라짐 요건엔 부합.
"""
import io
import json
import re
import time
import uuid

from pypdf import PdfReader

import config
import usage_store


# chat_id → {"context": {...}, "history": [{"role":..., "text":...}, ...]}
CHAT_STORE: dict = {}

# 컨텍스트 안전 한도 — PDF 텍스트가 너무 크면 잘라냄
MAX_PDF_CHARS_PER_DOC = 30000   # 서류당 최대 텍스트 길이
MAX_HISTORY = 20                # 누적 대화 (최근 N개만 유지)


def new_chat_id() -> str:
    return uuid.uuid4().hex


def _extract_pdf_text(file_bytes: bytes) -> str:
    """PDF 텍스트 레이어 추출. 실패 시 빈 문자열."""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        chunks = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
                if t.strip():
                    chunks.append(t)
            except Exception:
                continue
        return "\n".join(chunks).strip()
    except Exception:
        return ""


def build_context(all_results: dict, uploaded_files: dict, project_info: dict,
                  documents: list) -> dict:
    """검토 결과 + PDF 텍스트 추출본 + 프로젝트 정보 → 채팅 컨텍스트.

    uploaded_files: {doc_id: [(filename, bytes), ...]}
    documents:      DOCUMENTS 리스트 (서류 메타)
    """
    docs_ctx = []
    for doc in documents:
        did = doc["id"]
        name = doc["name"]
        rows = all_results.get(name) or []
        files = uploaded_files.get(did, []) or []

        # 각 파일의 텍스트 추출 (PDF만, 이미지는 텍스트 없음)
        file_texts = []
        for filename, file_bytes in files:
            ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
            if ext == "pdf":
                txt = _extract_pdf_text(file_bytes)
                if len(txt) > MAX_PDF_CHARS_PER_DOC:
                    txt = txt[:MAX_PDF_CHARS_PER_DOC] + "\n... (이하 생략)"
                file_texts.append({"filename": filename, "text": txt})
            else:
                # 이미지: 텍스트 없음, 파일명만
                file_texts.append({"filename": filename, "text": "(이미지 파일 — 텍스트 추출 불가)"})

        docs_ctx.append({
            "doc_id": did,
            "doc_name": name,
            "review_rows": rows,
            "files": file_texts,
        })

    return {
        "project_info": project_info or {},
        "documents": docs_ctx,
    }


def save(chat_id: str, context: dict) -> None:
    CHAT_STORE[chat_id] = {"context": context, "history": []}


def clear(chat_id: str) -> None:
    CHAT_STORE.pop(chat_id, None)


def get_history(chat_id: str) -> list:
    entry = CHAT_STORE.get(chat_id)
    return entry["history"] if entry else []


def _format_context_for_prompt(context: dict) -> str:
    """LLM 프롬프트용 컨텍스트 문자열."""
    pi = context.get("project_info", {}) or {}
    pi_lines = []
    if pi.get("name"): pi_lines.append(f"공사명: {pi['name']}")
    if pi.get("date"): pi_lines.append(f"준공일자: {pi['date']}")
    if pi.get("amount"): pi_lines.append(f"준공금액: {pi['amount']}")
    if pi.get("road_material"): pi_lines.append(f"도로재질: {pi['road_material']}")
    if pi.get("extension"): pi_lines.append(f"준공연장: {pi['extension']}m")
    if pi.get("order_extension"): pi_lines.append(f"발주연장: {pi['order_extension']}m")
    if pi.get("plp") is not None: pi_lines.append(f"PLP 옵션: {'예' if pi['plp'] else '아니오'}")

    parts = ["[공사 정보]"]
    parts.extend(pi_lines if pi_lines else ["(정보 없음)"])
    parts.append("")

    for doc in context.get("documents", []):
        parts.append(f"━━━ [{doc['doc_name']}] (doc_id={doc['doc_id']}) ━━━")
        # 검토 결과
        rows = doc.get("review_rows") or []
        if rows:
            parts.append("- 검토 결과:")
            for r in rows:
                item = r.get("항목", "")
                res = r.get("결과", "")
                ext = r.get("추출값", "")
                note = r.get("비고", "")
                parts.append(f"    · [{res}] {item} | 추출값: {ext} | 비고: {note}")
        else:
            parts.append("- 검토 결과: (없음)")
        # 첨부 파일 텍스트
        files = doc.get("files") or []
        if files:
            parts.append("- 첨부 파일 (텍스트 추출본):")
            for f in files:
                parts.append(f"  ▷ 파일명: {f['filename']}")
                txt = f.get("text", "").strip()
                if txt:
                    # 들여쓰기 추가
                    for line in txt.split("\n"):
                        parts.append(f"    {line}")
                else:
                    parts.append("    (텍스트 없음)")
        else:
            parts.append("- 첨부 파일: (없음)")
        parts.append("")
    return "\n".join(parts)


SYSTEM_PROMPT = """너는 도시가스 공사 준공서류 검토 결과를 설명하고 사용자 질문에 답하는 전문 보조원이야.
규칙:
- 검토 결과와 첨부 파일 텍스트 추출본을 근거로만 답할 것. 추측 금지.
- 텍스트에 명시되지 않은 내용을 묻거나, 이미지·도면·사진에 대한 시각적 질문은
  "원본 파일을 직접 확인해주세요"라고 안내할 것.
- 한국어로 간결하게 답할 것. 표나 목록이 도움 되면 사용.
- 사용자가 잘못 알고 있는 내용은 부드럽게 정정.
"""


def ask(chat_id: str, user_message: str, company: str | None = None) -> dict:
    """사용자 메시지 → Gemini 호출 → 답변 반환.

    반환: {"ok": bool, "answer": str, "error": str | None}
    """
    entry = CHAT_STORE.get(chat_id)
    if not entry:
        return {"ok": False, "answer": "", "error": "채팅 컨텍스트가 없어요. 페이지를 새로고침해주세요."}

    if config.USE_MOCK or not config.GEMINI_API_KEY:
        return {"ok": False, "answer": "", "error": "AI 키가 설정되지 않았습니다."}

    context = entry["context"]
    history = entry["history"]

    # 프롬프트 조립
    ctx_text = _format_context_for_prompt(context)
    history_text_parts = []
    # 최근 MAX_HISTORY 개만 포함
    recent = history[-MAX_HISTORY:]
    for h in recent:
        role = "사용자" if h["role"] == "user" else "보조원"
        history_text_parts.append(f"{role}: {h['text']}")
    history_text = "\n".join(history_text_parts)

    full_prompt = (
        SYSTEM_PROMPT
        + "\n\n=== 검토 자료 ===\n"
        + ctx_text
        + ("\n\n=== 이전 대화 ===\n" + history_text if history_text else "")
        + f"\n\n=== 사용자 질문 ===\n{user_message}\n\n=== 답변 ===\n"
    )

    try:
        import google.generativeai as genai
        genai.configure(api_key=config.GEMINI_API_KEY)
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        response = model.generate_content(full_prompt)
        answer = (response.text or "").strip()
        # usage 기록 (chat 가상 doc_id)
        try:
            meta = getattr(response, "usage_metadata", None)
            if meta:
                pt = getattr(meta, "prompt_token_count", 0) or 0
                ct = getattr(meta, "candidates_token_count", 0) or 0
                usage_store.record(company or "", "chat", pt, ct)
        except Exception:
            pass
    except Exception as e:
        msg = str(e)
        # 429 한 번 재시도
        if "429" in msg:
            m = re.search(r"retry_delay\s*\{?\s*seconds:\s*(\d+)", msg)
            wait_sec = (int(m.group(1)) + 2) if m else 30
            time.sleep(min(wait_sec, 60))
            try:
                response = model.generate_content(full_prompt)
                answer = (response.text or "").strip()
            except Exception as e2:
                return {"ok": False, "answer": "", "error": f"AI 호출 실패: {str(e2)[:200]}"}
        else:
            return {"ok": False, "answer": "", "error": f"AI 호출 실패: {msg[:200]}"}

    # 대화 기록 누적
    history.append({"role": "user", "text": user_message})
    history.append({"role": "assistant", "text": answer})

    return {"ok": True, "answer": answer, "error": None}
