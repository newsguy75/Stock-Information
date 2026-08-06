# -*- coding: utf-8 -*-
"""
indices.py
==========
주요 지수 7종 수집 + 분석:
  코스피 / 코스닥 / 코스피200(*코스피150 미존재 대체) / 코스닥150 /
  반도체 / 바이오 / 조선

- 코스피·코스닥·200·150: pykrx get_index_ohlcv (이름으로 조회)
- 반도체/바이오/조선: pykrx 지수 목록에서 키워드로 동적 매칭, 실패 시
  KOSPI 업종지수(전기전자/의약품/운수장비)로 폴백.

각 지수: 등락률, MA5/20 방향, 스토캐 구간/다이버전스.
"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
import numpy as np
import pandas as pd

from stoch_frames import analyze_frame
from signals import compute_ma_signal, SignalConfig

CFG = SignalConfig()

# 요청 지수 → (조회이름 후보, 시장, 폴백업종이름)
INDEX_PLAN = [
    ("코스피",       ["코스피"],                 "KOSPI", None),
    ("코스닥",       ["코스닥"],                 "KOSDAQ", None),
    ("코스피200",    ["코스피 200"],             "KOSPI", None),   # 코스피150 미존재 → 200
    ("코스닥150",    ["코스닥 150"],             "KOSDAQ", None),
    ("반도체",       ["반도체"],                 "BOTH", "전기전자"),
    ("바이오",       ["바이오", "헬스케어", "제약"], "BOTH", "의약품"),
    ("조선",         ["조선"],                   "BOTH", "운수장비"),
]


@dataclass
class IndexView:
    label: str
    resolved: str
    last: float
    chg: float
    ma5_dir: str
    ma20_dir: str
    zone: str
    divergence: str
    ok: bool = True


def _resolve_names(stock):
    """pykrx 지수 이름 목록 캐시 (KOSPI+KOSDAQ)."""
    names = {}
    for mkt in ("KOSPI", "KOSDAQ"):
        try:
            for tk in stock.get_index_ticker_list(market=mkt):
                nm = stock.get_index_ticker_name(tk)
                names[nm] = (tk, mkt)
        except Exception:
            pass
    return names


def _match(names: dict, candidates, market):
    """후보 키워드를 포함하는 지수 이름을 찾음."""
    for kw in candidates:
        # 정확 일치 우선
        for nm, (tk, mkt) in names.items():
            if nm == kw and (market in ("BOTH", mkt)):
                return nm, tk
        # 부분 포함
        for nm, (tk, mkt) in names.items():
            if kw in nm and (market in ("BOTH", mkt)):
                return nm, tk
    return None, None


def fetch_indices(days: int = 400) -> list[IndexView]:
    try:
        from pykrx import stock
    except Exception:
        return []

    end = dt.date.today()
    start = end - dt.timedelta(days=int(days * 1.5))
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    names = _resolve_names(stock)
    views = []

    def ohlcv(ticker_or_name):
        try:
            df = stock.get_index_ohlcv_by_date(s, e, ticker_or_name)
        except Exception:
            return None
        if df is None or len(df) == 0:
            return None
        df = df.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                                 "종가": "close", "거래량": "volume"})
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep].copy()
        if "volume" not in df.columns:
            df["volume"] = 0
        df.index = pd.to_datetime(df.index)
        return df.sort_index()

    for label, cands, market, fallback in INDEX_PLAN:
        df, resolved = None, ""
        # 1) 후보 이름 매칭
        nm, tk = _match(names, cands, market)
        if nm:
            df = ohlcv(tk) if tk else ohlcv(nm)
            resolved = nm
        # 2) 폴백 업종
        if (df is None or len(df) < 30) and fallback:
            nm2, tk2 = _match(names, [fallback], "BOTH")
            if nm2:
                df = ohlcv(tk2) if tk2 else ohlcv(nm2)
                resolved = f"{nm2}(대체)"
        if df is None or len(df) < 30:
            views.append(IndexView(label, "조회불가", 0, 0, "-", "-", "-", "없음", ok=False))
            continue

        ma = compute_ma_signal(df, CFG)
        # MA20 방향
        ma20 = df["close"].rolling(20).mean()
        ma20_dir = "상향" if ma20.iloc[-1] > ma20.iloc[-4] else \
                   ("하향" if ma20.iloc[-1] < ma20.iloc[-4] else "보합")
        sf = analyze_frame(df, "일봉")
        last = df["close"].iloc[-1]
        chg = (last - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100
        views.append(IndexView(label, resolved, float(last), float(chg),
                               ma.ma5_direction, ma20_dir, sf.zone, sf.divergence, ok=True))
    return views


if __name__ == "__main__":
    for v in fetch_indices():
        print(v.label, v.resolved, v.ok)
