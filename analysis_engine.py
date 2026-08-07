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
from signals import to_weekly


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
    # 이 스토캐가 참조한 최신 봉의 날짜 (진단용)
    try:
        last_bar_date = str(pd.Timestamp(df.index[-1]).date())
    except Exception:
        last_bar_date = ""

    # 방향 판정: %K 기울기 + %K vs %D 관계 종합
    #   - slope: %K가 얼마나 움직였는지 (임계값 3.0포인트 - 노이즈 필터)
    #   - k_vs_d: 위/아래 관계 (K > D 면 상승 기조, K < D 면 하락 기조)
    slope = k_now - k_prev
    k_above_d = pd.notna(d_now) and k_now > d_now

    # 방향은 slope과 k_vs_d 둘 다 고려. 하나만 신호면 "보합" 처리.
    if slope > 3.0 and k_above_d:
        direction = "상승"
    elif slope < -3.0 and not k_above_d:
        direction = "하락"
    elif abs(slope) <= 3.0:
        # 완만한 움직임: %K와 %D 관계로만 판단
        if k_above_d and slope > 0:
            direction = "상승"
        elif not k_above_d and slope < 0:
            direction = "하락"
        else:
            direction = "보합"
    else:
        # 강한 slope이지만 K/D 관계와 반대 → 전환 초기, 보합 처리 (오판 방지)
        direction = "보합"

    zone = "과매수" if k_now >= 80 else ("과매도" if k_now <= 20 else "중립")
    cross = None
    if len(k) >= 2 and len(d) >= 2:
        if k.iloc[-2] < d.iloc[-2] and k.iloc[-1] > d.iloc[-1]:
            cross = "골든크로스"
        elif k.iloc[-2] > d.iloc[-2] and k.iloc[-1] < d.iloc[-1]:
            cross = "데드크로스"
    return {"ok": True, "k": round(k_now, 1), "d": round(d_now, 1),
            "direction": direction, "zone": zone, "cross": cross,
            "last_bar": last_bar_date}


def analyze_stoch_frames(hourly: pd.DataFrame, daily: pd.DataFrame,
                          monthly: pd.DataFrame) -> dict:
    """
    1H/일봉/월봉 모든 프레임에서 3구간 스토캐:
      단기(5-3-3) / 중기(10-5-5) / 장기(20-10-10)
    """
    out = {"hourly": {}, "daily": {}, "monthly": {}, "verdict": {}}

    # 세 프레임 모두 동일 파라미터로 통일
    for frame_key, df in [("hourly", hourly), ("daily", daily), ("monthly", monthly)]:
        out[frame_key]["단기"] = _stoch_dir(df, 5, 3, 3)
        out[frame_key]["중기"] = _stoch_dir(df, 10, 5, 5)
        out[frame_key]["장기"] = _stoch_dir(df, 20, 10, 10)

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
# 2-B. 하락 경고 신호 (하락다이버전스 / 데드캣바운스 / 쌍봉) — 최우선 강조
# ======================================================================
def detect_double_top(daily: pd.DataFrame, lookback: int = 60,
                       tol_pct: float = 3.0, max_age: int = 5,
                       near_neckline_pct: float = 3.0) -> dict:
    """쌍봉(더블탑): 최근 구간에서 비슷한 높이의 고점 2개 + 사이 골 + 현재 하락.
    두 고점 차이가 tol_pct 이내이고, 두 번째 고점 이후 하락 중이면 쌍봉.

    유효성 필터:
    - 두번째 고점(peak2)이 최근 max_age 봉 이내여야 유효한 신호
      (오래된 쌍봉은 이미 소진된 신호)
    - "근접" 판정은 현재가가 넥라인의 near_neckline_pct% 이내일 때만
      (예: 3% 이내). 그 이상 떨어져있으면 "관찰" 로 톤다운.
    """
    df = daily.tail(lookback)
    if len(df) < 20:
        return {"found": False}
    high = df["high"].values
    close = df["close"]
    piv = []
    for i in range(3, len(high) - 3):
        if high[i] == max(high[i-3:i+4]):
            piv.append(i)
    if len(piv) < 2:
        return {"found": False}
    i1, i2 = piv[-2], piv[-1]
    n = len(df)

    # 유효기간: 두번째 고점이 최근 max_age 봉 이내
    if (n - 1 - i2) > max_age:
        return {"found": False, "reason": f"peak2 {n-1-i2}봉 전 - 오래됨"}

    h1, h2 = high[i1], high[i2]
    if abs(h1 - h2) / max(h1, h2) * 100 > tol_pct:
        return {"found": False}

    valley = min(high[i1:i2]) if i2 > i1 else h1
    neckline = float(df["low"].iloc[i1:i2+1].min())
    cur = float(close.iloc[-1])
    cur_date = str(df.index[-1].date())

    if not (cur < h2 and (h2 - valley) / h2 * 100 > 2):
        return {"found": False}

    # 넥라인과의 거리 %
    dist_pct = (cur - neckline) / neckline * 100

    if cur < neckline:
        status = "이탈"
        desc_tail = f"넥라인 이탈 ({cur - neckline:+,.0f}원, {dist_pct:+.1f}%) → 하락 확정"
    elif dist_pct <= near_neckline_pct:
        status = "근접"
        desc_tail = f"넥라인 {near_neckline_pct}% 이내 근접({dist_pct:+.1f}%) → 이탈 시 하락 가속"
    else:
        status = "관찰"
        desc_tail = f"넥라인까지 {dist_pct:+.1f}% 여유 → 지지 확인 필요"

    p1_date = str(df.index[i1].date())
    p2_date = str(df.index[i2].date())
    return {"found": True,
            "peak1_date": p1_date, "peak2_date": p2_date,
            "peak_level": round((h1 + h2) / 2),
            "neckline": round(neckline),
            "broke_neckline": cur < neckline,
            "status": status,
            "dist_pct": round(dist_pct, 1),
            "cur_date": cur_date,
            "desc": (f"쌍봉[{p1_date}·{p2_date}] 고점 {h1:,.0f}/{h2:,.0f} · "
                     f"넥라인 {neckline:,.0f} · 현재 {cur:,.0f}({cur_date}) — {desc_tail}")}


def detect_dead_cat_bounce(daily: pd.DataFrame, stoch_daily: dict = None,
                            max_age: int = 5) -> dict:
    """데드캣바운스: 급락 후 단기 반등이나 추세 회복 실패 징후.
    - 최근 20일 내 큰 낙폭(고점 대비 -15%↑)
    - 직후 소폭 반등(저점 대비 반등)
    - 그러나 20일선 아래 + 반등 거래량 미약 → 진성 반등 아닐 가능성.
    - 반등 시작 시점이 max_age 봉 이내여야 유효 (오래된 반등은 이미 소진)"""
    df = daily.tail(30)
    if len(df) < 20:
        return {"found": False}
    close = df["close"]
    ma20 = close.rolling(20).mean()
    recent_high = close.iloc[-20:].max()
    recent_low = close.iloc[-20:].min()
    cur = float(close.iloc[-1])
    low_idx = close.iloc[-20:].idxmin()
    low_date = str(pd.Timestamp(low_idx).date())
    cur_date = str(df.index[-1].date())

    # 저점 이후 경과 봉 수 (반등 진행 기간)
    low_pos = df.index.get_loc(low_idx)
    bounce_bars = len(df) - 1 - low_pos

    drop_pct = (recent_high - recent_low) / recent_high * 100
    bounce_pct = (cur - recent_low) / recent_low * 100 if recent_low else 0
    below_ma20 = pd.notna(ma20.iloc[-1]) and cur < ma20.iloc[-1]

    # 유효기간: 반등이 max_age 봉 이내(너무 오래되면 이미 신호 소진)
    if bounce_bars > max_age * 3:  # 반등이 15봉 넘게 지났으면 무효
        return {"found": False}

    if drop_pct >= 15 and 3 <= bounce_pct <= 15 and below_ma20:
        weak_vol = ""
        try:
            vol = df["volume"]
            after_low = vol.loc[low_idx:]
            before_low = vol.loc[:low_idx]
            if len(after_low) and len(before_low) and after_low.mean() < before_low.mean() * 0.8:
                weak_vol = " · 반등 거래량 미약(신뢰 낮음)"
        except Exception:
            pass
        return {"found": True,
                "drop_pct": round(drop_pct, 1), "bounce_pct": round(bounce_pct, 1),
                "low_date": low_date,
                "cur_date": cur_date,
                "bounce_bars": bounce_bars,
                "desc": (f"저점[{low_date}] {recent_low:,.0f}원 대비 "
                         f"현재[{cur_date}] +{bounce_pct:.0f}% 반등이나 "
                         f"고점 대비 -{drop_pct:.0f}% + 20일선 하회{weak_vol} → 데드캣바운스 경계")}
    return {"found": False}


def build_bear_warnings(daily: pd.DataFrame, div: dict, stoch_daily: dict) -> dict:
    """일봉 기준 하락 경고를 모아 최우선 강조용으로 반환.
    최근 5봉 이내 유효한 신호만 포함."""
    warnings = []

    # 1) 일봉 하락다이버전스 (최우선)
    #    이미 detect_divergence에서 max_age=15 필터로 최근 것만 잡힘
    dd = div.get("일봉", {})
    if dd.get("found") and dd.get("type") == "하락":
        warnings.append({
            "kind": "하락다이버전스",
            "level": "높음",
            "date": dd.get("to_date", ""),
            "desc": f"일봉 하락다이버전스 [{dd.get('to_date','')}] — {dd.get('basis','')}"
        })

    # 2) 쌍봉 (최근 5봉 이내 + 넥라인 근접/이탈만)
    dt_ = detect_double_top(daily)
    if dt_.get("found"):
        status = dt_.get("status", "관찰")
        # "관찰" 상태는 실질적 위험 아님 → 경고에서 제외
        if status in ("이탈", "근접"):
            level = "높음" if status == "이탈" else "주의"
            warnings.append({
                "kind": "쌍봉(더블탑)",
                "level": level,
                "date": dt_.get("peak2_date", ""),
                "desc": dt_["desc"]
            })

    # 3) 데드캣바운스
    dcb = detect_dead_cat_bounce(daily, stoch_daily)
    if dcb.get("found"):
        warnings.append({
            "kind": "데드캣바운스",
            "level": "주의",
            "date": dcb.get("low_date", ""),
            "desc": dcb["desc"]
        })

    # 4) 일봉 단기 스토캐 과매수권 하락전환 (보조 경고)
    #    실제 하락전환 확인: 과매수(K≥80) + 방향 하락 + (데드크로스 or K가 D 아래)
    #    단순 slope으로만 판정하면 노이즈로 오탐지가 자주 발생
    if stoch_daily:
        ds = stoch_daily.get("단기", {})
        if (ds.get("ok") and ds.get("zone") == "과매수"
                and ds.get("direction") == "하락"):
            # 추가 확인: 데드크로스 발생했거나 K가 D 아래로 명확히 내려간 경우만
            cross = ds.get("cross", "")
            k_val = ds.get("k", 0)
            d_val = ds.get("d", 0)
            k_below_d = k_val < d_val
            if cross == "데드크로스" or (k_below_d and (d_val - k_val) >= 2):
                last_bar = ds.get("last_bar", "")
                warnings.append({
                    "kind": "과매수 하락전환",
                    "level": "주의",
                    "date": last_bar,
                    "desc": (f"일봉 단기 스토캐[{last_bar}] 과매수권 하락전환"
                             f"(K{k_val}/D{d_val}"
                             + (f", 데드크로스)" if cross == "데드크로스"
                                else ", K<D)"))
                })

    return {"has_warning": len(warnings) > 0, "warnings": warnings}


# ======================================================================
# 4. 이평선 분석 (5/20/60일선 + 크로스 근접 + 5·20 방향 예측 1~3일)
# ======================================================================
def _ma_slope_forecast(ma_series: pd.Series, horizon: int = 3) -> dict:
    """이평선의 최근 기울기로 N일 후 방향 예측.
    최근 5봉 선형회귀 기울기(원/일)를 구해 1~3일 뒤 값과 방향을 추정."""
    s = ma_series.dropna()
    if len(s) < 6:
        return {"ok": False}
    y = s.iloc[-5:].values
    x = np.arange(len(y))
    # 1차 회귀 (기울기 = 원/일)
    slope, intercept = np.polyfit(x, y, 1)
    now = float(s.iloc[-1])
    slope_pct = slope / now * 100 if now else 0.0  # %/일
    if slope_pct > 0.15:
        direction = "상승"
    elif slope_pct < -0.15:
        direction = "하락"
    else:
        direction = "보합"
    proj = {}
    for d in range(1, horizon + 1):
        val = now + slope * d
        proj[f"{d}일후"] = round(val)
    return {"ok": True, "direction": direction, "slope_pct": round(slope_pct, 2),
            "now": round(now), "proj": proj}


def analyze_ma(daily: pd.DataFrame, lookback_days: int = 60) -> dict:
    """일봉 5/20/60 이평 위치 + 골든/데드크로스 근접도 + 5·20 방향예측(1~3일)."""
    # 60일선 계산엔 최소 60봉 + 방향추정 여유 필요
    df = daily.tail(max(lookback_days + 10, 75))
    close = df["close"]
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    if pd.isna(ma5.iloc[-1]) or pd.isna(ma20.iloc[-1]):
        return {"ok": False}

    px = float(close.iloc[-1])
    m5, m20 = float(ma5.iloc[-1]), float(ma20.iloc[-1])
    m60 = float(ma60.iloc[-1]) if pd.notna(ma60.iloc[-1]) else None

    pos = []
    pos.append("5일선 위" if px >= m5 else "5일선 아래")
    pos.append("20일선 위" if px >= m20 else "20일선 아래")
    if m60 is not None:
        pos.append("60일선 위" if px >= m60 else "60일선 아래")

    # 정/역배열 (60일선 포함 판정)
    if m60 is not None:
        if m5 > m20 > m60:
            cross_state = "정배열(5>20>60)"
        elif m5 < m20 < m60:
            cross_state = "역배열(5<20<60)"
        else:
            cross_state = "혼조"
    else:
        cross_state = "정배열(5>20)" if m5 > m20 else "역배열(5<20)"

    gap_pct = (m5 - m20) / m20 * 100
    gap_series = ((ma5 - ma20) / ma20 * 100).dropna()
    if len(gap_series) >= 6:
        recent_slope = (gap_series.iloc[-1] - gap_series.iloc[-6]) / 5
    else:
        recent_slope = 0.0

    forecast = None
    if gap_pct < 0 and recent_slope > 0.01:
        days = abs(gap_pct) / recent_slope
        if days <= 15:
            forecast = f"약 {days:.0f}일 뒤 5·20 골든크로스 예상 (갭 {gap_pct:.1f}%, 수렴 {recent_slope:.2f}%/일)"
    elif gap_pct > 0 and recent_slope < -0.01:
        days = abs(gap_pct) / abs(recent_slope)
        if days <= 15:
            forecast = f"약 {days:.0f}일 뒤 5·20 데드크로스 우려 (갭 {gap_pct:.1f}%, 발산 {recent_slope:.2f}%/일)"

    # 20·60 크로스 근접도 (60일선 있을 때)
    forecast60 = None
    if m60 is not None:
        gap2060 = (m20 - m60) / m60 * 100
        g2060_series = ((ma20 - ma60) / ma60 * 100).dropna()
        if len(g2060_series) >= 6:
            slope2060 = (g2060_series.iloc[-1] - g2060_series.iloc[-6]) / 5
            if gap2060 < 0 and slope2060 > 0.005:
                d = abs(gap2060) / slope2060
                if d <= 30:
                    forecast60 = f"약 {d:.0f}일 뒤 20·60 골든크로스 (중기 추세전환 신호)"
            elif gap2060 > 0 and slope2060 < -0.005:
                d = abs(gap2060) / abs(slope2060)
                if d <= 30:
                    forecast60 = f"약 {d:.0f}일 뒤 20·60 데드크로스 우려"

    # 최근 크로스 발생 여부
    diff = (ma5 - ma20).dropna()
    recent_cross = None
    if len(diff) >= 6:
        for i in range(len(diff) - 5, len(diff)):
            if i > 0 and np.sign(diff.iloc[i - 1]) != np.sign(diff.iloc[i]):
                recent_cross = ("골든크로스" if diff.iloc[i] > 0 else "데드크로스",
                                 len(diff) - i)
                break
    note = None
    if recent_cross:
        kind, ago = recent_cross
        if kind == "골든크로스" and px < m5:
            note = f"{ago}일 전 골든크로스 후 5일선 하회 → 쌍바닥/눌림 여부 관찰"
        elif kind == "데드크로스" and px > m5:
            note = f"{ago}일 전 데드크로스 후 5일선 회복 → 반등 시도"

    # 5·20일선 방향 예측 (1~3일 후)
    ma5_fc = _ma_slope_forecast(ma5, horizon=3)
    ma20_fc = _ma_slope_forecast(ma20, horizon=3)

    return {"ok": True, "price": round(px),
            "ma5": round(m5), "ma20": round(m20),
            "ma60": round(m60) if m60 is not None else None,
            "position": " / ".join(pos), "gap_pct": round(gap_pct, 2),
            "state": cross_state, "forecast": forecast, "forecast60": forecast60,
            "note": note,
            "ma5_forecast": ma5_fc, "ma20_forecast": ma20_fc}


# ======================================================================
# 4-B. 눌림목 매수 분석 (5/10/20일선 지지터치 + 손절가 + 기술적 의견)
# ======================================================================
def analyze_pullback(daily: pd.DataFrame, stoch_daily: dict = None,
                      vol_over: bool = None, alignment: str = None,
                      tol_pct: float = 0.8) -> dict:
    """
    눌림목 매수기법 판정.
    - 5일선 눌림 : 종가가 5일선 위 + 당일 저가가 5일선을 tol% 이내로 터치 → 1차 매수
    - 10일선 눌림: 종가가 10일선 위(5일선은 이탈했을 수 있음) + 저가 10일선 터치 → 2차 매수
    - 20일선 눌림: 종가가 20일선 위 + 저가 20일선 터치 → 조정 후 매수(추세 유지)
    각 신호에 손절가(해당 지지선 이탈가 -1~2%)와 신뢰도(정배열·거래량·스토캐 위치) 부여.
    """
    df = daily.tail(80)
    close = df["close"]
    low = df["low"]
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()

    if len(df) < 20 or pd.isna(ma20.iloc[-1]):
        return {"ok": False}

    px = float(close.iloc[-1])
    lo = float(low.iloc[-1])
    m5 = float(ma5.iloc[-1])
    m10 = float(ma10.iloc[-1]) if pd.notna(ma10.iloc[-1]) else None
    m20 = float(ma20.iloc[-1])

    signals = []

    def touched(line):
        # 당일 저가가 이평선을 tol% 이내로 눌렀는지 (위에서 터치)
        return lo <= line * (1 + tol_pct / 100)

    # --- 5일선 눌림 ---
    if px >= m5 and touched(m5):
        stop = round(m5 * 0.98)  # 5일선 -2% 이탈 시 손절
        signals.append({
            "line": "5일선", "level": round(m5), "stop": stop,
            "type": "1차 눌림목(단기)",
            "desc": f"종가 5일선({m5:,.0f}) 위 + 저가가 5일선 지지터치 → 단기 눌림목 매수 관점"
        })

    # --- 10일선 눌림 (5일선 이탈했으나 10일선 지지) ---
    if m10 is not None and px >= m10 and touched(m10) and px < m5:
        stop = round(m10 * 0.98)
        signals.append({
            "line": "10일선", "level": round(m10), "stop": stop,
            "type": "2차 눌림목(5일선 이탈·10일선 지지)",
            "desc": f"5일선({m5:,.0f}) 이탈했으나 10일선({m10:,.0f}) 지지 → 2차 매수 관점"
        })

    # --- 20일선 눌림 (중기 추세 지지) ---
    if px >= m20 and touched(m20):
        stop = round(m20 * 0.97)  # 20일선 -3% 이탈 시 손절
        signals.append({
            "line": "20일선", "level": round(m20), "stop": stop,
            "type": "중기 눌림목(추세 유지)",
            "desc": f"종가 20일선({m20:,.0f}) 위 + 저가 20일선 지지터치 → 중기 추세 눌림 매수"
        })

    # --- 신뢰도/기술적 의견 ---
    confidence = "중"
    opinion_bits = []
    if alignment == "정배열":
        confidence = "상"
        opinion_bits.append("정배열 상태라 눌림목 신뢰도 높음")
    elif alignment == "역배열":
        confidence = "하"
        opinion_bits.append("역배열이라 눌림목보다 추세 반등 확인 필요")

    # 거래량: 눌림목은 거래량 감소(매물 소화)가 이상적
    if vol_over is True:
        opinion_bits.append("단, 당일 거래량이 5일선 위 → 눌림보다 이탈/변동성 주의")
    elif vol_over is False:
        opinion_bits.append("거래량 축소 동반(건전한 눌림 패턴)")

    # 스토캐: 과매도권이면 반등 탄력
    if stoch_daily:
        ds = stoch_daily.get("단기", {})
        if ds.get("ok"):
            if ds.get("zone") == "과매도":
                opinion_bits.append(f"일봉 단기 스토캐 과매도(K{ds.get('k')}) → 반등 탄력 기대")
            elif ds.get("zone") == "과매수":
                opinion_bits.append(f"일봉 단기 스토캐 과매수(K{ds.get('k')}) → 추가 눌림 여지")

    if not signals:
        # 눌림 신호 없을 때: 현재 위치 기반 코멘트
        if px < m20:
            status = f"20일선({m20:,.0f}) 하회 — 눌림목 아님, 지지 회복 확인 후 접근"
        elif px < m5:
            status = f"5일선({m5:,.0f}) 하회·20일선 위 — 5일선 회복 or 20일선 터치 대기"
        else:
            status = "이평선 위 안착 — 눌림(터치) 대기 구간"
        return {"ok": True, "has_signal": False, "status": status,
                "confidence": confidence,
                "opinion": " / ".join(opinion_bits) if opinion_bits else "",
                "price": round(px)}

    return {"ok": True, "has_signal": True, "signals": signals,
            "confidence": confidence,
            "opinion": " / ".join(opinion_bits) if opinion_bits else "",
            "price": round(px)}
def analyze_supply_demand(code: str, demo: bool = False) -> dict:
    """pykrx 투자자별 순매수(5일/20일/60일) + 각 주체 매매비중.
    KRX 회원제 전환으로 실패 가능 → 방어적 폴백."""
    if demo:
        return {"ok": True, "demo": True,
                "foreign": {"d5": 12000, "d20": -30000, "d60": 45000},
                "inst": {"d5": 8000, "d20": 15000, "d60": 22000},
                "indiv": {"d5": -20000, "d20": 15000, "d60": -67000},
                "ratio": {"foreign": 35.0, "inst": 23.0, "indiv": 42.0},
                "main_5d": "외인", "main_20d": "기관",
                "summary": ("외인 5일 매수·20일 매도·60일 매수 / 기관 5일·20일·60일 매수 / "
                            "개인비중 42%(외35·기23) → 단기 외인 주도")}
    try:
        from pykrx import stock
        end = dt.date.today()
        e = end.strftime("%Y%m%d")
        s90 = (end - dt.timedelta(days=90)).strftime("%Y%m%d")
        df_full = stock.get_market_trading_value_by_date(s90, e, code)
        if df_full is None or len(df_full) == 0:
            return {"ok": False, "err": "pykrx 반환 없음"}
        df_full = df_full.sort_index()

        # 진단: 실제 pykrx가 반환한 컬럼 로그
        print(f"[수급진단] {code} pykrx 컬럼: {list(df_full.columns)}")
        print(f"[수급진단] {code} 최근 5행:\n{df_full.tail(5)}")

        # 기관 그룹 세분화 컬럼 (기관합계가 없을 때 합산 대상)
        INSTITUTIONAL_PARTS = ["금융투자", "보험", "투신", "사모", "은행",
                                "기타금융", "연기금", "국가"]

        def net(df_):
            """실제 컬럼명 방어적 매칭.
            pykrx 반환 컬럼 예: 개인, 외국인, 기관합계, 기타법인, 전체
            또는 세분화: 금융투자, 보험, 투신, 사모, 은행, 기타금융, 연기금 등
            """
            cols = list(df_.columns)

            # 외국인 (외국인합계 우선, 없으면 외국인 + 기타외국인)
            if "외국인합계" in cols:
                fo = df_["외국인합계"].sum()
            elif "외국인" in cols:
                fo = df_["외국인"].sum()
                if "기타외국인" in cols:
                    fo += df_["기타외국인"].sum()
            else:
                fo = 0.0

            # 기관 (기관합계 우선, 없으면 세분화 그룹 합산)
            if "기관합계" in cols:
                ins = df_["기관합계"].sum()
            else:
                ins = 0.0
                found_any = False
                for part in INSTITUTIONAL_PARTS:
                    if part in cols:
                        ins += df_[part].sum()
                        found_any = True
                if not found_any:
                    ins = 0.0

            # 개인
            ind = df_["개인"].sum() if "개인" in cols else 0.0

            return float(fo), float(ins), float(ind)

        # 최근 N 거래일 정확히 slice
        df5 = df_full.tail(5)
        df20 = df_full.tail(20)
        df60 = df_full.tail(60)
        f5, i5, p5 = net(df5)
        f20, i20, p20 = net(df20)
        f60, i60, p60 = net(df60)

        # 진단: 계산된 값
        print(f"[수급진단] {code} 5일: 외인={f5/1e8:.0f}억 기관={i5/1e8:.0f}억 개인={p5/1e8:.0f}억")
        print(f"[수급진단] {code} 20일: 외인={f20/1e8:.0f}억 기관={i20/1e8:.0f}억 개인={p20/1e8:.0f}억")

        n5, n20, n60 = len(df5), len(df20), len(df60)

        # 20일 기준 매매비중 (절대값 기준 점유율)
        total20 = abs(f20) + abs(i20) + abs(p20)
        r_f = (abs(f20) / total20 * 100) if total20 else float("nan")
        r_i = (abs(i20) / total20 * 100) if total20 else float("nan")
        r_p = (abs(p20) / total20 * 100) if total20 else float("nan")

        def side(v):
            return "매수" if v > 0 else "매도"

        # 단기(5일)·중기(20일) 주도 수급주체 (순매수 절대값 최대)
        main_5d = max([("외인", abs(f5)), ("기관", abs(i5)), ("개인", abs(p5))],
                      key=lambda x: x[1])[0]
        main_20d = max([("외인", abs(f20)), ("기관", abs(i20)), ("개인", abs(p20))],
                       key=lambda x: x[1])[0]

        summary = (f"외인 5일 {side(f5)}·20일 {side(f20)}·60일 {side(f60)} / "
                   f"기관 5일 {side(i5)}·20일 {side(i20)}·60일 {side(i60)} / "
                   f"개인비중 {r_p:.0f}%(외{r_f:.0f}·기{r_i:.0f}) → 단기 {main_5d} 주도")

        return {"ok": True,
                "foreign": {"d5": int(f5), "d20": int(f20), "d60": int(f60)},
                "inst": {"d5": int(i5), "d20": int(i20), "d60": int(i60)},
                "indiv": {"d5": int(p5), "d20": int(p20), "d60": int(p60)},
                "ratio": {"foreign": round(r_f, 1), "inst": round(r_i, 1), "indiv": round(r_p, 1)},
                "main_5d": main_5d, "main_20d": main_20d,
                "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e),
                "summary": "수급 데이터 조회 실패(KRX 로그인/차단 확인 필요)"}


# ======================================================================
# 6. 공매도 현황
# ======================================================================
def analyze_shorting(code: str, demo: bool = False) -> dict:
    """공매도 비중을 5일/20일/60일 구간으로 각각 추세 판정.
    '추세'는 각 구간의 첫날 대비 마지막날(최근) 비중 변화로 정의."""
    if demo:
        return {"ok": True, "demo": True,
                "now": 7.5,
                "d5": {"ago": 9.6, "trend": "하락"},
                "d20": {"ago": 5.2, "trend": "상승"},
                "d60": {"ago": 4.1, "trend": "상승"},
                "avg20": 6.8,
                "summary": "공매도 비중 현재 7.5% | 5일 하락(9.6→7.5) · 20일 상승(5.2→7.5) · 60일 상승(4.1→7.5)"}
    try:
        from pykrx import stock
        end = dt.date.today()
        e = end.strftime("%Y%m%d")
        s60 = (end - dt.timedelta(days=90)).strftime("%Y%m%d")
        df = stock.get_shorting_volume_by_date(s60, e, code)
        if df is None or len(df) < 2:
            return {"ok": False, "summary": "공매도 데이터 부족"}
        ratio_col = None
        for c in df.columns:
            if "비중" in c or "비율" in c:
                ratio_col = c
                break
        if ratio_col is None:
            return {"ok": False, "summary": "공매도 비중 컬럼 없음"}

        ser = df[ratio_col].dropna()
        if len(ser) < 2:
            return {"ok": False, "summary": "공매도 비중 데이터 부족"}
        now = float(ser.iloc[-1])

        def window_trend(n):
            """최근 n 거래일 구간의 첫날 대비 현재 비중 변화."""
            if len(ser) <= 1:
                return None
            past = ser.iloc[-min(n, len(ser))]
            ago = float(past)
            if now > ago + 0.3:
                t = "상승"
            elif now < ago - 0.3:
                t = "하락"
            else:
                t = "보합"
            spike = " 급증" if (now - ago) > 3 else ""
            return {"ago": round(ago, 1), "trend": t + spike}

        d5 = window_trend(5)
        d20 = window_trend(20)
        d60 = window_trend(60)
        avg20 = float(ser.iloc[-min(20, len(ser)):].mean())

        def fmt(label, w):
            if not w:
                return f"{label} N/A"
            return f"{label} {w['trend']}({w['ago']}→{now:.1f})"

        summary = (f"공매도 비중 현재 {now:.1f}% | "
                   f"{fmt('5일', d5)} · {fmt('20일', d20)} · {fmt('60일', d60)}")

        return {"ok": True, "now": round(now, 1),
                "d5": d5, "d20": d20, "d60": d60,
                "avg20": round(avg20, 1), "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e),
                "summary": "공매도 데이터 조회 실패(KRX 로그인/차단 확인 필요)"}


# ======================================================================
# 2. 다이버전스 근거 요약 (시점 포함)
# ======================================================================
def analyze_divergence_detail(daily: pd.DataFrame, monthly: pd.DataFrame,
                               hourly: pd.DataFrame) -> dict:
    out = {}
    # 프레임별 유효기간(봉 단위): 오래된 다이버전스는 이미 소진됐다고 보고 필터
    #  일봉 15봉 ≈ 3주, 월봉 3봉 ≈ 3개월, 1H 40봉 ≈ 5거래일
    max_age_by = {"일봉": 15, "월봉": 3, "1H": 40}
    for label, df in [("일봉", daily), ("월봉", monthly), ("1H", hourly)]:
        if df is None or len(df) < 20:
            out[label] = {"ok": False}
            continue
        try:
            divs = detect_divergence(df, stochastic(df),
                                     max_age=max_age_by.get(label, 15))
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
                         div: dict, pullback: dict = None, bear: dict = None) -> dict:
    score = 0
    reasons = []
    breakdown = []   # [{"item","pts","note"}] — 실제 채점 내역

    def add(item, pts, note):
        nonlocal score
        score += pts
        breakdown.append({"item": item, "pts": pts, "note": note})
        if pts != 0:
            reasons.append(note)

    # 다이버전스 (±2)
    dd = div.get("일봉", {})
    if dd.get("found"):
        if dd["type"] == "상승":
            add("일봉 다이버전스", +2, f"상승다이버전스[{dd.get('to_date','')}]")
        else:
            add("일봉 다이버전스", -2, f"하락다이버전스[{dd.get('to_date','')}]")
    else:
        add("일봉 다이버전스", 0, "다이버전스 없음")

    # 일봉 단기 스토캐 (±1)
    ds = stoch["daily"].get("단기", {})
    if ds.get("ok") and ds["direction"] == "상승":
        add("일봉 단기 스토캐", +1, f"단기 스토캐 상승(K{ds.get('k','')})")
    elif ds.get("ok") and ds["direction"] == "하락":
        add("일봉 단기 스토캐", -1, f"단기 스토캐 하락(K{ds.get('k','')})")
    else:
        add("일봉 단기 스토캐", 0, "단기 스토캐 보합")

    # 이평 배열 (±1)
    if ma.get("ok"):
        if "정배열" in ma["state"]:
            add("이평 배열", +1, f"{ma['state']}")
        elif "역배열" in ma["state"]:
            add("이평 배열", -1, f"{ma['state']}")
        else:
            add("이평 배열", 0, f"{ma['state']}")
        # 골든크로스 임박 (+1)
        if ma.get("forecast") and "골든크로스" in ma["forecast"]:
            add("5·20 골든크로스 임박", +1, ma["forecast"])
        elif ma.get("forecast") and "데드크로스" in ma["forecast"]:
            add("5·20 데드크로스 우려", 0, ma["forecast"])  # 감점은 배열에서 이미 반영

    # 수급 (외인/기관 각 +1)
    if supply.get("ok"):
        f5 = supply.get("foreign", {}).get("d5", 0)
        i5 = supply.get("inst", {}).get("d5", 0)
        if f5 > 0:
            add("외인 5일 수급", +1, "외인 5일 순매수")
        else:
            add("외인 5일 수급", 0, "외인 5일 순매도")
        if i5 > 0:
            add("기관 5일 수급", +1, "기관 5일 순매수")
        else:
            add("기관 5일 수급", 0, "기관 5일 순매도")

    # 공매도 5일 추세 (±1)
    if short.get("ok"):
        d5t = short.get("d5", {}).get("trend", "")
        if "상승" in d5t:
            add("공매도 5일 추세", -1, f"공매도 5일 상승({d5t})")
        elif "하락" in d5t:
            add("공매도 5일 추세", +1, f"공매도 5일 하락({d5t})")
        else:
            add("공매도 5일 추세", 0, "공매도 5일 보합")

    # 눌림목 매수 신호 (+1) — 정배열 상태의 지지선 터치는 매수 기회
    if pullback and pullback.get("ok") and pullback.get("has_signal"):
        sigs = pullback.get("signals", [])
        best = sigs[0] if sigs else None
        if best:
            add("눌림목 매수신호", +1, f"{best['line']} 지지터치({best['type']})")
    elif pullback and pullback.get("ok"):
        add("눌림목 매수신호", 0, "눌림 신호 없음(터치 대기)")

    # 하락 경고 추가 감점 (쌍봉/데드캣바운스 — 다이버전스는 위에서 이미 반영)
    has_strong_bear = False
    if bear and bear.get("has_warning"):
        for w in bear["warnings"]:
            if w["kind"] == "쌍봉(더블탑)":
                pts = -2 if w["level"] == "높음" else -1
                add("쌍봉(더블탑)", pts, w["desc"][:40])
                if w["level"] == "높음":
                    has_strong_bear = True
            elif w["kind"] == "데드캣바운스":
                add("데드캣바운스", -1, w["desc"][:40])
            # 하락다이버전스는 '일봉 다이버전스'에서 이미 -2 반영됨(중복 방지)
            if w["kind"] == "하락다이버전스" and w["level"] == "높음":
                has_strong_bear = True

    if score >= 4:
        verdict = "적극 매수 관점"
    elif score >= 2:
        verdict = "원만한 상승 예상"
    elif score >= 1:
        verdict = "완만한 상승/반등 시도"
    elif score == 0:
        verdict = "보합 예상"
    elif score >= -2:
        verdict = "약세/조정 주의"
    else:
        verdict = "하락 주의(관망)"

    # 강한 하락신호(넥라인 이탈 쌍봉 or 하락다이버전스)면 판정을 강제 하향
    if has_strong_bear and score > -2:
        verdict = "⚠️ 하락신호 우세 — 매도/관망"

    return {"score": score, "verdict": verdict, "reasons": reasons,
            "breakdown": breakdown, "strong_bear": has_strong_bear}


# ======================================================================
# 9. 월봉 종합 (확보 가능한 데이터 범위 내에서 최대한 판단)
# ======================================================================
def build_monthly_verdict(stoch: dict, div: dict, monthly: pd.DataFrame) -> dict:
    reasons = []
    score = 0
    breakdown = []
    n_months = len(monthly) if monthly is not None else 0

    def add(item, pts, note):
        nonlocal score
        score += pts
        breakdown.append({"item": item, "pts": pts, "note": note})
        if pts != 0:
            reasons.append(note)

    # 월봉 다이버전스 (±2) — 최우선
    md = div.get("월봉", {})
    if md.get("found"):
        if md["type"] == "상승":
            add("월봉 다이버전스", +2, f"상승다이버전스[{md.get('to_date','')}]")
        else:
            add("월봉 다이버전스", -2, f"하락다이버전스[{md.get('to_date','')}]")
    else:
        add("월봉 다이버전스", 0, "다이버전스 없음")

    # 월봉 스토캐 장/중/단 (확보된 프레임만, 각 ±1)
    ms = stoch.get("monthly", {})
    for key, label in [("장기", "월봉 장기 스토캐"), ("중기", "월봉 중기 스토캐"),
                        ("단기", "월봉 단기 스토캐")]:
        node = ms.get(key, {})
        if not node.get("ok"):
            add(label, 0, f"{key} 데이터 부족")
            continue
        d = node["direction"]
        zone = node.get("zone", "")
        k = node.get("k", "")
        # 과매도권에서 상승전환은 강한 호재(가중 +1 유지, 문구 강조)
        if d == "상승" and zone == "과매도":
            add(label, +1, f"{key} 과매도권 상승전환(K{k})")
        elif d == "상승":
            add(label, +1, f"{key} 상승(K{k})")
        elif d == "하락" and zone == "과매수":
            add(label, -1, f"{key} 과매수권 하락전환(K{k})")
        elif d == "하락":
            add(label, -1, f"{key} 하락(K{k})")
        else:
            add(label, 0, f"{key} 보합(K{k})")

    # 월봉 MA 배열 (60개월 확보 시에만, ±1)
    if monthly is not None and n_months >= 20:
        close = monthly["close"]
        m5 = close.rolling(5).mean().iloc[-1]
        m20 = close.rolling(20).mean().iloc[-1] if n_months >= 20 else None
        if pd.notna(m5) and m20 is not None and pd.notna(m20):
            if m5 > m20:
                add("월봉 MA 배열", +1, "월 5>20 (중장기 정배열)")
            else:
                add("월봉 MA 배열", -1, "월 5<20 (중장기 역배열)")

    # 판정 (확보 데이터가 적으면 판정 신뢰도 낮음을 문구로)
    if score >= 3:
        verdict = "월봉 상승 국면"
    elif score >= 1:
        verdict = "월봉 반등 초입 가능"
    elif score == 0:
        verdict = "월봉 보합/방향 미확정"
    elif score >= -2:
        verdict = "월봉 약세 조정"
    else:
        verdict = "월봉 약세 지속"

    # 데이터 범위 안내
    if n_months >= 55:
        cover = f"확보 {n_months}개월(약 5년) — 장기 지표 포함 판단"
    elif n_months >= 24:
        cover = f"확보 {n_months}개월(약 {n_months//12}년) — 중기까지 판단"
    elif n_months >= 6:
        cover = f"확보 {n_months}개월 — 단기 위주 제한적 판단(상장 이력 짧음)"
    else:
        cover = f"확보 {n_months}개월 — 데이터 부족, 참고용"

    return {"score": score, "verdict": verdict, "reasons": reasons,
            "breakdown": breakdown, "n_months": n_months,
            "note": f"월봉 판단 근거: {cover}"}


# ======================================================================
# 종목 1개 전체 분석 조립
# ======================================================================
def analyze_stock(name: str, code: str, daily: pd.DataFrame,
                   monthly: pd.DataFrame, hourly: pd.DataFrame,
                   demo: bool = False, is_index: bool = False) -> dict:
    # 일봉은 60일치로 분석(스토캐 워밍업 여유 포함), 월봉은 5년(60개월)까지 사용
    daily60 = daily.tail(80) if len(daily) > 80 else daily          # 지표 워밍업 여유 포함
    # 월봉: 5년 소스 반영 → 다이버전스/스토캐는 전체(최대 60개월) 사용
    monthly_full = monthly.tail(62) if len(monthly) > 62 else monthly

    stoch = analyze_stoch_frames(hourly, daily60, monthly_full)
    ma = analyze_ma(daily, lookback_days=60)
    if is_index:
        # 지수: 종목별 수급/공매도는 해당 없음 → 시장 통계로 대체
        from data_feed import fetch_market_breadth, fetch_market_investor_flow
        supply = {"ok": False, "summary": "지수는 수급 데이터 해당 없음",
                  "market_breadth": (fetch_market_breadth(code) if not demo else
                                     {"ok": True, "market": code, "total": 950,
                                      "up": 520, "down": 380, "flat": 50,
                                      "upper_limit": 3, "lower_limit": 1, "up_ratio": 54.7}),
                  "investor_flow": (fetch_market_investor_flow(code) if not demo else
                                    {"ok": True, "market": code,
                                     "foreign": 82_000_000_000,
                                     "inst": -45_000_000_000,
                                     "indiv": -37_000_000_000})}
        short = {"ok": False, "summary": "지수는 공매도 데이터 해당 없음"}
    else:
        supply = analyze_supply_demand(code, demo=demo)
        short = analyze_shorting(code, demo=demo)
    div = analyze_divergence_detail(daily60, monthly_full, hourly)

    # 하락 경고 (일봉 하락다이버전스/쌍봉/데드캣바운스) — 최우선 강조
    bear = build_bear_warnings(daily60, div, stoch.get("daily"))

    # 거래량 5일선 돌파 여부 (눌림 판정 보조)
    vol_over = None
    try:
        vser = daily["volume"]
        vma5 = vser.rolling(5).mean()
        vol_over = bool(vser.iloc[-1] > vma5.iloc[-1])
    except Exception:
        vol_over = None

    # 눌림목 매수 분석
    pullback = analyze_pullback(
        daily, stoch_daily=stoch.get("daily"),
        vol_over=vol_over,
        alignment=("정배열" if ma.get("ok") and "정배열" in ma.get("state", "")
                   else ("역배열" if ma.get("ok") and "역배열" in ma.get("state", "") else None)),
    )

    daily_verdict = build_daily_verdict(stoch, ma, supply, short, div, pullback, bear)
    monthly_verdict = build_monthly_verdict(stoch, div, monthly_full)

    # 3프레임 차트 (matplotlib PNG base64)
    charts = {}
    try:
        from chart_maker import make_three_frame_charts
        charts = make_three_frame_charts(hourly, daily60, monthly_full)
    except Exception as e:
        charts = {"error": str(e)}

    last_close = float(daily["close"].iloc[-1])
    prev_close = float(daily["close"].iloc[-2])
    chg = (last_close - prev_close) / prev_close * 100

    # 데이터 신선도 (각 프레임의 마지막 봉 날짜)
    def _last(df):
        try:
            return str(pd.Timestamp(df.index[-1]).date())
        except Exception:
            return ""
    data_freshness = {
        "daily": _last(daily),
        "weekly": _last(to_weekly(daily)) if len(daily) > 5 else "",
        "monthly": _last(monthly_full),
        "hourly": (str(pd.Timestamp(hourly.index[-1])) if hourly is not None and len(hourly) else ""),
    }

    return {
        "name": name, "code": code,
        "price": (round(last_close, 2) if is_index else round(last_close)),
        "is_index": is_index,
        "change_pct": round(chg, 2),
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "data_freshness": data_freshness,   # 각 프레임 마지막 봉 날짜
        "bear_warnings": bear,      # 2-B 하락 경고 (최우선)
        "divergence": div,          # 2번
        "stoch_frames": stoch,      # 3번
        "ma": ma,                   # 4번
        "pullback": pullback,       # 4-B 눌림목 매수
        "supply_demand": supply,    # 5번
        "shorting": short,          # 6번
        "daily_verdict": daily_verdict,     # 7,8번
        "monthly_verdict": monthly_verdict, # 9번
        "charts": charts,           # 차트 (JSON엔 base64가 커서 제외됨, HTML에만 사용)
    }
