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
from market_index import build_market_summary

HOLDINGS_PATH = os.environ.get("HOLDINGS_PATH", "holdings.json")
DAILY_YEARS = float(os.environ.get("DAILY_YEARS", "1"))
MONTHLY_YEARS = float(os.environ.get("MONTHLY_YEARS", "5"))


def _verdict_icon(verdict: str) -> str:
    # (하위호환용) 판정 기준 아이콘
    if any(w in verdict for w in ["상승", "반등"]):
        return "🔴"
    if any(w in verdict for w in ["하락", "주의", "관망", "약세"]):
        return "🔵"
    return "⚪"


def _chg_icon(chg: float) -> str:
    # 등락률 기준 아이콘 (상승🔴 / 보합⚪ / 하락🔵)
    if chg > 0.05:
        return "🔴"
    if chg < -0.05:
        return "🔵"
    return "⚪"


def build_stock_message(analysis: dict) -> str:
    """종목 1개 카톡 메시지 (스크린샷 포맷).
    🔴 종목명 가격(등락%) [판정 +score] [판정] 핵심시그널 나열
    """
    name = analysis["name"]
    dv = analysis["daily_verdict"]
    chg = analysis["change_pct"]
    icon = _chg_icon(chg)
    price = analysis["price"]

    # 하락 경고 우선 표기 (일봉 하락다이버전스/쌍봉/데드캣바운스)
    bear = analysis.get("bear_warnings", {})
    bear_line = ""
    if bear.get("has_warning"):
        kinds = [w["kind"] for w in bear["warnings"]]
        bear_line = "🚨 " + ", ".join(kinds) + "\n"

    # 판정 라벨 (score 구간별 세분화)
    score = dv["score"]
    if score >= 4:
        tag = "적극매수"
    elif score >= 2:
        tag = "매수우위"
    elif score >= 1:
        tag = "매수관심"
    elif score == 0:
        tag = "관망"
    elif score >= -2:
        tag = "주의"
    else:
        tag = "매도관심"

    # 호재 시그널 (긍정)
    pos_sigs, neg_sigs = [], []
    for frame in ["월봉", "일봉", "1H"]:
        d = analysis["divergence"].get(frame, {})
        if d.get("found"):
            if d["type"] == "상승":
                pos_sigs.append(f"{frame} 상승다이버전스")
            else:
                neg_sigs.append(f"{frame} 하락다이버전스")
    # 월봉 과매도권 상승전환 (호재)
    ms = analysis["stoch_frames"]["monthly"]
    for k in ["장기", "중기", "단기"]:
        node = ms.get(k, {})
        if node.get("ok") and node.get("zone") == "과매도" and node.get("direction") == "상승":
            pos_sigs.append("월봉 과매도권 상승전환")
            break
    # 이평 (호재/악재)
    ma = analysis["ma"]
    if ma.get("ok"):
        if ma.get("forecast"):
            if "골든크로스" in ma["forecast"]:
                pos_sigs.append("일봉 5·20 골든크로스 임박")
            elif "데드크로스" in ma["forecast"]:
                neg_sigs.append("일봉 5·20 데드크로스 우려")
        f5 = ma.get("ma5_forecast", {})
        if f5.get("ok"):
            if f5["direction"] == "상승":
                pos_sigs.append("MA5 상향")
            elif f5["direction"] == "하락":
                neg_sigs.append("MA5 하향")
        if ma.get("note") and "눌림" in ma["note"]:
            pos_sigs.append("5일선 지지터치(눌림목)")
    # 눌림목 매수 신호
    pb = analysis.get("pullback", {})
    if pb.get("ok") and pb.get("has_signal"):
        s = pb["signals"][0]
        pos_sigs.append(f"{s['line']} 눌림목(손절 {s['stop']:,})")
    # 공매도 (악재/호재)
    sh = analysis["shorting"]
    if sh.get("ok"):
        d5t = sh.get("d5", {}).get("trend", "")
        if "상승" in d5t:
            neg_sigs.append("공매도 5일↑")

    # 호재 위주로 표기, 악재는 뒤에 ⚠로
    line2 = f"[{tag}]"
    if pos_sigs:
        line2 += " " + ", ".join(pos_sigs[:4])
    if neg_sigs:
        line2 += (" ⚠" if pos_sigs else " ") + ", ".join(neg_sigs[:3])
    if not pos_sigs and not neg_sigs:
        # 다이버전스/크로스 같은 이벤트가 없으면 판정 근거를 대신 표시
        reasons = dv.get("reasons", [])
        line2 += " " + (", ".join(reasons[:4]) if reasons else "특이신호 없음")

    return (f"{bear_line}{icon} {name} {price:,}({chg:+.1f}%) [{tag} {score:+d}]\n{line2}")


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
    saved = save_report(analysis, now_vn)
    analysis["_html_path"] = saved.get("html")

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
    sh = analysis["shorting"]
    if sh.get("ok") and "상승" in sh.get("d5", {}).get("trend", ""):
        alerts.append(f"{name} 공매도 5일 상승")

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


def send_kakao_message(text, token, link_url=None):
    """단일 메시지 전송 (토큰 재사용). 4000자 초과 시 분할."""
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {token}"}
    ok = True
    for chunk in _split(text, 3800):
        template = {"object_type": "text", "text": chunk,
                    "link": {"web_url": link_url or "https://finance.naver.com"}}
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


def send_kakao(text):
    """호환용: 단건 전송 (토큰 자동 발급)."""
    token = refresh_access_token()
    if not token:
        print("[error] 카카오 토큰 없음 — 전송 skip")
        return False
    return send_kakao_message(text, token)


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
    pages_base = os.environ.get("PAGES_BASE_URL")

    # --- 1) 시황 요약 (KOSPI/KOSDAQ) ---
    market_text = build_market_summary(demo=args.demo)

    # --- 2) 종목별 분석 + 저장 + 개별 메시지 준비 ---
    analyses = []
    stock_messages = []   # [(메시지, 상세링크)]
    for h in holdings:
        name = h.get("name", h.get("code", "?"))
        code = h.get("code", "")
        if not code:
            continue
        try:
            analysis, _line, _alerts = process_stock(name, code, now_vn, demo=args.demo)
        except Exception as e:
            analysis = None
            stock_messages.append((f"● {name}: 오류 {e}", None))
        if analysis:
            analyses.append(analysis)
            msg = build_stock_message(analysis)
            safe = name.replace(" ", "-").replace("/", "-")
            link = (f"{pages_base}/data/latest/{code}_{safe}.html"
                    if pages_base else None)
            stock_messages.append((msg, link))
        if not args.demo:
            time.sleep(0.4)

    if analyses:
        write_day_index(analyses, now_vn)

    # 첨부용 HTML 파일 경로 수집 (+ 그날 index)
    html_files = [a["_html_path"] for a in analyses if a.get("_html_path")]
    index_path = os.path.join(os.environ.get("DATA_ROOT", "data"),
                              now_vn.strftime("%Y-%m-%d"), "index.html")
    if os.path.exists(index_path):
        html_files = [index_path] + html_files

    # --- 3) 출력 or 전송 ---
    if args.dry_run or args.demo:
        print(market_text)
        print()
        for msg, link in stock_messages:
            print(msg)
            if link:
                print(f"  🔗 {link}")
            print()
        print(f"[저장 완료] data/ 폴더 · HTML {len(html_files)}개")
        # 이메일 미리보기 (전송은 안 함)
        if os.environ.get("GMAIL_USER"):
            print("[dry-run] GMAIL_USER 설정됨 — 실행 시 이메일 발송됨")
        return

    # 카카오: 시황요약 먼저 → 종목별 개별
    token = refresh_access_token()
    if token:
        send_kakao_message(market_text, token)
        time.sleep(0.5)
        for msg, link in stock_messages:
            send_kakao_message(msg, token, link_url=link)
            time.sleep(0.5)
    else:
        print("[error] 카카오 토큰 없음 — 카톡 전송 skip")

    # 이메일: HTML 리포트 첨부 발송 (기존 스케줄러에서 함께 실행)
    try:
        from emailer import send_email
        send_email(analyses, market_text, html_files, now_vn)
    except Exception as e:
        print(f"[warn] 이메일 발송 예외: {e}")


if __name__ == "__main__":
    main()
