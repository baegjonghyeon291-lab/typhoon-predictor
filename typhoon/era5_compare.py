"""ERA5 특성과 NCEP 특성 비교: 같은 시점에서 특성 상관, 그리고 NCEP으로 학습한 환경 모델에 둘을 넣었을 때의 검증 성능.

    python -m typhoon.era5_compare [--start 2015 --end 2025]   → reports/era5_comparison.txt
"""
import argparse

import numpy as np
from sklearn.metrics import roc_auc_score, brier_score_loss

from .data import load, ROOT
from .features import add_features, add_targets, add_env, load_model, model_features, ENV_FEATURES

ENV = ROOT / "data" / "env"
NCEP, ERA5 = ENV / "atmos_features.parquet", ENV / "era5_features.parquet"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2015)
    ap.add_argument("--end", type=int, default=2025)
    a = ap.parse_args()
    base = add_features(load())
    lines = []

    def usable(n, e):
        """두 자료 모두 모든 환경 특성 값이 있고 비교 기간 안인 행."""
        return (n[ENV_FEATURES].notna().all(axis=1) & e[ENV_FEATURES].notna().all(axis=1)
                & n.SEASON.between(a.start, a.end))

    n0, e0 = add_env(base, files=[NCEP]), add_env(base, files=[ERA5])
    ok0 = usable(n0, e0)
    lines.append(f"비교 가능한 시점({a.start}~{a.end}, 두 자료 모두 값 있음): {int(ok0.sum())}행")
    for c in ENV_FEATURES:
        x, y = n0.loc[ok0, c], e0.loc[ok0, c]
        if len(x) > 2:
            lines.append(f"  {c:16s} 상관 {np.corrcoef(x, y)[0, 1]:.3f}  평균차(ERA5-NCEP) {float((y - x).mean()):+.2f}")
    for country in ["korea", "japan"]:
        # 도착 시각(정답)은 걸러내기 전 전체 경로에서 먼저 만든다(행을 먼저 거르면 첫 도착이 바뀐다)
        t = add_targets(base, country)
        n, e = add_env(t, files=[NCEP]), add_env(t, files=[ERA5])
        ok = usable(n, e)
        n, e = n[ok], e[ok]
        if n.hit.nunique() < 2:
            lines.append(f"\n[{country}] 검증 행 {len(n)}개, 도달/비도달이 한쪽뿐이라 AUC 계산 불가")
            continue
        full, track = load_model(country), load_model(country, full=False)
        res = {"경로 모델": track["clf"].predict_proba(n[model_features(track)])[:, 1],
               "환경 모델(NCEP 특성)": full["clf"].predict_proba(n[model_features(full)])[:, 1],
               "환경 모델(ERA5 특성)": full["clf"].predict_proba(e[model_features(full)])[:, 1]}
        lines.append(f"\n[{country}] 검증 {len(n)}행, 도달 {int(n.hit.sum())}행")
        for k, p in res.items():
            lines.append(f"  {k:22s} AUC {roc_auc_score(n.hit, p):.4f}  Brier {brier_score_loss(n.hit, p):.4f}")
    out = "\n".join(lines)
    (ROOT / "reports" / "era5_comparison.txt").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
