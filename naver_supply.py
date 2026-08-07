# -*- coding: utf-8 -*-
"""
naver_supply.py
===============
네이버 금융 종목별 외인/기관 매매동향 크롤링.
URL: https://finance.naver.com/item/frgn.naver?code={code}&page={page}

이 페이지는 KRX 확정치를 그대로 표시하므로 pykrx보다 신뢰성 높음.
당일 데이터는 장중엔 부정확하므로, 전일(D-1)까지의 확정치를 기준으로 5/20/60일 합산.
"""
from __future__ import annotations
import re
import datetime as dt
import requests
import pandas as pd


def fetch_naver_supply(code: str, need_days: int = 65) -> pd.DataFrame:
    """네이버 종목 외인/기관 매매동향 크롤링.

    반환: DataFrame(index=날짜, columns=['close','change','volume','institutional','foreign','foreign_ratio'])
      - institutional: 기관 순매매 (주식 수)
      - foreign: 외국인 순매매 (주식 수)
      - foreign_ratio: 외국인 보유비율(%)

    페이지당 약 10일치, need_days만큼 확보되면 중단.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return pd.DataFrame()

    headers = {"User-Agent": "Mozilla/5.0",
               "Referer": "https://finance.naver.com/"}

    rows = []
    for page in range(1, 15):  # 최대 15페이지(~150일)
        url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
        try:
            r = requests.get(url, timeout=8, headers=headers)
            r.raise_for_status()
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print(f"[warn] 네이버 수급 페이지 {page} 실패 {code}: {e}")
            break

        # class="type2" 테이블 안 tr 순회
        table = soup.find("table", class_="type2")
        if not table:
            break

        page_rows = 0
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 7:
                continue
            # 첫 컬럼이 날짜(YYYY.MM.DD)여야 함
            date_txt = tds[0].get_text(strip=True)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_txt):
                continue

            def num(td, allow_neg=True):
                txt = td.get_text(strip=True).replace(",", "").replace("+", "")
                # class에 rate_up/rate_down 있으면 부호 결정
                cls = " ".join(td.get("class", []))
                if "rate_down" in cls or txt.startswith("-"):
                    sign = -1
                    txt = txt.lstrip("-")
                else:
                    sign = 1
                if not txt or txt == "-":
                    return 0
                try:
                    return sign * int(txt) if allow_neg else int(txt)
                except ValueError:
                    return 0

            def num_float(td):
                txt = td.get_text(strip=True).replace(",", "").replace("%", "")
                try:
                    return float(txt)
                except ValueError:
                    return 0.0

            try:
                date_ = dt.datetime.strptime(date_txt, "%Y.%m.%d").date()
                close = num(tds[1], allow_neg=False)
                change = num(tds[2])
                # tds[3]은 등락률, tds[4]는 거래량
                volume = num(tds[4], allow_neg=False)
                # 컬럼 순서: tds[5]=기관 순매매, tds[6]=외국인 순매매, tds[7]=보유주수, tds[8]=보유율
                institutional = num(tds[5])
                foreign = num(tds[6])
                # 외국인 보유율(있으면)
                foreign_ratio = num_float(tds[8]) if len(tds) > 8 else 0.0

                rows.append({
                    "date": date_,
                    "close": close,
                    "change": change,
                    "volume": volume,
                    "institutional": institutional,
                    "foreign": foreign,
                    "foreign_ratio": foreign_ratio,
                })
                page_rows += 1
            except (ValueError, IndexError):
                continue

        if page_rows == 0:
            break

        if len(rows) >= need_days:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).drop_duplicates("date").set_index("date").sort_index()
    return df


def summarize_supply(df: pd.DataFrame, ref_close: float = None) -> dict:
    """5/20/60거래일 순매매 합계를 계산.
    - df: fetch_naver_supply 결과 (D-1까지 확정치)
    - ref_close: 금액 변환에 쓸 종가 (없으면 df의 최신 close 사용)

    반환: 리포트 표시용 dict (외인/기관 주 단위 + 원 단위 억원 환산)
    """
    if df is None or len(df) == 0:
        return {"ok": False, "err": "데이터 없음"}

    # 오늘 날짜 제외 (D-1까지만)
    today = dt.date.today()
    df = df[df.index < today]
    if len(df) == 0:
        return {"ok": False, "err": "D-1 이전 데이터 없음"}

    price = ref_close or float(df["close"].iloc[-1])

    def sum_last(n, col):
        s = df.tail(n)[col].sum()
        return int(s)

    # 주식 수 합계
    f5, f20, f60 = sum_last(5, "foreign"), sum_last(20, "foreign"), sum_last(60, "foreign")
    i5, i20, i60 = sum_last(5, "institutional"), sum_last(20, "institutional"), sum_last(60, "institutional")

    # 개인은 별도 컬럼이 없음 → 거래량 - |외인| - |기관| 은 부정확하므로 표시 안 함
    # (네이버 종목 페이지엔 개인 매매 컬럼이 없음)

    # 원 단위 (주가 × 주 수) 대략 환산
    def to_won(shares):
        return int(shares * price)

    # 실제 확보한 거래일 수
    n5, n20, n60 = min(5, len(df)), min(20, len(df)), min(60, len(df))

    return {
        "ok": True,
        "n_days": {"d5": n5, "d20": n20, "d60": n60},
        "price": int(price),
        "last_date": str(df.index[-1]),
        "foreign_shares": {"d5": f5, "d20": f20, "d60": f60},
        "inst_shares": {"d5": i5, "d20": i20, "d60": i60},
        "foreign_won": {"d5": to_won(f5), "d20": to_won(f20), "d60": to_won(f60)},
        "inst_won": {"d5": to_won(i5), "d20": to_won(i20), "d60": to_won(i60)},
    }


if __name__ == "__main__":
    # 테스트: 삼성SDI (006400)
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "006400"
    df = fetch_naver_supply(code)
    print(df.head(10))
    print()
    s = summarize_supply(df)
    print(f"결과: {s}")
    if s.get("ok"):
        fw5 = s["foreign_won"]["d5"] / 1e8
        iw5 = s["inst_won"]["d5"] / 1e8
        print(f"외인 5일: +{fw5:.0f}억" if fw5 > 0 else f"외인 5일: {fw5:.0f}억")
        print(f"기관 5일: +{iw5:.0f}억" if iw5 > 0 else f"기관 5일: {iw5:.0f}억")
