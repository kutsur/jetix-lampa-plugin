import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config

log = logging.getLogger("jetix.vk")

API_URL = "https://api.vk.com/method/"

VK_ERRORS = {
    5: "токен недействителен или отозван — получите новый VK_TOKEN",
    6: "слишком много запросов к VK API, повторите позже",
    15: "нет доступа к видео группы — аккаунт токена должен состоять в группе",
    18: "аккаунт токена удалён или заблокирован",
    29: "достигнут лимит запросов к методу на сегодня",
    30: "профиль приватный",
    100: "неверный параметр (проверьте GROUP_ID / ALBUM_ID)",
    113: "неверный идентификатор",
    200: "нет доступа к альбому",
    203: "нет доступа к группе — аккаунт токена должен состоять в группе",
    204: "нет доступа к видео",
}


class VKError(Exception):
    def __init__(self, code, message):
        self.code = code
        hint = VK_ERRORS.get(code)
        text = "VK API ошибка %s: %s" % (code, message)
        if hint:
            text += " (" + hint + ")"
        super().__init__(text)


class MediaError(Exception):
    pass


def api(method, **params):
    if not config.VK_TOKEN:
        raise VKError(5, "VK_TOKEN не задан")
    data = {k: v for k, v in params.items() if v is not None and v != ""}
    data["access_token"] = config.VK_TOKEN
    data["v"] = config.VK_API_VERSION
    req = urllib.request.Request(
        API_URL + method,
        data=urllib.parse.urlencode(data).encode(),
        headers={"User-Agent": config.USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise VKError(-1, "сеть недоступна: %s" % e)
    if "error" in payload:
        err = payload["error"]
        raise VKError(err.get("error_code"), err.get("error_msg", ""))
    return payload["response"]


def classify(v):
    ls = (v.get("live_status") or "").lower()
    if ls == "started":
        return "live"
    if ls in ("waiting", "upcoming"):
        return "upcoming"
    if ls in ("finished", "failed", "stopped"):
        return "finished"
    if v.get("upcoming"):
        return "upcoming"
    if v.get("live"):
        return "live"
    if v.get("type") == "live":
        return "finished"
    return None


def _best_image(v):
    imgs = v.get("image") or v.get("first_frame") or []
    if isinstance(imgs, list) and imgs:
        imgs = sorted(imgs, key=lambda i: i.get("width", 0))
        return imgs[-1].get("url")
    for key in ("photo_1280", "photo_800", "photo_640", "photo_320"):
        if v.get(key):
            return v[key]
    return None


def normalize(v, source="video.get"):
    owner_id = v.get("owner_id")
    vid = v.get("id")
    page = "https://vk.com/video%s_%s" % (owner_id, vid)
    page_ak = page + "?access_key=" + v["access_key"] if v.get("access_key") else page
    return {
        "key": "%s_%s" % (owner_id, vid),
        "owner_id": owner_id,
        "id": vid,
        "access_key": v.get("access_key"),
        "title": v.get("title") or "",
        "status": classify(v),
        "date": v.get("date") or v.get("adding_date") or 0,
        "start_time": v.get("live_start_time") or 0,
        "spectators": v.get("spectators"),
        "thumb": _best_image(v),
        "player": v.get("player"),
        "files": v.get("files") or {},
        "page": page,
        "page_ak": page_ak,
        "source": source,
    }


def pick(streams, now=None):
    now = now or time.time()
    live = [s for s in streams if s["status"] == "live"]
    if live:
        return max(live, key=lambda s: s["date"])
    upcoming = [s for s in streams if s["status"] == "upcoming"]
    if upcoming:
        return min(upcoming, key=lambda s: s["start_time"] or now + 10 ** 9)
    finished = [s for s in streams if s["status"] == "finished"]
    if finished:
        return max(finished, key=lambda s: s["date"])
    return None


def _from_video_get():
    params = {"owner_id": -config.GROUP_ID, "count": 30}
    if config.ALBUM_ID:
        params["album_id"] = config.ALBUM_ID
    resp = api("video.get", **params)
    return [normalize(v) for v in resp.get("items", []) if classify(v)]


def _from_wall():
    resp = api("wall.get", owner_id=-config.GROUP_ID, count=20)
    found = []
    for post in resp.get("items", []):
        atts = list(post.get("attachments") or [])
        for repost in post.get("copy_history") or []:
            atts.extend(repost.get("attachments") or [])
        for a in atts:
            if a.get("type") == "video" and a.get("video"):
                found.append(a["video"])
    if not found:
        return []
    ids = []
    for v in found[:50]:
        vid = "%s_%s" % (v.get("owner_id"), v.get("id"))
        if v.get("access_key"):
            vid += "_" + v["access_key"]
        ids.append(vid)
    full = api("video.get", videos=",".join(ids)).get("items", [])
    by_key = {"%s_%s" % (v.get("owner_id"), v.get("id")): v for v in full}
    out = []
    for v in found:
        merged = dict(v)
        merged.update(by_key.get("%s_%s" % (v.get("owner_id"), v.get("id")), {}))
        if classify(merged):
            out.append(normalize(merged, source="wall.get"))
    return out


_ytdlp_lock = threading.Lock()


def ytdlp_json(url, flat=False, playlist_end=None):
    cmd = [config.YTDLP, "-J", "--no-warnings", "--no-progress"]
    cmd += ["--flat-playlist"] if flat else ["--no-playlist"]
    if playlist_end:
        cmd += ["--playlist-end", str(playlist_end)]
    if config.cookies_path():
        cmd += ["--cookies", config.cookies_path()]
    cmd.append(url)
    log.info("yt-dlp: %s", url.split("?", 1)[0])
    with _ytdlp_lock:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except FileNotFoundError:
            raise MediaError("yt-dlp не установлен (pip install -U yt-dlp)")
        except subprocess.TimeoutExpired:
            raise MediaError("yt-dlp не ответил за 90 секунд")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise MediaError("yt-dlp: " + (err[-1] if err else "код %s" % proc.returncode))
    return json.loads(proc.stdout)


def _from_ytdlp_page():
    info = ytdlp_json(config.VK_VIDEOS_URL, flat=True, playlist_end=8)
    out = []
    for entry in (info.get("entries") or [])[:8]:
        url = entry.get("url")
        if not url:
            continue
        try:
            full = ytdlp_json(url)
        except MediaError as e:
            log.warning("пропускаю %s: %s", url, e)
            continue
        status = {"is_live": "live", "is_upcoming": "upcoming",
                  "was_live": "finished", "post_live": "finished"}.get(full.get("live_status"))
        if full.get("is_live"):
            status = "live"
        if not status:
            continue
        owner, _, vid = (full.get("id") or "").partition("_")
        out.append({
            "key": full.get("id"), "owner_id": owner, "id": vid, "access_key": None,
            "title": full.get("title") or "", "status": status,
            "date": full.get("timestamp") or 0, "start_time": full.get("release_timestamp") or 0,
            "spectators": None, "thumb": full.get("thumbnail"), "player": None,
            "files": {}, "page": url, "page_ak": url, "source": "yt-dlp",
            "_ytdlp": full,
        })
        if status == "live":
            break
    return out


def find_streams():
    warnings = []
    streams = []
    if config.VK_TOKEN:
        streams.extend(_from_video_get())
        if config.SEARCH_WALL:
            try:
                seen = {s["key"] for s in streams}
                streams.extend(s for s in _from_wall() if s["key"] not in seen)
            except VKError as e:
                warnings.append("wall.get: %s" % e)
    elif config.VK_VIDEOS_URL:
        streams.extend(_from_ytdlp_page())
    else:
        raise VKError(5, "не задан ни VK_TOKEN, ни VK_VIDEOS_URL")
    return streams, pick(streams), warnings


def _from_files(files):
    for key in ("hls_live", "hls_live_playback", "hls", "hls_ondemand"):
        url = files.get(key)
        if isinstance(url, str) and url.startswith("http"):
            return {"type": "hls", "url": url, "headers": {}, "method": "api:files." + key}
    for key, url in files.items():
        if key.startswith("hls") and isinstance(url, str) and url.startswith("http"):
            return {"type": "hls", "url": url, "headers": {}, "method": "api:files." + key}
    for q in ("1080", "720", "1440", "2160", "480", "360"):
        url = files.get("mp4_" + q)
        if url:
            return {"type": "mp4", "url": url, "headers": {}, "method": "api:files.mp4_" + q}
    return None


def _from_ytdlp_info(info, method):
    fmts = info.get("formats") or []
    hls = [f for f in fmts if str(f.get("protocol", "")).startswith("m3u8")]
    if hls:
        best = max(hls, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        url = best.get("manifest_url") or best.get("url")
        headers = best.get("http_headers") or info.get("http_headers") or {}
        return {"type": "hls", "url": url, "headers": headers, "method": method}
    mp4 = [f for f in fmts if f.get("ext") == "mp4" and str(f.get("protocol", "")).startswith("http")]
    if mp4:
        best = max(mp4, key=lambda f: f.get("height") or 0)
        headers = best.get("http_headers") or info.get("http_headers") or {}
        return {"type": "mp4", "url": best["url"], "headers": headers, "method": method}
    return None


def resolve_media(stream):
    errors = []

    got = _from_files(stream.get("files") or {})
    if got:
        return got
    errors.append("в ответе API нет поля files с потоком")

    if stream.get("_ytdlp"):
        got = _from_ytdlp_info(stream["_ytdlp"], "yt-dlp:page")
        if got:
            return got

    targets = []
    if stream.get("player"):
        targets.append(("yt-dlp:player", stream["player"]))
    targets.append(("yt-dlp:page", stream.get("page_ak") or stream["page"]))
    for method, url in targets:
        try:
            got = _from_ytdlp_info(ytdlp_json(url), method)
            if got:
                return got
            errors.append("%s: yt-dlp не нашёл форматов" % method)
        except MediaError as e:
            errors.append("%s: %s" % (method, e))
    if not config.cookies_path():
        errors.append("подсказка: для закрытой группы обычно нужен cookies.txt (переменная COOKIES)")
    raise MediaError("; ".join(errors))


class Resolver:
    def __init__(self):
        self._lock = threading.Lock()
        self._stream = None
        self._media = {}
        self.last_headers = {}

    def current(self, fresh=False):
        with self._lock:
            cached = self._stream
        if cached and not fresh and time.time() - cached[0] < config.STREAM_CACHE_TTL:
            return cached[1]
        try:
            streams, chosen, warnings = find_streams()
            result = {"stream": chosen, "streams": streams, "warnings": warnings, "error": None}
            for w in warnings:
                log.warning(w)
            if chosen:
                log.info("трансляция: %s [%s] %s", chosen["key"], chosen["status"], chosen["title"])
            else:
                log.info("в группе не найдено трансляций")
        except (VKError, MediaError) as e:
            log.error("поиск трансляции: %s", e)
            result = {"stream": None, "streams": [], "warnings": [], "error": str(e)}
            with self._lock:
                self._stream = (time.time() - config.STREAM_CACHE_TTL + 10, result)
            return result
        with self._lock:
            self._stream = (time.time(), result)
        return result

    def invalidate_media(self):
        with self._lock:
            self._media = {}

    def media(self, stream, fresh=False):
        key = stream["key"]
        with self._lock:
            cached = self._media.get(key)
        if cached and not fresh and time.time() - cached[0] < config.HLS_CACHE_TTL:
            return cached[1]
        media = resolve_media(stream)
        log.info("поток %s получен через %s (%s)", key, media["method"], media["type"])
        with self._lock:
            self._media = {key: (time.time(), media)}
            if media.get("headers"):
                self.last_headers = dict(media["headers"])
        return media
