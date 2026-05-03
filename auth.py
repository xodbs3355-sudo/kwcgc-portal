import hashlib

COMPANIES = {
    '국도': hashlib.sha256('국도2024'.encode()).hexdigest(),
    '태광': hashlib.sha256('태광2024'.encode()).hexdigest(),
    '성진': hashlib.sha256('성진2024'.encode()).hexdigest(),
    '그린': hashlib.sha256('그린2024'.encode()).hexdigest(),
    '대림': hashlib.sha256('대림2024'.encode()).hexdigest(),
}


def verify_login(username: str, password: str) -> bool:
    stored = COMPANIES.get(username)
    if not stored:
        return False
    return stored == hashlib.sha256(password.encode()).hexdigest()
