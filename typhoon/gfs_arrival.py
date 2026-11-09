"""GFS 예측 경로를 도착 모델에 붙인다(실험 + 실제 예측).

기존 도착 모델은 '지금 위치·움직임·대기 환경'만 보고 확률을 낸다. 기상청은 수치모델 예보 진로가
영향권(도시 200km)에 드는지를 보고 발표한다. 그래서 GFS 예보장으로 추적한 중심 경로를 지금 관측 위치에
이어 붙여, 그 경로가 영향권에 드는지·언제 드는지·얼마나 가까이 오는지를 기존 확률과 결합한다.

- 시점 t에는 t보다 6시간 이상 앞선 00·12시 발표만 쓴다(gfs_track.AVAIL_H, 실시간과 같은 조건).
- GFS 경로가 있는 시점만 결합하고, 없으면 기존 모델 그대로다(2021-06 이전, 추적 실패, 발표 파일 없음).
- 남은 시간: GFS 경로가 영향권에 들면 그 진입 시각을 쓰고 80% 범위는 실험의 절대 오차 80% 분위로 둔다.
- 결합 계수는 2021-06 이후 태풍 전체로 맞춰 models/gfs_arrival.json 에 둔다. 실험 성적은 연도별 교차 예측
  (한 해를 빼고 맞춘 뒤 그 해 예측)이고, 기상청은 같은 태풍·같은 정의(kma_lead.issue_entries)로 채점한다.

    python -m typhoon.gfs_arrival          # 실험 → reports/gfs_arrival_experiment.txt, 계수 → models/gfs_arrival.json
"""
import json
import warnings

import numpy as np
import pandas as pd

from .data import CITIES, RADIUS_KM, ROOT, haversine
from .gfs_track import AVAIL_H, DIR, FIRST, TRACKS, _path, track_cycle

REPORT = ROOT / "reports" / "gfs_arrival_experiment.txt"
PARAMS = ROOT / "models" / "gfs_arrival.json"
THRESHOLD = 0.4                     # analyze.THRESHOLD 와 같은 경보 기준
HORIZON = 120
LEADS = [24, 48, 72, 96, 120]
COUNTRIES = ["korea", "japan"]
X_NAMES = ["logit(기존)", "GFS 진입", "최근접 거리/1000km", "1-진입시간/120"]


# ---------------------------------------------------------------- GFS 경로 특성
_PATHS = {"mtime": None, "paths": {}}


def _paths():
    """(SID, 발표) → (리드, 위도, 경도). tracks.parquet(실험용 일괄 추적)이 있으면 읽어 둔다."""
    m = TRACKS.stat().st_mtime if TRACKS.exists() else None
    if _PATHS["mtime"] != m:
        p = {}
        if m is not None:
            for k, g in pd.read_parquet(TRACKS).sort_values("lead").groupby(["SID", "cycle"]):
                p[k] = (g.lead.to_numpy(float), g.lat.to_numpy(), g.lon.to_numpy())
        _PATHS.update(mtime=m, paths=p)
    return _PATHS["paths"]


def _path_for(sid, c, obs):
    """일괄 추적에 없으면 받아 둔 발표 파일(실시간 갱신이 쌓은 것)에서 바로 추적. 둘 다 없으면 None."""
    paths = _paths()
    if (sid, c) in paths:
        return paths[(sid, c)]
    at = obs.get(c)
    if at is None or not _path(c).exists():
        return None
    try:
        tr = track_cycle(np.load(_path(c))["p"], at[0], at[1] % 360)
    except Exception:  # noqa: BLE001  손상된 캐시 파일 등
        tr = []
    out = None if len(tr) < 2 else (np.array([x[0] for x in tr], float), np.array([x[1] for x in tr]),
                                    np.array([x[2] for x in tr]))
    paths[(sid, c)] = out
    return out


def gfs_path(d, country="korea"):
    """각 시점의 GFS 경로(관측 위치에 GFS 이동량을 더함, 1시간 보간, 최대 120시간)로
    gfs_entry_h(영향권에 처음 드는 시간, 안 들면 NaN), gfs_min_km(가장 가까운 거리, 경로 없으면 NaN)."""
    entry, mind = np.full(len(d), np.nan), np.full(len(d), np.nan)
    if len(d) == 0 or "SID" not in d or "time" not in d:
        return entry, mind
    cyc = (d.time - pd.Timedelta(hours=AVAIL_H)).dt.floor("12h")
    age = ((d.time - cyc) / pd.Timedelta(hours=1)).to_numpy()
    obs = {sid: {t: (la, lo) for t, la, lo in zip(g.time, g.LAT, g.LON)} for sid, g in d.groupby("SID")}
    for i, (sid, c, a, la0, lo0) in enumerate(zip(d.SID, cyc, age, d.LAT.to_numpy(), d.LON.to_numpy())):
        if c < FIRST:
            continue
        p = _path_for(sid, c, obs.get(sid, {}))
        if p is None or p[0][-1] < a + 12 or p[0][0] > a:
            continue
        L, la, lo = p
        hh = np.arange(a, min(L[-1], a + HORIZON) + 1)
        y = la0 + np.interp(hh, L, la) - np.interp(a, L, la)
        x = (lo0 % 360) + np.interp(hh, L, lo) - np.interp(a, L, lo)
        dist = np.min([haversine(y, x, *q) for q in CITIES[country]], axis=0)
        mind[i] = dist.min()
        ok = dist <= RADIUS_KM
        if ok.any():
            entry[i] = hh[ok.argmax()] - a
    return entry, mind


def stack_X(p_base, entry, mind):
    p = np.clip(np.asarray(p_base, float), 1e-4, 1 - 1e-4)
    hit = ~np.isnan(entry)
    return np.column_stack([np.log(p / (1 - p)), hit, np.minimum(np.nan_to_num(mind, nan=3000), 3000) / 1000,
                            np.where(hit, 1 - np.nan_to_num(entry, nan=HORIZON) / HORIZON, 0)])


# ---------------------------------------------------------------- 실제 예측
_PARAMS = {"mtime": None, "v": None}


def load_params():
    m = PARAMS.stat().st_mtime if PARAMS.exists() else None
    if _PARAMS["mtime"] != m:
        _PARAMS.update(mtime=m, v=json.loads(PARAMS.read_text(encoding="utf-8")) if m else None)
    return _PARAMS["v"]


def combine(country, p, h, lo, hi, entry, mind):
    """기존 (확률, 남은 시간, 80% 범위)에 GFS 경로를 결합. GFS 경로가 없는 시점은 그대로."""
    prm = (load_params() or {}).get(country)
    p, h, lo, hi = (np.array(v, float, copy=True) for v in (p, h, lo, hi))
    if not prm:
        return p, h, lo, hi
    has = ~np.isnan(mind)
    if has.any():
        z = stack_X(p[has], entry[has], mind[has]) @ np.array(prm["coef"]) + prm["intercept"]
        p[has] = 1 / (1 + np.exp(-z))
    hit = ~np.isnan(entry)
    if hit.any():
        h[hit] = entry[hit]
        lo[hit] = np.maximum(entry[hit] - prm["entry_err80_h"], 0)
        hi[hit] = entry[hit] + prm["entry_err80_h"]
    return p, h, lo, hi


def serve(country, d):
    """실제 예측: 기존 모델 + GFS 경로. → (확률, 남은 시간, 80% 하한, 상한). GFS 쪽이 실패하면 기존 모델 그대로."""
    from .features import predict, predict_range
    p, h = predict(country, d)
    lo, hi = predict_range(country, d)
    try:
        entry, mind = gfs_path(d, country)
        return combine(country, p, h, lo, hi, entry, mind)
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"[gfs_arrival] 결합 실패, 기존 모델 사용: {type(e).__name__}: {e}", RuntimeWarning)
        return p, h, lo, hi


# ---------------------------------------------------------------- 실험
def warning_table(d, col, th=THRESHOLD):
    """태풍마다 처음 경보(col >= th)가 도착 몇 시간 전이었는지(도착한 태풍), 경보 낸 태풍 중 안 온 비율."""
    rows = []
    for sid, g in d.groupby("SID"):
        w = g[g[col] >= th]
        rows.append((sid, g.arrival.iloc[0], w.hours_to.max() if len(w) and g.arrival.notna().any() else 0.0, len(w) > 0))
    t = pd.DataFrame(rows, columns=["SID", "arrival", "lead", "warned"])
    hit = t[t.arrival.notna()]
    lead = hit.lead.fillna(0)
    return {"storms": len(hit), "median_lead_h": float(lead.median()) if len(hit) else float("nan"),
            **{f"w{h}": float((lead >= h).mean()) for h in LEADS},
            "warned": int(t.warned.sum()), "far": float(t[t.warned].arrival.isna().mean()) if t.warned.any() else float("nan"),
            "per_storm": hit.set_index("SID").lead}


def kma_korea(d):
    """기상청 같은 기준(예보 진로 1시간 보간, 한국 6개 도시 200km) 첫 경보 선행시간·헛경보·진입 시각 오차."""
    from .data import load
    from .kma_lead import ib_summary, issue_entries, load_kma, match
    lst, f = load_kma()
    m, _ = match(lst, ib_summary(load(start_year=FIRST.year - 1)))
    e = issue_entries(f[f.YY >= FIRST.year]).merge(m, on=["YY", "TYP"])
    e = e[e.SID.isin(set(d.SID))].merge(d.groupby("SID").arrival.first().rename("arrival"), left_on="SID", right_index=True)
    before = e[e.arrival.isna() | (e.issued < e.arrival)]
    kw = before.groupby("SID").agg(warned=("entry", lambda x: x.notna().any()), arrival=("arrival", "first"))
    first = before[before.entry.notna()].groupby("SID").issued.min()
    arr = d.groupby("SID").arrival.first().dropna()
    lead = ((arr - first.reindex(arr.index)).dt.total_seconds() / 3600).clip(lower=0).fillna(0)
    w = before[before.entry.notna() & before.arrival.notna()]
    w = w[(w.arrival - w.issued).dt.total_seconds() / 3600 <= 120]
    return lead, int(kw.warned.sum()), float(kw[kw.warned].arrival.isna().mean()), \
        float(((w.entry - w.arrival).dt.total_seconds().abs() / 3600).mean()), len(w)


def experiment_country(df, country):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from .features import add_targets, predict, predict_range
    d = add_targets(df[df.time >= FIRST + pd.Timedelta(days=1)], country)
    start = d.groupby("SID").time.min()
    d = d[d.SID.isin(start[start >= FIRST + pd.Timedelta(days=1)].index)].copy()
    d["p_base"], d["h_base"] = predict(country, d)
    lo, hi = predict_range(country, d)
    d["gfs_entry_h"], d["gfs_min_km"] = gfs_path(d, country)
    d["year"] = d.SID.map(start).dt.year
    has = d.gfs_min_km.notna().to_numpy()
    X = stack_X(d.p_base, d.gfs_entry_h.to_numpy(), d.gfs_min_km.to_numpy())
    y = d.hit.to_numpy()

    # 연도별 교차 예측(GFS 경로 있는 시점만 결합, 없으면 기존)
    d["p_new"] = d.p_base
    for yr in sorted(d.year.unique()):
        tr, te = has & (d.year != yr).to_numpy(), has & (d.year == yr).to_numpy()
        if te.any():
            d.loc[te, "p_new"] = LogisticRegression(max_iter=2000).fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    lr = LogisticRegression(max_iter=2000).fit(X[has], y[has])
    hg = d[(d.hit == 1) & d.gfs_entry_h.notna()]
    err = (hg.gfs_entry_h - hg.hours_to).abs()
    params = {"coef": [round(float(v), 4) for v in lr.coef_[0]], "intercept": round(float(lr.intercept_[0]), 4),
              "entry_err80_h": round(float(err.quantile(.8)), 1), "rows": int(has.sum()), "storms": int(d[has].SID.nunique()),
              "fit_until": d.time.max().strftime("%Y-%m-%d")}

    # 남은 시간: GFS 진입이 있으면 그 시각(교차 없이도 계수와 무관)
    h_new = np.where(d.gfs_entry_h.notna(), d.gfs_entry_h, d.h_base)
    hit = d.hit.to_numpy() == 1
    lo_n = np.where(d.gfs_entry_h.notna(), np.maximum(d.gfs_entry_h - params["entry_err80_h"], 0), lo)
    hi_n = np.where(d.gfs_entry_h.notna(), d.gfs_entry_h + params["entry_err80_h"], hi)
    yh = d.hours_to.to_numpy()
    name = {"korea": "한국", "japan": "일본"}[country]
    lines = [f"[{name}] 2021-06 이후 시작한 태풍 {d.SID.nunique()}개, 시점 {len(d)}개(GFS 경로 있음 {has.mean():.0%}),"
             f" {name} 도착 태풍 {d[d.arrival.notna()].SID.nunique()}개",
             "", "1) 시점별 120시간 안 도착 확률     AUC     Brier"]
    for lab, c in [("기존 모델", "p_base"), ("기존+GFS 결합", "p_new")]:
        lines.append(f"  {lab:<28} {roc_auc_score(y, d[c]):.3f}   {brier_score_loss(y, d[c]):.4f}")
    res = {lab: warning_table(d, c) for lab, c in [("기존 모델", "p_base"), ("기존+GFS 결합", "p_new")]}
    lines += ["", f"2) {name} 도착 태풍: 첫 경보(보정 확률 {THRESHOLD} 이상)가 도착 몇 시간 전",
              "                 태풍  중앙값   24h   48h   72h   96h  120h 전 경보 | 경보 태풍  헛경보"]
    for lab, w in res.items():
        lines.append(f"  {lab:<12} {w['storms']:>5} {w['median_lead_h']:>6.0f}h " + " ".join(f"{w[f'w{h}']:>5.0%}" for h in LEADS)
                     + f"      | {w['warned']:>6}  {w['far']:>6.0%}")
    if country == "korea":
        kl, kn, kfar, kerr, kn_err = kma_korea(d)
        kk = kl.reindex(res["기존 모델"]["per_storm"].index).fillna(0)
        lines.append(f"  {'기상청':<12} {len(kk):>5} {kk.median():>6.0f}h " + " ".join(f"{(kk >= h).mean():>5.0%}" for h in LEADS)
                     + f"      | {kn:>6}  {kfar:>6.0%}   (예보 진로 영향권 진입)")
    lines += ["", "3) 남은 시간(도착 120시간 안 시점)        평균 오차   80% 범위 포함",
              f"  기존 회귀                         {np.abs(d.h_base - yh)[hit].mean():>6.1f}h      {((yh >= lo) & (yh <= hi))[hit].mean():>5.0%}",
              f"  GFS 진입 있으면 그 시각             {np.abs(h_new - yh)[hit].mean():>6.1f}h      {((yh >= lo_n) & (yh <= hi_n))[hit].mean():>5.0%}",
              f"    (GFS 경로가 영향권에 든 시점 {len(hg)}개만: 기존 {np.abs(hg.h_base - hg.hours_to).mean():.1f}h → GFS {err.mean():.1f}h,"
              f" 80% 오차 {params['entry_err80_h']}h)"]
    if country == "korea":
        lines.append(f"  기상청(진입을 예측한 발표 {kn_err}개)  {kerr:>6.1f}h")
        lines += ["", "4) 한국 도착 태풍별 첫 경보 선행시간(시간)", "  태풍              기존  결합  기상청"]
        names = d.groupby("SID").agg(n=("NAME", "first"), y=("SEASON", "first"))
        for sid in res["기존 모델"]["per_storm"].index:
            lines.append(f"  {names.y[sid]} {names.n[sid]:<12} {res['기존 모델']['per_storm'][sid]:>4.0f}"
                         f"  {res['기존+GFS 결합']['per_storm'][sid]:>4.0f}  {kk[sid]:>6.0f}")
    lines += ["", f"결합 계수(GFS 경로 있는 {params['rows']}개 시점 전체로 맞춤, 실제 예측에 씀): "
              + ", ".join(f"{n} {v:+.2f}" for n, v in zip(X_NAMES, params["coef"])) + f", 절편 {params['intercept']:+.2f}"]
    return lines, params


def main():
    from .data import load
    from .features import add_env, add_features
    df = add_env(add_features(load()), gfs=True)
    out, params = [f"GFS 예측 경로 → 도착 모델 결합 실험 ({pd.Timestamp.now():%Y-%m-%d})",
                   "결합 성적은 연도별 교차 예측. GFS 경로가 없는 시점은 기존 모델 그대로.", ""], {}
    for c in COUNTRIES:
        lines, params[c] = experiment_country(df, c)
        out += lines + [""]
    PARAMS.write_text(json.dumps(params, indent=1, ensure_ascii=False), encoding="utf-8")
    text = "\n".join(out)
    REPORT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
