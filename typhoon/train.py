"""한국·일본 도착 예측 모델 학습. 2014년까지 학습, 2015년부터 검증.

경로 특성만 쓰는 모델({나라}.joblib, 환경 자료가 없을 때 대신 씀)과
대기 환경 특성까지 쓰는 모델({나라}_full.joblib, 기본)을 함께 만든다.
"""
import argparse
import json
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, brier_score_loss, mean_absolute_error
from sklearn.model_selection import GroupKFold

from .data import load, ROOT
from .features import (add_features, add_targets, add_env, env_columns, prob, model_features,
                       FEATURES)

SPLIT_YEAR = 2015
# 분류 모델 설정. 기본값(max_iter 400, lr 0.05)은 학습 자료를 외워 과신해서, 학습을 느리게 하고 나무를 작게 했다.
# 검증(2015~, 환경 모델): 한국 Brier 0.0344 → 0.0336, 일본 AUC 0.885 → 0.896, 한국 48시간 전 경보 32% → 39%
CLF_PARAMS = dict(max_iter=800, learning_rate=0.02, max_leaf_nodes=15, min_samples_leaf=300, max_features=0.5,
                  random_state=0)
# 도착 시간 회귀도 같은 설정(검증 MAE 한국 경로 14.6 → 14.4시간)
REG_PARAMS = CLF_PARAMS


def arrival_range(tr, features):
    """남은 시간 범위 모델(목표 80%, 검증 포함 비율 74~79%): 10%·90% 분위 회귀와 폭 보정 delta(시간).
    분위 모델만으로는 검증에서 66~69%만 들어와(학습 자료 과적합) 학습 기간 안 태풍 단위 5겹 교차 예측의
    범위 밖 거리 80% 분위를 양쪽에 더한다(conformalized quantile regression)."""
    q = lambda a: HistGradientBoostingRegressor(loss="quantile", quantile=a, **REG_PARAMS)  # noqa: E731
    y = tr.hours_to.to_numpy()
    lo_o, hi_o = np.zeros(len(tr)), np.zeros(len(tr))
    for a, b in GroupKFold(5).split(tr, y, tr.SID):
        lo_o[b] = q(.1).fit(tr.iloc[a][features], y[a]).predict(tr.iloc[b][features])
        hi_o[b] = q(.9).fit(tr.iloc[a][features], y[a]).predict(tr.iloc[b][features])
    delta = round(float(np.quantile(np.maximum(lo_o - y, y - hi_o), 0.8)), 2)
    return q(.1).fit(tr[features], y), q(.9).fit(tr[features], y), delta


def calibration(tr, features):
    """학습 기간 안에서 태풍 단위 5겹 교차 예측 점수로 Platt 보정 [a, b] 를 맞춘다.
    학습 자료에 맞춘 트리는 학습 자료에서 과신하므로(학습 자료 '50%'가 실제 94%) 교차 예측으로만 맞춘다.
    검증(2015~)에서 한국 '50~70%'가 실제 38%로 과신, 일본 '10~20%'가 실제 24%로 과소였던 것을 바로잡는다."""
    oof = np.zeros(len(tr))
    for a, b in GroupKFold(5).split(tr, tr.hit, tr.SID):
        m = HistGradientBoostingClassifier(**CLF_PARAMS)
        m.fit(tr.iloc[a][features], tr.hit.iloc[a])
        oof[b] = m.decision_function(tr.iloc[b][features])
    lr = LogisticRegression().fit(oof[:, None], tr.hit)
    return [round(float(lr.coef_[0][0]), 4), round(float(lr.intercept_[0]), 4)]


def train_country(df, country, features, name):
    d = add_targets(df, country)
    tr, te = d[d.SEASON < SPLIT_YEAR], d[d.SEASON >= SPLIT_YEAR]
    clf = HistGradientBoostingClassifier(**CLF_PARAMS)
    clf.fit(tr[features], tr.hit)
    calib = calibration(tr, features)
    raw = clf.predict_proba(te[features])[:, 1]
    p = prob({"clf": clf, "calib": calib}, te[features])

    trp, tep = tr[tr.hit == 1], te[te.hit == 1]
    reg = HistGradientBoostingRegressor(**REG_PARAMS)
    reg.fit(trp[features], trp.hours_to)
    mae = mean_absolute_error(tep.hours_to, reg.predict(tep[features]))
    lo, hi, delta = arrival_range(trp, features)
    y = tep.hours_to.to_numpy()
    lo_t = np.maximum(lo.predict(tep[features]) - delta, 0)
    hi_t = hi.predict(tep[features]) + delta

    metrics = {"auc": round(roc_auc_score(te.hit, p), 4), "brier": round(brier_score_loss(te.hit, p), 4),
               "brier_uncalibrated": round(brier_score_loss(te.hit, raw), 4), "calib": calib,
               "arrival_mae_h": round(mae, 1),
               "arrival_range80_coverage": round(float(((y >= lo_t) & (y <= hi_t)).mean()), 3),
               "arrival_range80_median_width_h": round(float(np.median(hi_t - lo_t)), 1), "arrival_range_delta_h": delta,
               "train_rows": len(tr), "test_rows": len(te),
               "test_storms_hit": int(te[te.hit == 1].SID.nunique()), "n_features": len(features)}
    joblib.dump({"clf": clf, "reg": reg, "lo": lo, "hi": hi, "delta": delta, "features": features, "calib": calib},
                ROOT / "models" / f"{name}.joblib")
    return metrics


def serving_metrics(df, country):
    """실제 서비스 방식(환경 자료 없으면 경로 모델로 대체, GFS 예측 경로가 있으면 결합)으로 검증 기간 전체 평가.
    GFS 결합 계수는 2021-06 이후 전체로 맞춘 값이라 그 기간은 학습 자료가 섞인다(연도별 교차 성적은 gfs_arrival 보고서)."""
    from .gfs_arrival import serve
    te = add_targets(df[df.SEASON >= SPLIT_YEAR], country)
    p, h, lo, hi = serve(country, te)
    hit = te.hit.to_numpy() == 1
    y = te.hours_to.to_numpy()[hit]
    return {"auc": round(roc_auc_score(te.hit, p), 4), "brier": round(brier_score_loss(te.hit, p), 4),
            "arrival_mae_h": round(mean_absolute_error(te.hours_to[hit], h[hit]), 1),
            "arrival_range80_coverage": round(float(((y >= lo[hit]) & (y <= hi[hit])).mean()), 3),
            "env_rows_ratio": round(float(te[env_columns(te)].notna().any(axis=1).mean()), 3)}


def calibrate_only(df):
    """트리는 그대로 두고 기존 모델 파일에 보정만 넣는다(재학습하면 IBTrACS 개정으로 트리가 조금 바뀔 수 있어서)."""
    out = json.loads((ROOT / "reports" / "metrics.json").read_text())
    for c in ["korea", "japan"]:
        tr = add_targets(df[df.SEASON < SPLIT_YEAR], c)
        te = add_targets(df[df.SEASON >= SPLIT_YEAR], c)
        for name, sec in [(c, out), (f"{c}_full", out.get("full", {}))]:
            path = ROOT / "models" / f"{name}.joblib"
            if not path.exists():
                continue
            m = joblib.load(path)
            m.pop("calib", None)
            feats = model_features(m)
            raw = prob(m, te[feats])
            m["calib"] = calibration(tr, feats)
            joblib.dump(m, path)
            p = prob(m, te[feats])
            sec[c].update({"brier": round(brier_score_loss(te.hit, p), 4),
                           "brier_uncalibrated": round(brier_score_loss(te.hit, raw), 4), "calib": m["calib"]})
            print(name, m["calib"], sec[c]["brier_uncalibrated"], "->", sec[c]["brier"])
        if "full" in out:
            out["full"][c].update(serving_metrics(df, c))
    (ROOT / "reports" / "metrics.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate-only", action="store_true", help="트리는 두고 확률 보정만 다시 맞춤")
    ap.add_argument("--serving-only", action="store_true", help="모델은 두고 실제 서비스 방식 성적만 다시 계산")
    a = ap.parse_args()
    df = add_env(add_features(load()), gfs=a.serving_only)
    if a.calibrate_only:
        return calibrate_only(df)
    if a.serving_only:
        out = json.loads((ROOT / "reports" / "metrics.json").read_text(encoding="utf-8"))
        for c in ["korea", "japan"]:
            out["full"][c].update(serving_metrics(df, c))
        (ROOT / "reports" / "metrics.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        return print(json.dumps(out["full"], indent=2, ensure_ascii=False))
    (ROOT / "models").mkdir(exist_ok=True)
    out = {c: train_country(df, c, FEATURES, c) for c in ["korea", "japan"]}
    env = env_columns(df)
    if env:
        out["full"] = {c: train_country(df, c, FEATURES + env, f"{c}_full") for c in ["korea", "japan"]}
        for c in ["korea", "japan"]:
            out["full"][c].update(serving_metrics(df, c))
    (ROOT / "reports" / "metrics.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
