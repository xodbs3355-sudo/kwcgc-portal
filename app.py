"""
도시가스 공사 준공서류 검토 포털 — Flask 버전
"""
import io
import os
import pickle
import secrets
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, flash, jsonify
)

import config
import auth
import reviewer
import output
import prompts_store
import unit_prices_store
import usage_store
import chat
import share_store
from documents import DOCUMENTS


def _is_admin() -> bool:
    return auth.is_admin(session.get("company", ""))


def _compute_adjustment_required(project_info: dict) -> bool | None:
    """발주연장 대비 준공연장이 1.0m 이상 차이나면 정산 대상.
    값이 부족하거나 변환 실패 시 None.
    """
    try:
        ext_raw = (project_info.get("extension", "") or "").strip()
        order_raw = (project_info.get("order_extension", "") or "").strip()
        if ext_raw and order_raw:
            diff = abs(float(ext_raw) - float(order_raw))
            return diff >= 1.0
    except (ValueError, TypeError):
        pass
    return None


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024 * 16  # 행당 200MB × 16 여유


@app.context_processor
def inject_admin_flag():
    return {"is_admin": _is_admin()}


# 정적 파일 캐시 무효화용 버전 — style.css/app.js의 mtime 합산
def _compute_asset_version() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(base, "static", "css", "style.css"),
        os.path.join(base, "static", "js", "app.js"),
    ]
    mtimes = []
    for p in paths:
        try:
            mtimes.append(int(os.path.getmtime(p)))
        except OSError:
            pass
    return str(sum(mtimes)) if mtimes else "0"


_ASSET_VERSION_CACHE = {"value": _compute_asset_version()}


@app.context_processor
def inject_asset_version():
    # 매 요청마다 재계산 (실시간 반영). production에서는 한번만 계산해도 무방.
    return {"asset_version": _compute_asset_version()}


@app.context_processor
def inject_global_session_info():
    """헤더 등에서 사용할 전역 데이터 — 등록된 연도 / 현재 선택 연도."""
    return {
        "available_years": unit_prices_store.list_years(),
        "sess_project_info": session.get("project_info", {}),
    }


@app.before_request
def _auto_set_default_year():
    """로그인 상태에서 session 에 year 없으면 자동으로 현재 연도 또는 최신 연도 설정.
    헤더 드롭다운이 항상 합리적인 기본값을 갖도록.
    """
    if "company" not in session:
        return
    info = session.get("project_info", {})
    if info.get("year"):
        return
    years = unit_prices_store.list_years()
    if not years:
        return
    current_year = str(unit_prices_store.default_applicable_year())
    chosen = current_year if current_year in years else years[0]
    info = dict(info)
    info["year"] = chosen
    session["project_info"] = info


# ── 세션 저장소 (업로드된 파일 bytes 는 디스크 임시 파일로) ─────────
SESSION_DIR = os.path.join(tempfile.gettempdir(), "kwcgc_uploads")
os.makedirs(SESSION_DIR, exist_ok=True)


def _session_file_path(sess_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{sess_id}.pkl")


def load_uploaded() -> dict:
    sess_id = session.get("sess_id")
    if not sess_id:
        return {}
    path = _session_file_path(sess_id)
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return pickle.load(f)


def save_uploaded(data: dict):
    sess_id = session.get("sess_id")
    if not sess_id:
        sess_id = secrets.token_hex(16)
        session["sess_id"] = sess_id
    with open(_session_file_path(sess_id), "wb") as f:
        pickle.dump(data, f)


def clear_uploaded():
    sess_id = session.pop("sess_id", None)
    if sess_id:
        path = _session_file_path(sess_id)
        if os.path.exists(path):
            os.remove(path)


# ── 라우트 ────────────────────────────────────────────────────────
@app.route("/")
def index():
    if "company" not in session:
        return redirect(url_for("login"))
    # 최초 진입 시 default_skip 적용
    if "skips" not in session:
        session["skips"] = {d["id"]: True for d in DOCUMENTS if d.get("default_skip")}
    uploaded = load_uploaded()
    notes = session.get("notes", {})
    skips = session.get("skips", {})
    project_info = session.get("project_info", {})
    # 파일 이름만 템플릿에 전달 (bytes 는 X)
    file_names = {did: [name for name, _b in files] for did, files in uploaded.items()}
    return render_template(
        "upload.html",
        company=session["company"],
        documents=DOCUMENTS,
        file_names=file_names,
        notes=notes,
        skips=skips,
        project_info=project_info,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if auth.verify_login(username, password):
            session["company"] = username
            return redirect(url_for("index"))
        flash("아이디 또는 비밀번호가 올바르지 않습니다.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    clear_uploaded()
    # 채팅 컨텍스트 메모리 해제
    chat_id = session.get("chat_id")
    if chat_id:
        chat.clear(chat_id)
    session.clear()
    return redirect(url_for("login"))


@app.route("/upload/<doc_id>", methods=["POST"])
def upload_file(doc_id: str):
    """단일 서류 항목의 파일 업로드 (다중 가능)."""
    if "company" not in session:
        return jsonify({"ok": False, "error": "not logged in"}), 401

    if not any(d["id"] == doc_id for d in DOCUMENTS):
        return jsonify({"ok": False, "error": "invalid doc_id"}), 400

    files = request.files.getlist("files")
    uploaded = load_uploaded()
    existing = uploaded.get(doc_id, [])
    for f in files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ("pdf", "jpg", "jpeg", "png"):
            continue
        existing.append((f.filename, f.read()))
    uploaded[doc_id] = existing
    save_uploaded(uploaded)

    return jsonify({
        "ok": True,
        "files": [name for name, _b in existing],
    })


@app.route("/remove/<doc_id>/<int:idx>", methods=["POST"])
def remove_file(doc_id: str, idx: int):
    if "company" not in session:
        return jsonify({"ok": False}), 401
    uploaded = load_uploaded()
    files = uploaded.get(doc_id, [])
    if 0 <= idx < len(files):
        files.pop(idx)
        uploaded[doc_id] = files
        save_uploaded(uploaded)
    return jsonify({
        "ok": True,
        "files": [name for name, _b in uploaded.get(doc_id, [])],
    })


@app.route("/note/<doc_id>", methods=["POST"])
def save_note(doc_id: str):
    if "company" not in session:
        return jsonify({"ok": False}), 401
    notes = session.get("notes", {})
    notes[doc_id] = request.form.get("note", "")
    session["notes"] = notes
    return jsonify({"ok": True})


@app.route("/skip/<doc_id>", methods=["POST"])
def save_skip(doc_id: str):
    """해당없음 체크박스 상태 저장."""
    if "company" not in session:
        return jsonify({"ok": False}), 401
    skips = session.get("skips", {})
    skips[doc_id] = (request.form.get("skip") == "true")
    session["skips"] = skips
    return jsonify({"ok": True})


@app.route("/project-info", methods=["POST"])
def save_project_info():
    """공사명/준공일자/준공금액/PLP 여부 저장."""
    if "company" not in session:
        return jsonify({"ok": False}), 401
    info = session.get("project_info", {})
    field = request.form.get("field", "")
    if field in ("name", "date", "amount", "extension", "order_extension",
                 "land_fee", "road_material", "year"):
        info[field] = request.form.get("value", "")
    elif field == "plp":
        info["plp"] = (request.form.get("value", "") == "true")
    else:
        return jsonify({"ok": False, "error": "invalid field"}), 400
    session["project_info"] = info
    return jsonify({"ok": True})


@app.route("/review", methods=["POST"])
def review():
    if "company" not in session:
        return redirect(url_for("login"))

    uploaded = load_uploaded()
    skips = session.get("skips", {})
    project_info = session.get("project_info", {})
    company = session.get("company", "")

    # doc01 준공금액 검증용 — 단가표 기반 최종 공사비 미리 계산
    adjustment_required = _compute_adjustment_required(project_info)
    final_cost, final_cost_meta = unit_prices_store.compute_final_cost(
        project_info, adjustment_required
    )

    all_results = {}
    jobs = []
    for doc in DOCUMENTS:
        did = doc["id"]
        name = doc["name"]
        # 해당없음 체크된 서류는 SKIP 처리
        if skips.get(did):
            all_results[name] = [{
                "항목": "해당없음 처리",
                "결과": "SKIP",
                "추출값": "-",
                "비고": "사용자가 해당없음 체크",
            }]
            continue
        files = uploaded.get(did, [])
        jobs.append((did, name, files))

    # Gemini API 병렬 호출 — 서류별로 동시에 검토
    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = {
                executor.submit(
                    reviewer.review_document, did, name, files, project_info, company,
                    final_cost if did == "doc01" else None,
                    final_cost_meta if did == "doc01" else None,
                ): name
                for did, name, files in jobs
            }
            for future, name in futures.items():
                all_results[name] = future.result()

    session["review_results"] = all_results

    # 채팅 컨텍스트 구축 (검토 결과 + PDF 텍스트 추출본) — In-memory 보관
    try:
        old_chat_id = session.get("chat_id")
        if old_chat_id:
            chat.clear(old_chat_id)
        new_id = chat.new_chat_id()
        context = chat.build_context(all_results, uploaded, project_info, DOCUMENTS)
        chat.save(new_id, context)
        session["chat_id"] = new_id
    except Exception:
        # 채팅 컨텍스트 빌드 실패가 검토 자체를 막으면 안 됨
        session.pop("chat_id", None)

    return redirect(url_for("result"))


@app.route("/result")
def result():
    if "company" not in session:
        return redirect(url_for("login"))
    all_results = session.get("review_results")
    if not all_results:
        return redirect(url_for("index"))

    # 서류 단위 집계 (각 슬롯은 서류 개수, 합 ≤ DOCUMENTS 총 개수)
    counts = {"OK": 0, "NG": 0, "WARN": 0, "SKIP": 0}
    for _doc_name, rows in all_results.items():
        if not rows:
            continue
        has_ng   = any(r["결과"] == "NG"   for r in rows)
        has_warn = any(r["결과"] == "WARN" for r in rows)
        all_skip = all(r["결과"] == "SKIP" for r in rows)
        if all_skip:
            counts["SKIP"] += 1   # 해당 없음
        elif has_ng:
            counts["NG"] += 1     # 보완 또는 확인 (NG 포함)
        elif has_warn:
            counts["WARN"] += 1   # 보완 또는 확인 (WARN 만)
        else:
            counts["OK"] += 1     # 양호
    verdict_pass = (counts["NG"] + counts["WARN"]) == 0

    # 첨부 파일명 (미리보기용)
    uploaded = load_uploaded()
    file_names = {did: [name for name, _b in files] for did, files in uploaded.items()}

    # 정산 여부 + 최종 공사비 계산 — 헬퍼로 통합 (reviewer 와 공통 사용)
    project_info = session.get("project_info", {})
    adjustment_required = _compute_adjustment_required(project_info)
    if adjustment_required is True:
        adjustment_label = "정산 대상"
    elif adjustment_required is False:
        adjustment_label = "정산 비대상"
    else:
        adjustment_label = "-"
    final_cost, final_cost_meta = unit_prices_store.compute_final_cost(
        project_info, adjustment_required
    )

    return render_template(
        "result.html",
        company=session["company"],
        all_results=all_results,
        counts=counts,
        verdict_pass=verdict_pass,
        documents=DOCUMENTS,
        file_names=file_names,
        project_info=project_info,
        adjustment_label=adjustment_label,
        adjustment_required=adjustment_required,
        final_cost=final_cost,
        final_cost_meta=final_cost_meta,
    )


@app.route("/preview/<doc_id>/<int:idx>")
def preview_file(doc_id: str, idx: int):
    """첨부 파일 미리보기 — 검토 결과 우측 패널에서 사용."""
    if "company" not in session:
        return "", 401
    uploaded = load_uploaded()
    files = uploaded.get(doc_id, [])
    if idx < 0 or idx >= len(files):
        return "", 404
    filename, file_bytes = files[idx]
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    mime = {
        "pdf":  "application/pdf",
        "jpg":  "image/jpeg",
        "jpeg": "image/jpeg",
        "png":  "image/png",
    }.get(ext, "application/octet-stream")
    response = send_file(
        io.BytesIO(file_bytes),
        mimetype=mime,
        download_name=filename,
        as_attachment=False,
    )
    # 1시간 브라우저 캐시 (같은 파일 재요청 시 즉시 표시)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.route("/warmup", methods=["POST"])
def warmup():
    """Gemini 모델 워밍업 — 결과 검토 전 cold start 줄임."""
    if "company" not in session:
        return jsonify({"ok": False}), 401
    if not config.GEMINI_API_KEY:
        return jsonify({"ok": False, "reason": "no_key"})
    try:
        import google.generativeai as genai
        genai.configure(api_key=config.GEMINI_API_KEY)
        model = genai.GenerativeModel(config.GEMINI_MODEL)
        _ = model.generate_content("ping").text  # 최소 호출로 connection/auth 워밍업
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]})


# ── 검토 결과 채팅 ────────────────────────────────────────────
@app.route("/chat", methods=["POST"])
def chat_ask():
    if "company" not in session:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    chat_id = session.get("chat_id")
    if not chat_id:
        return jsonify({"ok": False, "error": "검토 결과가 없습니다. 먼저 검토를 진행해주세요."}), 400
    message = (request.form.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "메시지가 비어있습니다."}), 400
    if len(message) > 2000:
        return jsonify({"ok": False, "error": "메시지가 너무 깁니다 (2000자 이내)."}), 400
    res = chat.ask(chat_id, message, company=session.get("company", ""))
    if not res["ok"]:
        return jsonify({"ok": False, "error": res.get("error") or "알 수 없는 오류"}), 500
    return jsonify({"ok": True, "answer": res["answer"]})


@app.route("/chat/history", methods=["GET"])
def chat_history():
    """페이지 로드 시 이전 대화 복원용."""
    if "company" not in session:
        return jsonify({"ok": False, "history": []}), 401
    chat_id = session.get("chat_id")
    if not chat_id:
        return jsonify({"ok": True, "history": []})
    return jsonify({"ok": True, "history": chat.get_history(chat_id)})


@app.route("/download/pdf")
def download_pdf():
    if "company" not in session:
        return redirect(url_for("login"))
    uploaded = load_uploaded()
    if not uploaded:
        flash("첨부된 파일이 없습니다.", "error")
        return redirect(url_for("result"))

    # 검토 결과를 share_store 에 저장하고 QR URL 생성
    # 동일 세션 내 첫 다운로드 시에만 새 review_id 생성, 재다운로드 시 재사용
    share_url = None
    all_results = session.get("review_results") or {}
    project_info = session.get("project_info", {})
    company = session.get("company", "")
    if all_results:
        review_id = session.get("share_review_id")
        # 기존 ID 가 만료/삭제됐는지 확인
        if review_id and not share_store.load(review_id):
            review_id = None
        if not review_id:
            try:
                review_id = share_store.save({
                    "company": company,
                    "project_info": project_info,
                    "all_results": all_results,
                })
                session["share_review_id"] = review_id
            except Exception:
                review_id = None
        if review_id:
            share_url = url_for("share_result", review_id=review_id, _external=True)

    pdf_bytes = output.merge_attachments_to_pdf(
        uploaded, DOCUMENTS,
        share_url=share_url,
        project_name=project_info.get("name", ""),
    )
    return send_file(
        io.BytesIO(pdf_bytes),
        as_attachment=True,
        download_name="준공서류_첨부통합.pdf",
        mimetype="application/pdf",
    )


# ── 검토 결과 공유 (QR 스캔용, 인증 X) ──────────────────────────
@app.route("/r/<review_id>")
def share_result(review_id: str):
    """발주처용 read-only 검토 결과 페이지. UUID 가 비공개 보안 역할."""
    record = share_store.load(review_id)
    if not record:
        return render_template("share_expired.html"), 404
    payload = record.get("payload", {})
    all_results = payload.get("all_results", {})
    project_info = payload.get("project_info", {})

    counts = {"OK": 0, "NG": 0, "WARN": 0, "SKIP": 0}
    for _name, rows in all_results.items():
        if not rows:
            continue
        has_ng = any(r["결과"] == "NG" for r in rows)
        has_warn = any(r["결과"] == "WARN" for r in rows)
        all_skip = all(r["결과"] == "SKIP" for r in rows)
        if all_skip:
            counts["SKIP"] += 1
        elif has_ng:
            counts["NG"] += 1
        elif has_warn:
            counts["WARN"] += 1
        else:
            counts["OK"] += 1
    verdict_pass = (counts["NG"] == 0 and counts["WARN"] == 0)

    return render_template(
        "share_result.html",
        documents=DOCUMENTS,
        all_results=all_results,
        project_info=project_info,
        counts=counts,
        verdict_pass=verdict_pass,
        saved_at=record.get("saved_at", ""),
        expires_at=record.get("expires_at", ""),
        company=payload.get("company", ""),
    )


@app.route("/admin/prompts", methods=["GET"])
def admin_prompts():
    if not _is_admin():
        return redirect(url_for("index"))
    items = prompts_store.list_for_admin()
    return render_template(
        "admin_prompts.html",
        company=session["company"],
        items=items,
    )


@app.route("/admin/prompts/save", methods=["POST"])
def admin_prompts_save():
    if not _is_admin():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    doc_id = request.form.get("doc_id", "")
    prompt = request.form.get("prompt", "")
    if not any(d["id"] == doc_id for d in DOCUMENTS):
        return jsonify({"ok": False, "error": "invalid doc_id"}), 400
    all_prompts = prompts_store.load_all()
    if prompt.strip():
        all_prompts[doc_id] = prompt
    else:
        # 빈 값이면 기본값으로 되돌리기 (저장 dict 에서 제거)
        all_prompts.pop(doc_id, None)
    prompts_store.save_all(all_prompts)
    return jsonify({"ok": True})


@app.route("/admin/prompts/reset", methods=["POST"])
def admin_prompts_reset():
    """단일 서류 프롬프트를 기본값으로 초기화."""
    if not _is_admin():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    doc_id = request.form.get("doc_id", "")
    all_prompts = prompts_store.load_all()
    all_prompts.pop(doc_id, None)
    prompts_store.save_all(all_prompts)
    # 기본 프롬프트 반환
    doc = next((d for d in DOCUMENTS if d["id"] == doc_id), None)
    default_prompt = prompts_store.get_default(doc["name"], doc["id"]) if doc else ""
    return jsonify({"ok": True, "default_prompt": default_prompt})


# ── 연간단가표 관리자 라우트 ────────────────────────────────────
@app.route("/admin/unit-prices", methods=["GET"])
def admin_unit_prices():
    if "company" not in session:
        return redirect(url_for("login"))
    if not _is_admin():
        return redirect(url_for("index"))
    years = unit_prices_store.list_years()
    selected = request.args.get("year") or (years[0] if years else "")
    prices = unit_prices_store.get_year(selected) if selected else unit_prices_store._empty_year()
    return render_template(
        "admin_unit_prices.html",
        company=session["company"],
        years=years,
        selected_year=selected,
        prices=prices,
        length_keys=unit_prices_store.LENGTH_KEYS,
        material_keys=unit_prices_store.MATERIAL_KEYS,
        material_display=unit_prices_store.MATERIAL_DISPLAY,
        plp_key=unit_prices_store.PLP_KEY,
    )


@app.route("/admin/unit-prices/save", methods=["POST"])
def admin_unit_prices_save():
    if not _is_admin():
        return jsonify({"ok": False}), 403
    year = (request.form.get("year") or "").strip()
    if not year:
        return jsonify({"ok": False, "error": "연도 필수"}), 400

    prices = {}
    for mat in unit_prices_store.MATERIAL_KEYS:
        prices[mat] = {}
        for lk in unit_prices_store.LENGTH_KEYS:
            raw = (request.form.get(f"{mat}__{lk}") or "0").replace(",", "").strip()
            try:
                prices[mat][lk] = int(raw) if raw else 0
            except ValueError:
                prices[mat][lk] = 0
    # PLP 옵션은 연도 최상위 단일 필드 (도로재질/연장 무관)
    plp_raw = (request.form.get(unit_prices_store.PLP_KEY) or "0").replace(",", "").strip()
    try:
        prices[unit_prices_store.PLP_KEY] = int(plp_raw) if plp_raw else 0
    except ValueError:
        prices[unit_prices_store.PLP_KEY] = 0

    unit_prices_store.set_year(year, prices)
    return jsonify({"ok": True})


@app.route("/admin/unit-prices/add", methods=["POST"])
def admin_unit_prices_add():
    if not _is_admin():
        return jsonify({"ok": False}), 403
    year = (request.form.get("year") or "").strip()
    if not year.isdigit() or len(year) != 4:
        return jsonify({"ok": False, "error": "4자리 연도를 입력하세요"}), 400
    added = unit_prices_store.add_year(year)
    return jsonify({"ok": True, "added": added})


@app.route("/admin/unit-prices/delete", methods=["POST"])
def admin_unit_prices_delete():
    if not _is_admin():
        return jsonify({"ok": False}), 403
    year = (request.form.get("year") or "").strip()
    if not year:
        return jsonify({"ok": False, "error": "연도 필수"}), 400
    unit_prices_store.delete_year(year)
    return jsonify({"ok": True})


@app.route("/admin/unit-prices/reset", methods=["POST"])
def admin_unit_prices_reset():
    """해당 연도의 단가만 0으로 초기화. 연도 자체는 유지."""
    if not _is_admin():
        return jsonify({"ok": False}), 403
    year = (request.form.get("year") or "").strip()
    if not year:
        return jsonify({"ok": False, "error": "연도 필수"}), 400
    ok = unit_prices_store.reset_year(year)
    if not ok:
        return jsonify({"ok": False, "error": "존재하지 않는 연도"}), 404
    return jsonify({"ok": True})


# ── API 사용량 관리자 라우트 ────────────────────────────────────
@app.route("/admin/usage", methods=["GET"])
def admin_usage():
    if "company" not in session:
        return redirect(url_for("login"))
    if not _is_admin():
        return redirect(url_for("index"))
    return render_template(
        "admin_usage.html",
        company=session["company"],
        summary=usage_store.summary(),
        daily=usage_store.daily(30),
        by_doc=usage_store.by_doc("month"),
        by_company=usage_store.by_company("month"),
    )


@app.route("/status")
def status():
    """AI 검토 활성 여부 진단용."""
    key = config.GEMINI_API_KEY or ""
    return jsonify({
        "ai_enabled": not config.USE_MOCK,
        "model": config.GEMINI_MODEL,
        "api_key_set": bool(key),
        "api_key_length": len(key),
        "api_key_preview": (key[:6] + "..." + key[-4:]) if len(key) > 10 else "",
    })


@app.route("/api-key", methods=["POST"])
def set_api_key():
    """Gemini API 키 입력 (선택)."""
    if "company" not in session:
        return jsonify({"ok": False}), 401
    key = request.form.get("key", "").strip()
    if key:
        config.GEMINI_API_KEY = key
        config.USE_MOCK = False
    else:
        config.USE_MOCK = True
    return jsonify({"ok": True, "mock": config.USE_MOCK})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    # Railway/외부 호스팅: 0.0.0.0, 로컬: 127.0.0.1
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    app.run(host=host, port=port, debug=debug)
