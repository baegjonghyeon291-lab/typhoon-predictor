"""기상청 API허브 종관기상관측(ASOS) 일자료 (kma_sfcdd3.php, 키: .env 의 KMA_APIHUB_KEY).

서리 전용 항목은 없어서 일 최저 초상온도(TG_MIN) 0℃ 이하를 서리 가능 일로 본다.

    python -m typhoon.sources.kma_asos            # 1973년~어제, 주요 6개 지점
"""
import argparse
import io
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from ._env import require_key

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "kma" / "asos"
URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php"
STATIONS = {108: "서울", 159: "부산", 156: "광주", 184: "제주", 105: "강릉", 133: "대전"}
COLS = ["TM", "STN", "WS_AVG", "WR_DAY", "WD_MAX", "WS_MAX", "WS_MAX_TM", "WD_INS", "WS_INS", "WS_INS_TM",
        "TA_AVG", "TA_MAX", "TA_MAX_TM", "TA_MIN", "TA_MIN_TM", "TD_AVG", "TS_AVG", "TG_MIN", "HM_AVG", "HM_MIN",
        "HM_MIN_TM", "PV_AVG", "EV_S", "EV_L", "FG_DUR", "PA_AVG", "PS_AVG", "PS_MAX", "PS_MAX_TM", "PS_MIN",
        "PS_MIN_TM", "CA_TOT", "SS_DAY", "SS_DUR", "SS_CMB", "SI_DAY", "SI_60M_MAX", "SI_60M_MAX_TM", "RN_DAY",
        "RN_D99", "RN_DUR", "RN_60M_MAX", "RN_60M_MAX_TM", "RN_10M_MAX", "RN_10M_MAX_TM", "RN_POW_MAX",
        "RN_POW_MAX_TM", "SD_NEW", "SD_NEW_TM", "SD_MAX", "SD_MAX_TM", "TE_05", "TE_10", "TE_15", "TE_30", "TE_50"]


def fetch(key, stn, year, end):
    cache = OUT / "raw" / f"{stn}_{year}.txt"
    if cache.exists() and year < end.year:
        return cache.read_text()
    tm2 = min(date(year, 12, 31), end)
    for i in range(4):
        r = requests.get(URL, params={"tm1": f"{year}0101", "tm2": tm2.strftime("%Y%m%d"), "stn": stn,
                                      "help": 0, "authKey": key}, timeout=120)
        if r.status_code == 200 and "#START7777" in r.text:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(r.text)
            return r.text
        time.sleep(2 ** i)
    raise RuntimeError(f"{stn} {year} 실패: HTTP {r.status_code}")


def parse(text):
    rows = [l for l in text.splitlines() if l and not l.startswith("#")]
    df = pd.read_csv(io.StringIO("\n".join(rows)), sep=r"\s+", header=None, names=COLS, usecols=range(len(COLS)))
    df = df.replace([-9, -9.0, -99.0, -99.9], pd.NA)
    df["TM"] = pd.to_datetime(df.TM.astype(str), format="%Y%m%d")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1973)
    a = ap.parse_args()
    key = require_key("KMA_APIHUB_KEY", "기상청 API허브(https://apihub.kma.go.kr)에서 지상관측 > 종관기상관측 일자료(기간 조회) 활용신청 후 인증키를 넣으세요.")
    end = date.today() - timedelta(days=1)
    frames = []
    for stn in STATIONS:
        for year in range(a.start, end.year + 1):
            frames.append(parse(fetch(key, stn, year, end)))
            time.sleep(0.3)
        print(f"[asos] {STATIONS[stn]}({stn}) 완료", flush=True)
    df = pd.concat(frames, ignore_index=True)
    df["stn_name"] = df.STN.map(STATIONS)
    df["frost"] = (pd.to_numeric(df.TG_MIN, errors="coerce") <= 0).astype("Int8").where(df.TG_MIN.notna())
    df.to_csv(OUT / "asos_daily.csv", index=False)
    print(f"[asos] {len(df)}행 {df.TM.min():%Y-%m-%d}~{df.TM.max():%Y-%m-%d} → {OUT / 'asos_daily.csv'}")


if __name__ == "__main__":
    main()
