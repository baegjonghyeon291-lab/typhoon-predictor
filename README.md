# typhoon-predictor

> 참고용 머신러닝 예측이며 공식 예보가 아닙니다. 대비는 [기상청 태풍 정보](https://www.weather.go.kr/w/typhoon/report.do)로 판단하세요.

사이트: https://baegjonghyeon291-lab.github.io/typhoon-predictor/

직접 학습한 머신러닝 자체 모델로 태풍을 예측하고, 이를 기준으로 한·미·일 기관 예보와 비교해 왜 다른지 결과로 배우는 프로젝트. 1951년 이후 서태평양 태풍 경로 전체를 학습해 지금 태풍이 한국(또는 일본)에 언제 올지 예측한다.

## 데이터

[IBTrACS v04r01](https://www.ncei.noaa.gov/products/international-best-track-archive) 서태평양(WP) 자료 (미국 NOAA NCEI). 일본 기상청(JMA) 기록은 1951년부터, 한국 기상청(KMA) 기록은 2015년부터 포함돼 있다. 처음 실행할 때 `data/raw/`에 자동으로 내려받는다.

### 환경 자료 (키 없이 공개)

| 자료 | 출처 | 쓰는 값 |
|---|---|---|
| NCEP/NCAR Reanalysis 1 일평균 | NOAA PSL (OPeNDAP로 0–60N, 100–180E만) | 850·500·200hPa 바람(지향류, 연직 시어), 500hPa 고도(북태평양 고기압 위치·세기) |
| GFS 분석장(f000) 일평균 | NOAA, AWS 공개 버킷 `noaa-gfs-bdp-pds` (필요한 필드만 바이트 범위로) | NCEP와 같은 값. NCEP가 끝난 2026-03-17 이후 시점을 채운다 |
| ERSST v5 월평균 해수면온도 | NOAA PSL | (모델에는 안 씀) 태풍 위치·북쪽 5° 해수온, 1991–2020 대비 편차 |
| ONI, PDO, Niño4 | NOAA CPC, NCEI, PSL | 엘니뇨·태평양 10년 진동 상태 |

실시간 예측과 같은 조건이 되도록 월 단위 자료는 그 시점에 이미 발표된 값만 쓴다(해수온 편차·PDO·Niño4는 지난달, ONI는 두 달 전 중심값).

### 키가 필요한 자료 (연결 코드만 준비, `.env.example` 참고)

- ERA5 재분석 (ECMWF CDS) — NCEP보다 정밀한 상공 자료
- 기상청 태풍 예보 발표 이력, 종관기상관측(ASOS) 기온·강수·서리

영향권 기준: 한국은 서울·부산·광주·제주·강릉·대전, 일본은 후쿠오카·가고시마·오사카·도쿄·센다이·삿포로·나하·고치·니가타 중 한 곳에서 200km 안에 태풍 중심이 들어온 경우.

### 기관 예보

- 일본 기상청 5일 예보, 미국 JTWC 경보 전문: 키 없이 받는다. 지금 활동 중이거나 최근 7일 것만 있어 `data/forecast_log.jsonl`에 쌓는다.
- 기상청 예보 이력: `KMA_APIHUB_KEY` 필요.

## 모델

- 입력: 6시간 단위 태풍 상태(위치, 풍속, 기압, 6·24시간 이동량, 속도, 진행 방향, 발생 후 경과 시간, 날짜, 서울·도쿄까지 거리와 방위)
- 출력 1: 120시간 안에 영향권에 들어올 확률 (HistGradientBoostingClassifier)
- 출력 2: 들어온다면 남은 시간 (HistGradientBoostingRegressor)
- 모델 두 가지: 경로 특성만 쓰는 모델(환경 자료가 없을 때 대체)과 대기 환경 특성까지 쓰는 환경 모델(기본)
- 학습 1951–2014, 검증 2015–현재 (태풍 단위로 기간을 나눠 정보 누출 없음)

| | AUC | 도착 시간 오차(평균) | 검증 태풍 수(영향) |
|---|---|---|---|
| 한국 | 0.911 | 14.4시간 | 28 |
| 일본 | 0.906 | 15.6시간 | 91 |
| 한국 (환경 모델) | 0.912 | 14.5시간 | 28 |
| 일본 (환경 모델) | 0.909 | 16.1시간 | 91 |

도착 시간은 점 예측과 함께 범위를 낸다(목표 80%). 10%·90% 분위 회귀만으로는 검증에서 실제 도착이 66~69%만 들어와, 학습 기간 안 태풍 단위 교차 예측으로 맞춘 폭(약 3시간)을 양쪽에 더했다(conformalized quantile regression). 검증 포함 비율: 한국 74%(경로)·78%(환경), 일본 79%, 범위 폭 중앙값 한국 약 41시간, 일본 약 49시간.

확률은 Platt 보정을 거친다. 학습 자료에 맞춘 트리는 확률을 과신해서(검증에서 한국 '50~70%'가 실제 38%), 학습 기간(~2014) 안에서 태풍 단위 5겹 교차 예측 점수로 보정식을 맞췄다. 분류 트리는 학습을 느리게(학습률 0.02, 800회) 하고 나무를 작게(잎 15개, 잎당 최소 300개) 해서 덜 외우게 했다(`train.CLF_PARAMS`). 검증 Brier 점수(보정 전 기본 설정 → 지금): 한국 환경 모델 0.0357 → 0.0339, 일본 0.0853 → 0.0793 (`reports/metrics.json`).

환경 모델은 재분석 자료가 있는 시점에만 쓰고, 없는 시점(재분석 서버가 아직 갱신 안 된 최근 등)은 경로 모델로 대체한 실제 서비스 방식 기준이다. 검증 기간 중 환경 자료가 있는 시점은 약 93%. NCEP 재분석이 2026-03-17에서 멈춰서, 사이트 갱신 때는 그 뒤 시점을 GFS 분석장(분석 약 4시간 뒤 공개)으로 채워 지금 태풍에도 환경 모델을 쓴다. 겹치는 2021-06~2026-03에서 GFS 특성으로 바꿔도 성능이 같다(한국 AUC 0.875 → 0.876, `reports/gfs_comparison.txt`). 해수온·기후 지수를 넣은 조합은 오히려 성능이 떨어져 뺐다(`reports/env_experiment.txt`).

| 모델 첫 경보(보정 확률 40% 이상), 도착 전(중앙값) | 경로 모델 | 환경 모델 |
|---|---|---|
| 한국 | 30시간 | 36시간 |
| 일본 | 60시간 | 66시간 |

모델 설정과 경보 기준을 고를 때도 2015년~ 성능을 봤으므로 위 수치는 조금 낙관적일 수 있다. 2015년~을 보지 않고 학습 ~2004, 비교 2005~2014로 다시 비교해도 지금 설정이 AUC·Brier 모두 가장 좋았고, 경보 기준 40%는 30%·50% 사이의 절충이었다(`reports/tuning_holdout.txt`).

경보 기준 40%는 보정 전 50% 기준과 헛경보 비율이 비슷하다(2015년~ 환경 모델: 경보를 낸 태풍 중 실제로 오지 않은 비율 한국 52개 중 48%, 일본 126개 중 36%). 환경 모델 기준 도착 48시간 전 경보 비율은 한국 39%, 일본 60%.

## 한국 예측이 늦는 이유 (분석 결과)

`reports/analysis.json`

| 항목 | 한국 | 일본 |
|---|---|---|
| 발생부터 영향권 도착까지 걸린 시간(중앙값) | 198시간 | 156시간 |
| 모델이 처음 경보를 낸 시점(도착 전, 중앙값) | 30시간 | 60시간 |
| 도착 72시간 전에 경보한 비율 | 7% | 40% |
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
.venv/bin/python -m typhoon.env_gfs --compare  # GFS 분석장 vs NCEP 특성 비교 → reports/gfs_comparison.txt
.venv/bin/python -m typhoon.train      # 학습 → models/, reports/metrics.json
.venv/bin/python -m typhoon.analyze    # 분석 → reports/analysis.json
.venv/bin/python -m typhoon.tune_holdout  # 2015년~을 빼고 모델 설정·경보 기준 재확인 → reports/tuning_holdout.txt
.venv/bin/python -m typhoon.predict --update   # 최신 데이터 받아 가장 최근 태풍 예측
.venv/bin/python -m typhoon.predict --sid 2026288N10161
.venv/bin/python -m typhoon.export --update     # 사이트 데이터 갱신 → docs/data/
.venv/bin/python -m typhoon.track      # 경로·전향 모델 실험 → reports/track_experiment.txt
```

## 웹사이트

`docs/` 폴더가 GitHub Pages로 배포된다. 화면은 현재 태풍, 지난 태풍, 자체 모델 vs 기상청 세 가지다.
