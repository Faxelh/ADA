"""
Faux yt-dlp pour tester ADA sans réseau (sur PC uniquement).
Liens : https://test.invalid/<mode>/<id>[/<titre>]
  dash     : vidéo H.264 seule + son AAC séparés (à assembler) + un WebM écarté
  single   : MP4 complet (index en fin de fichier)
  audio    : son seul
  slow     : comme dash, lentement (pause / annulation)
  vp9only  : uniquement du WebM -> aucun format compatible
  live     : direct en cours
  error    : erreur <titre>
  playlist : 3 vidéos
"""
import os
import shutil
import time
import urllib.parse

from yt_dlp.utils import DownloadCancelled, DownloadError  # noqa: F401
from yt_dlp.version import __version__  # noqa: F401

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "fixtures")
CALLS = []  # (format_id, chemin) pour les tests


def _fmt(format_id, fixture, **extra):
    data = {"format_id": format_id, "url": "fixture://" + fixture, "protocol": "https",
            "filesize": os.path.getsize(os.path.join(FIXTURES, fixture)), "_fixture": fixture}
    data.update(extra)
    return data


class YoutubeDL:
    def __init__(self, params=None):
        self.params = params or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @staticmethod
    def _parse(url):
        parts = urllib.parse.urlparse(url).path.strip("/").split("/")
        mode = parts[0] if parts and parts[0] else "dash"
        vid = parts[1] if len(parts) > 1 else "vid"
        title = urllib.parse.unquote(parts[2]) if len(parts) > 2 else f"Vidéo {vid}"
        return mode, vid, title

    @staticmethod
    def _fake_search(url):
        # "ytsearch5:ma recherche" -> count=5, query="ma recherche"
        rest = url[len("ytsearch"):]
        count_str, _, query = rest.partition(":")
        try:
            count = max(1, int(count_str))
        except ValueError:
            count = 10
        query = urllib.parse.unquote(query) or "test"
        entries = [{"id": f"s{i}", "title": f"{query} résultat {i}", "uploader": "Chaîne test",
                    "duration": 120 + i, "webpage_url": f"https://test.invalid/dash/s{i}",
                    "thumbnail": None, "view_count": 1000 * i}
                   for i in range(1, count + 1)]
        return {"_type": "playlist", "title": f"Recherche : {query}", "entries": entries}

    @staticmethod
    def _fake_trending():
        entries = [{"id": f"t{i}", "title": f"Tendance {i}", "uploader": "Chaîne test",
                    "duration": 180 + i, "webpage_url": f"https://test.invalid/dash/t{i}",
                    "thumbnail": None, "view_count": 500000 - i * 1000}
                   for i in range(1, 16)]
        return {"_type": "playlist", "title": "Tendances", "entries": entries}

    def extract_info(self, url, download=True):
        if url.startswith("ytsearch"):
            return self._fake_search(url)
        if "/feed/trending" in url:
            return self._fake_trending()
        mode, vid, title = self._parse(url)
        if mode == "error":
            raise DownloadError("ERROR: " + title)
        if mode == "playlist":
            return {"_type": "playlist", "title": "Ma playlist", "entries": [
                {"id": f"p{i}", "title": f"Titre {i}", "url": f"https://test.invalid/dash/p{i}", "duration": 3}
                for i in (1, 2, 3)]}
        info = {"id": vid, "title": title, "uploader": "Testeur", "duration": 2, "webpage_url": url,
                "thumbnail": None, "extractor_key": "Fake"}
        if mode == "live":
            info["is_live"] = True
        webm = _fmt("303", "v_frag.mp4", ext="webm", vcodec="vp9", acodec="none", height=1080, tbr=3000)
        video = _fmt("136", "v_frag.mp4", ext="mp4", container="mp4_dash", vcodec="avc1.4d401f",
                     acodec="none", height=720, tbr=1000)
        video_abs = _fmt("135", "v_frag_abs.mp4", ext="mp4", vcodec="avc1.4d401e", acodec="none",
                         height=480, tbr=500)
        audio = _fmt("140", "a_frag.m4a", ext="m4a", container="m4a_dash", vcodec="none",
                     acodec="mp4a.40.2", abr=128)
        combined = _fmt("18", "av_plain.mp4", ext="mp4", vcodec="avc1.42001E", acodec="mp4a.40.2",
                        height=360, tbr=600)
        if mode in ("dash", "slow"):
            info["formats"] = [webm, video_abs, video, audio, combined]
        elif mode == "single":
            info["formats"] = [combined]
        elif mode == "audio":
            info["formats"] = [audio]
        elif mode == "vp9only":
            info["formats"] = [webm]
        info["_mode"] = mode
        return info

    def prepare_filename(self, info):
        template = self.params["outtmpl"]["default"]
        return template.replace("%(ext)s", info.get("ext") or "mp4")

    def process_info(self, info):
        path = self.prepare_filename(info)
        source = os.path.join(FIXTURES, info["_fixture"])
        CALLS.append((info.get("format_id"), path))
        size = os.path.getsize(source)
        part = path + ".part"
        slow = info.get("_mode") == "slow"
        steps = 40 if slow else 4
        chunk = max(1, size // steps + 1)
        start = os.path.getsize(part) if os.path.exists(part) else 0
        with open(source, "rb") as src, open(part, "ab") as out:
            src.seek(start)
            done = start
            while done < size:
                block = src.read(chunk)
                out.write(block)
                out.flush()
                done += len(block)
                for hook in self.params.get("progress_hooks", []):
                    hook({"status": "downloading", "downloaded_bytes": done, "total_bytes": size,
                          "speed": 1000.0, "eta": 1, "info_dict": info})
                if slow:
                    time.sleep(0.05)
        os.replace(part, path)
        for hook in self.params.get("progress_hooks", []):
            hook({"status": "finished", "filename": path, "info_dict": info})
        info["filepath"] = path
        return info
