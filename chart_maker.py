# -*- coding: utf-8 -*-
"""
chart_maker.py
==============
프레임(1시간봉/일봉/월봉)별 차트를 matplotlib로 그려 base64 PNG(data URI)로 반환.
HTML 리포트에 <img src="data:image/png;base64,..."> 로 그대로 삽입.

각 차트 구성(위→아래):
  1) 캔들 + MA5(빨강)/MA20(파랑)
  2) 거래량 (상승 빨강 / 하락 파랑)
  3) 스토캐스틱 %K/%D (업로드 차트 참조: 상승 빨강 / 하락 파랑 라인)

한국식 색상: 양봉/상승 빨강, 음봉/하락 파랑.
"""
from __future__ import annotations
import io
import base64
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

# 한국식 색
RED = "#e2483d"      # 상승/양봉
BLUE = "#3b7be0"     # 하락/음봉
MA5_C = "#e2483d"
MA20_C = "#3b7be0"
BG = "#0f1115"
GRID = "#2a2e37"
TXT = "#c8ccd4"


def _stoch(df, k=14, ks=3, d=3):
    low = df["low"].rolling(k).min()
    high = df["high"].rolling(k).max()
    raw = 100 * (df["close"] - low) / (high - low)
    slowk = raw.rolling(ks).mean()
    slowd = slowk.rolling(d).mean()
    return slowk, slowd


def make_chart(df: pd.DataFrame, title: str, max_bars: int = 80) -> str | None:
    """단일 프레임 차트 → base64 data URI. 실패/데이터부족 시 None."""
    if df is None or len(df) < 20:
        return None
    d = df.tail(max_bars).copy().reset_index(drop=True)
    n = len(d)
    x = np.arange(n)

    ma5 = d["close"].rolling(5).mean()
    ma20 = d["close"].rolling(20).mean()
    slowk, slowd = _stoch(d)

    up = d["close"] >= d["open"]
    colors = np.where(up, RED, BLUE)

    fig = plt.figure(figsize=(4.6, 5.2), dpi=110)
    fig.patch.set_facecolor(BG)
    gs = GridSpec(3, 1, height_ratios=[3, 1, 1.2], hspace=0.08)

    # --- 1) 캔들 + MA ---
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor(BG)
    # 심지
    ax1.vlines(x, d["low"], d["high"], color=colors, linewidth=0.6)
    # 몸통
    body_w = 0.6
    for i in range(n):
        o, c = d["open"].iloc[i], d["close"].iloc[i]
        lo, hi = min(o, c), max(o, c)
        ax1.add_patch(plt.Rectangle((x[i] - body_w / 2, lo), body_w, max(hi - lo, 1e-6),
                                     facecolor=colors[i], edgecolor=colors[i], linewidth=0.5))
    ax1.plot(x, ma5, color=MA5_C, linewidth=1.0, label="MA5")
    ax1.plot(x, ma20, color=MA20_C, linewidth=1.0, label="MA20")
    ax1.set_title(title, color=TXT, fontsize=10, loc="left", pad=4)
    ax1.legend(loc="upper left", fontsize=6, facecolor=BG, edgecolor=GRID, labelcolor=TXT)
    ax1.tick_params(colors=TXT, labelsize=6, bottom=False, labelbottom=False)
    for s in ax1.spines.values():
        s.set_color(GRID)
    ax1.grid(True, color=GRID, linewidth=0.3, alpha=0.5)
    ax1.margins(x=0.01)

    # --- 2) 거래량 ---
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax2.set_facecolor(BG)
    if "volume" in d.columns:
        ax2.bar(x, d["volume"], color=colors, width=0.7)
        volma = d["volume"].rolling(5).mean()
        ax2.plot(x, volma, color="#d8b24a", linewidth=0.8)
    ax2.tick_params(colors=TXT, labelsize=6, bottom=False, labelbottom=False)
    ax2.set_ylabel("Vol", color=TXT, fontsize=6)
    for s in ax2.spines.values():
        s.set_color(GRID)
    ax2.grid(True, color=GRID, linewidth=0.3, alpha=0.4)

    # --- 3) 스토캐스틱 ---
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    ax3.set_facecolor(BG)
    ax3.plot(x, slowk, color=RED, linewidth=0.9, label="%K")
    ax3.plot(x, slowd, color=BLUE, linewidth=0.9, label="%D")
    ax3.axhline(80, color=GRID, linewidth=0.5, linestyle="--")
    ax3.axhline(20, color=GRID, linewidth=0.5, linestyle="--")
    ax3.set_ylim(0, 100)
    ax3.set_ylabel("Stoch", color=TXT, fontsize=6)
    ax3.legend(loc="upper left", fontsize=6, facecolor=BG, edgecolor=GRID, labelcolor=TXT)
    ax3.tick_params(colors=TXT, labelsize=6)
    for s in ax3.spines.values():
        s.set_color(GRID)
    ax3.grid(True, color=GRID, linewidth=0.3, alpha=0.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"


def make_three_frame_charts(hourly, daily, monthly) -> dict:
    """1H/일/월 3프레임 차트를 각각 그려 dict로 반환."""
    return {
        "hourly": make_chart(hourly, "1H (60min)", max_bars=80),
        "daily": make_chart(daily, "Daily (60d)", max_bars=70),
        "monthly": make_chart(monthly, "Monthly (5y)", max_bars=60),
    }
