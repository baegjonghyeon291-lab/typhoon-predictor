"""실시간 대기 환경 특성: NOAA GFS 분석장으로 NCEP 재분석의 빈 곳을 채운다.

NCEP 재분석(env_atmos)은 2026-03-17에서 멈췄다. GFS 분석장(f000)은 AWS 공개 버킷에 키 없이
분석 시각 약 4시간 뒤 올라오므로, 지금 활동 중인 태풍에도 환경 모델을 쓸 수 있다.
.idx 로 필요한 필드(500hPa 고도, 850/500/200hPa 바람)만 바이트 범위로 받아 eccodes로 해독하고,
00·06·12·18시 평균을 하루 평균으로 data/env/gfs/YYYYMMDD.npz 에 캐시한다(0.5°, 0~60N, 100~180E).
특성 계산은 NCEP와 같은 2.5° 격자로 바꿔 env_atmos.features_from 을 그대로 쓴다.

    python -m typhoon.env_gfs --compare    # NCEP와 겹치는 기간(2021-06~)의 특성 값 비교
"""
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from typhoon.env_atmos import VARS, features_from

ROOT = Path(__file__).resolve().parent.parent
GFS = ROOT / "data" / "env" / "gfs"
BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{d:%Y%m%d}/{h:02d}/atmos/gfs.t{h:02d}z.pgrb2.0p50.f000"
FIRST_DAY = pd.Timestamp("2021-06-01")      # 버킷에 지금 구조(atmos/)로 남아 있는 첫날 부근
CYCLES = (0, 6, 12, 18)
# (변수, 기압면) 순서가 저장 배열 순서. u·v 기압면 순서는 env_atmos.VARS 와 같다
FIELDS = [("HGT", 500)] + [("UGRD", p) for p in VARS["uwnd"]] + [("VGRD", p) for p in VARS["vwnd"]]
RES = 0.5
LAT = np.arange(0, 60 + RES / 2, RES)
LON = np.arange(100, 180 + RES / 2, RES)
STEP = int(round(2.5 / RES))               # 2.5° 격자 한 칸 = 0.5° 5칸
_ECCODES = threading.Lock()                # eccodes 는 스레드 안전하지 않다(받기만 병렬, 해독은 한 번에 하나)


def _ranges(idx_text):
    """idx 본문 → {(변수, 기압면): (시작, 끝)} (분석 필드만)."""
    lines = [l.split(":") for l in idx_text.strip().splitlines()]
    out = {}
    for i, f in enumerate(lines):
        lev = f[4].split()[0] if len(f) >= 6 else ""
        if f[5:6] != ["anl"] or not f[4].endswith(" mb") or not lev.isdigit():
            continue
        key = (f[3], int(lev))
        if key in FIELDS:
            end = int(lines[i + 1][1]) - 1 if i + 1 < len(lines) else ""
            out[key] = (int(f[1]), end)
    return out


def _decode(msg):
    import eccodes
    with _ECCODES:
        h = eccodes.codes_new_from_message(msg)
        try:
            ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
            lat0 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
            vals = eccodes.codes_get_values(h).reshape(nj, ni)
        finally:
            eccodes.codes_release(h)
    assert (ni, nj, lat0) == (720, 361, 90.0), (ni, nj, lat0)
    # 위도 90→-90, 경도 0→359.5 → 0~60N 오름차순, 100~180E
    y0 = int(round((90 - LAT[-1]) / RES))
    x0 = int(round(LON[0] / RES))
    return vals[y0:y0 + len(LAT), x0:x0 + len(LON)][::-1].astype(np.float32)


def _fetch_cycle(sess, day, hour):
    """한 분석 시각의 7개 필드 [필드, y, x]. 아직 없으면 None."""
    url = BASE.format(d=day, h=hour)
    r = sess.get(url + ".idx", timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    rng = _ranges(r.text)
    if len(rng) != len(FIELDS):
        return None
    out = []
    for key in FIELDS:
        a, b = rng[key]
        rr = sess.get(url, headers={"Range": f"bytes={a}-{b}"}, timeout=120)
        rr.raise_for_status()
        out.append(_decode(rr.content))
    return np.stack(out)


def _path(day):
    return GFS / f"{day:%Y%m%d}.npz"


def fetch_day(day, sess=None, retries=3):
    """하루 평균 [필드, y, x]. 4개 분석 시각이 모두 있어야 만들고 캐시한다. 없으면 None."""
    p = _path(day)
    if p.exists():
        return np.load(p)["f"]
    if day < FIRST_DAY:
        return None
    sess = sess or requests.Session()
    cyc = []
    for h in CYCLES:
        for k in range(retries):
            try:
                c = _fetch_cycle(sess, day, h)
                break
            except (requests.RequestException, OSError):
                if k == retries - 1:
                    return None
        if c is None:
            return None
        cyc.append(c)
    f = np.mean(cyc, axis=0).astype(np.float32)
    GFS.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, f=f)
    tmp.replace(p)
    return f


def fetch(days, workers=8):
    """{날짜: 하루 평균} (받지 못한 날은 빠짐)."""
    days = sorted(set(days))
    with requests.Session() as sess, ThreadPoolExecutor(workers) as ex:
        got = list(ex.map(lambda d: fetch_day(d, sess), days))
    return {d: f for d, f in zip(days, got) if f is not None}


def _coarsen(f):
    """0.5° → 2.5°: 각 2.5° 격자점 중심 ±1° (5×5) 평균. 가장자리는 있는 칸만."""
    from typhoon.env_atmos import _box_mean
    return _box_mean(f, STEP // 2)[..., ::STEP, ::STEP]


def features(pts, workers=8):
    """pts(SID, LAT, LON, time)에 GFS 기반 atm_* 를 붙인 DataFrame(같은 인덱스).
    NCEP와 같이 전날 하루 평균을 쓴다."""
    from typhoon.env_atmos import add_changes
    day = pd.to_datetime(pts["time"]).dt.normalize() - pd.Timedelta(days=1)
    got = fetch(day[day >= FIRST_DAY].unique(), workers)
    cols = None
    if got:
        dates = pd.DatetimeIndex(sorted(got))
        arr = _coarsen(np.stack([got[d] for d in dates]))          # t, 필드, y, x
        n = len(VARS["uwnd"])
        z, u, v = arr[:, 0], arr[:, 1:1 + n], arr[:, 1 + n:]
        o = features_from(dates, LAT[::STEP], LON[::STEP], z, u, v, pts)
        cols = list(o.columns)
    else:
        o = pd.DataFrame(index=pts.index)
    o.insert(0, "SID", pts["SID"])
    o.insert(1, "time", pts["time"])
    if cols:
        o = add_changes(o)
    return o


def fill(df, workers=8):
    """df(add_env 전, atm_* 원값 병합 후)에서 NCEP 재분석이 끝난 뒤의 빈 시점을 GFS로 채운다.
    (재분석 기간 중 영역 밖이라 빈 시점까지 받으러 가지 않도록 재분석 마지막 시점 이후만.)
    지향류 24시간 변화 계산을 위해 빈 시점이 있는 태풍은 경로 전체를 계산해 빈 칸만 채운다."""
    raw = [c for c in df.columns if c.startswith("atm_")]
    empty = df[raw].isna().all(axis=1) if raw else pd.Series(True, index=df.index)
    ncep_end = df.loc[~empty, "time"].max() if (~empty).any() else FIRST_DAY
    need = empty & (df["time"] > max(ncep_end, FIRST_DAY + pd.Timedelta(days=1)))
    if not need.any():
        return df
    sids = df.loc[need, "SID"].unique()
    pts = df[df["SID"].isin(sids)]
    g = features(pts, workers)
    df = df.copy()
    for c in g.columns.drop(["SID", "time"]):
        if c not in df:
            df[c] = np.nan
        df.loc[need, c] = g.loc[need[need].index, c]
    n = int(g.loc[need[need].index].drop(columns=["SID", "time"]).notna().any(axis=1).sum())
    print(f"[gfs] 환경 특성 채움 {n}/{int(need.sum())}개 시점")
    return df


def compare(start="2021-06-02", end="2026-03-16", workers=8):
    """NCEP와 GFS가 둘 다 있는 기간의 태풍 시점에서 특성 값 차이."""
    from typhoon.data import load
    from typhoon.env_atmos import OUT
    df = load()
    df = df[(df["time"] >= start) & (df["time"] <= end)][["SID", "time", "LAT", "LON"]]
    ncep = pd.read_parquet(OUT)
    a = df.merge(ncep, on=["SID", "time"], how="left")
    a.index = df.index
    days = (df["time"].dt.normalize() - pd.Timedelta(days=1)).nunique()
    print(f"[gfs] 비교 {len(df)}개 시점, 받을 날 {days}일(캐시 제외)")
    b = features(df, workers)
    cols = [c for c in b.columns if c.startswith("atm_") and c in a.columns]
    lines = [f"NCEP 재분석 vs GFS 분석장 특성 비교 ({start} ~ {end}, 태풍 {df.SID.nunique()}개, {len(df)}개 시점)",
             f"{'특성':<16}{'n':>7}{'NCEP 평균':>11}{'GFS 평균':>11}{'평균 차':>9}{'MAE':>9}{'NCEP 표준편차':>14}{'상관':>7}"]
    for c in cols:
        x, y = a[c], b[c]
        ok = x.notna() & y.notna()
        x, y = x[ok], y[ok]
        lines.append(f"{c:<16}{ok.sum():>7}{x.mean():>11.2f}{y.mean():>11.2f}{(y - x).mean():>9.2f}"
                     f"{(y - x).abs().mean():>9.2f}{x.std():>14.2f}{np.corrcoef(x, y)[0, 1]:>7.3f}")
    text = "\n".join(lines)
    print(text)
    (ROOT / "reports" / "gfs_comparison.txt").write_text(text + "\n", encoding="utf-8")
    return a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--start", default="2021-06-02")
    ap.add_argument("--end", default="2026-03-16")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.compare:
        compare(a.start, a.end, a.workers)


if __name__ == "__main__":
    main()
