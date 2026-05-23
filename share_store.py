"""
검토 결과 영구 저장 + 공유 링크 (QR용).

저장 위치:
- Railway: /data/reviews/{uuid}.json
- 로컬:    ./data/reviews/{uuid}.json
- 환경변수 REVIEWS_DIR 로 오버라이드 가능

유효기간: 90일 (저장 시각 + 90일 경과 시 만료 처리)
"""
import datetime
import json
import os
import uuid


SHARE_TTL_DAYS = 90


def _resolve_dir() -> str:
    env_path = os.environ.get("REVIEWS_DIR")
    if env_path:
        os.makedirs(env_path, exist_ok=True)
        return env_path
    if os.path.isdir("/data"):
        p = "/data/reviews"
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reviews")
    os.makedirs(p, exist_ok=True)
    return p


REVIEWS_DIR = _resolve_dir()


def _file_path(review_id: str) -> str:
    # uuid 형식 검증 — 영숫자만
    if not review_id or not review_id.replace("-", "").isalnum():
        return ""
    return os.path.join(REVIEWS_DIR, f"{review_id}.json")


def save(payload: dict) -> str:
    """검토 결과를 영구 저장하고 review_id 반환.

    payload 예시:
    {
        "company": "공무",
        "project_info": {...},
        "all_results": {...},
        "counts": {...},
        "verdict_pass": bool,
        ...
    }
    """
    review_id = uuid.uuid4().hex
    record = {
        "review_id": review_id,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "expires_at": (datetime.datetime.now()
                       + datetime.timedelta(days=SHARE_TTL_DAYS)).isoformat(timespec="seconds"),
        "payload": payload,
    }
    path = _file_path(review_id)
    if not path:
        raise ValueError("invalid review_id")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    return review_id


def load(review_id: str) -> dict | None:
    """review_id 로 결과 조회. 만료된 경우 None 반환."""
    path = _file_path(review_id)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            record = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # 만료 체크
    try:
        expires_at = datetime.datetime.fromisoformat(record.get("expires_at", ""))
        if datetime.datetime.now() > expires_at:
            return None
    except (ValueError, TypeError):
        return None

    return record


def cleanup_expired() -> int:
    """만료된 파일 삭제. 반환: 삭제된 개수."""
    deleted = 0
    now = datetime.datetime.now()
    try:
        for name in os.listdir(REVIEWS_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(REVIEWS_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    record = json.load(f)
                expires_at = datetime.datetime.fromisoformat(
                    record.get("expires_at", "")
                )
                if now > expires_at:
                    os.remove(path)
                    deleted += 1
            except Exception:
                continue
    except Exception:
        pass
    return deleted
