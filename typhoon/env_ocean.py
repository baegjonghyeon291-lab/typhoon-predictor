"""태풍 경로 시점별 바다·기후 특성(ocn_*) 생성.

자료(모두 공개, 키 없음):
- NOAA ERSST v5 월평균 SST (PSL)
- ONI (CPC), PDO (NCEI ERSST v5), Niño4 편차 (PSL)

실행: python -m typhoon.env_ocean  -> data/env/ocean_features.parquet
"""
from pathlib import Path
import io
import numpy as np
import pandas as pd
import requests
import xarray as xr
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
OCEAN_DIR = ROOT / "data" / "env" / "ocean"
OUT = ROOT / "data" / "env" / "ocean_features.parquet"

SST_URL = "https://downloads.psl.noaa.gov/Datasets/noaa.ersst.v5/sst.mnmean.nc"
SST_OPENDAP = "https://psl.noaa.gov/thredds/dodsC/Datasets/noaa.ersst.v5/sst.mnmean.nc"
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
PDO_URL = "https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat"
NINO4_URL = "https://psl.noaa.gov/data/correlation/nina4.anom.data"

SST_FILE = OCEAN_DIR / "ersst_v5_wnp.nc"   # 영역만 잘라 저장
# 태풍 위치 범위(LAT 0~70, LON 56~267)를 넉넉히 덮는 영역
LAT_RANGE = (-10, 88)
LON_RANGE = (30, 290)
CLIM_YEARS = (1991, 2020)
AHEAD_DEG = 5.0

SEAS_MONTH = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
              "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}


def _get(url, timeout=120):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r


# ---------------- 다운로드 ----------------
def download_sst():
    OCEAN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OCEAN_DIR / "sst.mnmean.nc.part"
    try:
        with requests.get(SST_URL, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        ds = xr.open_dataset(tmp)
    except Exception as e:  # 전체 파일 실패 시 OPeNDAP로 영역만
        print(f"[ocean] 전체 파일 다운로드 실패({e}), OPeNDAP 사용")
        ds = xr.open_dataset(SST_OPENDAP)
    sub = ds["sst"].sel(lat=slice(LAT_RANGE[1], LAT_RANGE[0]), lon=slice(*LON_RANGE)).load()
    ds.close()
    sub.to_dataset().to_netcdf(SST_FILE)
    tmp.unlink(missing_ok=True)
    return sub


def download_indices():
    OCEAN_DIR.mkdir(parents=True, exist_ok=True)
    for url, name in [(ONI_URL, "oni.ascii.txt"), (PDO_URL, "ersst.v5.pdo.dat"), (NINO4_URL, "nina4.anom.data")]:
        (OCEAN_DIR / name).write_bytes(_get(url).content)


# ---------------- 지수 파싱 ----------------
def parse_oni():
    t = pd.read_csv(OCEAN_DIR / "oni.ascii.txt", sep=r"\s+")
    t["month"] = t["SEAS"].map(SEAS_MONTH)
    t["date"] = pd.to_datetime(dict(year=t["YR"], month=t["month"], day=1))
    return t.set_index("date")["ANOM"].astype(float).rename("oni")


def _parse_year_x12(path, missing):
    rows = []
    for line in Path(path).read_text().splitlines():
        p = line.split()
        if len(p) != 13 or not p[0].isdigit():
            continue
        y = int(p[0])
        for m, v in enumerate(p[1:], 1):
            v = float(v)
            if abs(v) >= missing:
                continue
            rows.append((pd.Timestamp(y, m, 1), v))
    return pd.Series(dict(rows)).sort_index()


def parse_pdo():
    return _parse_year_x12(OCEAN_DIR / "ersst.v5.pdo.dat", 99).rename("pdo")


def parse_nino4():
    return _parse_year_x12(OCEAN_DIR / "nina4.anom.data", 99).rename("nino4")


# ---------------- SST 처리 ----------------
def fill_land(arr):
    """육지/결측 격자를 가장 가까운 바다 격자 값으로 채움(시점별)."""
    out = arr.copy()
    for i in range(arr.shape[0]):
        a = arr[i]
        nan = np.isnan(a)
        if not nan.any() or nan.all():
            continue
        idx = ndimage.distance_transform_edt(nan, return_distances=False, return_indices=True)
        out[i] = a[tuple(idx)]
    return out


def bilinear(field, lats, lons, ti, plat, plon):
    """field (T,Y,X), lats 오름차순, lons 오름차순 등간격. 점별 시간 인덱스 ti."""
    dy, dx = lats[1] - lats[0], lons[1] - lons[0]
    fy = np.clip((plat - lats[0]) / dy, 0, len(lats) - 1.000001)
    fx = np.clip((plon - lons[0]) / dx, 0, len(lons) - 1.000001)
    y0, x0 = np.floor(fy).astype(int), np.floor(fx).astype(int)
    wy, wx = fy - y0, fx - x0
    f = lambda y, x: field[ti, y, x]
    return ((1 - wy) * (1 - wx) * f(y0, x0) + (1 - wy) * wx * f(y0, x0 + 1)
            + wy * (1 - wx) * f(y0 + 1, x0) + wy * wx * f(y0 + 1, x0 + 1))


def build(df=None, download=True):
    if df is None:
        from typhoon.data import load
        df = load()
    if download:
        sst = download_sst()
        download_indices()
    else:
        sst = xr.open_dataset(SST_FILE)["sst"]
    sst = sst.sortby("lat")
    times = pd.DatetimeIndex(sst.time.values)
    lats, lons = sst.lat.values.astype(float), sst.lon.values.astype(float)
    raw = sst.values.astype(float)
    filled = fill_land(raw)

    # 1991~2020 같은 달 기후값
    in_clim = (times.year >= CLIM_YEARS[0]) & (times.year <= CLIM_YEARS[1])
    clim = np.stack([filled[in_clim & (times.month == m)].mean(0) for m in range(1, 13)])
    anom = filled - clim[times.month - 1]

    # 태풍 시점 -> 월 인덱스. 실시간 예측과 같은 조건이 되도록 편차는 지난달 값(이번 달 평균은 아직 모름)을 쓰고
    # 기후값만 이번 달 것을 쓴다. 자료 마지막 달 이후는 마지막 달 편차 지속(clim + last anom)
    month = df["time"].dt.to_period("M").dt.to_timestamp()
    prev = month - pd.DateOffset(months=1)
    tpos = times.get_indexer(prev)
    last_t = times[-1]
    beyond = prev > last_t
    n_beyond = int(beyond.sum())
    tpos = np.where(beyond, len(times) - 1, tpos)
    if (tpos < 0).any():
        raise ValueError("SST 자료 범위 밖 시점 존재")

    plat = df["LAT"].values.astype(float)
    plon = df["LON"].values.astype(float) % 360
    mon_idx = month.dt.month.values - 1

    a_here = bilinear(anom, lats, lons, tpos, plat, plon)
    c_here = bilinear(clim, lats, lons, mon_idx, plat, plon)
    a_ahead = bilinear(anom, lats, lons, tpos, plat + AHEAD_DEG, plon)
    c_ahead = bilinear(clim, lats, lons, mon_idx, plat + AHEAD_DEG, plon)

    out = pd.DataFrame({"SID": df["SID"].values, "time": df["time"].values})
    out["ocn_sst"] = c_here + a_here
    out["ocn_sst_anom"] = a_here
    out["ocn_sst_ahead"] = c_ahead + a_ahead
    out["ocn_sst_ahead_diff"] = out["ocn_sst_ahead"] - out["ocn_sst"]

    info = {"sst": (times[0], last_t, n_beyond)}
    # 지수도 그 시점에 이미 발표된 값만: PDO·Niño4는 지난달, ONI는 3개월 평균의 가운데 달 기준이라 두 달 전
    for s, lag in [(parse_oni(), 2), (parse_pdo(), 1), (parse_nino4(), 1)]:
        key = month - pd.DateOffset(months=lag)
        last = s.index.max()
        s_full = s.reindex(s.index.union(key.unique())).sort_index().ffill()
        out[f"ocn_{s.name}"] = s_full.reindex(key).values
        info[s.name] = (s.index.min(), last, int((key > last).sum()))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)
    return out, info


def main():
    out, info = build()
    print(f"[ocean] 저장: {OUT}  행 {len(out)}")
    for k, (a, b, n) in info.items():
        msg = f"  {k}: {a:%Y-%m} ~ {b:%Y-%m}"
        if n:
            msg += f"  (이후 {n}행은 마지막 값으로 채움" + (", SST는 기후값+마지막 달 편차)" if k == "sst" else ")")
        print(msg)
    cols = [c for c in out.columns if c.startswith("ocn_")]
    print(pd.DataFrame({"결측률": out[cols].isna().mean().round(4), "평균": out[cols].mean().round(3)}))


if __name__ == "__main__":
    main()
