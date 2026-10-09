"""Préparation commune des tests (PC et iPhone)."""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
SRC = os.path.join(os.path.dirname(HERE), "src")

if os.path.isdir(SRC) and SRC not in sys.path:
    sys.path.insert(0, SRC)

try:
    import yt_dlp  # noqa: F401
    REAL_YTDLP = getattr(sys.modules["yt_dlp"], "__file__", "").find("fake_ytdlp") < 0
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "fake_ytdlp"))
    REAL_YTDLP = False

IS_IOS = sys.platform == "ios"


def fixture(name):
    return os.path.join(FIXTURES, name)


class TempConfig:
    """Dossiers temporaires pour config (Documents, data, cache)."""

    def __enter__(self):
        from player.core import config, storage
        self.root = tempfile.mkdtemp(prefix="player-test-")
        config.init(os.path.join(self.root, "Documents"), os.path.join(self.root, "data"),
                    os.path.join(self.root, "cache"))
        storage._history = None
        storage._playlists = None
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)
        return False
