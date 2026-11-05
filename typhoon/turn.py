"""48시간 전향 분류 모델: 앞으로 48시간 방향이 지난 24시간 방향보다 45도 이상 꺾일 확률(정의는 track.turn_table).

도달 모델과 같이 2014년까지 학습, 2015년부터 검증하고, 상공 자료가 없는 시점은 경로 특성 모델로 대신한다.

    python -m typhoon.turn      # → models/turn.joblib, models/turn_full.joblib, reports/turn_metrics.json
"""
import json

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

from .data import ROOT, load
from .features import FEATURES, _by_model, add_env, add_features, env_columns, prob
from .track import turn_table
from .train import CLF_PARAMS, SPLIT_YEAR, calibration

REPORT = ROOT / "reports" / "turn_metrics.json"
CALL_P = .5              # 이 확률 이상이면 전향 예측으로 본다(복기·사이트 공통)


def predict_turn(d):
    """시점별 48시간 전향 확률. 지난 24시간 방향이 없는 시점은 정의가 안 되므로 NaN."""
    p = np.full(len(d), np.nan)
    for m, feats, mask in _by_model("turn", d):
        if mask.any():
            p[mask] = prob(m, d.loc[mask, feats])
    p[(d.dlat_24.isna() | d.dlon_24.isna()).to_numpy()] = np.nan
    return p


def _fit(tr, feats, name):
    clf = HistGradientBoostingClassifier(**CLF_PARAMS).fit(tr[feats], tr.turn)
    m = {"clf": clf, "features": feats, "calib": calibration(tr, feats, "turn")}
    joblib.dump(m, ROOT / "models" / f"{name}.joblib")
    return m


def main():
    df = add_env(add_features(load()))
    tr, te = turn_table(df[df.SEASON < SPLIT_YEAR]), turn_table(df[df.SEASON >= SPLIT_YEAR])
    _fit(tr, FEATURES, "turn")
    env = env_columns(df)
    if env:
        _fit(tr, [*FEATURES, *env], "turn_full")
    p, y = predict_turn(te), te.turn.to_numpy()
    called = p >= CALL_P
    out = {"auc": round(roc_auc_score(y, p), 4), "brier": round(brier_score_loss(y, p), 4), "test_rows": len(te),
           "test_turn_ratio": round(float(y.mean()), 3), "call_p": CALL_P,
           "call_hit_ratio": round(float(y[called].mean()), 3), "turn_caught_ratio": round(float(called[y == 1].mean()), 3),
           "calib_bins": [{"p": f"{a:.0%}~{b:.0%}", "n": int(w.sum()), "actual": round(float(y[w].mean()), 3)}
                          for a, b in zip(np.arange(0, 1, .2), np.arange(.2, 1.01, .2))
                          if (w := (p >= a) & (p < b + (b >= 1))).any()]}
    REPORT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    main()
