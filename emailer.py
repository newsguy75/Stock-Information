# -*- coding: utf-8 -*-
"""
emailer.py
==========
브리핑 HTML을 이메일로 전송.
  - 본문(inline): HTML 리포트를 메일 본문에 그대로 렌더 (폰에서 바로 보임)
  - 첨부(attachment): 같은 HTML을 .html 파일로 첨부 (다운로드해서 열기)

Gmail SMTP 기준. Gmail은 '앱 비밀번호'(16자리)가 필요:
  Google 계정 → 보안 → 2단계 인증 켠 뒤 → 앱 비밀번호 생성.

환경변수:
  EMAIL_FROM      보내는 Gmail 주소 (예: you@gmail.com)
  EMAIL_APP_PW    Gmail 앱 비밀번호 (공백 없이 16자)
  EMAIL_TO        받는 주소 (기본: EMAIL_FROM 과 동일 = 나에게 보내기)
  SMTP_HOST/SMTP_PORT  (기본 smtp.gmail.com / 465, SSL)
"""
from __future__ import annotations
import os
import ssl
import smtplib
import datetime as dt
from email.message import EmailMessage


def send_email_report(html: str, subject: str | None = None,
                      attach_name: str | None = None) -> bool:
    sender = os.environ.get("EMAIL_FROM")
    app_pw = os.environ.get("EMAIL_APP_PW")
    to = os.environ.get("EMAIL_TO") or sender
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    if not (sender and app_pw and to):
        print("[warn] 이메일 설정(EMAIL_FROM/EMAIL_APP_PW) 없음 — 이메일 전송 skip")
        return False

    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    subject = subject or f"📊 포트폴리오 브리핑 {now:%m/%d %H:%M} (VN)"
    attach_name = attach_name or f"briefing_{now:%Y%m%d_%H%M}.html"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content("HTML을 지원하지 않는 메일 앱입니다. 첨부된 html 파일을 열어주세요.")
    # 본문에 HTML inline
    msg.add_alternative(html, subtype="html")
    # 동일 HTML을 파일로 첨부
    msg.add_attachment(html.encode("utf-8"), maintype="text", subtype="html",
                       filename=attach_name)

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
                s.login(sender, app_pw)
                s.send_message(msg)
        else:  # 587 STARTTLS
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(sender, app_pw)
                s.send_message(msg)
        print(f"[ok] 이메일 전송 완료 → {to}")
        return True
    except Exception as e:
        print(f"[error] 이메일 전송 실패: {e}")
        return False


if __name__ == "__main__":
    demo_html = "<h1>테스트 리포트</h1><p style='color:#e03131'>상승</p><p style='color:#1971c2'>하락</p>"
    ok = send_email_report(demo_html, subject="브리핑 테스트")
    print("결과:", ok)
