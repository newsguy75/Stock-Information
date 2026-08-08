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

# ---- 한글 폰트 설정 (없으면 영문 라벨로 자동 폴백) ----
KO_FONT_OK = False
try:
    import matplotlib.font_manager as _fm
    _CANDIDATES = ["NanumGothic", "Malgun Gothic", "AppleGothic",
                   "Noto Sans CJK KR", "Noto Sans CJK JP", "Noto Sans KR",
                   "UnDotum", "Baekmuk Gulim", "WenQuanYi Zen Hei"]
    _have = {f.name for f in _fm.fontManager.ttflist}
    for _c in _CANDIDATES:
        if _c in _have:
            plt.rcParams["font.family"] = _c
            KO_FONT_OK = True
            break
except Exception:
    pass
plt.rcParams["axes.unicode_minus"] = False   # 음수 부호 깨짐 방지


def L(ko: str, en: str) -> str:
    """한글 폰트가 있으면 한글, 없으면 영문 라벨."""
    return ko if KO_FONT_OK else en


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


def make_chart(df: pd.DataFrame, title: str, max_bars: int = 80,
               show_ma60: bool = False) -> str | None:
    """단일 프레임 차트 → base64 data URI. 실패/데이터부족 시 None.
    show_ma60=True 일 때만 60일선 표시 (일봉에만 적용)."""
    if df is None or len(df) < 20:
        return None
    d = df.tail(max_bars).copy().reset_index(drop=True)
    n = len(d)
    x = np.arange(n)

    ma5 = d["close"].rolling(5).mean()
    ma20 = d["close"].rolling(20).mean()
    ma60 = d["close"].rolling(60).mean() if show_ma60 else None
    slowk, slowd = _stoch(d)

    up = d["close"] >= d["open"]
    colors = np.where(up, RED, BLUE)

    fig = plt.figure(figsize=(12.0, 7.5), dpi=100)
    fig.patch.set_facecolor(BG)
    gs = GridSpec(3, 1, height_ratios=[3, 1, 1.2], hspace=0.08)

    # --- 1) 캔들 + MA ---
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor(BG)
    ax1.vlines(x, d["low"], d["high"], color=colors, linewidth=0.6)
    body_w = 0.6
    for i in range(n):
        o, c = d["open"].iloc[i], d["close"].iloc[i]
        lo, hi = min(o, c), max(o, c)
        ax1.add_patch(plt.Rectangle((x[i] - body_w / 2, lo), body_w, max(hi - lo, 1e-6),
                                     facecolor=colors[i], edgecolor=colors[i], linewidth=0.5))
    ax1.plot(x, ma5, color=MA5_C, linewidth=1.0, label="MA5")
    ax1.plot(x, ma20, color=MA20_C, linewidth=1.0, label="MA20")
    if ma60 is not None and ma60.notna().any():
        ax1.plot(x, ma60, color="#d8b24a", linewidth=1.0, label="MA60")
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


def _find_pivots(arr: np.ndarray, order: int = 3, mode: str = "low") -> list[int]:
    """단순 피벗 탐지: 좌우 order개보다 낮으면(높으면) 피벗."""
    out = []
    n = len(arr)
    for i in range(order, n - order):
        w = arr[i - order:i + order + 1]
        if mode == "low" and arr[i] == w.min():
            out.append(i)
        elif mode == "high" and arr[i] == w.max():
            out.append(i)
    return out


def _detect_div_on(close: np.ndarray, k: np.ndarray, order: int = 3,
                   max_age: int = 25) -> list[dict]:
    """가격 vs %K 다이버전스 탐지. 차트 표시용(인덱스 반환)."""
    res = []
    n = len(close)
    # 상승(bullish): 가격 저점↓ + %K 저점↑
    lows = [i for i in _find_pivots(close, order, "low") if not np.isnan(k[i])]
    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        if (n - 1 - i2) <= max_age and close[i2] < close[i1] and k[i2] > k[i1]:
            res.append({"type": "bullish", "i1": i1, "i2": i2})
    # 하락(bearish): 가격 고점↑ + %K 고점↓
    highs = [i for i in _find_pivots(close, order, "high") if not np.isnan(k[i])]
    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        if (n - 1 - i2) <= max_age and close[i2] > close[i1] and k[i2] < k[i1]:
            res.append({"type": "bearish", "i1": i1, "i2": i2})
    return res


def _detect_double(close: np.ndarray, order: int = 3, tol_pct: float = 3.0,
                   max_age: int = 20) -> list[dict]:
    """쌍바닥/쌍봉 탐지 (차트 표시용)."""
    out = []
    n = len(close)
    # 쌍바닥
    lows = _find_pivots(close, order, "low")
    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        v1, v2 = close[i1], close[i2]
        if (n - 1 - i2) <= max_age and abs(v1 - v2) / max(v1, v2) * 100 <= tol_pct:
            mid = close[i1:i2 + 1].max() if i2 > i1 else v1
            if (mid - min(v1, v2)) / max(v1, v2) * 100 > 2:
                out.append({"kind": "쌍바닥", "i1": i1, "i2": i2,
                            "level": (v1 + v2) / 2, "neck": mid})
    # 쌍봉
    highs = _find_pivots(close, order, "high")
    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        v1, v2 = close[i1], close[i2]
        if (n - 1 - i2) <= max_age and abs(v1 - v2) / max(v1, v2) * 100 <= tol_pct:
            mid = close[i1:i2 + 1].min() if i2 > i1 else v1
            if (max(v1, v2) - mid) / max(v1, v2) * 100 > 2:
                out.append({"kind": "쌍봉", "i1": i1, "i2": i2,
                            "level": (v1 + v2) / 2, "neck": mid})
    return out


def make_stoch_triple_chart(daily: pd.DataFrame, max_bars: int = 90) -> str | None:
    """일봉 기준 스토캐 3종(5-3-3 / 10-5-5 / 20-10-10) 비교 차트.
    - 상단: 종가 라인 + 쌍바닥/쌍봉 표시 + 다이버전스 선
    - 하단 3단: 각 스토캐 %K/%D + 해당 구간의 다이버전스 선
    """
    if daily is None or len(daily) < 40:
        return None
    d = daily.tail(max_bars).copy().reset_index(drop=True)
    n = len(d)
    x = np.arange(n)
    close = d["close"].to_numpy(dtype=float)

    SETS = [(L("단기 (5-3-3)", "Fast (5-3-3)"), 5, 3, 3),
            (L("중기 (10-5-5)", "Mid (10-5-5)"), 10, 5, 5),
            (L("장기 (20-10-10)", "Slow (20-10-10)"), 20, 10, 10)]

    fig = plt.figure(figsize=(12.0, 9.0), dpi=100)
    fig.patch.set_facecolor(BG)
    gs = GridSpec(4, 1, height_ratios=[2.4, 1, 1, 1], hspace=0.14)

    # ---------- 상단: 가격 ----------
    ax0 = fig.add_subplot(gs[0])
    ax0.set_facecolor(BG)
    ax0.plot(x, close, color="#d8dce4", linewidth=1.3, label=L("종가", "Close"))
    ax0.plot(x, d["close"].rolling(20).mean(), color=MA20_C, linewidth=1.0, label="MA20")

    # 쌍바닥/쌍봉 표시
    for pat in _detect_double(close):
        i1, i2, lvl = pat["i1"], pat["i2"], pat["level"]
        is_bottom = pat["kind"] == "쌍바닥"
        c = RED if is_bottom else BLUE
        pat_label = L(pat["kind"], "DoubleBottom" if is_bottom else "DoubleTop")
        ax0.plot([i1, i2], [close[i1], close[i2]], color=c, linewidth=2.0,
                 linestyle="--", alpha=0.9)
        ax0.scatter([i1, i2], [close[i1], close[i2]], color=c, s=45, zorder=5,
                    edgecolors=BG, linewidths=1.0)
        # 넥라인
        ax0.axhline(pat["neck"], color=c, linewidth=0.8, linestyle=":", alpha=0.6)
        ax0.annotate(pat_label, xy=((i1 + i2) / 2, lvl),
                     xytext=(0, -18 if is_bottom else 12),
                     textcoords="offset points", ha="center",
                     color=c, fontsize=9, fontweight="bold")

    ax0.set_title(L("일봉 · 스토캐스틱 3구간 (다이버전스 / 쌍바닥·쌍봉)",
                   "Daily - Stochastic 3 Sets (Divergence / Double Top-Bottom)"),
                  color=TXT, fontsize=11, loc="left", pad=6)
    ax0.grid(color=GRID, linewidth=0.4, alpha=0.5)
    ax0.tick_params(colors=TXT, labelsize=8)
    for sp in ax0.spines.values():
        sp.set_color(GRID)
    leg = ax0.legend(loc="upper left", fontsize=7, facecolor=BG, edgecolor=GRID)
    for t in leg.get_texts():
        t.set_color(TXT)
    ax0.set_xticklabels([])

    # ---------- 하단 3단: 스토캐 ----------
    for idx, (label, kp, ks, dp) in enumerate(SETS, start=1):
        ax = fig.add_subplot(gs[idx])
        ax.set_facecolor(BG)
        slowk, slowd = _stoch(d, kp, ks, dp)
        kv = slowk.to_numpy(dtype=float)

        ax.plot(x, slowk, color=RED, linewidth=1.1, label="%K")
        ax.plot(x, slowd, color=BLUE, linewidth=1.1, label="%D")
        ax.axhline(80, color=GRID, linewidth=0.6, linestyle="--", alpha=0.8)
        ax.axhline(20, color=GRID, linewidth=0.6, linestyle="--", alpha=0.8)
        ax.fill_between(x, 80, 100, color=BLUE, alpha=0.06)
        ax.fill_between(x, 0, 20, color=RED, alpha=0.06)

        # 다이버전스 선 (가격 패널 + 스토캐 패널 동시 표시)
        for dv in _detect_div_on(close, kv):
            i1, i2 = dv["i1"], dv["i2"]
            is_bull = dv["type"] == "bullish"
            c = RED if is_bull else BLUE
            tag = L("상승DIV", "Bull DIV") if is_bull else L("하락DIV", "Bear DIV")
            # 스토캐 패널에 선
            ax.plot([i1, i2], [kv[i1], kv[i2]], color=c, linewidth=2.2, alpha=0.95)
            ax.scatter([i1, i2], [kv[i1], kv[i2]], color=c, s=28, zorder=5,
                       edgecolors=BG, linewidths=0.8)
            ax.annotate(f"{tag}", xy=((i1 + i2) / 2, (kv[i1] + kv[i2]) / 2),
                        xytext=(0, 8 if is_bull else -14), textcoords="offset points",
                        ha="center", color=c, fontsize=7.5, fontweight="bold")
            # 가격 패널에도 대응 선 (장기 세트만 — 겹침 방지)
            if kp == 20:
                ax0.plot([i1, i2], [close[i1], close[i2]], color=c,
                         linewidth=2.2, alpha=0.9)
                ax0.annotate(tag, xy=((i1 + i2) / 2, (close[i1] + close[i2]) / 2),
                             xytext=(0, 10 if is_bull else -16),
                             textcoords="offset points", ha="center",
                             color=c, fontsize=8, fontweight="bold")

        ax.set_ylim(0, 100)
        ax.set_ylabel(label, color=TXT, fontsize=8)
        ax.grid(color=GRID, linewidth=0.35, alpha=0.4)
        ax.tick_params(colors=TXT, labelsize=7)
        for sp in ax.spines.values():
            sp.set_color(GRID)
        if idx < 3:
            ax.set_xticklabels([])
        leg = ax.legend(loc="upper left", fontsize=6.5, facecolor=BG, edgecolor=GRID, ncol=2)
        for t in leg.get_texts():
            t.set_color(TXT)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def make_three_frame_charts(hourly, daily, monthly) -> dict:
    """일봉/월봉 2프레임 차트를 각각 그려 dict로 반환.
    1시간봉은 UI에서 삭제되어 None. 함수명은 기존 호환성 유지."""
    return {
        "hourly": None,   # UI에서 제외 (뷰어 요청)
        "daily": make_chart(daily, "Daily", max_bars=80, show_ma60=True),
        "monthly": make_chart(monthly, "Monthly (5y)", max_bars=60),
        "stoch_triple": make_stoch_triple_chart(daily, max_bars=90),
    }
