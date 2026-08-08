# -*- coding: utf-8 -*-
"""
report_writer.py
================
analyze_stock() 결과를 data/ 폴더에 JSON + HTML로 저장.

구조:
  data/
  ├── 2026-08-07/
  │   ├── 023160_TK-Corporation_0542.json
  │   ├── 023160_TK-Corporation_0542.html
  │   └── index.html                       # 그날 전체 종목 인덱스
  └── latest/
      └── ... (당일 최신본 복사)

색상 규칙(한국식): 상승/매수/골든 = 빨강, 하락/매도/데드 = 파랑.
"""
from __future__ import annotations
import os
import json
import shutil
import datetime as dt
import html as html_lib


DATA_ROOT = os.environ.get("DATA_ROOT", "data")

# 한국식 색상
C_UP = "#d33"      # 빨강 (상승/매수)
C_DOWN = "#1a56db"  # 파랑 (하락/매도)
C_FLAT = "#666"
C_BG = "#0f1115"
C_CARD = "#1a1d24"
C_TEXT = "#e6e8ec"
C_SUB = "#9aa0aa"
C_LINE = "#2a2e37"


def _dir_color(word: str | None) -> str:
    if not word:
        return C_FLAT
    if any(w in word for w in ["상승", "매수", "골든", "정배열", "과매도"]):
        return C_UP
    if any(w in word for w in ["하락", "매도", "데드", "역배열", "과매수", "주의", "관망"]):
        return C_DOWN
    return C_FLAT


# ----------------------------------------------------------------------
# 저장 경로
# ----------------------------------------------------------------------
def _paths(name: str, code: str, now: dt.datetime) -> tuple[str, str, str, str]:
    day = now.strftime("%Y-%m-%d")
    hm = now.strftime("%H%M")
    safe = name.replace(" ", "-").replace("/", "-")
    day_dir = os.path.join(DATA_ROOT, day)
    os.makedirs(day_dir, exist_ok=True)
    stem = f"{code}_{safe}_{hm}"
    return day_dir, os.path.join(day_dir, stem + ".json"), os.path.join(day_dir, stem + ".html"), stem


# ----------------------------------------------------------------------
# JSON 저장
# ----------------------------------------------------------------------
def save_json(analysis: dict, now: dt.datetime) -> str:
    _, jpath, _, _ = _paths(analysis["name"], analysis["code"], now)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    return jpath


# ----------------------------------------------------------------------
# HTML 조립
# ----------------------------------------------------------------------
def _sd(label: str, node: dict) -> str:
    """스토캐 프레임 한 칸."""
    if not node.get("ok"):
        return f'<span class="chip na">{label} N/A</span>'
    col = _dir_color(node["direction"])
    cross = f' · {node["cross"]}' if node.get("cross") else ""
    return (f'<span class="chip" style="border-color:{col}">'
            f'<b>{label}</b> <span style="color:{col}">{node["direction"]}</span> '
            f'<span class="sub">({node["zone"]} K{node["k"]}{cross})</span></span>')


def _section(title: str, inner: str) -> str:
    return f'<div class="sec"><div class="sec-h">{title}</div>{inner}</div>'


def _section_if(title: str, inner: str, has_content: bool = True) -> str:
    """내용이 없으면 섹션 자체를 숨김."""
    if not has_content or not inner or not inner.strip():
        return ""
    return _section(title, inner)


def _fold(label: str, detail: str, open_: bool = False) -> str:
    """클릭하면 펼쳐지는 상세 블록. 기본은 접힘.
    JS 없이 <details>로 구현 — iframe 안에서도 동작."""
    if not detail or not detail.strip():
        return ""
    op = " open" if open_ else ""
    return (f'<details class="fold"{op}>'
            f'<summary class="fold-s">{label}</summary>'
            f'<div class="fold-b">{detail}</div></details>')


def build_html(analysis: dict) -> str:
    a = analysis
    chg = a["change_pct"]
    chg_col = C_UP if chg >= 0 else C_DOWN
    esc = html_lib.escape

    # 2-B. 하락 경고 배너 (최상단, 최우선 강조)
    bear = a.get("bear_warnings", {})
    bear_html = ""
    if bear.get("has_warning"):
        w_rows = []
        for w in bear["warnings"]:
            lv = w["level"]
            badge_col = "#c0182b" if lv == "높음" else "#c77a0a"
            w_rows.append(
                f'<div class="bear-row">'
                f'<span class="bear-badge" style="background:{badge_col}">{esc(w["kind"])}</span>'
                f'<span class="bear-desc">{esc(w["desc"])}</span></div>')
        bear_html = (
            '<div class="bear-banner">'
            '<div class="bear-title">🚨 하락 경고 신호 (일봉 기준)</div>'
            + "".join(w_rows) + '</div>')

    # 0. 2프레임 차트 (일 / 월) - 세로 2줄, 가득 크게
    charts = a.get("charts", {})
    chart_imgs = []
    for key, label in [("daily", "일봉"), ("monthly", "월봉")]:
        uri = charts.get(key)
        if uri:
            chart_imgs.append(f'<div class="chart-full"><img src="{uri}" alt="{label}"></div>')
        else:
            chart_imgs.append(f'<div class="chart-full na-chart">{label}<br><span class="sub">데이터 없음</span></div>')
    chart_html = _section("1. 차트 (일 · 월 / 캔들+MA5·20·60+거래량+스토캐)",
                          f'<div class="charts-vert">{"".join(chart_imgs)}</div>')

    # 2. 통합 방향성 예측 (다이버전스 + 스토캐 3구간 합산 → 하나의 결론)
    fc = a.get("forecast", {})
    s = a["stoch_frames"]
    if not fc:
        # analysis_engine이 구버전이라 forecast가 없을 때 즉석 계산 (파일 부분교체 대비)
        try:
            from analysis_engine import build_integrated_forecast
            fc = build_integrated_forecast(a.get("divergence", {}), s, a.get("ma"))
        except Exception as e:
            print(f"[warn] forecast 즉석계산 실패: {e}")
            fc = {}
    if fc.get("ok") and fc.get("rows") is not None:
        # ---- 신규 엔진 (trend_judge): 조정 vs 꺾임 ----
        phase = fc.get("phase", "")
        pcol = (C_UP if "조정" in phase and "꺾임" not in phase
                else C_DOWN if "꺾임" in phase else C_SUB)
        vcol = {"up": C_UP, "down": C_DOWN}.get(fc.get("vcolor"), C_SUB)

        fc_body = (
            f'<div class="fc-verdict" style="border-color:{pcol}">'
            f'<div class="fc-v" style="color:{pcol}">{esc(phase)}</div>'
            f'<div class="fc-narr">{esc(fc.get("narrative",""))}</div>'
            f'<div class="fc-s" style="margin-top:6px">{esc(fc.get("phase_note",""))}</div>'
            f'</div>'
        )

        # 우호 / 주의 요인
        pos, neg = fc.get("positives") or [], fc.get("cautions") or []

        def _fac_txt(x):
            if isinstance(x, dict):
                tf = f' <span class="sub" style="font-size:11px">{esc(x.get("tf",""))}</span>'
                return f'{esc(x.get("item",""))} — {esc(x.get("state",""))}{tf}'
            return esc(str(x))

        if pos or neg:
            cols = ''
            if pos:
                cols += ('<div class="fac"><div class="fac-h" style="color:' + C_UP + '">▲ 우호 요인</div>'
                         + "".join(f'<div class="fac-i">{_fac_txt(x)}</div>' for x in pos) + '</div>')
            if neg:
                cols += ('<div class="fac"><div class="fac-h" style="color:' + C_DOWN + '">▼ 주의 요인</div>'
                         + "".join(f'<div class="fac-i">{_fac_txt(x)}</div>' for x in neg) + '</div>')
            fc_body += f'<div class="fac-wrap">{cols}</div>'

        # 대응 가이드
        if fc.get("action"):
            fc_body += f'<div class="fc-note">🎯 {esc(fc["action"])}</div>'

        # 시점별 전망
        tl_rows = []
        for t in fc.get("timeline", []):
            dcol = _dir_color(t["dir"])
            arrow = {"상승": "↗", "하락": "↘", "보합": "→"}.get(t["dir"], "→")
            tl_rows.append(
                f'<tr><td class="hz-p">{esc(t["period"])}</td>'
                f'<td class="hz-l">{esc(t["label"])}</td>'
                f'<td class="hz-d" style="color:{dcol}"><b>{arrow} {esc(t["dir"])}</b></td>'
                f'<td class="hz-x sub">{esc(t["basis"])}</td></tr>')
        if tl_rows:
            fc_body += _fold('🕒 시점별 전망 보기',
                        '<table class="hz-tbl"><thead><tr>'
                        '<th>시점</th><th>구간</th><th>방향</th><th>근거</th>'
                        '</tr></thead><tbody>' + "".join(tl_rows) + '</tbody></table>')

        # 판단 근거 체크리스트 (점수 대신 방향 마커)
        ck_rows = []
        for r in fc.get("rows", []):
            p = r.get("pts", 0)
            if p > 0:
                mk, mcol = "▲", C_UP
            elif p < 0:
                mk, mcol = "▼", C_DOWN
            else:
                mk, mcol = "–", C_SUB
            ck_rows.append(
                f'<tr><td style="padding:5px 8px;width:22px;text-align:center;'
                f'color:{mcol};font-weight:700">{mk}</td>'
                f'<td style="padding:5px 8px;font-weight:600">{esc(r["item"])}'
                f'<span class="sub" style="font-size:11px"> {esc(r.get("tf",""))}</span></td>'
                f'<td style="padding:5px 8px">{esc(r["state"])}</td>'
                f'<td style="padding:5px 8px" class="sub">{esc(r.get("note",""))}</td></tr>')
        if ck_rows:
            fc_body += _fold('✅ 자세한 판단 근거 보기 (조정 vs 꺾임 체크리스트)',
                        '<table class="hz-tbl"><thead><tr>'
                        '<th></th><th>기준</th><th>상태</th><th>비고</th>'
                        '</tr></thead><tbody>' + "".join(ck_rows) + '</tbody></table>')

        # 스토캐 3구간 차트
        st_uri = (a.get("charts") or {}).get("stoch_triple")
        if st_uri:
            fc_body += _fold('📈 스토캐 3구간 차트 보기 (다이버전스 · 쌍바닥/쌍봉)',
                        f'<div class="chart-full"><img src="{st_uri}" alt="스토캐 3구간"></div>')

        div_html = _section("2. 추세 판정 — 조정 vs 꺾임 (시장구조·EMA·다이버전스·거래량·피보나치 종합)", fc_body)

    elif fc.get("ok"):
        vcol = {"up": C_UP, "down": C_DOWN}.get(fc.get("vcolor"), C_SUB)

        # (a) 최종 결론 배너
        fc_body = (
            f'<div class="fc-verdict" style="border-color:{vcol}">'
            f'<div class="fc-v" style="color:{vcol}">{esc(fc["verdict"])}</div>'
            f'<div class="fc-s">종합 점수 {fc["score"]:+.1f}'
            + (f' · 대세(월봉 장기) <b style="color:{_dir_color(fc["main_trend"])}">{esc(fc["main_trend"])}</b>'
               if fc.get("main_trend") else "") +
            '</div></div>'
        )

        # (b) 시점별 방향 타임라인
        hz_rows = []
        for h in fc.get("horizons", []):
            if h["direction"] == "판단불가":
                hz_rows.append(
                    f'<tr><td class="hz-p">{esc(h["period"])}</td>'
                    f'<td class="hz-l">{esc(h["label"])}</td>'
                    f'<td class="hz-d na">판단불가</td>'
                    f'<td class="hz-x sub">{esc(h["detail"])}</td></tr>')
                continue
            dcol = _dir_color(h["direction"])
            arrow = {"상승": "↗", "하락": "↘", "보합": "→"}.get(h["direction"], "→")
            hz_rows.append(
                f'<tr><td class="hz-p">{esc(h["period"])}</td>'
                f'<td class="hz-l">{esc(h["label"])}</td>'
                f'<td class="hz-d" style="color:{dcol}"><b>{arrow} {esc(h["direction"])}</b></td>'
                f'<td class="hz-x sub">{esc(h["detail"])}</td></tr>')
        fc_body += (
            '<table class="hz-tbl"><thead><tr>'
            '<th>시점</th><th>구간</th><th>방향</th><th>근거</th>'
            '</tr></thead><tbody>' + "".join(hz_rows) + '</tbody></table>'
        )

        # (c) 다이버전스 선행신호
        if fc.get("div_notes"):
            dn = "".join(f'<li>{esc(n)}</li>' for n in fc["div_notes"])
            fc_body += f'<div class="fc-sub">📐 다이버전스 (전환 선행신호)</div><ul class="verdict-list">{dn}</ul>'
        else:
            fc_body += '<div class="fc-sub">📐 다이버전스 <span class="sub">— 일봉·월봉 모두 없음</span></div>'

        # (d) 대세 필터 / 3단 조건
        if fc.get("filter_note"):
            fc_body += f'<div class="fc-note">🧭 {esc(fc["filter_note"])}</div>'
        if fc.get("setup"):
            fc_body += f'<div class="fc-note">{esc(fc["setup"])}</div>'

        # (e) 스토캐 3종 차트 (일봉 기준, 다이버전스·쌍바닥/쌍봉 표시)
        st_uri = (a.get("charts") or {}).get("stoch_triple")
        if st_uri:
            fc_body += ('<div class="fc-sub">📈 일봉 스토캐 3구간 차트 (다이버전스 · 쌍바닥/쌍봉)</div>'
                        f'<div class="chart-full"><img src="{st_uri}" alt="스토캐 3구간"></div>')

        # (f) 원자료 칩 (일봉·월봉 3구간)
        fc_body += (
            '<div class="fc-sub">📊 원자료 — 스토캐 3구간</div>'
            f'<div class="frame"><div class="frame-n">일봉</div><div class="chips">'
            + _sd("장기", s["daily"].get("장기", {})) + _sd("중기", s["daily"].get("중기", {})) + _sd("단기", s["daily"].get("단기", {}))
            + '</div></div>'
            f'<div class="frame"><div class="frame-n">월봉</div><div class="chips">'
            + _sd("장기", s["monthly"].get("장기", {})) + _sd("중기", s["monthly"].get("중기", {})) + _sd("단기", s["monthly"].get("단기", {}))
            + '</div></div>'
        )
        div_html = _section("2. 통합 방향성 예측 (다이버전스 + 스토캐 3구간 → 시점별 전망)", fc_body)
    else:
        div_html = _section("2. 통합 방향성 예측", '<div class="row"><span class="v na">데이터 부족</span></div>')

    stoch_html = ""   # 3번은 2번에 통합됨

    # 4. 이평선 (5/20/60 + 방향예측)
    ma = a["ma"]
    if ma.get("ok"):
        ma60_txt = f' / 60일 {ma["ma60"]:,}' if ma.get("ma60") else ' / 60일 미형성'
        ma_inner = (
            f'<div class="row"><span class="k">현재가/이평</span><span class="v">'
            f'{ma["price"]:,}원 · {ma["position"]} '
            f'<span class="sub">(5일 {ma["ma5"]:,} / 20일 {ma["ma20"]:,}{ma60_txt})</span></span></div>'
            f'<div class="row"><span class="k">배열</span><span class="v" style="color:{_dir_color(ma["state"])}">{ma["state"]} <span class="sub">(5·20갭 {ma["gap_pct"]:+.2f}%)</span></span></div>'
        )
        # 5·20일선 방향 예측 (1~3일)
        def fc_line(label, fc):
            if not fc or not fc.get("ok"):
                return ""
            col = _dir_color(fc["direction"])
            proj = fc.get("proj", {})
            proj_txt = " · ".join(f'{k} {v:,}' for k, v in proj.items())
            return (f'<div class="row"><span class="k">{label} 예측</span>'
                    f'<span class="v"><span style="color:{col}">{fc["direction"]}</span> '
                    f'<span class="sub">({fc["slope_pct"]:+.2f}%/일) → {proj_txt}</span></span></div>')
        ma_inner += fc_line("5일선", ma.get("ma5_forecast"))
        ma_inner += fc_line("20일선", ma.get("ma20_forecast"))
        if ma.get("forecast"):
            ma_inner += f'<div class="row"><span class="k">크로스</span><span class="v" style="color:{_dir_color(ma["forecast"])}">{esc(ma["forecast"])}</span></div>'
        if ma.get("forecast60"):
            ma_inner += f'<div class="row"><span class="k">중기크로스</span><span class="v" style="color:{_dir_color(ma["forecast60"])}">{esc(ma["forecast60"])}</span></div>'
        if ma.get("note"):
            ma_inner += f'<div class="row"><span class="k">비고</span><span class="v sub">{esc(ma["note"])}</span></div>'
        # 핵심 한 줄 + 상세 접기
        ma_head = (
            f'<div class="hl"><b style="color:{_dir_color(ma["state"])}">{esc(ma["state"])}</b>'
            f' · 현재가 {ma["price"]:,}원, {esc(ma["position"])}'
            + (f' · {esc(ma["forecast"])}' if ma.get("forecast") else '')
            + '</div>')
        ma_inner = ma_head + _fold("📐 이평선 상세 보기", ma_inner)
    else:
        ma_inner = ""
    ma_html = _section_if("4. 이평선 분석 (5·20·60일선 + 방향예측)", ma_inner)

    # 4-B. 눌림목 매수
    pb = a.get("pullback", {})
    if pb.get("ok") and pb.get("has_signal"):
        pb_rows = []
        for s in pb["signals"]:
            pb_rows.append(
                f'<div class="row"><span class="k" style="color:{C_UP}">{s["line"]} 눌림</span>'
                f'<span class="v"><b style="color:{C_UP}">{s["type"]}</b><br>'
                f'<span class="sub">{esc(s["desc"])}</span><br>'
                f'<span class="sub">진입 참고 {s["level"]:,} · '
                f'<span style="color:{C_DOWN}">손절 {s["stop"]:,}</span></span></span></div>')
        conf_col = C_UP if pb["confidence"] == "상" else (C_DOWN if pb["confidence"] == "하" else C_SUB)
        pb_inner = "".join(pb_rows)
        pb_inner += (f'<div class="row"><span class="k">신뢰도</span>'
                     f'<span class="v" style="color:{conf_col}">{pb["confidence"]}</span></div>')
        if pb.get("opinion"):
            pb_inner += f'<div class="row"><span class="k">기술적 의견</span><span class="v sub">{esc(pb["opinion"])}</span></div>'
        pb_head = (f'<div class="hl">눌림 신호 {len(pb["signals"])}건 · '
                   f'신뢰도 <b style="color:{conf_col}">{pb["confidence"]}</b></div>')
        pb_inner = pb_head + _fold("🎯 눌림목 진입·손절 상세 보기", pb_inner)
    else:
        # 신호 없으면 섹션 자체를 숨김 (인사이트 없음)
        pb_inner = ""
    pb_html = _section_if("4-B. 눌림목 매수 (5·10·20일선 지지터치 + 손절가)", pb_inner)

    # 5. 수급 (5·20·60일 + 비중)
    sup = a["supply_demand"]
    if sup.get("ok"):
        def money(v):
            col = C_UP if v >= 0 else C_DOWN
            # 억원 단위 축약
            eok = v / 1e8
            if abs(eok) >= 1:
                txt = f'{eok:+,.0f}억'
            else:
                txt = f'{v:+,.0f}'
            return f'<b style="color:{col}">{txt}</b>'
        fo, ins, ind = sup["foreign"], sup["inst"], sup["indiv"]
        rt = sup.get("ratio", {})
        sup_inner = (
            f'<div class="row"><span class="k">요약</span><span class="v">{esc(sup["summary"])}</span></div>'
            f'<div class="row"><span class="k">외인</span><span class="v">5일 {money(fo["d5"])} / 20일 {money(fo["d20"])} / 60일 {money(fo["d60"])} <span class="sub">(비중 {rt.get("foreign","?")}%)</span></span></div>'
            f'<div class="row"><span class="k">기관</span><span class="v">5일 {money(ins["d5"])} / 20일 {money(ins["d20"])} / 60일 {money(ins["d60"])} <span class="sub">(비중 {rt.get("inst","?")}%)</span></span></div>'
            f'<div class="row"><span class="k">개인</span><span class="v">5일 {money(ind["d5"])} / 20일 {money(ind["d20"])} / 60일 {money(ind["d60"])} <span class="sub">(비중 {rt.get("indiv","?")}%)</span></span></div>'
            f'<div class="row"><span class="k">수급주도</span><span class="v">단기(5일) <b>{sup.get("main_5d","?")}</b> / 중기(20일) <b>{sup.get("main_20d","?")}</b></span></div>'
        )
    else:
        # 지수인 경우: 시장 폭(상승/하락/상하한) + 투자자 매매 표시
        mb = sup.get("market_breadth", {}) if isinstance(sup.get("market_breadth"), dict) else {}
        infl = sup.get("investor_flow", {}) if isinstance(sup.get("investor_flow"), dict) else {}
        parts = []
        if mb.get("ok"):
            u, dn, fl = mb["up"], mb["down"], mb["flat"]
            ul, ll = mb["upper_limit"], mb["lower_limit"]
            src_tag = ("<span class='sub'>· 장중 실시간</span>"
                       if mb.get("source") == "naver_crawl" else
                       "<span class='sub'>· 확정치</span>")
            parts.append(
                f'<div class="row"><span class="k">시장 폭</span>'
                f'<span class="v">'
                f'<b style="color:{C_UP}">▲{u:,}</b> · '
                f'<b style="color:{C_DOWN}">▼{dn:,}</b> · '
                f'<span class="sub">보합 {fl:,}</span> '
                f'<span class="sub">(상승비율 {mb["up_ratio"]}%)</span> {src_tag}</span></div>'
                f'<div class="row"><span class="k">상하한가</span>'
                f'<span class="v">'
                f'상한 <b style="color:{C_UP}">{ul}</b> · '
                f'하한 <b style="color:{C_DOWN}">{ll}</b></span></div>'
            )
        else:
            parts.append(f'<div class="row"><span class="v na">시장 폭 데이터 없음{" ("+esc(mb.get("err",""))+")" if mb.get("err") else ""}</span></div>')

        if infl.get("ok"):
            def money_ix(v):
                col = C_UP if v > 0 else (C_DOWN if v < 0 else C_SUB)
                eok = v / 1e8
                txt = f'{eok:+,.0f}억' if abs(eok) >= 1 else f'{v:+,.0f}'
                return f'<b style="color:{col}">{txt}</b>'
            src_tag2 = ("<span class='sub'>· 장중 실시간</span>"
                        if infl.get("source") == "naver_crawl" else
                        "<span class='sub'>· 확정치</span>")
            parts.append(
                f'<div class="row"><span class="k">외인 순매수</span><span class="v">{money_ix(infl["foreign"])} {src_tag2}</span></div>'
                f'<div class="row"><span class="k">기관 순매수</span><span class="v">{money_ix(infl["inst"])}</span></div>'
                f'<div class="row"><span class="k">개인 순매수</span><span class="v">{money_ix(infl["indiv"])}</span></div>'
            )
        else:
            parts.append(f'<div class="row"><span class="v na">투자자 매매 데이터 없음{" ("+esc(infl.get("err",""))+")" if infl.get("err") else ""}</span></div>')
        sup_inner = "".join(parts)
    if sup_inner:
        _t5 = ("5. 시장 현황 (상승/하락/상하한가 + 투자자 매매)" if a.get("is_index")
               else "5. 거래량·수급 (외인·기관·개인 5·20·60일 + 비중)")
        _sum5 = sup.get("summary") if isinstance(sup, dict) else ""
        _head5 = f'<div class="hl">{esc(_sum5)}</div>' if _sum5 else ""
        sup_html = _section_if(_t5, _head5 + _fold("💹 수급 상세 보기", sup_inner))
    else:
        sup_html = ""

    # 6. 공매도 (5·20·60일 구간별)
    sh = a["shorting"]
    if sh.get("ok"):
        now_v = sh.get("now")
        def sh_line(label, w):
            if not w:
                return f'<div class="row"><span class="k">{label}</span><span class="v na">N/A</span></div>'
            col = _dir_color(w["trend"])
            return (f'<div class="row"><span class="k">{label}</span>'
                    f'<span class="v"><span style="color:{col}">{w["trend"]}</span> '
                    f'<span class="sub">({w["ago"]}% → {now_v}%)</span></span></div>')
        sh_inner = (
            f'<div class="row"><span class="k">현재 비중</span><span class="v"><b>{now_v}%</b> <span class="sub">(20일평균 {sh.get("avg20","?")}%)</span></span></div>'
            + sh_line("5일 추세", sh.get("d5"))
            + sh_line("20일 추세", sh.get("d20"))
            + sh_line("60일 추세", sh.get("d60"))
            + '<div class="row"><span class="k">기준</span><span class="v sub">각 구간 첫날 대비 현재 비중 변화(±0.3%p 이내 보합)</span></div>'
        )
        _d20 = sh.get("d20") or {}
        sh_head = (f'<div class="hl">현재 비중 <b>{now_v}%</b>'
                   + (f' · 20일 <span style="color:{_dir_color(_d20.get("trend",""))}">'
                      f'{esc(_d20.get("trend",""))}</span>' if _d20 else '')
                   + '</div>')
        sh_inner = sh_head + _fold("📉 공매도 상세 보기", sh_inner)
    else:
        sh_inner = ""
    sh_html = _section_if("6. 공매도 현황 (5·20·60일 구간별)", sh_inner)

    # 7,8. 일봉 종합 + 실제 채점표 (이 종목이 각 항목에서 받은 점수)
    dv = a["daily_verdict"]
    dvcol = _dir_color(dv["verdict"])
    breakdown = dv.get("breakdown", [])
    rows_html = []
    for b in breakdown:
        pts = b["pts"]
        if pts > 0:
            pcol, ptxt = C_UP, f"+{pts}"
        elif pts < 0:
            pcol, ptxt = C_DOWN, f"{pts}"
        else:
            pcol, ptxt = C_SUB, "0"
        rows_html.append(
            f'<tr><td>{esc(b["item"])}</td>'
            f'<td style="color:{pcol};font-weight:700;text-align:center">{ptxt}</td>'
            f'<td class="sub">{esc(b["note"])}</td></tr>')
    total_col = C_UP if dv["score"] > 0 else (C_DOWN if dv["score"] < 0 else C_SUB)
    score_table = (
        '<table class="score-tbl"><thead><tr><th>항목</th><th>점수</th><th>사유</th></tr></thead><tbody>'
        + "".join(rows_html)
        + f'<tr class="total-row"><td><b>합계</b></td>'
          f'<td style="color:{total_col};font-weight:700;text-align:center">{dv["score"]:+d}</td>'
          f'<td class="sub">→ {esc(dv["verdict"])}</td></tr>'
        + '</tbody></table>'
        '<div class="sub" style="margin-top:6px">판정 구간: +4↑ 적극매수 · +2~3 매수우위 · +1 매수관심 · 0 관망 · −1~−2 주의 · −3↓ 매도관심</div>'
    )
    daily_html = _section(
        "7·8. 일봉 종합 의견",
        f'<div class="verdict-big" style="color:{dvcol}">{dv["verdict"]}</div>'
        + _fold("🧮 채점 내역 보기", score_table))

    # 9. 월봉 종합 (채점표)
    mv = a["monthly_verdict"]
    mvcol = _dir_color(mv["verdict"])
    m_breakdown = mv.get("breakdown", [])
    m_rows = []
    for b in m_breakdown:
        pts = b["pts"]
        if pts > 0:
            pcol, ptxt = C_UP, f"+{pts}"
        elif pts < 0:
            pcol, ptxt = C_DOWN, f"{pts}"
        else:
            pcol, ptxt = C_SUB, "0"
        m_rows.append(
            f'<tr><td>{esc(b["item"])}</td>'
            f'<td style="color:{pcol};font-weight:700;text-align:center">{ptxt}</td>'
            f'<td class="sub">{esc(b["note"])}</td></tr>')
    m_total_col = C_UP if mv["score"] > 0 else (C_DOWN if mv["score"] < 0 else C_SUB)
    m_table = (
        '<table class="score-tbl"><thead><tr><th>항목</th><th>점수</th><th>사유</th></tr></thead><tbody>'
        + "".join(m_rows)
        + f'<tr class="total-row"><td><b>합계</b></td>'
          f'<td style="color:{m_total_col};font-weight:700;text-align:center">{mv["score"]:+d}</td>'
          f'<td class="sub">→ {esc(mv["verdict"])}</td></tr>'
        + '</tbody></table>'
    )
    monthly_html = _section(
        "9. 월봉 종합 의견",
        f'<div class="verdict-big" style="color:{mvcol}">{mv["verdict"]}</div>'
        + (f'<div class="sub" style="margin-top:4px">{esc(mv["note"])}</div>' if mv.get("note") else "")
        + _fold("🧮 채점 내역 보기", m_table))

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(a["name"])} 분석</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:{C_BG}; color:{C_TEXT};
    font-family:-apple-system,'Segoe UI','Malgun Gothic',sans-serif; font-size:14px; line-height:1.5; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:16px; }}
  @media (max-width:800px) {{ .wrap {{ max-width:100%; padding:12px; }} }}
  .head {{ display:flex; align-items:baseline; gap:10px; padding-bottom:12px; border-bottom:1px solid {C_LINE}; margin-bottom:14px; }}
  .head h1 {{ font-size:20px; margin:0; }}
  .code {{ color:{C_SUB}; font-size:13px; }}
  .price {{ margin-left:auto; font-size:18px; font-weight:700; }}
  .sec {{ background:{C_CARD}; border:1px solid {C_LINE}; border-radius:10px; padding:12px 14px; margin-bottom:10px; }}
  .sec-h {{ font-size:13px; font-weight:700; color:{C_SUB}; margin-bottom:8px; letter-spacing:.02em; }}
  .row {{ display:flex; gap:10px; padding:4px 0; border-top:1px solid {C_LINE}; }}
  .row:first-of-type {{ border-top:none; }}
  .k {{ color:{C_SUB}; min-width:74px; flex-shrink:0; }}
  .v {{ flex:1; }}
  .sub {{ color:{C_SUB}; font-size:12px; }}
  .na {{ color:#5a5f6a; font-style:italic; }}
  .frame {{ margin-bottom:8px; }}
  .frame-n {{ font-size:12px; color:{C_SUB}; margin-bottom:4px; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
  .chip {{ border:1px solid {C_LINE}; border-radius:6px; padding:3px 8px; font-size:12px; background:#12151b; }}
  .chip.na {{ color:#5a5f6a; }}
  .verdict-list {{ margin:8px 0 0; padding-left:18px; }}
  .verdict-list li {{ margin:2px 0; font-size:13px; }}
  .verdict-big {{ font-size:17px; font-weight:700; margin-bottom:4px; }}
  .foot {{ color:{C_SUB}; font-size:11px; text-align:center; margin-top:16px; }}
  .charts {{ display:flex; gap:8px; flex-wrap:wrap; }}
  .chart {{ flex:1; min-width:210px; }}
  .chart img {{ width:100%; border-radius:6px; display:block; }}
  .na-chart {{ flex:1; min-width:210px; text-align:center; padding:40px 0;
    color:#5a5f6a; border:1px dashed {C_LINE}; border-radius:6px; }}
  /* 접기/펼치기 */
  .fold {{ margin:8px 0 2px; }}
  .fold-s {{ cursor:pointer; list-style:none; font-size:12.5px; color:{C_SUB};
    padding:6px 10px; border:1px solid {C_LINE}; border-radius:6px;
    background:rgba(255,255,255,.02); user-select:none; }}
  .fold-s::-webkit-details-marker {{ display:none; }}
  .fold-s::before {{ content:"▸ "; color:{C_SUB}; }}
  details[open] > .fold-s::before {{ content:"▾ "; }}
  .fold-s:hover {{ color:{C_TEXT}; border-color:#3a3f4a; }}
  .fold-b {{ padding:8px 2px 2px; }}
  .hl {{ font-size:13.5px; line-height:1.6; padding:2px 0 4px; }}
  /* 통합 방향성 예측 */
  .fc-verdict {{ border:1px solid {C_LINE}; border-left-width:4px; border-radius:8px;
    padding:10px 14px; margin-bottom:10px; background:rgba(255,255,255,.02); }}
  .fc-v {{ font-size:19px; font-weight:800; letter-spacing:-.02em; }}
  .fc-s {{ color:{C_SUB}; font-size:12px; margin-top:2px; }}
  .fc-narr {{ font-size:14px; line-height:1.6; margin-top:6px; color:{C_TEXT}; }}
  .fac-wrap {{ display:flex; gap:10px; flex-wrap:wrap; margin:8px 0 4px; }}
  .fac {{ flex:1; min-width:220px; background:rgba(255,255,255,.03);
    border-radius:8px; padding:8px 12px; }}
  .fac-h {{ font-size:12px; font-weight:700; margin-bottom:4px; }}
  .fac-i {{ font-size:12.5px; padding:2px 0; color:{C_TEXT}; }}
  .hz-tbl {{ width:100%; border-collapse:collapse; font-size:12.5px; margin:6px 0 10px; }}
  .hz-tbl th {{ color:{C_SUB}; text-align:left; font-weight:600; padding:4px 8px;
    border-bottom:1px solid {C_LINE}; }}
  .hz-tbl td {{ padding:5px 8px; border-bottom:1px solid {C_LINE}; }}
  .hz-p {{ width:78px; color:{C_TEXT}; font-weight:600; }}
  .hz-l {{ width:78px; color:{C_SUB}; }}
  .hz-d {{ width:92px; }}
  .fc-sub {{ font-size:12px; color:{C_SUB}; font-weight:700; margin:10px 0 4px; }}
  .fc-note {{ font-size:12.5px; padding:8px 10px; margin:6px 0;
    background:rgba(255,255,255,.03); border-radius:6px; line-height:1.55; }}
  /* 세로 2줄 차트 (일봉/월봉 가득 차게) */
  .charts-vert {{ display:flex; flex-direction:column; gap:16px; }}
  .chart-full {{ width:100%; }}
  .chart-full img {{ width:100%; height:auto; border-radius:6px; display:block; }}
  .chart-full.na-chart {{ text-align:center; padding:60px 0;
    color:#5a5f6a; border:1px dashed {C_LINE}; border-radius:6px; }}
  .score-tbl {{ width:100%; border-collapse:collapse; font-size:12px; margin-top:4px; }}
  .score-tbl th {{ color:{C_SUB}; text-align:left; padding:4px 6px; border-bottom:1px solid {C_LINE}; font-weight:600; }}
  .score-tbl td {{ padding:4px 6px; border-bottom:1px solid {C_LINE}; }}
  .score-tbl th:nth-child(2), .score-tbl td:nth-child(2) {{ width:48px; text-align:center; }}
  .score-tbl .total-row td {{ border-top:2px solid {C_LINE}; border-bottom:none; padding-top:6px; }}
  .bear-banner {{ background:#2a0d12; border:1.5px solid #c0182b; border-radius:10px;
    padding:12px 14px; margin-bottom:12px; }}
  .bear-title {{ font-size:15px; font-weight:800; color:#ff5a6a; margin-bottom:8px; }}
  .bear-row {{ display:flex; gap:8px; align-items:baseline; padding:4px 0; }}
  .bear-badge {{ color:#fff; font-size:11px; font-weight:700; padding:2px 8px;
    border-radius:5px; flex-shrink:0; }}
  .bear-desc {{ font-size:13px; color:#f0d0d4; }}
  .freshness {{ font-size:12px; color:{C_SUB}; margin-bottom:12px;
    padding:6px 10px; background:{C_CARD}; border-radius:6px; }}
</style></head>
<body><div class="wrap">
  <div class="head">
    <h1>{esc(a["name"])}</h1><span class="code">{a["code"]}</span>
    <span class="price" style="color:{chg_col}">{a["price"]:,}원 ({chg:+.2f}%)</span>
  </div>
  <div class="freshness">📅 데이터 기준: 일봉 {esc(a.get("data_freshness",{}).get("daily","?"))} · 주봉 {esc(a.get("data_freshness",{}).get("weekly","?"))} · 월봉 {esc(a.get("data_freshness",{}).get("monthly","?"))}{" · 1H " + esc(str(a.get("data_freshness",{}).get("hourly",""))[:16]) if a.get("data_freshness",{}).get("hourly") else ""}</div>
  {bear_html}{chart_html}{div_html}{stoch_html}{ma_html}{pb_html}{sup_html}{sh_html}{daily_html}{monthly_html}
  <div class="foot">생성: {esc(a["timestamp"])} · 일봉 60일 / 월봉 최대 5년(60개월)</div>
</div></body></html>"""


# ----------------------------------------------------------------------
# 저장 + latest 갱신
# ----------------------------------------------------------------------
def save_report(analysis: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)  # VN 기준 폴더명
    day_dir, jpath, hpath, stem = _paths(analysis["name"], analysis["code"], now)

    # JSON엔 charts(base64 PNG)를 빼서 파일 크기 절약
    analysis_json = {k: v for k, v in analysis.items() if k != "charts"}
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(analysis_json, f, ensure_ascii=False, indent=2)
    with open(hpath, "w", encoding="utf-8") as f:
        f.write(build_html(analysis))

    # latest 폴더 갱신 (종목별 최신본)
    latest = os.path.join(DATA_ROOT, "latest")
    os.makedirs(latest, exist_ok=True)
    safe = analysis["name"].replace(" ", "-").replace("/", "-")
    shutil.copy(jpath, os.path.join(latest, f'{analysis["code"]}_{safe}.json'))
    shutil.copy(hpath, os.path.join(latest, f'{analysis["code"]}_{safe}.html'))

    return {"json": jpath, "html": hpath, "day_dir": day_dir}


def write_day_index(analyses: list[dict], now: dt.datetime | None = None) -> str:
    """그날 전체 종목을 한 페이지로 묶는 index.html."""
    now = now or dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    day = now.strftime("%Y-%m-%d")
    day_dir = os.path.join(DATA_ROOT, day)
    os.makedirs(day_dir, exist_ok=True)
    cards = []
    for a in analyses:
        chg = a["change_pct"]
        col = C_UP if chg >= 0 else C_DOWN
        dv = a["daily_verdict"]["verdict"]
        dvcol = _dir_color(dv)
        safe = a["name"].replace(" ", "-").replace("/", "-")
        # 해당 종목 최신 html 링크 (같은 폴더 내 파일명은 시각 포함이라 latest로 연결)
        link = f'../latest/{a["code"]}_{safe}.html'
        cards.append(
            f'<a class="card" href="{link}">'
            f'<div class="c-top"><b>{html_lib.escape(a["name"])}</b> '
            f'<span style="color:{col}">{chg:+.2f}%</span></div>'
            f'<div class="c-v" style="color:{dvcol}">{dv}</div></a>')
    body = "".join(cards)
    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>포트폴리오 분석 {day}</title>
<style>
  body {{ margin:0; background:{C_BG}; color:{C_TEXT}; font-family:-apple-system,'Malgun Gothic',sans-serif; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:16px; }}
  @media (max-width:800px) {{ .wrap {{ max-width:100%; padding:12px; }} }}
  h1 {{ font-size:18px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:10px; }}
  .card {{ display:block; background:{C_CARD}; border:1px solid {C_LINE}; border-radius:10px;
    padding:12px; text-decoration:none; color:{C_TEXT}; }}
  .card:hover {{ border-color:#3a4150; }}
  .c-top {{ display:flex; justify-content:space-between; margin-bottom:6px; }}
  .c-v {{ font-size:13px; font-weight:700; }}
</style></head><body><div class="wrap">
  <h1>📊 포트폴리오 분석 · {day}</h1>
  <div class="grid">{body}</div>
</div></body></html>"""
    ipath = os.path.join(day_dir, "index.html")
    with open(ipath, "w", encoding="utf-8") as f:
        f.write(doc)
    return ipath


def write_manifest(analyses: list[dict], now: dt.datetime | None = None) -> str:
    """뷰어(viewer.html)가 읽을 종목 목록 manifest.json 을 data/latest 에 생성.
    지수는 group='지수', 나머지는 섹터별로 묶지 않고 '보유종목'."""
    now = now or dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    latest = os.path.join(DATA_ROOT, "latest")
    os.makedirs(latest, exist_ok=True)

    items = []
    for a in analyses:
        is_index = a.get("is_index", False)
        items.append({
            "name": a["name"],
            "code": a["code"],
            "chg": a.get("change_pct"),
            "group": "지수" if is_index else "보유종목",
            "verdict": a.get("daily_verdict", {}).get("verdict", ""),
            "bear": bool(a.get("bear_warnings", {}).get("has_warning")),
        })

    manifest = {
        "updated": now.strftime("%Y-%m-%d %H:%M") + " (VN)",
        "count": len(items),
        "items": items,
    }
    mpath = os.path.join(latest, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return mpath
