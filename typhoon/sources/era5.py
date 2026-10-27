"""ERA5 기압면 자료 받기 (ECMWF Copernicus Climate Data Store, 새 CDS).

받는 것: 하루 4회(00/06/12/18 UTC), 1.0° 격자, 북위 0~60 · 동경 100~180
  - z500   : 500hPa 지위고도(gpm, geopotential / 9.80665 로 변환해 저장)
  - u, v   : 850 / 500 / 200 hPa 바람
저장(압축 int16):
  - 지난 해들   : data/env/era5/era5_YYYY.nc     (연 단위 요청 1개)
  - 올해(진행 중): data/env/era5/era5_YYYYMM.nc   (월 단위, 이번 달은 '오늘-6일'까지)
이미 있는 파일은 건너뛴다. 요청은 여러 개를 동시에 제출(--workers, 기본 4).

키: .env 의 CDS_API_KEY (CDS 개인 액세스 토큰)
주의: ERA5 pressure-levels 데이터셋 페이지에서 라이선스 동의를 한 번 해야 한다.

실행 예:
  python -m typhoon.sources.era5                       # 1951 ~ 최신 전부(최근 기간부터)
  python -m typhoon.sources.era5 --start 2015 --end 2025
  python -m typhoon.sources.era5 --recent              # 최근 2개월 다시 받기
  python -m typhoon.sources.era5 --dry-run
"""
import argparse
import calendar
import datetime as dt
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from ._env import DATA, require_key

CDS_URL = "https://cds.climate.copernicus.eu/api"
DATASET = "reanalysis-era5-pressure-levels"
OUT = DATA / "env" / "era5"
AREA = [60, 100, 0, 180]  # [북, 서, 남, 동]
TIMES = ["00:00", "06:00", "12:00", "18:00"]
LEVELS = [850, 500, 200]
G = 9.80665
FIRST_YEAR = 1951
LAG_DAYS = 6          # ERA5 공개 지연(약 5일) + 여유

GUIDE = """CDS_API_KEY 발급 방법 (ECMWF Copernicus Climate Data Store):
  1) https://cds.climate.copernicus.eu 접속 → Login/Register 로 ECMWF 계정 만들기(무료)
  2) https://cds.climate.copernicus.eu/profile 에서 'Personal Access Token' 복사
  3) https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels?tab=download
     맨 아래 'Terms of use' 에서 라이선스 동의(Accept)
  4) .env 에  CDS_API_KEY=복사한토큰  저장"""

# 저장 변수: 이름 -> (원본 변수, 기압면)
FIELDS = {"z500": ("z", 500)}
for _lv in LEVELS:
    FIELDS[f"u{_lv}"] = ("u", _lv)
    FIELDS[f"v{_lv}"] = ("v", _lv)
# int16 압축 (값 = 정수 * scale + offset)
PACK = {"z500": (0.1, 5500.0)}
PACK.update({k: (0.01, 0.0) for k in FIELDS if k != "z500"})

_nc_lock = threading.Lock()   # netCDF/HDF5 는 스레드 안전하지 않음


# ---------------------------------------------------------------- 기간·경로
def latest_day():
    return dt.date.today() - dt.timedelta(days=LAG_DAYS)


def year_path(y):
    return OUT / f"era5_{y}.nc"


def month_path(y, m):
    return OUT / f"era5_{y}{m:02d}.nc"


def periods(start_year, end_year):
    """(이름, 경로, 연, 월목록, 마지막 달 끝 일) 목록. 최근 연도부터."""
    last = latest_day()
    out = []
    for y in range(min(end_year, last.year), start_year - 1, -1):
        monthly = y == last.year or (not year_path(y).exists()
                                     and any(month_path(y, m).exists() for m in range(1, 13)))
        if not monthly:
            out.append((str(y), year_path(y), y, list(range(1, 13)), None))
        else:  # 진행 중인 해: 월 단위
            for m in range(12, 0, -1):
                if (y, m) > (last.year, last.month):
                    continue
                endday = last.day if (y, m) == (last.year, last.month) else None
                out.append((f"{y}-{m:02d}", month_path(y, m), y, [m], endday))
    return out


def have_year(y):
    """그 해 자료가 (연 파일 또는 12개 월 파일로) 다 있는가."""
    return year_path(y).exists() or all(month_path(y, m).exists() for m in range(1, 13))


def files_for_year(y):
    if year_path(y).exists():
        return [year_path(y)]
    return [month_path(y, m) for m in range(1, 13) if month_path(y, m).exists()]


def build_request(year, months, endday=None, grid=1.0):
    if endday is not None:
        days = range(1, endday + 1)
    else:
        days = range(1, 32)        # 없는 날짜(2/30 등)는 CDS 가 무시
    req = {
        "product_type": ["reanalysis"],
        "variable": ["geopotential", "u_component_of_wind", "v_component_of_wind"],
        "pressure_level": [str(l) for l in LEVELS],
        "year": [str(year)],
        "month": [f"{m:02d}" for m in months],
        "day": [f"{d:02d}" for d in days],
        "time": TIMES,
        "area": AREA,
        "grid": [grid, grid],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    return req


# ---------------------------------------------------------------- 후처리
def _pack(raw_path, out_path):
    """CDS 원본(9개 장) → 필요한 7개 장만 int16 압축 저장. 위도 오름차순, 시간 이름 'time'."""
    import xarray as xr
    with _nc_lock:
        ds = xr.open_dataset(raw_path).load()
    tname = "valid_time" if "valid_time" in ds.dims else "time"
    if "expver" in ds.dims:              # ERA5/ERA5T 섞인 경우
        ds = ds.max("expver")
    ds = ds.rename({tname: "time", "latitude": "lat", "longitude": "lon"}).sortby("lat").sortby("time")
    out = xr.Dataset(coords={"time": ds["time"].values, "lat": ds["lat"].values, "lon": ds["lon"].values})
    enc = {}
    for name, (var, lv) in FIELDS.items():
        a = ds[var].sel(pressure_level=lv).values.astype(np.float32)
        if name == "z500":
            a = a / G
        out[name] = (("time", "lat", "lon"), a)
        sc, off = PACK[name]
        enc[name] = {"dtype": "int16", "scale_factor": sc, "add_offset": off,
                     "_FillValue": -32768, "zlib": True, "complevel": 4}
    tmp = out_path.with_suffix(".tmp.nc")
    with _nc_lock:
        out.to_netcdf(tmp, encoding=enc)
    tmp.replace(out_path)


def _fetch(client_key, name, path, y, months, endday, grid, retries=3):
    import cdsapi
    raw = path.with_suffix(".raw.nc")
    last = None
    for k in range(retries):
        try:
            t0 = time.time()
            c = cdsapi.Client(url=CDS_URL, key=client_key, quiet=True, progress=False)
            c.retrieve(DATASET, build_request(y, months, endday, grid)).download(str(raw))
            _pack(raw, path)
            raw.unlink(missing_ok=True)
            return name, time.time() - t0, None
        except Exception as e:  # noqa: BLE001
            raw.unlink(missing_ok=True)
            text = str(e)
            low = text.lower()
            if "licence" in low or "license" in low or "401" in text or "unauthorized" in low:
                return name, 0, text[:500]
            last = text[:500]
            time.sleep(30 * (k + 1))
    return name, 0, last


def download(start_year=FIRST_YEAR, end_year=None, force=(), workers=4, grid=1.0,
             dry_run=False, log=print):
    """빠진 파일을 동시 요청으로 받는다. force: 다시 받을 경로 집합. 실패 목록 반환."""
    end_year = end_year or latest_day().year
    todo = []
    for name, path, y, months, endday in periods(start_year, end_year):
        if path in force or not path.exists():
            todo.append((name, path, y, months, endday))
    log(f"[era5] {start_year}~{end_year} 받을 요청 {len(todo)}개 (동시 {workers}개) → {OUT}")
    if dry_run or not todo:
        for t in todo[:8]:
            log("  ", t[0], build_request(t[2], t[3], t[4], grid)["month"], t[4])
        return []
    key = require_key("CDS_API_KEY", GUIDE)
    OUT.mkdir(parents=True, exist_ok=True)
    failed = []
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(_fetch, key, *t, grid) for t in todo]
        for i, f in enumerate(as_completed(futs), 1):
            name, sec, err = f.result()
            if err:
                failed.append((name, err))
                log(f"  [{i}/{len(todo)}] 실패 {name}: {err}")
            else:
                log(f"  [{i}/{len(todo)}] {name} 완료 ({sec:.0f}초, 누적 {time.time() - t0:.0f}초)")
    return failed


def recent_paths(n_months=2):
    last = latest_day()
    y, m = last.year, last.month
    out = []
    for _ in range(n_months):
        out.append(month_path(y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return set(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="ERA5 기압면 자료 다운로드(연 단위, 동시 요청)")
    ap.add_argument("--start", type=int, default=FIRST_YEAR)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--grid", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--recent", action="store_true", help="최근 2개월 다시 받기")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.recent:
        last = latest_day()
        failed = download(last.year - 1, last.year, force=recent_paths(), workers=a.workers,
                          grid=a.grid, dry_run=a.dry_run)
    else:
        failed = download(a.start, a.end, workers=a.workers, grid=a.grid, dry_run=a.dry_run)
    if failed:
        print("[era5] 실패:", failed, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
