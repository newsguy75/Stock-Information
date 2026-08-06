# -*- coding: utf-8 -*-
"""
analysis_engine.py
==================
종목별 상세 분석(요청 1~9번)을 계산해 구조화된 dict로 반환.
data/ 폴더에 JSON/HTML로 저장하기 위한 데이터 생성 계층.

분석 항목:
  3. 스토캐스틱 프레임별(1H 장/중/단기, 일봉, 월봉) 방향성 예측
  4. 이평선 분석 (5/20 위치, 골든크로스 근접도, N일 뒤 크로스 예측)
  5. 거래량 및 수급 (외인/기관/개인 5일·20일 순매수 + 비중)
  6. 공매도 현황 (5일 추세, 비중 급증 여부)
  7. 기타 기술적 인사이트 (스토캐 Gate/시간대칭 등)
  8. 일봉 종합 의견
  9. 월봉 분석 (동일 양식, 12개월 기준)

수급(5)·공매도(6)는 KRX 소스 의존이라 실패 시 {"ok": False} 로 폴백.
일봉 분석은 60일치, 월봉 분석은 12개월치 기준 (요청 9번).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import datetime as dt
import numpy as np
import pandas as pd

from mtf_stoch_scanner import stochastic, detect_divergence


# ======================================================================
# 3. 스토캐스틱 프레임별 방향성
# ======================================================================
def _stoch_dir(df: pd.DataFrame, k_period: int, k_smooth: int, d_period: int) -> dict:
    """단일 파라미터 세트의 스토캐 상태 + 방향."""
    if df is None or len(df) < k_period + k_smooth + d_period + 2:
        return {"ok": False}
    s = stochastic(df, k_period, k_smooth, d_period)
    k = s["%K"].dropna()
    d = s["%D"].dropna()
    if len(k) < 3:
        return {"ok": False}
    k_now, k_prev = float(k.iloc[-1]), float(k.iloc[-2])
    d_now = float(d.iloc[-1]) if len(d) else float("nan")
    # 방향: 상승/하락/보합 (%K 기울기 + %K vs %D)
    slope = k_now - k_prev
    if slope > 1.5:
        direction = "상승"
    elif slope < -1.5:
        direction = "하락"
    else:
        direction = "보합"
    zone = "과매수" if k_now >= 80 else ("과매도" if k_now <= 20 else "중립")
    cross = None
    if len(k) >= 2 and len(d) >= 2:
        if k.iloc[-2] < d.iloc[-2] and k.iloc[-1] > d.iloc[-1]:
            cross = "골든크로스"
        elif k.iloc[-2] > d.iloc[-2] and k.iloc[-1] < d.iloc[-1]:
            cross = "데드크로스"
    return {"ok": True, "k": round(k_now, 1), "d": round(d_now, 1),
            "direction": direction, "zone": zone, "cross": cross}


def analyze_stoch_frames(hourly: pd.DataFrame, daily: pd.DataFrame,
                          monthly: pd.DataFrame) -> dict:
    """
    1시간봉: 장기(40-20-20)/중기(20-10-10)/단기(5-3-3)
    일봉:    장기(20-10-10)/중기(10-5-5)/단기(5-3-3)
    월봉:    단기(5-3-3) 중심 (12개월 데이터 한계로 장기 생략)
    """
    out = {"hourly": {}, "daily": {}, "monthly": {}, "verdict": {}}

    # --- 1시간봉 3구간 ---
    out["hourly"]["장기"] = _stoch_dir(hourly, 40, 20, 20)
    out["hourly"]["중기"] = _stoch_dir(hourly, 20, 10, 10)
    out["hourly"]["단기"] = _stoch_dir(hourly, 5, 3, 3)
    # --- 일봉 3구간 ---
    out["daily"]["장기"] = _stoch_dir(daily, 20, 10, 10)
    out["daily"]["중기"] = _stoch_dir(daily, 10, 5, 5)
    out["daily"]["단기"] = _stoch_dir(daily, 5, 3, 3)
    # --- 월봉 (12개월 한계) ---
    out["monthly"]["중기"] = _stoch_dir(monthly, 10, 5, 5)
    out["monthly"]["단기"] = _stoch_dir(monthly, 5, 3, 3)

    out["verdict"] = _stoch_verdict(out)
    return out


def _dir_of(frame: dict, key: str) -> Optional[str]:
    node = frame.get(key, {})
    return node.get("direction") if node.get("ok") else None


def _stoch_verdict(out: dict) -> dict:
    """프레임별 방향성 종합해 문장형 예측 생성."""
    h = out["hourly"]; d = out["daily"]; m = out["monthly"]
    parts = []

    # 1시간봉 요약
    hl, hm, hs = _dir_of(h, "장기"), _dir_of(h, "중기"), _dir_of(h, "단기")
    if all([hl, hm, hs]):
        line = f"1H 장기{hl}·중기{hm}·단기{hs}"
        if hs == "하락" and hl in ("상승", "보합"):
            line += " → 단기 조정 예상(장기 방향은 유효)"
        elif hs == "상승" and hl == "상승":
            line += " → 단기 상승 탄력"
        elif hl == "하락" and hm == "하락":
            line += " → 중장기 하락압력"
        parts.append(line)

    # 일봉 요약
    dl, dm, ds = _dir_of(d, "장기"), _dir_of(d, "중기"), _dir_of(d, "단기")
    if all([dl, dm, ds]):
        line = f"일봉 장기{dl}·중기{dm}·단기{ds}"
        parts.append(line)

    # 교차 프레임 인사이트 (예: 일봉 약세나 1H 장기 상승 → 단기 반등 가능)
    if dl in ("보합", "하락") and hl == "상승":
        parts.append("일봉 약세 구간이나 1H 장기 상승 전환 → 일봉 기준 단기 반등 가능성")
    if dl == "상승" and hs == "하락":
        parts.append("일봉 상승 추세 중 1H 단기 눌림 → 눌림목 매수 관점 유효")

    # 월봉
    ms = _dir_of(m, "단기")
    if ms:
        parts.append(f"월봉 단기 {ms}")

    return {"lines": parts}


# ======================================================================
# 4. 이평선 분석 (골든크로스 근접도 + N일 뒤 예측)
# ======================================================================
def analyze_ma(daily: pd.DataFrame, lookback_days: int = 60) -> dict:
    """일봉 5/20 이평 위치 + 골든/데드크로스 근접도 및 도달 예상일."""
    df = daily.tail(max(lookback_days, 25))
    close = df["close"]
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    if pd.isna(ma5.iloc[-1]) or pd.isna(ma20.iloc[-1]):
        return {"ok": False}

    px = float(close.iloc[-1])
    m5, m20 = float(ma5.iloc[-1]), float(ma20.iloc[-1])
    pos = []
    pos.append("5일선 위" if px >= m5 else "5일선 아래")
    pos.append("20일선 위" if px >= m20 else "20일선 아래")

    gap_pct = (m5 - m20) / m20 * 100  # 양수면 5일선이 위(정배열 방향)
    # 최근 5일 gap 변화로 수렴/발산 속도 추정
    gap_series = ((ma5 - ma20) / ma20 * 100).dropna()
    if len(gap_series) >= 6:
        recent_slope = (gap_series.iloc[-1] - gap_series.iloc[-6]) / 5  # %/일
    else:
        recent_slope = 0.0

    cross_state = "정배열(5>20)" if gap_pct > 0 else "역배열(5<20)"
    forecast = None
    if gap_pct < 0 and recent_slope > 0.01:
        # 역배열이나 좁혀지는 중 → 골든크로스까지 며칠?
        days = abs(gap_pct) / recent_slope
        if days <= 15:
            forecast = f"약 {days:.0f}일 뒤 골든크로스 예상 (현재 갭 {gap_pct:.1f}%, 수렴속도 {recent_slope:.2f}%/일)"
    elif gap_pct > 0 and recent_slope < -0.01:
        days = abs(gap_pct) / abs(recent_slope)
        if days <= 15:
            forecast = f"약 {days:.0f}일 뒤 데드크로스 우려 (갭 {gap_pct:.1f}%, 발산 {recent_slope:.2f}%/일)"

    # 최근 크로스 발생 여부(쌍바닥/되돌림 힌트)
    diff = (ma5 - ma20).dropna()
    recent_cross = None
    if len(diff) >= 6:
        for i in range(len(diff) - 5, len(diff)):
            if i > 0 and np.sign(diff.iloc[i - 1]) != np.sign(diff.iloc[i]):
                recent_cross = ("골든크로스" if diff.iloc[i] > 0 else "데드크로스",
                                 len(diff) - i)  # (종류, N일 전)
                break

    note = None
    if recent_cross:
        kind, ago = recent_cross
        if kind == "골든크로스" and px < m5:
            note = f"{ago}일 전 골든크로스 후 5일선 하회 → 쌍바닥/눌림 여부 관찰"
        elif kind == "데드크로스" and px > m5:
            note = f"{ago}일 전 데드크로스 후 5일선 회복 → 반등 시도"

    return {"ok": True, "price": round(px), "ma5": round(m5), "ma20": round(m20),
            "position": " / ".join(pos), "gap_pct": round(gap_pct, 2),
            "state": cross_state, "forecast": forecast, "note": note}


# ======================================================================
# 5. 거래량 및 수급 (외인/기관/개인 5일·20일)
# ======================================================================
def analyze_supply_demand(code: str, demo: bool = False) -> dict:
    """pykrx 투자자별 순매수. KRX 회원제 전환으로 실패 가능 → 방어적 폴백."""
    if demo:
        return {"ok": True, "demo": True,
                "foreign_5d": 12000, "foreign_20d": -30000,
                "inst_5d": 8000, "inst_20d": 15000,
                "indiv_ratio": 42.0,
                "summary": "외인 5일 매수·20일 매도(단기 수급주체) / 기관 5일·20일 매수(중기 주체) / 개인비중 42%"}
    try:
        from pykrx import stock
        end = dt.date.today()
        start20 = (end - dt.timedelta(days=32)).strftime("%Y%m%d")
        start5 = (end - dt.timedelta(days=9)).strftime("%Y%m%d")
        e = end.strftime("%Y%m%d")

        def net(df_):
            # 컬럼: 기관합계/기타법인/개인/외국인합계 ... 순매수 거래대금
            cols = df_.columns
            fo = df_["외국인합계"].sum() if "외국인합계" in cols else np.nan
            ins = df_["기관합계"].sum() if "기관합계" in cols else np.nan
            ind = df_["개인"].sum() if "개인" in cols else np.nan
            return fo, ins, ind

        df20 = stock.get_market_trading_value_by_date(start20, e, code)
        df5 = stock.get_market_trading_value_by_date(start5, e, code)
        f20, i20, p20 = net(df20)
        f5, i5, p5 = net(df5)
        total20 = abs(f20) + abs(i20) + abs(p20)
        indiv_ratio = (abs(p20) / total20 * 100) if total20 else np.nan

        def trend(v5, v20):
            s5 = "매수" if v5 > 0 else "매도"
            s20 = "매수" if v20 > 0 else "매도"
            return s5, s20

        f5s, f20s = trend(f5, f20)
        i5s, i20s = trend(i5, i20)
        summary = (f"외인 5일 {f5s}·20일 {f20s} / 기관 5일 {i5s}·20일 {i20s} / "
                   f"개인비중 {indiv_ratio:.0f}%")
        return {"ok": True, "foreign_5d": int(f5), "foreign_20d": int(f20),
                "inst_5d": int(i5), "inst_20d": int(i20),
                "indiv_ratio": round(float(indiv_ratio), 1), "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e),
                "summary": "수급 데이터 조회 실패(KRX 로그인/차단 확인 필요)"}


# ======================================================================
# 6. 공매도 현황
# ======================================================================
def analyze_shorting(code: str, demo: bool = False) -> dict:
    if demo:
        return {"ok": True, "demo": True, "short_5d_trend": "상승",
                "short_ratio_now": 8.4, "short_ratio_5d_ago": 3.1,
                "summary": "공매도 5일 상승 추세, 비중 3.1%→8.4% 급증"}
    try:
        from pykrx import stock
        end = dt.date.today()
        start = (end - dt.timedelta(days=12)).strftime("%Y%m%d")
        e = end.strftime("%Y%m%d")
        df = stock.get_shorting_volume_by_date(start, e, code)
        if df is None or len(df) < 2:
            return {"ok": False, "summary": "공매도 데이터 부족"}
        # 비중 컬럼 탐색
        ratio_col = None
        for c in df.columns:
            if "비중" in c or "비율" in c:
                ratio_col = c
                break
        if ratio_col is None:
            return {"ok": False, "summary": "공매도 비중 컬럼 없음"}
        now = float(df[ratio_col].iloc[-1])
        ago = float(df[ratio_col].iloc[0])
        trend = "상승" if now > ago else ("하락" if now < ago else "보합")
        spike = " 급증" if (now - ago) > 3 else ""
        return {"ok": True, "short_5d_trend": trend,
                "short_ratio_now": round(now, 1), "short_ratio_5d_ago": round(ago, 1),
                "summary": f"공매도 5일 {trend} 추세, 비중 {ago:.1f}%→{now:.1f}%{spike}"}
    except Exception as e:
        return {"ok": False, "error": str(e),
                "summary": "공매도 데이터 조회 실패(KRX 로그인/차단 확인 필요)"}


# ======================================================================
# 2. 다이버전스 근거 요약 (시점 포함)
# ======================================================================
def analyze_divergence_detail(daily: pd.DataFrame, monthly: pd.DataFrame,
                               hourly: pd.DataFrame) -> dict:
    out = {}
    for label, df in [("일봉", daily), ("월봉", monthly), ("1H", hourly)]:
        if df is None or len(df) < 20:
            out[label] = {"ok": False}
            continue
        try:
            divs = detect_divergence(df, stochastic(df))
        except Exception:
            divs = []
        if not divs:
            out[label] = {"ok": True, "found": False}
            continue
        d = divs[-1]
        t0, t1 = d.date_points
        p0, p1 = d.price_points
        k0, k1 = d.stoch_points
        kind = "상승" if d.type == "bullish" else "하락"
        basis = (f"가격 {p0:,.0f}→{p1:,.0f}"
                 f"({'하락' if p1 < p0 else '상승'}) vs "
                 f"%K {k0:.0f}→{k1:.0f}({'상승' if k1 > k0 else '하락'})")
        out[label] = {"ok": True, "found": True, "type": kind,
                      "from_date": str(pd.Timestamp(t0).date()),
                      "to_date": str(pd.Timestamp(t1).date()),
                      "basis": basis,
                      "summary": f"{kind}다이버전스 [{pd.Timestamp(t1).date()}] — {basis}"}
    return out


# ======================================================================
# 7 + 8. 인사이트 + 일봉 종합의견
# ======================================================================
def build_daily_verdict(stoch: dict, ma: dict, supply: dict, short: dict,
                         div: dict) -> dict:
    score = 0
    reasons = []

    # 스토캐 방향 (일봉 단/중기)
    ds = stoch["daily"].get("단기", {})
    dm = stoch["daily"].get("중기", {})
    if ds.get("ok") and ds["direction"] == "상승":
        score += 1; reasons.append("일봉 단기 스토캐 상승")
    if ds.get("ok") and ds["direction"] == "하락":
        score -= 1; reasons.append("일봉 단기 스토캐 하락")

    # 다이버전스
    dd = div.get("일봉", {})
    if dd.get("found"):
        if dd["type"] == "상승":
            score += 2; reasons.append("일봉 상승다이버전스")
        else:
            score -= 2; reasons.append("일봉 하락다이버전스")

    # 이평
    if ma.get("ok"):
        if "정배열" in ma["state"]:
            score += 1; reasons.append("5>20 정배열")
        else:
            score -= 1; reasons.append("5<20 역배열")
        if ma.get("forecast") and "골든크로스" in ma["forecast"]:
            score += 1; reasons.append("골든크로스 임박")

    # 수급
    if supply.get("ok"):
        if supply.get("foreign_5d", 0) > 0:
            score += 1; reasons.append("외인 5일 순매수")
        if supply.get("inst_5d", 0) > 0:
            score += 1; reasons.append("기관 5일 순매수")

    # 공매도
    if short.get("ok") and short.get("short_5d_trend") == "상승":
        score -= 1; reasons.append("공매도 비중 상승")

    if score >= 3:
        verdict = "원만한 상승 예상"
    elif score >= 1:
        verdict = "완만한 상승/반등 시도"
    elif score == 0:
        verdict = "보합 예상"
    elif score >= -2:
        verdict = "약세/조정 주의"
    else:
        verdict = "하락 주의(관망)"

    return {"score": score, "verdict": verdict, "reasons": reasons}


# ======================================================================
# 9. 월봉 종합 (동일 양식, 12개월 기준)
# ======================================================================
def build_monthly_verdict(stoch: dict, div: dict) -> dict:
    reasons = []
    score = 0
    ms = stoch["monthly"].get("단기", {})
    if ms.get("ok"):
        if ms["direction"] == "상승":
            score += 1; reasons.append("월봉 단기 스토캐 상승")
        elif ms["direction"] == "하락":
            score -= 1; reasons.append("월봉 단기 스토캐 하락")
    md = div.get("월봉", {})
    if md.get("found"):
        if md["type"] == "상승":
            score += 2; reasons.append("월봉 상승다이버전스")
        else:
            score -= 2; reasons.append("월봉 하락다이버전스")

    if score >= 2:
        verdict = "월봉 상승 국면"
    elif score >= 1:
        verdict = "월봉 반등 초입 가능"
    elif score == 0:
        verdict = "월봉 보합/방향 미확정"
    else:
        verdict = "월봉 약세 지속"
    return {"score": score, "verdict": verdict, "reasons": reasons,
            "note": "월봉은 12개월 데이터 기준 분석(장기 지표 제약)"}


# ======================================================================
# 종목 1개 전체 분석 조립
# ======================================================================
def analyze_stock(name: str, code: str, daily: pd.DataFrame,
                   monthly: pd.DataFrame, hourly: pd.DataFrame,
                   demo: bool = False) -> dict:
    # 일봉은 60일치로만 분석(요청 9번), 월봉은 12개월치로 제한
    daily60 = daily.tail(80) if len(daily) > 80 else daily          # 지표 워밍업 여유 포함
    monthly12 = monthly.tail(14) if len(monthly) > 14 else monthly  # 12개월 + 여유

    stoch = analyze_stoch_frames(hourly, daily60, monthly12)
    ma = analyze_ma(daily, lookback_days=60)
    supply = analyze_supply_demand(code, demo=demo)
    short = analyze_shorting(code, demo=demo)
    div = analyze_divergence_detail(daily60, monthly12, hourly)

    daily_verdict = build_daily_verdict(stoch, ma, supply, short, div)
    monthly_verdict = build_monthly_verdict(stoch, div)

    last_close = float(daily["close"].iloc[-1])
    prev_close = float(daily["close"].iloc[-2])
    chg = (last_close - prev_close) / prev_close * 100

    return {
        "name": name, "code": code,
        "price": round(last_close), "change_pct": round(chg, 2),
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "divergence": div,          # 2번
        "stoch_frames": stoch,      # 3번
        "ma": ma,                   # 4번
        "supply_demand": supply,    # 5번
        "shorting": short,          # 6번
        "daily_verdict": daily_verdict,     # 7,8번
        "monthly_verdict": monthly_verdict, # 9번
    }
