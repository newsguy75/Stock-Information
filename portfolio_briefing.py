# -*- coding: utf-8 -*-
"""
portfolio_briefing.py
=====================
멀티프레임 포트폴리오 브리핑 (일봉/주봉/월봉 MA 시그널 + 일봉/1시간봉 거래량 알람
+ 1시간봉 스토캐스틱 동조화/다이버전스) 를 생성하고 카카오톡으로 전송.

스케줄(베트남 ICT 기준): 05 / 07 / 09 / 11 / 13 / 15 / 17 / 19 시, 1일 8회.
GitHub Actions cron(UTC): 0 0,2,4,6,8,10,12,22 * * *

데이터 수집 (2026-08 수정: 타임아웃 완화를 위해 일봉/월봉 소스 분리):
  - daily(1년)   -> 일봉 시그널 + 주봉 리샘플 (MA5/20, 다이버전스)
  - monthly_src(3년) -> 월봉 리샘플 전용 (MA5/20, 다이버전스. MA60은 생략)

실행:  python portfolio_briefing.py            # holdings.json 읽어 전송
       python portfolio_briefing.py --dry-run   # 전송 없이 콘솔 출력
       python portfolio_briefing.py --demo       # 더미 데이터로 포맷 확인
"""
from __future__ import annotations
import os
import sys
import json
import time
import datetime as dt
import argparse
import requests
import pandas as pd

import data_feed as feed
from signals import (SignalConfig, compute_ma_signal, compute_volume_signal,
                     to_weekly, to_monthly)
from mtf_stoch_scanner import (stochastic, analyze_mtf_sync, detect_divergence,
                               resample_ohlcv)

CFG = SignalConfig()
HOLDINGS_PATH = os.environ.get("HOLDINGS_PATH", "holdings.json")

# 데이터 수집 기간 (년) - 여기서 한번에 조정
DAILY_YEARS = float(os.environ.get("DAILY_YEARS", "1"))       # 일봉/주봉 소스
MONTHLY_YEARS = float(os.environ.get("MONTHLY_YEARS", "3"))   # 월봉 전용 소스

# 이모지 표기
DIR_ICON = {"상향": "🔺", "보합": "➖", "하향": "🔻"}
ALIGN_ICON = {"정배열": "✅정배열", "역배열": "⛔역배열", "혼조": "〰️혼조"}


# ----------------------------------------------------------------------
# 프레임별 MA 라인 포맷
# ----------------------------------------------------------------------
def _ma_block(label: str, sig, show_touch: bool = True) -> str:
    parts = [f"{label} MA5 {DIR_ICON[sig.ma5_direction]}{sig.ma5_direction}"
             f"({sig.ma5_slope_pct:+.1f}%) | {ALIGN_ICON[sig.alignment]}"]
    crosses = []
    if sig.cross_5_20:
        crosses.append(f"5·20 {sig.cross_5_20}")
    if sig.cross_20_60:
        crosses.append(f"20·60 {sig.cross_20_60}")
    if sig.cross_5_60:
        crosses.append(f"5·60 {sig.cross_5_60}")
    if crosses:
        parts.append("  ⚡" + ", ".join(crosses))
    if show_touch and sig.above_ma5 and sig.ma5_touch:
        parts.append("  🎯5일선 지지터치(눌림)")
    return "\n".join(parts)


def _vol_block(label: str, vsig) -> str:
    if vsig.over_vol_ma:
        return f"{label} 거래량 🚨5일선 돌파 (x{vsig.ratio:.1f})"
    return f"{label} 거래량 5일선 이내 (x{vsig.ratio:.1f})"


# ----------------------------------------------------------------------
# 종목 1개 리포트
# ----------------------------------------------------------------------
def build_stock_report(name: str, code: str, demo: bool = False) -> tuple[str, list[str]]:
    """
    반환: (본문 텍스트, 알람 리스트)
    알람 리스트: 거래량 돌파·크로스 발생·5일선 터치 등 '즉시 주목' 항목

    데이터 소스 분리:
      - daily(1년): 일봉 시그널 + 주봉 리샘플용
      - monthly_src(3년): 월봉 리샘플 전용 (별도 호출, MA60은 기간 부족으로 생략)
    """
    alerts: list[str] = []

    # --- 데이터 수집 ---
    if demo:
        daily = feed.dummy_daily(seed=abs(hash(code)) % 1000)
        monthly_src = feed.dummy_monthly_source(seed=abs(hash(code)) % 1000)
        hourly = feed.dummy_hourly(seed=abs(hash(code)) % 1000)
    else:
        daily = feed.fetch_daily(code, years=DAILY_YEARS)
        monthly_src = feed.fetch_monthly_source(code, years=MONTHLY_YEARS)
        hourly = feed.fetch_hourly(code)

    if len(daily) < 70:
        return f"● {name}({code}): 데이터 부족(일봉 {len(daily)}봉)", alerts

    weekly = to_weekly(daily)
    # 월봉은 별도로 받은 3년치 소스에서 리샘플 (실패 시 daily로 폴백)
    monthly = to_monthly(monthly_src) if len(monthly_src) >= 250 else to_monthly(daily)

    # --- 일봉 ---
    d_ma = compute_ma_signal(daily, CFG)
    d_vol = compute_volume_signal(daily, CFG)
    # --- 주봉 / 월봉 ---
    w_ma = compute_ma_signal(weekly, CFG) if len(weekly) >= 25 else None
    # 월봉은 3년(36개월) 기준이라 MA60은 원천적으로 안 나옴 -> MA20 확보 기준(22개월)으로 완화
    m_ma = compute_ma_signal(monthly, CFG) if len(monthly) >= 22 else None

    last_close = daily["close"].iloc[-1]
    prev_close = daily["close"].iloc[-2]
    chg = (last_close - prev_close) / prev_close * 100

    lines = [f"● {name}({code})  {last_close:,.0f}원 ({chg:+.2f}%)"]
    lines.append(_ma_block("[일]", d_ma))
    lines.append(_vol_block("[일]", d_vol))
    if w_ma:
        lines.append(_ma_block("[주]", w_ma))
    if m_ma:
        lines.append(_ma_block("[월]", m_ma))
        if m_ma.alignment == "혼조" and pd.isna(m_ma.ma60):
            pass  # MA60 미형성은 정상 (3년 소스 한계) - 별도 경고 없이 조용히 생략
    else:
        lines.append("[월] 데이터 부족(월봉 생략)")

    # --- 1시간봉 ---
    if len(hourly) >= 70:
        h_ma = compute_ma_signal(hourly, CFG)
        h_vol = compute_volume_signal(hourly, CFG)
        lines.append(f"[1H] MA5 {DIR_ICON[h_ma.ma5_direction]}{h_ma.ma5_direction} "
                     f"| {ALIGN_ICON[h_ma.alignment]}")
        lines.append(_vol_block("[1H]", h_vol))
        if h_vol.over_vol_ma:
            alerts.append(f"{name} 1H 거래량 5봉선 돌파 x{h_vol.ratio:.1f}")

        # 스토캐스틱 멀티프레임 동조화 + 다이버전스 (1H)
        try:
            sync = analyze_mtf_sync(
                {"60min": hourly}, anchor_tf="60min",
                stoch_params={"60min": (24, 5, 5)},
            )
            if sync and sync[-1].anchor_turn:
                at = sync[-1].anchor_turn
                lines.append(f"[1H] 스토캐 상승전환 {at.from_zone} (%K {at.k_value:.0f})")
            divs = detect_divergence(hourly, stochastic(hourly))
            if divs:
                d = divs[-1]
                tag = "🟢상승" if d.type == "bullish" else "🔴하락"
                lines.append(f"[1H] {tag}다이버전스 감지")
                alerts.append(f"{name} 1H {tag}다이버전스")
        except Exception as e:
            lines.append(f"[1H] 스토캐 계산 skip ({e})")
    else:
        lines.append("[1H] 데이터 수집 실패/부족")

    # --- 일봉 스토캐 다이버전스 (메인 3지표 중 하나) ---
    try:
        divs_d = detect_divergence(daily, stochastic(daily))
        if divs_d:
            d = divs_d[-1]
            tag = "🟢상승" if d.type == "bullish" else "🔴하락"
            lines.append(f"[일] {tag}다이버전스 감지")
            alerts.append(f"{name} 일봉 {tag}다이버전스")
    except Exception as e:
        lines.append(f"[일] 스토캐 계산 skip ({e})")

    # --- 월봉 스토캐 다이버전스 (메인 3지표 중 하나, 36개월 소스 기준) ---
    if m_ma is not None:
        try:
            divs_m = detect_divergence(monthly, stochastic(monthly))
            if divs_m:
                d = divs_m[-1]
                tag = "🟢상승" if d.type == "bullish" else "🔴하락"
                lines.append(f"[월] {tag}다이버전스 감지")
                alerts.append(f"{name} 월봉 {tag}다이버전스")
        except Exception as e:
            lines.append(f"[월] 스토캐 계산 skip ({e})")

    # --- 알람 취합 ---
    if d_vol.over_vol_ma:
        alerts.append(f"{name} 일봉 거래량 5일선 돌파 x{d_vol.ratio:.1f}")
    for lbl, s in [("일", d_ma), ("주", w_ma), ("월", m_ma)]:
        if s is None:
            continue
        if s.cross_5_20 == "골든크로스" or s.cross_20_60 == "골든크로스":
            alerts.append(f"{name} {lbl}봉 골든크로스")
        if s.cross_5_20 == "데드크로스" or s.cross_20_60 == "데드크로스":
            alerts.append(f"{name} {lbl}봉 데드크로스")
        if s.above_ma5 and s.ma5_touch:
            alerts.append(f"{name} {lbl}봉 5일선 지지터치")

    return "\n".join(lines), alerts


# ----------------------------------------------------------------------
# 전체 브리핑 조립
# ----------------------------------------------------------------------
def build_briefing(holdings: list[dict], demo: bool = False) -> str:
    now_ict = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    header = f"📊 포트폴리오 브리핑 (VN {now_ict:%m/%d %H:%M})\n" + "─" * 22
    body_blocks, all_alerts = [], []

    for h in holdings:
        name = h.get("name", h.get("code", "?"))
        code = h.get("code", "")
        if not code:
            continue
        try:
            block, alerts = build_stock_report(name, code, demo=demo)
        except Exception as e:
            block, alerts = f"● {name}({code}): 오류 {e}", []
        body_blocks.append(block)
        all_alerts.extend(alerts)
        if not demo:
            time.sleep(0.4)   # 네이버/FDR 과호출 방지

    parts = [header]
    if all_alerts:
        parts.append("🔔 즉시 알람\n" + "\n".join(f" • {a}" for a in all_alerts))
        parts.append("─" * 22)
    parts.append("\n\n".join(body_blocks))
    return "\n".join(parts)


# ----------------------------------------------------------------------
# 카카오톡 전송 (나에게 보내기)
# ----------------------------------------------------------------------
def refresh_access_token() -> str | None:
    """리프레시 토큰으로 액세스 토큰 갱신 (약 2개월마다 리프레시 토큰 만료)."""
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


def send_kakao(text: str) -> bool:
    """카카오 '나와의 채팅방'으로 텍스트 메시지 전송. 4000자 초과 시 분할."""
    token = refresh_access_token()
    if not token:
        print("[error] 카카오 토큰 없음 — 전송 skip")
        return False
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {token}"}
    ok = True
    for chunk in _split(text, 3800):
        template = {
            "object_type": "text",
            "text": chunk,
            "link": {"web_url": "https://finance.naver.com"},
        }
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


def _split(text: str, limit: int) -> list[str]:
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            out.append(buf)
            buf = ""
        buf += line + "\n"
    if buf:
        out.append(buf)
    return out


# ----------------------------------------------------------------------
# 엔트리포인트
# ----------------------------------------------------------------------
def load_holdings() -> list[dict]:
    with open(HOLDINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    # holdings.json 이 리스트이거나 {"holdings":[...]} 둘 다 허용
    return data["holdings"] if isinstance(data, dict) else data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="전송 없이 콘솔 출력")
    ap.add_argument("--demo", action="store_true", help="더미 데이터로 포맷 확인")
    ap.add_argument("--force", action="store_true", help="비거래일에도 실행")
    args = ap.parse_args()

    if not args.demo and not args.force and not feed.is_kr_trading_day():
        print("비거래일 — 브리핑 skip (강제: --force)")
        return

    if args.demo:
        holdings = [{"name": "TK Corporation", "code": "023160"},
                    {"name": "대한항공", "code": "003490"},
                    {"name": "두산에너빌리티", "code": "034020"}]
    else:
        holdings = load_holdings()

    text = build_briefing(holdings, demo=args.demo)

    if args.dry_run or args.demo:
        print(text)
    else:
        send_kakao(text)


if __name__ == "__main__":
    main()
