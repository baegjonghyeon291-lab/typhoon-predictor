"""GEFS 앙상블(30개 + 대조 1개)로 전향 확률 실험 (PRD P0, 참피 같은 급전향을 일찍 잡을 수 있는지).

AWS noaa-gefs-pds(키 없음)의 0.5° pgrb2a 파일에서 해면기압(PRMSL, 파일 마지막 메시지)만 .idx 로 바이트 범위를 받아
1° 격자(0~55N, 95~185E, gfs_track 과 같은 격자)로 줄여 data/env/gefs/{발표}/{멤버}.npz 로 캐시한다(0~132시간, 6시간 간격).
멤버마다 gfs_track.track_cycle 로 중심을 추적하고, 시점 t의 48시간 방향 변화가 45도 이상이고 200km 이상 움직인 멤버 비율을 전향 확률로 본다.

    python -m typhoon.gefs champi     # 참피(2026) 시범 → reports/gefs_champi.txt
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from .data import ROOT, haversine
from .gfs_track import LAT, LON, cycle_for, decode_prmsl, track_cycle

DIR = ROOT / "data" / "env" / "gefs"
URL = "https://noaa-gefs-pds.s3.amazonaws.com/gefs.{c:%Y%m%d}/{c:%H}/atmos/pgrb2ap5/{m}.t{c:%H}z.pgrb2a.0p50.f{f:03d}"
MEMBERS = ["gec00", *[f"gep{i:02d}" for i in range(1, 31)]]
LEADS = list(range(0, 133, 6))      # 0~132시간(2~4일 뒤 전향까지 보려고)
TURN_DEG, TURN_H, MIN_MOVE_KM = 45, 48, 200    # track.turn_table 과 같은 전향 정의
MIN_MEMBERS = 10                               # 받기 실패로 멤버가 이보다 적으면 확률을 내지 않는다


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
    return decode_prmsl(sess.get(url, headers={"Range": f"bytes={a}-{b}"}, timeout=60).content, (720, 361))


def _path(c, m):
    return DIR / f"{c:%Y%m%d%H}" / f"{m}.npz"


def _complete(p):
    """캐시가 온전한지: 모든 예보 시간이 채워져 있고 읽을 수 있다."""
    try:
        a = np.load(p)["p"]
    except Exception:  # noqa: BLE001  없거나 쓰다 끊긴 파일
        return False
    return len(a) == len(LEADS) and not np.isnan(a).all(axis=(1, 2)).any()


def fetch_member(sess, c, m, retries=3):
    """한 멤버의 모든 예보 시간을 받아 저장한다. 하나라도 못 받으면 저장하지 않고 False(다음에 다시 받는다)."""
    p = _path(c, m)
    if _complete(p):
        return True
    out = np.full((len(LEADS), len(LAT), len(LON)), np.nan, np.float32)
    for i, f in enumerate(LEADS):
        for k in range(retries):
            try:
                v = _fetch(sess, c, m, f)
                break
            except Exception:  # noqa: BLE001  받기 끊김·잘린 GRIB 은 다시 받는다
                if k == retries - 1:
                    return False
                time.sleep(2 * (k + 1))
        if v is None:
            return False
        out[i] = v
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, p=out)
    tmp.replace(p)
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


def member_turns(g, t, h0, h1):
    """시점 t에서 멤버별 'h0→h1시간 구간 진행 방향'과 지금 방향(지난 24시간)의 차이.
    → (45도 이상 꺾이고 200km 이상 움직인 멤버 비율, 쓴 멤버 수, 방향 변화 중앙값, 경로가 짧아 뺀 멤버 수).
    쓸 수 있는 멤버가 MIN_MEMBERS 미만이면 None."""
    from .features import bearing
    c = cycle_for(t)
    at_c, now, prev = g[g.time == c], g[g.time == t], g[g.time == t - pd.Timedelta(hours=24)]
    if at_c.empty or now.empty or prev.empty:
        return None
    d = (t - c) / pd.Timedelta(hours=1)
    h_now = bearing(prev.LAT.iloc[0], prev.LON.iloc[0], now.LAT.iloc[0], now.LON.iloc[0])
    turns, moved = [], []
    for leads, la, lo in member_tracks(c, float(at_c.LAT.iloc[0]), float(at_c.LON.iloc[0])).values():
        if leads[-1] < d + h1:
            continue
        y0, x0 = np.interp(d + h0, leads, la), np.interp(d + h0, leads, lo)
        y1, x1 = np.interp(d + h1, leads, la), np.interp(d + h1, leads, lo)
        turns.append(float((bearing(y0, x0, y1, x1) - h_now + 180) % 360 - 180))
        moved.append(haversine(y0, x0, y1, x1) >= MIN_MOVE_KM)
    if len(turns) < MIN_MEMBERS:
        return None
    turns = np.array(turns)
    lost = sum(_path(c, m).exists() for m in MEMBERS) - len(turns)
    return float(((np.abs(turns) >= TURN_DEG) & np.array(moved)).mean()), len(turns), float(np.median(turns)), lost


def late_turn(g, t, h0=48, h1=96):
    """48시간 안 전향 정의로는 잡히지 않는 2~4일 뒤 전향(48→96시간 구간)."""
    return member_turns(g, t, h0, h1)


def turn_probability(g, t):
    """한 태풍(g: 시간순 시점 표)의 시점 t: 그때 공개된 발표의 멤버로 본 48시간 안 전향 확률."""
    return member_turns(g, t, 0, TURN_H)


LIVE_BUDGET_S = 420     # 자동 갱신(30분 제한) 안에서 앙상블 받기에 쓰는 최대 시간


def live_turn(g):
    """활동 중인 태풍(g: 시간순 시점 표)의 마지막 시점 전향 확률. 그 시점에 공개된 발표 하나만 받는다(멤버 31 × 23개 × 색인·본문 약 1,426개 요청).
    → {"cycle", "members", "lost", "p48", "p96"} (p48: 48시간 안 전향, p96: 48→96시간 구간 전향, lost: 경로가 짧아 뺀 멤버 수) 또는 None."""
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
    return {"cycle": c.strftime("%Y-%m-%dT%H"), "members": max(a[1] if a else 0, b[1] if b else 0), "lost": max(a[3] if a else 0, b[3] if b else 0),
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
    ap.parse_args()
    champi()


if __name__ == "__main__":
    main()
