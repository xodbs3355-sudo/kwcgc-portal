"""
엑셀 파일 파싱 및 시트 이미지 추출
"""
import io
from pathlib import Path

import openpyxl
from PIL import Image


def load_workbook(file_bytes: bytes) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)


def get_sheet_names(wb: openpyxl.Workbook) -> list[str]:
    return wb.sheetnames


def extract_images_from_sheet(ws) -> list[Image.Image]:
    """시트에 삽입된 이미지 객체 추출"""
    images = []
    for img in ws._images:
        try:
            pil_img = Image.open(io.BytesIO(img._data()))
            images.append(pil_img)
        except Exception:
            pass
    return images


def sheet_to_image(ws, scale: float = 1.5) -> Image.Image:
    """
    시트 셀 데이터를 텍스트 그리드 이미지로 렌더링.
    삽입 이미지가 없을 때 fallback으로 사용.
    실제 운영에서는 xlwings/win32com 기반 캡처로 교체 권장.
    """
    from PIL import ImageDraw, ImageFont

    col_widths = {}
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            val = str(cell.value) if cell.value is not None else ""
            max_len = max(max_len, len(val))
        col_widths[col_letter] = max(max_len, 4)

    cell_w, cell_h = 80, 22
    cols = list(col_widths.keys())
    rows = ws.max_row or 1

    img_w = int(sum(col_widths[c] * 8 for c in cols) + 20)
    img_h = int(rows * cell_h + 20)

    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("malgun.ttf", 11)
    except Exception:
        font = ImageFont.load_default()

    x_offsets = {}
    x = 10
    for c in cols:
        x_offsets[c] = x
        x += col_widths[c] * 8

    for row_idx, row in enumerate(ws.iter_rows()):
        y = 10 + row_idx * cell_h
        for cell in row:
            col_letter = cell.column_letter
            x = x_offsets.get(col_letter, 0)
            val = str(cell.value) if cell.value is not None else ""
            draw.rectangle([x, y, x + col_widths[col_letter] * 8 - 1, y + cell_h - 1],
                           outline="#cccccc")
            draw.text((x + 2, y + 3), val[:15], fill="black", font=font)

    return img


def extract_sheet_content(ws) -> dict:
    """시트의 셀 텍스트 데이터를 딕셔너리로 추출"""
    data = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                data[cell.coordinate] = cell.value
    return data
