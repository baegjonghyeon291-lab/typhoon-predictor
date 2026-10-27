"""한국·일본 도착 예측 모델 학습. 2014년까지 학습, 2015년부터 검증."""
import json
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score, brier_score_loss, mean_absolute_error

from .data import load, ROOT
from .features import add_features, add_targets, FEATURES

SPLIT_YEAR = 2015


def train_country(df, country):
    d = add_targets(df, country)
    tr, te = d[d.SEASON < SPLIT_YEAR], d[d.SEASON >= SPLIT_YEAR]
    clf = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, random_state=0)
    clf.fit(tr[FEATURES], tr.hit)
    p = clf.predict_proba(te[FEATURES])[:, 1]

    trp, tep = tr[tr.hit == 1], te[te.hit == 1]
    reg = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, random_state=0)
    reg.fit(trp[FEATURES], trp.hours_to)
    mae = mean_absolute_error(tep.hours_to, reg.predict(tep[FEATURES]))

    metrics = {"auc": round(roc_auc_score(te.hit, p), 4), "brier": round(brier_score_loss(te.hit, p), 4),
               "arrival_mae_h": round(mae, 1), "train_rows": len(tr), "test_rows": len(te),
               "test_storms_hit": int(te[te.hit == 1].SID.nunique())}
    joblib.dump({"clf": clf, "reg": reg}, ROOT / "models" / f"{country}.joblib")
    return metrics


def main():
    df = add_features(load())
    (ROOT / "models").mkdir(exist_ok=True)
    out = {c: train_country(df, c) for c in ["korea", "japan"]}
    (ROOT / "reports" / "metrics.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
