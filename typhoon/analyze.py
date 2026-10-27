"""한국이 일본보다 태풍 예측이 늦는 이유를 데이터로 분석."""
import json
import joblib
import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

from .data import load, ROOT
from .features import add_features, add_targets, FEATURES
from .train import SPLIT_YEAR

THRESHOLD = 0.5
LEADS = [24, 48, 72, 96, 120]


def natural_lead(df, country):
    """발생(첫 기록)부터 영향권 도착까지 걸린 시간."""
    first = df.groupby("SID").time.min()
    hit = df[df[f"in_{country}"]].groupby("SID").time.min()
    h = ((hit - first.loc[hit.index]).dt.total_seconds() / 3600)
    return {"storms": int(len(h)), "median_h": float(h.median()), "p25_h": float(h.quantile(.25)),
            "p75_h": float(h.quantile(.75))}


def warning_lead(df, country):
    """검증 기간 태풍에 대해 모델이 처음 경보(확률 0.5 이상)를 낸 시점이 도착 몇 시간 전인지."""
    m = joblib.load(ROOT / "models" / f"{country}.joblib")
    d = add_targets(df[df.SEASON >= SPLIT_YEAR], country)
    d = d[d.arrival.notna()].copy()
    d["p"] = m["clf"].predict_proba(d[FEATURES])[:, 1]
    rows = []
    for sid, g in d.groupby("SID"):
        warned = g[g.p >= THRESHOLD]
        rows.append(warned.hours_to.max() if len(warned) else 0.0)
    lead = pd.Series(rows)
    detect = {f"warned_{h}h_before": round(float((lead >= h).mean()), 3) for h in LEADS}
    return {"storms": int(len(lead)), "median_warning_lead_h": float(lead.median()), **detect}


def agency_start(df):
    """같은 태풍을 KMA와 JMA가 각각 처음 기록한 시각 차이(KMA가 늦으면 +)."""
    d = df[df.SEASON >= 2015]
    kma = d[d.KMA_LAT.notna()].groupby("SID").time.min()
    jma = d[d.TOKYO_LAT.notna()].groupby("SID").time.min()
    both = kma.index.intersection(jma.index)
    diff = (kma[both] - jma[both]).dt.total_seconds() / 3600
    return {"storms": int(len(both)), "median_kma_minus_jma_h": float(diff.median()),
            "mean_kma_minus_jma_h": round(float(diff.mean()), 1),
            "kma_later_ratio": round(float((diff > 0).mean()), 3)}


def track_turn(df):
    """도착 48시간 전 진행 방향과 도착 시점 방향 차이(전향 정도). 클수록 경로 예측이 어렵다."""
    out = {}
    for c in ["korea", "japan"]:
        d = add_targets(df, c)
        d = d[(d.hours_to > 0) & (d.hours_to <= 48)]
        g = d.sort_values("time").groupby("SID").heading
        turn = (g.last() - g.first() + 180) % 360 - 180
        out[c] = {"storms": int(turn.notna().sum()), "median_abs_turn_deg": round(float(turn.abs().median()), 1)}
    return out


def importance(df, country, n=8):
    m = joblib.load(ROOT / "models" / f"{country}.joblib")
    d = add_targets(df[df.SEASON >= SPLIT_YEAR], country)
    r = permutation_importance(m["clf"], d[FEATURES], d.hit, scoring="roc_auc", n_repeats=3, random_state=0)
    s = pd.Series(r.importances_mean, index=FEATURES).sort_values(ascending=False).head(n)
    return {k: round(float(v), 4) for k, v in s.items()}


def main():
    df = add_features(load())
    out = {
        "natural_lead": {c: natural_lead(df, c) for c in ["korea", "japan"]},
        "model_warning_lead": {c: warning_lead(df, c) for c in ["korea", "japan"]},
        "agency_tracking_start": agency_start(df),
        "track_turn_last_48h": track_turn(df),
        "feature_importance": {c: importance(df, c) for c in ["korea", "japan"]},
    }
    (ROOT / "reports" / "analysis.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
