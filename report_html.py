# -*- coding: utf-8 -*-
"""
report_html.py (v3)
===================
- 상단: 지수 대시보드 7종 (등락률/MA방향/스토캐구간/다이버전스)
- 종목 카드: 종합판정(메인/보조 분리) + 캔들차트 + 프레임별 스토캐 다이버전스
  + MA(5/20/60) + 거래량 + 수급
- 색상: 상승/강세=빨강, 하락/약세=파랑
"""
from __future__ import annotations
import io, base64
import datetime as dt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from signals import compute_ma_signal, compute_volume_signal, to_weekly, to_monthly, SignalConfig
from supply_demand import fetch_supply, supply_text
from stoch_frames import analyze_frame, stoch_text
from insight import score_stock, add_api_comment
import data_feed as feed

RED, BLUE, GREY = "#e03131", "#1971c2", "#868e96"
CFG = SignalConfig()
STANCE_COLOR = {"매수우위": RED, "관망": GREY, "비중축소": BLUE}
DIV_COLOR = {"상승": RED, "하락": BLUE, "없음": GREY}


def make_chart_png(df, code, bars=120):
    d = df.tail(bars).reset_index(drop=True)
    ma5 = d["close"].rolling(5).mean(); ma20 = d["close"].rolling(20).mean()
    ma60 = d["close"].rolling(60).mean(); vol_ma = d["volume"].rolling(5).mean()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.2, 4.2), sharex=True,
                                    gridspec_kw={"height_ratios": [3, 1]}, dpi=110)
    fig.patch.set_facecolor("white")
    x = np.arange(len(d))
    for i in range(len(d)):
        o, c, h, l = d.loc[i, ["open", "close", "high", "low"]]
        col = RED if c >= o else BLUE
        ax1.vlines(x[i], l, h, color=col, linewidth=0.7)
        ax1.vlines(x[i], o, c, color=col, linewidth=3.2)
    ax1.plot(x, ma5, color="#f08c00", lw=1.0, label="MA5")
    ax1.plot(x, ma20, color="#2f9e44", lw=1.0, label="MA20")
    ax1.plot(x, ma60, color="#9c36b5", lw=1.0, label="MA60")
    ax1.legend(loc="upper left", fontsize=7, ncol=3, frameon=False)
    ax1.set_title(f"{code} (last {bars})", fontsize=9)
    ax1.grid(alpha=0.15); ax1.tick_params(labelsize=7)
    vcolors = [RED if d.loc[i, "close"] >= d.loc[i, "open"] else BLUE for i in range(len(d))]
    ax2.bar(x, d["volume"], color=vcolors, alpha=0.6, width=0.7)
    ax2.plot(x, vol_ma, color=GREY, lw=1.0, label="Vol MA5")
    ax2.legend(loc="upper left", fontsize=7, frameon=False)
    ax2.grid(alpha=0.15); ax2.tick_params(labelsize=7)
    plt.tight_layout(pad=0.5)
    buf = io.BytesIO(); fig.savefig(buf, format="png", bbox_inches="tight"); plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _dir_span(direction):
    color = {"상향": RED, "하향": BLUE}.get(direction, GREY)
    arrow = {"상향": "▲", "보합": "―", "하향": "▼"}.get(direction, "")
    return f'<span style="color:{color};font-weight:600">{arrow}{direction}</span>'


def _badge(text, kind):
    c = {"bull": RED, "bear": BLUE, "neutral": GREY}.get(kind, GREY)
    return (f'<span style="display:inline-block;padding:2px 8px;margin:2px;'
            f'border:1px solid {c};color:{c};border-radius:10px;font-size:12px">{text}</span>')


def analyze_stock(name, code, daily, hourly=None, use_api=True):
    weekly = to_weekly(daily); monthly = to_monthly(daily)
    d_ma = compute_ma_signal(daily, CFG)
    d_vol = compute_volume_signal(daily, CFG)
    w_ma = compute_ma_signal(weekly, CFG) if len(weekly) >= 70 else None
    m_ma = compute_ma_signal(monthly, CFG) if len(monthly) >= 62 else None
    sup = fetch_supply(code)
    # 프레임별 스토캐
    st_d = analyze_frame(daily, "일봉")
    st_m = analyze_frame(monthly, "월봉")
    st_h = analyze_frame(hourly, "1시간") if (hourly is not None and len(hourly) >= 30) \
        else analyze_frame(pd.DataFrame(), "1시간")
    verdict = score_stock(d_ma, w_ma, m_ma, d_vol, sup, st_h, st_d, st_m)
    last = daily["close"].iloc[-1]; prev = daily["close"].iloc[-2]
    chg = (last - prev) / prev * 100
    stoch_sum = " / ".join(stoch_text(s) for s in (st_h, st_d, st_m) if s.ok)
    if use_api:
        verdict = add_api_comment(name, verdict, chg, supply_text(sup), stoch_sum)
    else:
        verdict.comment = f"[{verdict.stance}] " + ", ".join(verdict.main_reasons[:4])

    events = []
    if d_ma.cross_5_20 == "골든크로스":
        events.append(("golden", "5·20 골든크로스"))
    if d_ma.cross_5_20 == "데드크로스":
        events.append(("dead", "5·20 데드크로스"))
    if d_vol.over_vol_ma:
        events.append(("vol", f"거래량 돌파 x{d_vol.ratio:.1f}"))
    for s in (st_h, st_d, st_m):
        if s.ok and s.divergence != "없음":
            events.append(("div_up" if s.divergence == "상승" else "div_dn",
                           f"{s.frame} {s.divergence}다이버전스"))
    if d_ma.above_ma5 and d_ma.ma5_touch:
        events.append(("touch", "5일선 지지터치"))

    return dict(name=name, code=code, daily=daily, chg=chg, last=last,
                d_ma=d_ma, w_ma=w_ma, m_ma=m_ma, d_vol=d_vol, sup=sup,
                st_h=st_h, st_d=st_d, st_m=st_m, verdict=verdict, events=events)


def one_line_summary_from(res):
    v = res["verdict"]
    icon = {"매수우위": "🔴", "비중축소": "🔵", "관망": "⚪"}[v.stance]
    text = (f"{icon} {res['name']} {res['last']:,.0f}({res['chg']:+.1f}%) "
            f"[{v.stance} {v.score:+d}] {v.comment}")
    return text, v.stance


def stoch_row_html(res):
    def cell(sf):
        if not sf.ok:
            return f'<td style="color:{GREY}">-</td>'
        div_c = DIV_COLOR[sf.divergence]
        div_txt = f'<b style="color:{div_c}">{sf.divergence}다이버전스</b>' if sf.divergence != "없음" else "다이버전스 없음"
        turn = ""
        if sf.turn == "상승전환":
            turn = f'<span style="color:{RED}">상승전환{"(과매도)" if sf.turn_from_oversold else ""}</span>'
        elif sf.turn == "하락전환":
            turn = f'<span style="color:{BLUE}">하락전환{"(과매수)" if sf.turn_from_overbought else ""}</span>'
        zone_c = RED if sf.zone == "과매도" else (BLUE if sf.zone == "과매수" else GREY)
        return (f'<td>%K {sf.k:.0f} <span style="color:{zone_c}">{sf.zone}</span><br>'
                f'{div_txt}{"<br>" + turn if turn else ""}</td>')
    return (f'<table class="stoch"><tr><th>스토캐</th><th>1시간봉</th><th>일봉</th><th>월봉</th></tr>'
            f'<tr><td style="color:#999">상태</td>{cell(res["st_h"])}{cell(res["st_d"])}{cell(res["st_m"])}</tr></table>')


def stock_card_html(res):
    d_ma, w_ma, m_ma = res["d_ma"], res["w_ma"], res["m_ma"]
    sup, v = res["sup"], res["verdict"]
    chg_color = RED if res["chg"] >= 0 else BLUE
    stance_c = STANCE_COLOR.get(v.stance, GREY)

    badges = []
    for kind, txt in res["events"]:
        bk = "bull" if kind in ("golden", "vol", "div_up") else \
             ("bear" if kind in ("dead", "div_dn") else "neutral")
        badges.append(_badge(txt, bk))

    def ma_row(lbl, s):
        if s is None:
            return f'<tr><td>{lbl}</td><td colspan="3" style="color:{GREY}">데이터 부족</td></tr>'
        ac = RED if s.alignment == "정배열" else (BLUE if s.alignment == "역배열" else GREY)
        return (f"<tr><td><b>{lbl}</b></td><td>MA5 {_dir_span(s.ma5_direction)} ({s.ma5_slope_pct:+.1f}%)</td>"
                f'<td style="color:{ac}">{s.alignment}</td><td>{"5일선 위" if s.above_ma5 else "5일선 아래"}</td></tr>')

    if sup.ok:
        fc = RED if sup.foreign_5d >= 0 else BLUE
        ic = RED if sup.inst_5d >= 0 else BLUE
        supply_html = f"""<table class="supply"><tr><th>수급(보조)</th><th>5일</th><th>20일</th><th>추세/연속</th></tr>
          <tr><td>외국인</td><td style="color:{fc}">{sup.foreign_5d:+.0f}억</td>
            <td style="color:{RED if sup.foreign_20d>=0 else BLUE}">{sup.foreign_20d:+.0f}억</td>
            <td>{sup.foreign_trend}/{'매수' if sup.foreign_streak>0 else '매도'}{abs(sup.foreign_streak)}일</td></tr>
          <tr><td>기관</td><td style="color:{ic}">{sup.inst_5d:+.0f}억</td>
            <td style="color:{RED if sup.inst_20d>=0 else BLUE}">{sup.inst_20d:+.0f}억</td>
            <td>{sup.inst_trend}</td></tr></table>"""
    else:
        supply_html = f'<div style="color:{GREY};font-size:12px">수급 데이터 없음</div>'

    chart_b64 = make_chart_png(res["daily"], res["code"])
    main_r = ", ".join(v.main_reasons) if v.main_reasons else "특이 없음"
    sub_r = ", ".join(v.sub_reasons) if v.sub_reasons else "-"

    return f"""
    <div class="card">
      <div class="card-head"><span class="name">{res['name']}</span>
        <span class="code">{res['code']}</span>
        <span class="price" style="color:{chg_color}">{res['last']:,.0f}원 ({res['chg']:+.2f}%)</span></div>
      <div class="verdict" style="border-color:{stance_c}">
        <span class="stance" style="background:{stance_c}">{v.stance} {v.score:+d}</span>
        <span class="comment">{v.comment}</span></div>
      <div class="reasons"><b style="color:{stance_c}">메인</b> {main_r}<br>
        <span style="color:#999">보조 {sub_r}</span></div>
      <div class="badges">{''.join(badges) if badges else '<span style="color:#aaa">특이 이벤트 없음</span>'}</div>
      {stoch_row_html(res)}
      <img class="chart" src="data:image/png;base64,{chart_b64}"/>
      <table class="ma"><tr><th></th><th>MA5 방향</th><th>배열</th><th>위치</th></tr>
        {ma_row('일봉', d_ma)}{ma_row('주봉', w_ma)}{ma_row('월봉', m_ma)}</table>
      {supply_html}
    </div>"""


def index_dashboard_html(views):
    if not views:
        return ('<div class="summary"><b>📈 지수</b><br>'
                '<span style="color:#999">지수 데이터 없음 (pykrx 접근 필요)</span></div>')
    rows = []
    for v in views:
        if not v.ok:
            rows.append(f'<tr><td>{v.label}</td><td colspan="4" style="color:{GREY}">조회불가</td></tr>')
            continue
        cc = RED if v.chg >= 0 else BLUE
        div_c = DIV_COLOR.get(v.divergence, GREY)
        zone_c = RED if v.zone == "과매도" else (BLUE if v.zone == "과매수" else GREY)
        div_txt = f'<span style="color:{div_c}">{v.divergence}</span>' if v.divergence != "없음" else "-"
        rows.append(
            f'<tr><td><b>{v.label}</b><br><span style="font-size:11px;color:#aaa">{v.resolved}</span></td>'
            f'<td style="color:{cc}">{v.last:,.1f}<br>{v.chg:+.2f}%</td>'
            f'<td>MA5 {_dir_span(v.ma5_dir)}<br>MA20 {_dir_span(v.ma20_dir)}</td>'
            f'<td style="color:{zone_c}">{v.zone}</td><td>{div_txt}</td></tr>')
    return (f'<div class="summary"><b>📈 지수 대시보드</b>'
            f'<table class="idx"><tr><th>지수</th><th>등락</th><th>이평</th><th>스토캐</th><th>다이버전스</th></tr>'
            f'{"".join(rows)}</table></div>')


def portfolio_summary_html(results):
    def has(rs, *kinds):
        return [r for r in rs if any(k in kinds for k, _ in r["events"])]
    div_up = has(results, "div_up"); div_dn = has(results, "div_dn")
    golden = has(results, "golden"); dead = has(results, "dead")
    volup = has(results, "vol"); touch = has(results, "touch")
    buy = [r for r in results if r["verdict"].stance == "매수우위"]
    cut = [r for r in results if r["verdict"].stance == "비중축소"]
    def names(rs): return ", ".join(r["name"] for r in rs) if rs else "없음"
    rows = [("🟢 상승 다이버전스", div_up, RED), ("🔴 하락 다이버전스", div_dn, BLUE),
            ("⚡ 골든크로스", golden, RED), ("⚡ 데드크로스", dead, BLUE),
            ("📈 거래량 돌파", volup, RED), ("🎯 5일선 지지터치", touch, GREY),
            ("종합 매수우위", buy, RED), ("종합 비중축소", cut, BLUE)]
    lines = "".join(f'<div class="sum-row"><span class="sum-label" style="color:{c}">{lb}</span>'
                    f'<span class="sum-val">{names(rs)} <b>({len(rs)})</b></span></div>'
                    for lb, rs, c in rows)
    return f'<div class="summary"><b>📌 오늘의 이벤트 (메인지표 중심)</b>{lines}</div>'


def build_html(results, index_views=None):
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    dash = index_dashboard_html(index_views or [])
    summary = portfolio_summary_html(results)
    cards = "\n".join(stock_card_html(r) for r in results)
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>포트폴리오 브리핑 {now:%Y-%m-%d %H:%M}</title><style>
  body {{ font-family:-apple-system,"Malgun Gothic","Apple SD Gothic Neo",sans-serif; margin:0; background:#f5f6f8; color:#222; }}
  .wrap {{ max-width:820px; margin:0 auto; padding:14px; }}
  h1 {{ font-size:19px; margin:6px 0; }} .ts {{ color:#888; font-size:13px; margin-bottom:12px; }}
  .summary {{ background:#fff; border-radius:12px; padding:14px; margin-bottom:16px; box-shadow:0 1px 4px rgba(0,0,0,.06); font-size:14px; }}
  .sum-row {{ display:flex; gap:10px; padding:5px 0; border-bottom:1px solid #f2f2f2; }}
  .sum-label {{ min-width:135px; font-weight:600; }} .sum-val {{ color:#444; }}
  .card {{ background:#fff; border-radius:12px; padding:14px; margin-bottom:16px; box-shadow:0 1px 4px rgba(0,0,0,.06); }}
  .card-head {{ display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; margin-bottom:8px; }}
  .name {{ font-size:17px; font-weight:700; }} .code {{ color:#888; font-size:13px; }}
  .price {{ margin-left:auto; font-size:16px; font-weight:600; }}
  .verdict {{ border:1.5px solid; border-radius:8px; padding:8px 10px; margin-bottom:8px; display:flex; gap:10px; align-items:center; }}
  .stance {{ color:#fff; padding:3px 10px; border-radius:6px; font-weight:700; font-size:13px; white-space:nowrap; }}
  .comment {{ font-size:13.5px; line-height:1.5; }}
  .reasons {{ font-size:12.5px; line-height:1.6; margin-bottom:10px; color:#333; }}
  .badges {{ margin-bottom:10px; }}
  .chart {{ width:100%; border-radius:8px; border:1px solid #eee; margin-top:6px; }}
  table {{ width:100%; border-collapse:collapse; margin-top:8px; font-size:13px; }}
  th, td {{ text-align:left; padding:5px 6px; border-bottom:1px solid #f0f0f0; vertical-align:top; }}
  th {{ color:#999; font-weight:500; }}
  table.idx td {{ font-size:12.5px; }} table.stoch td {{ font-size:12px; }}
</style></head><body><div class="wrap">
  <h1>📊 포트폴리오 브리핑</h1>
  <div class="ts">VN {now:%Y-%m-%d %H:%M} · KST {now + dt.timedelta(hours=2):%H:%M}</div>
  {dash}{summary}{cards}
  <div style="color:#aaa;font-size:12px;text-align:center;margin:18px 0">
    메인: 스토캐 다이버전스·거래량·5/20일선 · 보조: 수급 등 · 상승=빨강/하락=파랑 · 투자판단 본인책임
  </div></div></body></html>"""


if __name__ == "__main__":
    demo = [("TK Corporation", "023160"), ("대한항공", "003490"), ("두산에너빌리티", "034020")]
    results = []
    for name, code in demo:
        df = feed.dummy_daily(seed=abs(hash(code)) % 1000)
        hr = feed.dummy_hourly(seed=abs(hash(code)) % 1000)
        results.append(analyze_stock(name, code, df, hourly=hr, use_api=False))
    html = build_html(results, index_views=[])
    open("/home/claude/sample_report.html", "w", encoding="utf-8").write(html)
    print("saved", len(html))
    for r in results:
        print(one_line_summary_from(r)[0])
