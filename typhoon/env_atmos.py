"""대기 환경(지향류·북태평양 고기압) 특성.

NCEP/NCAR Reanalysis 1 일평균(NOAA PSL OPeNDAP)에서 lat 0~60N, lon 100~180E만 받아
data/env/ncep/{var}.{year}.nc 로 캐시하고, 태풍 경로 각 시점에 atm_* 특성을 붙인다.

    python -m typhoon.env_atmos            # 전체 받기 + 계산
    python -m typhoon.env_atmos --recent   # 최근 2년만 다시 받아 기존 parquet 갱신
"""
import argparse
import datetime as dt
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from typhoon.data import load

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "data" / "env"
NCEP = ENV / "ncep"
OUT = ENV / "atmos_features.parquet"
BASE = "https://psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis.dailyavgs/pressure/{var}.{year}.nc"

FIRST_YEAR = 1951
LAT_SLICE = slice(60, 0)      # NCEP 위도는 내림차순
LON_SLICE = slice(100, 180)
VARS = {"hgt": [500], "uwnd": [850, 500, 200], "vwnd": [850, 500, 200]}
RIDGE_GPM = 5880
STEER_HALF = 5.0              # 지향류 평균 상자 반폭(도)


# ---------------------------------------------------------------- 다운로드
def _path(var, year):
    return NCEP / f"{var}.{year}.nc"


def _fetch(var, year, retries=4):
    p = _path(var, year)
    last = None
    for k in range(retries):
        try:
            with xr.open_dataset(BASE.format(var=var, year=year)) as ds:
                da = ds[var].sel(level=VARS[var], lat=LAT_SLICE, lon=LON_SLICE).load()
            tmp = p.with_suffix(".tmp.nc")
            da.astype("float32").to_dataset().to_netcdf(tmp)
            tmp.replace(p)
            return var, year, None
        except Exception as e:  # 네트워크 오류 재시도
            last = e
            time.sleep(3 * (k + 1))
    return var, year, repr(last)


def download(years, force_years=(), workers=6):
    """years 중 캐시 없는 것(및 force_years)을 병렬로 받는다. 실패 목록 반환."""
    NCEP.mkdir(parents=True, exist_ok=True)
    jobs = [(v, y) for y in years for v in VARS if y in force_years or not _path(v, y).exists()]
    failed = []
    if not jobs:
        return failed
    print(f"[atmos] 다운로드 {len(jobs)}개 파일")
    # netCDF4(libcurl)는 스레드 안전하지 않아 스레드 병렬 시 abort → 프로세스 병렬
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(_fetch, v, y) for v, y in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            v, y, err = f.result()
            if err:
                failed.append((v, y, err))
                print(f"  실패 {v} {y}: {err}")
            elif i % 20 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}")
    return failed


# ---------------------------------------------------------------- 계산 도우미
def _open_year(year):
    """(dates, lat 오름차순, lon, z500[t,y,x], u[t,lev,y,x], v[t,lev,y,x])."""
    out = {}
    for var in VARS:
        with xr.open_dataset(_path(var, year)) as ds:
            da = ds[var].sortby("lat").load()
        out[var] = da
    z = out["hgt"].sel(level=500)
    dates = pd.to_datetime(z.time.values).normalize()
    return (dates, z.lat.values.astype(float), z.lon.values.astype(float),
            z.values.astype(np.float32), out["uwnd"].values.astype(np.float32),
            out["vwnd"].values.astype(np.float32))


def _box_mean(f, n):
    """마지막 두 축에 대해 (2n+1)^2 상자 평균, 가장자리는 있는 격자만 평균."""
    pad = [(0, 0)] * (f.ndim - 2) + [(n, n), (n, n)]
    g = np.pad(f, pad, constant_values=np.nan)
    ny, nx = f.shape[-2:]
    acc = np.zeros_like(f, dtype=np.float64)
    cnt = np.zeros_like(f, dtype=np.float64)
    for dy in range(2 * n + 1):
        for dx in range(2 * n + 1):
            s = g[..., dy:dy + ny, dx:dx + nx]
            ok = ~np.isnan(s)
            acc += np.where(ok, s, 0)
            cnt += ok
    return (acc / np.maximum(cnt, 1)).astype(np.float32)


def _bilinear(field, ti, lat, lon, glat, glon):
    """field[t,y,x]를 (ti, lat, lon) 점들에서 bilinear 보간. 영역 밖은 NaN."""
    dy, dx = glat[1] - glat[0], glon[1] - glon[0]
    fy = (lat - glat[0]) / dy
    fx = (lon - glon[0]) / dx
    out = np.full(len(ti), np.nan, dtype=np.float64)
    ok = (fy >= 0) & (fy <= len(glat) - 1) & (fx >= 0) & (fx <= len(glon) - 1) & (ti >= 0)
    if not ok.any():
        return out
    t, y, x = ti[ok], fy[ok], fx[ok]
    y0 = np.minimum(np.floor(y).astype(int), len(glat) - 2)
    x0 = np.minimum(np.floor(x).astype(int), len(glon) - 2)
    wy, wx = y - y0, x - x0
    f00 = field[t, y0, x0]; f01 = field[t, y0, x0 + 1]
    f10 = field[t, y0 + 1, x0]; f11 = field[t, y0 + 1, x0 + 1]
    out[ok] = (f00 * (1 - wy) * (1 - wx) + f01 * (1 - wy) * wx
               + f10 * wy * (1 - wx) + f11 * wy * wx)
    return out


def features_year(year, pts):
    """pts: 해당 연도 행(LAT, LON, time). 같은 인덱스의 atm_* DataFrame 반환."""
    dates, glat, glon, z, u, v = _open_year(year)
    # 실시간 예측과 같은 조건: 그 시점엔 당일 평균이 아직 없으니 전날 평균을 쓴다
    day = pd.to_datetime(pts["time"]).dt.normalize() - pd.Timedelta(days=1)
    ti = pd.Index(dates).get_indexer(day)          # 없는 날(올해 미래 등)은 -1
    lat = pts["LAT"].to_numpy(float)
    lon = pts["LON"].to_numpy(float) % 360
    res = 2.5
    n = int(round(STEER_HALF / res))

    # 지향류: 850/500/200 평균을 ±5° 상자 평균 후 중심에서 보간
    us = _box_mean(u.mean(axis=1), n)
    vs = _box_mean(v.mean(axis=1), n)
    # 시어: 200 - 850 (중심 보간)
    lev = list(VARS["uwnd"])
    i850, i200 = lev.index(850), lev.index(200)
    du = u[:, i200] - u[:, i850]
    dv = v[:, i200] - v[:, i850]

    o = pd.DataFrame(index=pts.index)
    o["atm_u_steer"] = _bilinear(us, ti, lat, lon, glat, glon)
    o["atm_v_steer"] = _bilinear(vs, ti, lat, lon, glat, glon)
    o["atm_shear"] = np.hypot(_bilinear(du, ti, lat, lon, glat, glon),
                              _bilinear(dv, ti, lat, lon, glat, glon))
    o["atm_z500"] = _bilinear(z, ti, lat, lon, glat, glon)
    o["atm_z500_n"] = _bilinear(z, ti, lat + 5, lon, glat, glon)
    o["atm_z500_e"] = _bilinear(z, ti, lat, lon + 10, glat, glon)
    o["atm_z500_w"] = _bilinear(z, ti, lat, lon - 10, glat, glon)

    # 날짜별 스칼라: 5880 서쪽 끝, 영역 최대
    band = (glat >= 10) & (glat <= 40)
    # 대륙 쪽 5880 영역과 구분하려고 140E 이동에 걸친(서태평양 고기압) 연결 영역만 사용
    from scipy.ndimage import label
    hit = z[:, band, :] >= RIDGE_GPM                       # t,y,x
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

    # 아열대고기압 축: 태풍 경도(선형 보간)에서 z500 최대 위도, 10~50N
    axis_band = (glat >= 10) & (glat <= 50)
    gl = glat[axis_band]
    fx = (lon - glon[0]) / (glon[1] - glon[0])
    okx = (fx >= 0) & (fx <= len(glon) - 1) & (ti >= 0)
    ridge_lat = np.full(len(ti), np.nan)
    if okx.any():
        x0 = np.minimum(np.floor(fx[okx]).astype(int), len(glon) - 2)
        wx = (fx[okx] - x0)[:, None]
        zb = z[:, axis_band, :]
        col = zb[ti[okx], :, x0] * (1 - wx) + zb[ti[okx], :, x0 + 1] * wx   # pts,y
        ridge_lat[okx] = gl[np.argmax(col, axis=1)]

    valid = ti >= 0
    tsafe = np.where(valid, ti, 0)
    o["atm_ridge_west"] = np.where(valid, ridge_west[tsafe], np.nan)
    o["atm_ridge_lat"] = ridge_lat
    o["atm_z500_max"] = np.where(valid, zmax[tsafe], np.nan)
    return o


def add_changes(feat):
    """같은 태풍의 24시간 전 시점 대비 지향류 변화."""
    key = feat[["SID", "time", "atm_u_steer", "atm_v_steer"]].copy()
    prev = key.rename(columns={"atm_u_steer": "pu", "atm_v_steer": "pv"})
    prev["time"] = prev["time"] + pd.Timedelta(hours=24)
    m = feat[["SID", "time"]].merge(prev, on=["SID", "time"], how="left")
    feat["atm_u_steer_24"] = feat["atm_u_steer"].to_numpy() - m["pu"].to_numpy()
    feat["atm_v_steer_24"] = feat["atm_v_steer"].to_numpy() - m["pv"].to_numpy()
    return feat


def compute(df, years):
    parts = []
    yr = df["time"].dt.year
    for y in years:
        pts = df[yr == y]
        if pts.empty:
            continue
        if not all(_path(v, y).exists() for v in VARS):
            print(f"  {y}: 캐시 없음, NaN")
            continue
        parts.append(features_year(y, pts))
    feat = pd.concat(parts) if parts else pd.DataFrame(index=df.index[:0])
    return feat


def build(recent=False, workers=6):
    t0 = time.time()
    this_year = dt.datetime.utcnow().year
    df = load()
    keep = df[["SID", "time"]].copy()
    all_years = list(range(FIRST_YEAR, this_year + 1))
    if recent and OUT.exists():
        old = pd.read_parquet(OUT)
        old = old.drop(columns=[c for c in old.columns if c.endswith("_24")])
        merged = keep.merge(old, on=["SID", "time"], how="left", indicator=True)
        missing_years = set(df.loc[(merged["_merge"] == "left_only").to_numpy(), "time"].dt.year)
        years = sorted(set([this_year - 1, this_year]) | missing_years)
        failed = download(years, force_years=(this_year - 1, this_year), workers=workers)
        new = compute(df, years)
        base = merged.drop(columns="_merge")
        base.index = df.index
        cols = [c for c in new.columns]
        base.loc[new.index, cols] = new[cols]
        base.loc[df["time"].dt.year.isin(years) & ~df.index.isin(new.index), cols] = np.nan
        feat = base
    else:
        years = all_years
        failed = download(years, force_years=(this_year,), workers=workers)
        new = compute(df, years)
        feat = pd.concat([keep, new.reindex(df.index)], axis=1)
    feat = add_changes(feat)
    assert len(feat) == len(df)
    ENV.mkdir(parents=True, exist_ok=True)
    feat.to_parquet(OUT, index=False)
    print(f"[atmos] 저장 {OUT} ({len(feat)}행, {time.time() - t0:.0f}초)")
    if failed:
        print("[atmos] 실패:", failed)
    return feat, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", action="store_true", help="최근 2년만 다시 받아 갱신")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    build(recent=a.recent, workers=a.workers)


if __name__ == "__main__":
    main()
