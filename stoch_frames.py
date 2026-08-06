# -*- coding: utf-8 -*-
"""
stoch_frames.py
===============
프레임별(1시간봉/일봉/월봉) 스토캐스틱 상태 + 다이버전스 분석.
메인 지표 3종(스토캐 다이버전스 · 거래량 · 5/20일선) 중 스토캐 담당.

각 프레임에 대해:
  - %K, %D, 구간(과매수/과매도/중립)
  - 전환(turn): 최근 봉에서 상승/하락 전환 여부 + 과매도/과매수권 여부
  - 다이버전스: 가격 vs %K 저점/고점 비교로 상승/하락 다이버전스 탐지
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class StochFrame:
    frame: str            # '1시간' / '일봉' / '월봉'
    k: float
    d: float
    zone: str             # '과매수' / '과매도' / '중립'
    turn: str             # '상승전환' / '하락전환' / '없음'
    turn_from_oversold: bool
    turn_from_overbought: bool
    divergence: str       # '상승' / '하락' / '없음'
    ok: bool = True


def _stoch(df, k_period=14, k_smooth=3, d_period=3):
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    raw_k = 100 * (df["close"] - low_min) / (high_max - low_min)
    slow_k = raw_k.rolling(k_smooth).mean()
    slow_d = slow_k.rolling(d_period).mean()
    return slow_k, slow_d


def _find_pivots(series, order=3, mode="low"):
    idx, vals = [], series.values
    for i in range(order, len(vals) - order):
        w = vals[i - order:i + order + 1]
        if np.isnan(vals[i]):
            continue
        if mode == "low" and vals[i] == np.nanmin(w):
            idx.append(i)
        elif mode == "high" and vals[i] == np.nanmax(w):
            idx.append(i)
    return idx


def _divergence(df, k, pivot_order=3, max_gap=40):
    """가장 최근의 상승/하락 다이버전스 하나를 반환 ('상승'/'하락'/'없음')."""
    low_piv = _find_pivots(df["low"], pivot_order, "low")
    high_piv = _find_pivots(df["high"], pivot_order, "high")
    latest = None
    latest_i = -1
    # 상승 다이버전스: 가격 저점 낮아지는데 %K 저점 높아짐
    for a, b in zip(low_piv, low_piv[1:]):
        if b - a > max_gap:
            continue
        if np.isnan(k.iloc[a]) or np.isnan(k.iloc[b]):
            continue
        if df["low"].iloc[b] < df["low"].iloc[a] and k.iloc[b] > k.iloc[a]:
            if b > latest_i:
                latest, latest_i = "상승", b
    # 하락 다이버전스: 가격 고점 높아지는데 %K 고점 낮아짐
    for a, b in zip(high_piv, high_piv[1:]):
        if b - a > max_gap:
            continue
        if np.isnan(k.iloc[a]) or np.isnan(k.iloc[b]):
            continue
        if df["high"].iloc[b] > df["high"].iloc[a] and k.iloc[b] < k.iloc[a]:
            if b > latest_i:
                latest, latest_i = "하락", b
    return latest or "없음"


def analyze_frame(df: pd.DataFrame, frame_name: str,
                  k_period=14, oversold=20, overbought=80) -> StochFrame:
    if df is None or len(df) < k_period + 6:
        return StochFrame(frame_name, 0, 0, "중립", "없음", False, False, "없음", ok=False)
    k, d = _stoch(df, k_period)
    k_now, d_now = k.iloc[-1], d.iloc[-1]
    if np.isnan(k_now):
        return StochFrame(frame_name, 0, 0, "중립", "없음", False, False, "없음", ok=False)

    zone = "과매수" if k_now > overbought else ("과매도" if k_now < oversold else "중립")

    # 전환: 최근 3봉 방향
    turn, from_os, from_ob = "없음", False, False
    if len(k.dropna()) >= 3:
        k1, k2, k3 = k.iloc[-3], k.iloc[-2], k.iloc[-1]
        if k2 <= k1 and k3 > k2:
            turn = "상승전환"
            from_os = k2 < oversold
        elif k2 >= k1 and k3 < k2:
            turn = "하락전환"
            from_ob = k2 > overbought

    div = _divergence(df, k)
    return StochFrame(frame_name, round(float(k_now), 1), round(float(d_now), 1),
                      zone, turn, from_os, from_ob, div, ok=True)


def stoch_text(sf: StochFrame) -> str:
    if not sf.ok:
        return f"{sf.frame} 스토캐 데이터부족"
    bits = [f"%K {sf.k:.0f}", sf.zone]
    if sf.turn != "없음":
        tag = sf.turn
        if sf.turn_from_oversold:
            tag += "(과매도권)"
        if sf.turn_from_overbought:
            tag += "(과매수권)"
        bits.append(tag)
    if sf.divergence != "없음":
        bits.append(f"{sf.divergence}다이버전스")
    return f"{sf.frame} " + ", ".join(bits)


if __name__ == "__main__":
    rng = pd.date_range("2020-01-01", periods=400, freq="B")
    r = np.random.default_rng(5)
    close = 20000 + np.cumsum(r.normal(0, 200, 400))
    df = pd.DataFrame({"open": close, "high": close + r.uniform(0, 150, 400),
                       "low": close - r.uniform(0, 150, 400), "close": close,
                       "volume": r.integers(1e5, 1e6, 400)}, index=rng)
    for f in ["일봉", "월봉"]:
        sf = analyze_frame(df, f)
        print(stoch_text(sf))
