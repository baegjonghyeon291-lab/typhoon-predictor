"""기상청(KMA) 태풍 발표 이력으로 본 한국 경보 선행시간 분석.

입력: data/kma/typhoon/kma_typhoon_list.csv, kma_typhoon_fcst.csv
      (python -m typhoon.sources.kma typhoon 으로 생성)
      IBTrACS (typhoon.data.load)
출력: reports/kma_lead.json

- 매칭: 기상청 태풍(연도, 영문 이름) ↔ IBTrACS(SEASON, NAME). 이름은 영문자만 비교하고,
  같은 해에 없으면 앞뒤 1년까지 보되 기상청 기간과 IBTrACS 기간이 겹치는 것만 인정.
- 시각: 기상청 TYP_TM(분석시각, UTC)을 '발표 시각'으로 본다. 실제 통보문 공개는 분석시각보다
  몇 시간 늦으므로 선행시간은 그만큼 과대평가될 수 있다.
- 도착: IBTrACS 6시간 자료에서 한국 6개 도시 200km(in_korea) 안에 처음 들어온 시각
  (analyze.py / features.add_targets 와 같은 정의).
- 선행시간이 0 이하(도착 뒤 첫 발표 또는 끝내 예측 못함)는 우리 모델 형식에 맞춰 0 으로 둔다.
"""
import json
import re

import numpy as np
import pandas as pd

from .data import CITIES, RADIUS_KM, ROOT, haversine, load

KMA = ROOT / "data" / "kma" / "typhoon"
OUT = ROOT / "reports" / "kma_lead.json"
LEADS = [24, 48, 72, 96, 120]
SPLIT_YEAR = 2015  # train.SPLIT_YEAR 와 같은 값(우리 모델 검증 기간)


def norm(name):
    return re.sub(r"[^A-Z]", "", str(name).upper())


def kma_min_dist(lat, lon):
    return np.min([haversine(lat, lon, a, b) for a, b in CITIES["korea"]], axis=0)


def load_kma():
    lst = pd.read_csv(KMA / "kma_typhoon_list.csv")
    f = pd.read_csv(KMA / "kma_typhoon_fcst.csv", low_memory=False)
    lst["TM_ST"] = pd.to_datetime(lst["TM_ST"].astype("Int64").astype(str), format="%Y%m%d%H%M", errors="coerce")
    lst["TM_ED"] = pd.to_datetime(lst["TM_ED"].astype("Int64").astype(str), format="%Y%m%d%H%M", errors="coerce")
    for c in ["FT", "YY", "TYP", "SEQ", "TMD", "LAT", "LON"]:
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f["TYP_TM"] = pd.to_datetime(f["TYP_TM"].astype("Int64").astype(str), format="%Y%m%d%H%M", errors="coerce")
    return lst, f


def ib_summary(df):
    g = df.groupby("SID")
    s = pd.DataFrame({
        "SEASON": g.SEASON.first(), "NAME": g.NAME.first(),
        "t0": g.time.min(), "t1": g.time.max(),
        "jma_first": df[df.TOKYO_LAT.notna()].groupby("SID").time.min(),
        "arrival": df[df.in_korea].groupby("SID").time.min(),
        "in_korea": g.in_korea.any(),
    })
    # 'BULBUL:MATMO' 처럼 이름이 둘인 경우 각각으로 매칭되게 한다
    s["keys"] = s.NAME.astype(str).map(lambda n: {norm(x) for x in n.split(":")})
    return s


def match(lst, ib):
    rows, fails = [], []
    for _, t in lst.iterrows():
        k = norm(t.TYP_EN)
        cand = ib[ib["keys"].map(lambda ks: k in ks) & ib.SEASON.between(t.YY - 1, t.YY + 1)].copy()
        if len(cand):
            # 기간 겹침
            ov = (cand.t1 >= t.TM_ST - pd.Timedelta("3D")) & (cand.t0 <= t.TM_ED + pd.Timedelta("3D"))
            cand = cand[ov]
        if len(cand) == 0:
            fails.append({"YY": int(t.YY), "TYP": int(t.SEQ), "name": t.TYP_EN})
            continue
        cand["d"] = (cand.t0 - t.TM_ST).abs()
        sid = cand.sort_values("d").index[0]
        rows.append({"YY": int(t.YY), "TYP": int(t.SEQ), "SID": sid})
    return pd.DataFrame(rows), fails


def lead_stats(lead):
    lead = pd.Series(lead, dtype=float).clip(lower=0)
    return {"storms": int(len(lead)), "median_warning_lead_h": float(lead.median()) if len(lead) else None,
            **{f"warned_{h}h_before": round(float((lead >= h).mean()), 3) for h in LEADS}}


def raw_stats(x):
    x = pd.Series(x, dtype=float).dropna()
    return {"n": int(len(x)), "median_h": float(x.median()), "mean_h": round(float(x.mean()), 1),
            "p25_h": float(x.quantile(.25)), "p75_h": float(x.quantile(.75))}


def fcst_korea_issue(g, interp=False):
    """발표(SEQ)별로 예측 진로점(FT=1) 중 한국 6개 도시 200km 안 점이 있는지 → 처음 그런 발표의 시각.
    interp=True 면 분석점과 예측점 사이를 1시간 간격 직선 보간해서 판정."""
    for seq, s in g.sort_values(["SEQ", "TMD"]).groupby("SEQ"):
        p = s[s.FT == 1].dropna(subset=["LAT", "LON"])
        if p.empty:
            continue
        if interp:
            a = s.dropna(subset=["LAT", "LON"]).sort_values("TMD")
            if len(a) < 2:
                continue
            hh = np.arange(a.TMD.min(), a.TMD.max() + 1)
            lat = np.interp(hh, a.TMD, a.LAT)
            lon = np.interp(hh, a.TMD, a.LON)
        else:
            lat, lon = p.LAT.values, p.LON.values
        if (kma_min_dist(lat, lon) <= RADIUS_KM).any():
            return s.TYP_TM.iloc[0], int(seq)
    return pd.NaT, None


def main():
    lst, f = load_kma()
    df = load(start_year=lst.YY.min() - 1)
    ib = ib_summary(df)
    m, fails = match(lst, ib)

    first = f.groupby(["YY", "TYP"]).agg(kma_first=("TYP_TM", "min"), n_issue=("SEQ", "nunique"),
                                         n_fcst_rows=("FT", "sum")).reset_index()
    t = (lst.rename(columns={"SEQ": "TYP"})[["YY", "TYP", "TYP_NAME", "TYP_EN", "EFF", "TM_ST"]]
         .merge(first, on=["YY", "TYP"], how="left")
         .merge(m, on=["YY", "TYP"], how="left"))
    t = t.merge(ib[["jma_first", "arrival", "in_korea", "t0"]], left_on="SID", right_index=True, how="left")

    fk, fk_seq, fki = {}, {}, {}
    for (yy, typ), g in f.groupby(["YY", "TYP"]):
        fk[(yy, typ)], fk_seq[(yy, typ)] = fcst_korea_issue(g)
        fki[(yy, typ)] = fcst_korea_issue(g, interp=True)[0]
    keys = list(zip(t.YY, t.TYP))
    t["kma_fcst_korea"] = [fk.get(k, pd.NaT) for k in keys]
    t["kma_fcst_korea_interp"] = [fki.get(k, pd.NaT) for k in keys]
    h = lambda a, b: (a - b).dt.total_seconds() / 3600

    # 1) 첫 발표 vs JMA 첫 기록
    t["kma_minus_jma_h"] = h(t.kma_first, t.jma_first)
    t["kma_first_minus_list_start_h"] = h(t.kma_first, t.TM_ST)
    both = t.dropna(subset=["kma_minus_jma_h"])
    first_vs_jma = {
        "all": {**raw_stats(both.kma_minus_jma_h), "kma_later_ratio": round(float((both.kma_minus_jma_h > 0).mean()), 3),
                "same_time_ratio": round(float((both.kma_minus_jma_h == 0).mean()), 3)},
        f"since_{SPLIT_YEAR}": raw_stats(both[both.YY >= SPLIT_YEAR].kma_minus_jma_h),
        "by_period": {f"{a}-{b}": raw_stats(both[both.YY.between(a, b)].kma_minus_jma_h)
                      for a, b in [(2001, 2007), (2008, 2014), (2015, 2020), (2021, 2026)]
                      if both.YY.between(a, b).any()},
        "note": "양수 = 기상청 첫 발표(분석시각)가 IBTrACS JMA(TOKYO) 첫 기록보다 늦음. JMA 첫 기록은 열대저압부 단계부터 시작될 수 있음.",
    }

    # 2) 한국 영향 태풍: 첫 발표 → 도착 선행시간
    hit = t[t.arrival.notna()].copy()
    hit["lead_first_issue_h"] = h(hit.arrival, hit.kma_first)
    hit["lead_fcst_korea_h"] = h(hit.arrival, hit.kma_fcst_korea)
    hit["lead_fcst_korea_interp_h"] = h(hit.arrival, hit.kma_fcst_korea_interp)
    # 우리 모델과 같은 대상: 도착 전 기록이 있는 태풍(IBTrACS 첫 기록이 도착보다 앞)
    hit = hit[hit.t0 < hit.arrival]

    def block(d):
        return {
            "first_issue": {**lead_stats(d.lead_first_issue_h.fillna(0)), "raw": raw_stats(d.lead_first_issue_h)},
            "first_fcst_track_in_korea": {**lead_stats(d.lead_fcst_korea_h.fillna(0)),
                                          "never_forecast_ratio": round(float(d.lead_fcst_korea_h.isna().mean()), 3),
                                          "raw": raw_stats(d.lead_fcst_korea_h)},
            "first_fcst_track_in_korea_interp1h": {**lead_stats(d.lead_fcst_korea_interp_h.fillna(0)),
                                                   "never_forecast_ratio": round(float(d.lead_fcst_korea_interp_h.isna().mean()), 3)},
        }

    eff_hit = t[t.EFF.isin([1, 2, 3])]
    korea = {
        "definition": "도착 = IBTrACS 에서 한국 6개 도시 200km 안 첫 시각(in_korea). 대상 = in_korea 태풍 중 매칭 성공·도착 전 기록 있음.",
        "all_years": block(hit),
        f"since_{SPLIT_YEAR}_same_as_model": block(hit[hit.YY >= SPLIT_YEAR]),
        "eff123_storms_total": int(len(eff_hit)),
        "eff123_and_in_korea": int(len(hit[hit.EFF.isin([1, 2, 3])])),
    }

    # 3) 우리 모델 비교
    model = None
    ap = ROOT / "reports" / "analysis.json"
    if ap.exists():
        model = json.loads(ap.read_text()).get("model_warning_lead_full", {}).get("korea")

    # 4) EFF vs in_korea
    mm = t[t.SID.notna()].copy()
    eff = mm.EFF.isin([1, 2, 3])
    ink = mm.in_korea.fillna(False).astype(bool)
    agree = {
        "matched_storms": int(len(mm)),
        "agreement_ratio": round(float((eff == ink).mean()), 3),
        "both_yes": int((eff & ink).sum()), "eff_only": int((eff & ~ink).sum()),
        "in_korea_only": int((~eff & ink).sum()), "both_no": int((~eff & ~ink).sum()),
        "by_eff": {int(k): {"storms": int(len(g)), "in_korea_ratio": round(float(g.in_korea.fillna(False).astype(bool).mean()), 3)}
                   for k, g in mm.groupby("EFF")},
        "eff_only_list": mm[eff & ~ink].apply(lambda r: f"{r.YY} {r.TYP_EN} EFF{r.EFF}", axis=1).tolist(),
        "in_korea_only_list": mm[~eff & ink].apply(lambda r: f"{r.YY} {r.TYP_EN} EFF{r.EFF}", axis=1).tolist(),
    }

    out = {
        "source": "기상청 API허브 typ_lst.php + typ_data.php(mode=3)",
        "years": [int(lst.YY.min()), int(lst.YY.max())],
        "kma_storms": int(len(lst)),
        "kma_storms_with_issue_records": int(t.kma_first.notna().sum()),
        "kma_storms_with_forecast_points": int((t.n_fcst_rows > 0).sum()),
        "matched_ibtracs": int(t.SID.notna().sum()),
        "match_failed": len(fails), "match_failed_list": fails,
        "kma_first_issue_vs_jma_first_record": first_vs_jma,
        "korea_lead": korea,
        "our_model_korea_full": model,
        "eff_vs_in_korea": agree,
        "notes": [
            "기상청 발표 시각은 TYP_TM(분석시각, UTC). 실제 공개 시각은 이보다 늦다.",
            "선행시간 0 이하·예측 없음은 0으로 두고 비율 계산(우리 모델 warning_lead 와 같은 방식). raw 는 음수 포함 원값.",
            "first_fcst_track_in_korea: 예측점(FT=1) 중 한국 6개 도시 200km 안 점이 처음 나온 발표. interp1h 는 점 사이 직선 보간.",
        ],
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    per = KMA / "kma_lead_per_storm.csv"
    t.to_csv(per, index=False)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
