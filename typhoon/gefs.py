"""GEFS 앙상블(30개 + 대조 1개)로 전향 확률 실험 (PRD P0, 참피 같은 급전향을 일찍 잡을 수 있는지).

AWS noaa-gefs-pds(키 없음)의 0.5° pgrb2a 파일에서 해면기압(PRMSL, 파일 마지막 메시지)만 .idx 로 바이트 범위를 받아
1° 격자(0~55N, 95~185E, gfs_track 과 같은 격자)로 줄여 data/env/gefs/{발표}/{멤버}.npz 로 캐시한다(0~60시간, 6시간 간격).
멤버마다 gfs_track.track_cycle 로 중심을 추적하고, 시점 t의 48시간 방향 변화가 45도 이상인 멤버 비율을 전향 확률로 본다.

    python -m typhoon.gefs champi     # 참피(2026) 시범 → reports/gefs_champi.txt
"""
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .data import ROOT
from .gfs_track import AVAIL_H, LAT, LON, cycle_for, track_cycle

DIR = ROOT / "data" / "env" / "gefs"
URL = "https://noaa-gefs-pds.s3.amazonaws.com/gefs.{c:%Y%m%d}/{c:%H}/atmos/pgrb2ap5/{m}.t{c:%H}z.pgrb2a.0p50.f{f:03d}"
MEMBERS = ["gec00", *[f"gep{i:02d}" for i in range(1, 31)]]
LEADS = list(range(0, 133, 6))      # 0~132시간(2~4일 뒤 전향까지 보려고)
TURN_DEG, TURN_H = 45, 48
_ECC = threading.Lock()


def _decode(msg):
    import eccodes
    with _ECC:
        h = eccodes.codes_new_from_message(msg)
        try:
            name = eccodes.codes_get(h, "shortName")
            ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
            v = eccodes.codes_get_values(h).reshape(nj, ni)
        finally:
            eccodes.codes_release(h)
    assert name == "prmsl" and (ni, nj) == (720, 361), (name, ni, nj)
    v = v[::2, ::2]                                   # 0.5° → 1° (정수 도 격자점)
    y0 = int(90 - LAT[-1])
    return v[y0:y0 + len(LAT), int(LON[0]):int(LON[0]) + len(LON)][::-1].astype(np.float32) / 100


def _fetch(sess, c, m, f):
    url = URL.format(c=c, m=m, f=f)
    r = sess.get(url + ".idx", timeout=60)
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    lines = r.text.strip().splitlines()
    i = next((k for k, l in enumerate(lines) if ":PRMSL:mean sea level:" in l), None)
    if i is None:
        return None
    a = int(lines[i].split(":")[1])
    b = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else ""
    return _decode(sess.get(url, headers={"Range": f"bytes={a}-{b}"}, timeout=60).content)


def _path(c, m):
    return DIR / f"{c:%Y%m%d%H}" / f"{m}.npz"


def fetch_member(sess, c, m):
    p = _path(c, m)
    if p.exists() and len(np.load(p)["p"]) == len(LEADS):
        return True
    out = np.full((len(LEADS), len(LAT), len(LON)), np.nan, np.float32)
    for i, f in enumerate(LEADS):
        for k in range(3):
            try:
                v = _fetch(sess, c, m, f)
                break
            except Exception:  # noqa: BLE001  받기 끊김·잘린 GRIB 은 다시 받는다
                v = None
                continue
        if v is None:
            if f == 0:
                return False
            continue
        out[i] = v
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(p, p=out)
    return True


def fetch_cycles(cycles, workers=16):
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=workers * 2))
    jobs = [(c, m) for c in cycles for m in MEMBERS]
    with ThreadPoolExecutor(workers) as ex:
        ok = list(ex.map(lambda j: fetch_member(sess, *j), jobs))
    print(f"[gefs] 발표 {len(cycles)}개 × 멤버 {len(MEMBERS)}: 성공 {sum(ok)}/{len(jobs)}")


def member_tracks(c, lat0, lon0):
    """발표 c의 멤버별 중심 경로 {멤버: (리드 배열, 위도, 경도)} (발표 시각 위치에서 시작)."""
    out = {}
    for m in MEMBERS:
        if not _path(c, m).exists():
            continue
        tr = track_cycle(np.load(_path(c, m))["p"], lat0, lon0 % 360, LEADS)
        if len(tr) >= 2:
            out[m] = (np.array([x[0] for x in tr], float), np.array([x[1] for x in tr]), np.array([x[2] for x in tr]))
    return out


def late_turn(g, t, h0=48, h1=96):
    """시점 t에서 멤버별 'h0→h1시간 구간 진행 방향'과 지금 방향의 차이 → (45도 이상 비율, 멤버 수, 중앙값).
    48시간 안 전향 정의로는 잡히지 않는 2~4일 뒤 전향을 본다."""
    from .features import bearing
    c = cycle_for(t)
    at_c, now, prev = g[g.time == c], g[g.time == t], g[g.time == t - pd.Timedelta(hours=24)]
    if at_c.empty or now.empty or prev.empty:
        return None
    d = (t - c) / pd.Timedelta(hours=1)
    h_now = bearing(prev.LAT.iloc[0], prev.LON.iloc[0], now.LAT.iloc[0], now.LON.iloc[0])
    turns = []
    for leads, la, lo in member_tracks(c, float(at_c.LAT.iloc[0]), float(at_c.LON.iloc[0])).values():
        if leads[-1] < d + h1:
            continue
        q = lambda h: (np.interp(d + h, leads, la), np.interp(d + h, leads, lo))   # noqa: E731
        turns.append(float((bearing(*q(h0), *q(h1)) - h_now + 180) % 360 - 180))
    if not turns:
        return None
    turns = np.array(turns)
    return float((np.abs(turns) >= TURN_DEG).mean()), len(turns), float(np.median(turns))


def turn_probability(g, t):
    """한 태풍(g: 시간순 시점 표)의 시점 t: 그때 공개된 발표의 멤버 중 48시간 방향 변화 45도 이상인 비율.
    → (확률, 쓸 수 있는 멤버 수, 방향 변화 중앙값)."""
    from .features import bearing
    c = cycle_for(t)
    at_c, now, prev = g[g.time == c], g[g.time == t], g[g.time == t - pd.Timedelta(hours=24)]
    if at_c.empty or now.empty or prev.empty:
        return None
    d = (t - c) / pd.Timedelta(hours=1)
    h_now = bearing(prev.LAT.iloc[0], prev.LON.iloc[0], now.LAT.iloc[0], now.LON.iloc[0])
    turns = []
    for leads, la, lo in member_tracks(c, float(at_c.LAT.iloc[0]), float(at_c.LON.iloc[0])).values():
        if leads[-1] < d + TURN_H:
            continue
        y0, x0 = np.interp(d, leads, la), np.interp(d, leads, lo)
        y1, x1 = np.interp(d + TURN_H, leads, la), np.interp(d + TURN_H, leads, lo)
        turns.append(float((bearing(y0, x0, y1, x1) - h_now + 180) % 360 - 180))
    if not turns:
        return None
    turns = np.array(turns)
    return float((np.abs(turns) >= TURN_DEG).mean()), len(turns), float(np.median(turns))


LIVE_BUDGET_S = 420     # 자동 갱신(30분 제한) 안에서 앙상블 받기에 쓰는 최대 시간


def live_turn(g):
    """활동 중인 태풍(g: 시간순 시점 표)의 마지막 시점 전향 확률. 그 시점에 공개된 발표 하나만 받는다(멤버 31 × 23개).
    → {"cycle", "members", "p48", "p96"} (p48: 48시간 안 45도 이상, p96: 48→96시간 구간 45도 이상) 또는 None."""
    import time
    t = g.time.iloc[-1]
    c = cycle_for(t)
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=32))
    end = time.monotonic() + LIVE_BUDGET_S
    with ThreadPoolExecutor(16) as ex:
        futs = [ex.submit(lambda m: time.monotonic() < end and fetch_member(sess, c, m), m) for m in MEMBERS]
        [f.result() for f in futs]
    a, b = turn_probability(g, t), late_turn(g, t)
    if not a and not b:
        return None
    return {"cycle": c.strftime("%Y-%m-%dT%H"), "members": max(a[1] if a else 0, b[1] if b else 0),
            "p48": round(a[0], 2) if a else None, "p96": round(b[0], 2) if b else None}


def champi():
    from .data import load
    from .features import add_features
    df = add_features(load())
    g = df[df.SID == "2026288N10161"].sort_values("time")
    times = g[(g.time >= "2026-10-18") & (g.time <= "2026-10-24")].time
    cycles = sorted({cycle_for(t) for t in times})
    fetch_cycles(cycles)
    lines = ["참피(2026) GEFS 앙상블 전향 확률(48시간 방향 변화 45도 이상인 멤버 비율)", "시각(KST)    전향 확률  멤버 수  방향 변화 중앙값   실제 48시간 방향 변화"]
    from .features import bearing
    for t in times:
        r = turn_probability(g, t)
        fut, prev = g[g.time == t + pd.Timedelta(hours=TURN_H)], g[g.time == t - pd.Timedelta(hours=24)]
        now = g[g.time == t]
        real = ""
        if len(fut) and len(prev):
            h0 = bearing(prev.LAT.iloc[0], prev.LON.iloc[0], now.LAT.iloc[0], now.LON.iloc[0])
            h1 = bearing(now.LAT.iloc[0], now.LON.iloc[0], fut.LAT.iloc[0], fut.LON.iloc[0])
            real = f"{(h1 - h0 + 180) % 360 - 180:+.0f}°"
        lines.append(f"  {(t + pd.Timedelta(hours=9)):%m-%d %H시}   "
                     + (f"{r[0]:>6.0%}   {r[1]:>5}   {r[2]:>+8.0f}°" if r else "     –        –           –")
                     + f"          {real}")
    lines += ["", "2~4일 뒤 전향: 48→96시간 구간 진행 방향이 지금보다 45도 이상 다른 멤버 비율",
              "시각(KST)    전향 확률  멤버 수  방향 변화 중앙값   실제(48→96시간 구간)"]
    for t in times:
        r = late_turn(g, t)
        a_, b_, prev = (g[g.time == t + pd.Timedelta(hours=h)] for h in (48, 96, -24))
        now = g[g.time == t]
        real = ""
        if len(a_) and len(b_) and len(prev):
            h0 = bearing(prev.LAT.iloc[0], prev.LON.iloc[0], now.LAT.iloc[0], now.LON.iloc[0])
            h1 = bearing(a_.LAT.iloc[0], a_.LON.iloc[0], b_.LAT.iloc[0], b_.LON.iloc[0])
            real = f"{(h1 - h0 + 180) % 360 - 180:+.0f}°"
        lines.append(f"  {(t + pd.Timedelta(hours=9)):%m-%d %H시}   "
                     + (f"{r[0]:>6.0%}   {r[1]:>5}   {r[2]:>+8.0f}°" if r else "     –        –           –")
                     + f"          {real}")
    text = "\n".join(lines)
    (ROOT / "reports" / "gefs_champi.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["champi"])
    a = ap.parse_args()
    champi()


if __name__ == "__main__":
    main()
