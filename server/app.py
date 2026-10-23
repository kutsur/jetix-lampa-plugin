#!/usr/bin/env python3
import json
import logging
import shutil
import sys
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

import config
import proxy
from vk import Resolver, MediaError

log = logging.getLogger("jetix")
resolver = Resolver()

M3U8_TYPE = "application/vnd.apple.mpegurl"


def public_status(result, media=None, media_error=None, base=""):
    s = result.get("stream")
    out = {
        "live": bool(s and s["status"] == "live"),
        "status": s["status"] if s else "none",
        "title": s["title"] if s else None,
        "thumb": s["thumb"] if s else None,
        "page": s["page"] if s else None,
        "date": s["date"] if s else None,
        "start_time": s["start_time"] if s else None,
        "spectators": s["spectators"] if s else None,
        "playable": bool(media),
        "type": media["type"] if media else None,
        "url": None,
        "error": result.get("error") or media_error,
        "updated": int(time.time()),
    }
    if media:
        out["url"] = base + ("/live.m3u8" if media["type"] == "hls" else "/record.mp4")
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "jetix"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        path = self.path.split("?", 1)[0]
        if config.SECRET:
            path = path.replace(config.SECRET, "<secret>")
        log.info("%s %s %s", self.command, path, args[1] if len(args) > 1 else "")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range")

    def send(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False, indent=1), "application/json; charset=utf-8")

    def redirect(self, url):
        self.send_response(302)
        self._cors()
        self.send_header("Location", url)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def base_url(self):
        if config.PUBLIC_URL:
            root = config.PUBLIC_URL
        else:
            proto = self.headers.get("X-Forwarded-Proto", "http")
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "localhost")
            root = "%s://%s" % (proto, host)
        return root + "/" + config.SECRET

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        fresh = "fresh" in query
        prefix = "/" + config.SECRET
        if not config.SECRET or not (parts.path == prefix or parts.path.startswith(prefix + "/")):
            return self.send(404, "not found\n")
        route = parts.path[len(prefix):].lstrip("/")

        try:
            if route == "live.m3u8":
                return self.route_live(fresh, want="hls")
            if route == "record.mp4":
                return self.route_live(fresh, want="mp4")
            if route == "live.json":
                return self.route_json(fresh, lite="lite" in query)
            if route == "playlist.m3u":
                return self.route_playlist()
            if route == "health":
                return self.send(200, "ok\n")
            if route in ("", "status"):
                return self.route_status(fresh)
            if route.startswith("p/"):
                return self.route_proxy(route[2:])
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            log.exception("ошибка обработки %s", route.split("/", 1)[0])
            return self.send(500, "internal error\n")
        return self.send(404, "not found\n")

    def _resolve(self, fresh):
        result = resolver.current(fresh=fresh)
        stream = result.get("stream")
        media, media_error = None, None
        if stream and stream["status"] in ("live", "finished"):
            try:
                media = resolver.media(stream, fresh=fresh)
            except MediaError as e:
                media_error = str(e)
                log.error("не удалось получить поток %s: %s", stream["key"], e)
        return result, media, media_error

    def route_json(self, fresh, lite=False):
        if lite:
            return self.send_json(public_status(resolver.current(fresh=fresh), base=self.base_url()))
        result, media, media_error = self._resolve(fresh)
        self.send_json(public_status(result, media, media_error, self.base_url()))

    def route_live(self, fresh, want):
        result, media, media_error = self._resolve(fresh)
        stream = result.get("stream")
        if not media:
            if result.get("error"):
                msg = "Ошибка поиска трансляции: %s\n" % result["error"]
            elif not stream:
                msg = "В группе не найдено трансляций\n"
            elif stream["status"] == "upcoming":
                msg = "Эфир ещё не начался: %s\n" % stream["title"]
            else:
                msg = "Не удалось получить поток: %s\n" % media_error
            return self.send(404, msg)
        if want == "hls" and media["type"] != "hls":
            return self.redirect(self.base_url() + "/record.mp4")
        if want == "mp4" and media["type"] != "mp4":
            return self.redirect(self.base_url() + "/live.m3u8")

        if media["type"] == "mp4":
            if config.PROXY_MODE == "full":
                return self._proxy_fetch(media["url"], media.get("headers"))
            return self.redirect(media["url"])
        if config.PROXY_MODE == "off":
            return self.redirect(media["url"])
        return self._proxy_fetch(media["url"], media.get("headers"), force_playlist=True)

    def route_proxy(self, rest):
        if config.PROXY_MODE == "off":
            return self.send(404, "not found\n")
        url = proxy.parse_path(rest)
        if not url:
            return self.send(403, "bad signature\n")
        return self._proxy_fetch(url, resolver.last_headers)

    def _proxy_fetch(self, url, headers, force_playlist=False):
        try:
            up = proxy.open_upstream(url, headers, self.headers.get("Range"))
        except urllib.error.HTTPError as e:
            log.warning("upstream %s для %s", e.code, url.split("?", 1)[0])
            if e.code in (403, 404, 410):
                resolver.invalidate_media()
            return self.send(502, "upstream error %s\n" % e.code)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return self.send(502, "upstream unreachable: %s\n" % e)

        with up:
            ctype = up.headers.get("Content-Type", "")
            head = b"" if force_playlist else up.read(64)
            if force_playlist or proxy.looks_like_playlist(head, ctype):
                text = (head + up.read()).decode("utf-8", "replace")
                body = proxy.rewrite(text, up.geturl(), self.base_url(), config.PROXY_MODE)
                return self.send(200, body, M3U8_TYPE)

            self.send_response(up.status)
            self._cors()
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                if up.headers.get(h):
                    self.send_header(h, up.headers[h])
            if not up.headers.get("Content-Length"):
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command == "HEAD":
                return
            self.wfile.write(head)
            shutil.copyfileobj(up, self.wfile, 64 * 1024)

    def route_playlist(self):
        body = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="jetix" tvg-name="{name}" group-title="VK",{name}\n'
            "{base}/live.m3u8\n"
        ).format(name=config.CHANNEL_NAME, base=self.base_url())
        self.send(200, body, "audio/x-mpegurl; charset=utf-8")

    def route_status(self, fresh):
        result, media, media_error = self._resolve(fresh)
        base = self.base_url()
        st = public_status(result, media, media_error, base)
        lines = [
            "Jetix", "=====", "",
            "Статус:     %s" % st["status"],
            "Название:   %s" % (st["title"] or "-"),
            "Страница:   %s" % (st["page"] or "-"),
            "Поток:      %s" % (("%s через %s" % (media["type"], media["method"])) if media else "-"),
            "Прокси:     %s" % config.PROXY_MODE,
        ]
        if st["error"]:
            lines.append("Ошибка:     %s" % st["error"])
        for w in result.get("warnings") or []:
            lines.append("Внимание:   %s" % w)
        lines += ["", "Найденные трансляции:"]
        for s in result.get("streams") or []:
            lines.append("  [%-8s] %s  %s  (%s)" % (s["status"], s["key"], s["title"], s["source"]))
        lines += ["", "Адрес сервера для плагина: %s" % base,
                  "IPTV плейлист:             %s/playlist.m3u" % base,
                  "Поток:                     %s/live.m3u8" % base,
                  "", "?fresh=1 — сбросить кэш"]
        self.send(200, "\n".join(lines) + "\n")


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    problems = config.problems(require_secret=True)
    if problems:
        for p in problems:
            log.error("конфигурация: %s", p)
        sys.exit(2)
    srv = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    srv.daemon_threads = True
    log.info("Jetix слушает http://%s:%s (прокси: %s)", config.HOST, config.PORT, config.PROXY_MODE)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
