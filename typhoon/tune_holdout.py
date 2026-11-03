"""모델 설정(train.CLF_PARAMS)과 경보 기준(analyze.THRESHOLD)을 2015년~ 검증 성능으로 골랐기 때문에,
그 기간을 보지 않아도 같은 선택이 나오는지 확인한다. 학습 ~2004, 비교 2005~2014. 2015년~ 자료는 쓰지 않는다.

    python -m typhoon.tune_holdout     → reports/tuning_holdout.txt
"""
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold

from .data import ROOT, load
from .features import FEATURES, add_env, add_features, add_targets, env_columns
from .train import CLF_PARAMS, SPLIT_YEAR

TRAIN_END, HOLD_END = 2004, SPLIT_YEAR - 1
BASE = dict(max_iter=400, learning_rate=0.05, random_state=0)
CONFIGS = {
    "기존(400회, lr 0.05)": BASE,
    "잎당 최소 200": {**BASE, "min_samples_leaf": 200},
    "잎당 최소 500 + L2": {**BASE, "min_samples_leaf": 500, "l2_regularization": 1.0},
    "얕게(잎 8개)": {**BASE, "max_leaf_nodes": 8},
    "채택(800회, lr 0.02, 잎 15개, 잎당 300, 특성 절반)": CLF_PARAMS,
}
THRESHOLDS = (.3, .4, .5)


def cross_platt(score, y, groups):
    """비교 기간 안에서 태풍 단위 2겹으로 Platt 보정(맞춘 태풍에 다시 쓰지 않음)."""
    p = np.zeros(len(y))
    for a, b in GroupKFold(2).split(score, y, groups):
        p[b] = LogisticRegression().fit(score[a, None], y[a]).predict_proba(score[b, None])[:, 1]
    return p


def warnings_by_storm(d, p, t):
    """(첫 경보 중앙값, 48시간 전 경보 비율, 경보 태풍 수, 그중 오지 않은 비율). 경보 없이 온 태풍의 앞당김은 0시간."""
    g = d.assign(p=p)
    came = g[g.arrival.notna()]
    lead = [w.hours_to.max() if len(w := s[s.p >= t]) else 0.0 for _, s in came.groupby("SID")]
    warned = g[g.p >= t].groupby("SID").arrival.first()
    return (float(np.median(lead)), float((np.array(lead) >= 48).mean()), len(warned),
            float(warned.isna().mean()) if len(warned) else float("nan"))


def main():
    df = add_env(add_features(load()))
    features = FEATURES + env_columns(df)
    lines = [f"학습 ~{TRAIN_END}, 비교 {TRAIN_END + 1}~{HOLD_END}, 환경 모델 특성 {len(features)}개, 경보 기준 40%"]
    for c in ["korea", "japan"]:
        d = add_targets(df[df.SEASON <= HOLD_END], c)
        tr, te = d[d.SEASON <= TRAIN_END], d[d.SEASON > TRAIN_END].copy()
        y, groups = te.hit.to_numpy(), te.SID.to_numpy()
        lines.append(f"\n[{c}] 비교 {len(te)}개 시점, 영향 태풍 {te[te.hit == 1].SID.nunique()}개")
        rows = []
        for name, params in CONFIGS.items():
            m = HistGradientBoostingClassifier(**params).fit(tr[features], tr.hit)
            score = m.decision_function(te[features])
            p = cross_platt(score, y, groups)
            lead, w48, _, false = warnings_by_storm(te, p, .4)
            rows.append((name, roc_auc_score(y, score), brier_score_loss(y, p), lead, w48, false, p))
            lines.append(f"  {name:<36} AUC {rows[-1][1]:.4f}  Brier {rows[-1][2]:.4f}  첫 경보 {lead:.0f}시간  "
                         f"48시간 전 {w48:.0%}  헛경보 {false:.0%}")
        lines.append(f"  → Brier 최저: {min(rows, key=lambda r: r[2])[0]}")
        chosen = rows[-1]
        lines.append(f"  채택 설정의 경보 기준별:")
        for t in THRESHOLDS:
            lead, w48, n, false = warnings_by_storm(te, chosen[-1], t)
            lines.append(f"    {t:.0%}: 첫 경보 {lead:.0f}시간, 48시간 전 {w48:.0%}, 경보 태풍 {n}개 중 안 온 비율 {false:.0%}")
    text = "\n".join(lines)
    (ROOT / "reports" / "tuning_holdout.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
