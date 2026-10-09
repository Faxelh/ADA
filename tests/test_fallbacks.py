"""
Corrections des téléchargements : autres clients YouTube en cas d'échec,
blocage détecté, fichier final vérifié, HEVC « hev1 » rendu lisible
(« hvc1 »), vidéo TikTok lue directement si yt-dlp échoue.
"""

import os
import shutil
import struct
import unittest
from unittest import mock

from tests import support

if not support.REAL_YTDLP:
    from player.core import mp4mux, tiktok_photo
    from player.engine import downloader


@unittest.skipIf(support.REAL_YTDLP, "tests réservés au faux yt-dlp")
class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.cfg = support.TempConfig().__enter__()
        from player.core import storage
        self.history = storage.get_history()
        self.events = []

    def tearDown(self):
        self.cfg.__exit__(None, None, None)

    def run_job(self, url, media="video"):
        job = {"id": "fb" + str(len(self.events)), "url": url, "media_type": media}
        return downloader.download(job, lambda kind, data: self.events.append((kind, data)),
                                   lambda: False, self.history)

    def test_youtube_tries_other_clients(self):
        real = downloader._extract
        clients = []

        def flaky(url, settings, logger, client=None):
            clients.append(client)
            if len(clients) < 3:
                raise Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")
            return real("https://test.invalid/dash/yt1/Vidéo YouTube", settings, logger, client)

        with mock.patch.object(downloader, "_extract", flaky), \
                mock.patch.object(downloader.platforms, "detect_platform", return_value="youtube"):
            result = self.run_job("https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(clients, downloader.YOUTUBE_CLIENTS[:3])
        self.assertTrue(os.path.isfile(result["items"][0]["filepath"]))

    def test_definitive_errors_are_not_retried(self):
        clients = []

        def gone(url, settings, logger, client=None):
            clients.append(client)
            raise Exception("ERROR: [youtube] abc: Video unavailable. This video has been removed")

        with mock.patch.object(downloader, "_extract", gone), \
                mock.patch.object(downloader.platforms, "detect_platform", return_value="youtube"):
            with self.assertRaises(Exception):
                self.run_job("https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(len(clients), 1)

    def test_tiktok_direct_fallback(self):
        fixture = support.fixture("av_plain.mp4")

        class FakeClient:
            def download(self, url, path, headers=None, progress=None, cancel=None):
                shutil.copyfile(fixture, path)
                if progress:
                    progress(1.0)
                return path

        video = {"id": "7312345678901234567", "desc": "Danse du jour", "uploader": "Créa", "handle": "crea",
                 "urls": ["https://v16.tiktokcdn.com/video.mp4"], "duration": 3, "thumbnail": None}

        def blocked(url, settings, logger, client=None):
            raise Exception("ERROR: [TikTok] 731: Unable to extract webpage video data")

        with mock.patch.object(downloader, "_extract", blocked), \
                mock.patch.object(downloader, "_photo_post", lambda url: (url, None)), \
                mock.patch.object(tiktok_photo, "load_video", lambda url: (url, video, FakeClient())):
            result = self.run_job("https://www.tiktok.com/@crea/video/7312345678901234567")
        item = result["items"][0]
        self.assertEqual(item["platform"], "tiktok")
        self.assertTrue(item["filepath"].endswith(".mp4"))
        details = mp4mux.probe(item["filepath"])
        self.assertTrue(details["video"])

    def test_stall_detected(self):
        hooks = []

        class Ydl:
            def __init__(self, options):
                hooks.extend(options.get("progress_hooks") or [])

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def prepare_filename(self, info):
                return "/nonexistent"

            def process_info(self, info):
                for hook in hooks:
                    hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
                with mock.patch.object(downloader.time, "monotonic", return_value=10_000.0):
                    for hook in hooks:
                        hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})

        work = os.path.join(self.cfg.root, "w")
        os.makedirs(work)
        with mock.patch.object(downloader, "_new_ydl", Ydl):
            with self.assertRaises(Exception) as caught:
                downloader._fetch_format({"formats": []}, {"format_id": "1", "ext": "mp4"}, work, "video",
                                         lambda: False, lambda *a: None, downloader._Logger())
        self.assertEqual(downloader.explain(caught.exception)[1], "stalled")

    def test_broken_final_file_is_rejected(self):
        broken = os.path.join(self.cfg.root, "broken.mp4")
        with open(broken, "wb") as handle:
            handle.write(struct.pack(">I", 16) + b"ftypisom" + b"\0" * 4 + b"\0\0\0\x08mdat")
        with self.assertRaises(downloader.PlayerError):
            downloader._verify(broken, "video", "merge")
        self.assertFalse(os.path.exists(broken))


class HevcTagTest(unittest.TestCase):
    def test_hev1_becomes_hvc1(self):
        entry = struct.pack(">I", 16) + b"hev1" + b"\0" * 8
        stsd = struct.pack(">I", 16 + len(entry)) + b"stsd" + b"\0\0\0\0" + struct.pack(">I", 1) + entry
        fixed = mp4mux._normalize_stsd(stsd)
        self.assertIn(b"hvc1", fixed)
        self.assertNotIn(b"hev1", fixed)
        self.assertEqual(len(fixed), len(stsd))

    def test_avc_untouched(self):
        entry = struct.pack(">I", 16) + b"avc1" + b"\0" * 8
        stsd = struct.pack(">I", 16 + len(entry)) + b"stsd" + b"\0\0\0\0" + struct.pack(">I", 1) + entry
        self.assertEqual(mp4mux._normalize_stsd(stsd), stsd)


class TikTokVideoParseTest(unittest.TestCase):
    def test_prefers_h264(self):
        item = {"id": "1", "desc": "x", "author": {"nickname": "A", "uniqueId": "a"},
                "video": {"bitrateInfo": [
                    {"CodecType": "h265_hvc1", "Bitrate": 900, "PlayAddr": {"UrlList": ["https://h265"]}},
                    {"CodecType": "h264", "Bitrate": 500, "PlayAddr": {"UrlList": ["https://h264"]}},
                ], "playAddr": "https://play", "cover": "https://cover"}}
        video = tiktok_photo.parse_video(item)
        self.assertEqual(video["urls"][0], "https://h264")
        self.assertIn("https://play", video["urls"])
        self.assertEqual(video["thumbnail"], "https://cover")


if __name__ == "__main__":
    unittest.main()
