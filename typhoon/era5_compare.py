"""ERA5 특성과 NCEP 특성 비교: 같은 시점에서 특성 상관, 그리고 NCEP으로 학습한 환경 모델에 둘을 넣었을 때의 검증 성능.

    python -m typhoon.era5_compare     → reports/era5_comparison.txt
"""
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss

from .data import load, ROOT
from .features import add_features, add_targets, load_model, model_features, ENV_FEATURES, FEATURES
from .train import SPLIT_YEAR

ENV = ROOT / "data" / "env"


def with_env(df, path):
    env = pd.read_parquet(path).drop_duplicates(["SID", "time"])
    d = df.merge(env, on=["SID", "time"], how="left")
    d["atm_dz_ew"] = d.atm_z500_e - d.atm_z500_w
    d["atm_dz_n"] = d.atm_z500_n - d.atm_z500
    d["atm_dz_max"] = d.atm_z500_max - d.atm_z500
    d["atm_ridge_dlon"] = d.atm_ridge_west - d.LON
    d["atm_ridge_dlat"] = d.atm_ridge_lat - d.LAT
    return d


def main():
    base = add_features(load())
    ncep, era = with_env(base, ENV / "atmos_features.parquet"), with_env(base, ENV / "era5_features.parquet")
    lines = []
    ok = ncep[ENV_FEATURES].notna().all(axis=1) & era[ENV_FEATURES].notna().all(axis=1) & (ncep.SEASON >= SPLIT_YEAR) & (ncep.SEASON <= 2025)
    lines.append(f"비교 가능한 시점(2015~2025, 두 자료 모두 값 있음): {int(ok.sum())}행")
    for c in ENV_FEATURES:
        x, y = ncep.loc[ok, c], era.loc[ok, c]
        lines.append(f"  {c:16s} 상관 {np.corrcoef(x, y)[0, 1]:.3f}  평균차(ERA5-NCEP) {float((y - x).mean()):+.2f}")
    for country in ["korea", "japan"]:
        full, track = load_model(country), load_model(country, full=False)
        f = model_features(full)
        t_n, t_e = add_targets(ncep[ok], country), add_targets(era[ok], country)
        t_e = t_e.loc[t_e.index.intersection(t_n.index)]; t_n = t_n.loc[t_e.index]
        res = {"경로 모델": track["clf"].predict_proba(t_n[model_features(track)])[:, 1],
               "환경 모델(NCEP 특성)": full["clf"].predict_proba(t_n[f])[:, 1],
               "환경 모델(ERA5 특성)": full["clf"].predict_proba(t_e[f])[:, 1]}
        lines.append(f"\n[{country}] 검증 {len(t_n)}행, 도달 {int(t_n.hit.sum())}행")
        for k, p in res.items():
            lines.append(f"  {k:22s} AUC {roc_auc_score(t_n.hit, p):.4f}  Brier {brier_score_loss(t_n.hit, p):.4f}")
    out = "\n".join(lines)
    (ROOT / "reports" / "era5_comparison.txt").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
