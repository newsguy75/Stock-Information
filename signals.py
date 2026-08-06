# -*- coding: utf-8 -*-
"""
signals.py
==========
이동평균(5/20/60) 방향·정배열·골든/데드 크로스·5일선 터치,
거래량 5봉선 돌파 시그널 계산 엔진.

일봉 / 주봉 / 월봉 / 1시간봉 어느 프레임의 OHLCV DataFrame에도 동일하게 적용.
DataFrame columns : ['open','high','low','close','volume'], index=DatetimeIndex(오름차순)
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Literal, Optional


# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------
@dataclass
class SignalConfig:
    ma_short: int = 5
    ma_mid: int = 20
    ma_long: int = 60
    slope_lookback: int = 3          # MA5 방향 판정용 lookback (봉 수)
    flat_threshold_pct: float = 0.3  # 이 % 이내 변화면 '보합'
    touch_tol_pct: float = 0.8       # 저가가 MA5의 이 % 이내로 눌리면 '터치'
    cross_lookback: int = 3          # 최근 몇 봉 내 크로스를 '발생'으로 볼지
    vol_ma_period: int = 5           # 거래량 이동평균 기간 (거래량 5일/5봉선)


# ----------------------------------------------------------------------
# 개별 시그널 데이터 구조
# ----------------------------------------------------------------------
@dataclass
class MASignal:
    ma5: float
    ma20: float
    ma60: float
    ma5_direction: Literal["상향", "보합", "하향"]
    ma5_slope_pct: float
    alignment: Literal["정배열", "역배열", "혼조"]
    above_ma5: bool
    ma5_touch: bool                 # 5일선 위에 있으면서 저가가 5일선을 터치
    cross_5_20: Optional[str]       # '골든크로스' / '데드크로스' / None
    cross_20_60: Optional[str]
    cross_5_60: Optional[str]


@dataclass
class VolumeSignal:
    volume: float
    vol_ma: float
    over_vol_ma: bool               # 현재 거래량 > 거래량 이동평균
    ratio: float                    # volume / vol_ma


# ----------------------------------------------------------------------
# 계산 함수
# ----------------------------------------------------------------------
def _cross(fast: pd.Series, slow: pd.Series, lookback: int) -> Optional[str]:
    """최근 lookback 봉 내에 fast가 slow를 상향/하향 돌파했는지"""
    diff = (fast - slow).dropna()
    if len(diff) < lookback + 1:
        return None
    recent = diff.iloc[-(lookback + 1):]
    signs = np.sign(recent.values)
    for i in range(1, len(signs)):
        if signs[i - 1] < 0 and signs[i] > 0:
            return "골든크로스"
        if signs[i - 1] > 0 and signs[i] < 0:
            return "데드크로스"
    return None


def compute_ma_signal(df: pd.DataFrame, cfg: SignalConfig = SignalConfig()) -> MASignal:
    close = df["close"]
    ma5 = close.rolling(cfg.ma_short).mean()
    ma20 = close.rolling(cfg.ma_mid).mean()
    ma60 = close.rolling(cfg.ma_long).mean()

    ma5_now = ma5.iloc[-1]
    ma20_now = ma20.iloc[-1]
    ma60_now = ma60.iloc[-1]

    # MA5 방향 (slope_lookback 봉 전 대비 %)
    if len(ma5.dropna()) > cfg.slope_lookback:
        prev = ma5.iloc[-(cfg.slope_lookback + 1)]
        slope_pct = (ma5_now - prev) / prev * 100 if prev else 0.0
    else:
        slope_pct = 0.0
    if slope_pct > cfg.flat_threshold_pct:
        direction = "상향"
    elif slope_pct < -cfg.flat_threshold_pct:
        direction = "하향"
    else:
        direction = "보합"

    # 정배열 / 역배열
    if not any(pd.isna([ma5_now, ma20_now, ma60_now])):
        if ma5_now > ma20_now > ma60_now:
            alignment = "정배열"
        elif ma5_now < ma20_now < ma60_now:
            alignment = "역배열"
        else:
            alignment = "혼조"
    else:
        alignment = "혼조"

    # 5일선 위 + 터치
    last_close = close.iloc[-1]
    last_low = df["low"].iloc[-1]
    above = bool(last_close >= ma5_now) if not pd.isna(ma5_now) else False
    touch = False
    if above and not pd.isna(ma5_now):
        # 저가가 5일선을 아래로 관통하거나 tol 이내로 근접
        if last_low <= ma5_now * (1 + cfg.touch_tol_pct / 100):
            touch = True

    return MASignal(
        ma5=float(ma5_now), ma20=float(ma20_now), ma60=float(ma60_now),
        ma5_direction=direction, ma5_slope_pct=float(slope_pct),
        alignment=alignment, above_ma5=above, ma5_touch=touch,
        cross_5_20=_cross(ma5, ma20, cfg.cross_lookback),
        cross_20_60=_cross(ma20, ma60, cfg.cross_lookback),
        cross_5_60=_cross(ma5, ma60, cfg.cross_lookback),
    )


def compute_volume_signal(df: pd.DataFrame, cfg: SignalConfig = SignalConfig()) -> VolumeSignal:
    vol = df["volume"]
    vol_ma = vol.rolling(cfg.vol_ma_period).mean()
    v_now = float(vol.iloc[-1])
    vma_now = float(vol_ma.iloc[-1]) if not pd.isna(vol_ma.iloc[-1]) else float("nan")
    over = bool(v_now > vma_now) if not np.isnan(vma_now) else False
    ratio = (v_now / vma_now) if (vma_now and not np.isnan(vma_now)) else 0.0
    return VolumeSignal(volume=v_now, vol_ma=vma_now, over_vol_ma=over, ratio=ratio)


# ----------------------------------------------------------------------
# 리샘플 (일봉 -> 주봉/월봉)
# ----------------------------------------------------------------------
def to_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df_daily.resample("W-FRI").agg(agg).dropna(how="any")


def to_monthly(df_daily: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df_daily.resample("ME").agg(agg).dropna(how="any")


if __name__ == "__main__":
    # 더미 테스트
    rng = pd.date_range("2020-01-01", periods=1500, freq="B")
    rnd = np.random.default_rng(3)
    close = 20000 + np.cumsum(rnd.normal(0, 200, len(rng)))
    df = pd.DataFrame({
        "open": close, "high": close + rnd.uniform(0, 150, len(rng)),
        "low": close - rnd.uniform(0, 150, len(rng)), "close": close,
        "volume": rnd.integers(1e5, 1e6, len(rng)),
    }, index=rng)
    print("[일봉]", compute_ma_signal(df))
    print("[일봉 거래량]", compute_volume_signal(df))
    print("[주봉]", compute_ma_signal(to_weekly(df)))
    print("[월봉]", compute_ma_signal(to_monthly(df)))
