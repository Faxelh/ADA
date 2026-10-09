"""
Pont entre l'interface Swift (SwiftUI) d'ADA'S et le moteur Python.

L'app Swift appelle une seule fonction, dispatch(nom, json), et reçoit du
JSON en retour. Tout ce qui touche à Internet (yt-dlp), à la file des
imports et aux données (historique, playlists, Coffre) reste ici, en
Python, là où c'est déjà testé ; l'interface, le lecteur, Face ID et la
compression sont en Swift.

Chaque réponse est un objet JSON. En cas de problème : {"error": "..."}
(l'app n'est jamais interrompue par une exception Python).
"""

import json
import logging
import logging.handlers
import os
import sys
import threading
import time

log = logging.getLogger("player.bridge")

VAULT_FOLDER = ".coffre"          # dossier caché : invisible dans l'app Fichiers
BACKUP_NAME = "ADA-sauvegarde.json"
AUDIO_EXT = {".m4a", ".mp3", ".aac", ".wav", ".opus", ".ogg", ".flac", ".mp4", ".webm"}
SEARCH_LIMIT = 25


class _State:
    manager = None
    history = None
    playlists = None
    settings = None
    rev = 0
    lock = threading.Lock()


S = _State()


def _bump():
    with S.lock:
        S.rev += 1


# ----------------------------------------------------------------------
# Démarrage
# ----------------------------------------------------------------------

def _setup_logging(folder):
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        os.makedirs(folder, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(os.path.join(folder, "ada.log"), maxBytes=1_000_000,
                                                       backupCount=1, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except Exception:
        pass
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)


def op_init(args):
    from player.core import config, i18n, storage
    from player.engine.manager import DownloadManager

    if S.manager is not None:
        return {"ok": True, "already": True}
    config.init(args["documents"], args["data"], args["cache"])
    _setup_logging(os.path.join(args["data"], "logs"))
    i18n.init(args.get("language") or "fr")
    S.settings = config.get_settings()
    S.history = storage.get_history()
    S.playlists = storage.get_playlists()
    S.playlists.ensure_favorite()
    S.playlists.ensure_vault()
    S.history.add_listener(_bump)
    S.playlists.add_listener(_bump)
    S.manager = DownloadManager(S.settings, S.history)
    if S.manager.saved_queue():
        S.manager.resume_saved_queue()
    added = _rescan() if not S.history.entries() else 0
    log.info("Moteur ADA'S prêt (%d morceau(x) retrouvé(s))", added)
    return dict(op_info({}), ok=True)


def op_info(args):
    info = {"python": sys.version.split()[0]}
    try:
        from yt_dlp.version import __version__
        info["ytdlp"] = __version__
    except Exception as error:
        info["ytdlp"] = None
        info["ytdlp_error"] = str(error)
    try:
        from player.engine import jsc
        info["javascript"] = bool(jsc.available())
    except Exception:
        info["javascript"] = False
    return info


def op_shutdown(args):
    if S.manager is not None:
        S.manager.shutdown(save=True)
    return {"ok": True}


def op_language(args):
    from player.core import i18n
    i18n.init(args.get("language") or "fr")
    return {"ok": True}


# ----------------------------------------------------------------------
# File des imports
# ----------------------------------------------------------------------

def _job(job):
    keep = ("id", "url", "title", "platform", "thumbnail", "status", "label", "fraction", "speed", "eta",
            "message", "order")
    data = {key: job.get(key) for key in keep}
    data["active"] = job.get("status") in ("queued", "starting", "downloading", "processing", "waiting",
                                           "retrying")
    return data


def op_poll(args):
    events = []
    for kind, ident, payload in S.manager.poll_events():
        if kind == "finished":
            for item in (payload or {}).get("items") or []:
                events.append({"kind": "finished", "id": ident, "title": item.get("title") or ""})
        elif kind == "job" and payload and payload.get("status") == "failed":
            events.append({"kind": "failed", "id": ident, "title": payload.get("title") or "",
                           "message": payload.get("message") or ""})
        elif kind == "network":
            events.append({"kind": "network", "online": bool((payload or {}).get("online"))})
    jobs = [_job(job) for job in S.manager.snapshot()]
    jobs.sort(key=lambda j: (not j["active"] and j["status"] != "paused", -(j.get("order") or 0)))
    return {"jobs": jobs, "events": events, "rev": S.rev, "active": S.manager.has_active()}


def op_import(args):
    from player.core import formats
    items = []
    for item in args.get("items") or []:
        url = (item.get("url") or "").strip()
        if url:
            items.append({"url": url, "media_type": "audio", "quality": formats.BEST, "title": item.get("title")})
    ids = S.manager.add_many(items[:50]) if items else []
    return {"ids": ids, "count": len(ids)}


def op_import_text(args):
    from player.core import platforms
    urls = platforms.extract_urls(args.get("text") or "")
    return op_import({"items": [{"url": url} for url in urls[:25]]})


def op_job(args):
    action = args.get("action")
    job_id = args.get("id")
    manager = S.manager
    if action == "cancel":
        manager.cancel(job_id)
    elif action == "pause":
        manager.pause(job_id)
    elif action == "resume":
        manager.resume(job_id)
    elif action == "retry":
        manager.retry(job_id, force=bool(args.get("force")))
    elif action == "clear_finished":
        manager.clear_finished()
    elif action == "pause_all":
        manager.pause_all()
    elif action == "resume_all":
        manager.resume_all()
    return {"ok": True}


# ----------------------------------------------------------------------
# Recherche et tendances (appelées hors du fil de l'interface)
# ----------------------------------------------------------------------

def op_search(args):
    from player.engine import downloader
    return {"results": downloader.search(args.get("query") or "", int(args.get("limit") or SEARCH_LIMIT))}


def op_trending(args):
    from player.engine import downloader
    return {"results": downloader.trending(int(args.get("limit") or 25))}


# ----------------------------------------------------------------------
# Bibliothèque
# ----------------------------------------------------------------------

def _uids():
    return S.playlists.ensure_favorite()["uid"], S.playlists.ensure_vault()["uid"]


def op_library(args):
    favorite, vault = S.playlists.ensure_favorite(), S.playlists.ensure_vault()
    hidden = {item.get("filepath") for item in vault["items"]}
    entries = []
    for entry in S.history.entries():
        path = entry.get("filepath") or ""
        if path in hidden:
            continue
        entry["exists"] = bool(path) and os.path.isfile(path)
        entries.append(entry)
    return {
        "rev": S.rev,
        "entries": entries,
        "favorites": [item.get("filepath") for item in favorite["items"]],
        "favorite_items": favorite["items"],
        "vault": vault["items"],
        "playlists": S.playlists.user_entries(),
    }


def _missing(path):
    return not path or not os.path.exists(path)


def op_toggle_favorite(args):
    entry = args.get("entry") or {}
    path = entry.get("filepath") or ""
    if _missing(path):
        return {"error": "missing"}
    favorite_uid, _vault = _uids()
    if S.playlists.has_item(favorite_uid, path):
        S.playlists.remove_item(favorite_uid, path)
        return {"favorite": False}
    S.playlists.add_item(favorite_uid, path, title=entry.get("title"), thumbnail=entry.get("thumbnail"),
                         duration=entry.get("duration"))
    return {"favorite": True}


def op_playlist_create(args):
    name = (args.get("name") or "").strip() or "Ma playlist"
    entry = S.playlists.create(name)
    return {"uid": entry["uid"], "name": entry["name"]}


def op_playlist_delete(args):
    return {"ok": S.playlists.delete(args.get("uid"))}


def op_playlist_rename(args):
    uid = args.get("uid")
    name = (args.get("name") or "").strip()
    if not name:
        return {"ok": False}
    with S.playlists._lock:
        for entry in S.playlists._load():
            if entry["uid"] == uid:
                entry["name"] = name
                S.playlists._save()
                return {"ok": True}
    return {"ok": False}


def op_playlist_add(args):
    entry = args.get("entry") or {}
    path = entry.get("filepath") or ""
    if _missing(path):
        return {"error": "missing"}
    uid = args.get("uid")
    target = S.playlists.get(uid) if uid else None
    if target is None:
        return {"error": "playlist"}
    added = S.playlists.add_item(uid, path, title=entry.get("title"), thumbnail=entry.get("thumbnail"),
                                 duration=entry.get("duration"))
    return {"added": bool(added), "name": target["name"]}


def op_playlist_remove(args):
    return {"ok": S.playlists.remove_item(args.get("uid"), args.get("filepath"))}


def op_delete(args):
    """Retire le morceau de la bibliothèque et supprime le fichier."""
    entry = args.get("entry") or {}
    path = entry.get("filepath") or ""
    for playlist in S.playlists.entries():
        S.playlists.remove_item(playlist["uid"], path)
    if entry.get("uid"):
        S.history.remove(entry.get("uid"))
    else:
        for item in S.history.entries():
            if item.get("filepath") == path:
                S.history.remove(item.get("uid"))
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            log.warning("Fichier non supprimé : %s", path)
            return {"ok": False}
    return {"ok": True}


def _unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


def op_vault_add(args):
    from player.core import config
    entry = args.get("entry") or {}
    path = entry.get("filepath") or ""
    if _missing(path):
        return {"error": "missing"}
    _favorite, vault_uid = _uids()
    if S.playlists.has_item(vault_uid, path):
        return {"error": "already"}
    vault_dir = os.path.join(config.DOCUMENTS_DIR, VAULT_FOLDER)
    target = _unique_path(os.path.join(vault_dir, os.path.basename(path)))
    try:
        os.makedirs(vault_dir, exist_ok=True)
        os.replace(path, target)
    except OSError:
        log.exception("Rangement dans le Coffre impossible")
        return {"error": "io"}
    for playlist in S.playlists.entries():
        if not playlist.get("vault"):
            S.playlists.remove_item(playlist["uid"], path)
    S.history.update_path(path, target)
    origin = os.path.relpath(path, config.DOCUMENTS_DIR)
    S.playlists.add_item(vault_uid, target, title=entry.get("title"), origin=origin,
                         thumbnail=entry.get("thumbnail"), duration=entry.get("duration"))
    return {"ok": True, "filepath": target}


def op_vault_remove(args):
    from player.core import config
    item = args.get("item") or {}
    path = item.get("filepath") or ""
    origin = item.get("origin")
    _favorite, vault_uid = _uids()
    if path and os.path.exists(path):
        back = os.path.join(config.DOCUMENTS_DIR, origin) if origin else \
            os.path.join(config.DOCUMENTS_DIR, os.path.basename(path))
        back = _unique_path(back)
        try:
            os.makedirs(os.path.dirname(back), exist_ok=True)
            os.replace(path, back)
        except OSError:
            log.exception("Sortie du Coffre impossible")
            return {"error": "io"}
        S.history.update_path(path, back)
    S.playlists.remove_item(vault_uid, path)
    return {"ok": True}


def op_filesize(args):
    return {"ok": S.history.update_filesize(args.get("filepath"), int(args.get("size") or 0))}


def op_clear_history(args):
    S.history.clear()
    return {"ok": True}


# ----------------------------------------------------------------------
# Réglages du moteur, sauvegarde, migration
# ----------------------------------------------------------------------

ENGINE_KEYS = ("max_parallel", "skip_duplicates", "clean_titles", "auto_retry")


def op_settings(args):
    values = {k: v for k, v in (args.get("values") or {}).items() if k in ENGINE_KEYS}
    if values:
        S.settings.update(**values)
    return {k: S.settings.get(k) for k in ENGINE_KEYS}


def op_backup(args):
    from player.core import config
    path = os.path.join(config.DOCUMENTS_DIR, BACKUP_NAME)
    data = {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "settings": S.settings.as_dict(),
        "history": config.read_json(config.HISTORY_FILE, []),
        "playlists": config.read_json(config.PLAYLISTS_FILE, []),
    }
    try:
        config.write_json_atomic(path, data)
    except OSError:
        log.exception("Sauvegarde impossible")
        return {"ok": False}
    return {"ok": True, "path": path}


def op_restore(args):
    from player.core import config
    path = os.path.join(config.DOCUMENTS_DIR, BACKUP_NAME)
    data = config.read_json(path, None)
    if not isinstance(data, dict):
        return {"ok": False}
    try:
        if isinstance(data.get("settings"), dict):
            config.write_json_atomic(config.SETTINGS_FILE, config.validate_settings(data["settings"]))
        if isinstance(data.get("history"), list):
            config.write_json_atomic(config.HISTORY_FILE, data["history"])
        if isinstance(data.get("playlists"), list):
            config.write_json_atomic(config.PLAYLISTS_FILE, data["playlists"])
    except OSError:
        log.exception("Restauration impossible")
        return {"ok": False}
    S.settings.reload()
    S.history.reload()
    S.playlists.reload()
    _uids()
    _bump()
    return {"ok": True}


def _rescan():
    """Ajoute à la bibliothèque les morceaux déjà présents dans Documents
    (ex. importés par l'ancienne version d'ADA'S) mais absents de l'historique."""
    from player.core import config
    known = {e.get("filepath") for e in S.history.entries()}
    known |= S.playlists.vault_paths()
    found = []
    for folder, dirs, files in os.walk(config.DOCUMENTS_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO_EXT:
                continue
            path = os.path.join(folder, name)
            if path in known or ".compression." in name or name.endswith(".part"):
                continue
            try:
                stat = os.stat(path)
            except OSError:
                continue
            found.append((stat.st_mtime, path, stat.st_size))
    found.sort()
    for mtime, path, size in found:
        S.history.add({"title": os.path.splitext(os.path.basename(path))[0], "filepath": path,
                       "filesize": size, "media_type": "audio",
                       "date": time.strftime("%d/%m/%Y %H:%M", time.localtime(mtime))})
    return len(found)


def op_rescan(args):
    return {"added": _rescan()}


# ----------------------------------------------------------------------

OPS = {name[3:]: func for name, func in globals().items() if name.startswith("op_")}


def dispatch(name, payload="{}"):
    """Point d'entrée unique appelé depuis Swift. Renvoie toujours du JSON."""
    try:
        args = json.loads(payload or "{}")
        func = OPS.get(name)
        if func is None:
            return json.dumps({"error": f"opération inconnue : {name}"})
        if name not in ("init", "info") and S.manager is None:
            return json.dumps({"error": "moteur non démarré"})
        result = func(args if isinstance(args, dict) else {})
        return json.dumps(result if result is not None else {}, ensure_ascii=False, default=str)
    except Exception as error:  # noqa: BLE001 — jamais d'exception vers Swift
        log.exception("Erreur dans %s", name)
        return json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False)
