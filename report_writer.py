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


def build_html(analysis: dict) -> str:
    a = analysis
    chg = a["change_pct"]
    chg_col = C_UP if chg >= 0 else C_DOWN
    esc = html_lib.escape

    # 2. 다이버전스
    div_rows = []
    for frame in ["일봉", "월봉", "1H"]:
        d = a["divergence"].get(frame, {})
        if not d.get("ok"):
            div_rows.append(f'<div class="row"><span class="k">{frame}</span><span class="v na">데이터 부족</span></div>')
        elif not d.get("found"):
            div_rows.append(f'<div class="row"><span class="k">{frame}</span><span class="v sub">다이버전스 없음</span></div>')
        else:
            col = _dir_color(d["type"])
            div_rows.append(
                f'<div class="row"><span class="k">{frame}</span>'
                f'<span class="v"><b style="color:{col}">{d["type"]}다이버전스</b> '
                f'<span class="sub">[{d["to_date"]}]</span><br>'
                f'<span class="sub">{esc(d["basis"])}</span></span></div>')
    div_html = _section("2. 스토캐스틱 다이버전스 (근거·시점)", "".join(div_rows))

    # 3. 스토캐 프레임
    s = a["stoch_frames"]
    def frame_line(fname, keys):
        cells = "".join(_sd(k, s[fname].get(k, {})) for k in keys)
        return f'<div class="frame"><div class="frame-n">{fname}</div><div class="chips">{cells}</div></div>'
    stoch_body = (
        frame_line("1시간봉", []) if False else ""
    )
    stoch_body = (
        f'<div class="frame"><div class="frame-n">1시간봉</div><div class="chips">'
        + _sd("장기", s["hourly"].get("장기", {})) + _sd("중기", s["hourly"].get("중기", {})) + _sd("단기", s["hourly"].get("단기", {}))
        + '</div></div>'
        f'<div class="frame"><div class="frame-n">일봉</div><div class="chips">'
        + _sd("장기", s["daily"].get("장기", {})) + _sd("중기", s["daily"].get("중기", {})) + _sd("단기", s["daily"].get("단기", {}))
        + '</div></div>'
        f'<div class="frame"><div class="frame-n">월봉</div><div class="chips">'
        + _sd("중기", s["monthly"].get("중기", {})) + _sd("단기", s["monthly"].get("단기", {}))
        + '</div></div>'
    )
    verdict_lines = "".join(f'<li>{esc(l)}</li>' for l in s["verdict"].get("lines", []))
    stoch_body += f'<ul class="verdict-list">{verdict_lines}</ul>'
    stoch_html = _section("3. 스토캐스틱 프레임별 방향성 (1H 장·중·단 / 일 / 월)", stoch_body)

    # 4. 이평선
    ma = a["ma"]
    if ma.get("ok"):
        ma_inner = (
            f'<div class="row"><span class="k">현재가/이평</span><span class="v">'
            f'{ma["price"]:,}원 · {ma["position"]} <span class="sub">(5일 {ma["ma5"]:,} / 20일 {ma["ma20"]:,})</span></span></div>'
            f'<div class="row"><span class="k">배열</span><span class="v" style="color:{_dir_color(ma["state"])}">{ma["state"]} <span class="sub">(갭 {ma["gap_pct"]:+.2f}%)</span></span></div>'
        )
        if ma.get("forecast"):
            ma_inner += f'<div class="row"><span class="k">예측</span><span class="v" style="color:{_dir_color(ma["forecast"])}">{esc(ma["forecast"])}</span></div>'
        if ma.get("note"):
            ma_inner += f'<div class="row"><span class="k">비고</span><span class="v sub">{esc(ma["note"])}</span></div>'
    else:
        ma_inner = '<div class="row"><span class="v na">이평 데이터 부족</span></div>'
    ma_html = _section("4. 이평선 분석 (골든/데드크로스 근접·예측)", ma_inner)

    # 5. 수급
    sup = a["supply_demand"]
    if sup.get("ok"):
        f5c = C_UP if sup.get("foreign_5d", 0) >= 0 else C_DOWN
        i5c = C_UP if sup.get("inst_5d", 0) >= 0 else C_DOWN
        sup_inner = (
            f'<div class="row"><span class="k">요약</span><span class="v">{esc(sup["summary"])}</span></div>'
            f'<div class="row"><span class="k">외인</span><span class="v">5일 <b style="color:{f5c}">{sup.get("foreign_5d",0):+,}</b> / 20일 {sup.get("foreign_20d",0):+,}</span></div>'
            f'<div class="row"><span class="k">기관</span><span class="v">5일 <b style="color:{i5c}">{sup.get("inst_5d",0):+,}</b> / 20일 {sup.get("inst_20d",0):+,}</span></div>'
        )
    else:
        sup_inner = f'<div class="row"><span class="v na">{esc(sup.get("summary","수급 데이터 없음"))}</span></div>'
    sup_html = _section("5. 거래량·수급 (외인·기관·개인)", sup_inner)

    # 6. 공매도
    sh = a["shorting"]
    if sh.get("ok"):
        sh_inner = (
            f'<div class="row"><span class="k">추세</span><span class="v" style="color:{_dir_color(sh.get("short_5d_trend"))}">{sh.get("short_5d_trend")}</span></div>'
            f'<div class="row"><span class="k">비중</span><span class="v">{sh.get("short_ratio_5d_ago")}% → {sh.get("short_ratio_now")}%</span></div>'
        )
    else:
        sh_inner = f'<div class="row"><span class="v na">{esc(sh.get("summary","공매도 데이터 없음"))}</span></div>'
    sh_html = _section("6. 공매도 현황", sh_inner)

    # 7,8. 일봉 종합
    dv = a["daily_verdict"]
    dvcol = _dir_color(dv["verdict"])
    reasons = " · ".join(dv["reasons"]) if dv["reasons"] else "근거 부족"
    daily_html = _section(
        "7·8. 일봉 종합 의견",
        f'<div class="verdict-big" style="color:{dvcol}">{dv["verdict"]} '
        f'<span class="sub">(score {dv["score"]:+d})</span></div>'
        f'<div class="sub">{esc(reasons)}</div>')

    # 9. 월봉 종합
    mv = a["monthly_verdict"]
    mvcol = _dir_color(mv["verdict"])
    mreasons = " · ".join(mv["reasons"]) if mv["reasons"] else "근거 부족"
    monthly_html = _section(
        "9. 월봉 종합 의견 (12개월 기준)",
        f'<div class="verdict-big" style="color:{mvcol}">{mv["verdict"]} '
        f'<span class="sub">(score {mv["score"]:+d})</span></div>'
        f'<div class="sub">{esc(mreasons)}</div>'
        f'<div class="sub" style="margin-top:4px">{esc(mv["note"])}</div>')

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(a["name"])} 분석</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:{C_BG}; color:{C_TEXT};
    font-family:-apple-system,'Segoe UI','Malgun Gothic',sans-serif; font-size:14px; line-height:1.5; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:16px; }}
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
</style></head>
<body><div class="wrap">
  <div class="head">
    <h1>{esc(a["name"])}</h1><span class="code">{a["code"]}</span>
    <span class="price" style="color:{chg_col}">{a["price"]:,}원 ({chg:+.2f}%)</span>
  </div>
  {div_html}{stoch_html}{ma_html}{sup_html}{sh_html}{daily_html}{monthly_html}
  <div class="foot">생성: {esc(a["timestamp"])} · 일봉 60일 / 월봉 12개월 기준</div>
</div></body></html>"""


# ----------------------------------------------------------------------
# 저장 + latest 갱신
# ----------------------------------------------------------------------
def save_report(analysis: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)  # VN 기준 폴더명
    day_dir, jpath, hpath, stem = _paths(analysis["name"], analysis["code"], now)

    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
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
  .wrap {{ max-width:760px; margin:0 auto; padding:16px; }}
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
