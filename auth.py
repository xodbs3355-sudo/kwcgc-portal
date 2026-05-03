COMPANIES = {
    '국도': 'gukdo2024',
    '태광': 'taekwang2024',
    '성진': 'sungjin2024',
    '그린': 'green2024',
    '대림': 'daelim2024',
}


def verify_login(username: str, password: str) -> bool:
    stored = COMPANIES.get(username)
    if not stored:
        return False
    return stored == password
