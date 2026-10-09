"""
Analyse et téléchargement d'un lien (exécuté dans un fil de travail).

Différences avec la version Windows :
  - pas de FFmpeg : ADA choisit des flux MP4/AAC (formats.choose), les
    télécharge séparément puis les réunit avec mp4mux ;
  - YouTube : défis JavaScript résolus par JavaScriptCore (jsc.py) ;
  - connexions : les cookies du navigateur intégré sont transmis à yt-dlp.
"""

import copy
import logging
import os
import shutil
import time

from player.core import config, errors, formats, mp4mux, naming, platforms, tiktok_photo
from player.engine import jsc

log = logging.getLogger("player.engine")


class Stop(Exception):
    """Arrêt demandé (pause ou annulation)."""


class PlayerError(Exception):
    pass


# ----------------------------------------------------------------------
# yt-dlp
# ----------------------------------------------------------------------

class _Logger:
    def __init__(self):
        self.warnings = []

    def debug(self, message):
        if message.startswith("[debug] "):
            return
        log.debug(message)

    def info(self, message):
        log.debug(message)

    def warning(self, message):
        log.info("yt-dlp : %s", message)
        self.warnings.append(message)

    def error(self, message):
        log.warning("yt-dlp : %s", message)


def cookie_file():
    path = os.path.join(config.DATA_DIR or "", "cookies.txt")
    return path if os.path.isfile(path) and os.path.getsize(path) > 0 else None


def base_options(logger, settings=None):
    settings = settings or config.get_settings()
    options = {
        "logger": logger,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 2,
        "continuedl": True,
        "fixup": "never",            # les corrections passent par FFmpeg, absent ici
        "cachedir": os.path.join(config.CACHE_DIR or "", "yt-dlp"),
        "http_chunk_size": 10 * 1024 * 1024,
        "concurrent_fragment_downloads": 3,
        "remote_components": ["ejs:github"],
        "check_formats": False,
    }
    options.update(jsc.ytdlp_options())
    if settings.get("use_browser_cookies"):
        cookies = cookie_file()
        if cookies:
            options["cookiefile"] = cookies
    return options


def _new_ydl(options):
    import yt_dlp
    return yt_dlp.YoutubeDL(options)


def _cancelled_type():
    try:
        from yt_dlp.utils import DownloadCancelled
        return DownloadCancelled
    except Exception:
        return Exception


# ----------------------------------------------------------------------
# Analyse
# ----------------------------------------------------------------------

def _thumbnail(info):
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = [t for t in info.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
    return thumbs[-1]["url"] if thumbs else None


def _photo_post(url):
    """(url_réelle, données) si le lien est une publication photo TikTok, sinon (url, None)."""
    if not tiktok_photo.is_tiktok(url):
        return url, None
    if not (tiktok_photo.is_short_link(url) or tiktok_photo.is_photo_url(url)):
        return url, None
    real_url, post, _client = tiktok_photo.load(url)
    return real_url, post


def analyze(url, stop=None):
    """Infos pour l'écran d'analyse (titre, miniature, qualités…) ou liste d'une playlist."""
    url = url.strip()
    real_url, post = _photo_post(url)
    if post is not None:
        return {
            "kind": "photo", "url": real_url, "id": post["id"],
            "title": post["desc"] or f"Photos de @{post['handle']}", "uploader": post["uploader"],
            "thumbnail": post["images"][0]["url"] if post["images"] else None,
            "duration": None, "qualities": [formats.BEST],
            "photo_count": len(post["images"]), "has_music": bool(post["music_url"]),
            "platform": "tiktok",
        }

    logger = _Logger()
    options = base_options(logger)
    options.update({"noplaylist": False, "extract_flat": "in_playlist", "skip_download": True})
    with _new_ydl(options) as ydl:
        info = ydl.extract_info(real_url, download=False)
    if info is None:
        raise PlayerError("Aucune vidéo trouvée à cette adresse.")

    if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
        entries = []
        for entry in info.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            entry_url = entry.get("webpage_url") or entry.get("url")
            if entry.get("ie_key") == "Youtube" and entry.get("id") and not str(entry_url).startswith("http"):
                entry_url = f"https://www.youtube.com/watch?v={entry['id']}"
            if entry_url:
                entries.append({"url": entry_url, "title": entry.get("title") or entry_url,
                                "duration": entry.get("duration"), "id": entry.get("id")})
        if len(entries) == 1 and not platforms.is_youtube_playlist(real_url):
            return analyze(entries[0]["url"], stop)
        return {"kind": "playlist", "url": real_url, "title": info.get("title") or "Playlist",
                "entries": entries, "platform": platforms.detect_platform(real_url)}

    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise PlayerError("player:live")

    fmts = info.get("formats") or [info]
    return {
        "kind": "video", "url": real_url, "id": info.get("id"),
        "title": naming.clean_title(info), "uploader": info.get("uploader") or info.get("channel") or "",
        "thumbnail": _thumbnail(info), "duration": info.get("duration"),
        "qualities": formats.qualities_from_formats(fmts),
        "audio_ok": formats.choose(fmts, "audio") is not None,
        "video_ok": formats.choose(fmts, "video") is not None,
        "platform": platforms.detect_platform(info.get("webpage_url") or real_url),
        "view_count": info.get("view_count"),
    }


def _flat_entries(info):
    """Transforme les entrées « à plat » (recherche, tendances) en liste légère."""
    results = []
    for entry in (info or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        entry_url = entry.get("webpage_url") or entry.get("url")
        if entry.get("ie_key") == "Youtube" and video_id and not str(entry_url).startswith("http"):
            entry_url = f"https://www.youtube.com/watch?v={video_id}"
        if not entry_url:
            continue
        results.append({
            "url": entry_url, "id": video_id, "title": entry.get("title") or entry_url,
            "uploader": entry.get("uploader") or entry.get("channel") or "",
            "duration": entry.get("duration"), "thumbnail": _thumbnail(entry),
            "view_count": entry.get("view_count"),
        })
    return results


def search(query, limit=20):
    """Recherche YouTube (onglet Recherche) : résultats légers, sans miniature détaillée."""
    query = (query or "").strip()
    if not query:
        return []
    logger = _Logger()
    options = base_options(logger)
    options.update({"noplaylist": True, "extract_flat": "in_playlist", "skip_download": True})
    with _new_ydl(options) as ydl:
        info = ydl.extract_info(f"ytsearch{max(1, min(limit, 50))}:{query}", download=False)
    return _flat_entries(info)


def trending(limit=25):
    """Tendances YouTube (page publique /feed/trending, aucune clé d'API requise).

    Cette page change parfois de forme côté YouTube (région, consentement…)
    et peut échouer alors que tout le reste (recherche, import) fonctionne.
    Dans ce cas, on retombe sur une recherche générique plutôt que de
    laisser l'onglet Trending complètement inutilisable."""
    logger = _Logger()
    options = base_options(logger)
    options.update({"noplaylist": False, "extract_flat": "in_playlist", "skip_download": True,
                    "playlist_items": f"1-{max(1, min(limit, 50))}"})
    try:
        with _new_ydl(options) as ydl:
            info = ydl.extract_info("https://www.youtube.com/feed/trending", download=False)
        results = _flat_entries(info)
        if results:
            return results
    except Exception:
        log.exception("Tendances YouTube indisponibles, repli sur une recherche générique")
    return search("musique populaire", limit)


# ----------------------------------------------------------------------
# Téléchargement
# ----------------------------------------------------------------------

def target_folder(platform, media_type, base=None):
    base = base or config.DOCUMENTS_DIR
    name = platforms.platform_name(platform)
    if platform == "other":
        name = "Autres"
    return os.path.join(base, name, "Audio" if media_type == "audio" else "Vidéos")


class Reporter:
    """Regroupe l'avancement de plusieurs fichiers (vidéo + son) en une seule barre."""

    def __init__(self, emit, weights):
        self.emit = emit
        self.weights = weights
        self.done = 0.0
        self.started = time.monotonic()

    def part(self, index, fraction, speed=None, eta=None):
        start = sum(self.weights[:index])
        total = start + self.weights[index] * max(0.0, min(1.0, fraction))
        self.emit("progress", {"fraction": min(0.97, total), "speed": speed, "eta": eta})


def _fetch_format(info, fmt, work_dir, name, stop, on_progress, logger):
    """Télécharge un seul format de info dans work_dir/name.<ext>. Renvoie le chemin."""
    cancelled = _cancelled_type()

    watch = {"bytes": -1, "moved": time.monotonic()}

    def hook(data):
        if stop():
            raise cancelled("player:cancel")
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            now = time.monotonic()
            if done > watch["bytes"]:
                watch["bytes"] = done
                watch["moved"] = now
            elif now - watch["moved"] > STALL_SECONDS:
                raise cancelled("player:stalled")
            fraction = (done / total) if total else 0.0
            if not total and data.get("fragment_count"):
                fraction = (data.get("fragment_index") or 0) / data["fragment_count"]
            on_progress(fraction, data.get("speed"), data.get("eta"))

    options = base_options(logger)
    options.update({
        "format": str(fmt.get("format_id") or "best"),
        "outtmpl": {"default": os.path.join(work_dir, name + ".%(ext)s")},
        "progress_hooks": [hook],
        "paths": {"home": work_dir, "temp": work_dir},
    })
    single = copy.deepcopy(info)
    # extract_info a déjà recopié en haut du dict les champs du format choisi
    # automatiquement : on les retire tous pour ne garder que ceux de fmt.
    format_keys = set()
    for entry in info.get("formats") or []:
        if isinstance(entry, dict):
            format_keys.update(entry)
    format_keys.discard("formats")
    for key in format_keys | {"requested_formats", "requested_downloads", "requested_subtitles",
                              "__files_to_move", "filepath", "_filename", "filename", "format",
                              "format_note"}:
        single.pop(key, None)
    single.update(copy.deepcopy(fmt))
    single["format_id"] = fmt.get("format_id")
    if not single.get("ext"):
        single["ext"] = "m4a" if (not formats.has_video(fmt)) else "mp4"

    with _new_ydl(options) as ydl:
        expected = ydl.prepare_filename(single)
        ydl.process_info(single)
    path = single.get("filepath") or expected
    if not os.path.isfile(path):
        # Certains téléchargeurs changent l'extension : on prend le fichier produit.
        produced = [os.path.join(work_dir, f) for f in os.listdir(work_dir)
                    if f.startswith(name + ".") and not f.endswith((".part", ".ytdl"))]
        if not produced:
            raise PlayerError("Le fichier téléchargé est introuvable.")
        path = max(produced, key=os.path.getmtime)
    on_progress(1.0, None, 0)
    return path


def _finalize_single(source, target, stop, emit):
    """MP4 complet : réécrit proprement (index au début, étiquettes lisibles par
    l'iPhone). Si la réécriture échoue, le fichier d'origine est gardé tel quel."""
    if not mp4mux.is_mp4(source):
        raise PlayerError("player:container")
    emit("phase", {"phase": "processing", "label": "Finalisation de la vidéo…"})
    try:
        mp4mux.remux(source, target, cancel=stop)
    except mp4mux.Cancelled:
        _discard(target)
        raise
    except mp4mux.Mp4Error:
        log.info("Réécriture MP4 impossible, fichier gardé tel quel")
        _discard(target)
        shutil.move(source, target)
        return target
    _discard(source)
    return target


def _moov_first(path):
    """L'index (moov) est-il avant les données ? Nécessaire pour une lecture fluide."""
    try:
        with open(path, "rb") as handle:
            position = 0
            for _ in range(8):
                handle.seek(position)
                head = handle.read(16)
                if len(head) < 8:
                    return False
                size = int.from_bytes(head[:4], "big")
                kind = head[4:8]
                if kind == b"moov":
                    return True
                if kind == b"mdat":
                    return False
                if size == 1:
                    size = int.from_bytes(head[8:16], "big")
                if size < 8:
                    return False
                position += size
    except OSError:
        return False
    return False


# Clients YouTube essayés l'un après l'autre si le premier échoue (formats
# refusés en 403, déchiffrement impossible, aucun format lisible, blocage…).
# None = choix automatique de yt-dlp.
YOUTUBE_CLIENTS = [None, ["tv", "web_safari"], ["android_vr"], ["ios", "mweb"]]

# Échecs pour lesquels un autre client (YouTube) ou une autre méthode
# (TikTok) a une vraie chance de réussir.
ALTERNATIVE_CATEGORIES = {"js_runtime", "forbidden", "format", "parse", "stalled", "server", "unknown",
                          "mux", "auth", "impersonate", "crash"}

# Pas un seul octet reçu pendant ce délai : le téléchargement est bloqué.
STALL_SECONDS = 45


def _extract(url, settings, logger, client=None):
    options = base_options(logger, settings)
    if client:
        options["extractor_args"] = {"youtube": {"player_client": list(client)}}
    with _new_ydl(options) as ydl:
        return ydl.extract_info(url, download=False)


def _clear(folder):
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _verify(target, media_type, mode):
    """Vérifie que le fichier final se lit (pistes présentes, index valide)."""
    if not mp4mux.is_mp4(target):
        return
    try:
        details = mp4mux.probe(target)
    except mp4mux.Mp4Error as error:
        _discard(target)
        raise PlayerError(f"mp4mux : fichier final illisible ({error})") from error
    if media_type == "video" and mode in ("merge", "single") and not details.get("video"):
        _discard(target)
        raise PlayerError("mp4mux : piste vidéo absente du fichier final")
    if mode in ("merge", "audio", "extract_audio") and not details.get("audio"):
        _discard(target)
        raise PlayerError("mp4mux : piste audio absente du fichier final")


def download(job, emit, stop, history=None):
    """
    Télécharge job (dict : url, media_type, quality, platform, id) et renvoie
    {"items": [ {title, filepath, files, photos, ...} ]}.
    emit(type, données) : "phase", "progress", "name". stop() : vrai si on doit s'arrêter.

    YouTube : si un essai échoue, on recommence avec d'autres « clients »
    YouTube. TikTok : si yt-dlp échoue, on lit la vidéo directement sur la
    page publique.
    """
    settings = config.get_settings()
    url = job["url"].strip()
    work_dir = os.path.join(config.WORK_DIR, job["id"])
    os.makedirs(work_dir, exist_ok=True)

    emit("phase", {"phase": "starting", "label": "Analyse du lien…"})

    # --- Publication photo TikTok ------------------------------------------------
    real_url, post = _photo_post(url)
    if post is not None:
        return _download_photo(job, emit, stop, history, real_url, post, settings)

    platform = platforms.detect_platform(real_url)
    clients = YOUTUBE_CLIENTS if platform == "youtube" else [None]
    last_error = None
    category = None
    for attempt, client in enumerate(clients):
        if stop():
            raise Stop()
        if attempt:
            log.info("Nouvel essai avec les clients YouTube %s", client)
            _clear(work_dir)
            emit("phase", {"phase": "starting", "label": "Nouvel essai…"})
        try:
            return _download_once(job, emit, stop, history, real_url, settings, work_dir, client)
        except Stop:
            raise
        except Exception as error:  # noqa: BLE001 — classé ci-dessous
            if stop():
                raise Stop()
            last_error = error
            category = explain(error, platform)[1]
            log.info("Essai %d échoué (%s) : %s", attempt + 1, category, error)
            if category not in ALTERNATIVE_CATEGORIES:
                break

    if platform == "tiktok" and category in ALTERNATIVE_CATEGORIES | {"unavailable", "unsupported"}:
        try:
            _clear(work_dir)
            return _download_tiktok_direct(job, emit, stop, history, real_url, settings, work_dir)
        except Stop:
            raise
        except Exception:  # noqa: BLE001 — on garde l'erreur de yt-dlp, plus parlante
            log.exception("Lecture directe TikTok impossible")
            if stop():
                raise Stop()
    raise last_error


def _download_once(job, emit, stop, history, real_url, settings, work_dir, client=None):
    media_type = job.get("media_type", "video")
    quality = job.get("quality") or formats.BEST

    logger = _Logger()
    info = _extract(real_url, settings, logger, client)
    if stop():
        raise Stop()
    if info is None:
        raise PlayerError("Aucune vidéo trouvée à cette adresse.")
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in info.get("entries") or [] if isinstance(e, dict)]
        if not entries:
            raise PlayerError("Aucune vidéo trouvée à cette adresse.")
        info = entries[0]
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise PlayerError("player:live")

    platform = platforms.detect_platform(info.get("webpage_url") or real_url)
    video_id = info.get("id")
    if settings.get("skip_duplicates") and history is not None and not job.get("force"):
        if history.find_duplicate(media_type, url=real_url, video_id=video_id):
            raise PlayerError("player:duplicate")

    plan = formats.choose(info.get("formats") or [info], media_type, quality)
    if plan is None:
        raise PlayerError("player:noformat")

    title = naming.clean_title(info, settings.get("clean_titles"))
    folder = target_folder(platform, media_type)
    os.makedirs(folder, exist_ok=True)
    stem = job.get("stem")
    if not stem or not naming.stem_taken(folder, stem):
        stem = naming.unique_stem(folder, naming.safe_stem(title, None),
                                  quality if media_type == "video" else None)
    emit("name", {"title": title, "stem": stem, "id": video_id, "platform": platform,
                  "thumbnail": _thumbnail(info)})

    parts = [(key, plan[key]) for key in ("video", "audio") if plan.get(key)]
    sizes = [max(1.0, formats.estimated_size(fmt, info.get("duration"))) for _key, fmt in parts]
    total = sum(sizes)
    weights = [0.95 * size / total for size in sizes]
    reporter = Reporter(emit, weights)
    emit("phase", {"phase": "downloading", "label": "Téléchargement…"})

    files = {}
    mode = plan["mode"]
    target = None
    try:
        for index, (key, fmt) in enumerate(parts):
            if stop():
                raise Stop()
            files[key] = _fetch_format(
                info, fmt, work_dir, key, stop,
                lambda fraction, speed, eta, i=index: reporter.part(i, fraction, speed, eta),
                logger,
            )

        if stop():
            raise Stop()
        emit("phase", {"phase": "processing", "label": "Assemblage…"})
        emit("progress", {"fraction": 0.96, "speed": None, "eta": None})
        if mode == "merge":
            for key in ("video", "audio"):
                if not mp4mux.is_mp4(files[key]):
                    raise PlayerError("player:container")
            target = os.path.join(folder, stem + ".mp4")
            mp4mux.merge(files["video"], files["audio"], target, cancel=stop)
        elif mode == "single":
            target = _finalize_single(files["video"], os.path.join(folder, stem + ".mp4"), stop, emit)
        elif mode == "audio":
            source = files["audio"]
            if mp4mux.is_mp4(source):
                target = os.path.join(folder, stem + ".m4a")
                mp4mux.remux(source, target, kinds=("soun",), cancel=stop)
            else:
                ext = os.path.splitext(source)[1] or ".m4a"
                target = os.path.join(folder, stem + ext)
                shutil.move(source, target)
        else:  # extract_audio
            if not mp4mux.is_mp4(files["video"]):
                raise PlayerError("player:container")
            target = os.path.join(folder, stem + ".m4a")
            mp4mux.remux(files["video"], target, kinds=("soun",), cancel=stop)
        _verify(target, media_type, mode)
    except mp4mux.Cancelled:
        if target:
            _discard(target)
        raise Stop()
    except mp4mux.Mp4Error as error:
        if target:
            _discard(target)
        raise PlayerError(f"mp4mux : {error}") from error
    finally:
        naming.release_stem(folder, stem)

    shutil.rmtree(work_dir, ignore_errors=True)
    chosen = plan.get("video") or {}
    item = {
        "title": title, "filepath": target, "files": [target], "photos": [],
        "media_type": media_type, "quality": quality, "platform": platform,
        "video_id": video_id, "url": real_url, "thumbnail": _thumbnail(info),
        "duration": info.get("duration"), "height": chosen.get("height"),
        "uploader": info.get("uploader") or info.get("channel") or "",
        "filesize": os.path.getsize(target),
        "label": ("M4A" if media_type == "audio" else
                  (f"{chosen.get('height')}p" if chosen.get("height") else "MP4")),
    }
    emit("progress", {"fraction": 1.0, "speed": None, "eta": 0})
    return {"items": [item], "warnings": logger.warnings[-3:]}


def _download_tiktok_direct(job, emit, stop, history, url, settings, work_dir):
    """Vidéo TikTok sans yt-dlp : lue sur la page publique, puis téléchargée directement."""
    media_type = job.get("media_type", "video")
    emit("phase", {"phase": "starting", "label": "Analyse du lien…"})
    real_url, video, client = tiktok_photo.load_video(url)
    if stop():
        raise Stop()
    if not video.get("urls"):
        raise PlayerError("Ce lien ne correspond pas à une vidéo prise en charge.")
    video_id = video["id"]
    if settings.get("skip_duplicates") and history is not None and not job.get("force"):
        if history.find_duplicate(media_type, url=real_url, video_id=video_id):
            raise PlayerError("player:duplicate")
    title = naming.clean_title({"title": video["desc"], "uploader": video["uploader"], "id": video_id},
                               settings.get("clean_titles"))
    if not video["desc"]:
        title = f"TikTok de @{video['handle']}" if video["handle"] else f"TikTok {video_id}"
    folder = target_folder("tiktok", media_type)
    os.makedirs(folder, exist_ok=True)
    stem = naming.unique_stem(folder, naming.safe_stem(title, None))
    emit("name", {"title": title, "stem": stem, "id": video_id, "platform": "tiktok",
                  "thumbnail": video.get("thumbnail")})
    emit("phase", {"phase": "downloading", "label": "Téléchargement…"})
    source = os.path.join(work_dir, "tiktok.mp4")
    target = None
    try:
        failure = None
        for link in video["urls"][:6]:
            try:
                client.download(link, source, headers=tiktok_photo.MEDIA_HEADERS,
                                progress=lambda f: emit("progress", {"fraction": min(0.95, f * 0.95),
                                                                     "speed": None, "eta": None}),
                                cancel=stop)
                failure = None
                break
            except tiktok_photo.PhotoError as error:
                if "player:cancel" in str(error):
                    raise Stop()
                failure = error
        if failure is not None:
            raise failure
        emit("phase", {"phase": "processing", "label": "Assemblage…"})
        if media_type == "audio":
            target = os.path.join(folder, stem + ".m4a")
            mp4mux.remux(source, target, kinds=("soun",), cancel=stop)
            mode = "audio"
        else:
            target = _finalize_single(source, os.path.join(folder, stem + ".mp4"), stop, emit)
            mode = "single"
        _verify(target, media_type, mode)
    except mp4mux.Cancelled:
        if target:
            _discard(target)
        raise Stop()
    except mp4mux.Mp4Error as error:
        if target:
            _discard(target)
        raise PlayerError(f"mp4mux : {error}") from error
    finally:
        naming.release_stem(folder, stem)
    shutil.rmtree(work_dir, ignore_errors=True)
    item = {
        "title": title, "filepath": target, "files": [target], "photos": [],
        "media_type": media_type, "quality": formats.BEST, "platform": "tiktok",
        "video_id": video_id, "url": real_url, "thumbnail": video.get("thumbnail"),
        "duration": video.get("duration"), "height": None, "uploader": video["uploader"],
        "filesize": os.path.getsize(target), "label": "M4A" if media_type == "audio" else "MP4",
    }
    emit("progress", {"fraction": 1.0, "speed": None, "eta": 0})
    return {"items": [item], "warnings": []}


def _download_photo(job, emit, stop, history, url, post, settings):
    media_type = job.get("media_type", "video")
    video_id = post["id"]
    if settings.get("skip_duplicates") and history is not None and not job.get("force"):
        if history.find_duplicate(media_type, url=url, video_id=video_id):
            raise PlayerError("player:duplicate")
    title = naming.clean_title({"title": post["desc"], "uploader": post["uploader"], "id": video_id},
                               settings.get("clean_titles"))
    if not post["desc"]:
        title = f"Photos de @{post['handle']}" if post["handle"] else f"Photos TikTok {video_id}"
    folder = target_folder("tiktok", media_type)
    os.makedirs(folder, exist_ok=True)
    stem = naming.unique_stem(folder, naming.safe_stem(title, None))
    thumbnail = post["images"][0]["url"] if post["images"] else None
    emit("name", {"title": title, "stem": stem, "id": video_id, "platform": "tiktok", "thumbnail": thumbnail})
    emit("phase", {"phase": "downloading", "label": "Téléchargement des photos…"
                   if media_type == "video" else "Téléchargement de la musique…"})
    try:
        result = tiktok_photo.download(
            post, folder, stem, media_type,
            progress=lambda f: emit("progress", {"fraction": min(0.99, f), "speed": None, "eta": None}),
            cancel=stop,
        )
    except tiktok_photo.PhotoError as error:
        if "player:cancel" in str(error):
            raise Stop()
        raise
    finally:
        naming.release_stem(folder, stem)
    item = {
        "title": title, "filepath": result["main"], "files": result["files"], "photos": result["photos"],
        "media_type": media_type, "quality": formats.BEST, "platform": "tiktok", "video_id": video_id,
        "url": url, "thumbnail": thumbnail, "duration": post.get("music_duration"), "height": None,
        "uploader": post["uploader"], "label": result["label"],
        "filesize": sum(os.path.getsize(p) for p in result["files"] if os.path.isfile(p)),
    }
    emit("progress", {"fraction": 1.0, "speed": None, "eta": 0})
    return {"items": [item], "warnings": []}


def explain(error, platform=None):
    if isinstance(error, Stop):
        return ("Arrêté.", "cancelled")
    return errors.explain(error, platform)
