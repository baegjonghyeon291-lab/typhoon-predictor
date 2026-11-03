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
