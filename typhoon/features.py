"""한 시점의 태풍 상태로 학습 특성과 정답(도착 여부·남은 시간) 만들기."""
import numpy as np
import pandas as pd

from .data import haversine

HORIZON_H = 120
SEOUL = (37.57, 126.98)
TOKYO = (35.68, 139.69)

FEATURES = ["LAT", "LON", "wind", "pres", "dwind_24", "dlat_6", "dlon_6", "dlat_24", "dlon_24",
            "speed_kmh", "heading", "age_h", "month", "doy",
            "dist_seoul", "bearing_seoul", "dist_tokyo", "bearing_tokyo"]


def bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    x = np.sin(lon2 - lon1) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(lon2 - lon1)
    return (np.degrees(np.arctan2(x, y)) + 360) % 360


def add_features(df):
    df = df.copy()
    g = df.groupby("SID")
    df["wind"] = df.TOKYO_WIND
    df["pres"] = df.TOKYO_PRES
    for h, k in [(6, 1), (24, 4)]:
        df[f"dlat_{h}"] = df.LAT - g.LAT.shift(k)
        df[f"dlon_{h}"] = df.LON - g.LON.shift(k)
    df["dwind_24"] = df.wind - g.wind.shift(4)
    prev_lat, prev_lon = g.LAT.shift(1), g.LON.shift(1)
    df["speed_kmh"] = haversine(prev_lat, prev_lon, df.LAT, df.LON) / 6
    df["heading"] = bearing(prev_lat, prev_lon, df.LAT, df.LON)
    df["age_h"] = (df.time - g.time.transform("min")).dt.total_seconds() / 3600
    df["month"] = df.time.dt.month
    df["doy"] = df.time.dt.dayofyear
    df["dist_seoul"] = haversine(df.LAT, df.LON, *SEOUL)
    df["bearing_seoul"] = bearing(df.LAT, df.LON, *SEOUL)
    df["dist_tokyo"] = haversine(df.LAT, df.LON, *TOKYO)
    df["bearing_tokyo"] = bearing(df.LAT, df.LON, *TOKYO)
    return df


def add_targets(df, country):
    """각 시점에서 해당 나라 영향권에 처음 들어오기까지 남은 시간. 이미 들어온 뒤 시점은 제외."""
    df = df.copy()
    first_hit = df[df[f"in_{country}"]].groupby("SID").time.min()
    df["arrival"] = df.SID.map(first_hit)
    df["hours_to"] = (df.arrival - df.time).dt.total_seconds() / 3600
    df = df[df.arrival.isna() | (df.hours_to > 0)].copy()
    df["hit"] = (df.hours_to <= HORIZON_H).astype(int)
    return df
