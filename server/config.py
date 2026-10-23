import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(Path(os.environ.get("JETIX_ENV_FILE", ROOT / ".env")))


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


VK_TOKEN = os.environ.get("VK_TOKEN", "").strip()
GROUP_ID = abs(_int("GROUP_ID", 0))
ALBUM_ID = os.environ.get("ALBUM_ID", "").strip()
VK_API_VERSION = os.environ.get("VK_API_VERSION", "5.199").strip()
SEARCH_WALL = _bool("SEARCH_WALL", True)
VK_VIDEOS_URL = os.environ.get("VK_VIDEOS_URL", "").strip()

COOKIES = os.environ.get("COOKIES", "").strip()
YTDLP = os.environ.get("YTDLP", "yt-dlp").strip()

HOST = os.environ.get("HOST", "127.0.0.1").strip()
PORT = _int("PORT", 18731)
SECRET = os.environ.get("SECRET", "").strip().strip("/")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
PROXY_MODE = os.environ.get("PROXY_MODE", "off").strip().lower()
VERIFY_MEDIA = _bool("VERIFY_MEDIA", True)

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Jetix").strip()

STREAM_CACHE_TTL = _int("STREAM_CACHE_TTL", 60)
HLS_CACHE_TTL = _int("HLS_CACHE_TTL", 90)

USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
)


def problems(require_secret=True):
    out = []
    if not VK_TOKEN and not VK_VIDEOS_URL:
        out.append("нет VK_TOKEN (или VK_VIDEOS_URL для режима без токена)")
    if VK_TOKEN and not GROUP_ID:
        out.append("не задан GROUP_ID")
    if require_secret and (len(SECRET) < 12 or "/" in SECRET):
        out.append("SECRET должен быть случайной строкой из 12+ символов без «/»")
    if PROXY_MODE not in ("off", "manifest", "full"):
        out.append("PROXY_MODE должен быть off, manifest или full")
    return out


def cookies_path():
    if COOKIES and Path(COOKIES).is_file():
        return COOKIES
    return None
