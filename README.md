# 멀티프레임 포트폴리오 브리핑 (일/주/월/1시간봉)

베트남 시간 기준 하루 8회(05·07·09·11·13·15·17·19시) 카카오톡 '나와의 채팅방'으로
포트폴리오 시그널을 전송하는 GitHub Actions 자동화.

## 파일 구성
| 파일 | 역할 |
|---|---|
| `portfolio_briefing.py` | 메인. 데이터 수집→시그널→리포트→카카오 전송 |
| `signals.py` | MA(5/20/60) 방향·정배열·골든/데드 크로스·5일선 터치·거래량 5봉선 |
| `mtf_stoch_scanner.py` | 스토캐스틱 멀티프레임 동조화 + 다이버전스 |
| `data_feed.py` | 일봉(FDR/pykrx) + 1시간봉(Naver 분봉 resample) 수집 |
| `holdings.json` | 보유 종목 목록 (실제 종목으로 교체) |
| `requirements.txt` | 의존성 |
| `.github/workflows/briefing.yml` | 스케줄러 (UTC cron) |

## 브리핑에 포함되는 시그널
- **일/주/월봉**: MA5 방향(상향/보합/하향), 5/20/60 정배열·역배열, 5·20 / 20·60 골든·데드크로스, 5일선 위에서의 5일선 지지터치(눌림)
- **일봉·1시간봉 거래량**: 거래량 5봉선(5일선) 돌파 시 🚨 알람
- **1시간봉**: 스토캐스틱 상승 전환(구간 표시) + 상승/하락 다이버전스
- 상단에 '즉시 알람' 섹션으로 거래량 돌파·크로스·다이버전스·5일선 터치를 모아서 표기

## 로컬 테스트
```bash
pip install -r requirements.txt
python portfolio_briefing.py --demo      # 더미 데이터, 전송 없음
python portfolio_briefing.py --dry-run   # 실데이터 조회, 콘솔 출력(전송 X)
```

## GitHub 설정
1. 위 파일들을 레포에 커밋 (`briefing.yml` 은 `.github/workflows/` 아래).
2. **Settings → Secrets and variables → Actions** 에 등록:
   - `KAKAO_REST_KEY` (앱 REST API 키)
   - `KAKAO_REFRESH_TOKEN`
   - `KAKAO_ACCESS_TOKEN` (선택. 없으면 리프레시로 갱신)
3. Actions 탭에서 `workflow_dispatch` 로 수동 1회 실행해 전송 확인.

## 반드시 확인할 것
- **종목코드**: `holdings.json` 의 코드는 예시입니다. 특히 `전진건설로봇` 은 코드
  미확정 상태라 빈 값(`""`)으로 두었고, 빈 코드는 자동 skip 됩니다. 정확한 6자리
  코드로 채워 넣으세요. (잘못된 코드는 엉뚱한 종목을 조회하므로 주의)
- **1시간봉 데이터**: 국내 분봉은 FDR/pykrx가 지원하지 않아 Naver 분봉 API를
  resample 합니다. 기존에 쓰시던 검증된 분봉 수집 함수가 있으면
  `data_feed.fetch_hourly` 를 그걸로 교체하는 걸 권장합니다.
- **주봉 MA60 / 월봉 MA60**: 각각 60주(~1.2년) / 60개월(5년) 데이터가 필요해
  일봉을 6년치 받습니다(`fetch_daily(years=6)`). 상장 이력이 짧은 종목은
  월봉 MA60이 안 잡혀 해당 라인이 생략됩니다.
- **카카오 리프레시 토큰**: 약 2개월마다 만료됩니다. 만료 시 재발급 후 Secret 갱신.

## 파라미터 튜닝 (`signals.py` 의 `SignalConfig`)
- `flat_threshold_pct` (기본 0.3): MA5 방향 '보합' 판정 폭. 고변동 테마주는 0.5~0.8로.
- `touch_tol_pct` (기본 0.8): 5일선 '터치' 인정 오차. 크게 하면 터치 알람이 자주 뜸.
- `cross_lookback` (기본 3): 최근 몇 봉 내 크로스를 '발생'으로 볼지.
- `vol_ma_period` (기본 5): 거래량 이동평균 기간.

## 스케줄 참고
| 베트남(ICT) | 한국(KST) | UTC(cron) |
|---|---|---|
| 05 07 09 11 13 15 17 19 | 07 09 11 13 15 17 19 21 | 22 00 02 04 06 08 10 12 |

한국장(09:00~15:30 KST) = 베트남 07:00~13:30 이라, 07·09·11·13시 브리핑이 장중을 커버합니다.
