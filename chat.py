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
import unit_prices_store
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

    # 단가표 스냅샷 — 검토 시점의 단가표 캡쳐 (관리자가 추후 단가표 수정해도 채팅 응답 일관성 유지)
    unit_prices_snapshot = {
        year: unit_prices_store.get_year(year)
        for year in unit_prices_store.list_years()
    }

    return {
        "project_info": project_info or {},
        "documents": docs_ctx,
        "unit_prices": unit_prices_snapshot,
    }


def save(chat_id: str, context: dict) -> None:
    CHAT_STORE[chat_id] = {"context": context, "history": []}


def clear(chat_id: str) -> None:
    CHAT_STORE.pop(chat_id, None)


def get_history(chat_id: str) -> list:
    entry = CHAT_STORE.get(chat_id)
    return entry["history"] if entry else []


def _format_unit_prices(unit_prices: dict) -> str:
    """연간단가표 + 최종 공사비 산정 룰을 채팅 컨텍스트용 텍스트로 포맷팅."""
    if not unit_prices:
        return "[연간단가표]\n(단가표 미등록)\n"

    parts = ["[연간단가표]"]
    parts.append("※ 단가 적용 기간: 당해년도 5/1 ~ 익년도 4/30")
    parts.append("※ 도로재질 분류: ASP(절삭포장) / CON`C 및 보도블럭(=ASP 外)")
    parts.append("")

    for year in sorted(unit_prices.keys(), reverse=True):
        data = unit_prices[year] or {}
        parts.append(f"━━━ {year}년 ━━━")
        parts.append("• 연장별 단가 (단위: 원)")
        parts.append("    연장  | ASP(절삭포장) | CON`C·보도블럭")
        for lk in unit_prices_store.LENGTH_KEYS:
            asp = (data.get("ASP") or {}).get(lk, 0)
            conc = (data.get("CONC_BLOCK") or {}).get(lk, 0)
            parts.append(f"    {lk:>4}  | {asp:>12,} | {conc:>12,}")
        parts.append("• 가산 항목 (연장/도로재질 규격 무관)")
        for item in unit_prices_store.RIGHT_COLUMN_ITEMS:
            v = data.get(item["key"], 0)
            parts.append(f"    - {item['label']}: {v:,}원")
        parts.append("")

    parts.append("[최종 공사비 산정 룰]")
    parts.append("※ 발주연장은 정수(1~10m), 준공연장은 소수점 첫째자리까지 표기")
    parts.append("")
    parts.append("1) 정산 여부 판정 (평면연장 기준)")
    parts.append("   · |준공연장 - 발주연장| ≥ 1m  → 정산 대상")
    parts.append("   · |준공연장 - 발주연장| < 1m  → 정산 비대상")
    parts.append("")
    parts.append("2) 적용 연장 결정")
    parts.append("   · 정산 대상   : 준공연장의 올림(ceiling) 값으로 단가 적용")
    parts.append("                   (∵ 실제 시공 결과 기준 정산)")
    parts.append("   · 정산 비대상 : 발주연장 그대로 적용")
    parts.append("")
    parts.append("3) 예시 (발주 2m 기준)")
    parts.append("   ▶ 증가 케이스")
    parts.append("   - 준공 2.9m → 차이 0.9 → 비대상 → 2m 단가")
    parts.append("   - 준공 3.0m → 차이 1.0 → 대상 → ceil(3.0)=3m → 3m 단가")
    parts.append("   - 준공 3.1m → 차이 1.1 → 대상 → ceil(3.1)=4m → 4m 단가")
    parts.append("   ▶ 감소 케이스")
    parts.append("   - 준공 1.1m → 차이 0.9 → 비대상 → 2m 단가")
    parts.append("   - 준공 1.0m → 차이 1.0 → 대상 → ceil(1.0)=1m → 1m 단가")
    parts.append("   - 준공 0.9m → 차이 1.1 → 대상 → ceil(0.9)=1m → 1m 단가")
    parts.append("   ▶ 큰 감소(기존관 위치 오표기 등) — 발주 2m → 준공 0.5m → ceil(0.5)=1m → 1m 단가")
    parts.append("")
    parts.append("4) 최종 공사비")
    parts.append("   = (적용 연장 m 단가)")
    parts.append("   + (PLP 옵션 가산, 선택 시 해당 연도 PLP 단가)")
    parts.append("   + (일시점용료 + 영구신청수수료, 사용자 입력값)")
    parts.append("")
    return "\n".join(parts)


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

    # 연간단가표 + 산정 룰 (단가/산식/정산 관련 질문 응답용)
    parts.append(_format_unit_prices(context.get("unit_prices") or {}))

    return "\n".join(parts)


SYSTEM_PROMPT = """너는 도시가스 공사 준공서류 검토 결과를 설명하고 사용자 질문에 답하는 전문 보조원이야.
규칙:
- 검토 결과, 연간단가표, 첨부 파일 텍스트 추출본을 근거로 답할 것. 추측 금지.
- 단가·산식·정산 룰 관련 질문은 [연간단가표] 와 [최종 공사비 산정 룰] 섹션을 적극 활용해 답할 것.
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
