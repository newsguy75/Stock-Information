# 데일리 아침 브리핑 (KST 07:00 → 카카오)

매 거래일 아침 7시(한국시간), 보유 포트폴리오의 **지수·평가손익·트리거 종목·수급·뉴스**를
카카오 '나와의 채팅방'으로 자동 발송합니다. 전체 표는 HTML 리포트로 저장.

- 시세/지수/ETF: **FinanceDataReader** (네이버 기반)
- 외국인/기관 수급: **네이버 금융** — 단기(2일vs3일 반전) / 중기(20일) / 장기(120일)
- 뉴스: 네이버 검색 API (선택)
- 스케줄: **GitHub Actions cron** (무료, PC 안 켜도 됨)
- 잔고: `holdings.json` 수동 갱신

## 트리거 4종
| 트리거 | 조건 |
|---|---|
| 등락 | 전일 대비 ±5% 이상 |
| 거래량 | 20일 평균 대비 3배 이상 |
| 수급 반전 | 최근 2일 vs 직전 3일 순매매 부호 전환 |
| 52주 | 고가/저가 3% 이내 근접 |

수급은 반전이 없어도 **항상** 단/중/장 3단계로 표시됩니다.
표기: `수급 외인 전환매수/매도/매수 · 기관 무/매수/매수` (순서: 단기·중기·장기)

---

## 로컬 실행 (내 PC)
```
python -m pip install -r requirements.txt
python morning_brief.py
```
카카오 키가 없으면 콘솔 출력 + `output/*.html`만 생성됩니다(테스트 모드).

## GitHub 자동화 3단계
1. 이 폴더를 **비공개(private) 저장소**로 업로드 (잔고 포함이므로 반드시 private)
2. `.github/workflows/morning-brief.yml` 이 포함됐는지 확인
3. Settings → Secrets and variables → Actions 에 등록:
```
KAKAO_REST_KEY        (필수)
KAKAO_REFRESH_TOKEN   (필수)
NAVER_CLIENT_ID       (선택 - 없으면 뉴스 생략)
NAVER_CLIENT_SECRET   (선택)
```
→ Actions 탭 → morning-brief → **Run workflow** 로 즉시 테스트

## 매일 저녁 할 일 (30초)
`holdings.json`의 `qty`(수량)·`avg`(평단) 수정 후 저장/커밋.
- 현재 값은 스크린샷에서 **역산한 추정치** → 한 번은 실측으로 교정 필요
- **전진건설로봇 `code`가 비어 있음** → 6자리 코드 입력 필요(비면 자동 스킵)

## 설정 조정
`morning_brief.py` 상단 상수:
```
TH_CHANGE_PCT  = 5.0    # 등락 기준
TH_VOL_MULT    = 3.0    # 거래량 배수
TH_NEAR_HL_PCT = 3.0    # 52주 근접 %
TH_FLOW_DAYS   = (2,3)  # 단기 수급 반전
FLOW_MID_DAYS  = 20     # 중기
FLOW_LONG_DAYS = 120    # 장기
```

## 참고 / 주의
- cron `0 22 * * 0-4`(UTC) = KST 월~금 07:00. GitHub 혼잡 시 5~15분 지연 가능.
- 카카오 access_token 6시간 / **refresh_token 약 2개월** → 만료 시 `get_token.py` 재실행 후 Secret 갱신.
- 카카오 텍스트 템플릿 200자 제한 → 트리거 종목만 상세 발송, 전체 표는 HTML.
- ETF는 외국인/기관 수급 데이터가 없어 수급 칸이 비어 있음(나머지 트리거는 정상).
- 저장소가 60일간 활동이 없으면 GitHub이 스케줄을 자동 비활성화할 수 있음.

## 다음 확장 예정
2차: 수급 TOP20 · 업종 등락 · 글로벌 지표(미국/중국/일본, 유가·금리·환율)
3차: Claude API 분석층 (유망섹터·종목 선정, 뉴스 모멘텀/리스크, 전략의견) + 낮 12시 장중 브리핑
