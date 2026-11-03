"""웹사이트(docs/)용 JSON 생성: 모델, 최근 태풍 예측, 지난 태풍 기록, 통계."""
import argparse
import json
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd

from .data import load, download, haversine, ROOT, CITIES, CITY_NAMES, RADIUS_KM
from . import realtime
from .features import add_features, add_targets, add_env, predict, predict_range, FEATURES
from .train import SPLIT_YEAR

OUT = ROOT / "docs" / "data"
RECENT_DAYS = 21


def num(v, d=2):
    return None if pd.isna(v) else round(float(v), d)


def tree_json(model):
    trees = []
    for it in model._predictors:
        nodes = []
        for n in it[0].nodes:
            if n["is_leaf"]:
                # 잎 값은 7자리로(파일 35% 감소, 결과 차이 1e-6 수준). 분기 기준값은 자료값과 맞물려 있어 줄이면 안 됨
                nodes.append([float(f"{n['value']:.7g}")])
            else:
                nodes.append([int(n["feature_idx"]), min(float(n["num_threshold"]), 1e300),
                              int(n["missing_go_to_left"]), int(n["left"]), int(n["right"])])
        trees.append(nodes)
    return {"base": float(np.ravel(model._baseline_prediction)[0]), "trees": trees}


def export_models():
    out = {"features": FEATURES}
    for c in ["korea", "japan"]:
        m = joblib.load(ROOT / "models" / f"{c}.joblib")
        out[c] = {"clf": tree_json(m["clf"]), "reg": tree_json(m["reg"]), "calib": m.get("calib")}
        if "lo" in m:                                   # 남은 시간 80% 범위(features.predict_range)
            out[c].update(lo=tree_json(m["lo"]), hi=tree_json(m["hi"]), delta=m["delta"])
    (OUT / "model.json").write_text(json.dumps(out, separators=(",", ":")))


def predict_rows(d, models):
    d = d.copy()
    for c in models:
        d[f"p_{c}"], d[f"h_{c}"] = predict(c, d)
        d[f"lo_{c}"], d[f"hi_{c}"] = predict_range(c, d)
    return d


def track(g, extra=()):
    return [[t.strftime("%Y-%m-%dT%H"), num(la), num(lo), num(w, 0), num(p, 0), *[num(r[e], 3) for e in extra]]
            for t, la, lo, w, p, (_, r) in zip(g.time, g.LAT, g.LON, g.wind, g.pres, g.iterrows())]


def approach(path):
    """예보 진로(시각, 위도, 경도, 70% 반경 km)를 1시간 간격으로 이어 나라별 영향권 접근 판정.
    center: 중심이 도시 200km 안에 처음 들어오는 시각, cone: 70% 확률 원이 그 영역에 처음 닿는 시각,
    min_km: 예보 기간 중 중심과 가장 가까운 도시까지 거리, near: 그 도시 이름, cities: 중심이 들어오는 도시별 시각(빠른 순)."""
    t = pd.to_datetime([p[0] for p in path])
    hrs = ((t - t[0]) / pd.Timedelta(hours=1)).to_numpy(float)
    grid = np.arange(0, hrs[-1] + 1)
    lat, lon, r = (np.interp(grid, hrs, [p[k] or 0 for p in path]) for k in (1, 2, 3))
    out = {}
    for c in CITIES:
        by_city = np.array([haversine(lat, lon, a, b) for a, b in CITIES[c]])
        d = by_city.min(axis=0)
        first = lambda ok: (t[0] + pd.Timedelta(hours=float(grid[ok][0]))).strftime("%Y-%m-%dT%H") if ok.any() else None
        out[c] = {"center": first(d <= RADIUS_KM), "cone": first(d - r <= RADIUS_KM), "min_km": num(d.min(), 0),
                  "near": CITY_NAMES[c][int(by_city.min(axis=1).argmin())],
                  "cities": sorted(({"name": n, "t": first(x <= RADIUS_KM)} for n, x in zip(CITY_NAMES[c], by_city)
                                    if (x <= RADIUS_KM).any()), key=lambda v: v["t"])}
    return out


def latest_forecasts():
    """일본 기상청 5일 예보(가장 최근 발표)를 태풍 이름별로. 예보 중심 위치와 70% 확률 반경(km)."""
    from .sources import jma
    rows = []
    for p in sorted(jma.XML_DIR.glob("*.xml")):
        try:
            rows += jma.parse_report(p)
        except Exception:
            continue
    if not rows:
        return {}
    d = pd.DataFrame(rows)
    out = {}
    for name, g in d.groupby("name"):
        g = g[g.report_time == g.report_time.max()].sort_values("lead_h")
        if (g.info_type == "取消").any():          # 가장 최근 발표가 취소면 그 전 예보는 폐기
            continue
        fc = g[g.lead_h > 0]
        if fc.empty:
            continue
        issued = pd.to_datetime(g.report_time.iloc[0], utc=True).strftime("%Y-%m-%dT%H")
        out[str(name).upper()] = {"issued": issued, "points": [
            [int(r.lead_h), num(r.lat, 1), num(r.lon, 1), num(r.radius70_km, 0), num(r.pressure_hpa, 0),
             num(r.max_wind_ms, 0)] for r in fc.itertuples()]}
        # 실황(0시간) 위치에서 시작하는 예보 진로로 한국·일본 영향권 접근 판정(시각은 UTC)
        path = [(pd.to_datetime(r.valid_time, utc=True).tz_localize(None), r.lat, r.lon, (r.radius70_km if r.lead_h and not pd.isna(r.radius70_km) else 0))
                for r in g.itertuples() if r.lat is not None and not pd.isna(r.lat) and r.valid_time]
        if len(path) >= 2 and path[0][0] < path[-1][0]:
            out[str(name).upper()]["approach"] = approach(path)
    return out


def export_recent(df, models, live=False):
    last_time = df.time.max()
    sids = df[df.time >= last_time - pd.Timedelta(days=RECENT_DAYS)].SID.unique()
    storms = []
    for sid in sids:
        g = predict_rows(df[df.SID == sid], models)
        last = g.iloc[-1]
        s = {"sid": sid, "name": str(g.NAME.iloc[0]), "grade": num(last.TOKYO_GRADE, 0), "start": g.time.min().strftime("%Y-%m-%dT%H"),
             "end": last.time.strftime("%Y-%m-%dT%H"), "track": track(g, ["p_korea", "p_japan"]),
             "env": src if isinstance(src := last.get("env_src"), str) else None}  # 마지막 예측에 쓴 상공 자료: gfs·ncep, 없으면 경로만
        for c in models:
            hit = g[g[f"in_{c}"]]
            if len(hit):
                s[c] = {"arrived": hit.time.min().strftime("%Y-%m-%dT%H")}
            else:
                s[c] = {"p": num(last[f"p_{c}"], 3), "hours": num(last[f"h_{c}"], 0),
                        "eta": (last.time + pd.Timedelta(hours=float(last[f"h_{c}"]))).strftime("%Y-%m-%dT%H")}
                if not pd.isna(last[f"lo_{c}"]):          # 남은 시간 범위(목표 80%, 검증 포함 비율 74~79%)
                    h = last[f"h_{c}"]                    # 표시용: 범위가 점 예측을 늘 감싸게
                    s[c]["range"] = [num(max(min(last[f"lo_{c}"], h), 0), 0), num(max(last[f"hi_{c}"], h), 0)]
        storms.append(s)
    storms.sort(key=lambda s: s["end"], reverse=True)
    if live:
        fc = latest_forecasts()
        for s in storms:
            if s["end"] == last_time.strftime("%Y-%m-%dT%H") and s["name"].upper() in fc:
                s["forecast"] = fc[s["name"].upper()]
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
            "data_until": last_time.strftime("%Y-%m-%dT%H"), "live": live, "storms": storms}


def export_history(df, models):
    """검증 기간(학습에 안 쓴 2015년~) 중 한국 영향 태풍: 시점별 모델 확률과 실제 도착."""
    d = add_targets(df[df.SEASON >= SPLIT_YEAR], "korea")
    hits = d[d.arrival.notna()].SID.unique()
    out = []
    for sid in hits:
        full = df[df.SID == sid]
        g = predict_rows(full, models)
        arrival = full[full.in_korea].time.min()
        japan = full[full.in_japan].time.min()
        g = g[g.time <= arrival + pd.Timedelta(hours=48)]
        out.append({"sid": sid, "name": str(full.NAME.iloc[0]), "season": int(full.SEASON.iloc[0]),
                    "arrival": arrival.strftime("%Y-%m-%dT%H"), "arrival_japan": None if pd.isna(japan) else japan.strftime("%Y-%m-%dT%H"),
                    "track": track(g, ["p_korea", "p_japan"])})
    out.sort(key=lambda s: s["arrival"], reverse=True)
    return out


def reliability(df, bins=(0, .1, .2, .3, .4, .5, .6, .8, 1.0001), min_n=20):
    """검증 기간(2015~) 시점별 서비스 방식 예측 확률을 구간으로 묶어 [평균 예측, 실제 비율, 시점 수]."""
    out = {}
    for c in ["korea", "japan"]:
        d = add_targets(df[df.SEASON >= SPLIT_YEAR], c)
        p, _ = predict(c, d)
        t = pd.DataFrame({"p": p, "y": d.hit.to_numpy()})
        g = t.groupby(pd.cut(t.p, bins, right=False), observed=True).agg(pred=("p", "mean"), obs=("y", "mean"),
                                                                           n=("y", "size"))
        out[c] = [[round(float(r.pred), 3), round(float(r.obs), 3), int(r.n)] for r in g.itertuples() if r.n >= min_n]
    return out


def export_stats(df):
    years = range(int(df.SEASON.min()), int(df.SEASON.max()) + 1)
    out = {"metrics": json.loads((ROOT / "reports" / "metrics.json").read_text()),
           "analysis": json.loads((ROOT / "reports" / "analysis.json").read_text()),
           "reliability": reliability(df)}
    for c in ["korea", "japan"]:
        hit = df[df[f"in_{c}"]].groupby("SID").time.min()
        by_month = hit.dt.month.value_counts().reindex(range(1, 13), fill_value=0)
        by_year = df[df.SID.isin(hit.index)].groupby("SID").SEASON.first().value_counts()
        out[f"{c}_by_month"] = [int(v) for v in by_month.values]
        out[f"{c}_by_year"] = {str(y): int(by_year.get(y, 0)) for y in years}
    return out


def refresh_sources(atmos_timeout=900):
    """최신 원자료 갱신. 어느 한 곳이 느리거나 죽어도 갱신 전체가 멈추지 않고 기존 자료로 이어간다."""
    import socket
    import subprocess
    import sys
    import warnings
    socket.setdefaulttimeout(120)
    try:
        download()
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"IBTrACS 갱신 실패, 기존 자료 사용: {type(e).__name__}", RuntimeWarning)
    import os
    import signal
    # NCEP 재분석은 2026-03-17에서 멈췄다. 하위 프로세스가 서버를 확인해 새로 채울 시점이 있을 때만 받는다(그 뒤는 env_gfs 가 채움)
    # 하위 프로세스(병렬 받기 작업자 포함)를 한 그룹으로 묶어, 제한시간이 지나면 모두 끝낸다
    proc = subprocess.Popen([sys.executable, "-m", "typhoon.env_atmos", "--recent"], cwd=ROOT,
                            start_new_session=True)
    try:
        if proc.wait(timeout=atmos_timeout) != 0:
            warnings.warn("NOAA 재분석 갱신 실패, 기존 특성 사용: 비정상 종료", RuntimeWarning)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        warnings.warn("NOAA 재분석 갱신 시간 초과, 기존 특성 사용", RuntimeWarning)


def log_forecasts(latest):
    """기록은 부가 기능이다. 실패해도 이미 만든 사이트 자료는 커밋되어야 한다."""
    import warnings
    try:
        from .forecast_log import append
        print(f"[forecast_log] 새 기록 {append(latest)}줄")
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"예보 기록 실패: {type(e).__name__}: {e}", RuntimeWarning)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    if ap.parse_args().update:
        refresh_sources()
    OUT.mkdir(parents=True, exist_ok=True)
    base = load()
    full = realtime.merge(base)
    live = len(full) > len(base)
    df = add_env(add_features(full), gfs=True)
    models = ["korea", "japan"]
    export_models()
    latest = export_recent(df, models, live)
    latest["feed_ok"] = realtime.FEED_OK
    for name, obj in [("latest", latest), ("history", export_history(df, models)), ("stats", export_stats(df))]:
        (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    if live:
        # 예보 전문은 7일치만 남으므로 접근 판정을 쌓아 두고 나중에 검증(forecast_log)
        log_forecasts(latest)


if __name__ == "__main__":
    main()
