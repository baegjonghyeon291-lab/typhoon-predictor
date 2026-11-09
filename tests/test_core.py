"""네트워크·큰 원자료 없이 도는 핵심 기능 테스트. 실행: python -m pytest -q"""
import json
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from typhoon import env_atmos, env_gfs, explain, export, features, forecast_log, realtime
from typhoon.data import ROOT, haversine, min_dist
from typhoon.env_atmos import features_from
from typhoon.export import approach
from typhoon.sources import jtwc
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


def test_gfs_decode_real_grib():
    """eccodes 를 실제로 불러 GRIB 메시지를 해독한다(서버에서 라이브러리가 동작하는지 함께 확인)."""
    eccodes = pytest.importorskip("eccodes")
    h = eccodes.codes_grib_new_from_samples("regular_ll_pl_grib2")
    for k, v in [("Ni", 720), ("Nj", 361), ("latitudeOfFirstGridPointInDegrees", 90.0),
                 ("longitudeOfFirstGridPointInDegrees", 0.0), ("latitudeOfLastGridPointInDegrees", -90.0),
                 ("longitudeOfLastGridPointInDegrees", 359.5), ("iDirectionIncrementInDegrees", 0.5),
                 ("jDirectionIncrementInDegrees", 0.5)]:
        eccodes.codes_set(h, k, v)
    lat = np.linspace(90, -90, 361)
    eccodes.codes_set_values(h, np.repeat(lat, 720).astype(float))     # 값 = 위도
    msg = eccodes.codes_get_message(h)
    eccodes.codes_release(h)
    f = env_gfs._decode(msg)
    assert f.shape == (len(env_gfs.LAT), len(env_gfs.LON))
    assert f[0, 0] == pytest.approx(0, abs=.01) and f[-1, 0] == pytest.approx(60, abs=.01)  # 0~60N 오름차순


def test_gfs_bad_message_only_skips_that_day(tmp_path, monkeypatch):
    monkeypatch.setattr(env_gfs, "GFS", tmp_path)
    def broken(*a):
        raise RuntimeError("잘린 GRIB")
    monkeypatch.setattr(env_gfs, "_fetch_cycle", broken)
    assert env_gfs.fetch_day(pd.Timestamp("2026-10-01")) is None


def test_gfs_corrupt_cache_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(env_gfs, "GFS", tmp_path)
    monkeypatch.setattr(env_gfs, "_fetch_cycle", lambda *a: None)
    day = pd.Timestamp("2026-10-01")
    env_gfs._path(day).write_bytes(b"not npz")
    assert env_gfs.fetch_day(day) is None
    assert not env_gfs._path(day).exists()


def _gfs_df(lat=20.0):
    return pd.DataFrame({"SID": ["A", "A", "OLD"], "time": pd.to_datetime(["2026-10-02T00", "2026-10-02T06", "2026-01-01T00"]),
                         "LAT": [lat, 21.0, 20.0], "LON": [130.0, 130.0, 130.0],
                         "atm_z500": [np.nan, np.nan, 5800.0], "atm_u_steer": [np.nan, np.nan, 1.0]})


def test_gfs_fill_reuses_cached_features(tmp_path, monkeypatch):
    monkeypatch.setattr(env_gfs, "FEATURES_CACHE", tmp_path / "features.parquet")
    calls = []
    def fake(pts, workers=8):
        calls.append(sorted(pts.SID.unique()))
        return pd.DataFrame({"SID": pts.SID, "time": pts.time, "atm_z500": 5900.0, "atm_u_steer": 2.0}, index=pts.index)
    monkeypatch.setattr(env_gfs, "features", fake)
    r = env_gfs.fill(_gfs_df())
    assert calls == [["A"]]
    assert r.env_src.tolist() == ["gfs", "gfs", "ncep"]
    assert r.atm_z500.tolist() == [5900.0, 5900.0, 5800.0]
    r2 = env_gfs.fill(_gfs_df())                     # 같은 태풍·같은 위치는 다시 계산하지 않음
    assert calls == [["A"]] and r2.env_src.tolist() == ["gfs", "gfs", "ncep"]
    env_gfs.fill(_gfs_df(lat=22.0))                  # 위치가 바뀌면(IBTrACS 개정) 다시 계산
    assert calls == [["A"], ["A"]]


def test_gfs_fill_retries_blank_rows_only_while_day_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(env_gfs, "GFS", tmp_path)
    monkeypatch.setattr(env_gfs, "FEATURES_CACHE", tmp_path / "features.parquet")
    calls = []
    def blank(pts, workers=8):
        calls.append(1)
        return pd.DataFrame({"SID": pts.SID, "time": pts.time, "atm_z500": np.nan, "atm_u_steer": np.nan}, index=pts.index)
    monkeypatch.setattr(env_gfs, "features", blank)
    env_gfs.fill(_gfs_df())
    env_gfs.fill(_gfs_df())                          # 전날 자료가 아직 없으면 다시 시도
    assert len(calls) == 2
    env_gfs._path(pd.Timestamp("2026-10-01")).write_bytes(b"x")
    env_gfs.fill(_gfs_df())                          # 자료가 있는데 비었으면 다시 계산해도 그대로라 건너뜀
    assert len(calls) == 2


def test_gfs_fetch_stops_at_time_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(env_gfs, "GFS", tmp_path)
    monkeypatch.setattr(env_gfs, "BUDGET_S", -1)
    monkeypatch.setattr(env_gfs, "_fetch_cycle", lambda *a: pytest.fail("제한 시간이 지나면 받지 않음"))
    with pytest.warns(RuntimeWarning, match="제한"):
        assert env_gfs.fetch([pd.Timestamp("2026-10-01")]) == {}


def test_gfs_failure_keeps_update_going(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "ENV_FILES", [])
    def broken(df):
        raise RuntimeError("GFS 고장")
    monkeypatch.setattr(env_gfs, "fill", broken)
    with pytest.warns(RuntimeWarning, match="GFS 보충 실패"):
        r = features.add_env(_gfs_df().rename(columns={"atm_z500": "atm_shear"}), gfs=True)
    assert r.env_src.tolist() == [None, None, "ncep"]    # 재분석 출처는 남는다


def test_gfs_no_empty_rows_still_reports_source(tmp_path):
    d = _gfs_df().iloc[[2]]
    assert env_gfs.fill(d).env_src.tolist() == ["ncep"]


# ---------------------------------------------------------------- 자동 갱신 보호
def test_server_last_day_falls_back_to_previous_year(monkeypatch):
    now = env_atmos.dt.datetime.utcnow().year
    class DS:
        time = type("T", (), {"values": np.array(["2026-03-17T00"], "datetime64[ns]")})
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def opener(url):
        if str(now) in url:
            raise OSError("없는 파일")
        return DS()
    monkeypatch.setattr(env_atmos.xr, "open_dataset", opener)
    assert env_atmos.server_last_day() == pd.Timestamp("2026-03-17")


def test_refresh_sources_checks_ncep_only_inside_time_limit(monkeypatch):
    """서버 확인은 강제 종료할 수 있는 하위 프로세스 안에서만 한다."""
    monkeypatch.setattr(export, "download", lambda: None)
    def forbidden(*a, **k):
        raise AssertionError("부모 프로세스에서 OPeNDAP 를 열면 안 됨")
    monkeypatch.setattr(env_atmos.xr, "open_dataset", forbidden)
    started = []
    class P:
        pid = 1
        def __init__(self, cmd, **k): started.append(cmd)
        def wait(self, timeout=None): return 0
    monkeypatch.setattr(subprocess, "Popen", P)
    export.refresh_sources()
    assert started and started[0][-1] == "--recent"


def test_recent_skips_download_when_nothing_to_fill(monkeypatch):
    monkeypatch.setattr(env_atmos, "server_last_day", lambda: pd.Timestamp("2026-03-17"))
    monkeypatch.setattr(env_atmos, "needs_update", lambda s: False)
    monkeypatch.setattr(env_atmos, "build", lambda **k: pytest.fail("받으면 안 됨"))
    monkeypatch.setattr("sys.argv", ["env_atmos", "--recent"])
    env_atmos.main()


def test_feed_status_tells_failure_from_quiet(tmp_path, monkeypatch):
    """피드를 못 받았는지(False), 받았는데 태풍이 없는지(True) 사이트가 구분할 수 있어야 한다."""
    monkeypatch.setattr(realtime.jma, "XML_DIR", tmp_path)
    monkeypatch.setattr(realtime, "FEED_OK", None)
    monkeypatch.setattr(realtime.jma, "fetch_feed", lambda: [])
    realtime._download_new()
    assert realtime.FEED_OK is True
    def down():
        raise OSError("접속 실패")
    monkeypatch.setattr(realtime.jma, "fetch_feed", down)
    with pytest.warns(RuntimeWarning):
        realtime._download_new()
    assert realtime.FEED_OK is False


def test_forecast_log_failure_does_not_stop_export(monkeypatch):
    def broken(latest):
        raise OSError("디스크")
    monkeypatch.setattr(forecast_log, "append", broken)
    with pytest.warns(RuntimeWarning, match="예보 기록 실패"):
        export.log_forecasts({})


# ---------------------------------------------------------------- 사이트 자료 일치
def test_site_lead_file_matches_reports():
    site = json.loads((ROOT / "docs" / "data" / "kma_lead.json").read_text())
    rep = json.loads((ROOT / "reports" / "kma_lead.json").read_text())
    a = json.loads((ROOT / "reports" / "analysis.json").read_text())
    assert site == rep
    assert site["our_model_korea_full"]["median_warning_lead_h"] == a["model_warning_lead_full"]["korea"]["median_warning_lead_h"]


def test_site_track_file_matches_reports():
    """사이트 경로 예측 섹션(docs/data/track.json)이 reports/ 실험 결과와 같아야 한다(scripts/track_json.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("track_json", ROOT / "scripts" / "track_json.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    site = json.loads((ROOT / "docs" / "data" / "track.json").read_text(encoding="utf-8"))
    assert site == mod.build()


def test_near_storms_named_within_limit():
    df = pd.DataFrame({"SID": ["a", "a", "b", "c", "d"], "SEASON": [2025, 2025, 2025, 2025, 2014],
                       "NAME": ["LINGLING", "LINGLING", "UNNAMED", "NAKRI", "OLD"],
                       "dist_korea": [800.0, 291.0, 20.0, 698.0, 100.0]})
    assert list(export.near_storms(df)) == ["a"]


def test_damage_file_matches_history():
    site = ROOT / "docs" / "data"
    damage = json.loads((site / "damage.json").read_text())["storms"]
    sids = {s["sid"] for s in json.loads((site / "history.json").read_text())}
    assert set(damage) <= sids
    assert all(v["won"] > 0 for v in damage.values())


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_site_scripts_parse():
    for f in ["app.js"]:
        subprocess.run(["node", "--check", str(ROOT / "docs" / f)], check=True)


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
    assert k["near"] in ("제주", "광주", "부산")      # 가장 가까이 지나는 도시 이름
    ts = [c["t"] for c in k["cities"]]
    assert ts and ts == sorted(ts) and ts[0] == k["center"]   # 도시별 진입 시각, 맨 앞이 첫 진입
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


# ---------------------------------------------------------------- 예측 근거
def test_group_shap_adds_up_to_tree_score():
    m = load_model("korea", full=False)
    X = _rows(6, seed=2)
    groups, base = explain.group_values(m, FEATURES, X)
    assert np.allclose(base + groups.sum(axis=1).to_numpy(), m["clf"].decision_function(X), atol=1e-6)
    assert "계절" in groups and "상공 바람" not in groups   # 경로 모델에는 상공 특성이 없다


def test_contributions_shape_and_failure_is_safe(monkeypatch):
    out = explain.contributions("korea", _rows(4, seed=3))
    assert len(out) == 4 and all(len(r) <= explain.TOP_N for r in out)
    assert all(abs(v) >= explain.MIN_PP for r in out for _, v in r)

    def broken(*a):
        raise ValueError("x")
    monkeypatch.setattr(explain, "contributions", broken)
    with pytest.warns(RuntimeWarning, match="근거 계산 실패"):
        assert explain.safe_contributions("korea", _rows(2)) == [[], []]


# ---------------------------------------------------------------- JTWC
WARNING = """WTPN31 PGTW 310300
SUBJ/TYPHOON 30W (IN-FA) WARNING NR 043//
   WARNING POSITION:
   310000Z --- NEAR 23.1N 139.3E
   PRESENT WIND DISTRIBUTION:
   MAX SUSTAINED WINDS - 070 KT, GUSTS 085 KT
   FORECASTS:
   12 HRS, VALID AT:
   311200Z --- 23.5N 138.7E
   MAX SUSTAINED WINDS - 075 KT, GUSTS 090 KT
   24 HRS, VALID AT:
   010000Z --- 24.4N 170.0W
   MAX SUSTAINED WINDS - 065 KT, GUSTS 080 KT
"""


def test_jtwc_parse_warning():
    import datetime as dt
    w = jtwc.parse_warning(WARNING, now=dt.datetime(2026, 11, 1, 5))    # 다음 달 초에 받아도 지난달 31일로
    assert w["name"] == "IN-FA" and w["number"] == "30W" and w["wind"] == 70
    assert w["time"] == dt.datetime(2026, 10, 31, 0) and w["issued"] == dt.datetime(2026, 10, 31, 3)
    assert w["points"] == [[12, 23.5, 138.7, 75], [24, 24.4, 190.0, 65]]
    assert jtwc.parse_warning("다른 형식") is None
    f = export.us_forecast(w)
    assert f["issued"] == "2026-10-31T03" and f["approach"]["korea"]["cone"] is None


def test_log_keeps_inputs_and_us_forecast(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "LOG", tmp_path / "log.jsonl")
    track = [["2026-08-01T00", 25.0, 130.0, 60, 980, 0.1, 0.2]]
    latest = {"generated": "2026-08-01T05:00", "data_until": "2026-08-01T00", "storms": [{
        "sid": "s", "name": "X", "end": "2026-08-01T00", "track": track, "korea": {"p": .1, "why": [["계절", 3]]},
        "japan": {"p": .2}, "inputs": {"LAT": 25.0}, "forecast_us": {"issued": "2026-08-01T00", "points": []},
        "forecast": {"issued": "2026-08-01T00", "points": [], "approach": {}}}]}
    assert forecast_log.append(latest) == 1
    r = json.loads(forecast_log.LOG.read_text())
    assert r["inputs"] == {"LAT": 25.0} and r["us"]["issued"] == "2026-08-01T00"
    assert r["model"]["korea"]["why"] == [["계절", 3]]


def test_log_keeps_us_forecast_without_new_japan_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "LOG", tmp_path / "log.jsonl")
    track = [["2026-08-01T00", 25.0, 130.0, 60, 980, 0.1, 0.2]]

    def latest(jma, us):
        s = {"sid": "s", "name": "X", "end": "2026-08-01T00", "track": track, "korea": {"p": .1}, "japan": {"p": .2}}
        if jma:
            s["forecast"] = {"issued": jma, "points": [], "approach": {}}
        if us:
            s["forecast_us"] = {"issued": us, "points": []}
        return {"generated": "2026-08-01T05:00", "data_until": "2026-08-01T00", "storms": [s]}
    assert forecast_log.append(latest(None, "2026-08-01T03")) == 1      # 일본 예보가 없어도 남긴다
    assert forecast_log.append(latest("2026-08-01T00", "2026-08-01T03")) == 1
    assert forecast_log.append(latest("2026-08-01T00", "2026-08-01T09")) == 1   # 미국 예보만 새로 나옴
    assert forecast_log.append(latest("2026-08-01T00", "2026-08-01T09")) == 0


def test_still_active():
    import datetime as dt
    now = dt.datetime(2026, 8, 2, 0)
    assert export.still_active(pd.DataFrame({"time": pd.to_datetime(["2026-08-01T12"])}), now)
    assert not export.still_active(pd.DataFrame({"time": pd.to_datetime(["2026-07-30T12"])}), now)


def test_false_alarm(monkeypatch):
    from typhoon import analyze
    t = pd.to_datetime(["2020-08-01T00", "2020-08-02T00", "2020-08-01T00", "2020-08-01T00"])
    df = pd.DataFrame({"SID": ["hit", "hit", "miss", "quiet"], "SEASON": 2020, "time": t,
                       "in_korea": [False, True, False, False]})
    monkeypatch.setattr(analyze, "predict", lambda c, d: (np.where(d.SID == "quiet", .1, .9), None))
    assert analyze.false_alarm(df, "korea") == {"warned_storms": 2, "false_alarm_ratio": .5}


# ---------------------------------------------------------------- 경로·전향
def test_track_future_and_turn():
    from typhoon import track
    assert track.angle_diff(350.0, 10.0) == 20
    t = pd.date_range("2020-08-01", periods=17, freq="6h")
    lat = [10 + .5 * max(0, i - 8) * 2 for i in range(17)]        # 서진하다 2일 뒤부터 북상
    lon = [140 - .5 * min(i, 8) for i in range(17)]
    d = pd.DataFrame({"SID": "a", "time": t, "LAT": lat, "LON": lon})
    d["dlat_24"], d["dlon_24"] = d.LAT - d.LAT.shift(4), d.LON - d.LON.shift(4)
    d["dlat_6"], d["dlon_6"] = d.LAT - d.LAT.shift(1), d.LON - d.LON.shift(1)
    f = track.add_future(d, 24)
    assert f.dx.iloc[0] == pytest.approx(-2.0) and f.dy.iloc[-1] != f.dy.iloc[-1]    # 끝 시점은 정답 없음
    py, px = track.persistence(d.iloc[[4]], 24)
    assert px[0] == pytest.approx(-2.0) and py[0] == 0
    turn = track.turn_table(d)
    assert turn.turn.iloc[0] == 1      # 서진 중 앞으로 북쪽으로 꺾임


# ---------------------------------------------------------------- GFS 예보 중심 추적
def test_gfs_track_follows_moving_low():
    from typhoon import gfs_track as G
    lat0, lon0 = 15.0, 140.0
    P = np.empty((len(G.LEADS), len(G.LAT), len(G.LON)), np.float32)
    for i, f in enumerate(G.LEADS):          # 6시간마다 북서로 0.6°·0.9° 이동하는 저기압
        la, lo = lat0 + .1 * f, lon0 - .15 * f
        d = haversine(G.LAT[:, None], G.LON[None, :], la, lo)
        P[i] = 1010 - 40 * np.exp(-(d / 250) ** 2)
    tr = G.track_cycle(P, lat0, lon0)
    assert [t[0] for t in tr] == G.LEADS                  # 끝까지 따라감
    f, la, lo, pm = tr[-1]
    assert haversine(la, lo, lat0 + .1 * f, lon0 - .15 * f) < 60
    flat = np.full_like(P, 1010)                          # 저기압이 없으면 바로 놓침
    assert G.track_cycle(flat, lat0, lon0) == []


def test_gfs_cycle_uses_only_published_runs():
    from typhoon.gfs_track import cycle_for
    assert cycle_for("2026-10-20T06") == pd.Timestamp("2026-10-20T00")
    assert cycle_for("2026-10-20T00") == pd.Timestamp("2026-10-19T12")   # 00시 발표는 아직 공개 전
    assert cycle_for("2026-10-20T18") == pd.Timestamp("2026-10-20T12")


def test_log_keeps_own_track(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "LOG", tmp_path / "log.jsonl")
    latest = _latest()
    latest["storms"][0]["track_own"] = {"issued": "2026-09-01T00", "cycle": "2026-08-31T12",
                                        "points": [[24, 32.0, 129.0]], "turn48": 20}
    assert forecast_log.append(latest) == 1
    assert forecast_log._read()[0]["own"]["cycle"] == "2026-08-31T12"
    latest["storms"][0]["track_own"] = dict(latest["storms"][0]["track_own"], cycle="2026-09-01T00")
    assert forecast_log.append(latest) == 1                 # GFS 새 발표도 새 기록


def test_kma_latest_without_key_is_empty(monkeypatch):
    from typhoon.sources import kma
    monkeypatch.setenv("KMA_APIHUB_KEY", "")
    assert kma.latest() == {}                               # 키가 없으면 갱신을 멈추지 않고 건너뜀


def test_log_keeps_kma_forecast(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "LOG", tmp_path / "log.jsonl")
    latest = _latest()
    latest["storms"][0]["forecast_kr"] = {"issued": "2026-09-01T00", "seq": 3, "points": [[24, 32.0, 129.0, 30]]}
    assert forecast_log.append(latest) == 1
    latest["storms"][0]["forecast_kr"] = dict(latest["storms"][0]["forecast_kr"], issued="2026-09-01T06")
    assert forecast_log.append(latest) == 1                 # 기상청 새 발표도 새 기록
    assert [r["kr"]["issued"] for r in forecast_log._read()] == ["2026-09-01T00", "2026-09-01T06"]


# ---------------------------------------------------------------- 복기(전향)
def _storm(turn_at=None, n=24):
    """6시간마다 서쪽으로 가다가 turn_at번째 점부터 북쪽으로 꺾이는 가짜 경로."""
    lat, lon, rows = 15.0, 140.0, []
    t = pd.date_range("2026-09-01", periods=n, freq="6h")
    for i in range(n):
        rows.append((t[i], lat, lon))
        if turn_at is not None and i >= turn_at:
            lat += .7
        else:
            lon -= .7
    return pd.DataFrame(rows, columns=["time", "LAT", "LON"])


def test_review_actual_turn_and_first_call():
    from typhoon.review import actual_turn, first_call
    assert actual_turn(_storm(None)) is None
    g = _storm(12)
    turn = actual_turn(g)
    assert turn is not None and turn <= g.time[12]
    hh = np.arange(0, 121, 12)
    straight = (g.time[4], hh, np.full(len(hh), 15.0), 140 - 4 * .7 - hh / 6 * .7)          # 계속 서진 예보
    north = (g.time[6], hh, 15.0 + np.where(hh > 24, (hh - 24) / 6 * .7, 0),                 # 24시간 뒤 북상 예보
             np.where(hh > 24, 140 - 10 * .7, 140 - 6 * .7 - hh / 6 * .7))
    assert first_call([straight], g, turn) is None
    assert first_call([straight, north], g, turn) == g.time[6]


def test_score_sources_same_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "LOG", tmp_path / "log.jsonl")
    t = pd.date_range("2026-09-01T00", periods=40, freq="6h")
    df = pd.DataFrame({"SID": "2026244N28128", "time": t, "LAT": np.linspace(30, 45, len(t)), "LON": 128.5})
    for c in ("korea", "japan"):
        df[f"in_{c}"] = min_dist(df.LAT.values, df.LON.values, c) <= 200
    latest = _latest()
    latest["storms"][0]["forecast"]["points"] = [[24, 31.5, 128.5, 150, 960, 40], [72, 36.0, 128.5, 250, 970, 30]]
    latest["storms"][0]["forecast_kr"] = {"issued": "2026-09-01T00", "points": [[24, 31.5, 128.5, 30], [72, 36.0, 128.5, 25]],
                                          "approach": {"korea": {"center": "2026-09-01T20"}, "japan": {"center": None}}}
    forecast_log.append(latest)
    s = forecast_log.score_sources(forecast_log._read(), df, df.time.max())
    assert s["kr"]["issues"] == 1 and s["kr"]["korea"]["hit"] == 1
    assert s["kr"]["track_error_km"]["24h"]["n"] == 1 and s["kr"]["track_error_km"]["24h"]["mean"] < 10
    assert s["jma"]["korea"]["hit"] == 1 and s["us"]["issues"] == 0


def test_display_wind_only_when_storm_has_no_jma_wind():
    from typhoon.export import display_wind
    only_usa = pd.DataFrame({"wind": [np.nan, np.nan, np.nan], "USA_WIND": [60.0, 100.0, np.nan]})
    w, used = display_wind(only_usa)
    assert used and w.iloc[1] == pytest.approx(88) and np.isnan(w.iloc[2])
    mixed = pd.DataFrame({"wind": [50.0, np.nan, np.nan], "USA_WIND": [60.0, 100.0, np.nan]})
    w, used = display_wind(mixed)                           # 일본 값이 있으면 최대 풍속을 미국 값으로 올리지 않는다
    assert used is False and w.iloc[0] == 50 and np.isnan(w.iloc[1])
    assert display_wind(mixed.assign(USA_WIND=np.nan, wind=np.nan))[1] is False


def test_score_sources_counts_gfs_cycle_once(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "LOG", tmp_path / "log.jsonl")
    t = pd.date_range("2026-09-01T00", periods=40, freq="6h")
    df = pd.DataFrame({"SID": "2026244N28128", "time": t, "LAT": np.linspace(30, 45, len(t)), "LON": 128.5})
    for c in ("korea", "japan"):
        df[f"in_{c}"] = min_dist(df.LAT.values, df.LON.values, c) <= 200
    own = {"cycle": "2026-08-31T12", "points": [[24, 31.5, 128.5]], "approach": {}}
    rows = [{"sid": "2026244N28128", "name": "X", "position": {"time": i, "lat": 30, "lon": 128.5}, "own": dict(own, issued=i)}
            for i in ("2026-09-01T00", "2026-09-01T06")]
    assert forecast_log.score_sources(rows, df, df.time.max())["own"]["issues"] == 1


def test_kma_latest_skips_issue_without_forecast(monkeypatch):
    from typhoon.sources import kma
    monkeypatch.setenv("KMA_APIHUB_KEY", "x")
    lst = pd.DataFrame({"NOW": ["1"], "SEQ": ["7"], "TYP_EN": ["INFA"]})
    d = pd.DataFrame({"FT": [0, 1, 0], "SEQ": [3, 3, 4], "TMD": [0, 24, 0], "LAT": [20.0, 22.0, 21.0], "LON": [130.0, 129.0, 129.5],
                      "WS": [30, 35, 30], "TYP_TM": ["202611010000", "202611010000", "202611010600"]})
    frames = iter([lst, d])
    monkeypatch.setattr(kma, "parse_hub_text", lambda *a: next(frames))
    monkeypatch.setattr(kma.requests, "get", lambda *a, **k: type("R", (), {"text": "", "raise_for_status": lambda self: None})())
    w = kma.latest()["INFA"]
    assert w["seq"] == 3 and w["points"] == [[24, 22.0, 129.0, 35]]


def test_gfs_cycle_recent_gap_not_cached(tmp_path, monkeypatch):
    from typhoon import gfs_track
    monkeypatch.setattr(gfs_track, "DIR", tmp_path)
    field = np.zeros((len(gfs_track.LAT), len(gfs_track.LON)))
    monkeypatch.setattr(gfs_track, "_fetch", lambda s, c, f: None if f == 12 else field)
    recent = pd.Timestamp.now("UTC").tz_localize(None).floor("12h")
    assert gfs_track.fetch_cycle(None, recent) == "missing" and not gfs_track._path(recent).exists()
    assert gfs_track.fetch_cycle(None, pd.Timestamp("2024-01-01")) == "ok"      # 오래된 발표의 빠진 파일은 다시 안 올라온다


def test_gefs_failed_member_not_cached_and_holes_refetched(tmp_path, monkeypatch):
    from typhoon import gefs
    monkeypatch.setattr(gefs, "DIR", tmp_path)
    monkeypatch.setattr(gefs.time, "sleep", lambda s: None)
    c, field = pd.Timestamp("2026-10-20"), np.zeros((len(gefs.LAT), len(gefs.LON)))

    def flaky(s, c, m, f):
        if f == 12:
            raise OSError("끊김")
        return field
    monkeypatch.setattr(gefs, "_fetch", flaky)
    assert gefs.fetch_member(None, c, "gec00") is False and not gefs._path(c, "gec00").exists()
    monkeypatch.setattr(gefs, "_fetch", lambda *a: field)
    assert gefs.fetch_member(None, c, "gec00") is True and gefs._complete(gefs._path(c, "gec00"))
    holes = np.full((len(gefs.LEADS), 2, 2), 1.0)           # 이전 방식으로 저장된, 구멍 난 캐시는 다시 받는다
    holes[3] = np.nan
    np.savez_compressed(gefs._path(c, "gec00"), p=holes)
    assert not gefs._complete(gefs._path(c, "gec00"))
    gefs._path(c, "gec00").write_bytes(b"broken")
    assert not gefs._complete(gefs._path(c, "gec00"))


def _gefs_case(monkeypatch, n, dlat, dlon):
    """북쪽으로 가던 태풍과, 시점 뒤 (dlat, dlon)도/48시간으로 움직이는 멤버 n개."""
    from typhoon import gefs
    g = pd.DataFrame({"time": pd.date_range("2026-10-19T12", periods=7, freq="6h"), "LAT": np.arange(7) * .5 + 15.0, "LON": 140.0})
    leads = np.arange(0, 133, 6.0)
    track = (leads, 15.0 + leads / 48 * dlat, 140.0 + leads / 48 * dlon)
    monkeypatch.setattr(gefs, "member_tracks", lambda *a: {f"m{i}": track for i in range(n)})
    return gefs, g, g.time.iloc[-1]


def test_gefs_turn_requires_movement(monkeypatch):
    gefs, g, t = _gefs_case(monkeypatch, 20, 0, 4.0)        # 동쪽으로 충분히 움직임: 전향
    assert gefs.turn_probability(g, t)[0] == 1
    gefs, g, t = _gefs_case(monkeypatch, 20, 0, .5)         # 방향은 꺾였지만 48시간에 약 50km: 흔들림
    assert gefs.turn_probability(g, t)[0] == 0


def test_gefs_needs_enough_members(monkeypatch):
    gefs, g, t = _gefs_case(monkeypatch, gefs_min := 9, 0, 4.0)
    assert gefs_min < gefs.MIN_MEMBERS and gefs.turn_probability(g, t) is None and gefs.late_turn(g, t) is None


def test_review_false_call_and_summary():
    from typhoon.review import false_call, summarize
    g = _storm(None)
    hh = np.arange(0, 121, 12)
    straight = (g.time[4], hh, np.full(len(hh), 15.0), 140 - 4 * .7 - hh / 6 * .7)
    north = (g.time[4], hh, 15.0 + hh / 6 * .7, np.full(len(hh), 137.2))
    assert not false_call([straight], g) and false_call([straight, north], g)
    lines = summarize({"a": {"turn": "t", "kma": {"lead_h": 10}, "own": "none", "clf": None},
                       "b": {"turn": "t", "kma": None, "own": {"lead_h": 30}, "clf": {"lead_h": 3}},
                       "c": {"turn": None, "false": {"kma": True, "own": None, "clf": False}}})
    assert "kma: 자료 있는 태풍 2개 중 미리 예측 1개" in lines[0]
    assert lines[3] == "kma: 전향 없던 태풍 1개 중 전향 예측 1개" and lines[4].startswith("own: 전향 없던 태풍 0개")
    assert "같은 표본(자체 경로 자료가 있는 1개) kma: 자료 있는 태풍 1개 중 미리 예측 0개" in lines[6]
