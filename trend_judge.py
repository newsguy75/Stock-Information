# -*- coding: utf-8 -*-
"""
trend_judge.py
==============
"조정(Pullback) vs 꺾임(Trend Reversal)" 판정 모듈.
사용자 제공 판단기준 문서를 그대로 구현.

체크리스트 (문서 표1):
  기준        | 조정               | 꺾임                    | 주 타임프레임
  시장구조    | HL 유지            | HL 붕괴 + LH 형성       | 1D
  EMA         | 20EMA 지지 성공    | 20EMA+50EMA 동시 이탈   | 1D
  RSI/스토캐  | 과매도 후 반등     | 다이버전스 후 추가하락  | 1D
  거래량      | 하락 시 거래량 약함| 하락 시 거래량 급증     | 1D
  피보나치    | 0.382~0.5 지지     | 0.5~0.618 뚫림          | 1D
  주봉        | 주봉 EMA 지지 유지 | 주봉 EMA 붕괴           | 1W

타임프레임 위계(문서):
  1D(일봉) = 주력 추세 판단, 가장 신뢰성 높음
  1W(주봉) = 큰 그림 / 사이클 전환
  1M(월봉) = 방향 확인용 (단타·스윙엔 너무 느림)
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 공통 유틸
# ----------------------------------------------------------------------
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _stoch(df: pd.DataFrame, k: int, ks: int, d: int):
    low = df["low"].rolling(k).min()
    high = df["high"].rolling(k).max()
    rng = (high - low).replace(0, np.nan)
    raw = 100 * (df["close"] - low) / rng
    sk = raw.rolling(ks).mean()
    sd = sk.rolling(d).mean()
    return sk, sd


def _pivots(arr: np.ndarray, order: int = 3, mode: str = "low") -> list[int]:
    out = []
    for i in range(order, len(arr) - order):
        w = arr[i - order:i + order + 1]
        if mode == "low" and arr[i] == w.min():
            out.append(i)
        elif mode == "high" and arr[i] == w.max():
            out.append(i)
    return out


# ----------------------------------------------------------------------
# 1. 시장 구조 (HH/HL/LH/LL) — 문서 1순위 기준
# ----------------------------------------------------------------------
def analyze_market_structure(df: pd.DataFrame, lookback: int = 70,
                             order: int = 3) -> dict:
    """스윙 고점/저점으로 HH·HL·LH·LL 구조 판정.
    - HL 유지 = 상승구조 유지(조정)
    - HL 붕괴 + LH = 하락구조(꺾임)
    """
    if df is None or len(df) < 30:
        return {"ok": False}
    d = df.tail(lookback)
    close = d["close"].to_numpy(dtype=float)
    highs = d["high"].to_numpy(dtype=float)
    lows = d["low"].to_numpy(dtype=float)

    hi_idx = _pivots(highs, order, "high")
    lo_idx = _pivots(lows, order, "low")
    if len(hi_idx) < 2 or len(lo_idx) < 2:
        return {"ok": False}

    h1, h2 = highs[hi_idx[-2]], highs[hi_idx[-1]]
    l1, l2 = lows[lo_idx[-2]], lows[lo_idx[-1]]
    hh = h2 > h1          # Higher High
    hl = l2 > l1          # Higher Low
    cur = float(close[-1])

    if hh and hl:
        structure, sig = "상승구조 (HH·HL)", 1
        note = "고점·저점 모두 상승 — 추세 유지"
    elif (not hh) and hl:
        structure, sig = "조정구조 (LH·HL)", 0
        note = "저점은 지켜짐(HL) — 고점만 낮아진 조정 국면"
    elif hh and (not hl):
        structure, sig = "확장/불안 (HH·LL)", 0
        note = "변동성 확대 — 방향 불명확"
    else:
        structure, sig = "하락구조 (LH·LL)", -1
        note = "저점 붕괴(LL) + 고점 하락(LH) — 추세 꺾임"

    # 최근 저점이 아직 안 깨졌는지 (HL 유지 여부)
    hl_intact = cur > l2 * 0.995
    return {"ok": True, "structure": structure, "signal": sig, "note": note,
            "hh": bool(hh), "hl": bool(hl), "hl_intact": bool(hl_intact),
            "last_low": round(float(l2), 2), "last_high": round(float(h2), 2)}


# ----------------------------------------------------------------------
# 2. EMA 상태 (일봉 20/50, 주봉 20)
# ----------------------------------------------------------------------
def analyze_ema(daily: pd.DataFrame, weekly: pd.DataFrame = None) -> dict:
    out = {"ok": False}
    if daily is None or len(daily) < 55:
        return out
    c = daily["close"]
    e20, e50 = _ema(c, 20), _ema(c, 50)
    cur = float(c.iloc[-1])
    v20, v50 = float(e20.iloc[-1]), float(e50.iloc[-1])

    above20, above50 = cur > v20, cur > v50
    if above20 and above50:
        d_state, d_sig = "20·50EMA 위 (지지 성공)", 1
    elif (not above20) and (not above50):
        d_state, d_sig = "20·50EMA 동시 이탈 (꺾임 신호)", -1
    elif above50 and not above20:
        d_state, d_sig = "20EMA 이탈·50EMA 지지 (조정 구간)", 0
    else:
        d_state, d_sig = "20EMA 위·50EMA 아래 (회복 시도)", 0

    # EMA 배열
    align = "정배열" if v20 > v50 else "역배열"

    out = {"ok": True, "daily_state": d_state, "daily_signal": d_sig,
           "align": align, "ema20": round(v20, 2), "ema50": round(v50, 2),
           "dist20_pct": round((cur - v20) / v20 * 100, 2)}

    # 주봉 20EMA (문서: 주봉 EMA 붕괴 = 사이클 전환)
    if weekly is not None and len(weekly) >= 22:
        wc = weekly["close"]
        w20 = _ema(wc, 20)
        wcur, wv = float(wc.iloc[-1]), float(w20.iloc[-1])
        w_ok = wcur > wv
        out["weekly_state"] = ("주봉 20EMA 지지 유지" if w_ok
                               else "주봉 20EMA 붕괴 (사이클 전환 경계)")
        out["weekly_signal"] = 1 if w_ok else -1
        out["w_ema20"] = round(wv, 2)
    return out


# ----------------------------------------------------------------------
# 3. 거래량 성격 (하락 시 거래량 급증 = 꺾임)
# ----------------------------------------------------------------------
def analyze_volume_character(df: pd.DataFrame, win: int = 20) -> dict:
    if df is None or len(df) < win + 5:
        return {"ok": False}
    d = df.tail(win)
    up = d["close"] >= d["open"]
    v_up = d.loc[up, "volume"].mean() if up.any() else 0.0
    v_dn = d.loc[~up, "volume"].mean() if (~up).any() else 0.0
    if not v_up or not v_dn:
        return {"ok": False}
    ratio = v_dn / v_up
    if ratio >= 1.25:
        state, sig = "하락일 거래량 급증 — 매도 우위(꺾임 경계)", -1
    elif ratio <= 0.85:
        state, sig = "하락일 거래량 위축 — 매물 소화(조정 성격)", 1
    else:
        state, sig = "상승·하락 거래량 균형", 0
    return {"ok": True, "state": state, "signal": sig,
            "down_up_ratio": round(float(ratio), 2)}


# ----------------------------------------------------------------------
# 4. 피보나치 되돌림 (0.382~0.5 지지=조정 / 0.618 이탈=꺾임)
# ----------------------------------------------------------------------
def analyze_fibonacci(df: pd.DataFrame, lookback: int = 120,
                      order: int = 3) -> dict:
    """최근 '스윙 구간' 기준 되돌림 판정.

    전체 구간(고점~저점)을 쓰면 장기 하락 후 바닥 반등 중인 종목이
    항상 '0.618 이탈'로 잡히는 오판이 생긴다.
    → 마지막 스윙 저점 이후 형성된 고점을 기준으로 상승레그를 잡고,
      그 레그의 되돌림 위치를 본다. 상승레그가 없으면 하락레그의 반등률을 본다.
    """
    if df is None or len(df) < 30:
        return {"ok": False}
    d = df.tail(lookback)
    highs = d["high"].to_numpy(dtype=float)
    lows = d["low"].to_numpy(dtype=float)
    cur = float(d["close"].iloc[-1])

    lo_idx = _pivots(lows, order, "low")
    if not lo_idx:
        return {"ok": False}
    li = lo_idx[-1]
    leg_low = float(lows[li])
    # 그 저점 이후 최고가
    after = highs[li:]
    if len(after) < 2:
        return {"ok": False}
    leg_high = float(after.max())

    if leg_high <= leg_low * 1.02:
        return {"ok": False}      # 유의미한 레그 없음

    rng = leg_high - leg_low
    # 되돌림 비율: 레그 고점에서 얼마나 내려왔는가
    retrace = (leg_high - cur) / rng
    lv382 = leg_high - rng * 0.382
    lv5 = leg_high - rng * 0.5
    lv618 = leg_high - rng * 0.618

    if retrace <= 0.382:
        state, sig = "0.382 이내 되돌림 — 강세 유지", 1
    elif retrace <= 0.5:
        state, sig = "0.382~0.5 되돌림 — 정상 조정", 1
    elif retrace <= 0.618:
        state, sig = "0.5~0.618 되돌림 — 지지 확인 필요", 0
    else:
        state, sig = "0.618 하향 이탈 — 상승레그 훼손", -1

    return {"ok": True, "state": state, "signal": sig,
            "retrace_pct": round(retrace * 100, 1),
            "leg_low": round(leg_low, 2), "leg_high": round(leg_high, 2),
            "fib_382": round(lv382, 2), "fib_5": round(lv5, 2),
            "fib_618": round(lv618, 2)}


# ----------------------------------------------------------------------
# 5. 다이버전스 (스토캐 3구간 통합) — 차트와 동일 파라미터
# ----------------------------------------------------------------------
STOCH_SETS = [("단기", 5, 3, 3), ("중기", 10, 5, 5), ("장기", 20, 10, 10)]


def detect_divergence_multi(df: pd.DataFrame, max_bars: int = 90,
                            order: int = 3, max_age: int = 25) -> dict:
    """3개 스토캐 세트 각각에서 다이버전스 탐지.
    chart_maker._detect_div_on 과 동일 로직 → 차트/텍스트 불일치 제거."""
    if df is None or len(df) < 40:
        return {"ok": False, "items": []}
    d = df.tail(max_bars).reset_index(drop=True)
    close = d["close"].to_numpy(dtype=float)
    n = len(close)
    items = []
    for label, kp, ks, dp in STOCH_SETS:
        sk, _ = _stoch(d, kp, ks, dp)
        k = sk.to_numpy(dtype=float)
        found = None
        lows = [i for i in _pivots(close, order, "low") if not np.isnan(k[i])]
        if len(lows) >= 2:
            i1, i2 = lows[-2], lows[-1]
            if (n - 1 - i2) <= max_age and close[i2] < close[i1] and k[i2] > k[i1]:
                found = {"type": "상승", "i1": i1, "i2": i2,
                         "date": str(pd.Timestamp(df.tail(max_bars).index[i2]).date()),
                         "basis": (f"가격 {close[i1]:,.0f}→{close[i2]:,.0f}(하락) vs "
                                   f"%K {k[i1]:.0f}→{k[i2]:.0f}(상승)")}
        if found is None:
            highs = [i for i in _pivots(close, order, "high") if not np.isnan(k[i])]
            if len(highs) >= 2:
                i1, i2 = highs[-2], highs[-1]
                if (n - 1 - i2) <= max_age and close[i2] > close[i1] and k[i2] < k[i1]:
                    found = {"type": "하락", "i1": i1, "i2": i2,
                             "date": str(pd.Timestamp(df.tail(max_bars).index[i2]).date()),
                             "basis": (f"가격 {close[i1]:,.0f}→{close[i2]:,.0f}(상승) vs "
                                       f"%K {k[i1]:.0f}→{k[i2]:.0f}(하락)")}
        if found:
            found["set"] = label
            items.append(found)
    bull = sum(1 for x in items if x["type"] == "상승")
    bear = sum(1 for x in items if x["type"] == "하락")
    return {"ok": True, "items": items, "bull": bull, "bear": bear}


# ----------------------------------------------------------------------
# 6. 쌍바닥 / 쌍봉
# ----------------------------------------------------------------------
def detect_double_patterns(df: pd.DataFrame, lookback: int = 80,
                           order: int = 3, tol_pct: float = 3.0,
                           max_age: int = 20) -> dict:
    if df is None or len(df) < 30:
        return {"ok": False, "items": []}
    d = df.tail(lookback)
    close = d["close"].to_numpy(dtype=float)
    n = len(close)
    items = []

    lows = _pivots(close, order, "low")
    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        v1, v2 = close[i1], close[i2]
        if (n - 1 - i2) <= max_age and abs(v1 - v2) / max(v1, v2) * 100 <= tol_pct:
            neck = float(close[i1:i2 + 1].max())
            if (neck - min(v1, v2)) / max(v1, v2) * 100 > 2:
                cur = float(close[-1])
                items.append({"kind": "쌍바닥", "signal": 1,
                              "level": round((v1 + v2) / 2, 2),
                              "neck": round(neck, 2),
                              "date": str(pd.Timestamp(d.index[i2]).date()),
                              "broke": cur > neck,
                              "note": ("넥라인 돌파 — 상승 전환 확인"
                                       if cur > neck else
                                       f"넥라인 {neck:,.0f} 돌파 시 상승 확정")})

    highs = _pivots(close, order, "high")
    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        v1, v2 = close[i1], close[i2]
        if (n - 1 - i2) <= max_age and abs(v1 - v2) / max(v1, v2) * 100 <= tol_pct:
            neck = float(close[i1:i2 + 1].min())
            if (max(v1, v2) - neck) / max(v1, v2) * 100 > 2:
                cur = float(close[-1])
                items.append({"kind": "쌍봉", "signal": -1,
                              "level": round((v1 + v2) / 2, 2),
                              "neck": round(neck, 2),
                              "date": str(pd.Timestamp(d.index[i2]).date()),
                              "broke": cur < neck,
                              "note": ("넥라인 이탈 — 하락 확정"
                                       if cur < neck else
                                       f"넥라인 {neck:,.0f} 지지 중 — 이탈 시 하락")})
    return {"ok": True, "items": items}


# ----------------------------------------------------------------------
# 7. 종합 판정
# ----------------------------------------------------------------------

def judge_trend(daily: pd.DataFrame, weekly: pd.DataFrame = None,
                monthly: pd.DataFrame = None, stoch_frames: dict = None) -> dict:
    """문서 체크리스트 기반 종합 판정.
    반환: 조정/꺾임 구분 + 방향 결론 + 근거 표 + 시점별 전망
    """
    ms = analyze_market_structure(daily)
    ema = analyze_ema(daily, weekly)
    vol = analyze_volume_character(daily)
    fib = analyze_fibonacci(daily)
    dv = detect_divergence_multi(daily)
    dbl = detect_double_patterns(daily)

    rows = []      # 체크리스트 근거 표
    score = 0.0

    # (1) 시장구조 — 가중치 최대 (문서 1순위, 1D)
    W_MS = 4.0
    if ms.get("ok"):
        s = ms["signal"] * W_MS
        if ms["signal"] == 0 and ms.get("hl_intact"):
            s = W_MS * 0.4      # HL 유지 = 조정 성격 → 소폭 가점
        score += s
        rows.append({"item": "시장구조 (HH/HL)", "tf": "1D",
                     "state": ms["structure"], "note": ms["note"],
                     "pts": round(s, 1)})
    else:
        rows.append({"item": "시장구조 (HH/HL)", "tf": "1D",
                     "state": "판단불가", "note": "피벗 부족", "pts": 0})

    # (2) EMA 일봉 20/50
    W_EMA = 3.0
    if ema.get("ok"):
        s = ema["daily_signal"] * W_EMA
        score += s
        rows.append({"item": "EMA 20/50", "tf": "1D",
                     "state": ema["daily_state"],
                     "note": f"{ema['align']} · 20EMA 이격 {ema['dist20_pct']:+.1f}%",
                     "pts": round(s, 1)})
        # (3) 주봉 EMA
        if "weekly_signal" in ema:
            sw = ema["weekly_signal"] * 3.0
            score += sw
            rows.append({"item": "주봉 20EMA", "tf": "1W",
                         "state": ema["weekly_state"],
                         "note": f"주봉 20EMA {ema['w_ema20']:,.0f}",
                         "pts": round(sw, 1)})

    # (4) 다이버전스 3구간 통합 — 차트와 동일
    if dv.get("ok"):
        if dv["items"]:
            s = 0.0
            for it in dv["items"]:
                s += 1.6 if it["type"] == "상승" else -1.6
            score += s
            desc = " / ".join(f"{it['set']} {it['type']}DIV[{it['date']}]"
                              for it in dv["items"])
            rows.append({"item": "스토캐 다이버전스", "tf": "1D",
                         "state": f"상승 {dv['bull']} · 하락 {dv['bear']}",
                         "note": desc, "pts": round(s, 1)})
        else:
            rows.append({"item": "스토캐 다이버전스", "tf": "1D",
                         "state": "없음", "note": "3구간 모두 미검출", "pts": 0})

    # (5) 쌍바닥 / 쌍봉
    if dbl.get("items"):
        s = 0.0
        notes = []
        for it in dbl["items"]:
            base = 3.0 if it["kind"] == "쌍바닥" else -3.0
            if it["broke"]:
                base *= 1.4      # 넥라인 확정 시 강화
            s += base
            notes.append(f"{it['kind']}[{it['date']}] {it['note']}")
        score += s
        rows.append({"item": "쌍바닥/쌍봉", "tf": "1D",
                     "state": " · ".join(x["kind"] for x in dbl["items"]),
                     "note": " / ".join(notes), "pts": round(s, 1)})

    # (6) 거래량 성격
    if vol.get("ok"):
        s = vol["signal"] * 1.5
        score += s
        rows.append({"item": "거래량 성격", "tf": "1D", "state": vol["state"],
                     "note": f"하락일/상승일 거래량 비 {vol['down_up_ratio']}",
                     "pts": round(s, 1)})

    # (7) 피보나치
    if fib.get("ok"):
        s = fib["signal"] * 1.5
        score += s
        rows.append({"item": "피보나치 되돌림", "tf": "1D", "state": fib["state"],
                     "note": (f"레그 {fib['leg_low']:,.0f}→{fib['leg_high']:,.0f} · "
                              f"되돌림 {fib['retrace_pct']}% · "
                              f"0.618={fib['fib_618']:,.0f}"),
                     "pts": round(s, 1)})

    # (8) 스토캐 방향 (일봉 중심, 월봉은 참고 수준으로 축소)
    sf = stoch_frames or {}
    DIRV = {"상승": 1, "보합": 0, "하락": -1}
    for fkey, skey, w, tf in [("daily", "장기", 2.0, "1D"),
                              ("daily", "중기", 1.5, "1D"),
                              ("daily", "단기", 0.8, "1D"),
                              ("monthly", "장기", 2.0, "1M")]:
        node = (sf.get(fkey) or {}).get(skey, {})
        if not node.get("ok"):
            continue
        s = DIRV.get(node.get("direction", "보합"), 0) * w
        # 과매도 반등은 가점, 과매수 하락은 감점 (문서: 과매도 후 반등=조정)
        if node.get("zone") == "과매도" and node.get("direction") != "하락":
            s += w * 0.5
        elif node.get("zone") == "과매수" and node.get("direction") == "하락":
            s -= w * 0.3
        score += s
        rows.append({"item": f"스토캐 {skey}", "tf": tf,
                     "state": f"{node.get('direction')} · {node.get('zone')}",
                     "note": f"K{node.get('k')}"
                             + (f" · {node['cross']}" if node.get("cross") else ""),
                     "pts": round(s, 1)})

    # ---------- 조정 vs 꺾임 판정 ----------
    hl_ok = ms.get("ok") and (ms.get("hl") or ms.get("hl_intact"))
    ema_broken = ema.get("ok") and ema.get("daily_signal") == -1
    vol_spike = vol.get("ok") and vol.get("signal") == -1
    fib_broken = fib.get("ok") and fib.get("signal") == -1
    bull_div = dv.get("bull", 0) >= 2
    bear_div = dv.get("bear", 0) >= 2
    has_dbottom = any(i["kind"] == "쌍바닥" for i in dbl.get("items", []))
    has_dtop = any(i["kind"] == "쌍봉" for i in dbl.get("items", []))

    break_cnt = sum([ema_broken, vol_spike, fib_broken, bear_div, not hl_ok])
    if break_cnt >= 3:
        phase = "추세 꺾임"
        phase_note = "HL 붕괴·EMA 동시이탈·거래량 급증 등 전환 신호 다수 — 반등은 기술적 반등으로 접근"
    elif break_cnt <= 1 and (bull_div or has_dbottom or hl_ok):
        phase = "조정 (추세 유지)"
        phase_note = "저점 구조 유지 + 반등 신호 — 추세 훼손 아닌 눌림 성격"
    else:
        phase = "조정/꺾임 경계"
        phase_note = "신호 혼재 — 20EMA 회복과 최근 저점 유지 여부가 분기점"

    # ---------- 서술형 근거 (점수 대신 문장으로) ----------
    def _josa(word: str, has_batchim: str, no_batchim: str) -> str:
        """마지막 글자 받침 유무로 조사 선택."""
        if not word:
            return no_batchim
        ch = word[-1]
        if "가" <= ch <= "힣":
            return has_batchim if (ord(ch) - 0xAC00) % 28 else no_batchim
        return has_batchim if ch.isdigit() or ch.isalpha() else no_batchim

    def _label(r):
        item, state = r["item"], r["state"]
        if item == "스토캐 다이버전스":
            b, be = dv.get("bull", 0), dv.get("bear", 0)
            if b and not be:
                return f"상승 다이버전스 {b}건"
            if be and not b:
                return f"하락 다이버전스 {be}건"
            return f"다이버전스 혼재(상승 {b}·하락 {be})"
        if item.startswith("스토캐 "):
            gu = item.replace("스토캐 ", "")
            head = state.split("·")[0].strip()          # 방향만
            zone = state.split("·")[-1].strip() if "·" in state else ""
            z = f" {zone}" if zone in ("과매수", "과매도") else ""
            return f"{gu} 스토캐 {head}{z}"
        if item == "쌍바닥/쌍봉":
            return state.replace(" · ", "·") + " 형성"
        # 나머지는 상태 앞부분만 (괄호·대시 제거)
        return state.split("(")[0].split("—")[0].strip()

    pos = [_label(r) for r in rows if r.get("pts", 0) > 0]
    neg = [_label(r) for r in rows if r.get("pts", 0) < 0]
    pos = list(dict.fromkeys(pos))[:4]
    neg = list(dict.fromkeys(neg))[:4]

    if pos and neg:
        p_txt, n_txt = " · ".join(pos), " · ".join(neg)
        lean = "상승 가능성이 높으나" if score > 0 else "하락 압력이 우세하나"
        narrative = (f"{p_txt}{_josa(pos[-1], '을', '를')} 근거로 {lean}, "
                     f"{n_txt}{_josa(neg[-1], '은', '는')} 주의가 필요합니다.")
    elif pos:
        p_txt = " · ".join(pos)
        narrative = (f"{p_txt}{_josa(pos[-1], '이', '가')} 우호적으로 정렬돼 "
                     f"상승 쪽 신호가 우세합니다. 다만 신호 편중 시 되돌림에 유의하세요.")
    elif neg:
        n_txt = " · ".join(neg)
        narrative = (f"{n_txt}{_josa(neg[-1], '이', '가')} 겹쳐 하락 쪽 신호가 우세합니다. "
                     f"반등 시에도 추세 회복 확인 전까지는 보수적 접근이 필요합니다.")
    else:
        narrative = "뚜렷한 방향 신호가 부족합니다. 20EMA 회복 여부와 최근 저점 유지가 분기점입니다."

    # ---------- 서술형 결론 (단정 대신 근거 기반) ----------
    pos = sorted([r for r in rows if r.get("pts", 0) > 0],
                 key=lambda r: -r["pts"])
    neg = sorted([r for r in rows if r.get("pts", 0) < 0],
                 key=lambda r: r["pts"])

    def _phrase(r):
        """근거 항목을 짧고 자연스러운 구절로."""
        item, st = r["item"], r["state"]
        # 스토캐 항목
        for k in ["스토캐 장기", "스토캐 중기", "스토캐 단기"]:
            if item.startswith(k):
                tf = "월봉" if r.get("tf") == "1M" else "일봉"
                seg = item.replace("스토캐 ", "")
                dir_ = st.split("·")[0].strip()
                zone = st.split("·")[-1].strip() if "·" in st else ""
                z = f"({zone})" if zone and zone != "중립" else ""
                return f"{tf} {seg} 스토캐 {dir_}{z}"
        if item == "스토캐 다이버전스":
            note = r.get("note", "")
            if "상승" in st and "하락" in st:
                b = st.replace("상승 ", "").split(" · ")
                return f"상승·하락 다이버전스 혼재"
            if st.startswith("상승"):
                n = st.split("·")[0].replace("상승", "").strip()
                return f"상승 다이버전스 {n}건" if n and n != "0" else "상승 다이버전스"
            if "하락" in st:
                return "하락 다이버전스"
            return "다이버전스"
        if item == "거래량 성격":
            return st.split("—")[-1].strip().rstrip(")").replace("(", " ")
        if item == "피보나치 되돌림":
            return st.split("—")[0].strip()
        if item == "쌍바닥/쌍봉":
            return st
        return st

    def _join(items, n=3):
        parts = []
        for r in items[:n]:
            p = _phrase(r)
            if p and p not in parts:
                parts.append(p)
        return ", ".join(parts)

    up_side = score > 0
    lean = ("상승" if up_side else "하락") if abs(score) >= 2 else "방향성 중립"

    pos_txt = _join(pos)
    neg_txt = _join(neg)

    def _cautions(txt):
        return f" 다만 {txt}{_josa(txt, '은', '는')} 주의를 기울여야 합니다." if txt else ""

    if lean == "방향성 중립":
        narrative = "뚜렷한 방향성이 잡히지 않는 구간입니다."
        if pos_txt:
            narrative += f" {pos_txt} 등은 우호적이나,"
        if neg_txt:
            narrative += f" {neg_txt}{_josa(neg_txt, '이', '가')} 상충합니다."
    elif up_side:
        strong = "높아 보입니다" if score >= 6 else "다소 우세합니다"
        narrative = f"{pos_txt} 등을 근거로 상승 가능성이 {strong}.{_cautions(neg_txt)}"
    else:
        strong = "높아 보입니다" if score <= -6 else "다소 우세합니다"
        narrative = f"{neg_txt} 등을 근거로 하락 가능성이 {strong}."
        if pos_txt:
            narrative += f" 다만 {pos_txt}{_josa(pos_txt, '은', '는')} 하방을 제한할 수 있습니다."

    # 특수 케이스 서술 보정
    if has_dbottom and bull_div and hl_ok:
        narrative = ("쌍바닥 형성과 다중 상승 다이버전스, 최근 저점 유지를 근거로 "
                     "바닥 반등 가능성이 높아 보입니다." + _cautions(neg_txt))
        phase = "조정 (추세 유지)"
        phase_note = "쌍바닥 + 다중 상승다이버전스 + 저점 유지 — 반등 초입 가능성"
    elif has_dtop and bear_div and not hl_ok:
        narrative = ("쌍봉과 다중 하락 다이버전스, 저점 붕괴를 근거로 "
                     "천장권 이탈 가능성에 무게가 실립니다.")
        if pos_txt:
            narrative += f" 다만 {pos_txt}{_josa(pos_txt, '은', '는')} 낙폭을 제한할 수 있습니다."
        phase = "추세 꺾임"

    # ---------- 시점별 전망 ----------
    timeline = []
    if ms.get("ok"):
        timeline.append({
            "period": "수일~2주", "label": "단기",
            "dir": ("상승" if (bull_div or has_dbottom) and hl_ok else
                    "하락" if (bear_div or has_dtop) else "보합"),
            "basis": f"{ms['structure']} · 20EMA 이격 {ema.get('dist20_pct', 0):+.1f}%"})
    timeline.append({
        "period": "3~6주", "label": "스윙",
        "dir": ("상승" if ema.get("daily_signal", 0) >= 0 and score > 0 else
                "하락" if ema.get("daily_signal", 0) < 0 and score < 0 else "보합"),
        "basis": ema.get("daily_state", "-")})
    if "weekly_signal" in ema:
        timeline.append({
            "period": "1~3개월", "label": "중기(주봉)",
            "dir": "상승" if ema["weekly_signal"] > 0 else "하락",
            "basis": ema["weekly_state"]})
    mon = (sf.get("monthly") or {}).get("장기", {})
    if mon.get("ok"):
        timeline.append({
            "period": "3~6개월", "label": "대세(월봉)",
            "dir": mon.get("direction", "보합"),
            "basis": f"월봉 장기 스토캐 K{mon.get('k')} · {mon.get('zone')}"})

    # ---------- 대응 가이드 ----------
    if phase == "조정 (추세 유지)":
        action = ("눌림목 분할 매수 유효. 최근 저점"
                  + (f" {ms['last_low']:,.0f}" if ms.get("ok") else "")
                  + " 이탈 시 손절.")
    elif phase == "추세 꺾임":
        action = "신규 매수 보류. 보유분은 반등 시 비중 축소, 20EMA 회복 전까지 관망."
    else:
        action = ("관망 우위. 20EMA"
                  + (f"({ema['ema20']:,.0f})" if ema.get("ok") else "")
                  + " 회복 시 매수 전환, 최근 저점 이탈 시 손절.")

    return {
        "ok": True,
        "score": round(score, 1),      # 내부 판단용 (화면 미표시)
        "lean": lean,
        "vcolor": ("up" if lean == "상승" else
                   "down" if lean == "하락" else "flat"),
        "phase": phase,
        "phase_note": phase_note,
        "narrative": narrative,
        "positives": pos,
        "cautions": neg,
        "action": action,
        "rows": rows,
        "timeline": timeline,
        "structure": ms,
        "ema": ema,
        "volume": vol,
        "fib": fib,
        "divergence": dv,
        "double": dbl,
    }
