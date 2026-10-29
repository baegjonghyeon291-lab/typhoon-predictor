# typhoon-predictor

사이트: https://baegjonghyeon291-lab.github.io/typhoon-predictor/

머신러닝 기반 태풍 예측 프로그램. 1951년 이후 서태평양 태풍 경로 전체를 학습해 지금 태풍이 한국(또는 일본)에 언제 올지 예측하고, 한국이 일본보다 태풍 예측이 늦는 이유를 데이터로 분석한다.

## 데이터

[IBTrACS v04r01](https://www.ncei.noaa.gov/products/international-best-track-archive) 서태평양(WP) 자료 (미국 NOAA NCEI). 일본 기상청(JMA) 기록은 1951년부터, 한국 기상청(KMA) 기록은 2015년부터 포함돼 있다. 처음 실행할 때 `data/raw/`에 자동으로 내려받는다.

### 환경 자료 (키 없이 공개)

| 자료 | 출처 | 쓰는 값 |
|---|---|---|
| NCEP/NCAR Reanalysis 1 일평균 | NOAA PSL (OPeNDAP로 0–60N, 100–180E만) | 850·500·200hPa 바람(지향류, 연직 시어), 500hPa 고도(북태평양 고기압 위치·세기) |
| ERSST v5 월평균 해수면온도 | NOAA PSL | (모델에는 안 씀) 태풍 위치·북쪽 5° 해수온, 1991–2020 대비 편차 |
| ONI, PDO, Niño4 | NOAA CPC, NCEI, PSL | 엘니뇨·태평양 10년 진동 상태 |

실시간 예측과 같은 조건이 되도록 월 단위 자료는 그 시점에 이미 발표된 값만 쓴다(해수온 편차·PDO·Niño4는 지난달, ONI는 두 달 전 중심값).

### 키가 필요한 자료 (연결 코드만 준비, `.env.example` 참고)

- ERA5 재분석 (ECMWF CDS) — NCEP보다 정밀한 상공 자료
- 기상청 태풍 예보 발표 이력, 종관기상관측(ASOS) 기온·강수·서리

영향권 기준: 한국은 서울·부산·광주·제주·강릉·대전, 일본은 후쿠오카·가고시마·오사카·도쿄·센다이·삿포로·나하·고치·니가타 중 한 곳에서 200km 안에 태풍 중심이 들어온 경우.

## 모델

- 입력: 6시간 단위 태풍 상태(위치, 풍속, 기압, 6·24시간 이동량, 속도, 진행 방향, 발생 후 경과 시간, 날짜, 서울·도쿄까지 거리와 방위)
- 출력 1: 120시간 안에 영향권에 들어올 확률 (HistGradientBoostingClassifier)
- 출력 2: 들어온다면 남은 시간 (HistGradientBoostingRegressor)
- 모델 두 가지: 경로 특성만 쓰는 모델(사이트의 직접 예측용)과 대기·해양 환경 특성까지 쓰는 환경 모델(실제 태풍 예측용)
- 학습 1951–2014, 검증 2015–현재 (태풍 단위로 기간을 나눠 정보 누출 없음)

| | AUC | 도착 시간 오차(평균) | 검증 태풍 수(영향) |
|---|---|---|---|
| 한국 | 0.907 | 14.6시간 | 28 |
| 일본 | 0.901 | 15.9시간 | 90 |
| 한국 (환경 모델) | 0.911 | 14.8시간 | 28 |
| 일본 (환경 모델) | 0.907 | 16.0시간 | 90 |

환경 모델은 재분석 자료가 있는 시점에만 쓰고, 없는 시점(재분석 서버가 아직 갱신 안 된 최근 등)은 경로 모델로 대체한 실제 서비스 방식 기준이다. 검증 기간 중 환경 자료가 있는 시점은 약 93%. 해수온·기후 지수를 넣은 조합은 오히려 성능이 떨어져 뺐다(`reports/env_experiment.txt`).

| 모델 첫 경보, 도착 전(중앙값) | 경로 모델 | 환경 모델 |
|---|---|---|
| 한국 | 24시간 | 33시간 |
| 일본 | 60시간 | 48시간 |

## 한국 예측이 늦는 이유 (분석 결과)

`reports/analysis.json`

| 항목 | 한국 | 일본 |
|---|---|---|
| 발생부터 영향권 도착까지 걸린 시간(중앙값) | 198시간 | 156시간 |
| 모델이 처음 경보를 낸 시점(도착 전, 중앙값) | 24시간 | 60시간 |
| 도착 72시간 전에 경보한 비율 | 14% | 33% |
| 도착 전 48시간 동안 진행 방향 변화(중앙값) | 47.2° | 39.9° |

- 한국은 태풍이 오기까지 시간이 더 걸리는데도 경보는 더 늦다. 시간이 부족해서가 아니라 경로가 끝까지 불확실하기 때문이다.
- 한국에 오는 태풍은 도착 직전 48시간 동안 방향이 더 크게 꺾인다(전향). 일본 남쪽에서 북쪽·북동쪽으로 꺾이는 지점이 한국 쪽으로 오느냐 일본 쪽으로 가느냐를 가른다.
- 두 모델 모두 날짜(계절)와 경도가 가장 중요한 특성이다. 같은 위치라도 계절에 따라 북태평양 고기압 위치가 달라 진로가 바뀌는 것과 맞는다.
- 2015년 이후 같은 태풍 278개를 비교하면 KMA 기록 시작이 JMA보다 중앙값 12시간(평균 18.1시간) 늦고, 81%가 늦게 시작된다.

## 실행

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m typhoon.env_ocean  # 해수온·기후 지수(실험용, 모델에는 안 씀) → data/env/ocean_features.parquet
.venv/bin/python -m typhoon.env_atmos  # 재분석 대기 특성 → data/env/atmos_features.parquet
.venv/bin/python -m typhoon.train      # 학습 → models/, reports/metrics.json
.venv/bin/python -m typhoon.analyze    # 분석 → reports/analysis.json
.venv/bin/python -m typhoon.predict --update   # 최신 데이터 받아 가장 최근 태풍 예측
.venv/bin/python -m typhoon.predict --sid 2026288N10161
.venv/bin/python -m typhoon.export --update     # 사이트 데이터 갱신 → docs/data/
```

## 웹사이트

`docs/` 폴더가 GitHub Pages로 배포된다. 학습한 트리 모델을 `docs/data/model.json`으로 내보내 브라우저에서 직접 계산하므로(파이썬 결과와 동일) 지도에서 위치를 찍고 조건을 바꾸면 바로 예측된다.
