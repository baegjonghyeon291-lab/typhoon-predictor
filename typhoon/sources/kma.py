"""한국 기상청 자료 받기.

1) typhoon : 태풍 예보 발표 이력 (발표 시각 + 예상 진로)
   기본 경로 = 기상청 API허브 (키: KMA_APIHUB_KEY)
     - 태풍목록      https://apihub.kma.go.kr/api/typ01/url/typ_lst.php?YY=2012&disp=1&help=0&authKey=...
     - 태풍정보+예측  https://apihub.kma.go.kr/api/typ01/url/typ_data.php?YY=2011&typ=9&mode=3&disp=1&help=0&authKey=...
       mode=3 : 과거 분석정보 + 과거 모든 예측정보 (발표번호 SEQ 별로 분석시각 TYP_TM, 예측시각 FT_TM, 위경도 등)
   예비 경로 = 공공데이터포털 '기상청_태풍정보 조회서비스' (키: DATA_GO_KR_KEY, --via datagokr)
     - getTyphoonInfo(fromTmFc, toTmFc) 로 통보문 목록, getTyphoonFcst(tmFc, typSeq) 로 예상 진로
   저장: data/kma/typhoon/raw/...  (원문 그대로)
         data/kma/typhoon/kma_typhoon_list.csv      태풍 목록 (EFF 1상륙 2직접 3간접 4없음, 시각 UTC)
         data/kma/typhoon/kma_typhoon_fcst.csv      분석(FT=0)+예측(FT=1) 전 행
         data/kma/typhoon/kma_first_forecast.csv    태풍별 첫 발표(첫 SEQ) 시각과 첫 예측 진로 요약

2) asos : 종관기상관측(ASOS) 일자료 — 공공데이터포털 '기상청_지상(종관, ASOS) 일자료 조회서비스'
   (키: DATA_GO_KR_KEY)
   https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList
     ?serviceKey=..&pageNo=1&numOfRows=999&dataType=JSON&dataCd=ASOS&dateCd=DAY
     &startDt=YYYYMMDD&endDt=YYYYMMDD&stnIds=108
   지점: 서울108 부산159 광주156 제주184 강릉105 대전133, 기본 1973 ~ 어제
   서리: 별도 '서리 일수' 필드는 없다. 일기현상 문자열(iscs)에 '서리'가 있으면 frost=1 로 표시하고,
         최저 초상온도(minTg)도 함께 저장한다.
   저장: data/kma/asos/raw/{지점}_{연도}.json, data/kma/asos_daily.csv
   ※ API허브 kma_sfcdd3.php(ASOS 일자료)에는 일기현상(iscs) 항목이 없어 서리 판정이 안 되므로
     ASOS 는 공공데이터포털을 쓴다.

실행 예:
  python -m typhoon.sources.kma typhoon --start 2001
  python -m typhoon.sources.kma typhoon --via datagokr --start 2015
  python -m typhoon.sources.kma asos --start 1973
"""
import argparse
import datetime as dt
import json
import sys
import time

import pandas as pd
import requests

from ._env import DATA, decoded_service_key, require_key

OUT = DATA / "kma"
HUB = "https://apihub.kma.go.kr/api/typ01/url"
DGK_TYP = "https://apis.data.go.kr/1360000/TyphoonInfoService"
DGK_ASOS = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
STATIONS = {108: "서울", 159: "부산", 156: "광주", 184: "제주", 105: "강릉", 133: "대전"}

HUB_GUIDE = """KMA_APIHUB_KEY 발급 방법 (기상청 API허브):
  1) https://apihub.kma.go.kr 접속 → 회원가입(무료) 후 로그인
  2) 상단 메뉴에서 '태풍' (https://apihub.kma.go.kr/apiList.do?seqApi=8) 이동
  3) '1. 태풍정보(기상청 발표) 조회' 항목의 [API 활용신청] 버튼으로 신청
  4) 마이페이지에서 인증키(authKey) 확인 → .env 에  KMA_APIHUB_KEY=인증키"""

DGK_GUIDE = """DATA_GO_KR_KEY 발급 방법 (공공데이터포털):
  1) https://www.data.go.kr 회원가입(무료) 후 로그인
  2) 아래 페이지에서 각각 [활용신청] (개발계정, 자동승인)
     - ASOS 일자료 : https://www.data.go.kr/data/15059093/openapi.do
     - 태풍정보    : https://www.data.go.kr/data/15043565/openapi.do
  3) 마이페이지 > 데이터활용 > Open API > 활용신청 현황 에서 '일반 인증키(Decoding)' 복사
  4) .env 에  DATA_GO_KR_KEY=인증키"""

# 실제 응답(help=1 머리말, 2026-10 확인) 기준 열 순서. 쉼표 구분, 줄 끝은 ",="
HUB_LST_COLS = ["YY", "SEQ", "NOW", "EFF", "TM_ST", "TM_ED", "TYP_NAME", "TYP_EN", "REM"]
HUB_DATA_COLS = ["FT", "YY", "TYP", "SEQ", "TMD", "TYP_TM", "FT_TM", "LAT", "LON", "DIR", "SP",
                 "PS", "WS", "RAD15", "RAD25", "RAD", "ED15", "ER15", "LOC", "ED25", "ER25"]


# ---------------------------------------------------------------- 공통
def _mask(text, params):
    for k in ("authKey", "serviceKey"):
        v = params.get(k)
        if v:
            text = str(text).replace(str(v), "***")
    return text


def get(url, params, tries=5):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code in (401, 403):
                sys.exit(f"[인증 실패] 키가 틀렸거나 해당 API 활용신청이 안 되어 있습니다.\n"
                         f"응답: {_mask(r.text[:300], params)}")
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = _mask(e, params)
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"요청 실패: {url} {last}")


def parse_hub_text(text, cols, free_col):
    """API허브 typ01 텍스트(# 머리말 + 쉼표 구분 자료줄, 끝 ",=")를 DataFrame 으로.
    free_col(REM·LOC) 안에 쉼표가 들어가도 앞뒤 고정 칸 수로 잘라 합친다."""
    k = cols.index(free_col)
    n_after = len(cols) - k - 1
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        v = [x.strip() for x in s.split(",")]
        if v and v[-1] == "=":
            v = v[:-1]
        if len(v) < len(cols):
            v = v + [None] * (len(cols) - len(v))
        elif len(v) > len(cols):
            tail = v[len(v) - n_after:] if n_after else []
            v = v[:k] + [",".join(v[k:len(v) - n_after])] + tail
        rows.append(v)
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------- 태풍 (API허브)
def typhoon_hub(years, key, pause=0.3):
    raw = OUT / "typhoon" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    this_year = dt.date.today().year
    lists, fcsts = [], []
    for y in years:
        p = raw / f"typ_lst_{y}.txt"
        if not p.exists() or y >= this_year:
            r = get(f"{HUB}/typ_lst.php", {"YY": y, "disp": 1, "help": 0, "authKey": key})
            p.write_text(r.text, encoding="utf-8")
            time.sleep(pause)
        lst = parse_hub_text(p.read_text(encoding="utf-8"), HUB_LST_COLS, "REM")
        if lst.empty:
            print(f"  {y}: 태풍 목록 없음")
            continue
        lists.append(lst)
        n_rows = 0
        for _, t in lst.iterrows():
            seq = int(t["SEQ"])
            q = raw / f"typ_data_{y}_{seq:02d}.txt"
            if not q.exists() or (y >= this_year and str(t["NOW"]) == "1") or q.stat().st_size < 200:
                r = get(f"{HUB}/typ_data.php",
                        {"YY": y, "typ": seq, "mode": 3, "disp": 1, "help": 0, "authKey": key})
                q.write_text(r.text, encoding="utf-8")
                time.sleep(pause)
            d = parse_hub_text(q.read_text(encoding="utf-8"), HUB_DATA_COLS, "LOC")
            n_rows += len(d)
            if not d.empty:
                fcsts.append(d)
        print(f"  {y}: 태풍 {len(lst)}개, 분석·예측 {n_rows}행")
    return (pd.concat(lists, ignore_index=True) if lists else pd.DataFrame(),
            pd.concat(fcsts, ignore_index=True) if fcsts else pd.DataFrame())


def first_forecast_hub(f, lst=None):
    """태풍별 기상청 첫 발표(가장 작은 발표번호 SEQ)의 분석시각(UTC)과,
    예측 진로가 들어 있는 첫 발표의 예측점 요약."""
    if f.empty:
        return f
    f = f.copy()
    for c in ["FT", "YY", "TYP", "SEQ", "TMD", "LAT", "LON"]:
        f[c] = pd.to_numeric(f[c], errors="coerce")
    out = []
    for (yy, typ), g in f.groupby(["YY", "TYP"]):
        s0 = g["SEQ"].min()
        row = {"YY": int(yy), "TYP": int(typ), "first_seq": int(s0),
               "first_issue_tm_utc": str(g.loc[g["SEQ"] == s0, "TYP_TM"].iloc[0]),
               "n_issues": int(g["SEQ"].nunique()),
               "last_issue_tm_utc": str(g.loc[g["SEQ"] == g["SEQ"].max(), "TYP_TM"].iloc[0])}
        pred = g[g["FT"] == 1]
        row["n_issues_with_fcst"] = int(pred["SEQ"].nunique())
        if not pred.empty:
            s1 = pred["SEQ"].min()
            p1 = pred[pred["SEQ"] == s1].sort_values("TMD")
            row["first_fcst_seq"] = int(s1)
            row["first_fcst_issue_tm_utc"] = str(p1["TYP_TM"].iloc[0])
            row["first_fcst_max_tmd_h"] = int(p1["TMD"].max())
            for _, r in p1.iterrows():
                h = int(r["TMD"])
                row[f"lat_{h:03d}h"] = r["LAT"]
                row[f"lon_{h:03d}h"] = r["LON"]
        out.append(row)
    out = pd.DataFrame(out)
    if lst is not None and not lst.empty:
        l2 = lst[["YY", "SEQ", "TYP_NAME", "TYP_EN", "EFF"]].copy()
        l2["YY"] = pd.to_numeric(l2["YY"]); l2["SEQ"] = pd.to_numeric(l2["SEQ"])
        out = out.merge(l2.rename(columns={"SEQ": "TYP"}), on=["YY", "TYP"], how="left")
        front = ["YY", "TYP", "TYP_NAME", "TYP_EN", "EFF"]
        out = out[front + [c for c in out.columns if c not in front]]
    return out


# ---------------------------------------------------------------- 태풍 (공공데이터포털)
def dgk_items(url, params):
    r = get(url, params)
    try:
        js = r.json()
    except ValueError:
        sys.exit(f"[응답 오류] JSON 이 아닙니다(키·활용신청 확인): {r.text[:300]}")
    body = js.get("response", {})
    head = body.get("header", {})
    if head.get("resultCode") not in ("00", None):
        if head.get("resultCode") == "03":  # NO_DATA
            return [], 0
        sys.exit(f"[API 오류] {head}")
    b = body.get("body", {}) or {}
    items = (b.get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items, int(b.get("totalCount") or 0)


def dgk_all(url, params):
    out, page = [], 1
    while True:
        items, total = dgk_items(url, {**params, "pageNo": page, "numOfRows": 999, "dataType": "JSON"})
        out += items
        if len(out) >= total or not items:
            return out
        page += 1


def typhoon_datagokr(years, key):
    raw = OUT / "typhoon" / "raw_datagokr"
    raw.mkdir(parents=True, exist_ok=True)
    infos, fcsts = [], []
    for y in years:
        p = raw / f"info_{y}.json"
        if not p.exists() or y >= dt.date.today().year:
            items = []
            for m in range(1, 13):  # 월 단위로 끊어 조회
                end = (dt.date(y, m, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
                items += dgk_all(f"{DGK_TYP}/getTyphoonInfo",
                                 {"serviceKey": key, "fromTmFc": f"{y}{m:02d}01", "toTmFc": end.strftime("%Y%m%d")})
            p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        items = json.loads(p.read_text(encoding="utf-8"))
        print(f"  {y}: 통보문 {len(items)}건")
        infos += items
        for it in items:
            tm, seq = str(it.get("tmFc")), it.get("typSeq")
            q = raw / f"fcst_{tm}_{seq}.json"
            if not q.exists():
                fc = dgk_all(f"{DGK_TYP}/getTyphoonFcst", {"serviceKey": key, "tmFc": tm, "typSeq": seq})
                q.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
                time.sleep(0.1)
            for row in json.loads(q.read_text(encoding="utf-8")):
                row["issue_tmFc"], row["issue_tmSeq"], row["year"] = tm, it.get("tmSeq"), y
                fcsts.append(row)
    return pd.DataFrame(infos), pd.DataFrame(fcsts)


def first_forecast_datagokr(info, f):
    if info.empty:
        return info
    info = info.copy()
    info["tmSeq"] = pd.to_numeric(info["tmSeq"], errors="coerce")
    info["year"] = info["tmFc"].astype(str).str[:4]
    first = info.sort_values(["tmFc"]).groupby(["year", "typSeq"], as_index=False).first()
    first = first[["year", "typSeq", "typName", "typEn", "tmFc", "tmSeq", "typLat", "typLon"]]
    first = first.rename(columns={"tmFc": "first_issue_tmFc_kst"})
    if not f.empty:
        f = f.copy()
        f["key"] = f["issue_tmFc"].astype(str) + "_" + f["seq"].astype(str)
        keys = first["first_issue_tmFc_kst"].astype(str) + "_" + first["typSeq"].astype(str)
        sub = f[f["key"].isin(set(keys))]
        track = (sub.groupby("key")
                 .apply(lambda g: json.dumps(g[["tm", "lat", "lon", "radPr"]].to_dict("records"), ensure_ascii=False))
                 .rename("first_fcst_track_json"))
        first["key"] = keys.values
        first = first.merge(track, left_on="key", right_index=True, how="left").drop(columns="key")
    return first


def cmd_typhoon(a):
    years = range(a.start, (a.end or dt.date.today().year) + 1)
    if a.via == "hub":
        key = require_key("KMA_APIHUB_KEY", HUB_GUIDE)
        lst, f = typhoon_hub(years, key)
        first = first_forecast_hub(f, lst)
    else:
        key = decoded_service_key(require_key("DATA_GO_KR_KEY", DGK_GUIDE))
        lst, f = typhoon_datagokr(years, key)
        first = first_forecast_datagokr(lst, f)
    out = OUT / "typhoon"
    out.mkdir(parents=True, exist_ok=True)
    lst.to_csv(out / "kma_typhoon_list.csv", index=False)
    f.to_csv(out / "kma_typhoon_fcst.csv", index=False)
    first.to_csv(out / "kma_first_forecast.csv", index=False)
    print(f"저장: {out}/kma_typhoon_list.csv ({len(lst)}행), kma_typhoon_fcst.csv ({len(f)}행), "
          f"kma_first_forecast.csv ({len(first)}행)")


# ---------------------------------------------------------------- ASOS
def cmd_asos(a):
    key = decoded_service_key(require_key("DATA_GO_KR_KEY", DGK_GUIDE))
    raw = OUT / "asos" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    yesterday = dt.date.today() - dt.timedelta(days=1)
    end_year = a.end or yesterday.year
    stations = [int(s) for s in a.stations.split(",")] if a.stations else list(STATIONS)
    frames = []
    for stn in stations:
        for y in range(a.start, end_year + 1):
            p = raw / f"{stn}_{y}.json"
            last = min(dt.date(y, 12, 31), yesterday)
            if not p.exists() or y == yesterday.year:
                items = dgk_all(DGK_ASOS, {"serviceKey": key, "dataCd": "ASOS", "dateCd": "DAY",
                                           "startDt": f"{y}0101", "endDt": last.strftime("%Y%m%d"),
                                           "stnIds": stn})
                p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
                time.sleep(0.1)
            items = json.loads(p.read_text(encoding="utf-8"))
            if items:
                frames.append(pd.DataFrame(items))
        print(f"  {stn} {STATIONS.get(stn, '')}: {a.start}~{end_year} 완료")
    if not frames:
        print("받은 자료가 없습니다.")
        return
    df = pd.concat(frames, ignore_index=True)
    df["iscs"] = df.get("iscs", pd.Series(dtype=str)).fillna("")
    df["frost"] = df["iscs"].str.contains("서리").astype(int)
    df["stnName"] = df["stnId"].astype(int).map(STATIONS)
    first = ["tm", "stnId", "stnName", "avgTa", "minTa", "maxTa", "sumRn", "avgWs", "maxWs",
             "maxInsWs", "minTg", "frost", "iscs"]
    cols = [c for c in first if c in df.columns] + [c for c in df.columns if c not in first]
    df = df[cols].sort_values(["stnId", "tm"])
    df.to_csv(OUT / "asos_daily.csv", index=False)
    print(f"저장: {OUT / 'asos_daily.csv'} ({len(df)}행, {df['tm'].min()} ~ {df['tm'].max()})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="한국 기상청 자료 받기")
    sub = ap.add_subparsers(dest="cmd")
    t = sub.add_parser("typhoon", help="태풍 예보 발표 이력")
    t.add_argument("--via", choices=["hub", "datagokr"], default="hub",
                   help="hub=기상청 API허브(KMA_APIHUB_KEY, 기본), datagokr=공공데이터포털(DATA_GO_KR_KEY)")
    t.add_argument("--start", type=int, default=2001)
    t.add_argument("--end", type=int, default=None)
    s = sub.add_parser("asos", help="ASOS 일자료")
    s.add_argument("--start", type=int, default=1973)
    s.add_argument("--end", type=int, default=None)
    s.add_argument("--stations", default=None, help="쉼표로 구분한 지점번호 (기본 108,159,156,184,105,133)")
    a = ap.parse_args(argv)
    if a.cmd == "typhoon":
        cmd_typhoon(a)
    elif a.cmd == "asos":
        cmd_asos(a)
    else:
        ap.print_help()
        print("\n필요한 키:\n\n" + HUB_GUIDE + "\n\n" + DGK_GUIDE)


if __name__ == "__main__":
    main()
