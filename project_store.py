"""
공사 저장소 — 검토 시작 시점에 자동 저장 → 60일 후 만료.

저장 구조:
  /data/projects/
    └ {uuid}/
        ├ meta.json      ← company, project_info, all_results, file_names, 저장일, 만료일
        └ files.pkl      ← {doc_id: [(filename, bytes), ...]}  (업로드 파일 원본)

정책:
- 같은 (회사명 + 공사명) 조합은 동일 uuid 재사용 → 완전 덮어쓰기
- TTL 60일 (마지막 저장일 기준)
- 시공사: 자기 회사 공사만 조회
- 관리자: 전체 조회

환경변수 PROJECTS_DIR 로 경로 오버라이드 가능.
"""
import datetime
import hashlib
import json
import os
import pickle
import shutil
import uuid as _uuid


PROJECT_TTL_DAYS = 60


def _resolve_dir() -> str:
    env_path = os.environ.get("PROJECTS_DIR")
    if env_path:
        os.makedirs(env_path, exist_ok=True)
        return env_path
    if os.path.isdir("/data"):
        p = "/data/projects"
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "projects")
    os.makedirs(p, exist_ok=True)
    return p


PROJECTS_DIR = _resolve_dir()


def _project_path(project_id: str) -> str:
    if not project_id or not project_id.replace("-", "").isalnum():
        return ""
    return os.path.join(PROJECTS_DIR, project_id)


def _key(company: str, project_name: str) -> str:
    """회사+공사명 정규화 키 — 공백 제거 + 소문자."""
    norm_c = "".join((company or "").split()).lower()
    norm_n = "".join((project_name or "").split()).lower()
    return f"{norm_c}||{norm_n}"


def _hash_key(key: str) -> str:
    """회사+공사명 키 → 결정적 uuid 형태 ID (16자 hex)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _read_meta(project_id: str) -> dict | None:
    path = _project_path(project_id)
    if not path:
        return None
    meta_path = os.path.join(path, "meta.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save(company: str,
         project_info: dict,
         files: dict,
         all_results: dict) -> str | None:
    """저장 — 같은 (회사+공사명) 이면 덮어쓰기, 아니면 신규 ID 발급.

    files: {doc_id: [(filename, bytes), ...]}
    반환: project_id (실패 시 None)
    """
    name = (project_info or {}).get("name", "").strip()
    if not company or not name:
        return None   # 회사명/공사명 없으면 저장 X

    project_id = _hash_key(_key(company, name))
    project_dir = _project_path(project_id)
    if not project_dir:
        return None

    try:
        os.makedirs(project_dir, exist_ok=True)

        # 파일 이름만 별도 추출 (UI 표시용 / bytes 없이도 메타로 조회 가능)
        file_names = {
            did: [fn for fn, _b in flist]
            for did, flist in (files or {}).items()
        }

        now = datetime.datetime.now()
        expires = now + datetime.timedelta(days=PROJECT_TTL_DAYS)
        meta = {
            "project_id": project_id,
            "company": company,
            "project_name": name,
            "project_info": project_info or {},
            "all_results": all_results or {},
            "file_names": file_names,
            "saved_at": now.isoformat(timespec="seconds"),
            "expires_at": expires.isoformat(timespec="seconds"),
        }
        with open(os.path.join(project_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

        # 파일 bytes 는 별도 pickle (Volume)
        with open(os.path.join(project_dir, "files.pkl"), "wb") as f:
            pickle.dump(files or {}, f)

        return project_id
    except Exception:
        return None


def load(project_id: str) -> dict | None:
    """저장된 공사 불러오기 — 만료된 경우 None.
    반환: {project_id, company, project_info, all_results, files, saved_at, expires_at}
    """
    meta = _read_meta(project_id)
    if not meta:
        return None

    # 만료 체크
    try:
        expires_at = datetime.datetime.fromisoformat(meta.get("expires_at", ""))
        if datetime.datetime.now() > expires_at:
            return None
    except (ValueError, TypeError):
        return None

    # 파일 bytes 로드
    files = {}
    files_path = os.path.join(_project_path(project_id), "files.pkl")
    if os.path.isfile(files_path):
        try:
            with open(files_path, "rb") as f:
                files = pickle.load(f)
        except Exception:
            files = {}

    return {
        "project_id": meta.get("project_id"),
        "company": meta.get("company"),
        "project_name": meta.get("project_name"),
        "project_info": meta.get("project_info") or {},
        "all_results": meta.get("all_results") or {},
        "file_names": meta.get("file_names") or {},
        "saved_at": meta.get("saved_at"),
        "expires_at": meta.get("expires_at"),
        "files": files,
    }


def list_for(company: str | None = None,
             include_all: bool = False) -> list[dict]:
    """공사 목록 — 만료된 항목은 자동 cleanup 후 반환.

    include_all=True 면 회사 무관 전체 (관리자용).
    그 외 company 가 일치하는 것만.
    반환: [{project_id, company, project_name, saved_at, expires_at, file_count}, ...]
    """
    items = []
    now = datetime.datetime.now()
    try:
        for entry in os.listdir(PROJECTS_DIR):
            project_dir = os.path.join(PROJECTS_DIR, entry)
            if not os.path.isdir(project_dir):
                continue
            meta = _read_meta(entry)
            if not meta:
                continue
            # 만료 체크 — 만료된 항목 폴더 삭제 (lazy cleanup)
            try:
                expires_at = datetime.datetime.fromisoformat(meta.get("expires_at", ""))
                if now > expires_at:
                    shutil.rmtree(project_dir, ignore_errors=True)
                    continue
            except (ValueError, TypeError):
                continue
            # 회사 필터
            if not include_all:
                if not company or meta.get("company") != company:
                    continue
            # 파일 수 카운트
            file_names = meta.get("file_names") or {}
            file_count = sum(len(v) for v in file_names.values())
            items.append({
                "project_id": meta.get("project_id"),
                "company": meta.get("company"),
                "project_name": meta.get("project_name"),
                "saved_at": meta.get("saved_at"),
                "expires_at": meta.get("expires_at"),
                "file_count": file_count,
            })
    except Exception:
        pass
    # 최신순
    items.sort(key=lambda x: x.get("saved_at") or "", reverse=True)
    return items


def cleanup_expired() -> int:
    """만료 항목 일괄 삭제. 반환: 삭제 개수."""
    deleted = 0
    now = datetime.datetime.now()
    try:
        for entry in os.listdir(PROJECTS_DIR):
            project_dir = os.path.join(PROJECTS_DIR, entry)
            if not os.path.isdir(project_dir):
                continue
            meta = _read_meta(entry)
            if not meta:
                continue
            try:
                expires_at = datetime.datetime.fromisoformat(meta.get("expires_at", ""))
                if now > expires_at:
                    shutil.rmtree(project_dir, ignore_errors=True)
                    deleted += 1
            except (ValueError, TypeError):
                continue
    except Exception:
        pass
    return deleted
