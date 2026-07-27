#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
데일리 아침 브리핑 (KST 07:00)
- 시세/지수/ETF: FinanceDataReader (네이버 기반)
- 외국인/기관 수급: 네이버 금융 종목별 순매매 (frgn), 단기/중기(20일)/장기(120일)
- 뉴스: 네이버 검색 API (트리거 발동 종목 위주)
- 발송: 카카오 '나에게 보내기' (200자 제한 → 자동 분할)
- 전체 리포트: output/brief_YYYYMMDD.html

환경변수(없으면 해당 기능 자동 스킵 = 로컬 테스트 가능):
  KAKAO_REST_KEY, KAKAO_REFRESH_TOKEN   # 없으면 콘솔 출력만
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET  # 없으면 뉴스 스킵
"""
import os, sys, json, io, time, datetime, traceback
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import FinanceDataReader as fdr

KST = ZoneInfo("Asia/Seoul")
HERE = os.path.dirname(os.path.abspath(__file__))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── 트리거 임계값 ────────────────────────────────────────────────
TH_CHANGE_PCT   = 5.0
TH_VOL_MULT     = 3.0
TH_NEAR_HL_PCT  = 3.0
TH_FLOW_DAYS    = (2, 3)   # 단기: 최근 2일 vs 직전 3일 순매매 부호 전환
FLOW_MID_DAYS   = 20       # 중기 누적
FLOW_LONG_DAYS  = 120      # 장기 누적
CAP_KAKAO_MSGS  = 8

def man(x):
    return round(x / 10000)

# ── 시세/지수 (FinanceDataReader) ────────────────────────────────
def daterange(days_back=400):
    today = datetime.datetime.now(KST).date()
    return ((today - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d"),
            today.strftime("%Y-%m-%d"))

def get_ohlcv(code, frm, to):
    df = fdr.DataReader(code, frm, to)
    if df is None or df.empty:
        raise ValueError("데이터 없음")
    return df[df["Volume"] > 0]

def get_index(frm, to, symbol, label):
    try:
        df = fdr.DataReader(symbol, frm, to)
        df = df[df["Close"] > 0]
        c, p = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
        return {"label": label, "close": c, "chg": (c/p-1)*100}
    except Exception:
        return {"label": label, "close": None, "chg": None}

# ── 외국인/기관 수급 (네이버 금융, 단/중/장) ─────────────────────
def _parse_frgn(html):
    """네이버 frgn 페이지 1장에서 [날짜, 기관, 외국인] 순매매량 추출."""
    for t in pd.read_html(io.StringIO(html), thousands=","):
        cols = ["".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
        if any("외국인" in c for c in cols) and any("기관" in c for c in cols):
            t = t.copy(); t.columns = cols
            pick = lambda a, b: next((c for c in t.columns if a in c and b in c), None)
            dcol = next((c for c in t.columns if "날짜" in c), None)
            icol, fcol = pick("기관", "순매매"), pick("외국인", "순매매")
            if not (dcol and icol and fcol):
                return None
            d = t[[dcol, icol, fcol]].copy(); d.columns = ["날짜", "기관", "외국인"]
            d = d.dropna(subset=["날짜"])
            for c in ["기관", "외국인"]:
                d[c] = pd.to_numeric(d[c].astype(str).str.replace(",", ""), errors="coerce")
            return d.dropna(subset=["기관", "외국인"])
    return None

def get_flow(code, need=FLOW_LONG_DAYS + 5, max_pages=7):
    """종목별 외국인/기관 순매매량을 오래된→최신 순 DataFrame으로. 실패 시 None."""
    frames = []
    try:
        for page in range(1, max_pages + 1):
            url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            r.encoding = "euc-kr"
            part = _parse_frgn(r.text)
            if part is None or part.empty:
                break
            frames.append(part)
            if sum(len(f) for f in frames) >= need:
                break
            time.sleep(0.1)
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["날짜"])
        df["_d"] = pd.to_datetime(df["날짜"], format="%Y.%m.%d", errors="coerce")
        df = df.dropna(subset=["_d"]).sort_values("_d").reset_index(drop=True)
        return df[["날짜", "기관", "외국인"]]
    except Exception:
        return None

def flow_reversal(series):
    if series is None or len(series) < sum(TH_FLOW_DAYS):
        return None
    recent = series.iloc[-TH_FLOW_DAYS[0]:].sum()
    prior  = series.iloc[-(TH_FLOW_DAYS[0]+TH_FLOW_DAYS[1]):-TH_FLOW_DAYS[0]].sum()
    if prior < 0 <= recent and abs(recent) > 0:
        return "매수전환"
    if prior > 0 >= recent and abs(recent) > 0:
        return "매도전환"
    return None

def _dir(x):
    return "순매수" if x > 0 else ("순매도" if x < 0 else "중립")

def flow_analyze(df):
    """단기(반전)/중기(20일)/장기(120일) 수급을 외국인·기관 각각 산출."""
    out = {}
    for inv in ["외국인", "기관"]:
        s = df[inv]
        out[inv] = {"단기": flow_reversal(s),
                    "중기": _dir(s.iloc[-FLOW_MID_DAYS:].sum()),
                    "장기": _dir(s.iloc[-FLOW_LONG_DAYS:].sum())}
    return out

def flow_text(fd):
    """카톡용 한 줄 (항상 표시). 순서: 단기/중기/장기"""
    ts = lambda v: {"매수전환": "전환매수", "매도전환": "전환매도"}.get(v, "무")
    tm = lambda v: {"순매수": "매수", "순매도": "매도", "중립": "중립"}[v]
    f, i = fd["외국인"], fd["기관"]
    return (f"수급 외인 {ts(f['단기'])}/{tm(f['중기'])}/{tm(f['장기'])}"
            f" · 기관 {ts(i['단기'])}/{tm(i['중기'])}/{tm(i['장기'])}")

def flow_cell(fd):
    """HTML용 (단/중/장 화살표). ↑매수전환 ↓매도전환 ▲순매수 ▼순매도 ·중립"""
    gs = {"매수전환": "↑", "매도전환": "↓", None: "–"}
    gm = {"순매수": "▲", "순매도": "▼", "중립": "·"}
    r = lambda inv, lab: f"{lab} {gs[fd[inv]['단기']]}{gm[fd[inv]['중기']]}{gm[fd[inv]['장기']]}"
    return f"{r('외국인','외')}<br>{r('기관','기')}"

# ── 뉴스 ─────────────────────────────────────────────────────────
def naver_news(query, n=2):
    cid, cs = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and cs):
        return []
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": cs},
            params={"query": query, "display": n, "sort": "date"}, timeout=8)
        r.raise_for_status()
        items = r.json().get("items", [])
        clean = lambda s: (s.replace("<b>", "").replace("</b>", "").replace("&quot;", '"')
                            .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
        return [{"title": clean(it["title"]), "link": it["link"]} for it in items]
    except Exception:
        return []

# ── 종목 분석 ────────────────────────────────────────────────────
def analyze(pos, frm, to):
    code = pos["code"]
    df = get_ohlcv(code, frm, to)
    if len(df) < 2:
        raise ValueError("가격 데이터 부족")
    close = float(df["Close"].iloc[-1]); prev = float(df["Close"].iloc[-2])
    chg = (close / prev - 1) * 100
    vol = float(df["Volume"].iloc[-1])
    vol20 = float(df["Volume"].iloc[-21:-1].mean()) if len(df) > 21 else float(df["Volume"].iloc[:-1].mean())
    vmult = (vol / vol20) if vol20 > 0 else 0.0
    win = df.iloc[-252:] if len(df) >= 252 else df
    hi52, lo52 = float(win["High"].max()), float(win["Low"].min())
    pos_pct = (close - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 50.0
    qty, avg = pos["qty"], pos["avg"]
    pl = (close - avg) * qty
    rtn = (close / avg - 1) * 100 if avg else 0.0

    flow_detail = None
    fdf = get_flow(code)
    if fdf is not None and not fdf.empty:
        flow_detail = flow_analyze(fdf)

    triggers = []
    if abs(chg) >= TH_CHANGE_PCT:
        triggers.append(f"{'▲' if chg>0 else '▼'}{abs(chg):.1f}%")
    if vmult >= TH_VOL_MULT:
        triggers.append(f"거래량 {vmult:.1f}배")
    if flow_detail:  # 단기 반전은 트리거로도 사용
        for inv, lab in [("외국인", "외인"), ("기관", "기관")]:
            if flow_detail[inv]["단기"]:
                triggers.append(f"{lab} {flow_detail[inv]['단기']}")
                break
    if close >= hi52 * (1 - TH_NEAR_HL_PCT/100):
        triggers.append("52주고가 근접")
    if close <= lo52 * (1 + TH_NEAR_HL_PCT/100):
        triggers.append("52주저가 근접")

    return {"name": pos["name"], "code": code, "close": close, "chg": chg,
            "vmult": vmult, "pos_pct": pos_pct, "pl": pl, "rtn": rtn,
            "buy_amt": avg*qty, "eval_amt": close*qty,
            "triggers": triggers, "flow": flow_detail}

# ── 리포트 구성 ──────────────────────────────────────────────────
def build_messages(idx, rows, trig_rows, news_map, date_str):
    msgs = []
    tot_buy  = sum(r["buy_amt"]  for r in rows)
    tot_eval = sum(r["eval_amt"] for r in rows)
    tot_pl, tot_rtn = tot_eval - tot_buy, ((tot_eval/tot_buy - 1)*100 if tot_buy else 0)
    def idx_line(i):
        return f"{i['label']} n/a" if i["close"] is None else f"{i['label']} {i['close']:,.0f} ({i['chg']:+.1f}%)"
    head = (f"📈 데일리 브리핑 {date_str}\n{idx_line(idx['kospi'])} / {idx_line(idx['kosdaq'])}\n"
            f"━━━━━━\n평가 {man(tot_eval):,}만 · 손익 {man(tot_pl):+,}만 ({tot_rtn:+.1f}%)\n"
            f"⚡ 트리거 {len(trig_rows)}종목")
    if trig_rows:
        head += "\n" + ", ".join(r["name"] for r in trig_rows)
    msgs.append(head[:200])
    for r in trig_rows:
        block = (f"⚡ {r['name']} {r['close']:,.0f} ({r['chg']:+.1f}%)\n"
                 f"{' / '.join(r['triggers'])}\n"
                 f"평손 {man(r['pl']):+,}만 ({r['rtn']:+.1f}%) · 52주 {r['pos_pct']:.0f}%")
        if r["flow"]:
            block += "\n" + flow_text(r["flow"])
        nl = news_map.get(r["name"], [])
        if nl:
            block += f"\n📰 {nl[0]['title'][:42]}"
        msgs.append(block[:200])
    if not trig_rows:
        msgs.append("오늘은 발동된 트리거가 없습니다. 전체 현황은 첨부 리포트를 확인하세요.")
    return msgs[:CAP_KAKAO_MSGS]

def build_html(idx, rows, trig_rows, news_map, date_str):
    tot_buy  = sum(r["buy_amt"]  for r in rows)
    tot_eval = sum(r["eval_amt"] for r in rows)
    tot_pl, tot_rtn = tot_eval - tot_buy, ((tot_eval/tot_buy - 1)*100 if tot_buy else 0)
    def c(v): return "pos" if v >= 0 else "neg"
    trs = ""
    for r in sorted(rows, key=lambda x: x["pl"]):
        badge = "".join(f"<span class='b'>{t}</span>" for t in r["triggers"])
        flow = flow_cell(r["flow"]) if r["flow"] else "<span class=sub>-</span>"
        trs += (f"<tr><td>{r['name']}</td><td class='r'>{r['close']:,.0f}</td>"
                f"<td class='r {c(r['chg'])}'>{r['chg']:+.1f}%</td>"
                f"<td class='r {c(r['pl'])}'>{man(r['pl']):+,}만</td>"
                f"<td class='r {c(r['rtn'])}'>{r['rtn']:+.1f}%</td>"
                f"<td class='r'>{r['vmult']:.1f}x</td>"
                f"<td class='r'>{r['pos_pct']:.0f}%</td>"
                f"<td class='flow'>{flow}</td><td>{badge}</td></tr>")
    news_html = ""
    for r in trig_rows:
        nl = news_map.get(r["name"], [])
        if nl:
            lis = "".join(f"<li><a href='{n['link']}' target='_blank'>{n['title']}</a></li>" for n in nl)
            news_html += f"<h4>{r['name']}</h4><ul>{lis}</ul>"
    def idx_html(i):
        return f"{i['label']} n/a" if i["close"] is None else f"{i['label']} {i['close']:,.0f} <span class='{c(i['chg'])}'>({i['chg']:+.1f}%)</span>"
    return f"""<!doctype html><html lang=ko><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>데일리 브리핑 {date_str}</title>
<style>
body{{font-family:-apple-system,'Malgun Gothic',sans-serif;margin:0;padding:16px;background:#0f1115;color:#e6e6e6}}
h1{{font-size:18px;margin:0 0 4px}} h4{{margin:12px 0 4px;color:#8ab4f8}}
.sub{{color:#9aa0a6;font-size:13px;margin-bottom:12px}}
.card{{background:#171a21;border-radius:12px;padding:14px;margin-bottom:14px}}
.big{{font-size:20px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:7px 6px;border-bottom:1px solid #262a33;text-align:left;white-space:nowrap}}
th{{color:#9aa0a6;font-weight:600}} .r{{text-align:right}} .flow{{font-size:12px;line-height:1.5}}
.pos{{color:#ff5c5c}} .neg{{color:#4b9fff}}
.b{{display:inline-block;background:#2a2f3a;color:#ffd166;border-radius:6px;padding:1px 6px;margin:1px;font-size:11px}}
a{{color:#8ab4f8;text-decoration:none}} .overflow{{overflow-x:auto}}
</style>
<h1>📈 데일리 브리핑</h1><div class=sub>{date_str} · KST 07:00</div>
<div class=card>
  <div>{idx_html(idx['kospi'])} &nbsp; {idx_html(idx['kosdaq'])}</div>
  <div class=big style='margin-top:8px'>
    평가 {man(tot_eval):,}만 · <span class='{c(tot_pl)}'>손익 {man(tot_pl):+,}만 ({tot_rtn:+.1f}%)</span>
  </div>
  <div class=sub>매입 {man(tot_buy):,}만 · 트리거 {len(trig_rows)}종목</div>
</div>
<div class=card><b>⚡ 트리거 발동</b>: {', '.join(r['name'] for r in trig_rows) or '없음'}</div>
<div class='card overflow'>
<table><tr><th>종목</th><th class=r>종가</th><th class=r>등락</th><th class=r>평가손익</th>
<th class=r>수익률</th><th class=r>거래량</th><th class=r>52주</th><th>수급 단중장</th><th>트리거</th></tr>
{trs}</table>
<div class=sub style='margin-top:8px'>수급 = 외국인/기관, 순서 단기·중기(20일)·장기(120일) · ↑매수전환 ↓매도전환 ▲순매수 ▼순매도 ·중립</div>
</div>
{f"<div class=card><b>📰 뉴스</b>{news_html}</div>" if news_html else ""}
<div class=sub>※ 종가 기준(전일). 정보 제공용이며 투자 판단은 본인 책임.</div>
</html>"""

# ── 카카오 발송 ──────────────────────────────────────────────────
def send_kakao(msgs):
    key, rt = os.environ.get("KAKAO_REST_KEY"), os.environ.get("KAKAO_REFRESH_TOKEN")
    if not (key and rt):
        print("[DRY-RUN] 카카오 미설정 → 콘솔 출력\n" + "\n---\n".join(msgs))
        return
    from kakao import refresh_access_token, send_text
    at, new_rt = refresh_access_token(key, rt)
    print(f"[카카오] 토큰 재발급 OK (access_token 길이 {len(at)})")
    if new_rt and new_rt != rt:
        print(f"::warning::카카오 refresh_token 갱신됨. GitHub Secret 업데이트 권장:\n{new_rt}")
    ok = 0
    for n, m in enumerate(msgs, 1):
        try:
            res = send_text(at, m)
            ok += 1
            print(f"[카카오] {n}/{len(msgs)} 전송 → {res} (본문 {len(m)}자, 첫줄: {m.splitlines()[0][:30]})")
        except Exception as e:
            print(f"[카카오] {n}/{len(msgs)} 실패 → {e}")
        time.sleep(1.0)
    print(f"카카오 발송 결과: 성공 {ok}건 / 전체 {len(msgs)}건")

# ── 메인 ─────────────────────────────────────────────────────────
def main():
    date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d (%a)")
    frm, to = daterange()
    with open(os.path.join(HERE, "holdings.json"), encoding="utf-8") as f:
        holdings = json.load(f)["positions"]

    idx = {"kospi": get_index(frm, to, "KS11", "코스피"),
           "kosdaq": get_index(frm, to, "KQ11", "코스닥")}

    rows, skipped = [], []
    for pos in holdings:
        if not pos.get("code"):
            skipped.append(pos["name"]); continue
        try:
            rows.append(analyze(pos, frm, to))
        except Exception as e:
            skipped.append(f"{pos['name']}({e})")
    if skipped:
        print("[안내] 스킵된 종목: " + ", ".join(skipped))

    trig_rows = [r for r in rows if r["triggers"]]
    news_map = {r["name"]: naver_news(r["name"], 2) for r in trig_rows}

    msgs = build_messages(idx, rows, trig_rows, news_map, date_str)
    html = build_html(idx, rows, trig_rows, news_map, date_str)

    out = os.path.join(HERE, "output", f"brief_{datetime.datetime.now(KST):%Y%m%d}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"리포트 저장: {out}")
    send_kakao(msgs)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
