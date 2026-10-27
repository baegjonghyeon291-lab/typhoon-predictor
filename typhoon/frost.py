"""서리 예측: 오늘 관측으로 내일 서리(일 최저 초상온도 0℃ 이하) 여부. 1973–2014 학습, 2015– 검증.

    python -m typhoon.sources.kma_asos   # 자료 받기
    python -m typhoon.frost              # 학습 → models/frost.joblib, reports/frost_metrics.json
"""
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, brier_score_loss, f1_score

from .data import ROOT

ASOS = ROOT / "data" / "kma" / "asos" / "asos_daily.csv"
SPLIT_YEAR = 2015
OBS = ["TA_AVG", "TA_MAX", "TA_MIN", "TD_AVG", "TS_AVG", "TG_MIN", "HM_AVG", "HM_MIN", "WS_AVG", "WS_MAX",
       "PS_AVG", "CA_TOT", "SS_DAY", "RN_DAY", "SD_MAX"]
FEATURES = OBS + ["d_TA_MIN", "d_PS_AVG", "d_TD_AVG", "doy_sin", "doy_cos", "STN"]


def build():
    d = pd.read_csv(ASOS, parse_dates=["TM"]).sort_values(["STN", "TM"])
    g = d.groupby("STN")
    for c in ["TA_MIN", "PS_AVG", "TD_AVG"]:
        d[f"d_{c}"] = d[c] - g[c].shift(1)
    doy = d.TM.dt.dayofyear
    d["doy_sin"], d["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    nxt = g.TM.shift(-1)
    d["target"] = g.frost.shift(-1)
    d = d[(nxt - d.TM == pd.Timedelta(days=1)) & d.target.notna()].copy()
    d["target"] = d.target.astype(int)
    return d


def main():
    d = build()
    # 서리가 생길 수 있는 철(10월~4월)만 평가에 의미가 있어 그 기간으로 학습·검증
    d = d[d.TM.dt.month.isin([10, 11, 12, 1, 2, 3, 4])]
    tr, te = d[d.TM.dt.year < SPLIT_YEAR], d[d.TM.dt.year >= SPLIT_YEAR]
    clf = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, random_state=0,
                                         categorical_features=[FEATURES.index("STN")])
    clf.fit(tr[FEATURES], tr.target)
    p = clf.predict_proba(te[FEATURES])[:, 1]
    # 비교 기준: 오늘 서리였으면 내일도 서리
    persist = te.frost.fillna(0).astype(int)
    out = {"auc": round(roc_auc_score(te.target, p), 4), "brier": round(brier_score_loss(te.target, p), 4),
           "f1": round(f1_score(te.target, p >= 0.5), 4), "persistence_f1": round(f1_score(te.target, persist), 4),
           "train_days": len(tr), "test_days": len(te), "frost_rate_test": round(float(te.target.mean()), 3),
           "by_station_auc": {str(s): round(roc_auc_score(x.target, p[te.STN.values == s]), 4)
                              for s, x in te.groupby("STN")}}
    joblib.dump({"clf": clf, "features": FEATURES}, ROOT / "models" / "frost.joblib")
    (ROOT / "reports" / "frost_metrics.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
