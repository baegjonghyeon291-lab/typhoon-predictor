"""공통 도우미: 프로젝트 경로, .env 읽기, 키 없을 때 안내 후 종료."""
import os
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
ENV_FILE = ROOT / ".env"


def load_env(path=ENV_FILE):
    """KEY=VALUE 형식 .env 를 읽어 os.environ 에 넣는다(이미 있는 환경변수는 유지)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def get_key(name):
    load_env()
    v = os.environ.get(name, "").strip()
    return v or None


def require_key(name, guide):
    """키가 없으면 한국어 안내를 출력하고 종료한다."""
    v = get_key(name)
    if v:
        return v
    msg = [
        "",
        f"[키 없음] {name} 가 설정되어 있지 않습니다.",
        f"  프로젝트 루트의 .env 파일에 다음 줄을 추가하세요:  {name}=발급받은키",
        f"  (.env 위치: {ENV_FILE})",
        "",
        guide.strip(),
        "",
        "자세한 절차는 .env.example 의 주석을 참고하세요.",
        "",
    ]
    print("\n".join(msg), file=sys.stderr)
    sys.exit(2)


def decoded_service_key(key):
    """공공데이터포털 키는 Encoding/Decoding 두 가지가 있다.
    requests 가 다시 인코딩하므로 Decoding 형태로 맞춰 넘긴다."""
    return unquote(key) if "%" in key else key
