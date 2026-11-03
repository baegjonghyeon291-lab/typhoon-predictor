"""GFS 예보장으로 태풍 중심 경로를 추적해 경로·전향 모델 입력으로 쓰는 실험 (PRD P0-1).

GFS 1.0° 예보 파일(AWS noaa-gfs-bdp-pds, 키 없음)의 첫 메시지인 해면기압(PRMSL)만 앞부분 바이트로 받아
0~55N, 95~185E를 data/env/gfs_fc/{발표시각}.npz 로 캐시한다(00·12시 발표, 0~132시간 6시간 간격).
발표 시각의 실제 위치에서 시작해 6시간마다 해면기압 최저점을 따라가 GFS가 예측한 중심 경로를 만든다.

시점 t의 특성은 그때 이미 공개된 발표(t보다 6시간 이상 앞선 00·12시)만 쓴다. 공개는 발표 약 4시간 뒤다.

    python -m typhoon.gfs_track fetch        # 2021-06~ 태풍이 있던 발표 받기(오래 걸림, 이어받기 됨)
    python -m typhoon.gfs_track track        # 중심 추적 → data/env/gfs_fc/tracks.parquet
    python -m typhoon.gfs_track experiment   # 경로·전향 비교 → reports/gfs_track_experiment.txt
"""
import argparse
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .data import ROOT, haversine

DIR = ROOT / "data" / "env" / "gfs_fc"
TRACKS = DIR / "tracks.parquet"
REPORT = ROOT / "reports" / "gfs_track_experiment.txt"
URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{c:%Y%m%d}/{c:%H}/atmos/gfs.t{c:%H}z.pgrb2.1p00.f{f:03d}"
FIRST = pd.Timestamp("2021-06-01")
LEADS = list(range(0, 133, 6))            # 발표 뒤 0~132시간
LAT = np.arange(0, 56, 1.0)               # 오름차순
LON = np.arange(95, 186, 1.0)
AVAIL_H = 6                               # 시점 t에는 t-6시간 이전 발표만 쓴다(공개 약 4시간 뒤)
_ECC = threading.Lock()


# ---------------------------------------------------------------- 받기
def cycle_for(t):
    """시점 t에 쓸 수 있는 가장 최근 00·12시 발표."""
    return (pd.Timestamp(t) - pd.Timedelta(hours=AVAIL_H)).floor("12h")


def _decode(msg):
    import eccodes
    with _ECC:
        h = eccodes.codes_new_from_message(msg)
        try:
            name = eccodes.codes_get(h, "shortName")
            ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
            v = eccodes.codes_get_values(h).reshape(nj, ni)
        finally:
            eccodes.codes_release(h)
    assert name == "prmsl" and (ni, nj) == (360, 181), (name, ni, nj)
    y0 = int(90 - LAT[-1])                        # 위도 90→-90
    return v[y0:y0 + len(LAT), int(LON[0]):int(LON[0]) + len(LON)][::-1].astype(np.float32) / 100   # hPa


def _fetch(sess, c, f):
    """첫 GRIB 메시지(PRMSL)를 앞부분 바이트로 받는다. 없으면 None."""
    url = URL.format(c=c, f=f)
    r = sess.get(url, headers={"Range": "bytes=0-299999"}, timeout=60)
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    b = r.content
    assert b[:4] == b"GRIB", url
    n = struct.unpack(">Q", b[8:16])[0]          # GRIB2 전체 길이
    if n > len(b):
        b = sess.get(url, headers={"Range": f"bytes=0-{n - 1}"}, timeout=60).content
    return _decode(b[:n])


def _path(c):
    return DIR / f"{c:%Y%m%d%H}.npz"


def fetch_cycle(sess, c, retries=3):
    p = _path(c)
    if p.exists():
        return "cached"
    out = np.full((len(LEADS), len(LAT), len(LON)), np.nan, np.float32)
    for i, f in enumerate(LEADS):
        for k in range(retries):
            try:
                v = _fetch(sess, c, f)
                break
            except (requests.RequestException, OSError):
                if k == retries - 1:
                    return "failed"
                time.sleep(2 * (k + 1))
        if v is None:
            if f == 0:
                return "missing"
            continue
        out[i] = v
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, p=out)
    tmp.replace(p)
    return "ok"


def needed_cycles(df):
    d = df[df.time >= FIRST + pd.Timedelta(hours=12)]
    return sorted({cycle_for(t) for t in d.time.unique()})


def fetch_all(df, workers=12):
    cycles = needed_cycles(df)
    todo = [c for c in cycles if not _path(c).exists()]
    print(f"[gfs_track] 발표 {len(cycles)}개 중 받을 것 {len(todo)}개", flush=True)
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=workers * 2))
    stat = {}
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_cycle, sess, c): c for c in todo}
        for i, f in enumerate(as_completed(futs), 1):
            s = f.result()
            stat[s] = stat.get(s, 0) + 1
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} {stat}", flush=True)
    return stat


# ---------------------------------------------------------------- 중심 추적
SEARCH_KM = 330        # 6시간 이동 한계(약 55km/h)
RING_KM = (300, 500)   # 닫힌 저기압 확인용 고리
MIN_DEPTH = 1.0        # 고리 평균보다 이만큼(hPa) 낮아야 중심으로 본다


def _grid_dist(lat, lon):
    return haversine(LAT[:, None], LON[None, :], lat, lon)


def _find(field, lat, lon):
    """(lat, lon) 근처 해면기압 최저점(포물선 보정). 닫힌 저기압이 아니면 None."""
    d = _grid_dist(lat, lon)
    win = np.where(d <= SEARCH_KM, field, np.nan)
    if np.isnan(win).all():
        return None
    iy, ix = np.unravel_index(np.nanargmin(win), win.shape)
    if d[iy, ix] > SEARCH_KM - 60 or not (0 < iy < len(LAT) - 1 and 0 < ix < len(LON) - 1):
        return None                               # 창 가장자리 = 따라가던 중심이 아님
    pmin = field[iy, ix]
    ring = field[(_grid_dist(LAT[iy], LON[ix]) >= RING_KM[0]) & (_grid_dist(LAT[iy], LON[ix]) <= RING_KM[1])]
    if np.isnan(pmin) or np.nanmean(ring) - pmin < MIN_DEPTH:
        return None

    def sub(a, b, c):                             # 세 점 포물선의 꼭짓점 위치(-0.5~0.5)
        den = a - 2 * b + c
        return 0.0 if den == 0 else float(np.clip(0.5 * (a - c) / den, -.5, .5))
    dy = sub(field[iy - 1, ix], pmin, field[iy + 1, ix])
    dx = sub(field[iy, ix - 1], pmin, field[iy, ix + 1])
    return LAT[iy] + dy, LON[ix] + dx, float(pmin)


def track_cycle(P, lat0, lon0, leads=None):
    """발표 하나(P[리드, y, x])에서 (lat0, lon0)부터 중심 추적 → [(리드, lat, lon, pmin)]. 놓치면 거기서 끝."""
    out, prev = [], None
    lat, lon = lat0, lon0
    for i, f in enumerate(leads or LEADS):
        if np.isnan(P[i]).all():
            break
        g = (lat, lon) if prev is None else (2 * lat - prev[0], 2 * lon - prev[1])   # 직선 연장 첫 추정
        hit = _find(P[i], *g) or (_find(P[i], lat, lon) if prev is not None else None)
        if hit is None:
            break
        if f == 0 and haversine(hit[0], hit[1], lat0, lon0) > 250:
            break                                 # 발표 시각에 실제 위치 근처에 중심이 없음
        prev = (lat, lon) if f else (hit[0], hit[1])
        lat, lon = hit[0], hit[1]
        out.append((f, lat, lon, hit[2]))
    return out


def track_all(df):
    """태풍·발표마다 GFS 중심 경로. 발표 시각에 실제 관측이 있는 태풍만."""
    rows = []
    obs = df[df.time >= FIRST][["SID", "time", "LAT", "LON"]]
    for c, g in obs.groupby("time"):
        if c.hour % 12 or not _path(c).exists():
            continue
        P = np.load(_path(c))["p"]
        for r in g.itertuples():
            lon = r.LON % 360
            if not (LON[0] + 3 <= lon <= LON[-1] - 3 and LAT[0] + 3 <= r.LAT <= LAT[-1] - 3):
                continue
            for f, la, lo, pm in track_cycle(P, r.LAT, lon):
                rows.append((r.SID, c, f, la, lo, pm))
    t = pd.DataFrame(rows, columns=["SID", "cycle", "lead", "lat", "lon", "pmin"])
    t.to_parquet(TRACKS, index=False)
    print(f"[gfs_track] 태풍 {t.SID.nunique()}개, 발표 {t.groupby(['SID', 'cycle']).ngroups}개 추적 → {TRACKS}")
    return t


# ---------------------------------------------------------------- 실시간 자체 경로
OWN_H = list(range(12, 121, 12))


def own_track(g, sess=None):
    """활동 중인 태풍(g: 한 태풍의 시점 표, 시간순)의 자체 경로.
    마지막 관측 시각 t에 공개된 발표를 받아 발표 시각 실제 위치부터 중심을 추적하고,
    GFS 이동량(지금 → h시간 뒤)을 마지막 관측 위치에 더한다(실험의 'GFS 그대로', 섞는 비율 5~20%는 생략).
    → {"issued", "cycle", "points": [[h, lat, lon]], "turn48"} 또는 None(발표 없음·추적 실패)."""
    from .features import bearing
    last = g.iloc[-1]
    t, c = last.time, cycle_for(last.time)
    at_c = g[g.time == c]
    if at_c.empty:
        return None
    if not _path(c).exists() and fetch_cycle(sess or requests.Session(), c) not in ("ok", "cached"):
        return None
    tr = track_cycle(np.load(_path(c))["p"], float(at_c.LAT.iloc[0]), float(at_c.LON.iloc[0]) % 360)
    if len(tr) < 2:
        return None
    leads = np.array([x[0] for x in tr], float)
    la, lo = np.array([x[1] for x in tr]), np.array([x[2] for x in tr])
    d = (t - c) / pd.Timedelta(hours=1)
    if d > leads[-1]:
        return None
    pos = lambda L: (np.interp(L, leads, la), np.interp(L, leads, lo))     # noqa: E731
    y0, x0 = pos(d)
    pts = [[h, round(float(last.LAT + pos(d + h)[0] - y0), 2), round(float(last.LON + pos(d + h)[1] - x0), 2)]
           for h in OWN_H if d + h <= leads[-1]]
    if not pts:
        return None
    turn = None
    prev = g[g.time == t - pd.Timedelta(hours=24)]
    p48 = [q for q in pts if q[0] == 48]
    if len(prev) and p48:
        h_now = bearing(prev.LAT.iloc[0], prev.LON.iloc[0], last.LAT, last.LON)
        h_fut = bearing(last.LAT, last.LON, p48[0][1], p48[0][2])
        turn = round(float((h_fut - h_now + 180) % 360 - 180))     # +: 시계 방향(오른쪽)으로 꺾임
    return {"issued": t.strftime("%Y-%m-%dT%H"), "cycle": c.strftime("%Y-%m-%dT%H"), "points": pts, "turn48": turn}


# ---------------------------------------------------------------- 특성
GFS_H = [24, 48, 72, 96, 120]


def add_gfs(df, tracks=None):
    """각 시점에 쓸 수 있는 발표의 GFS 예측 이동량 gfs_dy_{h}, gfs_dx_{h} (도, 지금 GFS 위치 기준)."""
    tracks = pd.read_parquet(TRACKS) if tracks is None else tracks
    pos = tracks.set_index(["SID", "cycle", "lead"])[["lat", "lon"]]
    d = df.copy()
    d["gfs_cycle"] = (d.time - pd.Timedelta(hours=AVAIL_H)).dt.floor("12h")
    d["gfs_age"] = (d.time - d.gfs_cycle) / pd.Timedelta(hours=1)
    def at(h):
        idx = pd.MultiIndex.from_arrays([d.SID, d.gfs_cycle, (d.gfs_age + h).astype(int)])
        return pos.reindex(idx).to_numpy()
    now = at(0)
    for h in GFS_H:
        fut = at(h)
        d[f"gfs_dy_{h}"] = fut[:, 0] - now[:, 0]
        d[f"gfs_dx_{h}"] = (fut[:, 1] - now[:, 1] + 180) % 360 - 180
    return d.drop(columns=["gfs_cycle"])


GFS_FEATURES = ["gfs_age", *[f"gfs_{a}_{h}" for h in GFS_H for a in ("dy", "dx")]]


# ---------------------------------------------------------------- 실험
def experiment(split="2024-01-01"):
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.metrics import roc_auc_score
    from .data import load
    from .features import ENV_FEATURES, FEATURES, add_env, add_features, bearing
    from .track import (LEADS as TL, TURN_DEG, TURN_LEAD, add_future, angle_diff, error_km, fit_track, persistence,
                        predict_track, turn_table)
    from .train import CLF_PARAMS, REG_PARAMS, SPLIT_YEAR

    df = add_gfs(add_env(add_features(load()), gfs=True))
    feats = [*FEATURES, *[c for c in ENV_FEATURES if c in df]]
    old = df[df.SEASON < SPLIT_YEAR]
    split = pd.Timestamp(split)
    sids = df.groupby("SID").time.min()
    rec = df[df.SID.isin(sids[sids >= FIRST + pd.Timedelta(days=1)].index)]
    fit_p = rec[rec.SID.isin(sids[sids < split].index)]           # GFS 보정 맞추기(2021-06~2023)
    ev = rec[rec.SID.isin(sids[sids >= split].index)]             # 최종 평가(2024~)
    small = dict(REG_PARAMS, min_samples_leaf=40)
    lines = [f"GFS 예보장 중심 추적 입력 실험 ({pd.Timestamp.now():%Y-%m-%d})",
             f"기존 경로 모델: 1951~{SPLIT_YEAR - 1} 학습. GFS 결합: {fit_p.SID.nunique()}개 태풍(2021-06~{split:%Y-%m}) 학습,"
             f" 평가 {ev.SID.nunique()}개 태풍({split:%Y-%m}~).", "",
             "A. GFS 경로가 있는 시점만",
             "위치 오차(km)  시간  시점 수  단순연장  기존모델  GFS그대로  GFS결합(트리)  섞기(k)  (중앙값: 기존 / 섞기)"]
    full = ["", "B. 평가 기간 전체 시점(GFS 경로가 없으면 기존 모델)",
            "위치 오차(km)  시간  시점 수  GFS 있음  기존모델  섞기+대체"]
    blend_models = {}
    for h in TL:
        base = fit_track(old, feats, h)
        g = [f"gfs_dy_{h}", f"gfs_dx_{h}"]
        def prep(x):
            t = add_future(x, h).dropna(subset=["dy", "dx", *g])
            by, bx = predict_track(base, t, feats, h)
            t = t.assign(base_dy=by, base_dx=bx)
            return t
        tf_all, te_all = add_future(fit_p, h).dropna(subset=["dy", "dx"]), add_future(ev, h).dropna(subset=["dy", "dx"])
        tf, te = prep(fit_p), prep(ev)
        # 섞기: 위치 = GFS + k·(기존 − GFS), k는 맞추기 기간에서 평균 오차가 가장 작은 값
        ks = np.linspace(0, 1, 21)
        k = ks[int(np.argmin([error_km(tf, tf[g[0]] + q * (tf.base_dy - tf[g[0]]), tf[g[1]] + q * (tf.base_dx - tf[g[1]])).mean()
                              for q in ks]))]
        cols = ["base_dy", "base_dx", *g, "LAT", "LON", "doy", "speed_kmh", "heading", "gfs_age"]
        my = HistGradientBoostingRegressor(**small).fit(tf[cols], tf.dy - tf[g[0]])
        mx = HistGradientBoostingRegressor(**small).fit(tf[cols], tf.dx - tf[g[1]])
        blend_models[h] = (my, mx, cols)
        e_per = error_km(te, *persistence(te, h))
        e_base = error_km(te, te.base_dy, te.base_dx)
        e_gfs = error_km(te, te[g[0]], te[g[1]])
        e_bl = error_km(te, te[g[0]] + my.predict(te[cols]), te[g[1]] + mx.predict(te[cols]))
        e_k = error_km(te, te[g[0]] + k * (te.base_dy - te[g[0]]), te[g[1]] + k * (te.base_dx - te[g[1]]))
        lines.append(f"{'':12}{h:>4}h {len(te):>7} {e_per.mean():>9.0f} {e_base.mean():>8.0f} {e_gfs.mean():>9.0f}"
                     f" {e_bl.mean():>12.0f} {e_k.mean():>8.0f}(k={k:.2f})   ({e_base.median():.0f} / {e_k.median():.0f})")
        by, bx = predict_track(base, te_all, feats, h)
        has = te_all[g[0]].notna().to_numpy()
        py = np.where(has, te_all[g[0]] + k * (by - te_all[g[0]]), by)
        px = np.where(has, te_all[g[1]] + k * (bx - te_all[g[1]]), bx)
        full.append(f"{'':12}{h:>4}h {len(te_all):>7} {has.mean():>8.0%} {error_km(te_all, by, bx).mean():>9.0f}"
                    f" {error_km(te_all, py, px).mean():>10.0f}")

    lines += full
    # 전향(48시간 45도): 같은 평가 시점에서 기존 분류 vs GFS 경로 방향 vs GFS 결합 분류
    tr_old = turn_table(old)
    clf_old = HistGradientBoostingClassifier(**CLF_PARAMS).fit(tr_old[feats], tr_old.turn)
    gcols = ["gfs_dy_48", "gfs_dx_48"]
    tf, te = turn_table(fit_p).dropna(subset=gcols), turn_table(ev).dropna(subset=gcols)
    for t in (tf, te):
        t["p_old"] = clf_old.predict_proba(t[feats])[:, 1]
        t["gfs_turn"] = angle_diff(bearing(t.LAT, t.LON, t.LAT + t.gfs_dy_48, t.LON + t.gfs_dx_48), t.head_now)
    ccols = ["p_old", "gfs_turn", *gcols, "LAT", "LON", "doy", "speed_kmh", "heading"]
    clf_b = HistGradientBoostingClassifier(**dict(CLF_PARAMS, min_samples_leaf=40)).fit(tf[ccols], tf.turn)
    te["p_bl"] = clf_b.predict_proba(te[ccols])[:, 1]
    y = te.turn.to_numpy()
    lines += ["", f"{TURN_LEAD}시간 전향({TURN_DEG}도 이상), 평가 시점 {len(te)}개, 실제 전향 {y.mean():.1%}",
              "방법            AUC    맞힘   예측 중 실제"]
    for name, score, th in [("기존 분류", te.p_old, .5), ("GFS 경로 방향", te.gfs_turn / 180, TURN_DEG / 180),
                            ("GFS 결합 분류", te.p_bl, .5)]:
        w = (score >= th).to_numpy()
        lines.append(f"{name:<14} {roc_auc_score(y, score):.3f}  {w[y == 1].mean():>5.1%}  {y[w].mean() if w.any() else float('nan'):>8.1%}")

    # 참피(2026): 시점별 전향 확률
    ch = ev[ev.NAME == "CHAMPI"]
    if len(ch):
        c = ch.dropna(subset=gcols).copy()
        if len(c):
            c["p_old"] = clf_old.predict_proba(c[feats])[:, 1]
            hn = bearing(c.LAT - c.dlat_24, c.LON - c.dlon_24, c.LAT, c.LON)
            c["gfs_turn"] = angle_diff(bearing(c.LAT, c.LON, c.LAT + c.gfs_dy_48, c.LON + c.gfs_dx_48), hn)
            c["p_bl"] = clf_b.predict_proba(c[ccols])[:, 1]
            lines += ["", "참피(2026) 시각(KST)       기존 분류  GFS 48h 방향 변화  GFS 결합 분류"]
            for r in c[(c.time >= "2026-10-18") & (c.time <= "2026-10-24")].itertuples():
                lines.append(f"  {(r.time + pd.Timedelta(hours=9)):%m-%d %H시}         {r.p_old:>5.0%}       {r.gfs_turn:>5.0f}°"
                             f"            {r.p_bl:>5.0%}")
    text = "\n".join(lines)
    REPORT.write_text(text + "\n", encoding="utf-8")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "track", "experiment"])
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    from .data import load
    if a.cmd == "fetch":
        fetch_all(load(), a.workers)
    elif a.cmd == "track":
        track_all(load())
    else:
        experiment()


if __name__ == "__main__":
    sys.exit(main())
