"""한 시점의 태풍 상태로 학습 특성과 정답(도착 여부·남은 시간) 만들기."""
import numpy as np
import pandas as pd

from .data import haversine, ROOT

HORIZON_H = 120
SEOUL = (37.57, 126.98)
TOKYO = (35.68, 139.69)

FEATURES = ["LAT", "LON", "wind", "pres", "dwind_24", "dlat_6", "dlon_6", "dlat_24", "dlon_24",
            "speed_kmh", "heading", "age_h", "month", "doy",
            "dist_seoul", "bearing_seoul", "dist_tokyo", "bearing_tokyo"]


ENV_FILES = ["atmos_features.parquet", "ocean_features.parquet"]
ENV_PREFIXES = ("atm_", "ocn_")
# 환경 모델에 쓰는 특성. 해수온·기후 지수와 500hPa 고도 절대값은 기후 추세로 기준이 바뀌어
# 2015년 이후 검증 성능을 떨어뜨려서 빼고, 지향류와 고기압의 상대 위치만 쓴다(reports/env_experiment.txt).
ENV_FEATURES = ["atm_u_steer", "atm_v_steer", "atm_u_steer_24", "atm_v_steer_24", "atm_shear",
                "atm_dz_ew", "atm_dz_n", "atm_dz_max", "atm_ridge_dlon", "atm_ridge_dlat"]


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


def add_env(df):
    """data/env/ 의 대기·해양 특성(env_atmos, env_ocean 출력)을 시점별로 붙인다. 없으면 그대로."""
    for f in ENV_FILES:
        p = ROOT / "data" / "env" / f
        if p.exists():
            env = pd.read_parquet(p)
            df = df.merge(env.drop_duplicates(["SID", "time"]), on=["SID", "time"], how="left")
    if "atm_z500" in df:
        df["atm_dz_ew"] = df.atm_z500_e - df.atm_z500_w
        df["atm_dz_n"] = df.atm_z500_n - df.atm_z500
        df["atm_dz_max"] = df.atm_z500_max - df.atm_z500
        df["atm_ridge_dlon"] = df.atm_ridge_west - df.LON
        df["atm_ridge_dlat"] = df.atm_ridge_lat - df.LAT
    return df


def env_columns(df):
    return [c for c in ENV_FEATURES if c in df.columns]


def model_features(m):
    """모델에 저장된 특성 목록. 예전 모델은 경로 특성만 쓴다."""
    return m.get("features", FEATURES)


def load_model(country, full=True):
    """환경 특성 모델(_full)이 있으면 그것, 없으면 경로만 쓰는 모델."""
    import joblib
    p = ROOT / "models" / f"{country}_full.joblib"
    if full and p.exists():
        return joblib.load(p)
    return joblib.load(ROOT / "models" / f"{country}.joblib")


def predict(country, d):
    """도달 확률과 남은 시간. 환경 자료가 있는 시점은 환경 모델, 없으면(최근 재분석 미갱신 등) 경로 모델."""
    track = load_model(country, full=False)
    p = track["clf"].predict_proba(d[FEATURES])[:, 1]
    h = track["reg"].predict(d[FEATURES])
    full = load_model(country)
    feats = model_features(full)
    env = [c for c in ENV_FEATURES if c in feats]
    if env:
        has = d[env].notna().any(axis=1).to_numpy()
        if has.any():
            p[has] = full["clf"].predict_proba(d.loc[has, feats])[:, 1]
            h[has] = full["reg"].predict(d.loc[has, feats])
    return p, h
