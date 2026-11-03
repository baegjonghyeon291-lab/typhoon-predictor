"""예보 진로 접근 판정 기록과 검증.

일본 기상청 5일 예보 전문은 피드에 최근 7일치만 있어 과거 판정을 다시 만들 수 없다. 그래서 자동 갱신 때마다
현재 태풍의 예보 진로·영향권 접근 판정(export.approach)·같은 시점의 모델 확률을 data/forecast_log.jsonl 에
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
    seen = {(r.get("sid"), r.get("name"), r.get("issued")) for r in _read()}
    new = []
    for s in latest.get("storms", []):
        fc = s.get("forecast")
        if s.get("end") != latest.get("data_until") or not fc or "approach" not in fc:
            continue
        key = (s["sid"], s["name"], fc["issued"])
        if key in seen:
            continue
        last = s["track"][-1]
        new.append({"logged": latest["generated"], "sid": s["sid"], "name": s["name"], "issued": fc["issued"],
                    "position": {"time": last[0], "lat": last[1], "lon": last[2]},
                    "points": fc["points"], "approach": fc["approach"], "env": s.get("env"),
                    "model": {c: s[c] for c in COUNTRIES if c in s}})
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
    out = []
    for r in rows:
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
    CHECK.write_text(json.dumps({"summary": summary, "rows": out}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return summary


if __name__ == "__main__":
    check()
