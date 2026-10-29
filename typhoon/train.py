"""한국·일본 도착 예측 모델 학습. 2014년까지 학습, 2015년부터 검증.

경로 특성만 쓰는 모델({나라}.joblib, 사이트의 직접 예측용)과
대기·해양 환경 특성까지 쓰는 모델({나라}_full.joblib, 실제 태풍 예측용)을 함께 만든다.
"""
import json
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score, brier_score_loss, mean_absolute_error

from .data import load, ROOT
from .features import add_features, add_targets, add_env, env_columns, predict, FEATURES

SPLIT_YEAR = 2015


def train_country(df, country, features, name):
    d = add_targets(df, country)
    tr, te = d[d.SEASON < SPLIT_YEAR], d[d.SEASON >= SPLIT_YEAR]
    clf = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, random_state=0)
    clf.fit(tr[features], tr.hit)
    p = clf.predict_proba(te[features])[:, 1]

    trp, tep = tr[tr.hit == 1], te[te.hit == 1]
    reg = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, random_state=0)
    reg.fit(trp[features], trp.hours_to)
    mae = mean_absolute_error(tep.hours_to, reg.predict(tep[features]))

    metrics = {"auc": round(roc_auc_score(te.hit, p), 4), "brier": round(brier_score_loss(te.hit, p), 4),
               "arrival_mae_h": round(mae, 1), "train_rows": len(tr), "test_rows": len(te),
               "test_storms_hit": int(te[te.hit == 1].SID.nunique()), "n_features": len(features)}
    joblib.dump({"clf": clf, "reg": reg, "features": features}, ROOT / "models" / f"{name}.joblib")
    return metrics


def serving_metrics(df, country):
    """실제 서비스 방식(환경 자료 없으면 경로 모델로 대체)으로 검증 기간 전체 평가."""
    te = add_targets(df[df.SEASON >= SPLIT_YEAR], country)
    p, h = predict(country, te)
    hit = te.hit.to_numpy() == 1
    return {"auc": round(roc_auc_score(te.hit, p), 4), "brier": round(brier_score_loss(te.hit, p), 4),
            "arrival_mae_h": round(mean_absolute_error(te.hours_to[hit], h[hit]), 1),
            "env_rows_ratio": round(float(te[env_columns(te)].notna().any(axis=1).mean()), 3)}


def main():
    df = add_env(add_features(load()))
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
