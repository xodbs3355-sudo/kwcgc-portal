"""
검토 결과 첨부 파일 PDF 병합 출력.
"""
import io

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter, PdfReader
from pypdf.annotations import Link
from pypdf.generic import Fit


# A4 — 300 DPI 기준 픽셀
A4_W_PX = 2480   # 210mm × 300/25.4
A4_H_PX = 3508   # 297mm × 300/25.4
MM_TO_PX = 300 / 25.4  # 1mm ≈ 11.81px @ 300DPI
PX_TO_PT = 72.0 / 300.0  # PIL 픽셀(300DPI) → PDF user space(pt)
A4_H_PT = A4_H_PX * PX_TO_PT  # ≈ 841.92pt


def _load_korean_font(size: int) -> ImageFont.ImageFont:
    """한글 지원 폰트를 환경별 후보 순으로 로드. 실패 시 기본 폰트."""
    candidates = [
        "malgun.ttf",                                              # Windows 맑은 고딕
        "MalgunGothic.ttf",
        "NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",         # Linux Nanum
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",            # Linux 대체 CJK
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",              # macOS
        "arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _image_to_pdf_bytes(img_bytes: bytes) -> bytes:
    """이미지 bytes → PDF bytes 변환."""
    img = Image.open(io.BytesIO(img_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def _make_qr_page_pdf(share_url: str, project_name: str = "") -> bytes:
    """QR + 안내 문구가 있는 A4 1페이지 PDF bytes 생성.

    QR: 우측 하단 30mm × 30mm (모서리 15mm 안쪽)
    """
    import qrcode

    page = Image.new("RGB", (A4_W_PX, A4_H_PX), "white")
    draw = ImageDraw.Draw(page)

    title_font = _load_korean_font(48)
    body_font = _load_korean_font(32)

    margin = int(20 * MM_TO_PX)
    draw.text(
        (margin, margin),
        "검토 결과 공유",
        fill="#1a1a1a", font=title_font,
    )
    body_y = margin + 80
    body_lines = [
        f"공사명: {project_name}" if project_name else "",
        "",
        "우측 하단의 QR 코드를 스캔하면",
        "이 준공서류의 검토 결과를 확인할 수 있습니다.",
        "",
        "유효기간: 발행일로부터 90일",
    ]
    for line in body_lines:
        if line:
            draw.text((margin, body_y), line, fill="#4a4d54", font=body_font)
        body_y += 50

    # QR 생성
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(share_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # 30mm × 30mm 로 리사이즈, 우측 하단 (모서리 15mm 안쪽)
    qr_size_px = int(30 * MM_TO_PX)
    qr_img = qr_img.resize((qr_size_px, qr_size_px), Image.LANCZOS)
    inset = int(15 * MM_TO_PX)
    qr_x = A4_W_PX - qr_size_px - inset
    qr_y = A4_H_PX - qr_size_px - inset
    page.paste(qr_img, (qr_x, qr_y))

    # QR 아래 URL 작게 표시 (선택사항 — 스캔 안 될 때 수동 입력)
    url_font = _load_korean_font(18)
    url_text_y = qr_y + qr_size_px + 8
    draw.text((qr_x, url_text_y), share_url, fill="#6b6f7a", font=url_font)

    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=300.0)
    return buf.getvalue()


def _make_toc_page_pdf(entries: list[tuple[str, int]]
                       ) -> tuple[bytes, list[tuple[tuple[float, float, float, float], int]]]:
    """목차 페이지 PDF + 링크 영역 좌표 생성.

    entries: [(서류명, 최종 PDF 내 시작 페이지 0-idx), ...]
    반환: (pdf_bytes, link_specs)
      link_specs: [((x1, y1, x2, y2), target_page_0idx), ...]  좌표는 PDF user space(pt, 좌하단 원점)
    """
    page = Image.new("RGB", (A4_W_PX, A4_H_PX), "white")
    draw = ImageDraw.Draw(page)

    title_font = _load_korean_font(72)
    entry_font = _load_korean_font(44)

    # 제목 (가운데 정렬)
    title = "목     차"
    tb = draw.textbbox((0, 0), title, font=title_font)
    title_w = tb[2] - tb[0]
    title_y = int(35 * MM_TO_PX)
    draw.text(((A4_W_PX - title_w) // 2, title_y), title, fill="#1a1a1a", font=title_font)

    # 제목 아래 구분선
    rule_y = title_y + (tb[3] - tb[1]) + int(8 * MM_TO_PX)
    margin_x = int(25 * MM_TO_PX)
    draw.line([(margin_x, rule_y), (A4_W_PX - margin_x, rule_y)], fill="#888", width=3)

    # 본문 (각 항목 약 15mm)
    body_top = rule_y + int(10 * MM_TO_PX)
    row_height = int(15 * MM_TO_PX)

    link_specs: list[tuple[tuple[float, float, float, float], int]] = []

    for idx, (name, target_idx) in enumerate(entries):
        row_top = body_top + idx * row_height
        text_y = row_top + int(3 * MM_TO_PX)

        label = f"{idx + 1}. {name}"
        draw.text((margin_x, text_y), label, fill="#2a2a2a", font=entry_font)

        page_label = str(target_idx + 1)  # 사용자에게는 1-based로 표시
        pb = draw.textbbox((0, 0), page_label, font=entry_font)
        page_w = pb[2] - pb[0]
        page_x = A4_W_PX - margin_x - page_w
        draw.text((page_x, text_y), page_label, fill="#2a2a2a", font=entry_font)

        # 점선 leader
        lb = draw.textbbox((0, 0), label, font=entry_font)
        label_w = lb[2] - lb[0]
        dot_start = margin_x + label_w + int(4 * MM_TO_PX)
        dot_end = page_x - int(4 * MM_TO_PX)
        dot_y = text_y + (pb[3] - pb[1]) - 6
        if dot_end > dot_start:
            for x in range(dot_start, dot_end, 16):
                draw.ellipse([x, dot_y, x + 4, dot_y + 4], fill="#a8a8a8")

        # 링크 영역 (행 전체를 탭 가능 영역으로) — PIL 좌표(px, 좌상단) → PDF(pt, 좌하단)
        rect_left = (margin_x - 10) * PX_TO_PT
        rect_right = (A4_W_PX - margin_x + 10) * PX_TO_PT
        rect_top_pt = A4_H_PT - row_top * PX_TO_PT
        rect_bot_pt = A4_H_PT - (row_top + row_height) * PX_TO_PT
        link_specs.append(((rect_left, rect_bot_pt, rect_right, rect_top_pt), target_idx))

    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=300.0)
    return buf.getvalue(), link_specs


def merge_attachments_to_pdf(uploaded_files: dict, documents: list,
                              share_url: str | None = None,
                              project_name: str = "") -> bytes:
    """첨부 파일들(PDF + 이미지)을 하나의 PDF로 병합.

    uploaded_files: {doc_id: [(filename, bytes), ...]}
    documents:      DOCUMENTS 리스트 (서류 순서 보장용)
    share_url:      검토 결과 공유 URL (있으면 마지막에 QR 페이지 추가)
    project_name:   QR 페이지에 표시할 공사명
    반환: 통합 PDF bytes
    """
    # 1단계: 첨부 파일을 임시 writer에 모으면서 서류별 시작 페이지(첨부 영역 내 0-idx) 기록
    attachments_writer = PdfWriter()
    doc_entries: list[tuple[str, int]] = []  # (서류명, 첨부 영역 내 시작 페이지 0-idx)

    for doc in documents:
        did = doc["id"]
        files = uploaded_files.get(did, [])
        if not files:
            continue

        first_page_offset = len(attachments_writer.pages)
        added_any = False

        for filename, file_bytes in files:
            ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
            try:
                if ext == "pdf":
                    pdf_bytes = file_bytes
                elif ext in ("jpg", "jpeg", "png"):
                    pdf_bytes = _image_to_pdf_bytes(file_bytes)
                else:
                    continue

                reader = PdfReader(io.BytesIO(pdf_bytes))
                for page in reader.pages:
                    attachments_writer.add_page(page)
                added_any = True
            except Exception:
                continue

        if added_any:
            doc_entries.append((doc["name"], first_page_offset))

    # 2단계: 최종 writer 조립 (목차 → 첨부 → QR)
    writer = PdfWriter()
    toc_page_count = 0
    link_specs: list[tuple[tuple[float, float, float, float], int]] = []

    # 목차 페이지 (첨부가 1건이라도 있을 때만)
    if doc_entries:
        try:
            # 최종 PDF 기준 절대 페이지(0-idx)로 변환 — 목차 페이지 수만큼 뒤로 밀림
            # 목차는 1페이지 가정 (DOCUMENTS는 최대 10개 → 1페이지 내 수용 가능)
            toc_entries_render = [(name, 1 + off) for name, off in doc_entries]
            toc_pdf, link_specs = _make_toc_page_pdf(toc_entries_render)
            toc_reader = PdfReader(io.BytesIO(toc_pdf))
            for page in toc_reader.pages:
                writer.add_page(page)
            toc_page_count = len(toc_reader.pages)
        except Exception:
            toc_page_count = 0
            link_specs = []

    # 첨부 페이지
    for page in attachments_writer.pages:
        writer.add_page(page)

    # 사이드바 북마크(아웃라인) — 뷰어에 따라 표시
    for name, attach_offset in doc_entries:
        try:
            writer.add_outline_item(name, toc_page_count + attach_offset)
        except Exception:
            pass

    # 목차 페이지에 클릭 가능한 링크 주석 추가
    if toc_page_count and link_specs:
        for rect, target_idx in link_specs:
            try:
                writer.add_annotation(
                    page_number=0,
                    annotation=Link(rect=rect, target_page_index=target_idx, fit=Fit.fit()),
                )
            except Exception:
                continue

    # 마지막 페이지에 QR 추가
    if share_url:
        try:
            qr_pdf = _make_qr_page_pdf(share_url, project_name)
            qr_reader = PdfReader(io.BytesIO(qr_pdf))
            for page in qr_reader.pages:
                writer.add_page(page)
        except Exception:
            pass

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
