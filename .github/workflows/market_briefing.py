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
import re
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
    "나스닥": "IXIC",         # Nasdaq Composite
    "S&P500": "US500",       # S&P 500
    # "SOX 반도체": FDR 미지원, 필요 시 SOXX ETF로 대체
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
    # FDR 지원 심볼만 사용. Yahoo 심볼(=F, =X, -Y.NYB)는 미지원
    "원달러": "USD/KRW",
    # WTI 유가·국채·달러인덱스는 FDR 미지원 → 네이버 크롤링 폴백 필요
}


def _fetch_us_symbol(symbol: str, days: int = 5) -> dict | None:
    """단일 심볼의 최근 종가와 등락률 반환. 실패 시 None."""
    try:
        import FinanceDataReader as fdr
        end = dt.date.today()
        start = end - dt.timedelta(days=days + 3)  # 주말 감안 여유
        df = fdr.DataReader(symbol, start, end)
        if len(df) < 2:
            print(f"[warn] {symbol}: 데이터 {len(df)}개")
            return None
        df.columns = [c.lower() for c in df.columns]
        if "close" not in df.columns:
            print(f"[warn] {symbol}: close 컬럼 없음. 컬럼={list(df.columns)}")
            return None
        last = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2])
        if prev == 0 or last == 0:
            return None
        chg = (last - prev) / prev * 100
        r = {"close": last, "change_pct": round(chg, 2),
             "date": str(df.index[-1].date())}
        print(f"[US심볼] {symbol}: {last:.2f} ({chg:+.2f}%) [{r['date']}]")
        return r
    except Exception as e:
        print(f"[warn] {symbol}: {e}")
        return None



def fetch_commodities() -> dict:
    """원자재/금리/환율. FDR + Yahoo 폴백 조합."""
    out = {}
    # FDR로 원달러
    for name, sym in COMMODITIES.items():
        r = _fetch_us_symbol(sym)
        if r:
            out[name] = r
        time.sleep(0.1)

    # Yahoo로 나머지 (FDR 미지원)
    #  ※ 리튬은 거래 가능한 선물이 없어 대표 ETF(LIT)를 프록시로 사용
    yahoo_targets = [
        ("WTI 유가",   "CL=F"),
        ("10년 국채",  "^TNX"),
        ("달러인덱스", "DX-Y.NYB"),
        ("구리",       "HG=F"),
        ("알루미늄",   "ALI=F"),
        ("리튬(ETF)",  "LIT"),
    ]
    for name, sym in yahoo_targets:
        if name in out:
            continue
        r = _fetch_yahoo_quote(sym)
        if r:
            out[name] = r
        time.sleep(0.1)
    return out


_YH_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "application/json",
}


def _fetch_yahoo_quote(symbol: str) -> dict | None:
    """Yahoo 단일 심볼 조회.
    1차: v8/finance/chart  (쿠키·crumb 불필요 — GitHub Actions에서도 동작)
    2차: v7/finance/quote  (구 엔드포인트, 401 나면 skip)
    """
    # --- 1차: v8 chart ---
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range=5d&interval=1d")
    try:
        r = requests.get(url, timeout=10, headers=_YH_HEADERS)
        if r.status_code == 200:
            j = r.json()
            res = (j.get("chart", {}).get("result") or [None])[0]
            if res:
                meta = res.get("meta", {})
                last = meta.get("regularMarketPrice")
                prev = (meta.get("chartPreviousClose")
                        or meta.get("previousClose"))
                # meta에 없으면 종가 배열에서 직접 계산
                if last is None or prev is None:
                    try:
                        closes = [c for c in res["indicators"]["quote"][0]["close"]
                                  if c is not None]
                        if len(closes) >= 2:
                            last = last if last is not None else closes[-1]
                            prev = prev if prev is not None else closes[-2]
                    except Exception:
                        pass
                if last is not None and prev:
                    chg = (float(last) - float(prev)) / float(prev) * 100
                    return {"close": float(last),
                            "change_pct": round(chg, 2),
                            "date": str(dt.date.today())}
        else:
            print(f"[warn] Yahoo v8 {symbol}: HTTP {r.status_code}")
    except Exception as e:
        print(f"[warn] Yahoo v8 {symbol}: {e}")

    # --- 2차: v7 quote (폴백) ---
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}",
            timeout=8, headers=_YH_HEADERS)
        if r.status_code != 200:
            return None
        j = r.json()
        results = j.get("quoteResponse", {}).get("result", [])
        if not results:
            return None
        d = results[0]
        last = d.get("regularMarketPrice")
        if last is None:
            return None
        return {"close": float(last),
                "change_pct": round(float(d.get("regularMarketChangePercent") or 0), 2),
                "date": str(dt.date.today())}
    except Exception as e:
        print(f"[warn] Yahoo v7 {symbol}: {e}")
        return None


def _fetch_yahoo_quotes(symbols: list[str]) -> dict:
    """여러 심볼 조회. v8은 배치를 지원하지 않으므로 순차 호출."""
    out = {}
    for sym in symbols:
        q = _fetch_yahoo_quote(sym)
        if q:
            out[sym] = q
        time.sleep(0.15)   # rate limit 완화
    print(f"[Yahoo] {len(out)}/{len(symbols)} 심볼 수집 성공")
    return out


# 미국 섹터 ETF ↔ 국장 영향 매핑
# (표시명, 심볼, 국내 영향 업종)
US_SECTORS = [
    ("반도체",        "SOXX", "삼성전자·SK하이닉스·소부장"),
    ("기술",          "XLK",  "IT·소프트웨어·플랫폼"),
    ("에너지",        "XLE",  "정유·조선/LNG·해운"),
    ("소재",          "XLB",  "화학·철강·2차전지 소재"),
    ("산업재",        "XLI",  "기계·건설·항공"),
    ("경기소비재",    "XLY",  "자동차·유통·여행"),
    ("필수소비재",    "XLP",  "음식료·생활용품"),
    ("헬스케어",      "XLV",  "제약·바이오"),
    ("금융",          "XLF",  "은행·증권·보험"),
    ("커뮤니케이션",  "XLC",  "미디어·엔터·게임"),
    ("유틸리티",      "XLU",  "전력·에너지인프라"),
    # --- 테마 업종 (국내 주도주와 직결) ---
    ("자동차",        "CARZ", "현대차·기아·부품주"),
    ("원자력",        "URA",  "두산에너빌리티·비에이치아이·우진"),
    ("조선·해운",     "BOAT", "HD현대重·한화오션·세아제강·동성화인텍"),
    ("방산·우주",     "ITA",  "한국항공우주·LIG넥스원·현대로템"),
]


def fetch_us_sectors() -> dict:
    """미국 주요 업종(섹터) 마감 + 국장 영향 업종 매핑.
    1차: 섹터 ETF (Yahoo 배치) — 안정적
    2차: Finviz groups 크롤링 (차단 시 skip)
    """
    syms = [s for _, s, _ in US_SECTORS]
    quotes = _fetch_yahoo_quotes(syms)

    items = []
    for label, sym, kr in US_SECTORS:
        q = quotes.get(sym)
        if not q:
            continue
        items.append({"name": label, "symbol": sym, "kr_impact": kr,
                      "close": q["close"], "change_pct": q["change_pct"]})

    if not items:
        # Finviz 폴백 시도
        fv = _fetch_finviz_sectors()
        if fv:
            return {"ok": True, "source": "finviz", "items": fv}
        return {"ok": False, "err": "섹터 데이터 없음"}

    items.sort(key=lambda x: x["change_pct"], reverse=True)
    return {"ok": True, "source": "sector_etf", "items": items}


def _fetch_finviz_sectors() -> list[dict]:
    """Finviz 섹터 퍼포먼스 크롤링 (폴백용).
    URL: finviz.com/groups.ashx?g=sector&v=210
    차단당하는 경우가 많아 실패해도 조용히 빈 리스트 반환."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    url = "https://finviz.com/groups.ashx?g=sector&v=210"
    try:
        r = requests.get(url, timeout=10, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/122.0 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9",
        })
        if r.status_code != 200:
            print(f"[warn] Finviz status {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for tr in soup.find_all("tr"):
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) < 3:
                continue
            name = tds[1] if len(tds) > 1 else ""
            # 등락률로 보이는 셀 탐색 (% 포함)
            pct = None
            for c in tds[2:]:
                if c.endswith("%"):
                    try:
                        pct = float(c.replace("%", "").replace(",", ""))
                        break
                    except ValueError:
                        continue
            if name and pct is not None:
                out.append({"name": name, "symbol": "", "kr_impact": "",
                            "close": 0.0, "change_pct": pct})
        out.sort(key=lambda x: x["change_pct"], reverse=True)
        return out[:12]
    except Exception as e:
        print(f"[warn] Finviz 크롤링 실패: {e}")
        return []


def fetch_us_indices() -> dict:
    """미국 지수 (FDR 3개 + Yahoo SOX)."""
    out = {}
    for name, sym in US_INDEX_MAP.items():
        r = _fetch_us_symbol(sym)
        if r:
            out[name] = r
    # SOX는 Yahoo로
    sox = _fetch_yahoo_quote("^SOX")
    if sox:
        out["SOX 반도체"] = sox
    return out


def fetch_us_stocks() -> dict:
    """미국 반도체·테크 개별 종목."""
    out = {}
    for name, sym in US_STOCKS:
        r = _fetch_us_symbol(sym)
        if r:
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
# 2-B. 시장 매매동향 (개인/외국인/기관 - 억원 단위)
# ======================================================================
def fetch_market_investor_summary() -> dict:
    """네이버 시장 실시간 투자자별 매매동향.
    URL: finance.naver.com/sise/investorDealTrendTime.naver
    또는 폴백: finance.naver.com/sise/sise_index.naver?code=KOSPI (지수 페이지)
    
    반환: {kospi: {개인, 외국인, 기관}, kosdaq: {...}} 단위: 억원"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False, "err": "bs4 미설치"}

    # 네이버 시황 페이지 (sise/sise_index.naver) 에는 각 시장별 투자자 매매 요약이 있음
    result = {}
    for market_code, key in [("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")]:
        try:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={market_code}"
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")

            # 매매동향 테이블 찾기 - "매매동향" 텍스트 근처의 표
            invest = {"개인": None, "외국인": None, "기관": None}
            # 페이지에 "개인" "외국인" "기관계" 텍스트가 나오는 표를 찾음
            for table in soup.find_all("table"):
                text = table.get_text()
                if "개인" in text and "외국인" in text and ("기관" in text):
                    # 표 안에서 각 라벨 옆의 숫자를 앵커링으로 찾음
                    for tr in table.find_all("tr"):
                        cells = tr.find_all(["th", "td"])
                        if len(cells) < 2:
                            continue
                        label = cells[0].get_text(strip=True)
                        for k in ["개인", "외국인", "기관"]:
                            if k in label and invest[k] is None:
                                # 옆 셀에서 숫자 추출 (억원 단위)
                                val_txt = cells[1].get_text(strip=True)
                                val_txt = val_txt.replace(",", "").replace("+", "")
                                try:
                                    # 부호 처리
                                    sign = -1 if "-" in val_txt else 1
                                    val_txt = val_txt.replace("-", "")
                                    num = float(re.sub(r"[^\d.]", "", val_txt))
                                    invest[k] = sign * num
                                except (ValueError, TypeError):
                                    pass
                    # 다 채워지면 그만
                    if all(v is not None for v in invest.values()):
                        break

            if all(v is not None for v in invest.values()):
                result[key] = invest
        except Exception as e:
            print(f"[warn] {market_code} 매매동향 실패: {e}")
            continue

    if result:
        return {"ok": True, **result}
    return {"ok": False, "err": "매매동향 파싱 실패"}


# ======================================================================
# 2-C. 거래대금 상위 20 (코스피/코스닥)
# ======================================================================
def fetch_top_trading_value(market: str = "kospi", top_n: int = 20) -> dict:
    """거래대금 상위 종목.
    URL: finance.naver.com/sise/sise_quant.naver?sosok=0 (거래량 상위)
    이 페이지는 실제로는 '거래량' 순위임. 표에는 거래량과 거래대금이 함께 나옴.
    
    표 컬럼 순서 (2026 기준):
    [N, 종목명, 현재가, 전일비, 등락률, 거래량, 매수호가, 매도호가, 매수총잔량, 매도총잔량, PER, ROE]
    거래대금 컬럼은 표에 없음. 거래량(주 단위)을 그대로 사용.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False, "err": "bs4 미설치"}

    sosok = "0" if market.lower() == "kospi" else "1"
    url = f"https://finance.naver.com/sise/sise_quant.naver?sosok={sosok}"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", class_="type_2")
        if not table:
            return {"ok": False, "err": "표 없음"}

        # 헤더에서 컬럼 위치 찾기 (안정적 앵커링)
        headers_txt = []
        header_row = table.find("tr")
        if header_row:
            headers_txt = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]

        # 컬럼 인덱스 매핑
        idx_name = idx_price = idx_chg = idx_vol = None
        for i, h in enumerate(headers_txt):
            if h in ("종목명", "Name"):
                idx_name = i
            elif h in ("현재가", "Price"):
                idx_price = i
            elif h in ("등락률", "%"):
                idx_chg = i
            elif h in ("거래량", "Volume"):
                idx_vol = i

        # 기본값 폴백 (헤더 파싱 실패 시)
        if idx_name is None:
            idx_name = 1
        if idx_price is None:
            idx_price = 2
        if idx_chg is None:
            idx_chg = 4
        if idx_vol is None:
            idx_vol = 5

        print(f"[거래대금 컬럼] 헤더={headers_txt}")
        print(f"[거래대금 컬럼] name={idx_name}, price={idx_price}, chg={idx_chg}, vol={idx_vol}")

        rows = []
        rank = 0
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < max(idx_name, idx_price, idx_chg, idx_vol) + 1:
                continue
            a = tds[idx_name].find("a") if idx_name < len(tds) else None
            if not a:
                continue
            name = a.get_text(strip=True)
            if not name:
                continue
            try:
                rank += 1
                price_txt = tds[idx_price].get_text(strip=True).replace(",", "")
                chg_pct_txt = tds[idx_chg].get_text(strip=True).replace(",", "").replace("%", "")

                # 부호 확인 (class 기반)
                if not chg_pct_txt.startswith("-") and not chg_pct_txt.startswith("+"):
                    cls = " ".join(tds[idx_chg].get("class", []))
                    row_cls = " ".join(tr.get("class", []))
                    all_cls = cls + " " + row_cls
                    if "nv01" in all_cls or "down" in all_cls.lower() or "blind" in all_cls.lower():
                        chg_pct_txt = "-" + chg_pct_txt
                    elif chg_pct_txt and chg_pct_txt != "0.00":
                        chg_pct_txt = "+" + chg_pct_txt

                vol_txt = tds[idx_vol].get_text(strip=True).replace(",", "")

                price = int(price_txt) if price_txt.isdigit() else 0
                volume = int(vol_txt) if vol_txt.isdigit() else 0
                # 거래대금 (원 단위) = 거래량 × 현재가
                value_won = volume * price

                rows.append({
                    "rank": rank,
                    "name": name,
                    "price": price,
                    "change_pct": float(chg_pct_txt) if chg_pct_txt else 0,
                    "volume": volume,
                    "value": value_won // 1_000_000,  # 백만원 단위
                })
                if len(rows) >= top_n:
                    break
            except (ValueError, IndexError) as e:
                print(f"[warn] {market} 거래대금 파싱 실패 rank={rank}: {e}")
                continue

        # 실제 거래대금(volume × price) 기준으로 재정렬
        rows.sort(key=lambda x: x["value"], reverse=True)
        for i, row in enumerate(rows, 1):
            row["rank"] = i

        return {"ok": True, "market": market.upper(), "items": rows[:top_n]}
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
        "us_sectors": {
            "ok": True, "source": "sector_etf",
            "items": [
                {"name": "에너지", "symbol": "XLE", "kr_impact": "정유·조선/LNG·해운", "close": 92.1, "change_pct": 2.15},
                {"name": "소재", "symbol": "XLB", "kr_impact": "화학·철강·2차전지 소재", "close": 88.4, "change_pct": 1.02},
                {"name": "산업재", "symbol": "XLI", "kr_impact": "기계·방산·건설·항공", "close": 141.2, "change_pct": 0.41},
                {"name": "유틸리티", "symbol": "XLU", "kr_impact": "전력·원전·에너지인프라", "close": 79.8, "change_pct": 0.18},
                {"name": "금융", "symbol": "XLF", "kr_impact": "은행·증권·보험", "close": 48.3, "change_pct": -0.22},
                {"name": "기술", "symbol": "XLK", "kr_impact": "IT·소프트웨어·플랫폼", "close": 231.7, "change_pct": -0.65},
                {"name": "반도체", "symbol": "SOXX", "kr_impact": "삼성전자·SK하이닉스·소부장", "close": 254.9, "change_pct": -1.84},
                {"name": "원자력", "symbol": "URA", "kr_impact": "두산에너빌리티·비에이치아이·우진", "close": 41.2, "change_pct": 3.05},
                {"name": "조선·해운", "symbol": "BOAT", "kr_impact": "HD현대重·한화오션·세아제강·동성화인텍", "close": 32.5, "change_pct": 1.44},
                {"name": "방산·우주", "symbol": "ITA", "kr_impact": "한국항공우주·LIG넥스원·현대로템", "close": 168.3, "change_pct": 0.72},
                {"name": "자동차", "symbol": "CARZ", "kr_impact": "현대차·기아·부품주", "close": 74.6, "change_pct": -0.31},
                {"name": "필수소비재", "symbol": "XLP", "kr_impact": "음식료·생활용품", "close": 81.9, "change_pct": 0.11},
            ],
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
            "구리": {"close": 4.42, "change_pct": 1.35, "date": "2026-08-06"},
            "알루미늄": {"close": 2612.0, "change_pct": 0.62, "date": "2026-08-06"},
            "리튬(ETF)": {"close": 48.7, "change_pct": -2.10, "date": "2026-08-06"},
        },
        "market_flow": {
            "ok": True,
            "kospi": {"개인": -11853, "외국인": 14514, "기관": -2817},
            "kosdaq": {"개인": 3950, "외국인": -2971, "기관": -1108},
        },
        "kospi_top_value": {
            "ok": True, "market": "KOSPI",
            "items": [
                {"rank": 1, "name": "SK하이닉스", "price": 1668000, "change_pct": 5.77, "value": 3200000},
                {"rank": 2, "name": "삼성전자", "price": 246500, "change_pct": 2.71, "value": 2100000},
                {"rank": 3, "name": "삼성전기", "price": 1351000, "change_pct": 14.01, "value": 1800000},
                {"rank": 4, "name": "SK스퀘어", "price": 1119000, "change_pct": 5.57, "value": 1500000},
                {"rank": 5, "name": "대원전선", "price": 15450, "change_pct": 12.04, "value": 1200000},
            ],
        },
        "kosdaq_top_value": {"ok": True, "market": "KOSDAQ", "items": []},
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
        "kr_kosdaq_top": {"ok": True, "market": "KOSDAQ", "rise": [], "fall": []},
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
        data = _demo_data()
    else:
        data = {
            "us_indices": fetch_us_indices(),
            "us_sectors": fetch_us_sectors(),
            "us_stocks": fetch_us_stocks(),
            "commodities": fetch_commodities(),
            "market_flow": fetch_market_investor_summary(),
            "kospi_top_value": fetch_top_trading_value("kospi", 20),
            "kosdaq_top_value": fetch_top_trading_value("kosdaq", 20),
            "kr_kospi_top": fetch_kr_top_movers("kospi"),
            "kr_kosdaq_top": fetch_kr_top_movers("kosdaq"),
            "news": fetch_market_news(),
        }
    # 업종은 항상 강세순 정렬 (데모/실데이터 동일)
    sd = data.get("us_sectors")
    if isinstance(sd, dict) and sd.get("ok") and sd.get("items"):
        sd["items"].sort(key=lambda x: x.get("change_pct", 0), reverse=True)

    # --- 수집 진단 요약 (로그로 무엇이 비었는지 즉시 확인) ---
    if not demo:
        def _cnt(v):
            if isinstance(v, dict):
                if "items" in v:
                    return len(v.get("items") or [])
                if v.get("ok") is False:
                    return 0
                return len([k for k in v if k not in ("ok", "source", "err", "market")])
            if isinstance(v, list):
                return len(v)
            return 0
        print("\n=== 수집 결과 요약 ===")
        for key, label in [("us_indices", "미국지수"), ("us_sectors", "미국업종"),
                           ("commodities", "원자재"), ("us_stocks", "미국종목"),
                           ("market_flow", "매매동향"), ("kospi_top_value", "코스피거래대금"),
                           ("kosdaq_top_value", "코스닥거래대금"),
                           ("kr_kospi_top", "코스피등락"), ("kr_kosdaq_top", "코스닥등락"),
                           ("news", "뉴스")]:
            v = data.get(key)
            n = _cnt(v)
            mark = "✓" if n else "✗"
            err = ""
            if isinstance(v, dict) and v.get("err"):
                err = f"  ← {v['err']}"
            print(f"  {mark} {label}: {n}건{err}")
        print()

    return data


def _icon(chg: float) -> str:
    if chg > 0.05:
        return "🔴"
    if chg < -0.05:
        return "🔵"
    return "⚪"


def build_kakao_text(data: dict) -> str:
    now_vn = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)
    lines = [f"📊 시장 브리핑 (VN {now_vn:%m/%d %H:%M})", "─" * 22]

    # 시장 매매동향 (개인/외국인/기관 - 억원)
    mf = data.get("market_flow", {})
    if mf.get("ok"):
        lines.append("💹 매매동향 (억원)")
        for market_key, label in [("kospi", "코스피"), ("kosdaq", "코스닥")]:
            m = mf.get(market_key)
            if m:
                def fmt(v):
                    return f"{v:+,.0f}"
                lines.append(f"  {label}: 개인 {fmt(m['개인'])} · 외인 {fmt(m['외국인'])} · 기관 {fmt(m['기관'])}")
        lines.append("")

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

    # 미국 업종(섹터) — 국장 영향 업종 병기
    sec_d = data.get("us_sectors", {})
    if sec_d.get("ok") and sec_d.get("items"):
        lines.append("")
        lines.append("🏭 미국 업종 마감 (국장 영향)")
        its = sec_d["items"]
        for it in its[:5]:
            kr = f" → {it['kr_impact']}" if it.get("kr_impact") else ""
            lines.append(f"  {_icon(it['change_pct'])} {it['name']} {it['change_pct']:+.2f}%{kr}")
        if len(its) > 8:
            lines.append("  ⋯")
        for it in its[-3:]:
            kr = f" → {it['kr_impact']}" if it.get("kr_impact") else ""
            lines.append(f"  {_icon(it['change_pct'])} {it['name']} {it['change_pct']:+.2f}%{kr}")

    # 미국 개별
    if data.get("us_stocks"):
        lines.append("")
        lines.append("💻 미국 반도체·테크")
        for name, r in data["us_stocks"].items():
            lines.append(f"  {_icon(r['change_pct'])} {name} ({r['change_pct']:+.2f}%)")

    # 거래대금 상위 5 (코스피)
    kv = data.get("kospi_top_value", {})
    if kv.get("ok") and kv.get("items"):
        lines.append("")
        lines.append("💵 코스피 거래대금 TOP5")
        for it in kv["items"][:5]:
            lines.append(f"  {it['rank']}. {it['name']} ({it['change_pct']:+.2f}%)")

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

    # 매매동향 (개인/외국인/기관)
    mf = data.get("market_flow", {})
    if mf.get("ok"):
        def flow_row(label, m):
            def cell(v):
                c = color(v)
                return f'<td style="text-align:right;padding:6px 10px;color:{c};font-weight:700">{v:+,.0f}</td>'
            return (f'<tr><td style="padding:6px 10px;font-weight:600">{label}</td>'
                    f'{cell(m["개인"])}{cell(m["외국인"])}{cell(m["기관"])}</tr>')
        flow_inner = (
            f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead><tr style="border-bottom:1px solid {C_LINE}">'
            f'<th style="padding:6px 10px;text-align:left;color:{C_SUB}">시장 (억원)</th>'
            f'<th style="padding:6px 10px;text-align:right;color:{C_SUB}">개인</th>'
            f'<th style="padding:6px 10px;text-align:right;color:{C_SUB}">외국인</th>'
            f'<th style="padding:6px 10px;text-align:right;color:{C_SUB}">기관</th>'
            f'</tr></thead><tbody>'
        )
        if mf.get("kospi"):
            flow_inner += flow_row("코스피", mf["kospi"])
        if mf.get("kosdaq"):
            flow_inner += flow_row("코스닥", mf["kosdaq"])
        flow_inner += '</tbody></table>'
        flow_html = sec("💹 시장 매매동향 (억원)", flow_inner)
    else:
        flow_html = ''

    # 거래대금 상위 20
    def value_top_html(title, tv):
        if not (tv and tv.get("ok") and tv.get("items")):
            return ''
        rows_html = []
        for it in tv["items"][:20]:
            c = color(it["change_pct"])
            value_eok = it.get("value", 0) / 100  # 백만원 → 억원
            rows_html.append(
                f'<tr>'
                f'<td style="padding:5px 10px;color:{C_SUB};text-align:center">{it["rank"]}</td>'
                f'<td style="padding:5px 10px"><b>{it["name"]}</b></td>'
                f'<td style="padding:5px 10px;text-align:right">{it["price"]:,}</td>'
                f'<td style="padding:5px 10px;text-align:right;color:{c};font-weight:700">{it["change_pct"]:+.2f}%</td>'
                f'<td style="padding:5px 10px;text-align:right;color:{C_SUB}">{value_eok:,.0f}억</td>'
                f'</tr>'
            )
        inner = (
            f'<table style="width:100%;border-collapse:collapse;font-size:12px">'
            f'<thead><tr style="border-bottom:1px solid {C_LINE}">'
            f'<th style="padding:6px 10px;text-align:center;color:{C_SUB}">#</th>'
            f'<th style="padding:6px 10px;text-align:left;color:{C_SUB}">종목</th>'
            f'<th style="padding:6px 10px;text-align:right;color:{C_SUB}">현재가</th>'
            f'<th style="padding:6px 10px;text-align:right;color:{C_SUB}">등락률</th>'
            f'<th style="padding:6px 10px;text-align:right;color:{C_SUB}">거래대금</th>'
            f'</tr></thead><tbody>' + "".join(rows_html) + '</tbody></table>'
        )
        return sec(title, inner)

    kospi_value_html = value_top_html("💵 코스피 거래대금 TOP20", data.get("kospi_top_value"))
    kosdaq_value_html = value_top_html("💵 코스닥 거래대금 TOP20", data.get("kosdaq_top_value"))

    # 미국 지수
    us_idx_rows = []
    for name, r in data.get("us_indices", {}).items():
        c = color(r['change_pct'])
        us_idx_rows.append(row(name,
            f'<b style="color:{c}">{r["close"]:,.2f}</b> '
            f'<span style="color:{c}">({r["change_pct"]:+.2f}%)</span>'))
    us_idx_html = sec("🇺🇸 미국 전일 마감",
                      "".join(us_idx_rows) if us_idx_rows else '<div style="color:#5a5f6a">데이터 없음</div>')

    # 미국 업종(섹터) — 국장 영향 매핑
    sec_d = data.get("us_sectors", {})
    if sec_d.get("ok") and sec_d.get("items"):
        srows = []
        for it in sec_d["items"]:
            c = color(it["change_pct"])
            bar_w = min(abs(it["change_pct"]) * 18, 100)
            srows.append(
                f'<tr>'
                f'<td style="padding:5px 10px;font-weight:600">{it["name"]}'
                f'<span style="color:{C_SUB};font-size:11px"> {it.get("symbol","")}</span></td>'
                f'<td style="padding:5px 10px;text-align:right;color:{c};font-weight:700;white-space:nowrap">'
                f'{it["change_pct"]:+.2f}%</td>'
                f'<td style="padding:5px 10px;width:110px">'
                f'<div style="height:6px;border-radius:3px;background:{c};width:{bar_w}%;opacity:.75"></div></td>'
                f'<td style="padding:5px 10px;color:{C_SUB};font-size:12px">{it.get("kr_impact","")}</td>'
                f'</tr>')
        src_tag = ("섹터 ETF 기준" if sec_d.get("source") == "sector_etf" else "Finviz")
        sec_inner = (
            f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead><tr style="border-bottom:1px solid {C_LINE}">'
            f'<th style="padding:6px 10px;text-align:left;color:{C_SUB}">업종</th>'
            f'<th style="padding:6px 10px;text-align:right;color:{C_SUB}">등락</th>'
            f'<th style="padding:6px 10px;color:{C_SUB}"></th>'
            f'<th style="padding:6px 10px;text-align:left;color:{C_SUB}">국내 영향 업종</th>'
            f'</tr></thead><tbody>' + "".join(srows) + '</tbody></table>'
            f'<div style="color:{C_SUB};font-size:11px;margin-top:6px">'
            f'※ {src_tag} · 위=강세 업종, 아래=약세 업종</div>'
        )
        sectors_html = sec("🏭 미국 업종 마감 → 국장 영향도", sec_inner)
    else:
        sectors_html = ''

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
  {flow_html}{us_idx_html}{sectors_html}{comm_html}{us_stock_html}{kospi_html}{kosdaq_html}{kospi_value_html}{kosdaq_value_html}{news_html}
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
