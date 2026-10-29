"""IBTrACS 지연(1~2일)을 메우는 실시간 보충 — 일본 기상청(JMA) 태풍 해석·예보 XML의 실황(analysis) 점.

출처: 気象庁防災情報XML (気象庁ホームページ, https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml) — 가공하여 사용.
키 불필요. 피드에는 최근 약 7일치만 있으므로 data/jma/xml/ 에 받은 전문을 계속 쌓아 쓴다.
실패(접속·파싱)하면 예외 대신 경고만 내고 이미 저장된 전문으로 이어가거나 빈 DataFrame을 돌려준다.
"""
import time
import warnings
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .data import CITIES, RADIUS_KM, haversine, min_dist  # noqa: F401
from .sources import jma

GRADE = {"TD": 2.0, "TS": 3.0, "STS": 4.0, "TY": 5.0, "LOW": 6.0}   # IBTrACS TOKYO_GRADE 코드
OUT_COLS = ["SID", "SEASON", "NAME", "ISO_TIME", "LAT", "LON", "DIST2LAND", "TOKYO_LAT", "TOKYO_GRADE",
            "TOKYO_WIND", "TOKYO_PRES", "KMA_LAT", "KMA_WIND", "KMA_PRES", "time",
            "dist_korea", "in_korea", "dist_japan", "in_japan"]


def _warn(msg):
    warnings.warn(f"[realtime] {msg}", RuntimeWarning, stacklevel=2)


def _download_new():
    """피드의 새 전문을 data/jma/xml 에 저장. 실패하면 경고만."""
    import requests
    jma.XML_DIR.mkdir(parents=True, exist_ok=True)
    try:
        entries = jma.fetch_feed()
    except Exception as e:  # noqa: BLE001
        _warn(f"JMA 피드 접속/파싱 실패: {type(e).__name__}")
        return
    for _, url in entries:
        p = jma.XML_DIR / url.rsplit("/", 1)[-1]
        if p.exists():
            continue
        try:
            r = requests.get(url, headers=jma.UA, timeout=30)
            if r.status_code == 200:
                p.write_bytes(r.content)
                time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            _warn(f"전문 받기 실패({p.name}): {type(e).__name__}")


def _analysis_rows():
    rows = []
    for p in sorted(jma.XML_DIR.glob("*.xml")):
        try:
            rows += [r for r in jma.parse_report(p) if r["lead_h"] == 0]
        except Exception:  # noqa: BLE001
            _warn(f"전문 파싱 실패: {p.name}")
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    d = d[(d["info_type"] != "取消") & d["lat"].notna() & d["lon"].notna()].copy()
    # 같은 태풍의 EventID 에서 번호가 한 번이라도 나온 경우 모든 발표에 번호를 채움
    num = d.dropna(subset=["typhoon_number"]).groupby("event_id")["typhoon_number"].last()
    d["typhoon_number"] = d["event_id"].map(num)
    nm = d.dropna(subset=["name"]).groupby("event_id")["name"].last()
    d["name"] = d["event_id"].map(nm)
    d = d.dropna(subset=["typhoon_number"])
    d["time"] = pd.to_datetime(d["valid_time"], utc=True).dt.tz_convert("UTC").dt.tz_localize(None)
    d = d[d["time"].dt.hour % 6 == 0]
    d["rt"] = pd.to_datetime(d["report_time"], utc=True)
    d = d.sort_values("rt").drop_duplicates(["typhoon_number", "time"], keep="last")  # 최신 발표 우선
    return d


def _match_by_track(ib, g, max_km=250.0, gap_h=12):
    """이름이 달라도 같은 태풍인지 위치·시각으로 판정: JMA 점과 같은 시각(없으면 직전 gap_h 안)의
    IBTrACS 점이 max_km 안에 있으면 그 태풍의 SID."""
    tmin = g["time"].min()
    near = ib[(ib["time"] >= tmin - pd.Timedelta(hours=gap_h)) & (ib["time"] <= g["time"].max())]
    if near.empty:
        return None
    best, best_km = None, max_km
    for sid, p in near.groupby("SID"):
        for _, r in g.iterrows():
            q = p[(p["time"] - r["time"]).abs() <= pd.Timedelta(hours=gap_h)]
            if q.empty:
                continue
            km = float(haversine(q["LAT"].values, q["LON"].values, r["lat"], r["lon"]).min())
            if km < best_km:
                best, best_km = sid, km
    return best


def fetch_recent(ibtracs_df, download=True):
    """load() 결과(ibtracs_df)와 같은 컬럼의 JMA 실시간 보충 행(IBTrACS 마지막 시각 이후분만)."""
    try:
        if download:
            _download_new()
        d = _analysis_rows()
        if d.empty:
            _warn("JMA 실황 자료 없음 — 빈 결과")
            return ibtracs_df.iloc[0:0].copy()
        ib = ibtracs_df
        last = ib.groupby("SID").agg(NAME=("NAME", "last"), SEASON=("SEASON", "last"),
                                      t0=("time", "min"), t1=("time", "max"))
        out = []
        for num, g in d.groupby("typhoon_number"):
            name = str(g["name"].dropna().iloc[-1]).upper() if g["name"].notna().any() else ""
            tmin, tmax = g["time"].min(), g["time"].max()
            year = 2000 + int(str(num)[:2])
            cand = last[(last["NAME"].str.upper() == name) &
                        (last["t1"] >= tmin - pd.Timedelta(days=10)) & (last["t0"] <= tmax)]
            if not len(cand):
                sid_t = _match_by_track(ib, g)       # IBTrACS 에 아직 NOT_NAMED 등으로 남은 경우 위치·시각으로
                if sid_t is not None:
                    cand = last.loc[[sid_t]]
            if len(cand):
                sid = cand.sort_values("t1").index[-1]
                season = int(last.loc[sid, "SEASON"])
                have = set(ib.loc[ib["SID"] == sid, "time"])
                g = g[~g["time"].isin(have)]
                # IBTrACS 가 끝난 뒤의 시각만 이어 붙임(중간 구멍은 메우지 않음)
                g = g[g["time"] > last.loc[sid, "t1"]]
            else:
                sid = f"RT{year}{str(num)[2:]}"
                season = year
            if g.empty:
                continue
            gr = g["class"].str.extract(r"\((\w+)\)")[0].map(GRADE)
            out.append(pd.DataFrame({
                "SID": sid, "SEASON": season, "NAME": name or "UNNAMED",
                "LAT": g["lat"].values, "LON": g["lon"].values,
                "TOKYO_LAT": g["lat"].values, "TOKYO_GRADE": gr.values,
                "TOKYO_WIND": g["max_wind_kt"].values, "TOKYO_PRES": g["pressure_hpa"].values,
                "time": g["time"].values}))
        if not out:
            return ib.iloc[0:0].copy()
        r = pd.concat(out, ignore_index=True)
        r["time"] = pd.to_datetime(r["time"])
        r["ISO_TIME"] = r["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
        # 육지까지 거리: 해석 자료에 없어 IBTrACS 전체에서 가장 가까운 점의 값으로 근사
        xyz = lambda la, lo: np.c_[np.cos(np.radians(la)) * np.cos(np.radians(lo)),  # noqa: E731
                                   np.cos(np.radians(la)) * np.sin(np.radians(lo)), np.sin(np.radians(la))]
        from scipy.spatial import cKDTree
        _, ix = cKDTree(xyz(ib.LAT.values, ib.LON.values)).query(xyz(r.LAT.values, r.LON.values))
        r["DIST2LAND"] = ib["DIST2LAND"].values[ix].astype("int64")
        for c in ["KMA_LAT", "KMA_WIND", "KMA_PRES"]:
            r[c] = np.nan
        for c in CITIES:
            r[f"dist_{c}"] = min_dist(r.LAT.values, r.LON.values, c)
            r[f"in_{c}"] = r[f"dist_{c}"] <= RADIUS_KM
        r["SEASON"] = r["SEASON"].astype("int64")
        r = r[OUT_COLS].drop_duplicates(["SID", "time"]).sort_values(["SID", "time"]).reset_index(drop=True)
        return r
    except Exception as e:  # noqa: BLE001
        _warn(f"실시간 보충 실패: {type(e).__name__}: {e}")
        return ibtracs_df.iloc[0:0].copy()


def merge(ibtracs_df, **kw):
    """IBTrACS 뒤에 JMA 보충분을 이어 붙인 전체 표(중복 없음, SID·time 정렬)."""
    r = fetch_recent(ibtracs_df, **kw)
    if r.empty:
        return ibtracs_df
    m = pd.concat([ibtracs_df, r], ignore_index=True)
    # IBTrACS 에 아직 NOT_NAMED 로 남은 태풍도 위치로 맞췄다면 이름을 JMA 쪽으로 통일(예보 원뿔은 이름으로 붙는다)
    for sid, g in r.groupby("SID"):
        jma_name = str(g["NAME"].iloc[-1]).upper()
        if jma_name and jma_name != "UNNAMED":
            m.loc[m["SID"] == sid, "NAME"] = jma_name
    return m.drop_duplicates(["SID", "time"], keep="first").sort_values(["SID", "time"]).reset_index(drop=True)
