# -*- coding: utf-8 -*-
"""
portfolio_briefing.py
=====================
1) 종목별 상세 분석(요청 1~9번)을 계산해 data/ 폴더에 JSON+HTML로 저장
2) 카카오톡으로는 '요약(즉시 알람 + 종목별 한 줄 종합의견)'만 전송

데이터 수집 (타임아웃 완화):
  - daily(1년)   -> 일봉 시그널(60일 분석) + 주봉 리샘플
  - monthly_src(3년) -> 월봉 리샘플 (분석은 12개월 기준)
  - hourly(Naver 분봉 resample) -> 1시간봉 스토캐 장/중/단기

스케줄(베트남 ICT): 05/07/09/11/13/15/17/19시. cron(UTC): 0 0,2,4,6,8,10,12,22 * * *

실행:  python portfolio_briefing.py            # 저장 + 카톡 전송
       python portfolio_briefing.py --dry-run   # 저장만, 카톡 전송 없음
       python portfolio_briefing.py --demo       # 더미 데이터로 전체 확인
"""
from __future__ import annotations
import os
import json
import time
import datetime as dt
import argparse
import requests
import pandas as pd

import data_feed as feed
from signals import to_weekly, to_monthly
from analysis_engine import analyze_stock
from report_writer import save_report, write_day_index, _dir_color

HOLDINGS_PATH = os.environ.get("HOLDINGS_PATH", "holdings.json")
DAILY_YEARS = float(os.environ.get("DAILY_YEARS", "1"))
MONTHLY_YEARS = float(os.environ.get("MONTHLY_YEARS", "3"))


def _verdict_icon(verdict: str) -> str:
    if any(w in verdict for w in ["상승", "반등"]):
        return "🔴"
    if any(w in verdict for w in ["하락", "주의", "관망", "약세"]):
        return "🔵"
    return "⚪"


def process_stock(name: str, code: str, now_vn: dt.datetime, demo: bool = False):
    if demo:
        daily = feed.dummy_daily(seed=abs(hash(code)) % 1000)
        monthly_src = feed.dummy_monthly_source(seed=abs(hash(code)) % 1000)
        hourly = feed.dummy_hourly(seed=abs(hash(code)) % 1000)
    else:
        daily = feed.fetch_daily(code, years=DAILY_YEARS)
        monthly_src = feed.fetch_monthly_source(code, years=MONTHLY_YEARS)
        hourly = feed.fetch_hourly(code)

    if len(daily) < 70:
        return None, f"● {name}: 데이터 부족", []

    monthly = to_monthly(monthly_src) if len(monthly_src) >= 250 else to_monthly(daily)

    analysis = analyze_stock(name, code, daily, monthly, hourly, demo=demo)
    save_report(analysis, now_vn)

    dv = analysis["daily_verdict"]
    icon = _verdict_icon(dv["verdict"])
    chg = analysis["change_pct"]
    summary_line = f"{icon} {name} {chg:+.1f}% → {dv['verdict']}"

    alerts = []
    for frame in ["일봉", "월봉", "1H"]:
        d = analysis["divergence"].get(frame, {})
        if d.get("found"):
            tag = "🔴상승" if d["type"] == "상승" else "🔵하락"
            alerts.append(f"{name} {frame} {tag}다이버전스[{d['to_date']}]")
    ma = analysis["ma"]
    if ma.get("ok") and ma.get("forecast") and "골든크로스" in ma["forecast"]:
        alerts.append(f"{name} 골든크로스 임박")
    if analysis["shorting"].get("ok") and analysis["shorting"].get("short_5d_trend") == "상승":
        alerts.append(f"{name} 공매도 비중 상승")

    return analysis, summary_line, alerts


def build_summary_text(summaries, all_alerts, now_vn, index_url):
    header = f"📊 포트폴리오 요약 (VN {now_vn:%m/%d %H:%M})\n" + "─" * 20
    parts = [header]
    if all_alerts:
        parts.append("🔔 즉시 알람\n" + "\n".join(f" • {a}" for a in all_alerts))
        parts.append("─" * 20)
    parts.append("\n".join(summaries))
    parts.append("─" * 20)
    if index_url:
        parts.append(f"📑 상세 리포트: {index_url}")
    else:
        parts.append("📑 상세: GitHub 레포 data/ 폴더 참고")
    return "\n".join(parts)


def refresh_access_token():
    rest_key = os.environ.get("KAKAO_REST_KEY")
    refresh = os.environ.get("KAKAO_REFRESH_TOKEN")
    if not (rest_key and refresh):
        return os.environ.get("KAKAO_ACCESS_TOKEN")
    try:
        r = requests.post("https://kauth.kakao.com/oauth/token", data={
            "grant_type": "refresh_token",
            "client_id": rest_key,
            "refresh_token": refresh,
        }, timeout=10)
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        print(f"[warn] 토큰 갱신 실패: {e}")
        return os.environ.get("KAKAO_ACCESS_TOKEN")


def send_kakao(text):
    token = refresh_access_token()
    if not token:
        print("[error] 카카오 토큰 없음 — 전송 skip")
        return False
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {token}"}
    ok = True
    for chunk in _split(text, 3800):
        template = {"object_type": "text", "text": chunk,
                    "link": {"web_url": "https://finance.naver.com"}}
        try:
            r = requests.post(url, headers=headers,
                              data={"template_object": json.dumps(template)}, timeout=10)
            if r.status_code != 200:
                print(f"[error] 카카오 전송 실패 {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:
            print(f"[error] 카카오 전송 예외: {e}")
            ok = False
        time.sleep(0.3)
    return ok


def _split(text, limit):
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            out.append(buf); buf = ""
        buf += line + "\n"
    if buf:
        out.append(buf)
    return out


def load_holdings():
    with open(HOLDINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["holdings"] if isinstance(data, dict) else data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="저장만, 카톡 전송 없음")
    ap.add_argument("--demo", action="store_true", help="더미 데이터로 전체 확인")
    ap.add_argument("--force", action="store_true", help="비거래일에도 실행")
    args = ap.parse_args()

    if not args.demo and not args.force and not feed.is_kr_trading_day():
        print("비거래일 — skip (강제: --force)")
        return

    if args.demo:
        holdings = [{"name": "TK Corporation", "code": "023160"},
                    {"name": "대한항공", "code": "003490"},
                    {"name": "두산에너빌리티", "code": "034020"}]
    else:
        holdings = load_holdings()

    now_vn = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)

    analyses, summaries, all_alerts = [], [], []
    for h in holdings:
        name = h.get("name", h.get("code", "?"))
        code = h.get("code", "")
        if not code:
            continue
        try:
            analysis, line, alerts = process_stock(name, code, now_vn, demo=args.demo)
        except Exception as e:
            analysis, line, alerts = None, f"● {name}: 오류 {e}", []
        if analysis:
            analyses.append(analysis)
        summaries.append(line)
        all_alerts.extend(alerts)
        if not args.demo:
            time.sleep(0.4)

    index_url = None
    if analyses:
        write_day_index(analyses, now_vn)
        pages_base = os.environ.get("PAGES_BASE_URL")
        if pages_base:
            index_url = f"{pages_base}/data/{now_vn:%Y-%m-%d}/index.html"

    text = build_summary_text(summaries, all_alerts, now_vn, index_url)

    if args.dry_run or args.demo:
        print(text)
        print("\n[저장 완료] data/ 폴더 확인")
    else:
        send_kakao(text)


if __name__ == "__main__":
    main()
