# -*- coding: utf-8 -*-
"""
portfolio_briefing.py (v3)
==========================
- 수급(외국인/기관/개인 3주체) + 종합 인사이트 판정 + Claude API 코멘트 통합
- 카카오: 요약 헤더 + 종목별 개별 메시지(200자 이내, 종합판정+코멘트)
- HTML: 전체 이벤트 요약 + 종목별 카드(차트/수급/판정)

실행:
  python portfolio_briefing.py            # HTML + 카카오 전송
  python portfolio_briefing.py --dry-run   # 전송 없이 HTML 생성 + 콘솔
  python portfolio_briefing.py --demo       # 더미 데이터 (API/수급 off)
환경변수:
  ANTHROPIC_API_KEY  (있으면 API 코멘트, 없으면 규칙 코멘트)
  KAKAO_REST_KEY / KAKAO_REFRESH_TOKEN / KAKAO_ACCESS_TOKEN
  PAGES_URL          (카카오 메시지에 붙일 상세 링크)
"""
from __future__ import annotations
import os, json, time, argparse
import datetime as dt
import requests

import data_feed as feed
from report_html import build_html, analyze_stock, one_line_summary_from
from indices import fetch_indices
from emailer import send_email_report

HOLDINGS_PATH = os.environ.get("HOLDINGS_PATH", "holdings.json")
REPORT_DIR = os.environ.get("REPORT_DIR", "report")
PAGES_URL = os.environ.get("PAGES_URL", "")


def collect(holdings, demo=False):
    results, summaries = [], []
    use_api = bool(os.environ.get("ANTHROPIC_API_KEY")) and not demo
    for h in holdings:
        name = h.get("name", h.get("code", "?"))
        code = h.get("code", "")
        if not code:
            continue
        try:
            if demo:
                daily = feed.dummy_daily(seed=abs(hash(code)) % 1000)
                hourly = feed.dummy_hourly(seed=abs(hash(code)) % 1000)
            else:
                daily = feed.fetch_daily(code, years=6)
                hourly = feed.fetch_hourly(code)
            if len(daily) < 70:
                summaries.append(f"⚪ {name} 데이터 부족")
                continue
            res = analyze_stock(name, code, daily, hourly=hourly, use_api=use_api)
            results.append(res)
            summaries.append(one_line_summary_from(res)[0])
        except Exception as e:
            summaries.append(f"⚪ {name}({code}) 오류: {e}")
        if not demo:
            time.sleep(0.5)
    return results, summaries


def save_html(results, index_views=None):
    os.makedirs(REPORT_DIR, exist_ok=True)
    html = build_html(results, index_views=index_views or [])
    path = os.path.join(REPORT_DIR, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path, html


def refresh_access_token():
    rest_key = os.environ.get("KAKAO_REST_KEY")
    refresh = os.environ.get("KAKAO_REFRESH_TOKEN")
    if not (rest_key and refresh):
        return os.environ.get("KAKAO_ACCESS_TOKEN")
    try:
        r = requests.post("https://kauth.kakao.com/oauth/token", data={
            "grant_type": "refresh_token", "client_id": rest_key,
            "refresh_token": refresh}, timeout=10)
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        print(f"[warn] 토큰 갱신 실패: {e}")
        return os.environ.get("KAKAO_ACCESS_TOKEN")


def _send_text(token, text, link_url=""):
    text = text[:195] + "…" if len(text) > 200 else text
    template = {"object_type": "text", "text": text,
                "link": {"web_url": link_url or "https://developers.kakao.com"}}
    r = requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                      headers={"Authorization": f"Bearer {token}"},
                      data={"template_object": json.dumps(template)}, timeout=10)
    if r.status_code != 200:
        print(f"[error] 카카오 전송 실패 {r.status_code}: {r.text[:200]}")
        return False
    return True


def send_kakao(results, summaries, link_url=""):
    token = refresh_access_token()
    if not token:
        print("[error] 카카오 토큰 없음 — 전송 skip")
        return False
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    buy = sum(1 for r in results if r["verdict"].stance == "매수우위")
    cut = sum(1 for r in results if r["verdict"].stance == "비중축소")
    ev = sum(1 for r in results if r["events"])
    head = f"📊 브리핑 {now:%m/%d %H:%M}\n🔴매수우위 {buy} · 🔵비중축소 {cut} · 이벤트 {ev}"
    if link_url:
        head += f"\n상세: {link_url}"
    _send_text(token, head, link_url); time.sleep(0.35)
    for s in summaries:
        _send_text(token, s, link_url); time.sleep(0.35)
    return True


def load_holdings():
    with open(HOLDINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["holdings"] if isinstance(data, dict) else data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not args.demo and not args.force and not feed.is_kr_trading_day():
        print("비거래일 — skip (강제: --force)"); return

    if args.demo:
        holdings = [{"name": "TK Corporation", "code": "023160"},
                    {"name": "대한항공", "code": "003490"},
                    {"name": "두산에너빌리티", "code": "034020"}]
    else:
        holdings = load_holdings()

    results, summaries = collect(holdings, demo=args.demo)
    index_views = [] if args.demo else fetch_indices()
    path, html = save_html(results, index_views=index_views)
    print(f"HTML 저장: {path}")
    if args.dry_run or args.demo:
        print("\n".join(summaries))
    else:
        send_kakao(results, summaries, link_url=PAGES_URL)
        send_email_report(html)   # 이메일로 HTML 본문+첨부 전송


if __name__ == "__main__":
    main()
