"""ERA5 기압면 자료 받기 (ECMWF Copernicus Climate Data Store, 새 CDS).

받는 것: 하루 4회(00/06/12/18 UTC), 1.0° 격자, 북위 0~60 · 동경 100~180
  - z500   : 500hPa 지위고도(gpm = geopotential / 9.80665 로 변환해 저장)
  - u, v   : 850 / 500 / 200 hPa 바람

CDS 요청 한도: 요청 1개당 비용 60000(= 장 수 × 12) → 장 약 5000개.
  그래서 요청 단위를  u 1년 / v 1년 / z500 3년  으로 묶는다(1년당 약 2.3개 요청).
  진행 중인 해는 '지난달까지' 묶음(u/v/z) + 이번 달(오늘-6일까지) 월 파일 1개.

저장(int16 압축):
  data/env/era5/era5_YYYY.nc            지난 해 전체
  data/env/era5/era5_YYYY_MM-MM.nc      해의 일부 달 묶음(진행 중인 해)
  data/env/era5/era5_YYYYMM.nc          월 파일(이번 달, --recent 로 다시 받은 달). 같은 시각이면 월 파일 우선
  data/env/era5/raw/                    받는 중인 원본(합쳐지면 지움)
이미 있는 기간은 건너뛴다. 요청은 여러 개를 동시에 제출(--workers, 기본 4).

키: .env 의 CDS_API_KEY (CDS 개인 액세스 토큰)
주의: ERA5 pressure-levels 데이터셋 페이지에서 라이선스 동의를 한 번 해야 한다.

실행 예:
  python -m typhoon.sources.era5                       # 1951 ~ 최신(최근 기간부터 받음)
  python -m typhoon.sources.era5 --start 2015 --end 2025
  python -m typhoon.sources.era5 --recent              # 최근 2개월 다시 받기
  python -m typhoon.sources.era5 --dry-run
"""
import argparse
import datetime as dt
import re
import sys
import time

import numpy as np

from ._env import DATA, require_key

CDS_URL = "https://cds.climate.copernicus.eu/api"
DATASET = "reanalysis-era5-pressure-levels"
OUT = DATA / "env" / "era5"
RAW = OUT / "raw"
AREA = [60, 100, 0, 180]  # [북, 서, 남, 동]
TIMES = ["00:00", "06:00", "12:00", "18:00"]
LEVELS = [850, 500, 200]
G = 9.80665
FIRST_YEAR = 1951
LAG_DAYS = 6          # ERA5 공개 지연(약 5일) + 여유
Z_BLOCK = 3           # z500 은 3년씩 묶어 요청
# 받는 순서(우선순위): 올해 → 2015~ → 1979~2014 → 1951~1978
PRIORITY = [(2015, 9999), (1979, 2014), (1951, 1978)]

GUIDE = """CDS_API_KEY 발급 방법 (ECMWF Copernicus Climate Data Store):
  1) https://cds.climate.copernicus.eu 접속 → Login/Register 로 ECMWF 계정 만들기(무료)
  2) https://cds.climate.copernicus.eu/profile 에서 'Personal Access Token' 복사
  3) https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels?tab=download
     맨 아래 'Terms of use' 에서 라이선스 동의(Accept)
  4) .env 에  CDS_API_KEY=복사한토큰  저장"""

VARNAME = {"u": "u_component_of_wind", "v": "v_component_of_wind", "z": "geopotential"}
FIELDS = {"z500": ("z", 500)}
for _lv in LEVELS:
    FIELDS[f"u{_lv}"] = ("u", _lv)
    FIELDS[f"v{_lv}"] = ("v", _lv)
PACK = {"z500": (0.1, 5500.0)}                 # 값 = 정수 * scale + offset
PACK.update({k: (0.01, 0.0) for k in FIELDS if k != "z500"})


# ---------------------------------------------------------------- 기간·파일
def latest_day():
    return dt.date.today() - dt.timedelta(days=LAG_DAYS)


def year_path(y):
    return OUT / f"era5_{y}.nc"


def block_path(y, m0, m1):
    return OUT / f"era5_{y}_{m0:02d}-{m1:02d}.nc"


def month_path(y, m):
    return OUT / f"era5_{y}{m:02d}.nc"


_PAT = re.compile(r"era5_(\d{4})(?:_(\d{2})-(\d{2})|(\d{2}))?\.nc$")


def files_for_year(y):
    """그 해 저장 파일들(연·묶음 먼저, 월 파일 나중 → 겹치면 월 파일 우선)."""
    big, mon = [], []
    for p in sorted(OUT.glob(f"era5_{y}*.nc")):
        m = _PAT.match(p.name)
        if not m or int(m.group(1)) != y:
            continue
        (mon if m.group(4) else big).append(p)
    return big + mon


def months_covered(y):
    got = set()
    for p in files_for_year(y):
        m = _PAT.match(p.name)
        if m.group(4):
            got.add(int(m.group(4)))
        elif m.group(2):
            got.update(range(int(m.group(2)), int(m.group(3)) + 1))
        else:
            got.update(range(1, 13))
    return got


def month_incomplete(p, y, m):
    """월 파일이 그 달(이번 달이면 latest_day 까지)을 덮지 못하면 True. 읽을 수 없어도 True."""
    import calendar
    last = latest_day()
    if (y, m) > (last.year, last.month):
        return False
    need = last.day if (y, m) == (last.year, last.month) else calendar.monthrange(y, m)[1]
    try:
        import xarray as xr
        with xr.open_dataset(p) as d:
            got = d["time"].values.max()
        return got.astype("datetime64[D]").astype(object).day < need
    except Exception:  # noqa: BLE001
        return True


def build_request(kind, years, months, endday=None, grid=1.0):
    """kind: 'u','v','z' 또는 'all'(세 변수 한 번에, 한 달짜리용)."""
    variables = [VARNAME[k] for k in ("z", "u", "v")] if kind == "all" else [VARNAME[kind]]
    levels = ["500"] if kind == "z" else [str(l) for l in LEVELS]
    days = range(1, (endday or 31) + 1)        # 없는 날짜(2/30 등)는 CDS 가 무시
    return {
        "product_type": ["reanalysis"],
        "variable": variables,
        "pressure_level": levels,
        "year": [str(y) for y in years],
        "month": [f"{m:02d}" for m in months],
        "day": [f"{d:02d}" for d in days],
        "time": TIMES,
        "area": AREA,
        "grid": [grid, grid],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


class Job:
    """CDS 요청 1개 = 원본 파일 1개."""
    def __init__(self, kind, years, months, endday=None):
        self.kind, self.years, self.months, self.endday = kind, list(years), list(months), endday
        ys = f"{years[0]}" if len(years) == 1 else f"{years[0]}-{years[-1]}"
        ms = "" if len(months) == 12 else f"_{months[0]:02d}-{months[-1]:02d}"
        ed = f"_d{endday:02d}" if endday else ""       # 부분 구간 요청은 이름이 달라야 한다
        self.name = f"{kind}_{ys}{ms}{ed}"
        self.raw = RAW / f"{self.name}.nc"


class Target:
    """최종 저장 파일 1개와 그걸 만드는 데 필요한 원본들."""
    def __init__(self, path, year, months, jobs):
        self.path, self.year, self.months, self.jobs = path, year, months, jobs


def plan(start_year, end_year, force_months=()):
    """(targets, jobs) — 최근 기간부터. force_months: 다시 받을 (연, 월) 목록(월 파일로)."""
    last = latest_day()
    end_year = min(end_year, last.year)
    targets, jobs = [], {}

    def job(kind, years, months, endday=None):
        j = Job(kind, years, months, endday)
        return jobs.setdefault(j.name, j)

    force = set(force_months)
    # 1) 월 파일(이번 달, 강제 갱신 달)
    cand = set(force) | {(last.year, last.month)}
    for p in OUT.glob("era5_*.nc"):      # 이미 있는 월 파일 중 달 전체를 못 덮는 것은 다시 받는다
        mm = _PAT.match(p.name)
        if mm and mm.group(4):
            cand.add((int(mm.group(1)), int(mm.group(4))))
    for y, m in sorted(cand, reverse=True):
        if not (start_year <= y <= end_year):
            continue
        p = month_path(y, m)
        if (y, m) in force or not p.exists() or month_incomplete(p, y, m):
            endday = last.day if (y, m) == (last.year, last.month) else None
            targets.append(Target(p, y, [m], [job("all", [y], [m], endday)]))
    if force:                    # --recent: 지정한 달만
        return targets, [j for t in targets for j in t.jobs]
    # 2) 진행 중인 해: 지난달까지 빠진 달들 묶음
    if start_year <= last.year <= end_year and last.month > 1:
        have = months_covered(last.year)
        miss = [m for m in range(1, last.month) if m not in have and (last.year, m) not in force]
        for m0, m1 in _runs(miss):
            ms = list(range(m0, m1 + 1))
            targets.append(Target(block_path(last.year, m0, m1), last.year, ms,
                                  [job(k, [last.year], ms) for k in ("u", "v", "z")]))
    # 3) 지난 해들: 우선순위 구간별로 최근부터, z 는 3년 묶음
    for lo, hi in PRIORITY:
        ys = [y for y in range(min(hi, end_year, last.year - 1), max(lo, start_year) - 1, -1)
              if not months_covered(y)]
        for i in range(0, len(ys), Z_BLOCK):
            blk = sorted(ys[i:i + Z_BLOCK])
            zj = job("z", blk, range(1, 13))
            for y in sorted(blk, reverse=True):
                targets.append(Target(year_path(y), y, list(range(1, 13)),
                                      [job("u", [y], range(1, 13)), job("v", [y], range(1, 13)), zj]))
    # 일부 달만 있는 지난 해(예: 올해였던 해의 남은 달)는 월 파일로
    for y in range(max(start_year, FIRST_YEAR), min(end_year, last.year - 1) + 1):
        have = months_covered(y)
        if have and len(have) < 12:
            for m in range(1, 13):
                if m not in have and (y, m) not in force:
                    targets.append(Target(month_path(y, m), y, [m], [job("all", [y], [m])]))
    order = []
    for t in targets:
        for j in t.jobs:
            if j not in order:
                order.append(j)
    return targets, order


def _runs(ms):
    out = []
    for m in ms:
        if out and m == out[-1][1] + 1:
            out[-1][1] = m
        else:
            out.append([m, m])
    return [tuple(r) for r in out]


# ---------------------------------------------------------------- 후처리
def _open_raw(path):
    import xarray as xr
    ds = xr.open_dataset(path).load()
    tname = "valid_time" if "valid_time" in ds.dims else "time"
    if "expver" in ds.dims:              # ERA5/ERA5T 섞인 경우
        ds = ds.max("expver")
    ds = ds.rename({tname: "time", "latitude": "lat", "longitude": "lon"})
    drop = [c for c in ("number", "expver") if c in ds.coords]
    return ds.drop_vars(drop).sortby("lat").sortby("time")


def pack(target):
    """원본들에서 target 기간만 뽑아 7개 장 int16 압축 저장."""
    import pandas as pd
    import xarray as xr
    arrays = {}
    for j in target.jobs:
        ds = _open_raw(j.raw)
        t = pd.DatetimeIndex(ds["time"].values)
        ds = ds.isel(time=np.where((t.year == target.year) & t.month.isin(target.months))[0])
        for name, (var, lv) in FIELDS.items():
            if var in ds and lv in ds["pressure_level"].values:
                a = ds[var].sel(pressure_level=lv)
                arrays[name] = a / G if name == "z500" else a
    missing = [k for k in FIELDS if k not in arrays]
    if missing:
        raise RuntimeError(f"{target.path.name}: 장 없음 {missing}")
    out = xr.Dataset({k: arrays[k].drop_vars("pressure_level").astype(np.float32) for k in FIELDS})
    out = out.dropna("time", how="all", subset=["z500"])
    enc = {k: {"dtype": "int16", "scale_factor": PACK[k][0], "add_offset": PACK[k][1],
               "_FillValue": -32768, "zlib": True, "complevel": 4} for k in FIELDS}
    tmp = target.path.with_suffix(".tmp.nc")
    out.to_netcdf(tmp, encoding=enc)
    tmp.replace(target.path)
    # 같은 해의 묶음/연 파일에 포함되는 월 파일이 아니면 그대로 둠
    return len(out["time"])


# ---------------------------------------------------------------- 다운로드
# CDS 는 데이터셋별 대기열 수에 한도가 있어 한꺼번에 많이 내면 "Maximum number queued requests"
# 로 거부된다. 그래서 스레드로 막 던지지 않고, 한 루프가 최대 workers 개만 대기열에 올려 두고
# 상태를 보며 끝난 것을 내려받고 다음 것을 낸다. 거부되면 잠시 쉬고 다시 낸다.
# 이미 CDS 에 올라가 있는 같은 요청(이전 실행에서 낸 것)은 새로 내지 않고 이어 받는다.
def _norm(req):
    return {k: sorted(map(str, v)) if isinstance(v, (list, tuple)) else str(v)
            for k, v in req.items()}


def _client(key):
    from ecmwf.datastores import Client
    return Client(url=CDS_URL, key=key, progress=False, maximum_tries=10, retry_after=30)


def _existing(client, log):
    """CDS 에 남아 있는 (대기·실행·완료) 작업: 정규화한 요청 → request_id."""
    out = {}
    try:
        jobs = client.get_jobs(limit=100, sortby="-created", status=["accepted", "running", "successful"])
        for rid in jobs.request_ids:
            try:
                r = client.get_remote(rid)
                out.setdefault(repr(sorted(_norm(r.request).items())), rid)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        log(f"  (기존 작업 목록 실패: {str(e)[:120]})")
    return out


def download(start_year=FIRST_YEAR, end_year=None, force_months=(), workers=4, grid=1.0,
             dry_run=False, log=print, poll=30):
    """빠진 기간을 받아 저장. 실패 목록 반환."""
    end_year = end_year or latest_day().year
    targets, jobs = plan(start_year, end_year, force_months)
    todo = [j for j in jobs if not j.raw.exists()]
    log(f"[era5] {start_year}~{end_year}: 만들 파일 {len(targets)}개, CDS 요청 {len(todo)}개 "
        f"(대기열 최대 {workers}개) → {OUT}")
    if dry_run:
        for j in todo[:12]:
            log("  ", j.name, j.endday or "")
        return []
    if not targets:
        return []
    key = require_key("CDS_API_KEY", GUIDE)
    RAW.mkdir(parents=True, exist_ok=True)
    client = _client(key)
    pending = list(targets)
    queue = list(todo)
    requeued = {}           # job.name -> 격리 후 다시 요청한 횟수
    failed = []

    def quarantine(t, err):
        """pack 실패한 target 의 나쁜 raw 를 .bad 로 치우고 재요청 대상으로 돌려준다."""
        bad = []
        for j in t.jobs:
            if not j.raw.exists():
                continue
            try:
                _open_raw(j.raw)
            except Exception:  # noqa: BLE001
                bad.append(j)
        if not bad:                      # 개별 파일은 멀쩡 → 내용 문제, 전부 다시 받는다
            bad = [j for j in t.jobs if j.raw.exists()]
        out = []
        for j in bad:
            j.raw.replace(j.raw.with_suffix(".bad"))
            requeued[j.name] = requeued.get(j.name, 0) + 1
            if requeued[j.name] > 2:
                failed.append((j.name, f"raw 불량 반복: {err}"[:300]))
                log(f"  포기(불량 raw 반복) {j.name}")
                pending[:] = [x for x in pending if x is not t]
            else:
                out.append(j)
        return out

    def flush():
        """저장 가능한 target 을 포장. 실패한 raw 는 격리하고 다시 요청할 job 목록을 돌려준다."""
        redo = []
        for t in list(pending):
            if all(j.raw.exists() for j in t.jobs):
                try:
                    n = pack(t)
                except Exception as e:  # noqa: BLE001
                    log(f"  포장 실패 {t.path.name}: {str(e)[:150]} → 불량 raw 격리, 재요청")
                    redo += quarantine(t, str(e)[:150])
                    continue
                pending.remove(t)
                log(f"  저장 {t.path.name} ({n}시각)")
        used = {j.raw for t in pending for j in t.jobs}
        for j in jobs:
            if j.raw.exists() and j.raw not in used:
                j.raw.unlink()
        return redo

    queue.extend(j for j in flush() if j not in queue)
    known = _existing(client, log) if queue else {}
    active = {}             # job.name -> (job, remote, 제출 시각)
    t0, done = time.time(), 0
    backoff_until = 0.0
    attempts, qwait = {}, 0
    MAX_TRY, MAX_QWAIT = 5, 30
    # 이미 CDS 에 올라가 있는 것부터 붙잡기(새로 내지 않음)
    for j in list(queue):
        req = build_request(j.kind, j.years, j.months, j.endday, grid)
        rid = known.get(repr(sorted(_norm(req).items())))
        if rid and requeued.get(j.name, 0) == 0:
            active[j.name] = (j, client.get_remote(rid), time.time())
            queue.remove(j)
            log(f"  기존 작업 이어 받기: {j.name}")
    while queue or active:
        now = time.time()
        # 새로 내기
        while queue and len(active) < workers and now >= backoff_until:
            j = queue[0]
            try:
                r = client.submit(DATASET, build_request(j.kind, j.years, j.months, j.endday, grid))
            except Exception as e:  # noqa: BLE001
                text = str(e)
                low = text.lower()
                if any(s in low for s in ("licence", "license", "unauthorized")) or "401" in text:
                    log(f"  [중단] {text[:300]}")
                    return failed + [(j.name, text[:300])]
                if "too large" in low or "cost limit" in low:
                    failed.append((j.name, text[:200]))
                    queue.pop(0)
                    log(f"  실패(요청 큼) {j.name}")
                    continue
                if "queued requests" in low or "maximum number" in low:    # 대기열 한도(남의 작업 포함)
                    qwait += 1
                    if not active and qwait >= MAX_QWAIT:
                        log(f"  [중단] 대기열 한도가 {MAX_QWAIT}회(약 {MAX_QWAIT * 2}분) 계속됨. 기존 CDS 작업이 끝나야 함")
                        return failed + [(x.name, "대기열 한도 지속") for x in queue]
                    backoff_until = time.time() + 120
                    break
                attempts[j.name] = attempts.get(j.name, 0) + 1
                log(f"  제출 오류 {j.name} ({attempts[j.name]}/{MAX_TRY}): {text[:200]}")
                if attempts[j.name] >= MAX_TRY:
                    failed.append((j.name, text[:200]))
                    queue.pop(0)
                    log(f"  포기 {j.name}: 제출 {MAX_TRY}회 실패")
                    continue
                backoff_until = time.time() + 120
                break
            queue.pop(0)
            active[j.name] = (j, r, time.time())
            log(f"  제출 {j.name} (대기열 {len(active)})")
            time.sleep(5)
        time.sleep(poll)
        for name, (j, r, ts) in list(active.items()):
            try:
                st = r.status
            except Exception:  # noqa: BLE001
                continue
            if st == "successful":
                part = j.raw.with_suffix(".part")
                try:
                    r.download(str(part))
                    part.replace(j.raw)
                except Exception as e:  # noqa: BLE001
                    part.unlink(missing_ok=True)
                    log(f"  내려받기 오류 {name}: {str(e)[:150]} (다시 시도)")
                    continue
                del active[name]
                done += 1
                log(f"  [{done}/{len(todo)}] {name} 받음 (제출 후 {time.time() - ts:.0f}초, "
                    f"누적 {time.time() - t0:.0f}초)")
                for rj in flush():
                    queue.insert(0, rj)
            elif st in ("failed", "rejected", "dismissed", "deleted"):
                msg = ""
                try:
                    r.get_results()
                except Exception as e:  # noqa: BLE001
                    msg = str(e).replace("\n", " ")[:300]
                del active[name]
                if "queued requests" in msg.lower():   # 대기열 한도 → 다시 내기
                    queue.insert(0, j)
                    backoff_until = time.time() + 120
                    log(f"  대기열 한도로 거부 {name}, 2분 뒤 다시 냄")
                else:
                    failed.append((name, msg))
                    log(f"  실패 {name}: {msg}")
    for t in pending:
        failed.append((t.path.name, "저장 못 함"))
    log(f"[era5] 받기 끝: {done}개, 실패 {len(failed)}개, {time.time() - t0:.0f}초")
    return failed


def recent_months(n=2):
    last = latest_day()
    y, m, out = last.year, last.month, []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="ERA5 기압면 자료 다운로드(묶음 요청, 동시 제출)")
    ap.add_argument("--start", type=int, default=FIRST_YEAR)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--grid", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--recent", action="store_true", help="최근 2개월 다시 받기")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.recent:
        rm = recent_months()
        failed = download(min(y for y, _ in rm), max(y for y, _ in rm), force_months=rm,
                          workers=a.workers, grid=a.grid, dry_run=a.dry_run)
    else:
        failed = download(a.start, a.end, workers=a.workers, grid=a.grid, dry_run=a.dry_run)
    if failed:
        print("[era5] 실패:", failed, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
