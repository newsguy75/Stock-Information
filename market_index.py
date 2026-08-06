# -*- coding: utf-8 -*-
"""
market_index.py
===============
KOSPI / KOSDAQ 지수 시황 요약 (카톡 첫 메시지용).

FDR로 지수 OHLCV를 받아 등락률 + MA5/20 방향 + 간단한 코멘트 생성.
FDR 지수 심볼: 'KS11'(코스피), 'KQ11'(코스닥).
실패 시 방어적 폴백.
"""
from __future__ import annotations
import datetime as dt
import pandas as pd
import numpy as np


INDEX_SYMBOLS = {
    "KOSPI": "KS11",
    "KOSDAQ": "KQ11",
}


def _fetch_index(symbol: str, years: float = 1.0) -> pd.DataFrame:
    import FinanceDataReader as fdr
    end = dt.date.today()
    start = end - dt.timedelta(days=int(years * 365.25))
    df = fdr.DataReader(symbol, start, end)
    df = df.rename(columns=str.lower)
    # 지수는 volume이 없을 수 있음
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep]
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def _dummy_index(seed: int) -> pd.DataFrame:
    rng = pd.date_range(dt.date.today() - dt.timedelta(days=200), periods=140, freq="B")
    r = np.random.default_rng(seed)
    close = 2500 + np.cumsum(r.normal(2, 20, len(rng)))
    return pd.DataFrame({
        "open": close, "high": close + r.uniform(0, 10, len(rng)),
        "low": close - r.uniform(0, 10, len(rng)), "close": close,
        "volume": r.integers(1e8, 5e8, len(rng)),
    }, index=rng)


def analyze_index(name: str, symbol: str, demo: bool = False) -> dict:
    try:
        df = _dummy_index(hash(symbol) % 100) if demo else _fetch_index(symbol)
        if len(df) < 25:
            return {"ok": False, "name": name}
        close = df["close"]
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        chg = (last - prev) / prev * 100
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        # MA5 방향
        ma5_prev = close.rolling(5).mean().iloc[-4]
        ma5_dir = "상향" if ma5 > ma5_prev else ("하향" if ma5 < ma5_prev else "보합")
        align = "5>20" if ma5 > ma20 else "5<20"
        return {"ok": True, "name": name, "close": last, "chg": round(chg, 2),
                "ma5_dir": ma5_dir, "align": align}
    except Exception as e:
        return {"ok": False, "name": name, "error": str(e)}


def _icon(chg: float) -> str:
    if chg > 0.05:
        return "🔴"
    if chg < -0.05:
        return "🔵"
    return "⚪"


def build_market_summary(demo: bool = False) -> str:
    """카톡 첫 메시지: KOSPI/KOSDAQ 시황 요약."""
    now_vn = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    lines = [f"🏛 시황 요약 (VN {now_vn:%m/%d %H:%M})", "─" * 20]
    results = []
    for name, sym in INDEX_SYMBOLS.items():
        r = analyze_index(name, sym, demo=demo)
        results.append(r)
        if r.get("ok"):
            icon = _icon(r["chg"])
            lines.append(f"{icon} {name} {r['close']:,.2f} ({r['chg']:+.2f}%)")
            lines.append(f"   MA5 {r['ma5_dir']} · {r['align']}")
        else:
            lines.append(f"⚪ {name} 데이터 조회 실패")

    # 코스피/코스닥 방향 종합 한 줄
    ups = sum(1 for r in results if r.get("ok") and r["chg"] > 0)
    if ups == 2:
        tone = "양 지수 강세 — 위험선호"
    elif ups == 0:
        tone = "양 지수 약세 — 위험회피"
    else:
        tone = "지수 혼조 — 종목별 대응"
    lines.append("─" * 20)
    lines.append(f"📌 {tone}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(build_market_summary(demo=True))
