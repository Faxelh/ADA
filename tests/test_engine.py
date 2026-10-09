"""
Moteur (téléchargement, recherche, tendances, playlists) avec le faux
yt-dlp (PC uniquement : sur iPhone le vrai yt-dlp est présent et ces
tests sont sautés).
"""

import os
import time
import unittest
from unittest import mock

from tests import support

if not support.REAL_YTDLP:
    from player.engine import downloader, manager


def wait(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@unittest.skipIf(support.REAL_YTDLP, "tests réservés au faux yt-dlp")
class DownloaderTest(unittest.TestCase):
    def setUp(self):
        self.cfg = support.TempConfig().__enter__()
        from player.core import storage
        self.history = storage.get_history()
        self.events = []

    def tearDown(self):
        self.cfg.__exit__(None, None, None)

    def run_job(self, url, media="audio", quality=None, stop=lambda: False):
        job = {"id": "j" + str(len(self.events)), "url": url, "media_type": media, "quality": quality}
        return downloader.download(job, lambda kind, data: self.events.append((kind, data)), stop, self.history)

    def test_audio_download(self):
        result = self.run_job("https://test.invalid/audio/abc/Ma Chanson")
        item = result["items"][0]
        self.assertTrue(item["filepath"].endswith("Ma Chanson.m4a"))
        self.assertTrue(os.path.exists(item["filepath"]))
        fractions = [d["fraction"] for k, d in self.events if k == "progress"]
        self.assertEqual(fractions[-1], 1.0)

    def test_search(self):
        results = downloader.search("une chanson", limit=5)
        self.assertEqual(len(results), 5)
        self.assertIn("une chanson", results[0]["title"])
        self.assertTrue(results[0]["url"].startswith("https://test.invalid/"))

    def test_search_empty_query(self):
        self.assertEqual(downloader.search("   "), [])

    def test_trending(self):
        results = downloader.trending(10)
        self.assertTrue(results)
        self.assertTrue(all(r["url"] for r in results))


@unittest.skipIf(support.REAL_YTDLP, "tests réservés au faux yt-dlp")
class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = support.TempConfig().__enter__()
        from player.core import config, storage
        self.history = storage.get_history()
        self.manager = manager.DownloadManager(config.get_settings(), self.history)

    def tearDown(self):
        self.manager.shutdown(save=False)
        self.cfg.__exit__(None, None, None)

    def test_add_many_writes_queue_once(self):
        from player.core import config
        items = [{"url": f"https://test.invalid/slow/m{i}", "media_type": "audio"} for i in range(20)]
        self.manager.pause_all()
        with mock.patch.object(config, "write_json_atomic") as write:
            ids = self.manager.add_many(items)
            self.assertEqual(write.call_count, 1)
        self.assertEqual(len(ids), 20)

    def test_single_job_completes(self):
        job_id = self.manager.add("https://test.invalid/audio/one/Titre Un", "audio")
        self.assertTrue(wait(lambda: self.manager.job(job_id)["status"] == "done"))
        entries = self.history.entries()
        self.assertEqual(entries[0]["title"], "Titre Un")

    def test_duplicate_add_while_in_progress_reuses_same_job(self):
        # Toucher deux fois de suite le même bouton d'import (double appui,
        # ou import lancé depuis deux onglets) ne doit jamais relancer un
        # deuxième téléchargement du même lien : le contrôle sur
        # l'historique ne voit que les téléchargements déjà *terminés*, pas
        # ceux encore en file, d'où ce deuxième filtre dans add_many().
        self.manager.pause_all()
        first = self.manager.add("https://test.invalid/slow/dup1", "audio")
        second = self.manager.add("https://test.invalid/slow/dup1", "audio")
        self.assertEqual(first, second)
        self.assertEqual(len(self.manager.snapshot()), 1)

    def test_duplicate_add_many_within_same_batch(self):
        items = [{"url": "https://test.invalid/slow/dup2", "media_type": "audio"}] * 3
        self.manager.pause_all()
        ids = self.manager.add_many(items)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.manager.snapshot()), 1)

    def test_duplicate_add_with_force_is_allowed(self):
        self.manager.pause_all()
        first = self.manager.add("https://test.invalid/slow/dup3", "audio", force=True)
        second = self.manager.add("https://test.invalid/slow/dup3", "audio", force=True)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.manager.snapshot()), 2)


@unittest.skipIf(support.REAL_YTDLP, "tests réservés au faux yt-dlp")
class PlaylistsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = support.TempConfig().__enter__()
        from player.core import storage
        self.playlists = storage.get_playlists()

    def tearDown(self):
        self.cfg.__exit__(None, None, None)

    def test_create_add_remove_delete(self):
        entry = self.playlists.create("Mix")
        self.assertTrue(self.playlists.add_item(entry["uid"], "/tmp/a.m4a", title="A"))
        self.assertFalse(self.playlists.add_item(entry["uid"], "/tmp/a.m4a", title="A"))  # pas de doublon
        self.assertEqual(len(self.playlists.get(entry["uid"])["items"]), 1)
        self.assertTrue(self.playlists.remove_item(entry["uid"], "/tmp/a.m4a"))
        self.assertEqual(len(self.playlists.get(entry["uid"])["items"]), 0)
        self.assertTrue(self.playlists.delete(entry["uid"]))
        self.assertIsNone(self.playlists.get(entry["uid"]))

    def test_favorite_is_singleton_and_hidden_from_user_entries(self):
        first = self.playlists.ensure_favorite()
        second = self.playlists.ensure_favorite()
        self.assertEqual(first["uid"], second["uid"])
        self.playlists.create("Playlist normale")
        user_entries = self.playlists.user_entries()
        self.assertEqual(len(user_entries), 1)
        self.assertEqual(user_entries[0]["name"], "Playlist normale")

    def test_vault_is_singleton_hidden_and_tracks_paths(self):
        vault = self.playlists.ensure_vault()
        self.assertEqual(vault["uid"], self.playlists.ensure_vault()["uid"])
        self.playlists.ensure_favorite()
        self.playlists.create("Normale")
        self.assertEqual([p["name"] for p in self.playlists.user_entries()], ["Normale"])
        self.playlists.add_item(vault["uid"], "/tmp/secret.m4a", title="Secret")
        self.assertEqual(self.playlists.vault_paths(), {"/tmp/secret.m4a"})

    def test_has_item(self):
        entry = self.playlists.create("Mix")
        self.assertFalse(self.playlists.has_item(entry["uid"], "/tmp/x.m4a"))
        self.playlists.add_item(entry["uid"], "/tmp/x.m4a", title="X")
        self.assertTrue(self.playlists.has_item(entry["uid"], "/tmp/x.m4a"))

    def test_reload_picks_up_disk_changes(self):
        from player.core import config
        entry = self.playlists.create("Avant")
        config.write_json_atomic(config.PLAYLISTS_FILE, [{"uid": "x", "name": "Après", "items": []}])
        self.playlists.reload()
        names = [p["name"] for p in self.playlists.entries()]
        self.assertEqual(names, ["Après"])


if __name__ == "__main__":
    unittest.main()
