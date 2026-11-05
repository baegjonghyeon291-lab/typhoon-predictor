"""예보 진로 접근 판정 기록과 검증.

일본 기상청 5일 예보 전문은 피드에 최근 7일치만 있어 과거 판정을 다시 만들 수 없다. 그래서 자동 갱신 때마다
현재 태풍의 일본·미국 예보 진로와 영향권 접근 판정(export.approach), 같은 시점의 모델 확률·근거·입력값을 data/forecast_log.jsonl 에
한 줄씩 쌓는다(같은 태풍·같은 발표는 한 번만). 태풍이 끝난 뒤 실제 영향권 도착과 맞춰 본다.

    python -m typhoon.forecast_log           # 기록된 판정 검증 → reports/forecast_log_check.json
"""
import json

import numpy as np
import pandas as pd

from .data import ROOT, haversine

LOG = ROOT / "data" / "forecast_log.jsonl"
CHECK = ROOT / "reports" / "forecast_log_check.json"
COUNTRIES = ("korea", "japan")


def _read():
    if not LOG.exists():
        return []
    rows = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # 중간에 끊긴 줄은 건너뜀
    return rows


def append(latest):
    """export_recent 결과(latest.json 객체)에서 활동 중이고 예보가 있는 태풍을 기록. 새로 쓴 줄 수 반환."""
    seen = {(r.get("sid"), r.get("name"), r.get("issued"), (r.get("us") or {}).get("issued"), (r.get("own") or {}).get("cycle"),
             (r.get("kr") or {}).get("issued")) for r in _read()}
    new = []
    for s in latest.get("storms", []):
        fc, us, own, kr = s.get("forecast"), s.get("forecast_us"), s.get("track_own"), s.get("forecast_kr")
        fc = fc if fc and "approach" in fc else {}        # 일본·미국 중 하나만 있어도, 어느 쪽이든 새 발표면 남긴다
        if s.get("end") != latest.get("data_until") or not (fc or us or own or kr):
            continue
        key = (s["sid"], s["name"], fc.get("issued"), (us or {}).get("issued"), (own or {}).get("cycle"), (kr or {}).get("issued"))
        if key in seen:
            continue
        last = s["track"][-1]
        new.append({"logged": latest["generated"], "sid": s["sid"], "name": s["name"], "issued": fc.get("issued"),
                    "position": {"time": last[0], "lat": last[1], "lon": last[2]},
                    "points": fc.get("points"), "approach": fc.get("approach"), "env": s.get("env"),
                    "model": {c: s[c] for c in COUNTRIES if c in s},   # 확률·도착 시간과 근거(why)
                    "turn": s.get("turn"),                             # 자체 전향 모델 확률과 근거
                    "inputs": s.get("inputs"), "us": us, "own": own, "kr": kr})   # own: GFS 추적 자체 경로(gfs_track.own_track)
        seen.add(key)
    if new:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8", newline="\n") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(new)


def _resolve(r, df):
    """기록의 태풍을 IBTrACS SID로. 실시간 임시 번호(RT…)면 기록 시각 위치에서 가장 가까운 태풍."""
    if r["sid"] in set(df.SID):
        return r["sid"]
    t = pd.Timestamp(r["position"]["time"])
    near = df[(df.time - t).abs() <= pd.Timedelta(hours=6)]
    if near.empty:
        return None
    d = haversine(near.LAT.values, near.LON.values, r["position"]["lat"], r["position"]["lon"])
    return near.SID.values[np.argmin(d)] if d.min() <= 300 else None


def check(df=None, refresh=True):
    """태풍이 끝난 기록만 실제 도착과 맞춘다. '끝남'은 벽시계가 아니라 받은 IBTrACS 자료 끝 기준:
    자료가 발표 뒤 5일 이상 이어지거나, 그 태풍 기록이 자료 끝보다 3일 이상 먼저 끝난(소멸) 경우.
    (오래된 로컬 파일로 중간에 잘린 경로를 '도착 안 함'으로 세지 않도록 기본으로 새로 받는다.)
    이미 영향권에 들어온 뒤에 기록된 판정은 예측이 아니므로 그 나라에서는 뺀다."""
    from .data import load, download
    if df is None:
        if refresh:
            try:
                download()
            except Exception as e:  # noqa: BLE001
                print(f"[forecast_log] IBTrACS 새로 받기 실패, 로컬 파일 사용: {type(e).__name__}")
        df = load()
    data_end = df.time.max()
    rows = _read()
    judged = {}                   # 미국 예보만 바뀐 줄은 같은 일본 발표이므로 한 번만 채점한다
    for r in rows:
        if r.get("approach"):
            judged.setdefault((r["sid"], r["issued"]), r)
    out = []
    for r in judged.values():
        sid = _resolve(r, df)
        if sid is None:
            continue
        g = df[df.SID == sid]
        issued = pd.Timestamp(r["issued"])
        t0 = pd.Timestamp(r["position"]["time"])
        done = data_end >= issued + pd.Timedelta(hours=120) or g.time.max() < data_end - pd.Timedelta(days=3)
        if not done:
            continue
        for c in COUNTRIES:
            a = r["approach"].get(c, {})
            if "arrived" in (r["model"].get(c) or {}) or g[(g.time <= t0) & g[f"in_{c}"]].size:
                continue          # 기록 시점에 이미 영향권에 들어온 뒤
            hit = g[(g.time > t0) & g[f"in_{c}"]].time
            arrived = hit.min() if len(hit) and hit.min() <= issued + pd.Timedelta(hours=120) else None
            out.append({"sid": sid, "name": r["name"], "issued": r["issued"], "country": c,
                        "fc_center": a.get("center") is not None, "fc_cone": a.get("cone") is not None,
                        "model_p": (r["model"].get(c) or {}).get("p"),
                        "arrived": arrived.strftime("%Y-%m-%dT%H") if arrived is not None else None,
                        "center_err_h": ((pd.Timestamp(a["center"]) - arrived) / pd.Timedelta(hours=1))
                        if a.get("center") and arrived is not None else None})
    t = pd.DataFrame(out)
    summary = {"logged": len(rows), "checked": len(t)}
    if len(t):
        for c in COUNTRIES:
            x = t[t.country == c]
            y = x.arrived.notna()
            summary[c] = {"forecasts": len(x), "arrived": int(y.sum()),
                          "center_hit": int((x.fc_center & y).sum()), "center_false": int((x.fc_center & ~y).sum()),
                          "center_miss": int((~x.fc_center & y).sum()),
                          "cone_hit": int((x.fc_cone & y).sum()), "cone_false": int((x.fc_cone & ~y).sum()),
                          "median_center_err_h": float(x.center_err_h.dropna().median()) if x.center_err_h.notna().any() else None}
    sources = score_sources(rows, df, data_end)
    CHECK.write_text(json.dumps({"summary": summary, "sources": sources, "rows": out}, ensure_ascii=False, indent=1,
                                default=str), encoding="utf-8")
    print(json.dumps({"summary": summary, "sources": sources}, ensure_ascii=False, indent=1))
    return summary


SOURCES = {"jma": "일본 기상청", "us": "미국 JTWC", "kr": "한국 기상청", "own": "자체 경로"}
ERR_H = (24, 48, 72)


def _source_forecast(r, k):
    """기록 한 줄에서 출처 k의 예보 {issued, points, approach}. 일본은 줄 자체, 나머지는 하위 칸."""
    f = {"issued": r.get("issued"), "points": r.get("points"), "approach": r.get("approach")} if k == "jma" else r.get(k)
    return f if f and f.get("issued") and f.get("points") else None


def score_sources(rows, df, data_end):
    """출처마다 같은 기준으로: 영향권 중심 진입 판정(적중·헛판정·놓침, 나라별)과 24·48·72시간 위치 오차(km).
    같은 발표는 한 번만 센다. 예보 시간은 발표 시각 기준(미국은 위치 기준 시각이 발표보다 약 3시간 앞이라 근사)."""
    out = {}
    for k, label in SOURCES.items():
        seen, judged, errs = set(), [], {h: [] for h in ERR_H}
        for r in rows:
            f = _source_forecast(r, k)
            if not f:
                continue
            issue = (r["sid"], f.get("cycle") or f["issued"])   # 자체 경로의 issued는 관측 시각이라 GFS 발표(cycle)로 묶는다
            if issue in seen:
                continue
            seen.add(issue)
            sid = _resolve(r, df)
            if sid is None:
                continue
            g = df[df.SID == sid].set_index("time")
            base, t0 = pd.Timestamp(f["issued"]), pd.Timestamp(r["position"]["time"])
            if not (data_end >= base + pd.Timedelta(hours=120) or g.index.max() < data_end - pd.Timedelta(days=3)):
                continue                                  # 태풍이 아직 안 끝남
            for p in f["points"]:
                h = int(p[0])
                if h in errs and base + pd.Timedelta(hours=h) in g.index:
                    o = g.loc[base + pd.Timedelta(hours=h)]
                    errs[h].append(float(haversine(p[1], p[2], o.LAT, o.LON)))
            for c in COUNTRIES:
                a = (f.get("approach") or {}).get(c)
                if a is None or g[(g.index <= t0) & g[f"in_{c}"]].size:
                    continue                              # 판정 없음, 또는 이미 영향권 안
                span = max(int(p[0]) for p in f["points"])
                hit = g[(g.index > t0) & g[f"in_{c}"]].index
                arrived = len(hit) and hit.min() <= base + pd.Timedelta(hours=span)
                judged.append((c, a.get("center") is not None, bool(arrived)))
        s = {"label": label, "issues": len(seen)}
        for c in COUNTRIES:
            j = [(fc, y) for cc, fc, y in judged if cc == c]
            s[c] = {"judged": len(j), "hit": sum(fc and y for fc, y in j), "false": sum(fc and not y for fc, y in j),
                    "miss": sum(y and not fc for fc, y in j)}
        s["track_error_km"] = {f"{h}h": {"n": len(v), "mean": round(float(np.mean(v))) if v else None} for h, v in errs.items()}
        out[k] = s
    return out


if __name__ == "__main__":
    check()
