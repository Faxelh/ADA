"""
Historique des téléchargements.

- Écriture atomique (jamais de fichier tronqué, donc jamais d'historique
  perdu après un plantage).
- Accès protégé par un verrou : plusieurs téléchargements peuvent se
  terminer en même temps.
- Chaque entrée a un identifiant unique (uid) : on supprime une entrée
  précise, pas « la 3e de la liste » (qui pouvait viser la mauvaise
  ligne si l'historique avait bougé entre l'affichage et le clic).
- Un doublon n'est reconnu que si le fichier existe encore : supprimer
  un fichier permet de le retélécharger.
"""

import logging
import os
import threading
import time
import uuid

from player.core import config, platforms

log = logging.getLogger("player.history")

MAX_ENTRIES = 1000


def _normalize_entry(entry):
    if not isinstance(entry, dict):
        return None
    entry = dict(entry)
    entry.setdefault("uid", uuid.uuid4().hex)
    entry["title"] = str(entry.get("title") or "Sans titre")
    entry["platform"] = str(entry.get("platform") or "")
    entry["date"] = str(entry.get("date") or "")
    # Sur iPhone, le chemin du dossier de l'app change à chaque mise à jour :
    # on garde le chemin relatif au dossier Documents et on recalcule l'absolu.
    documents = config.DOCUMENTS_DIR
    relpath = entry.get("relpath")
    if documents and relpath:
        entry["filepath"] = os.path.join(documents, relpath)
    elif documents and entry.get("filepath"):
        try:
            rel = os.path.relpath(entry["filepath"], documents)
        except ValueError:
            rel = None
        if rel and not rel.startswith(".."):
            entry["relpath"] = rel
    return entry


class History:
    EXISTS_TTL = 30  # secondes pendant lesquelles on se fie au résultat d'un os.path.exists

    def __init__(self, path=None):
        self.path = path or config.HISTORY_FILE
        self._lock = threading.RLock()
        self._entries = None
        self._listeners = []
        self._index = None
        self._exists = {}

    def _file_exists(self, path):
        """os.path.exists avec un petit cache : des centaines de liens vérifiés sans ralentir."""
        now = time.monotonic()
        cached = self._exists.get(path)
        if cached is not None and now - cached[0] < self.EXISTS_TTL:
            return cached[1]
        result = os.path.exists(path)
        self._exists[path] = (now, result)
        if len(self._exists) > 5000:
            self._exists.clear()
        return result

    def _build_index(self):
        """Index {("id", identifiant) | ("url", lien normalisé): [entrées]} pour des recherches instantanées."""
        if self._index is None:
            index = {}
            for entry in self._load():
                if entry.get("video_id"):
                    index.setdefault(("id", str(entry["video_id"])), []).append(entry)
                if entry.get("url"):
                    index.setdefault(("url", platforms.normalize_url(entry["url"])), []).append(entry)
            self._index = index
        return self._index

    # -- interne -------------------------------------------------------
    def _load(self):
        if self._entries is None:
            raw = config.read_json(self.path, [])
            if not isinstance(raw, list):
                log.warning("Historique illisible, remplacé par une liste vide")
                raw = []
            entries = [_normalize_entry(item) for item in raw]
            self._entries = [item for item in entries if item]
        return self._entries

    def _save(self):
        self._index = None
        self._exists.clear()
        try:
            config.write_json_atomic(self.path, self._entries[:MAX_ENTRIES])
        except OSError as error:
            log.warning("Impossible d'enregistrer l'historique : %s", error)
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # un écouteur défaillant ne doit rien casser
                log.exception("Écouteur d'historique en erreur")

    # -- API -----------------------------------------------------------
    def add_listener(self, callback):
        self._listeners.append(callback)

    def reload(self):
        """Relit le fichier depuis le disque (ex. après une restauration de sauvegarde)."""
        with self._lock:
            self._entries = None
            self._index = None
            self._exists.clear()
            self._load()
            for callback in list(self._listeners):
                try:
                    callback()
                except Exception:
                    log.exception("Écouteur d'historique en erreur")

    def entries(self):
        with self._lock:
            return [dict(item) for item in self._load()]

    def add(self, entry):
        with self._lock:
            item = _normalize_entry(entry)
            self._load().insert(0, item)
            del self._entries[MAX_ENTRIES:]
            self._save()
            return dict(item)

    def update_path(self, old, new):
        """Le fichier a été déplacé (ex. rangé dans le Coffre) : on suit son
        nouveau chemin (et son chemin relatif à Documents)."""
        with self._lock:
            changed = False
            for entry in self._load():
                if entry.get("filepath") == old:
                    entry["filepath"] = new
                    entry.pop("relpath", None)
                    entry.update(_normalize_entry(entry))
                    changed = True
            if changed:
                self._save()
            return changed

    def update_filesize(self, filepath, size):
        """Met à jour la taille affichée d'un fichier (ex. après compression)."""
        with self._lock:
            changed = False
            for entry in self._load():
                if entry.get("filepath") == filepath:
                    entry["filesize"] = int(size)
                    changed = True
            if changed:
                self._save()
            return changed

    def remove(self, uid):
        with self._lock:
            entries = self._load()
            before = len(entries)
            self._entries = [item for item in entries if item.get("uid") != uid]
            if len(self._entries) != before:
                self._save()
                return True
            return False

    def clear(self):
        with self._lock:
            self._entries = []
            self._save()

    def find_duplicate(self, media_type, quality=None, url=None, video_id=None):
        """Entrée déjà téléchargée correspondant à ce lien / cet identifiant, ou None."""
        norm = platforms.normalize_url(url) if url else ""
        video_id = video_id or (platforms.video_id_from_url(url) if url else None)

        with self._lock:
            index = self._build_index()
            candidates = []
            if video_id:
                candidates += index.get(("id", str(video_id)), [])
            if norm:
                candidates += index.get(("url", norm), [])
            for entry in candidates:
                if entry.get("media_type") and entry.get("media_type") != media_type:
                    continue
                stored_quality = entry.get("quality")
                if quality and stored_quality and stored_quality != quality:
                    continue
                filepath = entry.get("filepath")
                if filepath and not self._file_exists(filepath):
                    continue  # fichier supprimé depuis : on peut retélécharger
                return dict(entry)

        return None

    def known_ids(self, media_type, quality=None):
        """Identifiants déjà téléchargés (fichier toujours présent) pour ce type/qualité."""
        ids = set()
        with self._lock:
            for entry in self._load():
                if entry.get("media_type") and entry.get("media_type") != media_type:
                    continue
                stored_quality = entry.get("quality")
                if quality and stored_quality and stored_quality != quality:
                    continue
                filepath = entry.get("filepath")
                if filepath and not self._file_exists(filepath):
                    continue
                if entry.get("video_id"):
                    ids.add(str(entry["video_id"]))
        return ids

    def paths_by_id(self):
        """{chemin de fichier en minuscules: video_id} — pour éviter d'écraser la vidéo d'un autre."""
        mapping = {}
        with self._lock:
            for entry in self._load():
                if entry.get("filepath") and entry.get("video_id"):
                    mapping[os.path.normcase(os.path.abspath(entry["filepath"]))] = str(entry["video_id"])
        return mapping


_history = None
_history_lock = threading.Lock()


def get_history():
    global _history
    with _history_lock:
        if _history is None:
            if config.HISTORY_FILE is None:
                config.init_default()
            _history = History()
        return _history


# ----------------------------------------------------------------------
# Playlists : regrouper des téléchargements pour les lire à la suite.
# ----------------------------------------------------------------------

def _normalize_playlist_item(item):
    if not isinstance(item, dict):
        return None
    item = dict(item)
    documents = config.DOCUMENTS_DIR
    relpath = item.get("relpath")
    if documents and relpath:
        item["filepath"] = os.path.join(documents, relpath)
    elif documents and item.get("filepath"):
        try:
            rel = os.path.relpath(item["filepath"], documents)
        except ValueError:
            rel = None
        if rel and not rel.startswith(".."):
            item["relpath"] = rel
    if not item.get("filepath"):
        return None
    item["title"] = str(item.get("title") or os.path.basename(item["filepath"]))
    return item


def _normalize_playlist(entry):
    if not isinstance(entry, dict):
        return None
    entry = dict(entry)
    entry.setdefault("uid", uuid.uuid4().hex)
    entry["name"] = str(entry.get("name") or "Playlist")
    entry["created"] = str(entry.get("created") or "")
    entry["favorite"] = bool(entry.get("favorite"))
    entry["vault"] = bool(entry.get("vault"))
    items = [_normalize_playlist_item(item) for item in entry.get("items") or []]
    entry["items"] = [item for item in items if item]
    return entry


class Playlists:
    """Playlists de l'utilisateur (regroupements de fichiers déjà téléchargés)."""

    def __init__(self, path=None):
        self.path = path or config.PLAYLISTS_FILE
        self._lock = threading.RLock()
        self._entries = None
        self._listeners = []

    def add_listener(self, callback):
        self._listeners.append(callback)

    def reload(self):
        """Relit le fichier depuis le disque (ex. après une restauration de sauvegarde)."""
        with self._lock:
            self._entries = None
            self._load()
            for callback in list(self._listeners):
                try:
                    callback()
                except Exception:
                    log.exception("Écouteur de playlists en erreur")

    def _load(self):
        if self._entries is None:
            raw = config.read_json(self.path, [])
            if not isinstance(raw, list):
                log.warning("Playlists illisibles, remplacées par une liste vide")
                raw = []
            entries = [_normalize_playlist(item) for item in raw]
            self._entries = [item for item in entries if item]
        return self._entries

    def _save(self):
        try:
            config.write_json_atomic(self.path, self._entries)
        except OSError as error:
            log.warning("Playlists non enregistrées : %s", error)
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                log.exception("Écouteur de playlists en erreur")

    def entries(self):
        with self._lock:
            return [dict(entry, items=list(entry["items"])) for entry in self._load()]

    def get(self, uid):
        with self._lock:
            for entry in self._load():
                if entry["uid"] == uid:
                    return dict(entry, items=list(entry["items"]))
            return None

    def create(self, name, favorite=False, vault=False):
        with self._lock:
            entry = _normalize_playlist({"name": name, "created": time.strftime("%d/%m/%Y %H:%M"),
                                         "items": [], "favorite": favorite, "vault": vault})
            self._load().insert(0, entry)
            self._save()
            return dict(entry)

    def get_vault(self):
        """Le Coffre : liste spéciale protégée par Face ID. Les morceaux qui y
        sont rangés disparaissent de la bibliothèque et de l'historique."""
        with self._lock:
            for entry in self._load():
                if entry.get("vault"):
                    return dict(entry, items=list(entry["items"]))
            return None

    def ensure_vault(self):
        existing = self.get_vault()
        if existing is not None:
            return existing
        return self.create("Coffre", vault=True)

    def vault_paths(self):
        vault = self.get_vault()
        return {item.get("filepath") for item in vault["items"]} if vault else set()

    def get_favorite(self):
        """La playlist spéciale « Favoris » (toujours la même, jamais listée
        parmi les playlists de l'utilisateur, jamais supprimable depuis l'interface)."""
        with self._lock:
            for entry in self._load():
                if entry.get("favorite"):
                    return dict(entry, items=list(entry["items"]))
            return None

    def ensure_favorite(self):
        existing = self.get_favorite()
        if existing is not None:
            return existing
        return self.create("Favoris", favorite=True)

    def has_item(self, uid, filepath):
        with self._lock:
            for entry in self._load():
                if entry["uid"] == uid:
                    return any(item.get("filepath") == filepath for item in entry["items"])
            return False

    def user_entries(self):
        """Playlists créées par l'utilisateur (sans Favoris ni Coffre)."""
        return [entry for entry in self.entries() if not entry.get("favorite") and not entry.get("vault")]

    def delete(self, uid):
        with self._lock:
            entries = self._load()
            before = len(entries)
            self._entries = [entry for entry in entries if entry["uid"] != uid]
            if len(self._entries) != before:
                self._save()
                return True
            return False

    def add_item(self, uid, filepath, title=None, **extra):
        with self._lock:
            for entry in self._load():
                if entry["uid"] != uid:
                    continue
                if any(item.get("filepath") == filepath for item in entry["items"]):
                    return False
                item = _normalize_playlist_item(dict(extra, filepath=filepath, title=title))
                if item is None:
                    return False
                entry["items"].append(item)
                self._save()
                return True
            return False

    def remove_item(self, uid, filepath):
        with self._lock:
            for entry in self._load():
                if entry["uid"] != uid:
                    continue
                before = len(entry["items"])
                entry["items"] = [item for item in entry["items"] if item.get("filepath") != filepath]
                if len(entry["items"]) != before:
                    self._save()
                    return True
            return False


_playlists = None
_playlists_lock = threading.Lock()


def get_playlists():
    global _playlists
    with _playlists_lock:
        if _playlists is None:
            if config.PLAYLISTS_FILE is None:
                config.init_default()
            _playlists = Playlists()
        return _playlists
