COMPANIES = {
    '국도': '0000',
    '태광': '0000',
    '성진': '0000',
    '그린': '0000',
    '대림': '0000',
    '공무': '0000',  # 관리자
}

ADMIN_USERS = {'공무'}


def verify_login(username: str, password: str) -> bool:
    stored = COMPANIES.get(username)
    if not stored:
        return False
    return stored == password


def is_admin(username: str) -> bool:
    return username in ADMIN_USERS
