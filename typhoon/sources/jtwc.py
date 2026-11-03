"""미국 합동태풍경보센터(JTWC) 현재 예보 받기 — 키 필요 없음.

출처: https://www.metoc.navy.mil/jtwc/rss/jtwc.rss 에 활동 중인 태풍의 전문 링크가 있다.
  전문(wp3026web.txt): 발표 위치와 12·24·36·48·72·96·120시간 예보 중심, 최대풍속(1분 평균, kt)
한계: 피드에는 지금 활동 중인 태풍만 있다. 지난 예보는 forecast_log 로 쌓아야 남는다.

실행: python -m typhoon.sources.jtwc
"""
import re
from datetime import datetime, timezone

import requests

RSS = "https://www.metoc.navy.mil/jtwc/rss/jtwc.rss"
PRODUCTS = "https://www.metoc.navy.mil/jtwc/products/"
HEADERS = {"User-Agent": "typhoon-predictor"}
TIMEOUT = 20


def _when(ddhh, now):
    """전문의 'DDHH'(UTC)를 now에 가장 가까운 달력 시각으로. 월말을 넘어가는 예보도 맞춘다."""
    day, hour = int(ddhh[:2]), int(ddhh[2:4])
    best = None
    for shift in (-1, 0, 1):
        month = now.month + shift
        year = now.year + (month - 1) // 12
        try:
            t = datetime(year, (month - 1) % 12 + 1, day, hour)
        except ValueError:
            continue
        if best is None or abs(t - now) < abs(best - now):
            best = t
    return best


def _lon(v, side):
    return round(360 - float(v), 1) if side == "W" else float(v)


def parse_warning(text, now=None):
    """경보 전문 하나를 {name, number, issued, time, lat, lon, wind, points}로. 형식이 다르면 None.
    points 는 [예보 시간, 위도, 경도, 최대풍속(kt)] 목록. time 은 위치 기준 시각, issued 는 발표 시각(모두 UTC)."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    head = re.search(r"SUBJ/.*?\b(\d{2}[A-Z])\s*\(([^)]+)\)", text)
    pos = re.search(r"WARNING POSITION:\s*(\d{4})\d{2}Z --- NEAR (\d+\.\d)N (\d+\.\d)([EW])", text)
    if not head or not pos:
        return None
    t0 = _when(pos[1], now)
    head_time = re.search(r"\bPGTW (\d{4})\d{2}\b", text)
    wind = re.search(r"PRESENT WIND DISTRIBUTION:\s*MAX SUSTAINED WINDS - (\d+) KT", text)
    points = []
    block = re.compile(r"(\d+) HRS, VALID AT:\s*\d{6}Z --- (\d+\.\d)N (\d+\.\d)([EW])\s*MAX SUSTAINED WINDS - (\d+) KT")
    for m in block.finditer(text):
        points.append([int(m[1]), float(m[2]), _lon(m[3], m[4]), int(m[5])])
    return {"name": head[2].strip().upper(), "number": head[1], "issued": _when(head_time[1], now) if head_time else t0, "time": t0, "lat": float(pos[2]),
            "lon": _lon(pos[3], pos[4]), "wind": int(wind[1]) if wind else None, "points": points}


def latest(now=None):
    """활동 중인 북서태평양 태풍의 최신 예보를 이름별로. 받지 못하면 빈 사전(갱신은 이어 간다)."""
    try:
        rss = requests.get(RSS, timeout=TIMEOUT, headers=HEADERS)
        rss.raise_for_status()
    except requests.RequestException as e:
        print(f"[jtwc] 피드 실패: {type(e).__name__}")
        return {}
    out = {}
    for code in sorted(set(re.findall(r"/products/(wp\d{4})web\.txt", rss.text))):
        try:
            r = requests.get(f"{PRODUCTS}{code}web.txt", timeout=TIMEOUT, headers=HEADERS)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[jtwc] {code} 실패: {type(e).__name__}")
            continue
        w = parse_warning(r.text, now)
        if w and w["points"]:
            out[w["name"]] = w
    return out


if __name__ == "__main__":
    for name, w in latest().items():
        print(name, w["number"], w["issued"], w["points"])
