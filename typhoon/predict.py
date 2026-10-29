"""태풍 하나의 최신 위치로 한국·일본 도착 확률과 예상 도착 시각 예측.

사용법:
  python -m typhoon.predict                # 데이터에서 가장 최근 태풍
  python -m typhoon.predict --sid 2025xxx  # 특정 태풍(IBTrACS SID)
  python -m typhoon.predict --update       # 최신 데이터 다시 받은 뒤 예측
"""
import argparse
import pandas as pd

from .data import load, download, ROOT
from .features import add_features, add_env, predict as predict_rows, HORIZON_H


def fmt(v, unit):
    return "-" if pd.isna(v) else f"{v:.0f}{unit}"


def predict(sid=None):
    df = add_env(add_features(load(start_year=2000)))
    if sid is None:
        sid = df.loc[df.time.idxmax(), "SID"]
    g = df[df.SID == sid]
    if g.empty:
        raise SystemExit(f"SID {sid} 없음")
    last = g.iloc[[-1]]
    print(f"{g.NAME.iloc[0]} ({sid})  기준 {last.time.iloc[0]:%Y-%m-%d %H시} UTC  "
          f"위치 {last.LAT.iloc[0]:.1f}N {last.LON.iloc[0]:.1f}E  풍속 {fmt(last.wind.iloc[0], 'kt')}  기압 {fmt(last.pres.iloc[0], 'hPa')}")
    for c, label in [("korea", "한국"), ("japan", "일본")]:
        if g[f"in_{c}"].any():
            print(f"  {label}: 이미 영향권 통과 ({g[g[f'in_{c}']].time.min():%Y-%m-%d %H시})")
            continue
        p, h = (float(v[0]) for v in predict_rows(c, last))
        eta = last.time.iloc[0] + pd.Timedelta(hours=h)
        print(f"  {label}: {HORIZON_H}시간 내 영향 확률 {p:.0%}" +
              (f", 오면 약 {h:.0f}시간 뒤 ({eta:%m-%d %H시} UTC)" if p >= 0.2 else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sid")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    if a.update:
        download()
    predict(a.sid)
