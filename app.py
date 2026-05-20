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
.block-container {
    padding-top: 20px !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
    max-width: 100% !important;
}

/* 헤더 */
.portal-logo { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; }
.company-badge {
    font-size: 12px; color: #6b6f7a;
    background: #f7f7f9; border: 1px solid #e5e5e8;
    border-radius: 6px; padding: 3px 10px;
}

/* 섹션 타이틀 */
.section-title {
    font-size: 11px; font-weight: 600; color: #9b9fa8;
    text-transform: uppercase; letter-spacing: 0.8px;
    margin: 0 0 4px;
}

/* 결과 카드 */
.result-summary { display: flex; gap: 10px; margin: 16px 0 24px; }
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

/* 기본 버튼 */
div[data-testid="stButton"] button {
    background: #5e6ad2; color: #fff; border: none;
    border-radius: 6px; font-size: 13px; font-weight: 500;
    padding: 8px 20px; transition: background 0.15s;
}
div[data-testid="stButton"] button:hover { background: #4b58c5; }

/* 페이지 vertical gap */
div[data-testid="stMainBlockContainer"] > div[data-testid="stVerticalBlock"] {
    gap: 4px !important;
}

/* 빈 stMarkdown 컨테이너 hide */
div[data-testid="stMarkdownContainer"]:empty,
div[data-testid="stMarkdown"]:has(> div[data-testid="stMarkdownContainer"]:empty) {
    display: none !important;
}

/* 검토 시작 버튼 위로 */
.st-key-run_review_btn {
    margin-top: -10px !important;
}

/* ═════════ TEMP: 컬럼 경계선 디버그 (빨간 점선) ═════════ */
/* 헤더·본문·기타 모든 stHorizontalBlock 의 stColumn 마지막 제외 */
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:not(:last-child) {
    border-right: 1px dashed #e5484d !important;
}
/* 행 자체에도 위/아래 빨간 점선 (본문 행) */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"]) {
    outline: 1px dashed rgba(229,72,77,0.4) !important;
    outline-offset: -1px !important;
}

/* ═════════ 서류 행 — CSS Grid 기반 ═════════ */

/* 서류 행 (stFileUploader 포함된 stHorizontalBlock) → Grid */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"]) {
    display: grid !important;
    grid-template-columns: 0.3fr 1.6fr 0.8fr 2.2fr 2.5fr !important;
    align-items: stretch !important;
    border-bottom: 1px solid #e5e5e8 !important;
    min-height: 44px !important;
    padding: 0 !important;
    gap: 0 !important;
    width: 100% !important;
    transition: background 0.12s ease !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"]):hover {
    background: #fafafa !important;
}

/* 각 셀(stColumn) — flex 수직중앙 + 좌측정렬 (3·4번째 셀) */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  > div[data-testid="stColumn"] {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-start !important;
    padding: 4px 8px !important;
    min-height: 44px !important;
    width: 100% !important;
    flex: none !important;
    overflow: hidden !important;
    box-sizing: border-box !important;
}
/* No.·서류명 셀 (1·2번째) — 카드 좌우 정중앙 */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  > div[data-testid="stColumn"]:nth-child(1),
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  > div[data-testid="stColumn"]:nth-child(2) {
    justify-content: center !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  > div[data-testid="stColumn"]:nth-child(1)
  > div[data-testid="stVerticalBlock"],
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  > div[data-testid="stColumn"]:nth-child(2)
  > div[data-testid="stVerticalBlock"] {
    justify-content: center !important;
}

/* 셀 내부 stVerticalBlock — 너비 채우고, gap 제거 */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  > div[data-testid="stColumn"]
  > div[data-testid="stVerticalBlock"] {
    width: 100% !important;
    gap: 0 !important;
    display: flex !important;
    align-items: center !important;
    flex-direction: row !important;
}

/* 셀 내부 wrapper div reset */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  div[data-testid="stVerticalBlock"] > div {
    width: 100% !important;
    padding: 0 !important;
    margin: 0 !important;
    min-height: 0 !important;
    box-sizing: border-box !important;
}

/* ═════════ 파일 업로더 (Upload 버튼) ═════════ */

/* file_uploader 외곽 reset */
div[data-testid="stFileUploader"],
div[data-testid="stFileUploader"] > div {
    border: none !important;
    background: transparent !important;
    padding: 0 !important;
    margin: 0 !important;
    min-height: 0 !important;
    width: auto !important;
}

/* ═════════════════════════════════════════════════════════
   ★ stFileUploader — 클릭만 차단 (텍스트는 button 안만 표시)
   ═════════════════════════════════════════════════════════ */
div[data-testid="stFileUploader"] {
    pointer-events: none !important;
}
div[data-testid="stFileUploader"] section {
    border: none !important;
    background: transparent !important;
    padding: 0 !important;
    margin: 0 !important;
    min-height: 0 !important;
    height: auto !important;
    display: flex !important;
    align-items: center !important;
    gap: 8px !important;
    overflow: visible !important;
}

/* dropzone — 보라색 버튼 카드 (강제 visibility + 명시적 크기) */
div[data-testid="stFileUploader"] div[data-testid="stFileUploaderDropzone"],
div[data-testid="stFileUploaderDropzone"] {
    background-color: #5e6ad2 !important;
    background: #5e6ad2 !important;
    border: 1px solid #4b58c5 !important;
    border-radius: 6px !important;
    padding: 0 14px !important;
    height: 28px !important;
    min-height: 28px !important;
    width: 92px !important;
    min-width: 92px !important;
    max-width: 92px !important;
    pointer-events: none !important;
    overflow: visible !important;
    display: inline-flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    align-items: center !important;
    justify-content: center !important;
    cursor: pointer !important;
    box-sizing: border-box !important;
    box-shadow: 0 1px 2px rgba(94,106,210,0.2) !important;
    transition: background 0.15s, border-color 0.15s, box-shadow 0.15s !important;
}
div[data-testid="stFileUploaderDropzone"]:hover {
    background: #4b58c5 !important;
    border-color: #3f4ab0 !important;
    box-shadow: 0 2px 4px rgba(94,106,210,0.3) !important;
}

/* ★ button 과 자식 — 클릭 가능 + 흰색 텍스트 (보라 배경 위) — 강제 visibility */
div[data-testid="stFileUploader"] button,
div[data-testid="stFileUploader"] button * {
    pointer-events: auto !important;
    font-size: 13px !important;
    color: #fff !important;
    visibility: visible !important;
    opacity: 1 !important;
}
div[data-testid="stFileUploader"] button {
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
    height: 24px !important;
    line-height: 1 !important;
    font-weight: 500 !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 6px !important;
    overflow: visible !important;
    width: 100% !important;
}
div[data-testid="stFileUploader"] button span,
div[data-testid="stFileUploader"] button div,
div[data-testid="stFileUploader"] button p {
    display: inline-flex !important;
    align-items: center !important;
    color: #fff !important;
    font-size: 13px !important;
}

/* ★ 200MB instructions — 정밀 hide (button 자식 빼고 모두) */
[data-testid="stFileUploaderDropzoneInstructions"],
div[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"],
div[data-testid="stFileUploader"] [data-testid*="DropzoneInstructions"],
/* section 직계 small/p (button 옆 형제로 있는 경우) */
div[data-testid="stFileUploader"] > div > section > small,
div[data-testid="stFileUploader"] > div > section > p,
div[data-testid="stFileUploader"] section > small,
div[data-testid="stFileUploader"] section > p,
/* dropzone 직계 small/p (button 옆 형제로 있는 경우) */
div[data-testid="stFileUploaderDropzone"] > small,
div[data-testid="stFileUploaderDropzone"] > p {
    display: none !important;
}

/* 아이콘 — 14px, 흰색 (보라 배경 위), 버튼 테두리 무시 */
div[data-testid="stFileUploader"] button svg {
    width: 14px !important;
    height: 14px !important;
    flex-shrink: 0 !important;
    overflow: visible !important;
    color: #fff !important;
    fill: #fff !important;
    font-size: 14px !important;
}

/* ★ file_uploader 자체의 파일 칩 hide — 첨부파일 열에 직접 표시할 것 */
div[data-testid="stFileUploader"] [data-testid*="FileData"],
div[data-testid="stFileUploader"] [data-testid*="FileUploaderFile"]:not([data-testid="stFileUploaderFileName"] *) {
    display: none !important;
}

/* ═════════ 특기사항 입력란 ═════════ */

div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  div[data-testid="stTextInput"] {
    width: 100% !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  div[data-baseweb="input"] {
    height: 30px !important;
    min-height: 0 !important;
    width: 100% !important;
    box-sizing: border-box !important;
    border: 1px solid #e5e5e8 !important;
    border-radius: 6px !important;
    background: #fff !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  div[data-baseweb="input"] input {
    height: 28px !important;
    padding: 0 10px !important;
    font-size: 13px !important;
    border: none !important;
    background: transparent !important;
    box-sizing: border-box !important;
    width: 100% !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stFileUploader"])
  div[data-baseweb="input"]:focus-within {
    border-color: #5e6ad2 !important;
    box-shadow: 0 0 0 2px rgba(94,106,210,0.15) !important;
}

/* 입력 필드 (로그인 페이지 등 다른 위치) */
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
st.markdown("<div style='border-top:1px solid #e5e5e8;margin-bottom:8px;'></div>", unsafe_allow_html=True)

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
    '<p style="font-size:13px;color:#6b6f7a;margin:0 0 4px;">'
    '파일당 최대 200MB &nbsp;|&nbsp; PDF · JPG · PNG'
    '</p>',
    unsafe_allow_html=True,
)

uploaded = {}   # doc_id → list of (filename, bytes)
notes    = {}   # doc_id → str


def render_doc_row(doc):
    did  = doc["id"]
    cond = doc["condition"]

    c_num, c_name, c_upload, c_files, c_note = st.columns([0.3, 1.6, 0.8, 2.2, 2.5])

    with c_num:
        st.markdown(
            f'<div style="display:inline-flex;align-items:center;justify-content:center;'
            f'background:#eef0fb;border:1px solid #d8dcf3;border-radius:6px;'
            f'padding:4px 10px;min-width:28px;height:24px;'
            f'font-size:12px;font-weight:600;color:#5e6ad2;line-height:1;'
            f'margin-bottom:15px;">'
            f'{doc["num"]}</div>',
            unsafe_allow_html=True,
        )
    with c_name:
        st.markdown(
            f'<div style="display:inline-flex;align-items:center;'
            f'background:#f7f7f9;border:1px solid #e5e5e8;border-radius:6px;'
            f'padding:4px 12px;height:24px;'
            f'font-size:13px;font-weight:500;color:#1a1a1a;line-height:1;'
            f'margin-bottom:15px;">'
            f'{doc["name"]}</div>',
            unsafe_allow_html=True,
        )
    with c_upload:
        files = st.file_uploader(
            f"{doc['name']}",
            type=["pdf", "jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key=f"files_{did}",
            label_visibility="collapsed",
        )
        uploaded[did] = [(f.name, f.read()) for f in files] if files else []
    with c_files:
        if files:
            names_html = " · ".join(
                f'<span style="color:#5e6ad2;">{f.name}</span>' for f in files
            )
            st.markdown(
                f'<div style="font-size:12px;line-height:1.4;'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
                f'padding:4px 0;">{names_html}</div>',
                unsafe_allow_html=True,
            )
    with c_note:
        note = st.text_input(
            "특기사항",
            key=f"note_{did}",
            placeholder="",
            label_visibility="collapsed",
        )
        notes[did] = note


# 검토 시작 버튼 — 특기사항 열 우측 절반에 배치
_, _, _, _, _, btn_area = st.columns([0.3, 1.6, 0.8, 2.2, 1.25, 1.25])
with btn_area:
    run = st.button("검토 시작", use_container_width=True, key="run_review_btn")

# 테이블 헤더
h_num, h_name, h_upload, h_files, h_note = st.columns([0.3, 1.6, 0.8, 2.2, 2.5])
with h_num:
    st.markdown('<div style="font-size:13px;font-weight:600;color:#9b9fa8;padding:0 8px 4px;text-align:center;">No.</div>', unsafe_allow_html=True)
with h_name:
    st.markdown('<div style="font-size:13px;font-weight:600;color:#9b9fa8;padding:0 8px 4px;">서류명</div>', unsafe_allow_html=True)
with h_upload:
    st.markdown('<div style="font-size:13px;font-weight:600;color:#9b9fa8;padding:0 8px 4px;text-align:center;">파일첨부</div>', unsafe_allow_html=True)
with h_files:
    st.markdown('<div style="padding:0 8px 4px;">&nbsp;</div>', unsafe_allow_html=True)
with h_note:
    st.markdown('<div style="font-size:13px;font-weight:600;color:#9b9fa8;padding:0 8px 4px;">특기사항</div>', unsafe_allow_html=True)
st.markdown("<hr style='margin:0 0 2px;border:none;border-top:1.5px solid #1a1a1a;'>", unsafe_allow_html=True)

for idx, doc in enumerate(DOCUMENTS):
    if idx == 0:
        st.markdown("<div style='height:5px;'></div>", unsafe_allow_html=True)
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
