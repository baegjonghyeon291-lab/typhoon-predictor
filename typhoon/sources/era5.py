"""ERA5 기압면 자료 받기 (ECMWF Copernicus Climate Data Store, 새 CDS).

받는 것: 하루 4회(00/06/12/18 UTC)
  - z500  : 500hPa 지위(geopotential)
  - u, v  : 850 / 500 / 200 hPa 바람
영역: 북위 0~60, 동경 100~180.  기간: 기본 1951년 ~ 지난달 (ERA5 자체는 1940년부터 있음).
저장: data/env/era5/z500_YYYYMM.nc, data/env/era5/uv_YYYYMM.nc  (월 단위, 이미 있으면 건너뜀)

필요 패키지: cdsapi 0.7.7 이상
  설치:  .venv/bin/pip install "cdsapi>=0.7.7"

키: .env 의 CDS_API_KEY (CDS 개인 액세스 토큰, 예전 'UID:키' 형식이 아님)
주의: 키가 있어도 ERA5 pressure-levels 데이터셋 페이지에서 라이선스(이용약관) 동의를
      한 번 해야 다운로드된다. 안 하면 "required licences not accepted" 오류가 난다.

실행 예:
  python -m typhoon.sources.era5                   # 1951-01 ~ 지난달 전부
  python -m typhoon.sources.era5 --start 2020 --end 2020
  python -m typhoon.sources.era5 --start 2023-08 --end 2023-09 --grid 0.25
  python -m typhoon.sources.era5 --dry-run         # 요청 목록만 출력
"""
import argparse
import calendar
import datetime as dt
import sys

from ._env import DATA, require_key

CDS_URL = "https://cds.climate.copernicus.eu/api"
DATASET = "reanalysis-era5-pressure-levels"
OUT = DATA / "env" / "era5"
AREA = [60, 100, 0, 180]  # [북, 서, 남, 동]
TIMES = ["00:00", "06:00", "12:00", "18:00"]

GUIDE = """CDS_API_KEY 발급 방법 (ECMWF Copernicus Climate Data Store):
  1) https://cds.climate.copernicus.eu 접속 → 오른쪽 위 Login/Register 로 ECMWF 계정 만들기(무료)
  2) 로그인 후 https://cds.climate.copernicus.eu/profile 에서 'Personal Access Token' 복사
  3) https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels?tab=download
     맨 아래 'Terms of use' 에서 라이선스 동의(Accept) 한 번 하기
  4) .env 에  CDS_API_KEY=복사한토큰  저장"""

# 요청 묶음: 파일 접두어 -> (변수, 기압면)
REQUESTS = {
    "z500": (["geopotential"], ["500"]),
    "uv": (["u_component_of_wind", "v_component_of_wind"], ["200", "500", "850"]),
}


def parse_ym(s, end=False):
    parts = s.split("-")
    y = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else (12 if end else 1)
    return y, m


def month_range(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def last_complete_month():
    today = dt.date.today()
    first = today.replace(day=1)
    prev = first - dt.timedelta(days=1)
    return prev.year, prev.month


def build_request(year, month, variables, levels, grid):
    ndays = calendar.monthrange(year, month)[1]
    req = {
        "product_type": ["reanalysis"],
        "variable": variables,
        "pressure_level": levels,
        "year": [str(year)],
        "month": [f"{month:02d}"],
        "day": [f"{d:02d}" for d in range(1, ndays + 1)],
        "time": TIMES,
        "area": AREA,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    if grid and grid != 0.25:
        req["grid"] = [grid, grid]
    return req


def main(argv=None):
    ap = argparse.ArgumentParser(description="ERA5 기압면 자료 월 단위 다운로드")
    ap.add_argument("--start", default="1951", help="시작 연 또는 연-월 (기본 1951)")
    ap.add_argument("--end", default=None, help="끝 연 또는 연-월 (기본 지난달)")
    ap.add_argument("--grid", type=float, default=1.0,
                    help="격자 간격(도). 기본 1.0 (원본 0.25 는 월당 수백MB, 전체 100GB 이상)")
    ap.add_argument("--only", choices=list(REQUESTS), help="z500 또는 uv 한 가지만")
    ap.add_argument("--dry-run", action="store_true", help="요청 목록만 보여 주고 받지 않음")
    a = ap.parse_args(argv)

    start = parse_ym(a.start)
    end = parse_ym(a.end, end=True) if a.end else last_complete_month()
    end = min(end, last_complete_month())
    kinds = [a.only] if a.only else list(REQUESTS)

    todo = []
    for y, m in month_range(start, end):
        for k in kinds:
            path = OUT / f"{k}_{y}{m:02d}.nc"
            if not path.exists():
                todo.append((y, m, k, path))
    print(f"ERA5 {start[0]}-{start[1]:02d} ~ {end[0]}-{end[1]:02d}, 격자 {a.grid}°, "
          f"받을 파일 {len(todo)}개 (이미 있는 파일은 건너뜀) → {OUT}")
    if a.dry_run:
        for y, m, k, path in todo[:12]:
            print(" ", path.name, build_request(y, m, *REQUESTS[k], a.grid))
        if len(todo) > 12:
            print(f"  ... 외 {len(todo) - 12}개")
        return
    if not todo:
        return

    key = require_key("CDS_API_KEY", GUIDE)
    try:
        import cdsapi
    except ImportError:
        print("cdsapi 가 없습니다. 설치:  .venv/bin/pip install \"cdsapi>=0.7.7\"", file=sys.stderr)
        sys.exit(2)

    OUT.mkdir(parents=True, exist_ok=True)
    client = cdsapi.Client(url=CDS_URL, key=key, quiet=False)
    for i, (y, m, k, path) in enumerate(todo, 1):
        req = build_request(y, m, *REQUESTS[k], a.grid)
        tmp = path.with_suffix(".nc.part")
        print(f"[{i}/{len(todo)}] {path.name} 요청 중 (CDS 대기열에 따라 수 분~수십 분 걸릴 수 있음)")
        try:
            client.retrieve(DATASET, req).download(str(tmp))
        except Exception as e:  # noqa: BLE001
            text = str(e)
            if tmp.exists():
                tmp.unlink()
            low = text.lower()
            if "licence" in low or "license" in low:
                sys.exit("\n[라이선스 미동의] 아래 페이지 맨 아래에서 Terms of use 에 동의한 뒤 다시 실행하세요:\n"
                         "  https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels?tab=download")
            if "401" in text or "authentication" in low or "unauthorized" in low:
                sys.exit("\n[인증 실패] .env 의 CDS_API_KEY 가 맞는지 확인하세요 "
                         "(https://cds.climate.copernicus.eu/profile 의 Personal Access Token).")
            raise
        tmp.rename(path)
    print("완료")


if __name__ == "__main__":
    main()
