# -*- coding: utf-8 -*-
"""
mtf_stoch_scanner.py
=====================
스토캐스틱 계산 + 프레임별 다이버전스 탐지 + 멀티프레임 동조화(anchor 프레임
기준 상승/하락 전환 여부).

인터페이스 (portfolio_briefing.py 에서 사용):
  stochastic(df, k_period=14, k_smooth=3, d_period=3) -> DataFrame[%K, %D]
  detect_divergence(df, stoch_df, pivot_order=3) -> list[Divergence]
  analyze_mtf_sync(frames: dict[str, DataFrame], anchor_tf: str,
                    stoch_params: dict[str, tuple]) -> list[SyncResult]
  resample_ohlcv(df, rule) -> DataFrame
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 스토캐스틱
# ----------------------------------------------------------------------
def stochastic(df: pd.DataFrame, k_period: int = 14, k_smooth: int = 3,
               d_period: int = 3) -> pd.DataFrame:
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    raw_k = 100 * (df["close"] - low_min) / (high_max - low_min)
    slow_k = raw_k.rolling(k_smooth).mean()
    slow_d = slow_k.rolling(d_period).mean()
    return pd.DataFrame({"%K": slow_k, "%D": slow_d}, index=df.index)


# ----------------------------------------------------------------------
# 다이버전스
# ----------------------------------------------------------------------
@dataclass
class Divergence:
    type: str                 # 'bullish' / 'bearish'
    date_points: tuple
    price_points: tuple
    stoch_points: tuple


def _pivots(series: pd.Series, order: int = 3, mode: str = "low") -> list[int]:
    vals = series.values
    idx = []
    for i in range(order, len(vals) - order):
        if np.isnan(vals[i]):
            continue
        w = vals[i - order:i + order + 1]
        if mode == "low" and vals[i] == np.nanmin(w):
            idx.append(i)
        elif mode == "high" and vals[i] == np.nanmax(w):
            idx.append(i)
    return idx


def detect_divergence(df: pd.DataFrame, stoch_df: pd.DataFrame,
                       pivot_order: int = 3, max_gap: int = 60) -> list[Divergence]:
    """가격 저점/고점 vs %K 저점/고점 비교로 다이버전스 탐지.
    최근 max_gap 캔들 이내에서 마지막 2개 피벗 쌍을 비교."""
    close = df["close"]
    k = stoch_df["%K"]
    n = len(df)
    if n < pivot_order * 2 + 5:
        return []

    lo_idx = _pivots(close.tail(max_gap), pivot_order, "low")
    hi_idx = _pivots(close.tail(max_gap), pivot_order, "high")
    offset = max(0, n - max_gap)
    out: list[Divergence] = []

    # 상승(bullish) 다이버전스: 가격 저점 하락 + %K 저점 상승
    if len(lo_idx) >= 2:
        i1, i2 = lo_idx[-2] + offset, lo_idx[-1] + offset
        p1, p2 = close.iloc[i1], close.iloc[i2]
        k1, k2 = k.iloc[i1], k.iloc[i2]
        if pd.notna(k1) and pd.notna(k2) and p2 < p1 and k2 > k1:
            out.append(Divergence("bullish", (df.index[i1], df.index[i2]),
                                   (p1, p2), (k1, k2)))

    # 하락(bearish) 다이버전스: 가격 고점 상승 + %K 고점 하락
    if len(hi_idx) >= 2:
        i1, i2 = hi_idx[-2] + offset, hi_idx[-1] + offset
        p1, p2 = close.iloc[i1], close.iloc[i2]
        k1, k2 = k.iloc[i1], k.iloc[i2]
        if pd.notna(k1) and pd.notna(k2) and p2 > p1 and k2 < k1:
            out.append(Divergence("bearish", (df.index[i1], df.index[i2]),
                                   (p1, p2), (k1, k2)))

    return out


# ----------------------------------------------------------------------
# 멀티프레임 동조화 (anchor 프레임의 상승/하락 전환 여부)
# ----------------------------------------------------------------------
@dataclass
class AnchorTurn:
    direction: str        # '상승전환' / '하락전환'
    from_zone: str         # '과매도' / '과매수' / '중립'
    k_value: float


@dataclass
class SyncResult:
    timestamp: object
    anchor_turn: Optional[AnchorTurn]


def analyze_mtf_sync(frames: dict, anchor_tf: str, stoch_params: dict) -> list[SyncResult]:
    """anchor_tf 프레임에서 최근 %K가 과매도/과매수권에서 전환됐는지 판정."""
    df = frames.get(anchor_tf)
    if df is None or len(df) < 10:
        return []
    params = stoch_params.get(anchor_tf, (14, 3, 3))
    sdf = stochastic(df, *params)
    k = sdf["%K"].dropna()
    if len(k) < 3:
        return [SyncResult(df.index[-1] if len(df) else None, None)]

    k_now, k_prev = k.iloc[-1], k.iloc[-2]
    turn = None
    if k_prev <= 20 and k_now > k_prev:
        turn = AnchorTurn("상승전환", "과매도", float(k_now))
    elif k_prev >= 80 and k_now < k_prev:
        turn = AnchorTurn("하락전환", "과매수", float(k_now))

    return [SyncResult(df.index[-1], turn)]


# ----------------------------------------------------------------------
# 리샘플
# ----------------------------------------------------------------------
def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule).agg(agg).dropna(how="any")


# ----------------------------------------------------------------------
# 리포트 포맷 (참고용)
# ----------------------------------------------------------------------
def format_report(title: str, sync_results: list, divs: dict) -> str:
    lines = [f"=== {title} ==="]
    for tf, dv in divs.items():
        if not dv:
            continue
        for d in dv:
            tag = "상승" if d.type == "bullish" else "하락"
            lines.append(f"[{tf}] {tag}다이버전스 | 가격 {d.price_points[0]:.0f}->{d.price_points[1]:.0f} | "
                          f"%K {d.stoch_points[0]:.1f}->{d.stoch_points[1]:.1f}")
    return "\n".join(lines)


if __name__ == "__main__":
    rng = pd.date_range("2026-08-04 09:00", periods=600, freq="1min")
    rnd = np.random.default_rng(7)
    price = 23000 + np.cumsum(rnd.normal(0, 15, len(rng)))
    high = price + rnd.uniform(0, 30, len(rng))
    low = price - rnd.uniform(0, 30, len(rng))
    vol = rnd.integers(100, 1000, len(rng))
    df1min = pd.DataFrame({"open": price, "high": high, "low": low,
                            "close": price, "volume": vol}, index=rng)

    frames = {
        "5min": resample_ohlcv(df1min, "5min"),
        "15min": resample_ohlcv(df1min, "15min"),
        "60min": resample_ohlcv(df1min, "60min"),
    }
    sync_results = analyze_mtf_sync(
        frames, anchor_tf="60min",
        stoch_params={"60min": (24, 5, 5), "15min": (14, 3, 3), "5min": (14, 3, 3)},
    )
    divs = {tf: detect_divergence(frames[tf], stochastic(frames[tf])) for tf in frames}
    print(format_report("DEMO", sync_results, divs))
