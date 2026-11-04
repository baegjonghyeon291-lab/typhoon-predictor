"""지난 태풍 복기: 실제 전향 시작과, 누가 언제 처음 전향을 예측했는지 (PRD P1-10).

전향 = 지난 24시간 진행 방향 대비 앞으로 48시간 방향이 45도 이상 꺾이고 200km 이상 움직임(track.turn_table 과 같은 정의).
예보가 전향을 예측했다 = 발표 시점의 예보 경로에서 0·24·48시간에 시작하는 48시간 구간 중 하나가 발표 시점 진행 방향보다
45도 이상 꺾임(즉 4일 안 전향). 실제 전향 시작 120시간 전부터 시작 시각까지 중 가장 이른 발표를 '첫 전향 예측'으로 본다.

  - 기상청: API허브 예보 이력(2001~, data/kma/typhoon, 키 필요). 발표 시각에 공개 지연 KMA_LAG_H를 더한다(가정값)
  - 자체 경로: GFS 예보 중심 추적(gfs_track, 2021-06~, data/env/gfs_fc). 발표 6시간 뒤를 그 예측의 시점으로 본다
  - 자체 분류: 경로 모델 실험의 48시간 전향 분류(track.py, 1951~2014 학습)가 50% 이상. 앞의 둘(4일)보다 보는 기간이 짧다
전향이 없던 태풍에서 전향을 예측했는지(false)도 같이 남겨, 자주 꺾는 예측이 앞선 시간에서만 유리해 보이지 않게 한다.
일본 기상청·미국 JTWC의 과거 예보는 받을 수 없어 예보 기록(forecast_log)이 쌓인 태풍부터 넣는다.

    python -m typhoon.review      # → docs/data/review.json (사이트 지난 태풍 복기 줄)
"""
import json

import numpy as np
import pandas as pd

from .data import ROOT, haversine
from .features import bearing

OUT = ROOT / "docs" / "data" / "review.json"
TURN_DEG, SEG_H, MIN_MOVE_KM = 45, 48, 200
WINDOW_H = 120
KMA_LAG_H = 3            # 기상청 분석 시각 → 공개까지의 지연(가정값). 자료에 공개 시각이 없다. GFS는 AVAIL_H(6시간)


def _diff(a, b):
    return float(abs((a - b + 180) % 360 - 180))


def heading_now(g, t):
    prev, now = g[g.time == t - pd.Timedelta(hours=24)], g[g.time == t]
    if prev.empty or now.empty:
        return None
    return bearing(prev.LAT.iloc[0], prev.LON.iloc[0], now.LAT.iloc[0], now.LON.iloc[0])


def actual_turn(g):
    """실제 전향 시작 시각(없으면 None)."""
    for t in g.time:
        h0, fut = heading_now(g, t), g[g.time == t + pd.Timedelta(hours=SEG_H)]
        if h0 is None or fut.empty:
            continue
        now = g[g.time == t]
        la0, lo0, la1, lo1 = now.LAT.iloc[0], now.LON.iloc[0], fut.LAT.iloc[0], fut.LON.iloc[0]
        if haversine(la0, lo0, la1, lo1) >= MIN_MOVE_KM and _diff(bearing(la0, lo0, la1, lo1), h0) >= TURN_DEG:
            return t
    return None


def path_turns(hours, lat, lon, h_now):
    """예보 경로(발표 시점 기준 시간·위치)가 4일 안 전향을 예측하는지."""
    for s in (0, 24, 48):
        if hours[-1] < s + SEG_H:
            break
        y0, x0 = np.interp(s, hours, lat), np.interp(s, hours, lon)
        y1, x1 = np.interp(s + SEG_H, hours, lat), np.interp(s + SEG_H, hours, lon)
        if haversine(y0, x0, y1, x1) >= MIN_MOVE_KM and _diff(bearing(y0, x0, y1, x1), h_now) >= TURN_DEG:
            return True
    return False


def _calls_turn(issue, g):
    t0, hh, la, lo = issue
    h0 = heading_now(g, t0.floor("6h"))
    return h0 is not None and len(hh) >= 2 and path_turns(np.asarray(hh, float), la, lo, h0)


def first_call(issues, g, turn):
    """issues: [(시점, 시간 배열, 위도, 경도)] 중 전향 시작 120시간 전 ~ 시작 사이에 처음 전향을 예측한 시점."""
    for issue in sorted(issues, key=lambda x: x[0]):
        if turn - pd.Timedelta(hours=WINDOW_H) <= issue[0] <= turn and _calls_turn(issue, g):
            return issue[0]
    return None


def false_call(issues, g):
    """전향이 없던 태풍에서 전향을 예측한 발표가 있었는지."""
    return any(_calls_turn(issue, g) for issue in issues)


def summarize(out):
    """출처별 미리 예측한 태풍 수·앞선 시간·헛예측, 그리고 자체 경로 자료가 있는 태풍만의 같은 표본 비교."""
    n, quiet = [r for r in out.values() if r["turn"]], [r for r in out.values() if not r["turn"]]

    def line(k, rows):
        v = [r[k]["lead_h"] for r in rows if isinstance(r.get(k), dict)]
        have = [r for r in rows if r.get(k) != "none"]
        return f"{k}: 자료 있는 태풍 {len(have)}개 중 미리 예측 {len(v)}개, 앞선 시간 중앙값 {np.median(v) if v else float('nan'):.0f}시간"

    lines = [line(k, n) for k in ("kma", "own", "clf")]
    for k in ("kma", "own", "clf"):
        f = [r["false"][k] for r in quiet if r.get("false", {}).get(k) is not None]
        lines.append(f"{k}: 전향 없던 태풍 {len(f)}개 중 전향 예측 {sum(f)}개")
    same = [r for r in n if r.get("own") != "none"]
    lines += [f"같은 표본(자체 경로 자료가 있는 {len(same)}개) " + line(k, same) for k in ("kma", "own", "clf")]
    return lines


def kma_issues():
    from .kma_lead import ib_summary, load_kma, match
    from .data import load
    lst, f = load_kma()
    m, _ = match(lst, ib_summary(load(start_year=int(lst.YY.min()) - 1)))
    f = f.merge(m, on=["YY", "TYP"]).dropna(subset=["LAT", "LON", "TMD"])
    out = {}
    for (sid, seq), s in f.groupby(["SID", "SEQ"]):
        s = s.sort_values("TMD").drop_duplicates("TMD")
        out.setdefault(sid, []).append((s.TYP_TM.iloc[0] + pd.Timedelta(hours=KMA_LAG_H), s.TMD.to_numpy() - KMA_LAG_H,
                                        s.LAT.to_numpy(), s.LON.to_numpy()))
    return out


def gfs_issues():
    from .gfs_track import AVAIL_H, TRACKS
    if not TRACKS.exists():
        return {}
    out = {}
    for (sid, c), s in pd.read_parquet(TRACKS).groupby(["SID", "cycle"]):
        s = s.sort_values("lead")
        s = s[s.lead >= AVAIL_H]                         # 발표가 공개된 뒤(6시간)부터의 경로
        if len(s) >= 2:
            out.setdefault(sid, []).append((c + pd.Timedelta(hours=AVAIL_H), (s.lead - AVAIL_H).to_numpy(),
                                            s.lat.to_numpy(), s.lon.to_numpy()))
    return out


def classifier_calls(df):
    """경로 모델 실험의 48시간 전향 분류 확률(1951~2014 학습) → {SID: [(시점, 확률)]}."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from .features import ENV_FEATURES, FEATURES
    from .track import turn_table
    from .train import CLF_PARAMS, SPLIT_YEAR
    feats = [*FEATURES, *[c for c in ENV_FEATURES if c in df]]
    tr = turn_table(df[df.SEASON < SPLIT_YEAR])
    clf = HistGradientBoostingClassifier(**CLF_PARAMS).fit(tr[feats], tr.turn)
    d = df[df.SEASON >= SPLIT_YEAR]
    p = clf.predict_proba(d[feats])[:, 1]
    return {sid: list(zip(g.time, pp)) for (sid, g), pp in
            ((x, p[d.SID.to_numpy() == x[0]]) for x in d.groupby("SID"))}


def build():
    from .data import load
    from .export import near_storms
    from .features import add_env, add_features
    df = add_env(add_features(load()), gfs=True)        # 사이트와 같은 입력(NCEP 이후는 GFS 분석장)
    kma, gfs, clf = kma_issues(), gfs_issues(), classifier_calls(df)
    out = {}
    for sid in near_storms(df):
        g = df[df.SID == sid].sort_values("time")
        turn = actual_turn(g)
        r = {"turn": turn.strftime("%Y-%m-%dT%H") if turn is not None else None}
        if turn is not None:
            def lead(t0):
                return None if t0 is None else {"t": t0.strftime("%Y-%m-%dT%H"), "lead_h": int((turn - t0) / pd.Timedelta(hours=1))}
            r["kma"] = lead(first_call(kma.get(sid, []), g, turn)) if sid in kma else "none"
            r["own"] = lead(first_call(gfs.get(sid, []), g, turn)) if sid in gfs else "none"
            calls = [t for t, p in clf.get(sid, []) if p >= .5 and turn - pd.Timedelta(hours=WINDOW_H) <= t <= turn]
            r["clf"] = lead(min(calls)) if calls else None
        else:
            r["false"] = {"kma": false_call(kma[sid], g) if sid in kma else None,
                          "own": false_call(gfs[sid], g) if sid in gfs else None,
                          "clf": any(p >= .5 for _, p in clf.get(sid, []))}
        out[sid] = r
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[review] 태풍 {len(out)}개, 실제 전향 {sum(bool(r['turn']) for r in out.values())}개 → {OUT}")
    for s in summarize(out):
        print("  " + s)
    return out


if __name__ == "__main__":
    build()
