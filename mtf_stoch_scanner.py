# -*- coding: utf-8 -*-
"""
mtf_stoch_scanner.py
=====================
멀티 타임프레임 스토캐스틱 동조화(sync) + 다이버전스 탐지 스캐너

목적
----
1시간봉(상위) 장기 스토캐스틱이 상승 전환할 때, 하위 프레임(15분/5분)에서는
몇 번째 사이클에서 함께 전환되는지를 정량적으로 잡아내고,
가격-오실레이터 다이버전스(상승/하락)를 자동으로 표시한다.

기존 자동화 스택과의 연결점
----------------------------
- 일봉/스윙용: pykrx, FinanceDataReader 로 받은 OHLCV DataFrame을 그대로 입력
- 분봉(장중 단타)용: 증권사 API(키움/이베스트 등) 혹은 네이버금융 크롤링으로
  1분봉을 받아온 뒤 이 스크립트의 resample 함수로 5분/15분/60분을 만들어 사용
  (pykrx/yfinance는 국내 분봉 미지원 -> 1분봉 원천 데이터가 별도로 필요)

입력 데이터 형식
----------------
columns: ['open', 'high', 'low', 'close', 'volume']
index  : DatetimeIndex (오름차순, 시간 정렬)

사용 예시는 파일 하단 __main__ 참조.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Literal


# ----------------------------------------------------------------------
# 1. 스토캐스틱 계산
# ----------------------------------------------------------------------

def stochastic(df: pd.DataFrame, k_period: int = 14, k_smooth: int = 3,
                d_period: int = 3) -> pd.DataFrame:
    """
    Slow Stochastic (%K, %D) 계산
    - k_period : Raw %K 계산에 쓰이는 lookback (예: 1시간봉 장기용은 60~120)
    - k_smooth : Raw %K를 스무딩하는 이평 기간 (Slow %K)
    - d_period : Slow %K를 다시 스무딩해서 얻는 %D
    """
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    raw_k = 100 * (df["close"] - low_min) / (high_max - low_min)
    slow_k = raw_k.rolling(k_smooth).mean()
    slow_d = slow_k.rolling(d_period).mean()
    out = pd.DataFrame({"%K": slow_k, "%D": slow_d}, index=df.index)
    return out


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """1분봉 등 원천 데이터를 상위 프레임으로 리샘플 (예: '5min', '15min', '60min')"""
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    return df.resample(rule).agg(agg).dropna(how="any")


# ----------------------------------------------------------------------
# 2. 전환(turn) 탐지
# ----------------------------------------------------------------------

@dataclass
class TurnEvent:
    timestamp: pd.Timestamp
    direction: Literal["up", "down"]
    k_value: float
    from_zone: str   # 'oversold' / 'overbought' / 'neutral'


def detect_turns(stoch_df: pd.DataFrame, oversold: float = 20, overbought: float = 80,
                  lookback: int = 3) -> list[TurnEvent]:
    """
    %K가 저점(oversold 부근)을 찍고 반등하거나, 고점(overbought 부근)을 찍고
    꺾이는 시점을 탐지. lookback 캔들 동안 하락 후 상승 반전(또는 그 반대)하면
    전환으로 인정.
    """
    k = stoch_df["%K"]
    turns = []
    for i in range(lookback, len(k)):
        window = k.iloc[i - lookback:i + 1]
        if window.isna().any():
            continue
        # 상승 전환: lookback 구간이 하락 추세였다가 마지막에 반등
        if window.iloc[-2] <= window.iloc[-3] and window.iloc[-1] > window.iloc[-2]:
            zone = "oversold" if window.iloc[-2] < oversold else "neutral"
            turns.append(TurnEvent(k.index[i], "up", float(window.iloc[-1]), zone))
        # 하락 전환
        elif window.iloc[-2] >= window.iloc[-3] and window.iloc[-1] < window.iloc[-2]:
            zone = "overbought" if window.iloc[-2] > overbought else "neutral"
            turns.append(TurnEvent(k.index[i], "down", float(window.iloc[-1]), zone))
    return turns


# ----------------------------------------------------------------------
# 3. 멀티 타임프레임 동조화 분석
# ----------------------------------------------------------------------

@dataclass
class SyncResult:
    anchor_turn: TurnEvent
    lower_tf_cycles_before_sync: dict  # {tf_label: 개수}
    lower_tf_last_turn: dict           # {tf_label: TurnEvent or None}
    synced: bool


def analyze_mtf_sync(frames: dict[str, pd.DataFrame], anchor_tf: str,
                      stoch_params: dict[str, tuple] | None = None,
                      sync_window_bars: int = 3) -> list[SyncResult]:
    """
    frames      : {'60min': df60, '15min': df15, '5min': df5, ...} (OHLCV)
    anchor_tf   : 기준이 되는 상위 프레임 키 (예: '60min')
    stoch_params: {'60min': (k_period, k_smooth, d_period), ...}
                  미지정시 기본값 (14, 3, 3) 사용. 상위 프레임 '장기' 스토캐스틱을
                  보고 싶다면 예: {'60min': (60, 5, 5)} 처럼 k_period를 늘릴 것.
    sync_window_bars : 앵커 전환 시점 앞뒤 몇 봉 이내를 '동조화'로 인정할지
    """
    stoch_params = stoch_params or {}
    stoch_cache = {}
    turn_cache = {}
    for tf, df in frames.items():
        kp, ks, dp = stoch_params.get(tf, (14, 3, 3))
        s = stochastic(df, kp, ks, dp)
        stoch_cache[tf] = s
        turn_cache[tf] = detect_turns(s)

    anchor_turns = [t for t in turn_cache[anchor_tf] if t.direction == "up"]
    results = []
    lower_tfs = [tf for tf in frames if tf != anchor_tf]

    for at in anchor_turns:
        cycles_before = {}
        last_turn_map = {}
        synced = False
        for tf in lower_tfs:
            lt = turn_cache[tf]
            # 앵커 전환 시점 이전에 발생한 하위 프레임 '상승' 전환 개수 (사이클 수 근사)
            prior_up_turns = [t for t in lt if t.direction == "up" and t.timestamp <= at.timestamp]
            cycles_before[tf] = len(prior_up_turns)
            # 앵커 시점과 가장 가까운 하위 프레임 전환
            candidates = [t for t in lt if abs((t.timestamp - at.timestamp) / pd.Timedelta(tf)) <= sync_window_bars]
            near = min(candidates, key=lambda t: abs(t.timestamp - at.timestamp)) if candidates else None
            last_turn_map[tf] = near
            if near is not None and near.direction == "up":
                synced = True
        results.append(SyncResult(at, cycles_before, last_turn_map, synced))
    return results


# ----------------------------------------------------------------------
# 4. 다이버전스 탐지 (가격 vs 스토캐스틱)
# ----------------------------------------------------------------------

@dataclass
class DivergenceEvent:
    type: Literal["bullish", "bearish"]
    start: pd.Timestamp
    end: pd.Timestamp
    price_points: tuple
    stoch_points: tuple


def _find_pivots(series: pd.Series, order: int = 3, mode: str = "low"):
    """단순 좌우 order개 캔들보다 낮은/높은 지점을 피벗으로 인정"""
    idx = []
    vals = series.values
    for i in range(order, len(vals) - order):
        window = vals[i - order:i + order + 1]
        if mode == "low" and vals[i] == window.min() and not np.isnan(vals[i]):
            idx.append(i)
        elif mode == "high" and vals[i] == window.max() and not np.isnan(vals[i]):
            idx.append(i)
    return idx


def detect_divergence(df: pd.DataFrame, stoch_df: pd.DataFrame, pivot_order: int = 3,
                       max_lag_bars: int = 5) -> list[DivergenceEvent]:
    """
    가격 저점/고점과 스토캐스틱 %K 저점/고점을 비교하여
    - 상승 다이버전스: 가격 LL(전저점보다 낮음) + 스토캐스틱 HL(전저점보다 높음)
    - 하락 다이버전스: 가격 HH(전고점보다 높음) + 스토캐스틱 LH(전고점보다 낮음)
    를 탐지. 단순 인접 피벗 쌍 비교 방식.
    """
    events = []
    close = df["close"]
    k = stoch_df["%K"]

    low_pivots = _find_pivots(df["low"], pivot_order, "low")
    high_pivots = _find_pivots(df["high"], pivot_order, "high")

    # 상승 다이버전스 (저점 비교)
    for a, b in zip(low_pivots, low_pivots[1:]):
        if b - a > max_lag_bars * 4:  # 너무 먼 피벗쌍은 제외 (임계값은 상황에 맞게 조정)
            continue
        price_a, price_b = df["low"].iloc[a], df["low"].iloc[b]
        k_a, k_b = k.iloc[a], k.iloc[b]
        if pd.isna(k_a) or pd.isna(k_b):
            continue
        if price_b < price_a and k_b > k_a:
            events.append(DivergenceEvent(
                "bullish", df.index[a], df.index[b],
                (float(price_a), float(price_b)), (float(k_a), float(k_b))
            ))

    # 하락 다이버전스 (고점 비교)
    for a, b in zip(high_pivots, high_pivots[1:]):
        if b - a > max_lag_bars * 4:
            continue
        price_a, price_b = df["high"].iloc[a], df["high"].iloc[b]
        k_a, k_b = k.iloc[a], k.iloc[b]
        if pd.isna(k_a) or pd.isna(k_b):
            continue
        if price_b > price_a and k_b < k_a:
            events.append(DivergenceEvent(
                "bearish", df.index[a], df.index[b],
                (float(price_a), float(price_b)), (float(k_a), float(k_b))
            ))

    return events


# ----------------------------------------------------------------------
# 5. 리포트 출력 (카카오톡 브리핑 등에 바로 넣을 수 있는 텍스트 포맷)
# ----------------------------------------------------------------------

def format_report(ticker: str, sync_results: list[SyncResult],
                   divergences: dict[str, list[DivergenceEvent]]) -> str:
    lines = [f"=== {ticker} 멀티프레임 스토캐스틱 리포트 ==="]

    if sync_results:
        latest = sync_results[-1]
        lines.append(f"\n[동조화] 앵커(상위TF) 전환: {latest.anchor_turn.timestamp} "
                      f"({latest.anchor_turn.from_zone} → 상승, %K={latest.anchor_turn.k_value:.1f})")
        for tf, cnt in latest.lower_tf_cycles_before_sync.items():
            near = latest.lower_tf_last_turn.get(tf)
            near_str = f"근접전환 {near.timestamp} (%K={near.k_value:.1f})" if near else "근접전환 없음"
            lines.append(f"   - {tf}: 앵커 이전 상승전환 {cnt}회 누적 | {near_str}")
        lines.append(f"   -> 현재 동조화 상태: {'YES (매수 신뢰도↑)' if latest.synced else 'NO (하위TF 미확인, 대기)'}")
    else:
        lines.append("\n[동조화] 앵커 프레임에서 상승 전환 이벤트 없음")

    for tf, divs in divergences.items():
        if not divs:
            continue
        lines.append(f"\n[다이버전스 - {tf}]")
        for d in divs[-3:]:  # 최근 3개만
            lines.append(f"   - {d.type.upper()} | {d.start} -> {d.end} | "
                          f"가격 {d.price_points[0]:.0f}->{d.price_points[1]:.0f} | "
                          f"%K {d.stoch_points[0]:.1f}->{d.stoch_points[1]:.1f}")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 사용 예시
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # --- 예시 1) 1분봉 원천 데이터가 있는 경우 (증권사 API / 크롤링으로 수집) ---
    # df1min = load_your_1min_data("023160")   # columns: open/high/low/close/volume, DatetimeIndex
    # frames = {
    #     "5min":  resample_ohlcv(df1min, "5min"),
    #     "15min": resample_ohlcv(df1min, "15min"),
    #     "60min": resample_ohlcv(df1min, "60min"),
    # }
    # sync_results = analyze_mtf_sync(
    #     frames, anchor_tf="60min",
    #     stoch_params={"60min": (60, 5, 5), "15min": (14, 3, 3), "5min": (14, 3, 3)},
    # )
    # divs = {tf: detect_divergence(frames[tf], stochastic(frames[tf])) for tf in frames}
    # print(format_report("023160 (TK Corporation)", sync_results, divs))

    # --- 예시 2) 더미 데이터로 동작 확인 ---
    rng = pd.date_range("2026-08-04 09:00", periods=600, freq="1min")
    rnd = np.random.default_rng(7)
    price = 23000 + np.cumsum(rnd.normal(0, 15, len(rng)))
    high = price + rnd.uniform(0, 30, len(rng))
    low = price - rnd.uniform(0, 30, len(rng))
    vol = rnd.integers(100, 1000, len(rng))
    df1min = pd.DataFrame({"open": price, "high": high, "low": low,
                            "close": price, "volume": vol}, index=rng)

    frames = {
        "5min":  resample_ohlcv(df1min, "5min"),
        "15min": resample_ohlcv(df1min, "15min"),
        "60min": resample_ohlcv(df1min, "60min"),
    }
    sync_results = analyze_mtf_sync(
        frames, anchor_tf="60min",
        stoch_params={"60min": (24, 5, 5), "15min": (14, 3, 3), "5min": (14, 3, 3)},
    )
    divs = {tf: detect_divergence(frames[tf], stochastic(frames[tf])) for tf in frames}
    print(format_report("DEMO", sync_results, divs))
