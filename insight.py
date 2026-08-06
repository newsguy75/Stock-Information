# -*- coding: utf-8 -*-
"""
insight.py (v2)
===============
메인 3종(스토캐 다이버전스 · 거래량 · 5/20일선)을 큰 가중으로,
수급/배열/상위프레임 등은 보조로 종합 판정.

[매수우위 / 관망 / 비중축소] + 점수(-100~100) + 근거 + (선택) Claude API 코멘트.
"""
from __future__ import annotations
import os, json
from dataclasses import dataclass, field
import requests

from signals import MASignal, VolumeSignal
from supply_demand import SupplySignal
from stoch_frames import StochFrame


@dataclass
class Verdict:
    stance: str
    score: int
    main_reasons: list = field(default_factory=list)   # 메인 근거
    sub_reasons: list = field(default_factory=list)     # 보조 근거
    comment: str = ""


def score_stock(d_ma: MASignal, w_ma, m_ma, vol: VolumeSignal,
                sup: SupplySignal,
                stoch_1h: StochFrame, stoch_d: StochFrame, stoch_m: StochFrame) -> Verdict:
    score = 0
    main, sub = [], []

    # ================= 메인 1: 스토캐 다이버전스 (프레임별) =================
    # 월봉>일봉>1시간 순으로 신뢰 가중 (느린 프레임일수록 반전 의미 큼)
    div_weight = {"월봉": 20, "일봉": 22, "1시간": 12}
    for sf in (stoch_m, stoch_d, stoch_1h):
        if not sf.ok:
            continue
        w = div_weight.get(sf.frame, 12)
        if sf.divergence == "상승":
            score += w; main.append(f"{sf.frame} 상승다이버전스")
        elif sf.divergence == "하락":
            score -= w; main.append(f"{sf.frame} 하락다이버전스")
        # 과매도권 상승전환 / 과매수권 하락전환도 메인 보강
        if sf.turn == "상승전환" and sf.turn_from_oversold:
            score += 6; main.append(f"{sf.frame} 과매도권 상승전환")
        elif sf.turn == "하락전환" and sf.turn_from_overbought:
            score -= 6; main.append(f"{sf.frame} 과매수권 하락전환")

    # ================= 메인 2: 5/20일선 =================
    if d_ma.cross_5_20 == "골든크로스":
        score += 18; main.append("일봉 5·20 골든크로스")
    elif d_ma.cross_5_20 == "데드크로스":
        score -= 18; main.append("일봉 5·20 데드크로스")
    if d_ma.ma5_direction == "상향":
        score += 8; main.append("MA5 상향")
    elif d_ma.ma5_direction == "하향":
        score -= 8; main.append("MA5 하향")
    # 종가 vs MA20 위치
    if d_ma.above_ma5:  # (MASignal에는 above_ma5만 있음; 20선 위치는 배열로 근사)
        pass
    if d_ma.above_ma5 and d_ma.ma5_touch:
        score += 6; main.append("5일선 지지터치(눌림목)")

    # ================= 메인 3: 거래량 =================
    if vol.over_vol_ma:
        if score >= 0:
            score += 16; main.append(f"거래량 5일선 돌파 x{vol.ratio:.1f}(상승동반)")
        else:
            score -= 8; main.append(f"거래량 5일선 돌파 x{vol.ratio:.1f}(하락동반 주의)")

    # ================= 보조: 수급 =================
    if sup.ok:
        if sup.foreign_trend == "유입":
            score += 8; sub.append(f"외국인 유입(5일 +{sup.foreign_5d:.0f}억)")
        elif sup.foreign_trend == "유출":
            score -= 8; sub.append(f"외국인 유출(5일 {sup.foreign_5d:.0f}억)")
        if sup.inst_trend == "유입":
            score += 5; sub.append("기관 유입")
        elif sup.inst_trend == "유출":
            score -= 5; sub.append("기관 유출")
        if sup.foreign_streak >= 3:
            score += 3; sub.append(f"외국인 {sup.foreign_streak}일 연속매수")
        elif sup.foreign_streak <= -3:
            score -= 3; sub.append(f"외국인 {-sup.foreign_streak}일 연속매도")

    # ================= 보조: 배열/상위프레임 =================
    if d_ma.alignment == "정배열":
        score += 6; sub.append("일봉 정배열")
    elif d_ma.alignment == "역배열":
        score -= 6; sub.append("일봉 역배열")
    if w_ma and w_ma.alignment == "정배열":
        score += 4; sub.append("주봉 정배열")
    elif w_ma and w_ma.alignment == "역배열":
        score -= 4; sub.append("주봉 역배열")
    if m_ma and m_ma.alignment == "정배열":
        score += 3
    elif m_ma and m_ma.alignment == "역배열":
        score -= 3

    score = max(-100, min(100, score))
    if score >= 25:
        stance = "매수우위"
    elif score <= -20:
        stance = "비중축소"
    else:
        stance = "관망"
    return Verdict(stance=stance, score=score, main_reasons=main, sub_reasons=sub)


def add_api_comment(name: str, verdict: Verdict, price_change: float,
                    supply_summary: str, stoch_summary: str) -> Verdict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    fallback = f"[{verdict.stance}] " + ", ".join(verdict.main_reasons[:4])
    if not api_key:
        verdict.comment = fallback
        return verdict
    prompt = (
        f"한국주식 '{name}' 오늘 시그널. 등락률 {price_change:+.1f}%. "
        f"종합 {verdict.stance}(점수 {verdict.score}). "
        f"메인(스토캐 다이버전스·거래량·5/20일선): {', '.join(verdict.main_reasons)}. "
        f"스토캐 프레임별: {stoch_summary}. 보조(수급 등): {', '.join(verdict.sub_reasons)}. "
        f"단타·스윙 트레이더용 한 문장(90자 이내) 한국어 코멘트. "
        f"메인 지표를 우선 언급하고, 매수/매도 단정 대신 '~구간/~주목/~유의' 관찰형으로. 예측 금지."
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            data=json.dumps({"model": "claude-sonnet-4-6", "max_tokens": 200,
                             "messages": [{"role": "user", "content": prompt}]}),
            timeout=20)
        r.raise_for_status()
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
        verdict.comment = text or fallback
    except Exception:
        verdict.comment = fallback + " (API실패)"
    return verdict


if __name__ == "__main__":
    from signals import MASignal
    from stoch_frames import StochFrame
    d = MASignal(100, 95, 90, "상향", 1.2, "정배열", True, True, "골든크로스", None, None)
    v = VolumeSignal(200, 150, True, 1.33)
    s = SupplySignal(45, 12, -30, 120, 40, 4, 2, 8, 3, "유입", "유입", ok=True)
    sd = StochFrame("일봉", 25, 22, "중립", "상승전환", True, False, "상승", ok=True)
    sm = StochFrame("월봉", 40, 38, "중립", "없음", False, False, "상승", ok=True)
    sh = StochFrame("1시간", 55, 50, "중립", "없음", False, False, "없음", ok=True)
    verdict = score_stock(d, None, None, v, s, sh, sd, sm)
    verdict = add_api_comment("테스트", verdict, 2.3, "외국인 유입", "일봉 상승다이버전스")
    print("판정:", verdict.stance, verdict.score)
    print("메인:", verdict.main_reasons)
    print("보조:", verdict.sub_reasons)
    print("코멘트:", verdict.comment)
