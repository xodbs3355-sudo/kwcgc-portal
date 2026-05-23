"""
검토 결과 첨부 파일 PDF 병합 출력.
"""
import io

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter, PdfReader


# A4 — 300 DPI 기준 픽셀
A4_W_PX = 2480   # 210mm × 300/25.4
A4_H_PX = 3508   # 297mm × 300/25.4
MM_TO_PX = 300 / 25.4  # 1mm ≈ 11.81px @ 300DPI


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

    # 안내 문구 — 페이지 좌측 상단
    try:
        title_font = ImageFont.truetype("arial.ttf", 48)
        body_font = ImageFont.truetype("arial.ttf", 32)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

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
    try:
        url_font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        url_font = ImageFont.load_default()
    url_text_y = qr_y + qr_size_px + 8
    draw.text((qr_x, url_text_y), share_url, fill="#6b6f7a", font=url_font)

    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=300.0)
    return buf.getvalue()


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
    writer = PdfWriter()

    for doc in documents:
        did = doc["id"]
        files = uploaded_files.get(did, [])
        if not files:
            continue

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
                    writer.add_page(page)
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
