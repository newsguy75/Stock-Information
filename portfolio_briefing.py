# -*- coding: utf-8 -*-
"""
portfolio_briefing.py  (이메일 전용 버전)
==========================================
GitHub Pages 미사용. 비공개 레포에서 동작.
- 카카오: 요약 헤더 + 종목별 개별 메시지(200자 이내)
- 이메일: HTML 리포트 본문 렌더 + .html 첨부 (다운로드해서 열기)

실행:
  python portfolio_briefing.py             # 카카오 + 이메일 전송
  python portfolio_briefing.py --dry-run    # 전송 없이 HTML 생성 + 콘솔
  python portfolio_briefing.py --demo        # 더미 데이터
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


# -------------------- 카카오 --------------------
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


def _send_text(token, text):
    text = text[:195] + "…" if len(text) > 200 else text
    template = {"object_type": "text", "text": text,
                "link": {"web_url": "https://developers.kakao.com"}}
    r = requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                      headers={"Authorization": f"Bearer {token}"},
                      data={"template_object": json.dumps(template)}, timeout=10)
    if r.status_code != 200:
        print(f"[error] 카카오 전송 실패 {r.status_code}: {r.text[:200]}")
        return False
    return True


def send_kakao(results, summaries):
    token = refresh_access_token()
    if not token:
        print("[warn] 카카오 토큰 없음 — 카카오 전송 skip")
        return False
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    buy = sum(1 for r in results if r["verdict"].stance == "매수우위")
    cut = sum(1 for r in results if r["verdict"].stance == "비중축소")
    ev = sum(1 for r in results if r["events"])
    head = f"📊 브리핑 {now:%m/%d %H:%M}\n🔴매수우위 {buy} · 🔵비중축소 {cut} · 이벤트 {ev}\n(상세는 이메일 확인)"
    _send_text(token, head); time.sleep(0.35)
    for s in summaries:
        _send_text(token, s); time.sleep(0.35)
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
        send_kakao(results, summaries)
        send_email_report(html)


if __name__ == "__main__":
    main()
