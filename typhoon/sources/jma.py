"""일본 기상청(JMA) 공식 태풍 예보 수집 — 키 필요 없음.

출처: 気象庁防災情報XML (PULL형 공개 피드, 누구나 받을 수 있음)
  피드  : https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml  (최근 약 7일치)
  전문  : '台風解析・予報情報（５日予報）' (VPTW60~65) — 발표 시각, 실황 위치, 24/45/69/93/117시간
          (경우에 따라 3·6·9·12·15·18·21시간 등) 예보원 중심과 70% 확률 반경
이용조건: 気象庁ホームページ 이용규약(公共データ利用規約 第1.0版 준거) — 출처 표기 필요
  "출처: 気象庁ホームページ (https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml)" 및 가공 사실 표기.

한계: 피드는 최근 약 7일치만 들어 있다. 과거 이력(2013년 이후)을 담은 곳은
      국립정보학연구소 '디지털 태풍'뿐인데, 이용규약상 무단 재배포·사적 이용을 넘는 이용을
      금지하고 작성자 연락을 요구하므로 여기서는 받지 않는다.
      → 이 스크립트를 태풍 시즌 동안 하루 한 번 이상 실행하면 이력이 계속 쌓인다(이미 받은 전문은 건너뜀).

저장: data/jma/xml/*.xml            원문
      data/jma/jma_forecasts.csv     발표별·예보시간별 한 행
      data/jma/jma_first_issue.csv   태풍(번호)별 첫 발표 시각과 그때의 예보 진로

실행: python -m typhoon.sources.jma
"""
import re
import sys
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from ._env import DATA

FEED = "https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml"
OUT = DATA / "jma"
XML_DIR = OUT / "xml"
ATOM = "{http://www.w3.org/2005/Atom}"
UA = {"User-Agent": "typhoon-predictor research script (data collection, low rate)"}

ZEN = str.maketrans("０１２３４５６７８９", "0123456789")


def local(tag):
    return tag.rsplit("}", 1)[-1]


def find_all(el, name):
    return [e for e in el.iter() if local(e.tag) == name]


def find_one(el, name):
    for e in el.iter():
        if local(e.tag) == name:
            return e
    return None


def parse_coord(text):
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", text or "")
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def fetch_feed():
    r = requests.get(FEED, headers=UA, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for e in root.iter(f"{ATOM}entry"):
        title = e.findtext(f"{ATOM}title") or ""
        link = e.find(f"{ATOM}link")
        if title.startswith("台風解析・予報情報") and link is not None:
            out.append((title, link.get("href")))
    return out


def parse_report(path):
    root = ET.parse(path).getroot()
    head = find_one(root, "Head")
    g = lambda n: (find_one(head, n).text if find_one(head, n) is not None else None)  # noqa: E731
    base = {
        "file": path.name,
        "report_time": g("ReportDateTime"),
        "target_time": g("TargetDateTime"),
        "event_id": g("EventID"),
        "serial": g("Serial"),
        "info_type": g("InfoType"),
        "info_kind": g("InfoKind"),
    }
    rows = []
    number = name = None
    for mi in find_all(root, "MeteorologicalInfo"):
        dt_el = find_one(mi, "DateTime")
        kind = (dt_el.get("type") or "") if dt_el is not None else ""
        lead = 0
        m = re.search(r"(\d+)時間", kind.translate(ZEN))
        if m:
            lead = int(m.group(1))
        tnp = find_one(mi, "TyphoonNamePart")
        if tnp is not None:
            num_el, name_el = find_one(tnp, "Number"), find_one(tnp, "Name")
            number = num_el.text if num_el is not None else number
            name = name_el.text if name_el is not None else name
        lat = lon = radius = None
        if lead == 0:
            for c in find_all(mi, "Coordinate"):
                if c.get("type") == "中心位置（度）":
                    lat, lon = parse_coord(c.text)
                    break
        else:
            pc = find_one(mi, "ProbabilityCircle")
            if pc is not None:
                for bp in find_all(pc, "BasePoint"):
                    if bp.get("type") == "中心位置（度）":
                        lat, lon = parse_coord(bp.text)
                        break
                for rd in find_all(pc, "Radius"):
                    if rd.get("unit") == "km":
                        radius = float(rd.text) if rd.text else None
                        break
        pres = wind = wind_kt = None
        for p in find_all(mi, "Pressure"):
            if p.get("type") == "中心気圧" and p.text:
                pres = float(p.text)
        for w in find_all(mi, "WindSpeed"):
            if w.get("type") == "最大風速" and w.get("unit") == "m/s" and w.text:
                wind = float(w.text)
            if w.get("type") == "最大風速" and w.get("unit") == "ノット" and w.text:
                wind_kt = float(w.text)
        cls = find_one(mi, "TyphoonClass")
        rows.append({**base, "valid_time": dt_el.text if dt_el is not None else None,
                     "kind": kind, "lead_h": lead, "lat": lat, "lon": lon,
                     "radius70_km": radius, "pressure_hpa": pres, "max_wind_ms": wind,
                     "class": cls.text if cls is not None else None,
                     "max_wind_kt": wind_kt})
    for r in rows:
        r["typhoon_number"], r["name"] = number, name
    return rows


def main(argv=None):
    XML_DIR.mkdir(parents=True, exist_ok=True)
    try:
        entries = fetch_feed()
    except requests.RequestException as e:
        sys.exit(f"JMA 피드 받기 실패: {e}")
    new = 0
    for title, url in entries:
        p = XML_DIR / url.rsplit("/", 1)[-1]
        if p.exists():
            continue
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200:
            p.write_bytes(r.content)
            new += 1
            time.sleep(0.3)
    print(f"피드의 태풍 전문 {len(entries)}건 중 새로 받은 것 {new}건 → {XML_DIR}")

    rows = []
    for p in sorted(XML_DIR.glob("*.xml")):
        try:
            rows += parse_report(p)
        except ET.ParseError:
            print(f"  파싱 실패: {p.name}")
    if not rows:
        print("저장할 자료가 없습니다.")
        return
    df = pd.DataFrame(rows).sort_values(["event_id", "report_time", "lead_h"])
    df.to_csv(OUT / "jma_forecasts.csv", index=False)

    # 태풍별 첫 발표: 같은 EventID(열대저압부 단계부터 이어지는 식별자)의 가장 이른 발표
    first = []
    for eid, g in df[df["info_type"] != "取消"].groupby("event_id"):
        t0 = g["report_time"].min()
        g0 = g[g["report_time"] == t0].sort_values("lead_h")
        num = g["typhoon_number"].dropna()
        nm = g["name"].dropna()
        row = {"event_id": eid, "typhoon_number": num.iloc[-1] if len(num) else None,
               "name": nm.iloc[-1] if len(nm) else None, "first_report_time": t0,
               "first_serial": g0["serial"].iloc[0],
               "n_reports_collected": g["report_time"].nunique(),
               "feed_window_note": "피드에 남아 있던 가장 이른 발표(실제 첫 발표보다 늦을 수 있음)"
               if str(g0["serial"].iloc[0]) != "1" else "발표번호 1 (실제 첫 발표)"}
        for _, r in g0.iterrows():
            h = int(r["lead_h"])
            row[f"lat_{h:03d}h"], row[f"lon_{h:03d}h"] = r["lat"], r["lon"]
            if h:
                row[f"r70_{h:03d}h"] = r["radius70_km"]
        first.append(row)
    pd.DataFrame(first).to_csv(OUT / "jma_first_issue.csv", index=False)
    print(f"저장: {OUT / 'jma_forecasts.csv'} ({len(df)}행, 발표 {df['file'].nunique()}건, "
          f"{df['report_time'].min()} ~ {df['report_time'].max()}), jma_first_issue.csv ({len(first)}개 태풍)")
    print("출처: 気象庁防災情報XML (気象庁ホームページ) — 가공하여 사용")


if __name__ == "__main__":
    main()
