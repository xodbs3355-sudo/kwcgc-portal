import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
from datetime import datetime

GMAIL_USER = os.environ.get('GMAIL_USER', 'xodbs3355@gmail.com')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')
RECIPIENT = os.environ.get('RECIPIENT_EMAIL', 'xodbs3355@gmail.com')
SENDER_NAME = '준공서류검토봇'


def send_review_email(company: str, all_results: dict) -> bool:
    if not GMAIL_APP_PASSWORD:
        return False

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'[준공서류 검토결과] {company} - {now}'
    msg['From'] = f'{SENDER_NAME} <{GMAIL_USER}>'
    msg['To'] = RECIPIENT
    msg.attach(MIMEText(_build_html(company, all_results, now), 'html', 'utf-8'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, RECIPIENT, msg.as_string())
        return True
    except Exception as e:
        print(f'이메일 발송 오류: {e}')
        return False


def _build_html(company: str, all_results: dict, timestamp: str) -> str:
    all_items = [r for rows in all_results.values() for r in rows]
    ok   = sum(1 for r in all_items if r['결과'] == 'OK')
    ng   = sum(1 for r in all_items if r['결과'] == 'NG')
    warn = sum(1 for r in all_items if r['결과'] == 'WARN')
    skip = sum(1 for r in all_items if r['결과'] == 'SKIP')
    overall = '✅ 이상 없음' if ng == 0 else f'❌ NG {ng}건 확인 필요'

    rows_html = ''
    for sheet_name, items in all_results.items():
        rows_html += f'<tr style="background:#343a40;color:white;"><td colspan="3" style="padding:8px 12px;font-weight:bold;">📄 {sheet_name}</td></tr>'
        for item in items:
            s = item['결과']
            bg = {'OK': '#d5f5e3', 'NG': '#fadbd8', 'WARN': '#fef9e7', 'SKIP': '#f2f3f4'}.get(s, '#fff')
            ic = {'OK': '✅', 'NG': '❌', 'WARN': '⚠️', 'SKIP': '➖'}.get(s, '?')
            rows_html += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:7px 12px;border:1px solid #ddd;">{ic} {s}</td>'
                f'<td style="padding:7px 12px;border:1px solid #ddd;">{item["항목"]}</td>'
                f'<td style="padding:7px 12px;border:1px solid #ddd;">{item.get("비고","")}</td>'
                f'</tr>'
            )

    return f'''
<html><body style="font-family:'Malgun Gothic',Arial,sans-serif;padding:20px;color:#222;">
<h2 style="color:#2c3e50;">📋 준공서류 검토 결과</h2>
<table style="border-collapse:collapse;margin-bottom:20px;">
  <tr><td style="padding:5px 12px;"><b>업체명</b></td><td><b>{company}</b></td></tr>
  <tr><td style="padding:5px 12px;"><b>제출일시</b></td><td>{timestamp}</td></tr>
  <tr><td style="padding:5px 12px;"><b>최종결과</b></td><td><b style="font-size:16px;">{overall}</b></td></tr>
</table>
<table style="border-collapse:collapse;margin-bottom:20px;">
  <tr>
    <td style="background:#d5f5e3;padding:12px 20px;border-radius:6px;text-align:center;">✅ OK<br><b style="font-size:22px;">{ok}</b></td>
    <td style="width:8px;"></td>
    <td style="background:#fadbd8;padding:12px 20px;border-radius:6px;text-align:center;">❌ NG<br><b style="font-size:22px;">{ng}</b></td>
    <td style="width:8px;"></td>
    <td style="background:#fef9e7;padding:12px 20px;border-radius:6px;text-align:center;">⚠️ WARN<br><b style="font-size:22px;">{warn}</b></td>
    <td style="width:8px;"></td>
    <td style="background:#f2f3f4;padding:12px 20px;border-radius:6px;text-align:center;">➖ SKIP<br><b style="font-size:22px;">{skip}</b></td>
  </tr>
</table>
<h3>시트별 상세 결과</h3>
<table style="border-collapse:collapse;width:100%;max-width:700px;">
  <thead>
    <tr style="background:#2c3e50;color:white;">
      <th style="padding:9px 12px;border:1px solid #ddd;text-align:left;">결과</th>
      <th style="padding:9px 12px;border:1px solid #ddd;text-align:left;">검토항목</th>
      <th style="padding:9px 12px;border:1px solid #ddd;text-align:left;">비고</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
<p style="color:#999;font-size:12px;margin-top:25px;">이 메일은 준공서류검토봇이 자동으로 발송하였습니다.</p>
</body></html>'''
