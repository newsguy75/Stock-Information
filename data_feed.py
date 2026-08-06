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
    """일봉(단기, 기본 1년): FDR 우선, 실패 시 pykrx."""
    try:
        df = fetch_daily_fdr(code, years)
        if len(df) > 0:
            return df
    except Exception as e:
        print(f"[warn] FDR 실패 {code}: {e}")
    try:
        return fetch_daily_pykrx(code, years)
    except Exception as e:
        print(f"[error] pykrx도 실패 {code}: {e}")
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


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


# ----------------------------------------------------------------------
# 1시간봉 (Naver 분봉 -> resample)
# ----------------------------------------------------------------------
def fetch_minute_naver(code: str, count: int = 500) -> pd.DataFrame:
    """
    Naver fchart 분봉 API. count = 최근 몇 개의 1분봉.
    1시간봉 60봉을 만들려면 국내장 하루 ~390분 → 며칠치 필요.
    반환: 1분봉 OHLCV DataFrame.

    엔드포인트 응답은 XML(<item data="날짜|시가|고가|저가|종가|거래량">) 형식.
    """
    url = ("https://fchart.stock.naver.com/sise.nhn"
           f"?symbol={code}&timeframe=minute&count={count}&requestType=0")
    r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    rows = []
    for line in r.text.splitlines():
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
