"""전향 모델 개선 실험. 학습 기간(~2014) 안에서 태풍 단위 5겹 교차 예측으로만 후보를 고른다.

2015년 이후 자료는 고를 때 쓰지 않는다. 채택안은 turn.py 에서 한 번만 검증한다.
채점은 시점 AUC·Brier와 태풍 단위 값(전향 시작 전 몇 시간에 처음 예측했나, 전향 없던 태풍의 헛예측)을 함께 본다.

    python -m typhoon.turn_experiment      # → reports/turn_experiment.txt
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold

from .data import ROOT, haversine, load
from .features import ENV_FEATURES, FEATURES, add_env, add_features, bearing
from .review import actual_turn
from .track import MIN_MOVE_KM, TURN_DEG, add_future, angle_diff
from .train import CLF_PARAMS, SPLIT_YEAR

REPORT = ROOT / "reports" / "turn_experiment.txt"
SEG_H = 48
STARTS = {"48h": (0,), "72h": (0, 24), "96h": (0, 24, 48)}
LEADS = (0, 12, 24, 48, 72)
THRESHOLDS = (.3, .4, .5, .6, .7)


def add_direction_features(df):
    """진행 방향과 상공 바람의 관계처럼 나무가 스스로 만들기 어려운 값. 그 시점에 아는 값만 쓴다."""
    d = df.copy()
    now = bearing(d.LAT - d.dlat_24, d.LON - d.dlon_24, d.LAT, d.LON)
    recent = bearing(d.LAT - d.dlat_6, d.LON - d.dlon_6, d.LAT, d.LON)
    signed = lambda a, b: (a - b + 180) % 360 - 180   # noqa: E731
    d["turn_recent"] = signed(recent, now)                 # 최근 6시간이 하루 방향보다 얼마나 꺾였나
    if "atm_u_steer" in d:
        steer = (np.degrees(np.arctan2(d.atm_u_steer, d.atm_v_steer)) + 360) % 360
        d["steer_vs_heading"] = signed(steer, now)         # 상공 바람과 진행 방향의 차이
        d["steer_speed"] = np.hypot(d.atm_u_steer, d.atm_v_steer)
        d["steer_change"] = np.hypot(d.atm_u_steer - d.atm_u_steer_24, d.atm_v_steer - d.atm_v_steer_24)
        d["steer_along"] = d.steer_speed * np.cos(np.radians(d.steer_vs_heading))
        d["steer_across"] = d.steer_speed * np.sin(np.radians(d.steer_vs_heading))
    return d


DIRECTION_FEATURES = ["turn_recent", "steer_vs_heading", "steer_speed", "steer_change", "steer_along", "steer_across"]


def turn_labels(df, starts):
    """지금 진행 방향 대비, 지금부터 starts 시간에 시작하는 48시간 구간 중 하나라도 45도 이상 꺾이면 1.
    첫 구간 자료가 있는 시점만 쓴다. 뒤 구간은 태풍이 끝나 없으면 전향으로 세지 않는다(예보 판정 path_turns 와 같음)."""
    d = df.dropna(subset=["dlat_24", "dlon_24"]).copy()
    head = bearing(d.LAT - d.dlat_24, d.LON - d.dlon_24, d.LAT, d.LON)
    pos = df[["SID", "time", "LAT", "LON"]]
    y, ok = np.zeros(len(d), bool), None

    def at(h):
        return d[["SID", "time"]].assign(time=d.time + pd.Timedelta(hours=h)).merge(pos, on=["SID", "time"], how="left")
    for s in starts:
        a, b = at(s), at(s + SEG_H)
        move = haversine(a.LAT, a.LON, b.LAT, b.LON)
        turned = (move >= MIN_MOVE_KM) & (angle_diff(bearing(a.LAT, a.LON, b.LAT, b.LON), head.to_numpy()) >= TURN_DEG)
        y |= turned.fillna(False).to_numpy()
        if s == 0:
            ok = b.LAT.notna().to_numpy() & (move >= MIN_MOVE_KM).to_numpy()
    d["y"] = y.astype(int)
    return d[ok]


def oof_scores(t, feats, params=CLF_PARAMS):
    p = np.zeros(len(t))
    for a, b in GroupKFold(5).split(t, t.y, t.SID):
        m = HistGradientBoostingClassifier(**params).fit(t.iloc[a][feats], t.y.iloc[a])
        p[b] = m.predict_proba(t.iloc[b][feats])[:, 1]
    return p


def storm_scores(t, p, turns, th):
    """태풍 단위: 전향이 있던 태풍을 몇 시간 전에 처음 예측했나(120시간 전 ~ 시작), 없던 태풍의 헛예측 비율."""
    t = t.assign(p=p)
    leads, quiet, fa = [], 0, 0
    for sid, g in t.groupby("SID"):
        hits = g.time[g.p >= th]
        turn = turns.get(sid)
        if turn is None:
            quiet += 1
            fa += len(hits) > 0
            continue
        h = hits[(hits >= turn - pd.Timedelta(hours=120)) & (hits <= turn)]
        leads.append((turn - h.min()) / pd.Timedelta(hours=1) if len(h) else np.nan)
    leads = np.array(leads)
    return {"n": len(leads), **{f"l{h}": float(np.mean(leads >= h)) for h in LEADS},
            "median": float(np.nanmedian(leads)) if np.isfinite(leads).any() else float("nan"),
            "false": fa / quiet if quiet else float("nan"), "quiet": quiet}


def main():
    df = add_direction_features(add_env(add_features(load())))
    tr = df[df.SEASON < SPLIT_YEAR]
    tr = tr[tr.SEASON >= 1979]               # 전향 정의에 필요한 위치 자료 간격과 상공 자료 품질이 안정된 기간도 함께 본다
    turns = {sid: actual_turn(g.sort_values("time")) for sid, g in tr.groupby("SID")}
    env = [c for c in ENV_FEATURES if c in df]
    extra = [c for c in DIRECTION_FEATURES if c in df]
    lines = [f"학습 기간 1979~{SPLIT_YEAR - 1} 교차 예측, 태풍 {tr.SID.nunique()}개, 전향 있던 태풍 {sum(v is not None for v in turns.values())}개", "",
             "후보                      AUC   Brier  기준  미리 예측 비율(0/12/24/48/72시간 전)  중앙값  헛예측"]
    for horizon, starts in STARTS.items():
        t = turn_labels(tr, starts)
        for name, feats in (("기존 특성", [*FEATURES, *env]), ("방향 특성 추가", [*FEATURES, *env, *extra])):
            p = oof_scores(t, feats)
            auc, brier = roc_auc_score(t.y, p), brier_score_loss(t.y, p)
            for th in THRESHOLDS:
                s = storm_scores(t, p, turns, th)
                lines.append(f"{horizon} {name:<14} {auc:.3f} {brier:.3f} {th:.0%}  "
                             + " ".join(f"{s[f'l{h}']:>4.0%}" for h in LEADS)
                             + f"   {s['median']:>4.0f}h  {s['false']:.0%}({s['quiet']}개)")
            print(lines[-1], flush=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


GFS_SPLIT = pd.Timestamp("2024-01-01")
GFS_REPORT = ROOT / "reports" / "turn_gfs_experiment.txt"
SMALL = dict(max_iter=300, learning_rate=.04, max_leaf_nodes=8, min_samples_leaf=40, max_features=.7, random_state=0)
TRACK_THRESHOLDS = (.3, .4, .5, .6, .7, .8)


def add_gfs_direction(d):
    """GFS 예측 이동량 → 지금 진행 방향 대비 구간별 방향 변화(도, +: 오른쪽). 200km 미만 구간은 NaN."""
    d = d.copy()
    now = bearing(d.LAT - d.dlat_24, d.LON - d.dlon_24, d.LAT, d.LON)
    pos = {0: (d.LAT, d.LON), **{h: (d.LAT + d[f"gfs_dy_{h}"], d.LON + d[f"gfs_dx_{h}"]) for h in (24, 48, 72, 96)}}
    for a, b in ((0, 48), (24, 72), (48, 96)):
        move = haversine(*pos[a], *pos[b])
        d[f"gfs_turn_{a}_{b}"] = ((bearing(*pos[a], *pos[b]) - now + 180) % 360 - 180).where(move >= MIN_MOVE_KM)
        d[f"gfs_move_{a}_{b}"] = move
    d["gfs_turn_max"] = d[["gfs_turn_0_48", "gfs_turn_24_72", "gfs_turn_48_96"]].abs().max(axis=1)
    return d


GFS_DIR = ["gfs_age", "gfs_turn_0_48", "gfs_turn_24_72", "gfs_turn_48_96", "gfs_move_0_48", "gfs_move_24_72", "gfs_move_48_96", "gfs_turn_max"]
SMALL_BASE = ["LAT", "LON", "wind", "speed_kmh", "month", "turn_recent", "steer_vs_heading", "steer_speed", "atm_ridge_dlon", "atm_ridge_dlat"]


def gfs_main():
    """GFS 경로 특성 결합. 2021-06~2023 학습, 2024~ 평가(후보와 기준은 학습 기간 교차 예측으로 고른다)."""
    from .gfs_track import add_gfs
    from .turn import predict_turn
    df = add_gfs_direction(add_direction_features(add_gfs(add_env(add_features(load()), gfs=True))))
    df = df[df.gfs_dy_24.notna() | (df.time >= "2021-06-01")]
    df = df[df.time >= "2021-06-01"].assign(p_base=lambda x: predict_turn(x))
    turns = {sid: actual_turn(g.sort_values("time")) for sid, g in df.groupby("SID")}
    lines = [f"2021-06~{GFS_SPLIT:%Y-%m} 학습 / 이후 평가, 시점 {len(df)}개, 태풍 {df.SID.nunique()}개", ""]
    cands = {"기존 모델 + GFS 방향": ["p_base", *GFS_DIR], "기본 특성 + GFS 방향": [*SMALL_BASE, *GFS_DIR], "모두": ["p_base", *SMALL_BASE, *GFS_DIR]}
    for horizon, starts in (("48h", (0,)), ("96h", (0, 24, 48))):
        t = turn_labels(df, starts).dropna(subset=["gfs_turn_0_48"])
        tr, te = t[t.time < GFS_SPLIT], t[t.time >= GFS_SPLIT]
        lines.append(f"[{horizon}] 학습 {len(tr)}개({tr.SID.nunique()}개 태풍), 평가 {len(te)}개({te.SID.nunique()}개 태풍)")
        lines.append("  학습 기간 교차 예측: 후보 AUC Brier | 기준 | 미리 예측(0/12/24/48/72h 전) 중앙값 헛예측")
        best, best_b = None, 9
        for name, feats in cands.items():
            p = oof_scores(tr, feats, SMALL)
            b = brier_score_loss(tr.y, p)
            lines.append(f"  {name}: AUC {roc_auc_score(tr.y, p):.3f} Brier {b:.3f}")
            for th in TRACK_THRESHOLDS:
                s = storm_scores(tr, p, turns, th)
                lines.append(f"    {th:.0%} " + " ".join(f"{s[f'l{h}']:>4.0%}" for h in LEADS) + f"  {s['median']:>4.0f}h  {s['false']:.0%}({s['quiet']}개)")
            if b < best_b:
                best, best_b = name, b
        lines.append(f"  → Brier 최저: {best}")
        print("\n".join(lines), flush=True)
        GFS_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        lines.append("")
    return df, turns, cands


if __name__ == "__main__":
    import sys
    gfs_main() if "gfs" in sys.argv[1:] else main()
