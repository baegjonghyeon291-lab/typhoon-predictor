"""태풍 경로·전향 예측 실험: 지금 상태와 상공 자료로 h시간 뒤 위치 변화와 진로 꺾임을 예측한다.

학습 ~2014, 검증 2015~ (태풍 단위 기간 분리). 현재 진행 방향을 그대로 연장한 예측과 비교한다.

    python -m typhoon.track      # → reports/track_experiment.txt
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score

from .data import ROOT, haversine, load
from .features import ENV_FEATURES, FEATURES, add_env, add_features, bearing
from .train import CLF_PARAMS, REG_PARAMS, SPLIT_YEAR

LEADS = [24, 48, 72, 96, 120]
TURN_LEAD = 48
TURN_DEG = 45         # 지금 진행 방향과 앞으로의 방향이 이만큼 다르면 전향
MIN_MOVE_KM = 200     # 거의 멈춰 방향이 무의미한 경우는 전향 채점에서 뺀다
REPORT = ROOT / "reports" / "track_experiment.txt"


def angle_diff(a, b):
    return np.abs((a - b + 180) % 360 - 180)


def add_future(df, h):
    """h시간 뒤 같은 태풍의 위치 변화 열(dy, dx)을 붙인다. 없는 시점은 NaN."""
    f = df[["SID", "time", "LAT", "LON"]].copy()
    f["time"] = f.time - pd.Timedelta(hours=h)
    f = f.rename(columns={"LAT": "fut_lat", "LON": "fut_lon"})
    d = df.merge(f, on=["SID", "time"], how="left")
    d["dy"] = d.fut_lat - d.LAT
    d["dx"] = (d.fut_lon - d.LON + 180) % 360 - 180
    return d


def persistence(d, h):
    """지난 24시간(없으면 6시간) 이동을 h시간 연장한 위치 변화."""
    dy = (d.dlat_24 * h / 24).fillna(d.dlat_6 * h / 6).fillna(0)
    dx = (d.dlon_24 * h / 24).fillna(d.dlon_6 * h / 6).fillna(0)
    return dy.to_numpy(), dx.to_numpy()


def error_km(d, py, px):
    return haversine(d.LAT + py, d.LON + px, d.fut_lat, d.fut_lon)


def fit_track(train, feats, h):
    t = add_future(train, h).dropna(subset=["dy", "dx"])
    # 지금 진행 방향 연장에서 벗어나는 만큼을 배운다
    py, px = persistence(t, h)
    my = HistGradientBoostingRegressor(**REG_PARAMS).fit(t[feats], t.dy - py)
    mx = HistGradientBoostingRegressor(**REG_PARAMS).fit(t[feats], t.dx - px)
    return my, mx


def predict_track(models, d, feats, h):
    py, px = persistence(d, h)
    my, mx = models
    return py + my.predict(d[feats]), px + mx.predict(d[feats])


def turn_table(df, lead=TURN_LEAD):
    """전향 정답 표: 지난 24시간 방향 대비 앞으로 lead시간 방향 차이."""
    d = add_future(df, lead).dropna(subset=["dy", "dx", "dlat_24", "dlon_24"]).copy()
    d["head_now"] = bearing(d.LAT - d.dlat_24, d.LON - d.dlon_24, d.LAT, d.LON)
    d["head_fut"] = bearing(d.LAT, d.LON, d.fut_lat, d.fut_lon)
    d["move"] = haversine(d.LAT, d.LON, d.fut_lat, d.fut_lon)
    d = d[d.move >= MIN_MOVE_KM].copy()
    d["turn"] = (angle_diff(d.head_fut, d.head_now) >= TURN_DEG).astype(int)
    return d


def main():
    df = add_env(add_features(load()))
    feats = [*FEATURES, *[c for c in ENV_FEATURES if c in df]]
    train, test = df[df.SEASON < SPLIT_YEAR], df[df.SEASON >= SPLIT_YEAR]
    lines = [f"학습 ~{SPLIT_YEAR - 1} {train.SID.nunique()}개, 검증 {SPLIT_YEAR}~ {test.SID.nunique()}개, 특성 {len(feats)}개", "",
             "위치 오차(km) 시간  시점 수  연장 평균  모델 평균  개선  연장 중앙  모델 중앙"]
    models = {}
    for h in LEADS:
        models[h] = fit_track(train, feats, h)
        t = add_future(test, h).dropna(subset=["dy", "dx"])
        base = error_km(t, *persistence(t, h))
        model = error_km(t, *predict_track(models[h], t, feats, h))
        lines.append(f"{h:>4}h {len(t):>7} {base.mean():>9.0f} {model.mean():>9.0f} {1 - model.mean() / base.mean():>6.0%}"
                     f" {base.median():>9.0f} {model.median():>9.0f}")
    tr, te = turn_table(train), turn_table(test)
    clf = HistGradientBoostingClassifier(**CLF_PARAMS).fit(tr[feats], tr.turn)
    p = clf.predict_proba(te[feats])[:, 1]
    py, px = predict_track(models[TURN_LEAD], te, feats, TURN_LEAD)
    pred_turn = angle_diff(bearing(te.LAT, te.LON, te.LAT + py, te.LON + px), te.head_now) >= TURN_DEG
    y = te.turn.to_numpy()
    lines += ["", f"{TURN_LEAD}시간 전향({TURN_DEG}도 이상): 시점 {len(te)}개, 실제 전향 {y.mean():.1%}",
              f"전향 분류 AUC {roc_auc_score(y, p):.3f}",
              f"경로 모델의 전향 판단: 맞힘 {pred_turn[y == 1].mean():.1%}, 예측 중 실제 {y[pred_turn].mean():.1%}"]
    for th in (.3, .5):
        w = p >= th
        lines.append(f"분류 {th:.0%} 이상: 맞힘 {w[y == 1].mean():.1%}, 예측 중 실제 {y[w].mean():.1%}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return models, clf, feats


if __name__ == "__main__":
    main()
