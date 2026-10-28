"""ERA5 6시간 분석장으로 만든 대기 환경 특성 (env_atmos.py 의 NCEP 특성과 같은 정의·컬럼 이름 atm_*).

자료: typhoon/sources/era5.py 가 받은 data/env/era5/era5_YYYY[MM].nc (1.0°, 0~60N 100~180E, 하루 4회)
실시간 조건: 태풍 시점 t 에서 (t - 6시간)을 6시간 단위로 내림한 분석장을 쓴다(미래 정보 금지).
출력: data/env/era5_features.parquet (SID, time, atm_*), 행 수 = typhoon.data.load() 와 같음.
      자료가 없는 기간은 NaN.

    python -m typhoon.env_era5              # 빠진 자료 받기 + 전체 계산
    python -m typhoon.env_era5 --no-download
    python -m typhoon.env_era5 --recent     # 최근 2개월 다시 받아 해당 연도만 다시 계산
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from typhoon.data import load
from typhoon.env_atmos import RIDGE_GPM, STEER_HALF, _bilinear, _box_mean, add_changes
from typhoon.sources import era5 as src

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "data" / "env"
OUT = ENV / "era5_features.parquet"
LAG = pd.Timedelta(hours=6)
FIELDS = list(src.FIELDS)
COLS = ["atm_u_steer", "atm_v_steer", "atm_shear", "atm_z500", "atm_z500_n", "atm_z500_e",
        "atm_z500_w", "atm_ridge_west", "atm_ridge_lat", "atm_z500_max"]


def _open(year):
    """그 해 파일 + 전해 마지막 시각(1월 1일 00시용). 없으면 None.
    같은 시각이 겹치면 files_for_year 순서상 뒤(월 파일)가 이긴다."""
    files = src.files_for_year(year)
    if not files:
        return None
    dss = []
    for f in src.files_for_year(year - 1):      # 파일 순서와 무관하게 병합 뒤 가장 늦은 시각을 고른다
        with xr.open_dataset(f) as p:
            dss.append(p[FIELDS].isel(time=slice(-1, None)).load())
    n_prev = len(dss)
    for f in files:
        with xr.open_dataset(f) as d:
            dss.append(d[FIELDS].load())
    ds = xr.concat(dss, "time")
    keep = ~pd.Index(ds["time"].values).duplicated(keep="last")
    ds = ds.isel(time=np.where(keep)[0]).sortby("time")
    if n_prev:                                   # 전해 쪽은 가장 늦은 한 시각만 남긴다
        t = pd.DatetimeIndex(ds["time"].values)
        old = np.where(t.year < year)[0]
        drop = old[:-1] if len(old) else old
        ds = ds.isel(time=np.setdiff1d(np.arange(len(t)), drop))
    return ds


def features_year(year, pts):
    ds = _open(year)
    o = pd.DataFrame(np.nan, index=pts.index, columns=COLS)
    if ds is None:
        return o
    times = pd.DatetimeIndex(ds["time"].values)
    glat = ds["lat"].values.astype(float)
    glon = ds["lon"].values.astype(float)
    want = (pd.to_datetime(pts["time"]) - LAG).dt.floor("6h")
    ti_all = times.get_indexer(want)
    used = np.unique(ti_all[ti_all >= 0])
    if len(used) == 0:
        return o
    # 필요한 시각만 남겨 계산량을 줄인다
    remap = np.full(len(times), -1)
    remap[used] = np.arange(len(used))
    ti = np.where(ti_all >= 0, remap[np.maximum(ti_all, 0)], -1)
    sub = ds.isel(time=used)
    z = sub["z500"].values.astype(np.float32)
    u = np.stack([sub[f"u{l}"].values for l in src.LEVELS], axis=1).astype(np.float32)
    v = np.stack([sub[f"v{l}"].values for l in src.LEVELS], axis=1).astype(np.float32)

    lat = pts["LAT"].to_numpy(float)
    lon = pts["LON"].to_numpy(float) % 360
    res = glat[1] - glat[0]
    n = int(round(STEER_HALF / res))

    us = _box_mean(u.mean(axis=1), n)
    vs = _box_mean(v.mean(axis=1), n)
    i850, i200 = src.LEVELS.index(850), src.LEVELS.index(200)
    du = u[:, i200] - u[:, i850]
    dv = v[:, i200] - v[:, i850]

    o["atm_u_steer"] = _bilinear(us, ti, lat, lon, glat, glon)
    o["atm_v_steer"] = _bilinear(vs, ti, lat, lon, glat, glon)
    o["atm_shear"] = np.hypot(_bilinear(du, ti, lat, lon, glat, glon),
                              _bilinear(dv, ti, lat, lon, glat, glon))
    o["atm_z500"] = _bilinear(z, ti, lat, lon, glat, glon)
    o["atm_z500_n"] = _bilinear(z, ti, lat + 5, lon, glat, glon)
    o["atm_z500_e"] = _bilinear(z, ti, lat, lon + 10, glat, glon)
    o["atm_z500_w"] = _bilinear(z, ti, lat, lon - 10, glat, glon)

    from scipy.ndimage import label
    band = (glat >= 10) & (glat <= 40)
    hit = z[:, band, :] >= RIDGE_GPM
    east = glon >= 140
    ridge_west = np.full(len(z), np.nan)
    for t in range(len(z)):
        if not hit[t][:, east].any():
            continue
        lab, _ = label(hit[t])
        ids = np.unique(lab[:, east])
        ids = ids[ids > 0]
        cols = np.isin(lab, ids).any(axis=0)
        ridge_west[t] = glon[np.argmax(cols)]
    zmax = z.reshape(len(z), -1).max(axis=1)

    axis_band = (glat >= 10) & (glat <= 50)
    gl = glat[axis_band]
    fx = (lon - glon[0]) / (glon[1] - glon[0])
    okx = (fx >= 0) & (fx <= len(glon) - 1) & (ti >= 0)
    ridge_lat = np.full(len(ti), np.nan)
    if okx.any():
        x0 = np.minimum(np.floor(fx[okx]).astype(int), len(glon) - 2)
        wx = (fx[okx] - x0)[:, None]
        zb = z[:, axis_band, :]
        col = zb[ti[okx], :, x0] * (1 - wx) + zb[ti[okx], :, x0 + 1] * wx
        ridge_lat[okx] = gl[np.argmax(col, axis=1)]

    valid = ti >= 0
    tsafe = np.where(valid, ti, 0)
    o["atm_ridge_west"] = np.where(valid, ridge_west[tsafe], np.nan)
    o["atm_ridge_lat"] = ridge_lat
    o["atm_z500_max"] = np.where(valid, zmax[tsafe], np.nan)
    return o


def compute(df, years, log=print):
    yr = df["time"].dt.year
    parts = []
    for y in years:
        pts = df[yr == y]
        if pts.empty:
            continue
        if not src.files_for_year(y):
            continue
        parts.append(features_year(y, pts))
    log(f"[era5] 특성 계산 {len(parts)}개 연도")
    return pd.concat(parts) if parts else pd.DataFrame(columns=COLS)


def build(recent=False, download=True, workers=4):
    t0 = time.time()
    df = load()
    keep = df[["SID", "time"]].copy()
    failed = []
    if recent:
        rm = src.recent_months()
        if download:
            failed = src.download(min(y for y, _ in rm), max(y for y, _ in rm),
                                  force_months=rm, workers=workers)
        years = sorted({y for y, _ in rm})
        new = compute(df, years)
        if OUT.exists():
            old = pd.read_parquet(OUT)[["SID", "time"] + COLS]
            base = keep.merge(old, on=["SID", "time"], how="left")
            base.index = df.index
        else:
            base = pd.concat([keep, pd.DataFrame(np.nan, index=df.index, columns=COLS)], axis=1)
        base.loc[new.index, COLS] = new[COLS].to_numpy()
        feat = base
    else:
        if download:
            failed = src.download(workers=workers)
        years = sorted(df["time"].dt.year.unique())
        new = compute(df, years)
        feat = pd.concat([keep, new.reindex(df.index)[COLS]], axis=1)
    feat = add_changes(feat)
    assert len(feat) == len(df)
    feat.to_parquet(OUT, index=False)
    print(f"[era5] 저장 {OUT} ({len(feat)}행, {time.time() - t0:.0f}초)")
    if failed:
        print("[era5] 받기 실패:", failed)
    return feat, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", action="store_true", help="최근 2개월 다시 받아 갱신")
    ap.add_argument("--no-download", action="store_true", help="받지 않고 있는 파일로만 계산")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    build(recent=a.recent, download=not a.no_download, workers=a.workers)


if __name__ == "__main__":
    main()
