"""
도시가스 공사 준공서류 검토 포털
"""
import streamlit as st
import config
import auth
import reviewer
import output
from email_sender import send_review_email
from documents import DOCUMENTS

st.set_page_config(
    page_title="준공서류 검토 포털",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────
st.html("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; color: #1a1a1a; }
.stApp { background: #fff; }
#MainMenu, footer, header { visibility: hidden; }
div[data-testid="stToolbar"] { display: none; }
.block-container { padding-top: 20px !important; padding-left: 2rem !important; padding-right: 2rem !important; max-width: 100% !important; }

/* 헤더 */
.portal-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 0; border-bottom: 1px solid #e5e5e8; margin-bottom: 28px;
}
.portal-logo { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; }
.company-badge {
    font-size: 12px; color: #6b6f7a;
    background: #f7f7f9; border: 1px solid #e5e5e8;
    border-radius: 6px; padding: 3px 10px;
}

/* 서류 카드 */
.doc-card {
    border: 1px solid #e5e5e8; border-radius: 7px;
    padding: 10px 14px; margin-bottom: 0; background: #fff;
}
.doc-card:hover { border-color: #c8cbf0; background: #fafafa; }
.doc-header { display: flex; align-items: center; gap: 8px; }
.doc-num {
    font-size: 11px; font-weight: 600; color: #5e6ad2;
    background: #eef0fb; border-radius: 4px; padding: 1px 6px;
    min-width: 22px; text-align: center;
}
.doc-name { font-size: 13px; font-weight: 600; color: #1a1a1a; flex: 1; }
.doc-required {
    font-size: 10px; font-weight: 600; color: #e5484d;
    background: #fff0f0; border-radius: 4px; padding: 1px 6px;
}
.doc-optional {
    font-size: 10px; color: #6b6f7a;
    background: #f2f3f4; border-radius: 4px; padding: 1px 6px;
}
.doc-condition { font-size: 11px; color: #9b9fa8; margin-top: 1px; padding-left: 30px; }

/* 결과 카드 */
.result-summary {
    display: flex; gap: 10px; margin: 16px 0 24px;
}
.result-card {
    flex: 1; border: 1px solid #e5e5e8; border-radius: 8px;
    padding: 14px; text-align: center; background: #f9f9f9;
}
.result-num { font-size: 28px; font-weight: 600; line-height: 1; }
.result-label { font-size: 11px; color: #6b6f7a; margin-top: 4px; font-weight: 500; }
.card-ok   { border-top: 3px solid #4cb87a; } .num-ok   { color: #4cb87a; }
.card-ng   { border-top: 3px solid #e5484d; } .num-ng   { color: #e5484d; }
.card-warn { border-top: 3px solid #f5a623; } .num-warn { color: #f5a623; }
.card-skip { border-top: 3px solid #9b9fa8; } .num-skip { color: #9b9fa8; }

.verdict-pass {
    display: inline-flex; align-items: center; gap: 6px;
    background: #e8f7ee; color: #2d7d52; border: 1px solid #b7e4c7;
    border-radius: 6px; padding: 6px 14px; font-size: 13px; font-weight: 600;
}
.verdict-fail {
    display: inline-flex; align-items: center; gap: 6px;
    background: #fff0f0; color: #c0392b; border: 1px solid #fcc;
    border-radius: 6px; padding: 6px 14px; font-size: 13px; font-weight: 600;
}

/* 버튼 */
div[data-testid="stButton"] button {
    background: #5e6ad2; color: #fff; border: none;
    border-radius: 6px; font-size: 13px; font-weight: 500;
    padding: 8px 20px; transition: background 0.15s;
}
div[data-testid="stButton"] button:hover { background: #4b58c5; }

/* 파일 업로더 — 카드/배경 완전 제거 */
div[data-testid="stFileUploader"] > div,
div[data-testid="stFileUploader"] section {
    border: none !important;
    background: transparent !important;
    padding: 0 !important;
    min-height: 0 !important;
}
div[data-testid="stFileUploaderDropzone"] {
    background: transparent !important;
    padding: 0 !important;
    min-height: 0 !important;
}
div[data-testid="stFileUploaderDropzoneInstructions"] { display: none !important; }
/* 드롭존 내 200MB 텍스트 등 불필요 텍스트 완전 제거 */
div[data-testid="stFileUploaderDropzone"] small,
div[data-testid="stFileUploaderDropzone"] p,
div[data-testid="stFileUploaderDropzone"] span.st-emotion-cache-9ycgxx,
div[data-testid="stFileUploaderDropzone"] > div { display: none !important; }
/* 업로드 버튼 — button/span/label 전방위 타겟 */
div[data-testid="stFileUploader"] section button,
div[data-testid="stFileUploader"] section span,
div[data-testid="stFileUploader"] section label {
    height: 40px !important;
    font-size: 7px !important;
    padding: 0 8px !important;
    line-height: 40px !important;
}
div[data-testid="stFileUploader"] section button svg,
div[data-testid="stFileUploader"] section span svg,
div[data-testid="stFileUploader"] section label svg {
    width: 16px !important;
    height: 8px !important;
}
/* 첨부 후 파일 정보 — 행 높이 45px 고정 */
div[data-testid="stFileUploader"] section {
    height: 45px !important;
    overflow: hidden !important;
    display: flex !important;
    align-items: center !important;
    flex-wrap: nowrap !important;
}
div[data-testid="stFileUploader"] section > div {
    height: 45px !important;
    overflow: visible !important;
    display: flex !important;
    align-items: center !important;
    font-size: 11px !important;
    white-space: nowrap !important;
}
/* 서류 행 입력 필드 높이 축소 */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  div[data-baseweb="input"] {
    height: 45px !important;
    min-height: 0 !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  div[data-baseweb="input"] input {
    height: 45px !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}

/* 페이지 최상위 stVerticalBlock gap 축소 */
div[data-testid="stMainBlockContainer"] > div[data-testid="stVerticalBlock"] {
    gap: 8px !important;
}
/* 서류 행 구분선 (hr 대체) + 수직 정렬 */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"]) {
    align-items: center !important;
    border-bottom: 1px solid #e5e5e8 !important;
    padding: 2px 0 !important;
}
/* 서류 행 내부 컬럼 gap/padding 제거 + 수직 중앙 정렬 */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  > div[data-testid="stColumn"]
  > div[data-testid="stVerticalBlock"] {
    gap: 0 !important;
    justify-content: center !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  div[data-testid="stVerticalBlock"] > div {
    padding: 0 !important;
    margin: 0 !important;
    min-height: 0 !important;
}
/* 파일 업로더 클릭 영역을 버튼 크기로만 제한 (dropzone만, section은 제외해야 파일명 안잘림) */
div[data-testid="stFileUploaderDropzone"] {
    width: fit-content !important;
}
/* stFileUploader 자체 여백 제거 */
div[data-testid="stFileUploader"],
div[data-testid="stFileUploader"] * {
    min-height: 0 !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
}

/* 체크박스 */
div[data-testid="stCheckbox"] label { font-size: 13px; color: #6b6f7a; }

/* 구분선 */
.section-title {
    font-size: 11px; font-weight: 600; color: #9b9fa8;
    text-transform: uppercase; letter-spacing: 0.8px;
    margin: 24px 0 12px;
}

/* 입력 필드 */
div[data-baseweb="input"] input {
    border: 1px solid #e5e5e8 !important; border-radius: 6px !important;
    font-size: 13px !important;
}
div[data-baseweb="input"] input:focus {
    border-color: #5e6ad2 !important;
    box-shadow: 0 0 0 3px rgba(94,106,210,0.15) !important;
}
</style>""")


# ── 로그인 ────────────────────────────────────────────────────────
if 'company' not in st.session_state:
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown("### 준공서류 검토 포털")
        st.markdown('<p style="color:#6b6f7a;font-size:13px;margin-bottom:24px;">업체 계정으로 로그인하세요</p>', unsafe_allow_html=True)
        username = st.text_input("아이디", placeholder="업체 아이디", label_visibility="collapsed")
        password = st.text_input("비밀번호", type="password", placeholder="비밀번호", label_visibility="collapsed")
        if st.button("로그인", use_container_width=True):
            if auth.verify_login(username, password):
                st.session_state.company = username
                st.rerun()
            else:
                st.error("아이디 또는 비밀번호가 올바르지 않습니다.")
    st.stop()


# ── 헤더 ─────────────────────────────────────────────────────────
company = st.session_state.company
col_h, col_right = st.columns([1, 1])
with col_h:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;padding:12px 0;">'
        f'<span class="portal-logo">준공서류 검토 포털</span>'
        f'<span class="company-badge">{company}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
with col_right:
    _, btn_sub = st.columns([3, 1])
    with btn_sub:
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
        if st.button("로그아웃", type="secondary", use_container_width=True):
            for k in ['company', 'review_results']:
                st.session_state.pop(k, None)
            st.rerun()
st.markdown("<div style='border-top:1px solid #e5e5e8;margin-bottom:28px;'></div>", unsafe_allow_html=True)

# ── Gemini 설정 (사이드바) ────────────────────────────────────────
with st.sidebar:
    st.markdown("**AI 검토 설정**")
    key_input = st.text_input("Gemini API Key", value=config.GEMINI_API_KEY,
                               type="password", placeholder="AIza...",
                               help="없으면 첨부 여부만 확인됩니다")
    if key_input:
        config.GEMINI_API_KEY = key_input
        config.USE_MOCK = False
    st.caption("AI 검토 활성" if not config.USE_MOCK else "첨부 여부만 확인")


# ── 서류 업로드 폼 ────────────────────────────────────────────────
st.markdown('<div class="section-title">준공서류 업로드</div>', unsafe_allow_html=True)
st.markdown(
    '<p style="font-size:13px;color:#6b6f7a;margin-bottom:16px;">'
    '파일당 최대 200MB &nbsp;|&nbsp; PDF · JPG · PNG'
    '</p>',
    unsafe_allow_html=True,
)

uploaded = {}   # doc_id → list of (filename, bytes)
notes    = {}   # doc_id → str


def render_doc_row(doc):
    did  = doc["id"]
    cond = doc["condition"]

    c_num, c_name, c_upload, c_note = st.columns([0.3, 1.6, 3, 2.5])

    with c_num:
        st.markdown(
            f'<div style="font-size:12px;font-weight:600;color:#5e6ad2;line-height:45px;">{doc["num"]}</div>',
            unsafe_allow_html=True,
        )
    with c_name:
        st.markdown(f'<div style="font-size:13px;font-weight:500;color:#1a1a1a;line-height:45px;">{doc["name"]}</div>', unsafe_allow_html=True)
    with c_upload:
        files = st.file_uploader(
            f"{doc['name']}",
            type=["pdf", "jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key=f"files_{did}",
            label_visibility="collapsed",
        )
        uploaded[did] = [(f.name, f.read()) for f in files] if files else []
    with c_note:
        note = st.text_input(
            "특기사항",
            key=f"note_{did}",
            placeholder="특기사항 (선택)",
            label_visibility="collapsed",
        )
        notes[did] = note


# 검토 시작 버튼 — 특기사항 열 위에 별도 행으로 배치
_, _, _, btn_area = st.columns([0.3, 1.6, 3, 2.5])
with btn_area:
    run = st.button("검토 시작", use_container_width=True)

# 테이블 헤더
h_num, h_name, h_upload, h_note = st.columns([0.3, 1.6, 3, 2.5])
with h_num:
    st.markdown('<div style="font-size:11px;font-weight:600;color:#9b9fa8;padding-bottom:4px;">No.</div>', unsafe_allow_html=True)
with h_name:
    st.markdown('<div style="font-size:11px;font-weight:600;color:#9b9fa8;padding-bottom:4px;">서류명</div>', unsafe_allow_html=True)
with h_upload:
    st.markdown('<div style="font-size:11px;font-weight:600;color:#9b9fa8;padding-bottom:4px;">파일첨부</div>', unsafe_allow_html=True)
with h_note:
    st.markdown('<div style="font-size:11px;font-weight:600;color:#9b9fa8;padding-bottom:4px;">특기사항</div>', unsafe_allow_html=True)
st.markdown("<hr style='margin:0 0 2px;border:none;border-top:1.5px solid #1a1a1a;'>", unsafe_allow_html=True)

for doc in DOCUMENTS:
    render_doc_row(doc)

if run:
    all_results = {}
    bar = st.progress(0, text="검토 준비 중...")
    total = len(DOCUMENTS)

    for idx, doc in enumerate(DOCUMENTS):
        did  = doc["id"]
        name = doc["name"]
        bar.progress(idx / total, text=f"'{name}' 검토 중...")

        files = uploaded.get(did, [])
        all_results[name] = reviewer.review_document(did, name, files)

    bar.progress(1.0, text="검토 완료!")
    st.session_state.review_results = all_results

    sent = send_review_email(company, all_results)
    if sent:
        st.success("검토 완료 — 결과가 담당자 메일로 발송되었습니다.")
    else:
        st.success("검토 완료")


# ── 결과 표시 ─────────────────────────────────────────────────────
if not st.session_state.get("review_results"):
    st.stop()

all_results = st.session_state.review_results
all_items   = [r for rows in all_results.values() for r in rows]
ok   = sum(1 for r in all_items if r['결과'] == 'OK')
ng   = sum(1 for r in all_items if r['결과'] == 'NG')
warn = sum(1 for r in all_items if r['결과'] == 'WARN')
skip = sum(1 for r in all_items if r['결과'] == 'SKIP')

st.markdown("---")
st.markdown('<div class="section-title">검토 결과</div>', unsafe_allow_html=True)

st.markdown(f"""
<div class="result-summary">
  <div class="result-card card-ok">
    <div class="result-num num-ok">{ok}</div>
    <div class="result-label">OK</div>
  </div>
  <div class="result-card card-ng">
    <div class="result-num num-ng">{ng}</div>
    <div class="result-label">NG</div>
  </div>
  <div class="result-card card-warn">
    <div class="result-num num-warn">{warn}</div>
    <div class="result-label">WARN</div>
  </div>
  <div class="result-card card-skip">
    <div class="result-num num-skip">{skip}</div>
    <div class="result-label">해당없음</div>
  </div>
</div>
""", unsafe_allow_html=True)

vc  = "verdict-pass" if ng == 0 else "verdict-fail"
vt  = "이상 없음" if ng == 0 else f"NG {ng}건 확인 필요"
st.markdown(f'<div class="{vc}">최종 판정 — {vt}</div>', unsafe_allow_html=True)
st.markdown("<br>", unsafe_allow_html=True)

# 서류별 결과 탭
tabs = st.tabs(list(all_results.keys()))
for tab, (doc_name, rows) in zip(tabs, all_results.items()):
    with tab:
        for item in rows:
            s    = item['결과']
            with st.expander(f"{item['항목']}", expanded=(s == 'NG')):
                st.markdown(f'<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px;background:{"#e8f7ee" if s=="OK" else "#fff0f0" if s=="NG" else "#fffbeb" if s=="WARN" else "#f2f3f4"};color:{"#2d7d52" if s=="OK" else "#c0392b" if s=="NG" else "#92650a" if s=="WARN" else "#6b6f7a"}">{s}</span>', unsafe_allow_html=True)
                if item.get('추출값') and item['추출값'] not in ('-', ''):
                    st.caption(f"추출값: {item['추출값']}")
                if item.get('비고'):
                    st.caption(f"비고: {item['비고']}")

# 결과 다운로드
st.markdown("---")
col_dl, _ = st.columns([2, 5])
with col_dl:
    with st.spinner("엑셀 생성 중..."):
        excel_bytes = output.results_to_excel(all_results)
    st.download_button(
        "검토결과 엑셀 다운로드",
        data=excel_bytes,
        file_name="준공서류검토결과.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
