"""
도시가스 공사 준공서류 검토 포털 — Flask 버전
"""
import io
import os
import pickle
import secrets
import tempfile

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, flash, jsonify
)

import config
import auth
import reviewer
import output
from documents import DOCUMENTS


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024 * 16  # 행당 200MB × 16 여유


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
    """공사명/준공일자/준공금액 저장."""
    if "company" not in session:
        return jsonify({"ok": False}), 401
    info = session.get("project_info", {})
    field = request.form.get("field", "")
    if field in ("name", "date", "amount"):
        info[field] = request.form.get("value", "")
        session["project_info"] = info
    return jsonify({"ok": True})


@app.route("/review", methods=["POST"])
def review():
    if "company" not in session:
        return redirect(url_for("login"))

    uploaded = load_uploaded()
    skips = session.get("skips", {})

    all_results = {}
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
        all_results[name] = reviewer.review_document(did, name, files)

    session["review_results"] = all_results
    return redirect(url_for("result"))


@app.route("/result")
def result():
    if "company" not in session:
        return redirect(url_for("login"))
    all_results = session.get("review_results")
    if not all_results:
        return redirect(url_for("index"))

    all_items = [r for rows in all_results.values() for r in rows]
    counts = {
        "OK":   sum(1 for r in all_items if r["결과"] == "OK"),
        "NG":   sum(1 for r in all_items if r["결과"] == "NG"),
        "WARN": sum(1 for r in all_items if r["결과"] == "WARN"),
        "SKIP": sum(1 for r in all_items if r["결과"] == "SKIP"),
    }
    verdict_pass = counts["NG"] == 0

    # 첨부 파일명 (미리보기용)
    uploaded = load_uploaded()
    file_names = {did: [name for name, _b in files] for did, files in uploaded.items()}

    return render_template(
        "result.html",
        company=session["company"],
        all_results=all_results,
        counts=counts,
        verdict_pass=verdict_pass,
        documents=DOCUMENTS,
        file_names=file_names,
    )


@app.route("/download/excel")
def download_excel():
    if "company" not in session:
        return redirect(url_for("login"))
    all_results = session.get("review_results")
    if not all_results:
        return redirect(url_for("index"))

    excel_bytes = output.results_to_excel(all_results)
    return send_file(
        io.BytesIO(excel_bytes),
        as_attachment=True,
        download_name="준공서류검토결과.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    return send_file(
        io.BytesIO(file_bytes),
        mimetype=mime,
        download_name=filename,
        as_attachment=False,
    )


@app.route("/download/pdf")
def download_pdf():
    if "company" not in session:
        return redirect(url_for("login"))
    uploaded = load_uploaded()
    if not uploaded:
        flash("첨부된 파일이 없습니다.", "error")
        return redirect(url_for("result"))

    pdf_bytes = output.merge_attachments_to_pdf(uploaded, DOCUMENTS)
    return send_file(
        io.BytesIO(pdf_bytes),
        as_attachment=True,
        download_name="준공서류_첨부통합.pdf",
        mimetype="application/pdf",
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
