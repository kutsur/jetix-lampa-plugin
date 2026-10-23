import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

os.environ.update({
    "JETIX_ENV_FILE": "/nonexistent",
    "VK_TOKEN": "test-token",
    "GROUP_ID": "123",
    "SECRET": "s3cr3t-s3cr3t-s3cr3t",
    "PUBLIC_URL": "",
    "SEARCH_WALL": "1",
})

import config  # noqa: E402
import proxy  # noqa: E402
import vk  # noqa: E402
import app  # noqa: E402

SEGMENT = b"\x47" * 1880


class FakeCDN(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeCDN.hits.append((self.path, self.headers.get("Referer")))
        if self.path.startswith("/live/master.m3u8"):
            body = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n"
                    "720/index.m3u8?sig=abc\n"
                    '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="audio/index.m3u8"\n').encode()
            ctype = "application/vnd.apple.mpegurl"
        elif self.path.startswith("/live/720/index.m3u8"):
            body = ("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:5\n"
                    '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
                    "#EXTINF:2.0,\nseg5.ts?sig=abc\n#EXTINF:2.0,\nhttps://other.cdn/seg6.ts\n").encode()
            ctype = "application/x-mpegURL"
        elif self.path.startswith("/live/720/seg5.ts"):
            body, ctype = SEGMENT, "video/mp2t"
        elif self.path == "/expired.m3u8":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def get(url, follow=True):
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cdn = serve(FakeCDN)
        cls.cdn_url = "http://127.0.0.1:%d" % cls.cdn.server_address[1]
        cls.srv = serve(app.Handler)
        cls.base = "http://127.0.0.1:%d/%s" % (cls.srv.server_address[1], config.SECRET)

    @classmethod
    def tearDownClass(cls):
        cls.cdn.shutdown()
        cls.srv.shutdown()

    def setUp(self):
        app.resolver.__init__()
        FakeCDN.hits.clear()
        self.videos = []
        self.wall = []
        self.ytdlp_calls = []
        self.ytdlp_result = None

        def fake_api(method, **params):
            if method == "video.get" and "videos" in params:
                wanted = params["videos"].split(",")
                return {"items": [dict(v, player="https://vk.com/video_ext.php?oid=%s&id=%s&hash=h" % (v["owner_id"], v["id"]))
                                  for v in self.wall if "%s_%s" % (v["owner_id"], v["id"]) in [w.rsplit("_", 1)[0] if w.count("_") > 1 else w for w in wanted]]}
            if method == "video.get":
                return {"items": self.videos}
            if method == "wall.get":
                return {"items": [{"attachments": [{"type": "video", "video": v}]} for v in self.wall]}
            raise AssertionError(method)

        def fake_ytdlp(url, flat=False, playlist_end=None):
            self.ytdlp_calls.append(url)
            if self.ytdlp_result is None:
                raise vk.MediaError("ERROR: video unavailable")
            return self.ytdlp_result

        self._patches = [(vk, "api", vk.api), (vk, "ytdlp_json", vk.ytdlp_json)]
        vk.api = fake_api
        vk.ytdlp_json = fake_ytdlp
        config.PROXY_MODE = "off"

    def tearDown(self):
        for mod, name, orig in self._patches:
            setattr(mod, name, orig)

    def live_video(self, **kw):
        v = {"id": 7, "owner_id": -123, "title": "Стрим дня", "date": int(time.time()), "live": 1,
             "type": "live", "player": "https://vk.com/video_ext.php?oid=-123&id=7&hash=h",
             "image": [{"url": "http://img/1.jpg", "width": 320}, {"url": "http://img/2.jpg", "width": 1280}]}
        v.update(kw)
        return v

    def ytdlp_hls(self, url):
        return {"id": "-123_7", "formats": [
            {"format_id": "hls-360", "protocol": "m3u8_native", "height": 360, "url": url + "?v=360", "manifest_url": url},
            {"format_id": "hls-720", "protocol": "m3u8_native", "height": 720, "url": url + "?v=720", "manifest_url": url,
             "http_headers": {"User-Agent": "UA-from-ytdlp", "Referer": "https://vk.com/"}},
        ]}


class TestSelection(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(vk.classify({"live_status": "started"}), "live")
        self.assertEqual(vk.classify({"live_status": "waiting"}), "upcoming")
        self.assertEqual(vk.classify({"live_status": "finished", "live": 1}), "finished")
        self.assertEqual(vk.classify({"upcoming": 1, "live": 1}), "upcoming")
        self.assertEqual(vk.classify({"live": 1}), "live")
        self.assertEqual(vk.classify({"type": "live"}), "finished")
        self.assertIsNone(vk.classify({"type": "video"}))

    def test_pick_prefers_live(self):
        mk = lambda st, d, s=0: {"status": st, "date": d, "start_time": s}
        self.assertEqual(vk.pick([mk("finished", 300), mk("live", 100), mk("upcoming", 400, 999)])["status"], "live")
        self.assertEqual(vk.pick([mk("finished", 300), mk("upcoming", 1, 2000), mk("upcoming", 1, 1000)])["start_time"], 1000)
        self.assertEqual(vk.pick([mk("finished", 100), mk("finished", 300)])["date"], 300)
        self.assertIsNone(vk.pick([]))

    def test_files_preferred(self):
        m = vk._from_files({"mp4_720": "http://x/720.mp4", "hls_live": "http://x/live.m3u8"})
        self.assertEqual((m["type"], m["url"]), ("hls", "http://x/live.m3u8"))
        m = vk._from_files({"mp4_480": "http://x/480.mp4", "mp4_1080": "http://x/1080.mp4"})
        self.assertEqual((m["type"], m["url"]), ("mp4", "http://x/1080.mp4"))


class TestRewrite(unittest.TestCase):
    def setUp(self):
        config.SECRET = "s3cr3t-s3cr3t-s3cr3t"

    def test_manifest_and_full(self):
        text = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow/index.m3u8\n"
                '#EXT-X-KEY:METHOD=AES-128,URI="k.bin"\n#EXTINF:2,\nseg1.ts?a=1\n')
        out = proxy.rewrite(text, "https://cdn.vk/live/master.m3u8?t=1", "https://h/S", "manifest")
        lines = out.splitlines()
        self.assertTrue(lines[2].startswith("https://h/S/p/") and lines[2].endswith(".m3u8"))
        self.assertIn('URI="https://cdn.vk/live/k.bin"', lines[3])
        self.assertEqual(lines[5], "https://cdn.vk/live/seg1.ts?a=1")
        out = proxy.rewrite(text, "https://cdn.vk/live/master.m3u8", "https://h/S", "full")
        self.assertTrue(out.splitlines()[5].endswith(".ts"))
        self.assertIn("/p/", out.splitlines()[3])

    def test_signature(self):
        u = proxy.make_url("", "https://cdn.vk/a.ts", False)
        self.assertEqual(proxy.parse_path(u[len("/p/"):]), "https://cdn.vk/a.ts")
        bad = u[len("/p/"):].replace(u.split("/")[2], "0" * 20)
        self.assertIsNone(proxy.parse_path(bad))


class TestServer(Base):
    def test_auth(self):
        port = self.srv.server_address[1]
        self.assertEqual(get(self.base + "/health")[0], 200)
        self.assertEqual(get("http://127.0.0.1:%d/" % port)[0], 404)
        self.assertEqual(get("http://127.0.0.1:%d/health" % port)[0], 404)
        self.assertEqual(get("http://127.0.0.1:%d/live.json" % port)[0], 404)
        self.assertEqual(get(self.base + "/plugin.js")[0], 404)
        self.assertEqual(get("http://127.0.0.1:%d/wrong/live.json" % port)[0], 404)

    def test_no_stream(self):
        code, _, body = get(self.base + "/live.json")
        data = json.loads(body)
        self.assertEqual((code, data["status"], data["live"], data["playable"]), (200, "none", False, False))
        self.assertEqual(get(self.base + "/live.m3u8")[0], 404)

    def test_upcoming(self):
        self.videos = [self.live_video(live=None, upcoming=1, live_start_time=2000000000)]
        data = json.loads(get(self.base + "/live.json")[2])
        self.assertEqual((data["status"], data["playable"], data["start_time"]), ("upcoming", False, 2000000000))
        self.assertEqual(self.ytdlp_calls, [])
        code, _, body = get(self.base + "/live.m3u8")
        self.assertEqual(code, 404)
        self.assertIn("ещё не начался", body.decode())

    def test_live_via_files_redirect(self):
        self.videos = [self.live_video(files={"hls_live": self.cdn_url + "/live/master.m3u8"})]
        data = json.loads(get(self.base + "/live.json")[2])
        self.assertTrue(data["live"] and data["playable"])
        self.assertEqual(data["url"], self.base + "/live.m3u8")
        self.assertEqual(data["thumb"], "http://img/2.jpg")
        code, headers, _ = get(self.base + "/live.m3u8", follow=False)
        self.assertEqual(code, 302)
        self.assertEqual(headers["Location"], self.cdn_url + "/live/master.m3u8")
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    def test_live_via_ytdlp_player_then_cache(self):
        self.videos = [self.live_video()]
        self.ytdlp_result = self.ytdlp_hls(self.cdn_url + "/live/master.m3u8")
        get(self.base + "/live.json")
        get(self.base + "/live.m3u8", follow=False)
        self.assertEqual(self.ytdlp_calls, ["https://vk.com/video_ext.php?oid=-123&id=7&hash=h"])
        get(self.base + "/live.json?fresh=1")
        self.assertEqual(len(self.ytdlp_calls), 2)

    def test_ytdlp_failure_reports_error(self):
        self.videos = [self.live_video()]
        data = json.loads(get(self.base + "/live.json")[2])
        self.assertTrue(data["live"])
        self.assertFalse(data["playable"])
        self.assertIn("yt-dlp", data["error"])
        self.assertIn("COOKIES", data["error"])
        self.assertEqual(len(self.ytdlp_calls), 2)

    def test_wall_fallback(self):
        self.wall = [{"id": 9, "owner_id": -123, "access_key": "ak", "title": "С поста", "live": 1, "date": 5}]
        self.ytdlp_result = self.ytdlp_hls(self.cdn_url + "/live/master.m3u8")
        data = json.loads(get(self.base + "/live.json")[2])
        self.assertEqual((data["title"], data["status"]), ("С поста", "live"))

    def test_finished_mp4_record(self):
        self.videos = [self.live_video(live=None, files={"mp4_720": self.cdn_url + "/rec.mp4"})]
        data = json.loads(get(self.base + "/live.json")[2])
        self.assertEqual((data["status"], data["type"]), ("finished", "mp4"))
        self.assertTrue(data["url"].endswith("/record.mp4"))
        code, headers, _ = get(self.base + "/live.m3u8", follow=False)
        self.assertEqual((code, headers["Location"]), (302, self.base + "/record.mp4"))

    def test_proxy_full(self):
        config.PROXY_MODE = "full"
        self.videos = [self.live_video()]
        self.ytdlp_result = self.ytdlp_hls(self.cdn_url + "/live/master.m3u8")
        code, headers, body = get(self.base + "/live.m3u8")
        self.assertEqual(code, 200)
        self.assertIn("mpegurl", headers["Content-Type"])
        master = body.decode().splitlines()
        variant_url = master[2]
        self.assertTrue(variant_url.startswith(self.base + "/p/") and variant_url.endswith(".m3u8"))
        self.assertIn('URI="' + self.base + "/p/", master[3])
        variant = get(variant_url)[2].decode().splitlines()
        seg_url = [l for l in variant if "seg5" in l or (l.startswith(self.base) and l.endswith(".ts"))][0]
        code, headers, data = get(seg_url)
        self.assertEqual((code, data), (200, SEGMENT))
        self.assertTrue(all(ref == "https://vk.com/" for _, ref in FakeCDN.hits))
        head, tail = seg_url.split("/p/", 1)
        forged = head + "/p/" + "0" * 20 + "/" + tail.split("/", 1)[1]
        self.assertEqual(get(forged)[0], 403)

    def test_proxy_manifest_segments_direct(self):
        config.PROXY_MODE = "manifest"
        self.videos = [self.live_video(files={"hls": self.cdn_url + "/live/master.m3u8"})]
        master = get(self.base + "/live.m3u8")[2].decode().splitlines()
        variant = get(master[2])[2].decode().splitlines()
        self.assertIn(self.cdn_url + "/live/720/seg5.ts?sig=abc", variant)
        self.assertIn("https://other.cdn/seg6.ts", variant)

    def test_upstream_expired(self):
        config.PROXY_MODE = "full"
        self.videos = [self.live_video(files={"hls": self.cdn_url + "/expired.m3u8"})]
        self.assertEqual(get(self.base + "/live.m3u8")[0], 502)

    def test_playlist(self):
        code, _, body = get(self.base + "/playlist.m3u")
        self.assertEqual(code, 200)
        self.assertIn(self.base + "/live.m3u8", body.decode())

    def test_cors_preflight(self):
        req = urllib.request.Request(self.base + "/live.json", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 204)
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")

    def test_status_page_hides_nothing_secret(self):
        self.videos = [self.live_video(files={"hls": self.cdn_url + "/live/master.m3u8"})]
        body = get(self.base + "/status")[2].decode()
        self.assertIn("Стрим дня", body)
        self.assertNotIn("test-token", body)


if __name__ == "__main__":
    unittest.main()
