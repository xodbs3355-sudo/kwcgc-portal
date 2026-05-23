"""
검토 결과 첨부 파일 PDF 병합 출력.
"""
import io

from PIL import Image
from pypdf import PdfWriter, PdfReader


def _image_to_pdf_bytes(img_bytes: bytes) -> bytes:
    """이미지 bytes → PDF bytes 변환."""
    img = Image.open(io.BytesIO(img_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def merge_attachments_to_pdf(uploaded_files: dict, documents: list) -> bytes:
    """첨부 파일들(PDF + 이미지)을 하나의 PDF로 병합.

    uploaded_files: {doc_id: [(filename, bytes), ...]}
    documents:      DOCUMENTS 리스트 (서류 순서 보장용)
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

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
