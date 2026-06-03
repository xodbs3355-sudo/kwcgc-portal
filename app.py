"""
도시가스 공사 준공서류 검토 포털 — Flask 버전
"""
import io
import json
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
import project_store
from documents import DOCUMENTS


def _is_admin() -> bool:
    return auth.is_admin(session.get("company", ""))


def _apply_overrides(all_results: dict, overrides: dict | None) -> dict:
    """all_results 에 검토자 수동 OK 처리(manual_overrides) 적용.
    overrides = {doc_name: [item_idx, ...]}
    토글된 항목의 '결과'를 OK로 변경하고 '검토자_확인' = True, '원본_결과'를 보존.
    """
    if not overrides:
        return all_results
    out = {}
    for doc_name, rows in all_results.items():
        override_set = set(overrides.get(doc_name) or [])
        if not override_set:
            out[doc_name] = rows
            continue
        new_rows = []
        for i, row in enumerate(rows):
            if i in override_set and row.get("결과") != "OK":
                new_row = dict(row)
                new_row["원본_결과"] = row.get("결과")
                new_row["결과"] = "OK"
                new_row["검토자_확인"] = True
                new_rows.append(new_row)
            else:
                new_rows.append(row)
        out[doc_name] = new_rows
    return out


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
    # 부팅 시 1회 계산 캐시 반환 — CSS/JS 수정 시 Flask 재시작 필요.
    # (Railway 배포 = git push → 자동 재시작이라 운영에선 자동 반영됨)
    return {"asset_version": _ASSET_VERSION_CACHE["value"]}


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


def _session_meta_path(sess_id: str) -> str:
    """파일 이름만 보관하는 경량 메타 (file_names 만 필요한 페이지용)."""
    return os.path.join(SESSION_DIR, f"{sess_id}.names.json")


def load_uploaded() -> dict:
    """전체 업로드 데이터 (bytes 포함) — Gemini 검토/미리보기/PDF 출력용."""
    sess_id = session.get("sess_id")
    if not sess_id:
        return {}
    path = _session_file_path(sess_id)
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return pickle.load(f)


def load_file_names() -> dict:
    """파일 이름만 빠르게 로드 — bytes 가 필요 없는 페이지 (입력/결과) 에서 사용.

    수십MB 짜리 pickle 전체를 deserialize 하지 않고 작은 JSON 만 읽음.
    옛 세션 (meta.json 없음) 은 pkl 에서 추출하여 자동 마이그레이션.
    반환: {doc_id: [filename, ...]}
    """
    sess_id = session.get("sess_id")
    if not sess_id:
        return {}
    meta_path = _session_meta_path(sess_id)
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            pass
    # 마이그레이션: 옛 세션은 pkl 에서 이름 추출 후 meta 생성
    pkl_path = _session_file_path(sess_id)
    if not os.path.exists(pkl_path):
        return {}
    try:
        with open(pkl_path, "rb") as f:
            uploaded = pickle.load(f)
        names = {did: [name for name, _b in flist] for did, flist in uploaded.items()}
        _write_file_names(sess_id, names)
        return names
    except Exception:
        return {}


def _write_file_names(sess_id: str, names: dict) -> None:
    """meta.json 만 단독 쓰기 (save_uploaded 와 분리해서 사용 가능)."""
    try:
        with open(_session_meta_path(sess_id), "w", encoding="utf-8") as f:
            json.dump(names, f, ensure_ascii=False)
    except OSError:
        pass


def save_uploaded(data: dict):
    sess_id = session.get("sess_id")
    if not sess_id:
        sess_id = secrets.token_hex(16)
        session["sess_id"] = sess_id
    with open(_session_file_path(sess_id), "wb") as f:
        pickle.dump(data, f)
    # 파일 이름만 별도 저장 (다음 페이지 진입을 빠르게)
    names = {did: [name for name, _b in flist] for did, flist in data.items()}
    _write_file_names(sess_id, names)


def clear_uploaded():
    sess_id = session.pop("sess_id", None)
    if sess_id:
        for path in (_session_file_path(sess_id), _session_meta_path(sess_id)):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


# ── 라우트 ────────────────────────────────────────────────────────
@app.route("/")
def index():
    if "company" not in session:
        return redirect(url_for("login"))
    # 최초 진입 시 default_skip 적용
    if "skips" not in session:
        session["skips"] = {d["id"]: True for d in DOCUMENTS if d.get("default_skip")}
    # 파일 bytes 불필요 — 이름만 빠른 경로로 로드 (큰 pickle deserialize 회피)
    file_names = load_file_names()
    notes = session.get("notes", {})
    skips = session.get("skips", {})
    project_info = session.get("project_info", {})
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
                 "land_fee", "safety_fee", "road_material", "year"):
        info[field] = request.form.get("value", "")
    elif field in ("plp", "permit_fee"):
        info[field] = (request.form.get("value", "") == "true")
    else:
        return jsonify({"ok": False, "error": "invalid field"}), 400
    session["project_info"] = info
    return jsonify({"ok": True})


@app.route("/review", methods=["POST"])
def review():
    if "company" not in session:
        return redirect(url_for("login"))

    # ── 입력값 동기 보장 (race 해결) ──
    # 검토 시작 폼이 모든 project_info 값을 hidden 으로 같이 보냄 →
    # blur fetch 의 도달 여부와 무관하게 검토 시작 시점의 최신 값을 보장.
    _PROJECT_INFO_FIELDS = (
        "name", "date", "amount", "extension", "order_extension",
        "land_fee", "safety_fee", "road_material", "year",
    )
    _PROJECT_INFO_BOOL_FIELDS = ("plp", "permit_fee")
    if (any(f in request.form for f in _PROJECT_INFO_FIELDS)
            or any(f in request.form for f in _PROJECT_INFO_BOOL_FIELDS)):
        _info = session.get("project_info", {})
        for _field in _PROJECT_INFO_FIELDS:
            if _field in request.form:
                _info[_field] = request.form.get(_field, "")
        for _bf in _PROJECT_INFO_BOOL_FIELDS:
            if _bf in request.form:
                _info[_bf] = (request.form.get(_bf) == "true")
        session["project_info"] = _info

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

    # 공사 저장소 — 같은 (회사+공사명) 이면 덮어쓰기, 60일 보관
    # 공사 불러온 상태 (session 에 project_id 존재) 면 공사명 바뀌어도
    # 같은 ID 로 업데이트 (오타 보완 케이스 등)
    try:
        project_id = project_store.save(
            company=company,
            project_info=project_info,
            files=uploaded,
            all_results=all_results,
            project_id=session.get("project_id"),
        )
        if project_id:
            session["project_id"] = project_id
    except Exception:
        # 저장 실패가 검토를 막으면 안 됨
        pass

    return redirect(url_for("result"))


@app.route("/review/override", methods=["POST"])
def review_override():
    """검토자가 카드별로 'OK 처리' 토글 — session 에 manual_overrides 누적."""
    if "company" not in session:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    data = request.get_json(silent=True) or {}
    doc_name = (data.get("doc_name") or "").strip()
    item_idx = data.get("item_idx")
    override = bool(data.get("override"))
    if not doc_name or item_idx is None:
        return jsonify({"ok": False, "error": "doc_name / item_idx 필요"}), 400
    try:
        item_idx = int(item_idx)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "item_idx 정수 아님"}), 400

    overrides = session.get("manual_overrides", {}) or {}
    doc_overrides = list(overrides.get(doc_name, []))
    if override:
        if item_idx not in doc_overrides:
            doc_overrides.append(item_idx)
    else:
        doc_overrides = [i for i in doc_overrides if i != item_idx]
    if doc_overrides:
        overrides[doc_name] = doc_overrides
    else:
        overrides.pop(doc_name, None)
    session["manual_overrides"] = overrides
    return jsonify({"ok": True, "override": override})


@app.route("/result")
def result():
    if "company" not in session:
        return redirect(url_for("login"))
    all_results = session.get("review_results")
    if not all_results:
        # 시공사: 검토 결과 없으면 1번 탭으로 보냄
        # 관리자: 테스트/검수용으로 빈 상태 진입 허용
        if not _is_admin():
            return redirect(url_for("index"))
        all_results = {}

    # 검토자 수동 OK 처리(manual_overrides) 적용
    overrides = session.get("manual_overrides") or {}
    all_results = _apply_overrides(all_results, overrides)

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

    # 첨부 파일명 (미리보기용) — bytes 불필요, 빠른 경로 로드
    file_names = load_file_names()

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
    # conditional=True → Range 요청 지원
    # (PDF.js 가 스캔본 큰 PDF 의 첫 페이지에 필요한 부분만 먼저 받아서 빠르게 렌더링)
    response = send_file(
        io.BytesIO(file_bytes),
        mimetype=mime,
        download_name=filename,
        as_attachment=False,
        conditional=True,
    )
    # ETag — 같은 파일 재요청 시 304 응답으로 절약
    response.set_etag(f"preview-{doc_id}-{idx}-{len(file_bytes)}")
    # 1시간 브라우저 캐시 (같은 파일 재요청 시 즉시 표시)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.after_request
def _static_long_cache(response):
    """정적 자산 강력 캐시 (1년 immutable) — 첫 진입 후엔 304 검증 요청조차 안 함.

    - /static/pdfjs/ : 버전 고정 디렉토리. 변경 없음 → 안전.
    - /static/css/, /static/js/ : style.css?v=<mtime>, app.js?v=<mtime>
      형태로 이미 asset_version 쿼리 무효화 사용 중. 파일 수정 시 mtime
      이 바뀌어 ?v= 값이 변경 → 브라우저가 새 URL 로 인식 → 새로 받음.
      따라서 1년 immutable 캐시 적용해도 수정 즉시 반영됨.
    """
    try:
        path = request.path
        if (path.startswith("/static/pdfjs/")
                or path.startswith("/static/css/")
                or path.startswith("/static/js/")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    except Exception:
        pass
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
    overrides = session.get("manual_overrides") or {}
    all_results = _apply_overrides(all_results, overrides)  # 검토자 OK 처리 반영
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


# ── 공사 저장소 (자동 저장된 공사 목록/복원) ─────────────────────
@app.route("/project/list", methods=["GET"])
def project_list():
    """저장된 공사 목록 — 시공사는 자기 회사, 관리자는 전체.
    반환: {ok, items, is_admin}
    """
    if "company" not in session:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    company = session.get("company", "")
    is_admin = _is_admin()
    items = project_store.list_for(
        company=company,
        include_all=is_admin,
    )
    return jsonify({"ok": True, "items": items, "is_admin": is_admin})


@app.route("/project/load/<project_id>", methods=["POST"])
def project_load(project_id: str):
    """저장된 공사를 세션에 복원."""
    if "company" not in session:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    data = project_store.load(project_id)
    if not data:
        return jsonify({"ok": False, "error": "공사를 찾을 수 없거나 만료됨"}), 404
    # 권한 — 시공사는 자기 회사 공사만 (관리자는 전체)
    if not _is_admin() and data.get("company") != session.get("company"):
        return jsonify({"ok": False, "error": "권한 없음"}), 403

    # 세션 + 임시 디스크에 복원
    session["project_info"] = data.get("project_info") or {}
    session["review_results"] = data.get("all_results") or {}
    save_uploaded(data.get("files") or {})
    session["project_id"] = project_id
    # skips 는 새로 default 로 — 사용자가 다시 체크할 수 있게
    session["skips"] = {d["id"]: True for d in DOCUMENTS if d.get("default_skip")}

    # 채팅 컨텍스트 재구축
    try:
        old_chat_id = session.get("chat_id")
        if old_chat_id:
            chat.clear(old_chat_id)
        if data.get("all_results"):
            new_id = chat.new_chat_id()
            context = chat.build_context(
                data.get("all_results") or {},
                data.get("files") or {},
                data.get("project_info") or {},
                DOCUMENTS,
            )
            chat.save(new_id, context)
            session["chat_id"] = new_id
    except Exception:
        pass

    # 검토 결과 있으면 result, 없으면 upload 페이지로
    target = "result" if data.get("all_results") else "index"
    return jsonify({"ok": True, "redirect": url_for(target)})


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
    # 현재 기본값 버전을 함께 기록 (빈 값이면 기본값 복원)
    prompts_store.save_prompt(doc_id, prompt)
    return jsonify({"ok": True})


@app.route("/admin/prompts/reset", methods=["POST"])
def admin_prompts_reset():
    """단일 서류 프롬프트를 기본값으로 초기화."""
    if not _is_admin():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    doc_id = request.form.get("doc_id", "")
    prompts_store.reset_prompt(doc_id)
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
        right_column_items=unit_prices_store.RIGHT_COLUMN_ITEMS,
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
    # 우측 컬럼 단일 항목들 (PLP / ASP 쪽포장 / 영구도로점용허가신청수수료) — 도로재질·연장 무관
    for item in unit_prices_store.RIGHT_COLUMN_ITEMS:
        raw = (request.form.get(item["key"]) or "0").replace(",", "").strip()
        try:
            prices[item["key"]] = int(raw) if raw else 0
        except ValueError:
            prices[item["key"]] = 0

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

    # doc_id → 한글 서류명 매핑 (chat 같은 비-document id 는 별도 표기)
    doc_name_map = {d["id"]: d["name"] for d in DOCUMENTS}
    doc_name_map["chat"] = "AI 챗봇"

    # 1회 파일 read 로 4종 집계 동시 계산 (기존: _load_all() 4번)
    metrics = usage_store.compute_admin_metrics(daily_days=30, period="month")
    for row in metrics["by_doc"]:
        row["display"] = doc_name_map.get(row["doc"], row["doc"])

    # Gemini 모델 ID → 화면 표시명
    model_display_map = {
        "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "gemini-2.5-pro": "Gemini 2.5 Pro",
    }
    model_display = model_display_map.get(config.GEMINI_MODEL, config.GEMINI_MODEL)

    return render_template(
        "admin_usage.html",
        company=session["company"],
        summary=metrics["summary"],
        daily=metrics["daily"],
        by_doc=metrics["by_doc"],
        by_company=metrics["by_company"],
        model_display=model_display,
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    # Railway/외부 호스팅: 0.0.0.0, 로컬: 127.0.0.1
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    app.run(host=host, port=port, debug=debug)
