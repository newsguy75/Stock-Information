# 포트폴리오 브리핑 시스템 (카톡 요약 + 이메일 상세 리포트)

베트남 시간 기준 하루 8회, 보유 종목을 분석해
**카카오톡(요약)** + **이메일(HTML 상세 리포트)** + **GitHub `data/` 폴더(JSON/HTML 이력)**
로 전달하는 GitHub Actions 자동화.

## 파일 구성
| 파일 | 역할 |
|---|---|
| `portfolio_briefing.py` | 메인. 수집→분석→저장→카톡/이메일 전송 |
| `analysis_engine.py` | 핵심 분석. 다이버전스·스토캐·이평·수급·공매도·눌림목·하락경고·종합판정 |
| `report_writer.py` | data/ 폴더에 JSON+HTML 리포트 생성 (차트·채점표·하락배너) |
| `chart_maker.py` | 1H/일/월 3프레임 차트 (캔들+MA5·20+거래량+스토캐) matplotlib PNG |
| `market_index.py` | KOSPI/KOSDAQ 시황 요약 |
| `emailer.py` | Gmail SMTP로 HTML 리포트 첨부 발송 |
| `data_feed.py` | 일봉(FDR/pykrx) + 월봉소스(5년) + 1시간봉(네이버 분봉) |
| `signals.py` | MA/거래량/크로스 시그널 |
| `mtf_stoch_scanner.py` | 스토캐스틱·다이버전스·멀티프레임 |
| `holdings.json` | 보유 종목 (20종목) |
| `requirements.txt` | 의존성 (matplotlib 포함) |
| `.github/workflows/briefing.yml` | 스케줄러 |

## 리포트 구성 (요청 항목 전체)
1. **차트** — 1H/일/월 3프레임 (캔들+MA5·20+거래량+스토캐)
2. **스토캐 다이버전스** — 근거+시점, 일봉/월봉/1H
   - **🚨 하락 경고 배너(최상단)**: 일봉 하락다이버전스·쌍봉(더블탑)·데드캣바운스 강조
3. **스토캐 프레임별 방향성** — 1H 장/중/단, 일봉 장/중/단, 월봉 장/중/단
4. **이평선** — 5·20·60일선 + 5·20 방향예측(1~3일) + 크로스 근접
4-B. **눌림목 매수** — 5/10/20일선 지지터치 + 손절가 + 신뢰도/기술적 의견
5. **수급** — 외인·기관·개인 5·20·60일 + 매매비중%
6. **공매도** — 5·20·60일 구간별 추세
7·8. **일봉 종합** — 채점표(항목별 점수+사유+합계)
9. **월봉 종합** — 확보 데이터 범위 내 판단 + 채점표

## 하락 신호 강조 (핵심)
일봉 기준 하락 신호는 **최우선 강조**:
- **하락다이버전스**: 가격 신고가 but 스토캐 하락 → 리포트 최상단 빨간 배너
- **쌍봉(더블탑)**: 유사 고점 2개 + 넥라인 → 이탈 시 "하락 확정" 표기
- **데드캣바운스**: 급락(-15%↑) 후 약한 반등 + 20일선 하회
- 강한 하락신호(넥라인 이탈 쌍봉/하락다이버전스) → 종합판정 강제 하향("⚠️ 하락신호 우세")

## 로컬 테스트
```bash
pip install -r requirements.txt
python portfolio_briefing.py --demo      # 더미 데이터, 전송 없음
python portfolio_briefing.py --dry-run   # 실데이터 조회, 저장만(전송 X)
```

## GitHub Secrets (Settings → Secrets and variables → Actions)
| Secret | 용도 |
|---|---|
| `KAKAO_REST_KEY`, `KAKAO_REFRESH_TOKEN` | 카카오톡 전송 |
| `KRX_ID`, `KRX_PW` | KRX 회원제 로그인(수급·공매도) |
| `GMAIL_USER` | 보내는 Gmail 주소 |
| `GMAIL_APP_PW` | Gmail **앱 비밀번호**(2단계 인증 후 발급, 일반 비번 아님) |
| `MAIL_TO` | 받는 주소(미설정 시 자기 자신) |

## Gmail 앱 비밀번호 발급
1. Google 계정 → 보안 → 2단계 인증 켜기
2. 2단계 인증 → 앱 비밀번호 → 새로 생성 → 16자리 발급
3. 그 16자리를 `GMAIL_APP_PW` Secret에 등록 (일반 비번은 SMTP 로그인 안 됨)

## 반드시 확인
- **종목코드**: 우진·SK증권우·범한퓨얼셀·코스모신소재는 코드 확신도 낮음.
  첫 `--dry-run`에서 종목명·현재가 대조 필수.
- **1시간봉**: 네이버 분봉 수집이 살아야 나옴(KRX/pykrx는 분봉 미지원).
  실패 시 1H 관련 항목만 "데이터 없음"으로 폴백, 나머지는 정상.
- **matplotlib**: requirements에 포함됨. 없으면 차트만 "데이터 없음" 폴백.
- **카카오 리프레시 토큰**: 약 2개월마다 만료 → 재발급 후 Secret 갱신.
