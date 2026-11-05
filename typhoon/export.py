"""웹사이트(docs/)용 JSON 생성: 최근 태풍 예측, 지난 태풍 기록, 모델 지표."""
import argparse
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .data import load, download, haversine, ROOT, CITIES, CITY_NAMES, RADIUS_KM
from . import realtime
from .explain import safe_contributions
from .features import add_features, add_env, bearing, predict, predict_range, ENV_FEATURES, FEATURES
from .track import MIN_MOVE_KM
from .train import SPLIT_YEAR
from .turn import predict_turn

OUT = ROOT / "docs" / "data"
RECENT_DAYS = 21
NEAR_KM = 300          # 지난 태풍 목록 기준. 모델의 영향권(RADIUS_KM)과 별개
ACTIVE_H = 18          # 마지막 관측이 이 안이면 아직 활동 중으로 본다(사이트 QUIET_H 와 같은 값)


def num(v, d=2):
    return None if pd.isna(v) else round(float(v), d)


def predict_rows(d, models):
    d = d.copy()
    for c in models:
        d[f"p_{c}"], d[f"h_{c}"] = predict(c, d)
        d[f"lo_{c}"], d[f"hi_{c}"] = predict_range(c, d)
    d["p_turn"] = predict_turn(d)
    return d


USA_TO_10MIN = 0.88      # 미국 1분 평균 풍속 → 10분 평균 근사(WMO 권고 환산)


def display_wind(g):
    """표시용 풍속(kt): 태풍 전체에 일본 기상청(10분 평균) 풍속이 하나도 없을 때만 미국 1분 평균 × 0.88.
    일본 값이 있는 태풍에 점 단위로 섞으면 일본 자료 밖 구간 때문에 최대 풍속과 단계가 올라간다.
    모델 입력(wind)은 바꾸지 않는다. → (풍속 Series, 미국 환산을 썼는지)."""
    usa = g["USA_WIND"] * USA_TO_10MIN if "USA_WIND" in g else pd.Series(np.nan, index=g.index)
    used = bool(g.wind.isna().all() and usa.notna().any())
    return (usa if used else g.wind), used


def track(g, extra=()):
    w, _ = display_wind(g)
    return [[t.strftime("%Y-%m-%dT%H"), num(la), num(lo), num(ww, 0), num(p, 0), *[num(r[e], 3) for e in extra]]
            for t, la, lo, ww, p, (_, r) in zip(g.time, g.LAT, g.LON, w, g.pres, g.iterrows())]


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


def us_forecast(w):
    """JTWC 예보(sources.jtwc.latest)를 사이트 형식으로. 영향권 판정은 일본 예보와 같은 계산, 오차 범위는 없다."""
    path = [(w["time"], w["lat"], w["lon"], 0)]
    path += [(w["time"] + pd.Timedelta(hours=h), la, lo, 0) for h, la, lo, _ in w["points"]]
    return {"issued": w["issued"].strftime("%Y-%m-%dT%H"), "number": w["number"], "wind": w["wind"], "points": w["points"],
            "approach": approach(path)}


def kr_forecast(w):
    """기상청 예보(sources.kma.latest)를 사이트 형식으로. 영향권 판정은 일본·미국 예보와 같은 계산."""
    path = [(w["time"], w["lat"], w["lon"], 0)]
    path += [(w["time"] + pd.Timedelta(hours=h), la, lo, 0) for h, la, lo, _ in w["points"]]
    return {"issued": w["issued"].strftime("%Y-%m-%dT%H"), "seq": w["seq"], "points": w["points"], "approach": approach(path)}


def direction(points, base, g):
    """예보 경로의 방향 변화(지난 24시간 진행 방향 기준, 도. +: 오른쪽). 전향 모델·앙상블과 같은 정의로
    turn48: 지금 → 48시간 뒤, turn96: 48 → 96시간 뒤. 예보가 짧거나 200km 미만 움직이면 None.
    points: [[발표 base 기준 시간, 위도, 경도, ...]]."""
    last = g.iloc[-1]
    prev = g[g.time == last.time - pd.Timedelta(hours=24)]
    points = [p for p in points if p[1] is not None and p[2] is not None]
    if prev.empty or not points:
        return {}
    now = bearing(prev.LAT.iloc[0], prev.LON.iloc[0], last.LAT, last.LON)
    off = (last.time - pd.Timestamp(base)) / pd.Timedelta(hours=1)
    hh = [p[0] for p in points]
    at = lambda h: (float(np.interp(h + off, hh, [p[1] for p in points])), float(np.interp(h + off, hh, [p[2] for p in points])))  # noqa: E731

    def change(a, b):
        return round(float((bearing(*a, *b) - now + 180) % 360 - 180)) if haversine(*a, *b) >= MIN_MOVE_KM else None
    out = {}
    if hh[-1] >= 48 + off:
        out["turn48"] = change((float(last.LAT), float(last.LON)), at(48))
    if hh[-1] >= 96 + off:
        out["turn96"] = change(at(48), at(96))
    return out


def safe_own_track(g):
    """GFS 추적 자체 경로(gfs_track.own_track)에 영향권 판정을 붙인다. 실패해도 갱신은 이어 간다."""
    try:
        from .gfs_track import own_track
        o = own_track(g)
    except Exception as e:  # noqa: BLE001
        print(f"[gfs_track] 자체 경로 실패: {type(e).__name__}: {e}")
        return None
    if not o:
        return None
    t0 = pd.Timestamp(o["issued"])
    last = g.iloc[-1]
    o["approach"] = approach([(t0, last.LAT, last.LON, 0)] + [(t0 + pd.Timedelta(hours=h), la, lo, 0) for h, la, lo in o["points"]])
    o.update(direction(o["points"], t0, g))
    return o


def export_recent(df, models, live=False):
    last_time = df.time.max()
    sids = df[df.time >= last_time - pd.Timedelta(days=RECENT_DAYS)].SID.unique()
    storms = []
    for sid in sids:
        g = predict_rows(df[df.SID == sid], models)
        last = g.iloc[-1]
        s = {"sid": sid, "name": str(g.NAME.iloc[0]), "grade": num(last.TOKYO_GRADE, 0), "start": g.time.min().strftime("%Y-%m-%dT%H"),
             "end": last.time.strftime("%Y-%m-%dT%H"), "track": track(g, ["p_korea", "p_japan", "p_turn"]),
             "wind_usa": display_wind(g)[1] or None,
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
                if live and last.time == last_time:       # 활동 중인 태풍만 근거 계산(기록에도 함께 남는다)
                    s[c]["why"] = safe_contributions(c, g.iloc[[-1]])[0]
        if not pd.isna(last.p_turn):
            s["turn"] = {"p": num(last.p_turn, 3)}
            if live and last.time == last_time:           # 지금 전향 확률을 올리거나 내린 요인
                s["turn"]["why"] = safe_contributions("turn", g.iloc[[-1]])[0]
        if live and last.time == last_time:
            s["inputs"] = {k: num(last[k], 3) for k in [*FEATURES, *ENV_FEATURES] if k in g}
        storms.append(s)
    storms.sort(key=lambda s: s["end"], reverse=True)
    from .sources import jtwc
    us = jtwc.latest()         # 실시간 자료가 끊겨도 활동 중인 태풍을 확인할 수 있게 항상 받는다
    print(f"[jtwc] 활동 중 예보 {len(us)}개")
    from .sources import kma
    try:
        kr = kma.latest() if live else {}
    except Exception as ex:  # noqa: BLE001
        print(f"[kma] 실패: {type(ex).__name__}")
        kr = {}
    print(f"[kma] 활동 중 예보 {len(kr)}개")
    if live:
        fc = latest_forecasts()
        for s in storms:
            if s["end"] != last_time.strftime("%Y-%m-%dT%H"):
                continue
            gs = df[df.SID == s["sid"]].sort_values("time")
            if s["name"].upper() in fc:
                s["forecast"] = fc[s["name"].upper()]
                s["forecast"].update(direction(s["forecast"]["points"], s["forecast"]["issued"], gs))
            if s["name"].upper() in us:
                w = us[s["name"].upper()]
                s["forecast_us"] = {**us_forecast(w), **direction(w["points"], w["time"], gs)}
            if s["name"].upper() in kr:
                w = kr[s["name"].upper()]
                s["forecast_kr"] = {**kr_forecast(w), **direction(w["points"], w["time"], gs)}
            own = safe_own_track(gs)
            if own:
                s["track_own"] = own
                try:                                    # GEFS 앙상블 전향 확률(2~4일 뒤까지)
                    from .gefs import live_turn
                    turn = live_turn(gs)
                    if turn:
                        own["ens"] = turn
                except Exception as ex:  # noqa: BLE001
                    print(f"[gefs] 전향 확률 실패: {type(ex).__name__}: {ex}")
        print(f"[gfs_track] 자체 경로 {sum('track_own' in s for s in storms)}개")
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
            "data_until": last_time.strftime("%Y-%m-%dT%H"), "live": live, "us_active": sorted(us), "storms": storms}


def still_active(full, now=None):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return full.time.max() >= now - pd.Timedelta(hours=ACTIVE_H)


def near_storms(df):
    """검증 기간(학습에 안 쓴 2015년~) 중 한국 도시에 NEAR_KM 안으로 접근한 이름 붙은 태풍."""
    d = df[(df.SEASON >= SPLIT_YEAR) & ~df.NAME.isin(["UNNAMED", "NOT_NAMED"])]
    closest = d.groupby("SID").dist_korea.min()
    return closest[closest <= NEAR_KM].index


def export_history(df, models):
    """지난 태풍: 시점별 모델 확률, 한국 영향권 도착(없으면 null), 최근접 시각과 거리."""
    out = []
    for sid in near_storms(df):
        full = df[df.SID == sid]
        g = predict_rows(full, models)
        arrival = full[full.in_korea].time.min()
        japan = full[full.in_japan].time.min()
        if pd.isna(arrival) and still_active(full):    # 아직 접근 중일 수 있어 결과를 말할 수 없다
            continue
        close = full.loc[full.dist_korea.idxmin()]
        ref = close.time if pd.isna(arrival) else arrival
        g = g[g.time <= ref + pd.Timedelta(hours=48)]
        before = g[g.time < ref]
        peak = None
        if len(before):                                # 도착(최근접) 전 가장 높았던 확률과 그 근거
            top = before.loc[[before.p_korea.idxmax()]]
            peak = {"t": top.time.iloc[0].strftime("%Y-%m-%dT%H"), "p": num(top.p_korea.iloc[0], 3),
                    "why": safe_contributions("korea", top)[0]}
        out.append({"sid": sid, "name": str(full.NAME.iloc[0]), "season": int(full.SEASON.iloc[0]),
                    "arrival": None if pd.isna(arrival) else arrival.strftime("%Y-%m-%dT%H"),
                    "arrival_japan": None if pd.isna(japan) else japan.strftime("%Y-%m-%dT%H"),
                    "closest": {"t": close.time.strftime("%Y-%m-%dT%H"), "km": num(close.dist_korea, 0)}, "peak": peak,
                    "wind_usa": display_wind(g)[1] or None, "track": track(g, ["p_korea", "p_japan", "p_turn"])})
    out.sort(key=lambda s: s["arrival"] or s["closest"]["t"], reverse=True)
    return out


def export_stats():
    return {"metrics": json.loads((ROOT / "reports" / "metrics.json").read_text()),
            "analysis": json.loads((ROOT / "reports" / "analysis.json").read_text())}


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
    latest = export_recent(df, models, live)
    latest["feed_ok"] = realtime.FEED_OK
    # 입력값은 예보 기록 전용이라 사이트 자료에는 넣지 않는다
    site_latest = {**latest, "storms": [{k: v for k, v in s.items() if k != "inputs"} for s in latest["storms"]]}
    for name, obj in [("latest", site_latest), ("history", export_history(df, models)), ("stats", export_stats())]:
        (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    if live:
        # 예보 전문은 7일치만 남으므로 접근 판정을 쌓아 두고 나중에 검증(forecast_log)
        log_forecasts(latest)


if __name__ == "__main__":
    main()
