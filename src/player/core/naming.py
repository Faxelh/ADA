"""Titres lisibles et noms de fichiers uniques (jamais d'écrasement)."""

import glob
import os
import re

from player.core import formats

# ------------------------------------------------------------------
# Titres et noms de fichiers
# ------------------------------------------------------------------

_NUM = r"[\d][\d.,\s]*\s*[KkMmBb]?"
_FB_STATS = re.compile(
    r"^\s*" + _NUM + r"\s*(?:views?|vues?|lectures?|plays?)\s*"
    r"(?:[·•|,-]\s*" + _NUM + r"\s*(?:reactions?|réactions?|likes?|j’aime|j'aime|"
    r"comments?|commentaires?|shares?|partages?)\s*)*"
    r"(?:[|·•-]\s*)?",
    re.IGNORECASE,
)
_ON_REELS = re.compile(r"\s*(?:[|·•-]\s*)?(?:on|sur)\s+(?:Reels|Facebook|Instagram|TikTok)\s*$", re.IGNORECASE)
_SITE_SUFFIX = re.compile(r"\s*[|\-–·•]\s*(?:Facebook|Instagram|TikTok|YouTube|X)\s*$", re.IGNORECASE)
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_STEM = 80


def _first_line(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if len(line) >= 3:
            return line
    return ""


def clean_title(info, enabled=True):
    """
    Titre lisible pour l'historique et le nom du fichier.

    Facebook n'a pas de vrai titre : yt-dlp y met « 91K views · 2.3K
    reactions | Auteur on Reels » ou la légende entière. On retire les
    statistiques et les mentions « on Reels », et si ce qui reste n'est
    que le nom de l'auteur, on prend la première ligne de la description.
    """
    title = str(info.get("title") or "").replace("\r", " ").replace("\n", " ")
    uploader = str(info.get("uploader") or info.get("channel") or "").strip()

    if not enabled:
        return re.sub(r"\s{2,}", " ", title).strip() or uploader or str(info.get("id") or "Sans titre")

    cleaned = _FB_STATS.sub("", title)
    cleaned = _ON_REELS.sub("", cleaned)
    cleaned = _SITE_SUFFIX.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -|·•_.,")

    if not cleaned or (uploader and cleaned.lower() == uploader.lower()):
        description = _first_line(info.get("description"))
        if description:
            cleaned = description
            if uploader and uploader.lower() not in description.lower():
                cleaned = f"{description} - {uploader}"

    return cleaned or uploader or str(info.get("id") or "Sans titre")


def safe_stem(text, sanitize):
    """Nom de fichier valide sous Windows (sans extension), 80 caractères max."""
    stem = sanitize(text or "", restricted=False) if sanitize else (text or "")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    stem = re.sub(r"\s{2,}", " ", stem).strip()
    stem = stem[:MAX_STEM].rstrip(" .")
    if not stem:
        stem = "Sans titre"
    if stem.split(".")[0].lower() in _WINDOWS_RESERVED:
        stem = "_" + stem
    return stem


def stem_taken(folder, stem):
    """Un fichier (final ou partiel) porte-t-il déjà ce nom dans le dossier ?"""
    pattern = os.path.join(glob.escape(folder), glob.escape(stem) + ".*")
    return bool(glob.glob(pattern))


LOCK_SUFFIX = ".playerlock"


def _reserve(folder, stem):
    """
    Réserve un nom de fichier en créant « nom.playerlock » de façon atomique.
    Deux téléchargements simultanés dont le titre est identique (fréquent
    sur Facebook/TikTok) ne peuvent ainsi jamais choisir le même nom.
    """
    if stem_taken(folder, stem):
        return False
    try:
        with open(os.path.join(folder, stem + LOCK_SUFFIX), "x", encoding="utf-8"):
            pass
    except FileExistsError:
        return False
    except OSError:
        pass  # verrou impossible (droits…) : le test ci-dessus suffira
    return True


def release_stem(folder, stem):
    try:
        os.remove(os.path.join(folder, stem + LOCK_SUFFIX))
    except OSError:
        pass


def unique_stem(folder, stem, quality=None):
    """Nom libre (et réservé) : jamais d'écrasement du fichier d'une autre vidéo."""
    candidates = [stem]
    if quality and quality != formats.BEST:
        height = formats.height_from_quality(quality)
        if height:
            candidates.append(f"{stem} ({height}p)")
    for candidate in candidates:
        if _reserve(folder, candidate):
            return candidate
    number = 2
    while not _reserve(folder, f"{stem} ({number})"):
        number += 1
    return f"{stem} ({number})"
