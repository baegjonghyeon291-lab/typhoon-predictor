"""웹사이트(docs/)용 JSON 생성: 모델, 최근 태풍 예측, 지난 태풍 기록, 통계."""
import argparse
import json
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd

from .data import load, download, ROOT
from .features import add_features, add_targets, add_env, predict, FEATURES
from .train import SPLIT_YEAR

OUT = ROOT / "docs" / "data"
RECENT_DAYS = 21


def num(v, d=2):
    return None if pd.isna(v) else round(float(v), d)


def tree_json(model):
    trees = []
    for it in model._predictors:
        nodes = []
        for n in it[0].nodes:
            if n["is_leaf"]:
                nodes.append([float(n["value"])])
            else:
                nodes.append([int(n["feature_idx"]), min(float(n["num_threshold"]), 1e300),
                              int(n["missing_go_to_left"]), int(n["left"]), int(n["right"])])
        trees.append(nodes)
    return {"base": float(np.ravel(model._baseline_prediction)[0]), "trees": trees}


def export_models():
    out = {"features": FEATURES}
    for c in ["korea", "japan"]:
        m = joblib.load(ROOT / "models" / f"{c}.joblib")
        out[c] = {"clf": tree_json(m["clf"]), "reg": tree_json(m["reg"])}
    (OUT / "model.json").write_text(json.dumps(out, separators=(",", ":")))


def predict_rows(d, models):
    d = d.copy()
    for c in models:
        d[f"p_{c}"], d[f"h_{c}"] = predict(c, d)
    return d


def track(g, extra=()):
    return [[t.strftime("%Y-%m-%dT%H"), num(la), num(lo), num(w, 0), num(p, 0), *[num(r[e], 3) for e in extra]]
            for t, la, lo, w, p, (_, r) in zip(g.time, g.LAT, g.LON, g.wind, g.pres, g.iterrows())]


def export_recent(df, models):
    last_time = df.time.max()
    sids = df[df.time >= last_time - pd.Timedelta(days=RECENT_DAYS)].SID.unique()
    storms = []
    for sid in sids:
        g = predict_rows(df[df.SID == sid], models)
        last = g.iloc[-1]
        s = {"sid": sid, "name": str(g.NAME.iloc[0]), "start": g.time.min().strftime("%Y-%m-%dT%H"),
             "end": last.time.strftime("%Y-%m-%dT%H"), "track": track(g, ["p_korea", "p_japan"])}
        for c in models:
            hit = g[g[f"in_{c}"]]
            if len(hit):
                s[c] = {"arrived": hit.time.min().strftime("%Y-%m-%dT%H")}
            else:
                s[c] = {"p": num(last[f"p_{c}"], 3), "hours": num(last[f"h_{c}"], 0),
                        "eta": (last.time + pd.Timedelta(hours=float(last[f"h_{c}"]))).strftime("%Y-%m-%dT%H")}
        storms.append(s)
    storms.sort(key=lambda s: s["end"], reverse=True)
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
            "data_until": last_time.strftime("%Y-%m-%dT%H"), "storms": storms}


def export_history(df, models):
    """검증 기간(학습에 안 쓴 2015년~) 중 한국 영향 태풍: 시점별 모델 확률과 실제 도착."""
    d = add_targets(df[df.SEASON >= SPLIT_YEAR], "korea")
    hits = d[d.arrival.notna()].SID.unique()
    out = []
    for sid in hits:
        full = df[df.SID == sid]
        g = predict_rows(full, models)
        arrival = full[full.in_korea].time.min()
        g = g[g.time <= arrival + pd.Timedelta(hours=48)]
        out.append({"sid": sid, "name": str(full.NAME.iloc[0]), "season": int(full.SEASON.iloc[0]),
                    "arrival": arrival.strftime("%Y-%m-%dT%H"), "track": track(g, ["p_korea", "p_japan"])})
    out.sort(key=lambda s: s["arrival"], reverse=True)
    return out


def export_stats(df):
    hit = df[df.in_korea].groupby("SID").time.min()
    by_month = hit.dt.month.value_counts().reindex(range(1, 13), fill_value=0)
    by_year = df[df.SID.isin(hit.index)].groupby("SID").SEASON.first().value_counts().sort_index()
    years = range(int(df.SEASON.min()), int(df.SEASON.max()) + 1)
    return {"metrics": json.loads((ROOT / "reports" / "metrics.json").read_text()),
            "analysis": json.loads((ROOT / "reports" / "analysis.json").read_text()),
            "korea_by_month": [int(v) for v in by_month.values],
            "korea_by_year": {str(y): int(by_year.get(y, 0)) for y in years}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    if ap.parse_args().update:
        download()
        from . import env_atmos
        env_atmos.build(recent=True)
    OUT.mkdir(parents=True, exist_ok=True)
    df = add_env(add_features(load()))
    models = ["korea", "japan"]
    export_models()
    for name, obj in [("latest", export_recent(df, models)), ("history", export_history(df, models)),
                      ("stats", export_stats(df))]:
        (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
