"""
File d'attente des téléchargements (fils de travail, sans processus :
iOS n'autorise pas à lancer d'autres programmes).

- 1 à 3 téléchargements simultanés ;
- pause / reprise (le fichier partiel est repris là où il s'était arrêté),
  annulation, nouvelle tentative ;
- relances automatiques des erreurs passagères, attente du réseau ;
- la file est sauvegardée : les téléchargements interrompus (fermeture de
  l'app, iOS qui la suspend) sont proposés à la reprise au lancement.

L'interface lit l'état avec snapshot() et reçoit les événements avec
poll_events() depuis son fil principal.
"""

import collections
import itertools
import logging
import os
import shutil
import socket
import threading
import time
import uuid

from player.core import config, errors, platforms
from player.engine import downloader

log = logging.getLogger("player.manager")

ACTIVE = ("queued", "starting", "downloading", "processing", "waiting", "retrying")
FINISHED = ("done", "failed", "cancelled", "skipped")

STATUS_LABELS = {
    "queued": "En attente",
    "starting": "Démarrage…",
    "downloading": "Téléchargement…",
    "processing": "Assemblage…",
    "waiting": "En attente du réseau",
    "retrying": "Nouvelle tentative…",
    "paused": "En pause",
    "done": "Terminé",
    "failed": "Échec",
    "cancelled": "Annulé",
    "skipped": "Déjà téléchargé",
}

MAX_ATTEMPTS = {"network": 5, "timeout": 5, "server": 3, "rate_limit": 3, "parse": 2, "stalled": 2,
                "unknown": 2, "forbidden": 2}
BACKOFF = [3, 8, 20, 45, 90]


def is_online(timeout=3.0):
    for host in (("1.1.1.1", 443), ("8.8.8.8", 443), ("www.apple.com", 443)):
        try:
            with socket.create_connection(host, timeout=timeout):
                return True
        except OSError:
            continue
    return False


class Job:
    _counter = itertools.count(1)

    def __init__(self, url, media_type="video", quality=None, title=None, platform=None, force=False):
        self.id = uuid.uuid4().hex[:12]
        self.order = next(Job._counter)
        self.url = url
        self.media_type = media_type
        self.quality = quality
        self.title = title or url
        self.platform = platform or platforms.detect_platform(url)
        self.thumbnail = None
        self.force = force
        self.stem = None
        self.status = "queued"
        self.label = STATUS_LABELS["queued"]
        self.fraction = 0.0
        self.speed = None
        self.eta = None
        self.message = ""
        self.category = None
        self.attempts = 0
        self.result = None
        self.created = time.time()
        self.finished_at = None
        # interne
        self.stop_event = threading.Event()
        self.stop_reason = None  # "pause" | "cancel"
        self.not_before = 0.0

    def to_dict(self):
        return {
            "id": self.id, "url": self.url, "media_type": self.media_type, "quality": self.quality,
            "title": self.title, "platform": self.platform, "thumbnail": self.thumbnail,
            "status": self.status, "label": self.label, "fraction": self.fraction,
            "speed": self.speed, "eta": self.eta, "message": self.message, "category": self.category,
            "result": self.result, "order": self.order, "force": self.force,
        }

    def task(self):
        return {"id": self.id, "url": self.url, "media_type": self.media_type,
                "quality": self.quality, "stem": self.stem, "force": self.force}


class DownloadManager:
    def __init__(self, settings=None, history=None, on_success=None):
        self.settings = settings or config.get_settings()
        self.history = history
        self.on_success = on_success  # appelée dans le fil de travail après un succès
        self._lock = threading.RLock()
        self._jobs = collections.OrderedDict()
        self._events = collections.deque()
        self._wake = threading.Condition(self._lock)
        self._running = {}
        self._online = True
        self._closing = False
        self._scheduler = threading.Thread(target=self._loop, name="player-file", daemon=True)
        self._scheduler.start()

    # ------------------------------------------------------------------
    # Événements vers l'interface
    # ------------------------------------------------------------------

    def _emit(self, kind, ident=None, payload=None):
        self._events.append((kind, ident, payload))

    def poll_events(self):
        events = []
        while self._events:
            try:
                events.append(self._events.popleft())
            except IndexError:
                break
        return events

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def add(self, url, media_type="video", quality=None, title=None, platform=None, force=False):
        ids = self.add_many([{"url": url, "media_type": media_type, "quality": quality,
                              "title": title, "platform": platform, "force": force}])
        return ids[0] if ids else None

    def add_many(self, items):
        """Ajoute plusieurs téléchargements d'un coup (une grande playlist, par
        exemple) en une seule écriture de la file sur le disque.

        Appeler add() en boucle réenregistrait tout le fichier de la file à
        chaque vidéo ajoutée, et ce fichier grossit à chaque fois : pour une
        playlist de centaines de vidéos, ça faisait autant d'écritures
        disque que de vidéos, chacune un peu plus longue que la précédente,
        et bloquait l'interface plusieurs secondes (l'indicateur de blocage
        d'ADA s'affichait). Ici, les tâches sont créées puis la file n'est
        enregistrée qu'une seule fois à la fin.

        Repère aussi les doublons déjà en file (ou en cours) : toucher deux
        fois de suite le même bouton d'import (double appui, ou import lancé
        depuis deux onglets) renvoie l'identifiant du job déjà en cours au
        lieu de retélécharger la même chose en double — le contrôle sur
        l'historique (voir downloader.download) ne voit lui que les
        téléchargements déjà *terminés*, pas ceux encore en file.
        """
        skip_duplicates = bool(self.settings.get("skip_duplicates"))
        jobs = []
        ids = []
        with self._lock:
            in_progress = {}
            if skip_duplicates:
                for existing in self._jobs.values():
                    if existing.status not in FINISHED:
                        key = (existing.media_type, platforms.normalize_url(existing.url))
                        in_progress[key] = existing.id
            for item in items:
                url = item.get("url")
                media_type = item.get("media_type", "video")
                force = bool(item.get("force", False))
                key = (media_type, platforms.normalize_url(url)) if (url and skip_duplicates and not force) else None
                existing_id = in_progress.get(key) if key is not None else None
                if existing_id is not None:
                    ids.append(existing_id)
                    continue
                job = Job(url, media_type, item.get("quality"), item.get("title"), item.get("platform"), force)
                jobs.append(job)
                ids.append(job.id)
                if key is not None:
                    in_progress[key] = job.id
            for job in jobs:
                self._jobs[job.id] = job
            self._save_queue()
            self._wake.notify_all()
        for job in jobs:
            self._emit("added", job.id, job.to_dict())
        return ids

    def pause(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ACTIVE:
                return False
            if job.id in self._running:
                job.stop_reason = "pause"
                job.stop_event.set()
            self._set(job, "paused")
            self._save_queue()
            return True

    def resume(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "paused" or job.id in self._running:
                return False
            job.stop_event = threading.Event()
            job.stop_reason = None
            job.not_before = 0.0
            self._set(job, "queued")
            self._save_queue()
            self._wake.notify_all()
            return True

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in FINISHED:
                return False
            if job.id in self._running:
                job.stop_reason = "cancel"
                job.stop_event.set()
            else:
                self._cleanup(job)
            self._set(job, "cancelled", message="Annulé.")
            self._save_queue()
            return True

    def retry(self, job_id, force=False):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ("failed", "cancelled", "skipped") or job.id in self._running:
                return False
            job.stop_event = threading.Event()
            job.stop_reason = None
            job.attempts = 0
            job.not_before = 0.0
            job.force = force or job.status == "skipped"
            job.fraction = 0.0
            self._set(job, "queued", message="")
            self._save_queue()
            self._wake.notify_all()
            return True

    def pause_all(self):
        with self._lock:
            ids = [job.id for job in self._jobs.values() if job.status in ACTIVE]
        for job_id in ids:
            self.pause(job_id)

    def resume_all(self):
        with self._lock:
            ids = [job.id for job in self._jobs.values() if job.status == "paused"]
        for job_id in ids:
            self.resume(job_id)

    def clear_finished(self):
        with self._lock:
            for job_id in [j.id for j in self._jobs.values() if j.status in FINISHED]:
                del self._jobs[job_id]
            self._save_queue()
        self._emit("cleared")

    def snapshot(self):
        with self._lock:
            return [job.to_dict() for job in self._jobs.values()]

    def job(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def active_count(self):
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status in ACTIVE)

    def has_active(self):
        return self.active_count() > 0

    def online(self):
        return self._online

    def shutdown(self, save=True):
        with self._lock:
            self._closing = True
            if save:
                self._save_queue()
            for job in self._jobs.values():
                if job.id in self._running:
                    job.stop_reason = "pause"
                    job.stop_event.set()
            self._wake.notify_all()

    # ------------------------------------------------------------------
    # File sauvegardée
    # ------------------------------------------------------------------

    def _save_queue(self):
        if not config.QUEUE_FILE:
            return
        pending = [
            {"url": job.url, "media_type": job.media_type, "quality": job.quality, "title": job.title,
             "platform": job.platform, "force": job.force}
            for job in self._jobs.values() if job.status in ACTIVE or job.status == "paused"
        ]
        try:
            config.write_json_atomic(config.QUEUE_FILE, pending)
        except OSError:
            log.warning("File d'attente non enregistrée")

    def saved_queue(self):
        if not config.QUEUE_FILE:
            return []
        data = config.read_json(config.QUEUE_FILE, [])
        if not isinstance(data, list):
            return []
        with self._lock:
            known = {platforms.normalize_url(job.url) for job in self._jobs.values()}
        return [item for item in data if isinstance(item, dict) and item.get("url")
                and platforms.normalize_url(item["url"]) not in known]

    def resume_saved_queue(self):
        items = self.saved_queue()
        if items:
            self.add_many(items)
        return len(items)

    def discard_saved_queue(self):
        with self._lock:
            self._save_queue()

    # ------------------------------------------------------------------
    # Ordonnanceur
    # ------------------------------------------------------------------

    def _set(self, job, status, message=None, label=None):
        job.status = status
        job.label = label or STATUS_LABELS.get(status, status)
        if message is not None:
            job.message = message
        if status in FINISHED:
            job.finished_at = time.time()
            job.speed = None
            job.eta = None
        self._emit("job", job.id, job.to_dict())

    def _loop(self):
        last_network_check = 0.0
        while True:
            with self._lock:
                if self._closing:
                    return
                now = time.monotonic()
                waiting = [j for j in self._jobs.values() if j.status == "waiting"]
                if waiting and now - last_network_check > 5:
                    last_network_check = now
                    check_network = True
                else:
                    check_network = False
            if check_network:
                online = is_online()
                with self._lock:
                    if online != self._online:
                        self._online = online
                        self._emit("network", None, {"online": online})
                    if online:
                        for job in self._jobs.values():
                            if job.status == "waiting":
                                job.not_before = 0.0
                                self._set(job, "queued")
            with self._lock:
                limit = max(1, int(self.settings.get("max_parallel") or 1))
                now = time.monotonic()
                ready = [j for j in self._jobs.values()
                         if j.status in ("queued", "retrying") and j.not_before <= now and j.id not in self._running]
                ready.sort(key=lambda j: j.order)
                while ready and len(self._running) < limit:
                    job = ready.pop(0)
                    self._start(job)
                self._wake.wait(timeout=1.0)

    def _start(self, job):
        job.stop_event = job.stop_event if not job.stop_event.is_set() else threading.Event()
        job.stop_reason = None
        job.attempts += 1
        self._set(job, "starting", message="")
        thread = threading.Thread(target=self._run, args=(job,), name=f"player-{job.id}", daemon=True)
        self._running[job.id] = thread
        thread.start()

    def _run(self, job):
        stop_event = job.stop_event

        def stop():
            return stop_event.is_set()

        def emit(kind, data):
            with self._lock:
                if stop_event.is_set():
                    return
                if kind == "phase":
                    status = {"starting": "starting", "downloading": "downloading",
                              "processing": "processing"}.get(data.get("phase"), "downloading")
                    self._set(job, status, label=data.get("label"))
                elif kind == "progress":
                    job.fraction = max(job.fraction if job.status == "downloading" else 0.0,
                                       float(data.get("fraction") or 0.0))
                    job.speed = data.get("speed")
                    job.eta = data.get("eta")
                    self._emit("progress", job.id, {"fraction": job.fraction, "speed": job.speed, "eta": job.eta})
                elif kind == "name":
                    job.title = data.get("title") or job.title
                    job.stem = data.get("stem") or job.stem
                    job.platform = data.get("platform") or job.platform
                    job.thumbnail = data.get("thumbnail") or job.thumbnail
                    self._emit("job", job.id, job.to_dict())

        result = None
        failure = None
        try:
            result = downloader.download(job.task(), emit, stop, self.history)
        except BaseException as error:  # noqa: BLE001 — tout doit être rapporté, rien ne doit tuer le fil
            failure = error
            if not isinstance(error, (downloader.Stop, Exception)):
                log.exception("Erreur grave pendant %s", job.url)

        online_hint = True
        if failure is not None and not stop_event.is_set():
            if downloader.explain(failure, job.platform)[1] in errors.NETWORK:
                online_hint = is_online()  # hors verrou : peut prendre quelques secondes

        success_items = []
        with self._lock:
            self._running.pop(job.id, None)
            reason = job.stop_reason
            if reason == "cancel":
                self._cleanup(job)
                if job.status != "cancelled":
                    self._set(job, "cancelled", message="Annulé.")
            elif reason == "pause" or (self._closing and failure is not None):
                if job.status != "paused":
                    self._set(job, "paused")
            elif failure is None and result is not None:
                job.result = result
                job.fraction = 1.0
                items = result.get("items") or []
                if items:
                    job.title = items[0].get("title") or job.title
                success_items = items
                # L'historique et la rétroaction (ex. ajout à Photos) doivent être
                # faits avant de marquer le job "done" : l'interface (et les tests)
                # observent ce statut pour savoir que tout est terminé, et ne
                # doivent jamais le voir avant que l'historique soit à jour.
                for item in success_items:
                    if self.history is not None:
                        try:
                            entry = dict(item)
                            entry["date"] = time.strftime("%d/%m/%Y %H:%M")
                            entry.pop("files", None)
                            entry.pop("photos", None)
                            self.history.add(entry)
                        except Exception:
                            log.exception("Historique non mis à jour")
                    if self.on_success is not None:
                        try:
                            self.on_success(job.id, item)
                        except Exception:
                            log.exception("Action après téléchargement en erreur")
                self._set(job, "done", message=self._summary(items))
            else:
                self._handle_failure(job, failure, online_hint)
            self._save_queue()
            self._wake.notify_all()
        if success_items:
            self._emit("finished", job.id, {"items": success_items})

    @staticmethod
    def _summary(items):
        if not items:
            return ""
        item = items[0]
        parts = [item.get("label") or ""]
        size = item.get("filesize")
        if size:
            from player.core import formats
            parts.append(formats.size_text(size))
        return " • ".join(p for p in parts if p)

    def _handle_failure(self, job, error, online=True):
        message, category = downloader.explain(error, job.platform)
        job.category = category
        log.info("Échec %s (%s) : %s", job.url, category, error)
        if category == "duplicate":
            self._set(job, "skipped", message=message)
            return
        if category == "cancelled":
            self._set(job, "cancelled", message=message)
            return
        retryable = category in errors.RETRYABLE or category in ("forbidden", "unknown")
        if retryable and self.settings.get("auto_retry") and job.attempts < MAX_ATTEMPTS.get(category, 2):
            if category in errors.NETWORK and not online:
                self._online = False
                self._emit("network", None, {"online": False})
                job.not_before = time.monotonic() + 5
                self._set(job, "waiting", message=message)
                return
            delay = BACKOFF[min(job.attempts - 1, len(BACKOFF) - 1)]
            job.not_before = time.monotonic() + delay
            self._set(job, "retrying", message=f"{message} Nouvelle tentative dans {delay} s.",
                      label=f"Nouvelle tentative ({job.attempts + 1})…")
            return
        self._set(job, "failed", message=message)

    def _cleanup(self, job):
        if config.WORK_DIR:
            shutil.rmtree(os.path.join(config.WORK_DIR, job.id), ignore_errors=True)
