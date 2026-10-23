import base64
import binascii
import hashlib
import hmac
import re
import urllib.parse
import urllib.request

import config

URI_ATTR = re.compile(r'URI="([^"]+)"')


def _b64e(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def sign(url):
    return hmac.new(config.SECRET.encode(), url.encode(), hashlib.sha256).hexdigest()[:20]


def make_url(prefix, url, playlist):
    ext = "m3u8" if playlist else (_ext(url) or "ts")
    return "%s/p/%s/%s.%s" % (prefix, sign(url), _b64e(url), ext)


def parse_path(rest):
    try:
        sig, tail = rest.split("/", 1)
        url = _b64d(tail.rsplit(".", 1)[0])
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    if not hmac.compare_digest(sig, sign(url)):
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return url


def _ext(url):
    name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    if "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        if re.fullmatch(r"[a-z0-9]{1,5}", ext):
            return ext
    return None


def _is_playlist_url(url):
    return _ext(url) == "m3u8" or "m3u8" in urllib.parse.urlparse(url).path


def rewrite(text, base_url, prefix, mode):
    out = []
    next_is_playlist = False

    def seg(u):
        absu = urllib.parse.urljoin(base_url, u)
        return make_url(prefix, absu, False) if mode == "full" else absu

    def pl(u):
        return make_url(prefix, urllib.parse.urljoin(base_url, u), True)

    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
            continue
        if s.startswith("#"):
            if s.startswith("#EXT-X-STREAM-INF"):
                next_is_playlist = True
            if 'URI="' in s:
                playlist_tag = s.startswith(("#EXT-X-MEDIA", "#EXT-X-I-FRAME-STREAM-INF"))
                s = URI_ATTR.sub(lambda m: 'URI="%s"' % (pl(m.group(1)) if playlist_tag else seg(m.group(1))), s)
            out.append(s)
            continue
        if next_is_playlist or _is_playlist_url(urllib.parse.urljoin(base_url, s)):
            out.append(pl(s))
        else:
            out.append(seg(s))
        next_is_playlist = False
    return "\n".join(out) + "\n"


def upstream_headers(extra=None, range_header=None):
    h = {
        "User-Agent": config.USER_AGENT,
        "Referer": "https://vk.com/",
        "Origin": "https://vk.com",
        "Accept": "*/*",
    }
    for k, v in (extra or {}).items():
        if k.lower() in ("cookie", "host", "accept-encoding", "content-length"):
            continue
        h[k] = v
    if range_header:
        h["Range"] = range_header
    return h


def open_upstream(url, extra_headers=None, range_header=None, timeout=20):
    req = urllib.request.Request(url, headers=upstream_headers(extra_headers, range_header))
    return urllib.request.urlopen(req, timeout=timeout)


def looks_like_playlist(first_bytes, content_type):
    return first_bytes.lstrip().startswith(b"#EXTM3U") or "mpegurl" in (content_type or "").lower()
