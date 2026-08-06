# -*- coding: utf-8 -*-
"""
emailer.py
==========
종목별 HTML 리포트를 Gmail SMTP로 발송.
- 본문(inline): 시황요약 + 종목별 판정 한 줄 요약 + 하락경고
- 첨부: 각 종목 HTML 리포트 파일 (그날 index.html 포함)

환경변수:
  GMAIL_USER   : 보내는 Gmail 주소
  GMAIL_APP_PW : Gmail 앱 비밀번호 (2단계 인증 후 발급, 일반 비번 아님)
  MAIL_TO      : 받는 주소 (미설정 시 GMAIL_USER 로 자기 자신에게)
"""
from __future__ import annotations
import os
import smtplib
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formataddr


def _bear_summary_html(analyses: list[dict]) -> str:
    """하락경고 있는 종목만 모아 상단 요약."""
    rows = []
    for a in analyses:
        bear = a.get("bear_warnings", {})
        if bear.get("has_warning"):
            kinds = ", ".join(w["kind"] for w in bear["warnings"])
            rows.append(f'<li><b style="color:#c0182b">{a["name"]}</b> — {kinds}</li>')
    if not rows:
        return ""
    return ('<div style="background:#2a0d12;border:1.5px solid #c0182b;border-radius:8px;'
            'padding:12px 16px;margin-bottom:16px">'
            '<div style="color:#ff5a6a;font-weight:800;font-size:15px;margin-bottom:6px">'
            '🚨 하락 경고 종목</div>'
            f'<ul style="margin:4px 0;padding-left:20px;color:#f0d0d4">{"".join(rows)}</ul></div>')


def build_email_body(analyses: list[dict], market_text: str,
                      now_vn: dt.datetime) -> str:
    """이메일 본문 HTML (inline)."""
    # 시황
    market_html = market_text.replace("\n", "<br>")

    # 종목별 한 줄 요약
    rows = []
    for a in analyses:
        chg = a["change_pct"]
        col = "#d33" if chg > 0.05 else ("#1a56db" if chg < -0.05 else "#666")
        dv = a["daily_verdict"]
        dvcol = "#d33" if "상승" in dv["verdict"] or "매수" in dv["verdict"] else \
                ("#1a56db" if any(w in dv["verdict"] for w in ["하락", "매도", "주의", "관망"]) else "#666")
        bear = a.get("bear_warnings", {})
        bear_tag = ""
        if bear.get("has_warning"):
            bear_tag = ' <span style="color:#c0182b;font-weight:700">🚨' + \
                       ",".join(w["kind"] for w in bear["warnings"]) + '</span>'
        rows.append(
            f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee">'
            f'<b>{a["name"]}</b> <span style="color:{col}">{chg:+.1f}%</span>{bear_tag}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee;color:{dvcol};font-weight:700">'
            f'{dv["verdict"]} <span style="color:#999;font-weight:400">({dv["score"]:+d})</span></td></tr>')

    return f"""<div style="font-family:-apple-system,'Malgun Gothic',sans-serif;max-width:760px;margin:0 auto;color:#222">
  <h2 style="margin:0 0 12px">📊 포트폴리오 브리핑 · {now_vn:%Y-%m-%d %H:%M} (VN)</h2>
  <div style="background:#f6f8fa;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px">
    {market_html}
  </div>
  {_bear_summary_html(analyses)}
  <table style="width:100%;border-collapse:collapse;font-size:14px">
    <thead><tr>
      <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #ddd">종목</th>
      <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #ddd">일봉 종합</th>
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  <p style="color:#999;font-size:12px;margin-top:16px">
    상세 리포트(차트·다이버전스·수급·공매도·눌림목·하락경고)는 첨부된 HTML 파일을 열어 확인하세요.
  </p>
</div>"""


def send_email(analyses: list[dict], market_text: str,
               html_files: list[str], now_vn: dt.datetime | None = None) -> bool:
    """HTML 리포트를 Gmail SMTP로 발송. 실패 시 False."""
    user = os.environ.get("GMAIL_USER")
    app_pw = os.environ.get("GMAIL_APP_PW")
    to = os.environ.get("MAIL_TO", user)
    if not (user and app_pw):
        print("[warn] GMAIL_USER/GMAIL_APP_PW 미설정 — 이메일 skip")
        return False

    now_vn = now_vn or (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7))

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[포트폴리오] {now_vn:%m/%d %H:%M} 브리핑"
    msg["From"] = formataddr(("포트폴리오 브리핑", user))
    msg["To"] = to

    body = build_email_body(analyses, market_text, now_vn)
    msg.attach(MIMEText(body, "html", "utf-8"))

    # 첨부: 종목별 HTML
    for path in html_files:
        try:
            with open(path, "rb") as f:
                part = MIMEApplication(f.read(), _subtype="html")
            fname = os.path.basename(path)
            part.add_header("Content-Disposition", "attachment", filename=fname)
            msg.attach(part)
        except Exception as e:
            print(f"[warn] 첨부 실패 {path}: {e}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(user, app_pw)
            s.sendmail(user, [to], msg.as_string())
        print(f"[ok] 이메일 발송 완료 → {to}")
        return True
    except Exception as e:
        print(f"[error] 이메일 발송 실패: {e}")
        return False
