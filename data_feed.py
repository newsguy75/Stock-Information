# -*- coding: utf-8 -*-
"""
data_feed.py
============
데이터 수집 계층.

- 일봉(단타/스윙용, 일봉·주봉 MA5/20/60 + 스토캐 다이버전스 재료): 1년치
- 월봉 계산 전용 소스: 3년치 (월봉 MA5/20 + 다이버전스 재료. MA60은 60개월=5년
  필요해 3년으로는 안 나오지만, 3대 메인 지표 중 스토캐 다이버전스 프레임 유지가
  우선이라 여기서는 MA60은 포기하고 다이버전스/단기 MA만 본다.)
- 1시간봉: Naver 분봉 차트 API를 받아 60분으로 resample
           (pykrx/FDR/yfinance는 국내 분봉 미지원)

기존 대비 변경점 (타임아웃 완화):
  - 예전: 종목당 1콜 x 6년치 (일봉 MA60/주봉 MA60/월봉 MA60 전부 커버 목적)
  - 신규: 종목당 2콜 x (1년치 + 3년치) = 데이터량 약 33% 감소
    -> payload 크기가 타임아웃 원인이었다면 개선.
    -> API 호출 횟수(rate limit)가 원인이었다면 오히려 콜 수가 늘어나므로,
       실제 타임아웃 로그로 원인 재확인 권장 (건당 vs 총량 문제 구분).

주의: 이 모듈의 실제 네트워크 호출은 GitHub Actions / 로컬 등
      외부망이 열린 환경에서 동작합니다. 함수는 모두 방어적으로
      try/except 처리되어 있으며, 실패 시 빈 DataFrame 또는 예외를 반환합니다.
"""
from __future__ import annotations
import io
import datetime as dt
import pandas as pd
import numpy as np
import requests


# ----------------------------------------------------------------------
# 일봉 - 단기 소스 (일/주봉용, 기본 1년)
# ----------------------------------------------------------------------
def fetch_daily_fdr(code: str, years: float = 1.0) -> pd.DataFrame:
    """FinanceDataReader로 일봉 수집.
    일봉 MA60(60일) / 주봉 MA5·20(약 5주·20주) 계산에는 1년(약 245거래일)이면
    충분. 주봉 MA60(=60주=약1.15년)은 1년으로는 근소하게 부족해 생략될 수 있음.
    """
    import FinanceDataReader as fdr
    end = dt.date.today()
    start = end - dt.timedelta(days=int(years * 365.25))
    df = fdr.DataReader(code, start, end)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def fetch_daily_pykrx(code: str, years: float = 1.0) -> pd.DataFrame:
    """pykrx fallback (단기 소스)."""
    from pykrx import stock
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=int(years * 365.25))).strftime("%Y%m%d")
    df = stock.get_market_ohlcv(start, end, code)
    df = df.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                             "종가": "close", "거래량": "volume"})
    df = df[["open", "high", "low", "close", "volume"]]
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def fetch_daily(code: str, years: float = 1.0) -> pd.DataFrame:
    """일봉(단기, 기본 1년): FDR + pykrx 중 최신 선택 + 네이버 실시간 보정.

    FDR/pykrx가 오늘 봉을 아직 안 받았으면 (또는 장중이라 확정 전이면)
    네이버 실시간 시세로 오늘 봉을 만들어 붙이거나 갱신한다.
    """
    df_fdr, df_pk = None, None
    try:
        df_fdr = fetch_daily_fdr(code, years)
        if len(df_fdr) == 0:
            df_fdr = None
    except Exception as e:
        print(f"[warn] FDR 실패 {code}: {e}")
    try:
        df_pk = fetch_daily_pykrx(code, years)
        if len(df_pk) == 0:
            df_pk = None
    except Exception as e:
        print(f"[warn] pykrx 실패 {code}: {e}")

    if df_fdr is None and df_pk is None:
        print(f"[error] 일봉 수집 전부 실패 {code}")
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if df_fdr is None:
        chosen = _drop_stale_tail(df_pk, code)
    elif df_pk is None:
        chosen = _drop_stale_tail(df_fdr, code)
    else:
        last_fdr = df_fdr.index[-1]
        last_pk = df_pk.index[-1]
        chosen = df_fdr if last_fdr >= last_pk else df_pk
        src = "FDR" if last_fdr >= last_pk else "pykrx"
        if last_fdr != last_pk:
            print(f"[info] {code} 일봉 끝단: FDR {last_fdr.date()} / pykrx {last_pk.date()} → {src} 채택")
        chosen = _drop_stale_tail(chosen, code)

    # 네이버 실시간 보정 (오늘 봉 누락 or 장중 값 갱신)
    today = dt.date.today()
    last_date = chosen.index[-1].date() if len(chosen) else None
    realtime = _fetch_stock_realtime_naver(code)
    if realtime and realtime.get("close"):
        if last_date != today:
            print(f"[info] {code} 오늘 봉 누락({last_date}) → 네이버 실시간 append: {realtime['close']}")
            new_row = pd.DataFrame([{
                "open": realtime.get("open", realtime["close"]),
                "high": realtime.get("high", realtime["close"]),
                "low": realtime.get("low", realtime["close"]),
                "close": realtime["close"],
                "volume": realtime.get("volume", 0),
            }], index=[pd.Timestamp(today)])
            chosen = pd.concat([chosen, new_row])
        else:
            # 오늘 봉이 있어도 값이 다르면(장중) 실시간으로 갱신
            if abs(float(chosen["close"].iloc[-1]) - realtime["close"]) > 1:
                for k in ["open", "high", "low", "close", "volume"]:
                    if k in realtime and realtime[k] is not None:
                        chosen.iat[-1, chosen.columns.get_loc(k)] = realtime[k]

    return chosen


def _fetch_stock_realtime_naver(code: str) -> dict | None:
    """네이버 실시간 개별 종목 시세.
    API: polling.finance.naver.com/api/realtime/domestic/stock/{code}"""
    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
    try:
        r = requests.get(url, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://finance.naver.com/"})
        r.raise_for_status()
        j = r.json()
        d = (j.get("datas") or [None])[0]
        if not d:
            return None

        def num(v):
            if v is None:
                return None
            s = str(v).replace(",", "")
            try:
                return float(s)
            except ValueError:
                return None

        close = num(d.get("closePrice") or d.get("currentPrice"))
        if close is None:
            return None
        return {
            "close": close,
            "open": num(d.get("openPrice")),
            "high": num(d.get("highPrice")),
            "low": num(d.get("lowPrice")),
            "volume": num(d.get("accumulatedTradingVolume")) or 0,
        }
    except Exception as e:
        print(f"[warn] 네이버 실시간 종목 실패 {code}: {e}")
        return None


def _drop_stale_tail(df: pd.DataFrame, code: str = "") -> pd.DataFrame:
    """끝단의 '진짜 미체결/미갱신' 행만 제거.
    - 종가가 NaN인 행 (데이터 소스가 자리만 만들어둔 경우)
    을 제거한다. 거래량 0만으로는 제거하지 않는다(정상 거래일에도 장 시작
    직후엔 거래량이 0이거나, 관리종목·거래정지 후 재개일 등 예외가 있어
    유효한 최신 봉을 잘못 날릴 수 있기 때문)."""
    if df is None or len(df) == 0:
        return df
    d = df.copy()
    # 종가 NaN 행만 제거
    d = d[d["close"].notna()]
    # 끝단에서 OHLC가 전부 결측이거나 0인 진짜 빈 행만 제거
    while len(d) > 1:
        last = d.iloc[-1]
        empty = (pd.isna(last[["open", "high", "low", "close"]]).all()
                 or (last[["open", "high", "low", "close"]] == 0).all())
        if empty:
            d = d.iloc[:-1]
        else:
            break
    return d


# ----------------------------------------------------------------------
# 월봉 계산 전용 소스 (기본 3년 = 36개월)
# ----------------------------------------------------------------------
def fetch_monthly_source(code: str, years: float = 5.0) -> pd.DataFrame:
    """월봉 리샘플용 일봉 소스. 기본 5년(=60개월)으로 월봉 MA60까지 형성 가능.
    (요청 반영: 3년→5년). 상장 이력이 짧은 종목은 확보 가능한 만큼만 반환되고
    MA60은 자동 생략됨. 데이터량이 늘어 타임아웃이 재발하면 MONTHLY_YEARS 를
    4 등으로 낮춰 조정.
    """
    try:
        df = fetch_daily_fdr(code, years)
        if len(df) > 0:
            return df
    except Exception as e:
        print(f"[warn] FDR(월봉소스) 실패 {code}: {e}")
    try:
        return fetch_daily_pykrx(code, years)
    except Exception as e:
        print(f"[error] pykrx(월봉소스)도 실패 {code}: {e}")
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def fetch_index_daily(symbol: str, years: float = 1.0) -> pd.DataFrame:
    """지수 일봉 수집. FDR + pykrx 둘 다 받아 최신인 쪽 선택.
    추가로 오늘 봉이 아직 없으면 네이버 실시간 시세로 오늘 봉을 append.
    (KS11=코스피, KQ11=코스닥)"""
    dfs = []
    # 1) FDR
    try:
        import FinanceDataReader as fdr
        end = dt.date.today()
        start = end - dt.timedelta(days=int(years * 365.25))
        df = fdr.DataReader(symbol, start, end)
        df = df.rename(columns=str.lower)
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep]
        if "volume" not in df.columns:
            df["volume"] = 0
        df.index = pd.to_datetime(df.index)
        dfs.append(("FDR", _drop_stale_tail(df.sort_index(), symbol)))
    except Exception as e:
        print(f"[warn] FDR 지수 실패 {symbol}: {e}")

    # 2) pykrx (지수: KOSPI=1001, KOSDAQ=2001)
    try:
        from pykrx import stock
        krx_code = {"KS11": "1001", "KQ11": "2001"}.get(symbol)
        if krx_code:
            end = dt.date.today().strftime("%Y%m%d")
            start = (dt.date.today() - dt.timedelta(days=int(years * 365.25))).strftime("%Y%m%d")
            df = stock.get_index_ohlcv_by_date(start, end, krx_code)
            df = df.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                                     "종가": "close", "거래량": "volume"})
            df = df[["open", "high", "low", "close", "volume"]]
            df.index = pd.to_datetime(df.index)
            dfs.append(("pykrx", _drop_stale_tail(df.sort_index(), symbol)))
    except Exception as e:
        print(f"[warn] pykrx 지수 실패 {symbol}: {e}")

    # 최신 데이터 선택 (마지막 날짜가 더 최근인 쪽)
    if not dfs:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    dfs.sort(key=lambda x: x[1].index[-1] if len(x[1]) else pd.Timestamp("1900-01-01"),
             reverse=True)
    chosen_src, chosen = dfs[0]

    # 3) 네이버 실시간 폴백: 마지막 날짜가 오늘이 아니면 오늘 시세 append
    today = dt.date.today()
    last_date = chosen.index[-1].date() if len(chosen) else None
    if last_date != today:
        realtime = _fetch_index_realtime_naver(symbol)
        if realtime:
            print(f"[info] {symbol} 오늘 봉 누락({last_date}) → 네이버 실시간으로 보정: {realtime['close']}")
            new_row = pd.DataFrame([{
                "open": realtime.get("open", realtime["close"]),
                "high": realtime.get("high", realtime["close"]),
                "low": realtime.get("low", realtime["close"]),
                "close": realtime["close"],
                "volume": realtime.get("volume", 0),
            }], index=[pd.Timestamp(today)])
            chosen = pd.concat([chosen, new_row])
    else:
        # 오늘 봉이 있어도 장중이면 아직 종가 확정 전 → 네이버로 최신값 갱신
        realtime = _fetch_index_realtime_naver(symbol)
        if realtime and abs(chosen["close"].iloc[-1] - realtime["close"]) > 1:
            print(f"[info] {symbol} 오늘 봉 값 갱신: {chosen['close'].iloc[-1]:.2f} → {realtime['close']:.2f}")
            for k in ["open", "high", "low", "close"]:
                if k in realtime:
                    chosen.iat[-1, chosen.columns.get_loc(k)] = realtime[k]

    return chosen


def _fetch_index_realtime_naver(symbol: str) -> dict | None:
    """네이버 실시간 지수 시세.
    API: polling.finance.naver.com/api/realtime/domestic/index/KOSPI (또는 KOSDAQ)"""
    market = {"KS11": "KOSPI", "KQ11": "KOSDAQ"}.get(symbol)
    if not market:
        return None
    url = f"https://polling.finance.naver.com/api/realtime/domestic/index/{market}"
    try:
        r = requests.get(url, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://finance.naver.com/"})
        r.raise_for_status()
        j = r.json()
        # 응답 스키마: {"datas":[{"closePrice":"6258.12","openPrice":...,"highPrice":...,"lowPrice":...}]}
        d = (j.get("datas") or [None])[0]
        if not d:
            return None

        def num(v):
            if v is None:
                return None
            s = str(v).replace(",", "")
            try:
                return float(s)
            except ValueError:
                return None

        close = num(d.get("closePrice") or d.get("currentPrice"))
        if close is None:
            return None
        return {
            "close": close,
            "open": num(d.get("openPrice")),
            "high": num(d.get("highPrice")),
            "low": num(d.get("lowPrice")),
            "volume": num(d.get("accumulatedTradingVolume")) or 0,
        }
    except Exception as e:
        print(f"[warn] 네이버 실시간 지수 실패 {symbol}: {e}")
        return None


# ----------------------------------------------------------------------
# 시장 통계 (KOSPI/KOSDAQ 상하한가·상승·하락 + 투자자별 매매)
# ----------------------------------------------------------------------
_MARKET_MAP = {"KS11": "KOSPI", "KQ11": "KOSDAQ"}


def fetch_market_breadth(symbol: str) -> dict:
    """당일 시장 폭 통계: 상승/하락/보합/상한/하한 종목 수.
    1) pykrx (마감 확정치) → 2) 네이버 크롤링 (장중 실시간) 폴백."""
    market = _MARKET_MAP.get(symbol)
    if not market:
        return {"ok": False, "err": "지수 코드 아님"}

    # 1) pykrx 시도
    try:
        from pykrx import stock
        today = dt.date.today().strftime("%Y%m%d")
        df = stock.get_market_price_change_by_ticker(today, today, market=market)
        if df is None or len(df) == 0:
            yday = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
            df = stock.get_market_price_change_by_ticker(yday, yday, market=market)
        if df is not None and len(df) > 0:
            chg = df["등락률"]
            up = int((chg > 0).sum()); down = int((chg < 0).sum()); flat = int((chg == 0).sum())
            upper = int((chg >= 29.5).sum()); lower = int((chg <= -29.5).sum())
            total = up + down + flat
            return {"ok": True, "market": market, "total": total,
                    "up": up, "down": down, "flat": flat,
                    "upper_limit": upper, "lower_limit": lower,
                    "up_ratio": round(up / total * 100, 1) if total else 0,
                    "source": "pykrx"}
    except Exception:
        pass

    # 2) 네이버 폴백 (장중)
    return _fetch_breadth_naver(market)


def _fetch_breadth_naver(market: str) -> dict:
    """네이버 지수 페이지에서 상승/하락/보합 종목 수 파싱.
    URL: finance.naver.com/sise/sise_index.naver?code=KOSPI|KOSDAQ"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False, "err": "beautifulsoup4 미설치"}

    url = f"https://finance.naver.com/sise/sise_index.naver?code={market}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, timeout=10, headers=headers)
        r.raise_for_status()
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        # 페이지에 "상승 XXX 종목" "하락 XXX 종목" 형식으로 표기됨
        # 여러 위치 시도
        import re
        text = soup.get_text()
        # 예: "상승", "하락", "보합", "상한", "하한" 뒤 숫자
        def grab(label, txt):
            m = re.search(rf"{label}\s*([0-9,]+)", txt)
            return int(m.group(1).replace(",", "")) if m else None

        up = grab("상승", text); down = grab("하락", text); flat = grab("보합", text)
        upper = grab("상한", text); lower = grab("하한", text)

        if up is None and down is None:
            return {"ok": False, "err": "네이버 페이지 파싱 실패"}

        up = up or 0; down = down or 0; flat = flat or 0
        total = up + down + flat
        return {"ok": True, "market": market, "total": total,
                "up": up, "down": down, "flat": flat,
                "upper_limit": upper or 0, "lower_limit": lower or 0,
                "up_ratio": round(up / total * 100, 1) if total else 0,
                "source": "naver_crawl"}
    except Exception as e:
        return {"ok": False, "err": f"네이버 크롤링 실패: {e}"}


def fetch_market_investor_flow(symbol: str) -> dict:
    """당일 시장 투자자별 매매(외인/기관/개인) 순매수 거래대금.
    1) pykrx로 시도 (장 마감 후 확정치)
    2) 실패 or 장중이면 네이버 크롤링으로 폴백 (장중 실시간)"""
    market = _MARKET_MAP.get(symbol)
    if not market:
        return {"ok": False, "err": "지수 코드 아님"}

    # 1) pykrx 시도 (마감 후 확정치)
    try:
        from pykrx import stock
        today = dt.date.today().strftime("%Y%m%d")
        df = stock.get_market_trading_value_by_investor(today, today, market=market)
        if df is None or len(df) == 0:
            yday = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
            df = stock.get_market_trading_value_by_investor(yday, yday, market=market)
        if df is not None and len(df) > 0:
            col = "순매수" if "순매수" in df.columns else df.columns[-1]

            def val(idx):
                try:
                    return int(df.loc[idx, col])
                except Exception:
                    return 0

            idx_map = {i: str(i) for i in df.index}
            def find(*keys):
                for i, name in idx_map.items():
                    if any(k in name for k in keys):
                        return val(i)
                return 0

            foreign = find("외국인")
            inst = find("기관합계", "기관")
            indiv = find("개인")
            if any([foreign, inst, indiv]):
                return {"ok": True, "market": market,
                        "foreign": foreign, "inst": inst, "indiv": indiv,
                        "source": "pykrx"}
    except Exception as e:
        pass  # 네이버 폴백으로

    # 2) 네이버 크롤링 폴백 (장중 실시간)
    return _fetch_investor_flow_naver(market)


def _fetch_investor_flow_naver(market: str) -> dict:
    """네이버 금융에서 장중 실시간 투자자별 매매 크롤링.
    URL: finance.naver.com/sise/investorDealTrendDay.naver?bizdate=YYYYMMDD&sosok=01|10

    Note: 네이버는 코스피(01)/코스닥(10) 구분. 여기서는 KOSPI/KOSDAQ 별로 조회."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False, "err": "beautifulsoup4 미설치"}

    sosok = "01" if market == "KOSPI" else "10"
    today = dt.date.today().strftime("%Y%m%d")
    url = (f"https://finance.naver.com/sise/investorDealTrendDay.naver"
           f"?bizdate={today}&sosok={sosok}")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

    try:
        r = requests.get(url, timeout=10, headers=headers)
        r.raise_for_status()
        r.encoding = "euc-kr"  # 네이버 금융 인코딩
        soup = BeautifulSoup(r.text, "html.parser")

        # 텍스트 앵커링: "외국인", "기관계", "개인" 라벨 찾기
        # 첫 행(당일)의 순매수 값을 추출
        target_labels = {"외국인": "foreign", "기관계": "inst",
                         "기관합계": "inst", "개인": "indiv"}
        result = {"foreign": 0, "inst": 0, "indiv": 0}

        # 표 순회하며 헤더 매칭
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            # 헤더에서 컬럼 위치 찾기
            headers_txt = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
            col_idx = {}
            for i, h in enumerate(headers_txt):
                for label, key in target_labels.items():
                    if label in h:
                        col_idx[key] = i
                        break
            if not col_idx:
                continue
            # 첫 데이터 행(당일 값) 파싱
            for row in rows[1:2]:  # 첫 행만
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                for key, i in col_idx.items():
                    if i < len(cells):
                        # 예: "1,234", "-567", "1,234억"
                        txt = cells[i].replace(",", "").replace("억", "")
                        try:
                            # 네이버 표기는 백만원 단위 or 원 단위 - 실제 페이지 확인 필요
                            # 일단 억원 단위로 가정하고 억→원 변환
                            v = int(float(txt))
                            # 100만 원 단위로 오는 경우가 많음 → 원 단위로 변환
                            result[key] = v * 1_000_000
                        except (ValueError, TypeError):
                            pass
            break  # 첫 매칭 테이블만 사용

        if any(result.values()):
            return {"ok": True, "market": market, "source": "naver_crawl", **result}
        return {"ok": False, "err": "네이버에서 값 파싱 실패"}
    except Exception as e:
        return {"ok": False, "err": f"네이버 크롤링 실패: {e}"}


# ----------------------------------------------------------------------
# 1시간봉 (Naver 분봉 -> resample)
# ----------------------------------------------------------------------
def fetch_minute_naver(code: str, count: int = 500) -> pd.DataFrame:
    """
    네이버 분봉 수집. 구형 sise.nhn(XML)은 폐기되어, 현재 유효한
    분봉 엔드포인트를 순차 시도한다.

    반환: 1분봉 OHLCV DataFrame (columns: open/high/low/close/volume, DatetimeIndex).
    실패 시 빈 DataFrame.
    """
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

    # 시도 1: fchart 분봉 (신 도메인, XML/텍스트 응답)
    for base in ("https://api.finance.naver.com/siseJson.naver",
                 "https://fchart.stock.naver.com/sise.nhn"):
        try:
            if "siseJson" in base:
                # siseJson은 분봉 미지원 → skip (일/주/월만)
                continue
            url = f"{base}?symbol={code}&timeframe=minute&count={count}&requestType=0"
            r = requests.get(url, timeout=10, headers=headers)
            r.raise_for_status()
            df = _parse_naver_minute_xml(r.text)
            if len(df) > 0:
                return df
        except Exception:
            continue

    # 시도 2: 네이버 모바일 분봉 폴링 API (JSON)
    try:
        # 최근 N개 분봉 (minuteCandle)
        url = (f"https://api.stock.naver.com/chart/domestic/item/{code}/minute"
               f"?minuteUnit=1&count={count}")
        r = requests.get(url, timeout=10, headers=headers)
        r.raise_for_status()
        data = r.json()
        rows = []
        for it in (data if isinstance(data, list) else data.get("result", [])):
            # 키 이름은 응답 스키마에 따라 방어적으로 처리
            ts = it.get("localDateTime") or it.get("dateTime") or it.get("time")
            o = it.get("openPrice") or it.get("open")
            h = it.get("highPrice") or it.get("high")
            l = it.get("lowPrice") or it.get("low")
            c = it.get("closePrice") or it.get("close")
            v = it.get("accumulatedTradingVolume") or it.get("volume") or 0
            if ts is None or c is None:
                continue
            try:
                rows.append((pd.to_datetime(ts), float(o), float(h),
                             float(l), float(c), float(v)))
            except (ValueError, TypeError):
                continue
        if rows:
            df = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"]).set_index("dt")
            return df.sort_index()
    except Exception:
        pass

    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def _parse_naver_minute_xml(text: str) -> pd.DataFrame:
    """구형 fchart XML(<item data="날짜|시가|고가|저가|종가|거래량">) 파싱."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if 'data="' not in line:
            continue
        payload = line.split('data="', 1)[1].split('"', 1)[0]
        parts = payload.split("|")
        if len(parts) < 6:
            continue
        ts, o, h, l, c, v = parts[:6]
        try:
            rows.append((pd.to_datetime(ts, format="%Y%m%d%H%M"),
                         float(o), float(h), float(l), float(c), float(v)))
        except ValueError:
            continue
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"]).set_index("dt")
    return df.sort_index()


def resample_60min(df_min: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df_min.resample("60min").agg(agg).dropna(how="any")


def fetch_hourly(code: str, count: int = 2000) -> pd.DataFrame:
    """1시간봉. 실패 시 빈 DF."""
    try:
        m = fetch_minute_naver(code, count)
        if len(m) == 0:
            return m
        return resample_60min(m)
    except Exception as e:
        print(f"[warn] 1시간봉 수집 실패 {code}: {e}")
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


# ----------------------------------------------------------------------
# 한국 거래일 여부
# ----------------------------------------------------------------------
def is_kr_trading_day(date: dt.date | None = None) -> bool:
    date = date or dt.date.today()
    if date.weekday() >= 5:      # 토/일
        return False
    try:
        from pykrx import stock
        days = stock.get_previous_business_days(year=date.year, month=date.month)
        return pd.Timestamp(date) in set(pd.to_datetime(days))
    except Exception:
        return True              # pykrx 실패 시 평일이면 개장으로 간주


# ----------------------------------------------------------------------
# 더미 데이터 (오프라인 테스트용)
# ----------------------------------------------------------------------
def dummy_daily(seed: int = 1, n: int = 245) -> pd.DataFrame:
    rng = pd.date_range(dt.date.today() - dt.timedelta(days=int(n * 1.4)), periods=n, freq="B")
    r = np.random.default_rng(seed)
    close = 20000 + np.cumsum(r.normal(20, 250, n))
    close = np.clip(close, 5000, None)
    return pd.DataFrame({
        "open": close - r.uniform(-100, 100, n),
        "high": close + r.uniform(0, 200, n),
        "low": close - r.uniform(0, 200, n),
        "close": close,
        "volume": r.integers(1e5, 2e6, n),
    }, index=rng)


def dummy_monthly_source(seed: int = 1, n: int = 1225) -> pd.DataFrame:
    """5년치(약 1225 거래일) 더미 - 월봉 리샘플 테스트용(MA60 형성 가능)."""
    return dummy_daily(seed=seed + 500, n=n)


def dummy_hourly(seed: int = 1, n: int = 400) -> pd.DataFrame:
    rng = pd.date_range(dt.datetime.now() - dt.timedelta(hours=n), periods=n, freq="60min")
    r = np.random.default_rng(seed + 99)
    close = 23000 + np.cumsum(r.normal(0, 60, n))
    return pd.DataFrame({
        "open": close, "high": close + r.uniform(0, 40, n),
        "low": close - r.uniform(0, 40, n), "close": close,
        "volume": r.integers(1e3, 5e4, n),
    }, index=rng)
