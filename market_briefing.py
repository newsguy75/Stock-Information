# -*- coding: utf-8 -*-
"""
market_briefing.py
==================
한국시간 10시(베트남 8시) 아침 시황 브리핑.
데이터 나열형(A) + 뉴스 헤드라인(C) 조합.

수집 항목:
1. 미국 전일 마감 - 지수(다우/나스닥/S&P/SOX) + 반도체·테크 개별 종목
2. 원자재/금리 - WTI, 10년물 국채, 원달러
3. 한국 실시간 - KOSPI/KOSDAQ (이미 data_feed에 있음)
4. 국내 상승/하락 TOP - 네이버 크롤링
5. 시황 뉴스 헤드라인 - 네이버 금융 뉴스

실행: python market_briefing.py [--demo] [--dry-run]
"""
from __future__ import annotations
import os
import json
import time
import datetime as dt
import argparse
import requests
import pandas as pd

# ======================================================================
# 1. 미국 시장 (FDR)
# ======================================================================
US_INDEX_MAP = {
    "다우": "DJI",           # Dow Jones
    "나스닥": "IXIC",         # Nasdaq
    "S&P500": "US500",       # S&P 500
    "SOX 반도체": "SOX",     # Philadelphia Semiconductor
}

US_STOCKS = [
    ("엔비디아", "NVDA"),
    ("마이크론", "MU"),
    ("웨스턴디지털", "WDC"),
    ("샌디스크", "SNDK"),
    ("AMD", "AMD"),
    ("TSMC", "TSM"),
]

COMMODITIES = {
    "WTI 유가": "CL=F",
    "10년 국채": "US10YT=X",
    "원달러": "USD/KRW",
    "달러인덱스": "DX-Y.NYB",
}


def _fetch_us_symbol(symbol: str, days: int = 5) -> dict | None:
    """단일 심볼의 최근 종가와 등락률 반환. 실패 시 None."""
    try:
        import FinanceDataReader as fdr
        end = dt.date.today()
        start = end - dt.timedelta(days=days)
        df = fdr.DataReader(symbol, start, end)
        if len(df) < 2:
            return None
        df.columns = [c.lower() for c in df.columns]
        last = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2])
        chg = (last - prev) / prev * 100
        return {"close": last, "change_pct": round(chg, 2),
                "date": str(df.index[-1].date())}
    except Exception as e:
        return {"error": str(e)}


def fetch_us_indices() -> dict:
    """미국 지수 4종 전일 마감."""
    out = {}
    for name, sym in US_INDEX_MAP.items():
        r = _fetch_us_symbol(sym)
        if r and "close" in r:
            out[name] = r
    return out


def fetch_us_stocks() -> dict:
    """미국 반도체·테크 개별 종목."""
    out = {}
    for name, sym in US_STOCKS:
        r = _fetch_us_symbol(sym)
        if r and "close" in r:
            out[name] = r
        time.sleep(0.1)   # rate limit 방지
    return out


def fetch_commodities() -> dict:
    """원자재/금리/환율."""
    out = {}
    for name, sym in COMMODITIES.items():
        r = _fetch_us_symbol(sym)
        if r and "close" in r:
            out[name] = r
        time.sleep(0.1)
    return out


# ======================================================================
# 2. 한국 상승/하락 TOP (네이버 크롤링)
# ======================================================================
def fetch_kr_top_movers(market: str = "kospi", top_n: int = 10) -> dict:
    """상승률/하락률 TOP N. sosok=0(코스피), 1(코스닥).
    URL: finance.naver.com/sise/sise_rise.naver?sosok=0
         finance.naver.com/sise/sise_fall.naver?sosok=0"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False, "err": "bs4 미설치"}

    sosok = "0" if market.lower() == "kospi" else "1"

    def parse(url):
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", class_="type_2")
        if not table:
            return []
        rows = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tds[1].find("a") if len(tds) > 1 else None
            if not a:
                continue
            name = a.get_text(strip=True)
            if not name:
                continue
            try:
                price = tds[2].get_text(strip=True).replace(",", "")
                chg_pct = tds[4].get_text(strip=True).replace(",", "").replace("%", "")
                # +/- 부호는 span class로 있을 수 있음
                if "-" not in chg_pct and "+" not in chg_pct:
                    # 하락 페이지는 부호 없이 값만 있는 경우
                    chg_pct = "-" + chg_pct if "sise_fall" in url else "+" + chg_pct
                rows.append({
                    "name": name,
                    "price": int(price) if price.isdigit() else price,
                    "change_pct": float(chg_pct),
                })
                if len(rows) >= top_n:
                    break
            except (ValueError, IndexError):
                continue
        return rows

    try:
        rise = parse(f"https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}")
        fall = parse(f"https://finance.naver.com/sise/sise_fall.naver?sosok={sosok}")
        return {"ok": True, "market": market.upper(),
                "rise": rise[:top_n], "fall": fall[:top_n]}
    except Exception as e:
        return {"ok": False, "err": str(e)}


# ======================================================================
# 3. 시황 뉴스 헤드라인 (네이버 금융 뉴스)
# ======================================================================
def fetch_market_news(top_n: int = 5) -> list[dict]:
    """네이버 금융 시황 뉴스 헤드라인 top N.
    URL: finance.naver.com/news/mainnews.naver"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    url = "https://finance.naver.com/news/mainnews.naver"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        out = []
        # 뉴스 리스트 - li.block1 안에 dl > dd.articleSubject > a
        for item in soup.select("li.block1")[:top_n]:
            a = item.select_one("dd.articleSubject a")
            if not a:
                continue
            title = a.get_text(strip=True)
            link = a.get("href", "")
            if link and not link.startswith("http"):
                link = "https://finance.naver.com" + link
            # 요약
            summary_el = item.select_one("dd.articleSummary")
            summary = ""
            if summary_el:
                # 언론사 이름과 날짜 제거
                for span in summary_el.select("span"):
                    span.decompose()
                summary = summary_el.get_text(strip=True)
            # 언론사
            press_el = item.select_one("dd.articleSummary span.press")
            press = press_el.get_text(strip=True) if press_el else ""

            out.append({
                "title": title,
                "link": link,
                "summary": summary[:120],
                "press": press,
            })
        return out
    except Exception as e:
        print(f"[warn] 뉴스 크롤링 실패: {e}")
        return []


# ======================================================================
# 4. 데모 데이터
# ======================================================================
def _demo_data() -> dict:
    return {
        "us_indices": {
            "다우": {"close": 44321.5, "change_pct": -0.85, "date": "2026-08-06"},
            "나스닥": {"close": 18745.2, "change_pct": -0.06, "date": "2026-08-06"},
            "S&P500": {"close": 5620.4, "change_pct": -0.32, "date": "2026-08-06"},
            "SOX 반도체": {"close": 5850.1, "change_pct": 0.33, "date": "2026-08-06"},
        },
        "us_stocks": {
            "엔비디아": {"close": 142.5, "change_pct": -0.10, "date": "2026-08-06"},
            "마이크론": {"close": 87.3, "change_pct": -1.31, "date": "2026-08-06"},
            "웨스턴디지털": {"close": 62.4, "change_pct": -13.03, "date": "2026-08-06"},
            "샌디스크": {"close": 45.2, "change_pct": -6.81, "date": "2026-08-06"},
        },
        "commodities": {
            "WTI 유가": {"close": 78.32, "change_pct": 2.8, "date": "2026-08-06"},
            "10년 국채": {"close": 4.676, "change_pct": 0.5, "date": "2026-08-06"},
            "원달러": {"close": 1385.4, "change_pct": -0.3, "date": "2026-08-06"},
        },
        "kr_kospi_top": {
            "ok": True, "market": "KOSPI",
            "rise": [
                {"name": "BGF리테일", "price": 245000, "change_pct": 15.2},
                {"name": "한화솔루션", "price": 42350, "change_pct": 11.4},
                {"name": "엘앤에프", "price": 82100, "change_pct": 9.1},
            ],
            "fall": [
                {"name": "SK하이닉스", "price": 195000, "change_pct": -2.68},
            ],
        },
        "news": [
            {"title": "편의점 3사 2분기 어닝서프라이즈, BGF리테일 15% 급등",
             "summary": "편의점 3사가 예상을 뛰어넘는 실적을 발표하며...",
             "press": "매일경제", "link": ""},
            {"title": "미국, 중국산 폴리실리콘 파생상품에 15% 관세 부과",
             "summary": "한화솔루션 등 국내 태양광 관련주 급등",
             "press": "한국경제", "link": ""},
        ],
    }


# ======================================================================
# 5. 브리핑 조립
# ======================================================================
def collect_all(demo: bool = False) -> dict:
    if demo:
        return _demo_data()
    return {
        "us_indices": fetch_us_indices(),
        "us_stocks": fetch_us_stocks(),
        "commodities": fetch_commodities(),
        "kr_kospi_top": fetch_kr_top_movers("kospi"),
        "kr_kosdaq_top": fetch_kr_top_movers("kosdaq"),
        "news": fetch_market_news(),
    }


def _icon(chg: float) -> str:
    if chg > 0.05:
        return "🔴"
    if chg < -0.05:
        return "🔵"
    return "⚪"


def build_kakao_text(data: dict) -> str:
    now_vn = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    lines = [f"📊 시장 브리핑 (VN {now_vn:%m/%d %H:%M})", "─" * 22]

    # 미국 지수
    lines.append("🇺🇸 미국 전일 마감")
    for name, r in data.get("us_indices", {}).items():
        lines.append(f"  {_icon(r['change_pct'])} {name} {r['close']:,.2f} ({r['change_pct']:+.2f}%)")

    # 원자재/금리
    if data.get("commodities"):
        lines.append("")
        lines.append("💰 원자재/금리")
        for name, r in data["commodities"].items():
            unit = "%" if "국채" in name else ""
            lines.append(f"  {_icon(r['change_pct'])} {name} {r['close']:,.2f}{unit} ({r['change_pct']:+.2f}%)")

    # 미국 개별
    if data.get("us_stocks"):
        lines.append("")
        lines.append("💻 미국 반도체·테크")
        for name, r in data["us_stocks"].items():
            lines.append(f"  {_icon(r['change_pct'])} {name} ({r['change_pct']:+.2f}%)")

    # 국내 상승/하락 TOP
    kospi = data.get("kr_kospi_top", {})
    if kospi.get("ok") and kospi.get("rise"):
        lines.append("")
        lines.append("🔴 코스피 상승 TOP")
        for it in kospi["rise"][:5]:
            lines.append(f"  • {it['name']} ({it['change_pct']:+.1f}%)")
    if kospi.get("ok") and kospi.get("fall"):
        lines.append("🔵 코스피 하락 TOP")
        for it in kospi["fall"][:5]:
            lines.append(f"  • {it['name']} ({it['change_pct']:+.1f}%)")

    kosdaq = data.get("kr_kosdaq_top", {})
    if kosdaq.get("ok") and kosdaq.get("rise"):
        lines.append("")
        lines.append("🔴 코스닥 상승 TOP")
        for it in kosdaq["rise"][:5]:
            lines.append(f"  • {it['name']} ({it['change_pct']:+.1f}%)")

    # 뉴스
    news = data.get("news", [])
    if news:
        lines.append("")
        lines.append("📰 시황 뉴스")
        for n in news[:5]:
            lines.append(f"  • {n['title']}")

    return "\n".join(lines)


def build_html(data: dict) -> str:
    """뷰어용 HTML 리포트 - 리포트 뷰어 스타일과 통일."""
    now_vn = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    C_UP, C_DOWN, C_SUB = "#e2483d", "#3b7be0", "#9aa0aa"
    C_BG, C_CARD, C_LINE, C_TEXT = "#0f1115", "#1a1d24", "#2a2e37", "#e6e8ec"

    def color(chg):
        return C_UP if chg > 0.05 else (C_DOWN if chg < -0.05 else C_SUB)

    def sec(title, inner):
        return (f'<div style="background:{C_CARD};border:1px solid {C_LINE};'
                f'border-radius:10px;padding:12px 14px;margin-bottom:10px">'
                f'<div style="font-size:13px;font-weight:700;color:{C_SUB};'
                f'margin-bottom:8px">{title}</div>{inner}</div>')

    def row(k, v):
        return (f'<div style="display:flex;padding:4px 0;border-top:1px solid {C_LINE}">'
                f'<span style="color:{C_SUB};min-width:120px">{k}</span>'
                f'<span style="flex:1">{v}</span></div>')

    # 미국 지수
    us_idx_rows = []
    for name, r in data.get("us_indices", {}).items():
        c = color(r['change_pct'])
        us_idx_rows.append(row(name,
            f'<b style="color:{c}">{r["close"]:,.2f}</b> '
            f'<span style="color:{c}">({r["change_pct"]:+.2f}%)</span>'))
    us_idx_html = sec("🇺🇸 미국 전일 마감",
                      "".join(us_idx_rows) if us_idx_rows else '<div style="color:#5a5f6a">데이터 없음</div>')

    # 원자재
    comm_rows = []
    for name, r in data.get("commodities", {}).items():
        c = color(r['change_pct'])
        unit = "%" if "국채" in name else ""
        comm_rows.append(row(name,
            f'<b style="color:{c}">{r["close"]:,.2f}{unit}</b> '
            f'<span style="color:{c}">({r["change_pct"]:+.2f}%)</span>'))
    comm_html = sec("💰 원자재·금리·환율", "".join(comm_rows) if comm_rows else '<div style="color:#5a5f6a">데이터 없음</div>')

    # 미국 개별
    us_stock_rows = []
    for name, r in data.get("us_stocks", {}).items():
        c = color(r['change_pct'])
        us_stock_rows.append(row(name,
            f'${r["close"]:,.2f} <span style="color:{c}">({r["change_pct"]:+.2f}%)</span>'))
    us_stock_html = sec("💻 미국 반도체·테크", "".join(us_stock_rows) if us_stock_rows else '<div style="color:#5a5f6a">데이터 없음</div>')

    # 국내 TOP
    def movers_html(title, mv):
        if not (mv and mv.get("ok")):
            return sec(title, '<div style="color:#5a5f6a">데이터 없음</div>')
        rise_rows = []
        for it in (mv.get("rise") or [])[:10]:
            rise_rows.append(row(f'🔴 {it["name"]}',
                f'{it["price"]:,}원 <span style="color:{C_UP}">({it["change_pct"]:+.2f}%)</span>'))
        fall_rows = []
        for it in (mv.get("fall") or [])[:10]:
            fall_rows.append(row(f'🔵 {it["name"]}',
                f'{it["price"]:,}원 <span style="color:{C_DOWN}">({it["change_pct"]:+.2f}%)</span>'))
        inner = ('<div style="font-size:12px;color:#c8ccd4;margin:6px 0 4px">상승률 TOP</div>' + "".join(rise_rows) +
                 '<div style="font-size:12px;color:#c8ccd4;margin:10px 0 4px">하락률 TOP</div>' + "".join(fall_rows))
        return sec(title, inner)

    kospi_html = movers_html("📈 코스피 등락률 TOP", data.get("kr_kospi_top"))
    kosdaq_html = movers_html("📉 코스닥 등락률 TOP", data.get("kr_kosdaq_top"))

    # 뉴스
    news_items = []
    for n in (data.get("news") or [])[:8]:
        press = f' <span style="color:{C_SUB};font-size:12px">· {n.get("press","")}</span>' if n.get("press") else ""
        link_open = f'<a href="{n["link"]}" target="_blank" style="color:{C_TEXT};text-decoration:none">' if n.get("link") else "<span>"
        link_close = "</a>" if n.get("link") else "</span>"
        news_items.append(
            f'<div style="padding:8px 0;border-top:1px solid {C_LINE}">'
            f'{link_open}<b>{n["title"]}</b>{link_close}{press}'
            f'<div style="color:{C_SUB};font-size:12px;margin-top:2px">{n.get("summary","")}</div>'
            f'</div>')
    news_html = sec("📰 시황 뉴스 헤드라인",
                    "".join(news_items) if news_items else '<div style="color:#5a5f6a">뉴스 없음</div>')

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>시장 브리핑 · {now_vn:%Y-%m-%d}</title>
<style>
  body {{ margin:0; background:{C_BG}; color:{C_TEXT};
    font-family:-apple-system,'Segoe UI','Malgun Gothic',sans-serif;
    font-size:14px; line-height:1.5; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:16px; }}
  @media (max-width:800px) {{ .wrap {{ padding:12px; }} }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .meta {{ color:{C_SUB}; font-size:13px; margin-bottom:14px; }}
</style></head>
<body><div class="wrap">
  <h1>📊 시장 브리핑</h1>
  <div class="meta">{now_vn:%Y년 %m월 %d일 %H:%M} (VN 기준)</div>
  {us_idx_html}{comm_html}{us_stock_html}{kospi_html}{kosdaq_html}{news_html}
</div></body></html>"""


# ======================================================================
# 6. 저장 + 카톡/이메일 전송
# ======================================================================
def save_html(html: str) -> str:
    """뷰어와 같은 폴더 구조로 저장. viewer.html이 자동 링크할 수 있도록
    manifest.json에도 시장 브리핑을 special item으로 추가하는 건 별도 처리."""
    now_vn = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    data_root = os.environ.get("DATA_ROOT", "data")
    day_dir = os.path.join(data_root, now_vn.strftime("%Y-%m-%d"))
    latest = os.path.join(data_root, "latest")
    os.makedirs(day_dir, exist_ok=True)
    os.makedirs(latest, exist_ok=True)

    hm = now_vn.strftime("%H%M")
    day_path = os.path.join(day_dir, f"MARKET_시황_{hm}.html")
    latest_path = os.path.join(latest, "MARKET_시황.html")

    with open(day_path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(html)
    return latest_path


def send_kakao(text: str):
    """카톡 전송. portfolio_briefing.py의 함수 재사용."""
    try:
        from portfolio_briefing import refresh_access_token, send_kakao_message
        token = refresh_access_token()
        if not token:
            print("[error] 카카오 토큰 없음")
            return False
        return send_kakao_message(text, token)
    except Exception as e:
        print(f"[warn] 카톡 전송 실패: {e}")
        return False


# ======================================================================
# 7. 엔트리
# ======================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    print("=== 시장 브리핑 수집 시작 ===")
    data = collect_all(demo=args.demo)

    text = build_kakao_text(data)
    html = build_html(data)
    saved = save_html(html)
    print(f"[저장] {saved}")

    if args.dry_run or args.demo:
        print("\n" + text)
        print(f"\n[dry-run] HTML: {saved}")
        return

    ok = send_kakao(text)
    print(f"[카톡 전송] {'성공' if ok else '실패'}")


if __name__ == "__main__":
    main()
