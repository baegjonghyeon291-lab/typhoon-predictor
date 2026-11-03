"""예측 건별 근거: 특성 묶음이 도달 확률을 몇 %p 올리거나 내렸는지(TreeSHAP).

SHAP 값은 트리 점수(로그 오즈) 단위다. 묶음을 뺐을 때의 보정 확률과 지금 확률의 차이를 %p로 바꾼다.
서로 관련된 특성(위경도와 도시 거리 등)은 하나씩 보면 값이 나뉘어 읽기 어려워 묶어서 합친다.
"""
import numpy as np
import pandas as pd

from .features import _by_model

GROUPS = {
    "계절": ["month", "doy"],
    "위치": ["LAT", "LON", "dist_seoul", "bearing_seoul", "dist_tokyo", "bearing_tokyo"],
    "이동": ["dlat_6", "dlon_6", "dlat_24", "dlon_24", "speed_kmh", "heading"],
    "강도": ["wind", "pres", "dwind_24"],
    "발생 후 시간": ["age_h"],
    "상공 바람": ["atm_u_steer", "atm_v_steer", "atm_u_steer_24", "atm_v_steer_24", "atm_shear"],
    "북태평양 고기압": ["atm_dz_ew", "atm_dz_n", "atm_dz_max", "atm_ridge_dlon", "atm_ridge_dlat"],
}
TOP_N = 3
MIN_PP = 1                 # 1%p 미만은 표시하지 않는다

_EXPLAINERS = {}           # 모델 객체 id -> TreeExplainer


def _explainer(m):
    import shap
    key = id(m["clf"])
    if key not in _EXPLAINERS:
        _EXPLAINERS[key] = shap.TreeExplainer(m["clf"])
    return _EXPLAINERS[key]


def group_values(m, feats, X):
    """(행 × 묶음 SHAP 표, 기준값). 기준값 + 행 합 = 트리 점수."""
    ex = _explainer(m)
    values = np.asarray(ex.shap_values(X[feats]))
    frame = pd.DataFrame(values, columns=feats, index=X.index)
    groups = pd.DataFrame({g: frame[[c for c in cols if c in feats]].sum(axis=1) for g, cols in GROUPS.items()
                           if any(c in feats for c in cols)})
    return groups, float(np.ravel(ex.expected_value)[0])


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def contributions(country, d):
    """행마다 [[묶음 이름, %p], ...]. 영향이 큰 순으로 TOP_N개, MIN_PP 미만은 뺀다."""
    out = [[] for _ in range(len(d))]
    pos = np.arange(len(d))
    for m, feats, mask in _by_model(country, d):
        if not mask.any():
            continue
        X = d.loc[mask, feats]
        groups, _ = group_values(m, feats, X)
        raw = m["clf"].decision_function(X)
        a, b = m.get("calib", (1.0, 0.0))
        for i, score, (_, row) in zip(pos[mask], raw, groups.iterrows()):
            pp = (_sigmoid(a * score + b) - _sigmoid(a * (score - row.to_numpy()) + b)) * 100
            ranked = sorted(zip(row.index, pp), key=lambda v: -abs(v[1]))
            out[i] = [[g, int(round(v))] for g, v in ranked[:TOP_N] if abs(v) >= MIN_PP]
    return out


def safe_contributions(country, d):
    """근거는 부가 정보다. 계산이 실패해도 예측 갱신은 이어 간다."""
    import warnings
    try:
        return contributions(country, d)
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"예측 근거 계산 실패: {type(e).__name__}: {e}", RuntimeWarning)
        return [[] for _ in range(len(d))]
