"""경로·전향 실험 결과(reports/track_experiment.txt, reports/gfs_track_experiment.txt)를 사이트용 JSON 으로.

    python scripts/track_json.py      # → docs/data/track.json

실험을 다시 돌려 리포트가 바뀌면 이 스크립트도 다시 돌린다.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"
OUT = ROOT / "docs" / "data" / "track.json"


def nums(line):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", line)]


def base():
    """검증 2015~: 단순 연장 vs 자체 경로 모델 평균 오차(km)."""
    rows = []
    for line in (REP / "track_experiment.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(\d+)h\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%", line)
        if m:
            h, n, ext, model, _ = map(int, m.groups())
            rows.append({"h": h, "n": n, "extrap": ext, "model": model})
    return rows


def gfs():
    """평가 2024~: GFS 경로가 없으면 기존 모델로 대체한 전체 시점(B 표) + 전향 판별 + 참피 사례."""
    text = (REP / "gfs_track_experiment.txt").read_text(encoding="utf-8").splitlines()
    out = {"all": [], "turn": [], "champi": []}
    part = None
    for line in text:
        if line.startswith("B."):
            part = "B"
        elif line.startswith("48시간 전향"):
            part = "T"
        elif line.startswith("참피"):
            part = "C"
        elif part == "B":
            m = re.match(r"\s*(\d+)h\s+(\d+)\s+(\d+)%\s+(\d+)\s+(\d+)", line)
            if m:
                h, n, cov, old, blend = map(int, m.groups())
                out["all"].append({"h": h, "n": n, "gfs_share": cov / 100, "model": old, "blend": blend})
        elif part == "T":
            m = re.match(r"(\S.*?)\s+([01]\.\d+)\s+([\d.]+)%\s+([\d.]+)%", line)
            if m:
                out["turn"].append({"name": m.group(1).strip(), "auc": float(m.group(2)), "hit": round(float(m.group(3)) / 100, 4), "precision": round(float(m.group(4)) / 100, 4)})
        elif part == "C":
            m = re.match(r"\s*(\d+)-(\d+) (\d+)시\s+(\d+)%\s+(\d+)°\s+(\d+)%", line)
            if m:
                mo, d, hh, clf, deg, blend = map(int, m.groups())
                out["champi"].append({"kst": f"{mo}/{d} {hh:02d}시", "clf": clf / 100, "gfs_turn_deg": deg, "blend": blend / 100})
    return out


LEADS = [24, 48, 72, 96, 120]


def build():
    """리포트에서 사이트 자료를 만든다. 표의 줄이 하나라도 안 읽히면 멈춘다(조용히 빠지지 않게)."""
    g = gfs()
    data = {"base": base(), "gfs": g["all"], "turn": g["turn"], "champi": g["champi"]}
    assert [r["h"] for r in data["base"]] == LEADS, "track_experiment.txt 위치 오차 표 형식 확인"
    assert [r["h"] for r in data["gfs"]] == LEADS, "gfs_track_experiment.txt B 표 형식 확인"
    assert len(data["turn"]) == 3 and data["champi"], "gfs_track_experiment.txt 전향·참피 표 형식 확인"
    return data


def main():
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장: {OUT} (기준 {len(data['base'])}줄, GFS {len(data['gfs'])}줄, 전향 {len(data['turn'])}줄, 참피 {len(data['champi'])}시점)")


if __name__ == "__main__":
    main()
