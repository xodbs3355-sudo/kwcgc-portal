"""
도시가스 공사 준공서류 검토 포털
"""
import streamlit as st

import config
import auth
import parser as xlsx_parser
import reviewer
import output
from email_sender import send_review_email

st.set_page_config(
    page_title="준공서류 검토 포털",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Linear 스타일 CSS ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

/* 전체 기본 */
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    color: #1a1a1a;
}
.stApp { background: #fff; }

/* 헤더 영역 */
.portal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 0 14px 0;
    border-bottom: 1px solid #e5e5e8;
    margin-bottom: 32px;
}
.portal-logo {
    font-size: 15px;
    font-weight: 600;
    color: #1a1a1a;
    letter-spacing: -0.3px;
}
.portal-company-badge {
    font-size: 12px;
    color: #6b6f7a;
    background: #f7f7f9;
    border: 1px solid #e5e5e8;
    border-radius: 6px;
    padding: 4px 10px;
}

/* 로그인 카드 */
.login-wrap {
    max-width: 360px;
    margin: 80px auto 0;
}
.login-title {
    font-size: 20px;
    font-weight: 600;
    color: #1a1a1a;
    margin-bottom: 4px;
    letter-spacing: -0.4px;
}
.login-sub {
    font-size: 13px;
    color: #6b6f7a;
    margin-bottom: 28px;
}

/* 섹션 타이틀 */
.section-title {
    font-size: 13px;
    font-weight: 600;
    color: #6b6f7a;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin-bottom: 12px;
}

/* 업로드 드롭존 */
.upload-hint {
    font-size: 12px;
    color: #9b9fa8;
    margin-top: 6px;
}

/* 결과 요약 카드 */
.result-summary {
    display: flex;
    gap: 10px;
    margin: 20px 0 28px;
}
.result-card {
    flex: 1;
    background: #f7f7f9;
    border: 1px solid #e5e5e8;
    border-radius: 8px;
    padding: 14px 16px;
    text-align: center;
}
.result-card-num {
    font-size: 26px;
    font-weight: 600;
    line-height: 1;
    margin-bottom: 4px;
}
.result-card-label {
    font-size: 11px;
    color: #6b6f7a;
    font-weight: 500;
}
.card-ok   { border-top: 3px solid #4cb87a; }
.card-ng   { border-top: 3px solid #e5484d; }
.card-warn { border-top: 3px solid #f5a623; }
.card-skip { border-top: 3px solid #9b9fa8; }
.num-ok    { color: #4cb87a; }
.num-ng    { color: #e5484d; }
.num-warn  { color: #f5a623; }
.num-skip  { color: #9b9fa8; }

/* 최종 판정 뱃지 */
.verdict-pass {
    display: inline-flex; align-items: center; gap: 6px;
    background: #e8f7ee; color: #2d7d52;
    border: 1px solid #b7e4c7;
    border-radius: 6px; padding: 6px 14px;
    font-size: 13px; font-weight: 600;
}
.verdict-fail {
    display: inline-flex; align-items: center; gap: 6px;
    background: #fff0f0; color: #c0392b;
    border: 1px solid #fcc;
    border-radius: 6px; padding: 6px 14px;
    font-size: 13px; font-weight: 600;
}

/* 시트 결과 행 */
.sheet-row {
    display: flex; align-items: center;
    padding: 9px 12px;
    border: 1px solid #e5e5e8;
    border-radius: 7px;
    margin-bottom: 5px;
    background: #fff;
    font-size: 13px;
}
.sheet-row:hover { background: #f7f7f9; }
.sheet-name { flex: 1; color: #1a1a1a; font-weight: 500; }
.sheet-badge {
    font-size: 11px; font-weight: 600;
    padding: 2px 8px; border-radius: 4px;
}
.badge-ok   { background: #e8f7ee; color: #2d7d52; }
.badge-ng   { background: #fff0f0; color: #c0392b; }
.badge-warn { background: #fffbeb; color: #92650a; }
.badge-skip { background: #f2f3f4; color: #6b6f7a; }

/* Streamlit 기본 요소 오버라이드 */
div[data-testid="stButton"] button {
    background: #5e6ad2;
    color: #fff;
    border: none;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    padding: 6px 16px;
    transition: background 0.15s;
}
div[data-testid="stButton"] button:hover { background: #4b58c5; }
div[data-testid="stButton"] button:focus { box-shadow: 0 0 0 3px rgba(94,106,210,0.25); }

/* secondary 버튼 (로그아웃 등) */
div[data-testid="stButton"] button[kind="secondary"] {
    background: #f7f7f9;
    color: #1a1a1a;
    border: 1px solid #e5e5e8;
}
div[data-testid="stButton"] button[kind="secondary"]:hover { background: #ededf0; }

/* 입력 필드 */
div[data-baseweb="input"] input {
    border: 1px solid #e5e5e8 !important;
    border-radius: 6px !important;
    font-size: 13px !important;
    background: #fff !important;
}
div[data-baseweb="input"] input:focus {
    border-color: #5e6ad2 !important;
    box-shadow: 0 0 0 3px rgba(94,106,210,0.15) !important;
}

/* 파일 업로더 */
div[data-testid="stFileUploader"] {
    border: 1.5px dashed #e5e5e8 !important;
    border-radius: 8px !important;
    background: #fafafa !important;
}
div[data-testid="stFileUploader"]:hover {
    border-color: #5e6ad2 !important;
    background: #f5f5ff !important;
}

/* 탭 */
div[data-baseweb="tab-list"] { border-bottom: 1px solid #e5e5e8; gap: 0; }
button[data-baseweb="tab"] {
    font-size: 12px !important;
    font-weight: 500 !important;
    color: #6b6f7a !important;
    padding: 8px 14px !important;
    border-radius: 0 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: #1a1a1a !important;
    border-bottom: 2px solid #5e6ad2 !important;
}

/* Expander */
div[data-testid="stExpander"] {
    border: 1px solid #e5e5e8 !important;
    border-radius: 7px !important;
    margin-bottom: 4px !important;
}
div[data-testid="stExpander"] summary {
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 10px 14px !important;
}

/* Progress bar */
div[data-testid="stProgress"] > div { background: #5e6ad2 !important; }

/* 숨김 */
#MainMenu, footer, header { visibility: hidden; }
div[data-testid="stToolbar"] { display: none; }
.block-container { padding-top: 24px !important; padding-bottom: 40px !important; }
</style>
""", unsafe_allow_html=True)


# ── 로그인 화면 ───────────────────────────────────────────────────
if 'company' not in st.session_state:
    st.markdown('<div class="login-wrap">', unsafe_allow_html=True)
    st.markdown('<div class="login-title">📋 준공서류 검토 포털</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-sub">업체 계정으로 로그인하세요</div>', unsafe_allow_html=True)

    username = st.text_input("아이디", placeholder="업체 아이디 입력", label_visibility="collapsed")
    password = st.text_input("비밀번호", type="password", placeholder="비밀번호 입력", label_visibility="collapsed")

    if st.button("로그인", use_container_width=True):
        if auth.verify_login(username, password):
            st.session_state.company = username
            st.rerun()
        else:
            st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()


# ── 헤더 ─────────────────────────────────────────────────────────
company = st.session_state.company
col_logo, col_right = st.columns([6, 1])
with col_logo:
    st.markdown(
        f'<div class="portal-header">'
        f'<span class="portal-logo">📋 준공서류 검토 포털</span>'
        f'<span class="portal-company-badge">{company}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
with col_right:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("로그아웃", type="secondary"):
        del st.session_state.company
        if 'review_results' in st.session_state:
            del st.session_state.review_results
        st.rerun()


# ── Gemini API (사이드바) ─────────────────────────────────────────
with st.sidebar:
    st.markdown("**⚙️ AI 검토 설정**")
    api_key_input = st.text_input(
        "Gemini API Key",
        value=config.GEMINI_API_KEY,
        type="password",
        placeholder="AIza...",
        help="없으면 서류 첨부 여부만 확인됩니다",
    )
    if api_key_input:
        config.GEMINI_API_KEY = api_key_input
        config.USE_MOCK = False
    mode = "🟡 첨부 여부만 확인" if config.USE_MOCK else "🟢 AI 내용 검토"
    st.caption(mode)


# ── 파일 업로드 ───────────────────────────────────────────────────
st.markdown('<div class="section-title">파일 업로드</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="upload-hint">준공서류 엑셀 파일을 <b>.xlsx</b> 형식으로 저장 후 업로드하세요</div>',
    unsafe_allow_html=True,
)

uploaded_file = st.file_uploader(
    "준공서류 엑셀 업로드",
    type=["xlsx"],
    label_visibility="collapsed",
)

if uploaded_file is None:
    st.stop()

file_bytes = uploaded_file.read()
with st.spinner("파일 읽는 중..."):
    try:
        wb = xlsx_parser.load_workbook(file_bytes)
    except Exception as e:
        st.error(f"파일을 열 수 없습니다: {e}")
        st.stop()

sheet_names = [s for s in xlsx_parser.get_sheet_names(wb) if s != 'Sheet1']
st.caption(f"시트 {len(sheet_names)}개 확인됨 — {', '.join(sheet_names[:5])}{'...' if len(sheet_names) > 5 else ''}")

st.markdown("---")

# ── 검토 실행 ─────────────────────────────────────────────────────
col_btn, col_space = st.columns([2, 5])
with col_btn:
    run = st.button("🔍  검토 시작", use_container_width=True)

if run:
    all_results = {}
    review_images = {}
    bar = st.progress(0, text="검토 준비 중...")
    total = len(sheet_names)

    for idx, sname in enumerate(sheet_names):
        bar.progress(idx / total, text=f"'{sname}' 검토 중...")
        ws = wb[sname]
        images = xlsx_parser.extract_images_from_sheet(ws)
        img = images[0] if images else xlsx_parser.sheet_to_image(ws)
        all_results[sname] = reviewer.review_sheet(sname, img, ws)
        review_images[sname] = img

    bar.progress(1.0, text="검토 완료!")
    st.session_state.review_results = all_results
    st.session_state.review_images = review_images

    sent = send_review_email(company, all_results)
    if sent:
        st.success("✅ 검토 완료 — 결과가 담당자 메일로 발송되었습니다.")
    else:
        st.success("✅ 검토 완료")
        if not config.GMAIL_APP_PASSWORD if hasattr(config, 'GMAIL_APP_PASSWORD') else True:
            st.caption("메일 발송 설정이 필요합니다 (GMAIL_APP_PASSWORD)")


# ── 결과 표시 ─────────────────────────────────────────────────────
if not st.session_state.get("review_results"):
    st.stop()

all_results  = st.session_state.review_results
review_images = st.session_state.get("review_images", {})

st.markdown("---")
st.markdown('<div class="section-title">검토 결과</div>', unsafe_allow_html=True)

all_items = [r for rows in all_results.values() for r in rows]
ok   = sum(1 for r in all_items if r['결과'] == 'OK')
ng   = sum(1 for r in all_items if r['결과'] == 'NG')
warn = sum(1 for r in all_items if r['결과'] == 'WARN')
skip = sum(1 for r in all_items if r['결과'] == 'SKIP')

st.markdown(f"""
<div class="result-summary">
  <div class="result-card card-ok">
    <div class="result-card-num num-ok">{ok}</div>
    <div class="result-card-label">OK</div>
  </div>
  <div class="result-card card-ng">
    <div class="result-card-num num-ng">{ng}</div>
    <div class="result-card-label">NG</div>
  </div>
  <div class="result-card card-warn">
    <div class="result-card-num num-warn">{warn}</div>
    <div class="result-card-label">WARN</div>
  </div>
  <div class="result-card card-skip">
    <div class="result-card-num num-skip">{skip}</div>
    <div class="result-card-label">해당없음</div>
  </div>
</div>
""", unsafe_allow_html=True)

verdict_cls = "verdict-pass" if ng == 0 else "verdict-fail"
verdict_txt = "이상 없음" if ng == 0 else f"NG {ng}건 확인 필요"
verdict_ico = "✓" if ng == 0 else "✕"
st.markdown(
    f'<div class="{verdict_cls}">{verdict_ico}&nbsp; 최종 판정 — {verdict_txt}</div>',
    unsafe_allow_html=True,
)

st.markdown("<br>", unsafe_allow_html=True)

# 시트별 탭
tabs = st.tabs(list(all_results.keys()))
for tab, (sname, rows) in zip(tabs, all_results.items()):
    with tab:
        col_img, col_detail = st.columns([1, 1], gap="large")
        with col_img:
            st.markdown('<div class="section-title">서류 미리보기</div>', unsafe_allow_html=True)
            if sname in review_images:
                st.image(review_images[sname], use_container_width=True)
            else:
                st.caption("미리보기 없음")

        with col_detail:
            st.markdown('<div class="section-title">항목별 결과</div>', unsafe_allow_html=True)
            for item in rows:
                s = item['결과']
                icon  = {'OK': '✅', 'NG': '❌', 'WARN': '⚠️', 'SKIP': '➖'}.get(s, '?')
                badge = {'OK': 'badge-ok', 'NG': 'badge-ng', 'WARN': 'badge-warn', 'SKIP': 'badge-skip'}.get(s, '')
                with st.expander(f"{icon}  {item['항목']}", expanded=(s == 'NG')):
                    st.markdown(
                        f'<span class="sheet-badge {badge}">{s}</span>',
                        unsafe_allow_html=True,
                    )
                    if item.get('추출값') and item['추출값'] != '-':
                        st.caption(f"추출값: {item['추출값']}")
                    if item.get('비고'):
                        st.caption(f"비고: {item['비고']}")

# ── 결과 다운로드 ─────────────────────────────────────────────────
st.markdown("---")
st.markdown('<div class="section-title">결과 다운로드</div>', unsafe_allow_html=True)
with st.spinner("엑셀 생성 중..."):
    excel_bytes = output.results_to_excel(all_results)

col_dl, _ = st.columns([2, 5])
with col_dl:
    st.download_button(
        "📊  검토결과 엑셀 다운로드",
        data=excel_bytes,
        file_name=f"준공서류검토결과_{uploaded_file.name}",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
