"""IBTrACS 서태평양 태풍 이력 로드와 정리."""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "ibtracs_wp.csv"
URL = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
       "v04r01/access/csv/ibtracs.WP.list.v04r01.csv")

# 영향권 판정 기준 도시와 반경(km)
CITIES = {
    "korea": [(37.57, 126.98), (35.18, 129.08), (35.16, 126.85), (33.50, 126.53),
              (37.75, 128.90), (36.35, 127.38)],
    "japan": [(33.59, 130.40), (31.60, 130.56), (34.69, 135.50), (35.68, 139.69),
              (38.27, 140.87), (43.06, 141.35), (26.21, 127.68), (33.56, 133.53),
              (37.90, 139.00)],
}
RADIUS_KM = 200

COLS = ["SID", "SEASON", "NAME", "ISO_TIME", "LAT", "LON", "DIST2LAND",
        "TOKYO_LAT", "TOKYO_GRADE", "TOKYO_WIND", "TOKYO_PRES", "KMA_LAT", "KMA_WIND", "KMA_PRES"]


def download():
    import urllib.request
    RAW.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(URL, RAW)


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def min_dist(lat, lon, country):
    return np.min([haversine(lat, lon, a, b) for a, b in CITIES[country]], axis=0)


def load(start_year=1951):
    if not RAW.exists():
        download()
    df = pd.read_csv(RAW, skiprows=[1], usecols=COLS, low_memory=False, na_values=[" ", ""])
    df["time"] = pd.to_datetime(df["ISO_TIME"])
    df = df[(df.SEASON >= start_year) & (df.time.dt.hour % 6 == 0)].copy()
    for c in ["LAT", "LON", "DIST2LAND", "TOKYO_LAT", "TOKYO_WIND", "TOKYO_PRES", "KMA_LAT", "KMA_WIND", "KMA_PRES"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(["SID", "time"]).reset_index(drop=True)
    for c in CITIES:
        df[f"dist_{c}"] = min_dist(df.LAT.values, df.LON.values, c)
        df[f"in_{c}"] = df[f"dist_{c}"] <= RADIUS_KM
    return df
