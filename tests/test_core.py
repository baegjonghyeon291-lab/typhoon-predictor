"""네트워크·큰 원자료 없이 도는 핵심 기능 테스트. 실행: python -m pytest -q"""
import json
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from typhoon import env_gfs, forecast_log
from typhoon.data import ROOT, haversine, min_dist
from typhoon.env_atmos import features_from
from typhoon.export import approach
from typhoon.features import FEATURES, SEOUL, TOKYO, bearing, load_model, predict, predict_range, prob


# ---------------------------------------------------------------- GFS
IDX = """1:0:d=2026110100:PRMSL:mean sea level:anl:
2:100:d=2026110100:UGRD:0.01 mb:anl:
3:200:d=2026110100:HGT:500 mb:anl:
4:300:d=2026110100:UGRD:500 mb:anl:
5:400:d=2026110100:VGRD:500 mb:anl:
6:500:d=2026110100:UGRD:850 mb:anl:
7:600:d=2026110100:VGRD:850 mb:anl:
8:700:d=2026110100:UGRD:200 mb:anl:
9:800:d=2026110100:VGRD:200 mb:anl:
10:900:d=2026110100:HGT:500 mb:6 hour fcst:
11:1000:d=2026110100:TMP:2 m above ground:anl:
"""


def test_gfs_idx_ranges():
    r = env_gfs._ranges(IDX)
    assert set(r) == set(env_gfs.FIELDS)          # 0.01 mb·예보 필드는 건너뜀
    assert r[("HGT", 500)] == (200, 299)
    assert r[("VGRD", 200)] == (800, 899)


def test_gfs_coarsen_grid_matches_ncep():
    f = np.ones((2, len(env_gfs.LAT), len(env_gfs.LON)), np.float32) * 7
    c = env_gfs._coarsen(f)
    assert c.shape == (2, 25, 33)                   # NCEP 2.5° 0~60N, 100~180E
    assert np.allclose(c, 7)


# ---------------------------------------------------------------- 대기 특성
def test_features_from_uniform_wind():
    glat, glon = np.arange(0, 60.1, 2.5), np.arange(100, 180.1, 2.5)
    t = 3
    z = np.full((t, len(glat), len(glon)), 5800, np.float32)
    u = np.full((t, 3, len(glat), len(glon)), 4.0, np.float32)
    v = np.full((t, 3, len(glat), len(glon)), -2.0, np.float32)
    dates = pd.date_range("2026-08-01", periods=t)
    pts = pd.DataFrame({"LAT": [20.0, 25.0], "LON": [130.0, 140.0],
                        "time": pd.to_datetime(["2026-08-02T06", "2026-08-05T00"])})
    o = features_from(dates, glat, glon, z, u, v, pts)
    assert o.loc[0, "atm_u_steer"] == pytest.approx(4.0)
    assert o.loc[0, "atm_v_steer"] == pytest.approx(-2.0)
    assert o.loc[0, "atm_shear"] == pytest.approx(0.0)
    assert o.loc[1, ["atm_u_steer", "atm_z500"]].isna().all()   # 전날(8/4) 자료 없음 → NaN


# ---------------------------------------------------------------- 예보 진로 접근
def test_approach_center_cone_and_far():
    a = approach([("2026-09-01T00", 30.0, 128.0, 0), ("2026-09-02T00", 33.0, 128.5, 150),
                  ("2026-09-03T00", 37.0, 131.0, 250)])
    k = a["korea"]
    assert k["center"] is not None and k["cone"] is not None
    assert k["cone"] <= k["center"]                  # 원이 중심보다 먼저 닿음
    assert k["min_km"] < 200
    far = approach([("2026-09-01T00", 15.0, 150.0, 0), ("2026-09-03T00", 20.0, 160.0, 200)])
    assert far["korea"]["center"] is None and far["korea"]["cone"] is None
    assert far["korea"]["min_km"] > 1000


# ---------------------------------------------------------------- 예보 기록
def _latest(issued="2026-09-01T03", t0="2026-09-01T00"):
    s = {"sid": "RT2615", "name": "TEST", "end": t0, "env": "gfs",
         "track": [[t0, 30.0, 128.5, 80, 960, 0.5, 0.1]],
         "forecast": {"issued": issued, "points": [[24, 33.0, 128.5, 150, 960, 40]],
                      "approach": {"korea": {"center": "2026-09-01T22", "cone": "2026-09-01T12", "min_km": 64},
                                   "japan": {"center": None, "cone": None, "min_km": 400}}},
         "korea": {"p": 0.5, "hours": 20}, "japan": {"p": 0.1, "hours": 60}}
    return {"generated": "2026-09-01T03:20", "data_until": t0, "storms": [s]}


def test_forecast_log_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "LOG", tmp_path / "log.jsonl")
    assert forecast_log.append(_latest()) == 1
    assert forecast_log.append(_latest()) == 0       # 같은 발표는 한 번
    assert forecast_log.append(_latest("2026-09-01T06")) == 1
    assert len(forecast_log._read()) == 2


def test_forecast_log_check(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(forecast_log, "CHECK", tmp_path / "check.json")
    # 가짜 IBTrACS: 6시간마다 북상해 한국 영향권을 지나감
    t = pd.date_range("2026-09-01T00", periods=40, freq="6h")
    df = pd.DataFrame({"SID": "2026244N28128", "time": t, "LAT": np.linspace(30, 45, len(t)), "LON": 128.5})
    for c in ("korea", "japan"):
        df[f"in_{c}"] = min_dist(df.LAT.values, df.LON.values, c) <= 200
    forecast_log.append(_latest())
    s = forecast_log.check(df=df)
    assert s["korea"]["center_hit"] == 1             # 임시 번호(RT…)도 위치로 실제 태풍을 찾아 맞춤
    # 이미 영향권 안에서 기록된 판정은 그 나라 집계에서 빠진다
    inside = df[df.in_korea].iloc[1]
    t1 = inside.time.strftime("%Y-%m-%dT%H")
    late = _latest(t1, t1)
    late["storms"][0]["track"][0][1] = float(inside.LAT)
    forecast_log.append(late)
    s = forecast_log.check(df=df)
    assert s["korea"]["forecasts"] == 1


# ---------------------------------------------------------------- 모델
def _rows(n=40, seed=0):
    rng = np.random.default_rng(seed)
    lat, lon = rng.uniform(12, 35, n), rng.uniform(120, 150, n)
    d = pd.DataFrame({"LAT": lat, "LON": lon, "wind": rng.uniform(35, 110, n), "pres": rng.uniform(920, 1000, n),
                      "dwind_24": rng.uniform(-20, 20, n), "dlat_6": rng.uniform(-.5, 1.5, n),
                      "dlon_6": rng.uniform(-1.5, 1, n), "speed_kmh": rng.uniform(5, 40, n),
                      "heading": rng.uniform(0, 360, n), "age_h": rng.uniform(0, 200, n),
                      "month": rng.integers(6, 11, n).astype(float), "doy": rng.integers(150, 300, n).astype(float)})
    d["dlat_24"], d["dlon_24"] = d.dlat_6 * 4, d.dlon_6 * 4
    d["dist_seoul"], d["bearing_seoul"] = haversine(lat, lon, *SEOUL), bearing(lat, lon, *SEOUL)
    d["dist_tokyo"], d["bearing_tokyo"] = haversine(lat, lon, *TOKYO), bearing(lat, lon, *TOKYO)
    return d[FEATURES]


@pytest.mark.parametrize("country", ["korea", "japan"])
def test_prob_uses_platt_calibration(country):
    m = load_model(country, full=False)
    X = _rows()
    a, b = m["calib"]
    p = prob(m, X)
    assert np.allclose(p, 1 / (1 + np.exp(-(a * m["clf"].decision_function(X) + b))))
    assert ((p >= 0) & (p <= 1)).all()


@pytest.mark.parametrize("country", ["korea", "japan"])
def test_predict_range_is_valid(country):
    lo, hi = predict_range(country, _rows())          # 환경 열이 없으면 경로 모델
    assert (lo >= 0).all() and (hi >= lo).all()


def test_predict_ignores_index_labels():
    X = _rows()
    p, h = predict("korea", X.set_axis(range(100, 140)))
    p2, h2 = predict("korea", X)
    assert np.allclose(p, p2) and np.allclose(h, h2)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
@pytest.mark.parametrize("country", ["korea", "japan"])
def test_browser_model_matches_python(country, tmp_path):
    """사이트(docs/model.js + docs/data/model.json)와 파이썬 경로 모델 결과가 같아야 한다.
    모델을 바꾸고 export_models()로 model.json 을 다시 만들지 않으면 여기서 걸린다."""
    m = load_model(country, full=False)
    X = _rows(12, seed=1)
    py = np.c_[prob(m, X), m["reg"].predict(X), *predict_range(country, X)]
    f = tmp_path / "rows.json"
    f.write_text(json.dumps(X.to_dict("records")))
    js = (f"const {{predict}} = require({json.dumps(str(ROOT / 'docs' / 'model.js'))});"
          f"const M = require({json.dumps(str(ROOT / 'docs' / 'data' / 'model.json'))});"
          f"const rows = require({json.dumps(str(f))});"
          f"console.log(JSON.stringify(rows.map(r => {{ const q = predict(M, '{country}', r);"
          f" return [q.p, q.hours, q.lo, q.hi]; }})));")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    js = np.array(json.loads(out))
    assert np.allclose(js[:, 0], py[:, 0], atol=1e-6)            # 확률(잎 값 7자리 반올림 오차)
    assert np.allclose(js[:, 1:], py[:, 1:], atol=1e-3)          # 시간
