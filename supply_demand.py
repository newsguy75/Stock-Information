# -*- coding: utf-8 -*-
"""
supply_demand.py
================
투자자별 수급(순매수대금) 분석 — 외국인/기관/개인 3주체.

pykrx.stock.get_market_trading_value_by_date(start, end, code) 사용.
반환 컬럼에는 (금융투자/보험/투신/.../기관합계/개인/외국인/전체 등)이 들어옴.
버전/시장에 따라 컬럼명이 달라질 수 있어 매핑을 방어적으로 처리.
"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class SupplySignal:
    # 최근 5일 순매수 누적 (억원 단위로 환산)
    foreign_5d: float
    inst_5d: float
    indiv_5d: float
    # 20일 누적
    foreign_20d: float
    inst_20d: float
    # 연속 순매수일 (외국인/기관, +연속매수 / -연속매도)
    foreign_streak: int
    inst_streak: int
    # 당일 방향
    foreign_today: float
    inst_today: float
    # 20일 추세 판정
    foreign_trend: str   # '유입' / '유출' / '중립'
    inst_trend: str
    ok: bool = True
    note: str = ""


def _pick(df: pd.DataFrame, *candidates) -> pd.Series | None:
    """여러 후보 컬럼명 중 존재하는 첫 컬럼 반환."""
    for c in candidates:
        if c in df.columns:
            return df[c]
    return None


def _streak(series: pd.Series) -> int:
    """마지막 값 기준 같은 부호가 며칠 연속인지 (+매수 / -매도)."""
    if len(series) == 0:
        return 0
    vals = series.values
    last_sign = np.sign(vals[-1])
    if last_sign == 0:
        return 0
    cnt = 0
    for v in reversed(vals):
        if np.sign(v) == last_sign:
            cnt += 1
        else:
            break
    return int(cnt * last_sign)


def _trend(series: pd.Series) -> str:
    """20일 순매수 누적 방향 + 선형 기울기로 유입/유출/중립 판정."""
    if len(series) < 5:
        return "중립"
    cum = series.sum()
    x = np.arange(len(series))
    slope = np.polyfit(x, series.cumsum().values, 1)[0]
    if cum > 0 and slope > 0:
        return "유입"
    if cum < 0 and slope < 0:
        return "유출"
    return "중립"


def fetch_supply(code: str, days: int = 30) -> SupplySignal:
    """
    최근 days 영업일치 투자자별 순매수대금을 받아 시그널 계산.
    실패 시 ok=False.
    """
    try:
        from pykrx import stock
    except Exception as e:
        return SupplySignal(*([0] * 9), "중립", "중립", ok=False, note=f"pykrx 없음: {e}")

    end = dt.date.today()
    start = end - dt.timedelta(days=int(days * 1.8) + 10)  # 영업일 여유
    try:
        df = stock.get_market_trading_value_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code)
    except Exception as e:
        return SupplySignal(*([0] * 9), "중립", "중립", ok=False, note=f"조회 실패: {e}")

    if df is None or len(df) == 0:
        return SupplySignal(*([0] * 9), "중립", "중립", ok=False, note="데이터 없음")

    df = df.sort_index()
    foreign = _pick(df, "외국인", "외국인합계")
    inst = _pick(df, "기관합계", "기관")
    indiv = _pick(df, "개인")
    if foreign is None or inst is None:
        return SupplySignal(*([0] * 9), "중립", "중립", ok=False,
                            note=f"컬럼 매핑 실패: {list(df.columns)}")
    if indiv is None:
        indiv = pd.Series(0, index=df.index)

    E = 1e8  # 억원 환산
    f5 = foreign.tail(5).sum() / E
    i5 = inst.tail(5).sum() / E
    p5 = indiv.tail(5).sum() / E
    f20 = foreign.tail(20).sum() / E
    i20 = inst.tail(20).sum() / E

    return SupplySignal(
        foreign_5d=round(f5, 1), inst_5d=round(i5, 1), indiv_5d=round(p5, 1),
        foreign_20d=round(f20, 1), inst_20d=round(i20, 1),
        foreign_streak=_streak(foreign.tail(20)),
        inst_streak=_streak(inst.tail(20)),
        foreign_today=round(foreign.iloc[-1] / E, 1),
        inst_today=round(inst.iloc[-1] / E, 1),
        foreign_trend=_trend(foreign.tail(20)),
        inst_trend=_trend(inst.tail(20)),
        ok=True,
    )


def supply_text(s: SupplySignal) -> str:
    """한 줄 요약 텍스트."""
    if not s.ok:
        return "수급 조회 불가"
    def fmt(v):
        return f"+{v:.0f}억" if v >= 0 else f"{v:.0f}억"
    fs = f"연속매수{s.foreign_streak}일" if s.foreign_streak > 0 else \
         (f"연속매도{-s.foreign_streak}일" if s.foreign_streak < 0 else "혼조")
    return (f"외국인 5일 {fmt(s.foreign_5d)}({s.foreign_trend},{fs}) · "
            f"기관 5일 {fmt(s.inst_5d)}({s.inst_trend})")


if __name__ == "__main__":
    # 오프라인 더미
    idx = pd.date_range("2026-07-01", periods=25, freq="B")
    r = np.random.default_rng(1)
    df = pd.DataFrame({
        "외국인": r.normal(3e8, 5e8, 25),
        "기관합계": r.normal(-1e8, 4e8, 25),
        "개인": r.normal(-2e8, 6e8, 25),
    }, index=idx)
    # 더미로 직접 계산 로직만 확인
    from types import SimpleNamespace
    E = 1e8
    print("외국인 20일 추세:", _trend(df["외국인"].tail(20)))
    print("외국인 연속:", _streak(df["외국인"].tail(20)))
    print("외국인 5일 누적(억):", round(df["외국인"].tail(5).sum() / E, 1))
