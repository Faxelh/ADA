"""
Choix des formats lisibles sur iPhone/Android et petits formatages.

Sans FFmpeg, ADA ne réencode rien : il choisit des flux déjà au
bon format (vidéo H.264/HEVC en MP4, son AAC en M4A) puis les réunit avec
mp4mux. Les flux WebM/VP9/AV1/Opus de YouTube sont donc écartés.
"""

import re

BEST = "Meilleure"

QUALITY_LADDER = [2160, 1440, 1080, 720, 480, 360, 240, 144]
QUALITY_LABELS = {
    2160: "2160p (4K)",
    1440: "1440p (2K)",
    1080: "1080p",
    720: "720p",
    480: "480p",
    360: "360p",
    240: "240p",
    144: "144p",
}
STANDARD_QUALITIES = [BEST, "1080p", "720p", "480p", "360p"]

MP4_EXTS = {"mp4", "m4v", "m4a", "mov", "3gp"}
MP4_CONTAINERS = {"mp4_dash", "m4a_dash", "mp4", "m4a"}
GOOD_PROTOCOLS = {"https", "http", "http_dash_segments", "http_dash_segments_generator"}
HLS_PROTOCOLS = {"m3u8_native", "m3u8"}


def height_from_quality(quality):
    match = re.match(r"\s*(\d{3,4})", quality or "")
    return int(match.group(1)) if match else None


def _codec(value):
    return (value or "").lower()


def has_video(fmt):
    vcodec = fmt.get("vcodec")
    if vcodec == "none":
        return False
    return bool(vcodec) or bool(fmt.get("height")) or fmt.get("ext") in ("mp4", "m4v", "mov")


def has_audio(fmt):
    acodec = fmt.get("acodec")
    if acodec == "none":
        return False
    if acodec:
        return True
    # Codec inconnu : un fichier audio seul est forcément de l'audio ; un MP4 « complet » souvent aussi.
    return fmt.get("ext") in ("m4a", "mp3", "aac") or (fmt.get("vcodec") not in (None, "none"))


def _protocol_score(fmt):
    protocol = (fmt.get("protocol") or "https").split("+")[0]
    if protocol in GOOD_PROTOCOLS:
        return 2
    if protocol in HLS_PROTOCOLS:
        return 0
    return -1


def _container_ok(fmt):
    ext = (fmt.get("ext") or "").lower()
    container = (fmt.get("container") or "").lower()
    if container:
        return container in MP4_CONTAINERS
    return ext in MP4_EXTS or (ext == "" and _protocol_score(fmt) == 0)


def video_codec_score(fmt):
    codec = _codec(fmt.get("vcodec"))
    if codec.startswith(("avc1", "h264")):
        return 2
    if codec.startswith(("hvc1", "hev1", "h265", "hevc")):
        return 1  # « hev1 » est réétiqueté « hvc1 » par mp4mux (sinon illisible sur iPhone)
    if codec.startswith("avc3"):
        return 0
    if codec.startswith("bytevc1"):  # HEVC de TikTok : lisible, mais en dernier choix
        return 0
    if not codec:
        return 1 if (fmt.get("ext") or "").lower() in ("mp4", "m4v", "mov") else -1
    return -1  # vp9, av01, vp8…


def audio_codec_ok(fmt):
    codec = _codec(fmt.get("acodec"))
    if codec.startswith(("mp4a", "aac")):
        return True
    if not codec:
        return (fmt.get("ext") or "").lower() in ("m4a", "mp4", "aac")
    return False


def usable(fmt):
    if fmt.get("has_drm"):
        return False
    if _protocol_score(fmt) < 0:
        return False
    return _container_ok(fmt)


def _height(fmt):
    try:
        return int(fmt.get("height") or 0)
    except (TypeError, ValueError):
        return 0


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _video_key(fmt):
    return (_height(fmt), video_codec_score(fmt), _protocol_score(fmt), _number(fmt.get("fps")),
            _number(fmt.get("tbr") or fmt.get("vbr")))


def _audio_key(fmt):
    return (_number(fmt.get("language_preference")), _protocol_score(fmt),
            _number(fmt.get("abr") or fmt.get("tbr")))


def video_candidates(formats):
    return [f for f in formats or [] if usable(f) and has_video(f) and video_codec_score(f) >= 0
            and (not has_audio(f) or audio_codec_ok(f))]


def audio_candidates(formats):
    return [f for f in formats or [] if usable(f) and has_audio(f) and not has_video(f) and audio_codec_ok(f)]


def _limit(candidates, max_height):
    if not max_height:
        return candidates
    below = [f for f in candidates if _height(f) <= max_height + max(20, max_height * 0.1)]
    if below:
        return below
    smallest = min(_height(f) for f in candidates)
    return [f for f in candidates if _height(f) == smallest]


def choose(formats, media_type, quality=BEST):
    """
    Plan de téléchargement :
      {"mode": "merge"|"single"|"audio"|"extract_audio", "video": fmt|None, "audio": fmt|None}
    ou None si aucun format compatible.
    """
    formats = [f for f in (formats or []) if isinstance(f, dict)]
    audios = sorted(audio_candidates(formats), key=_audio_key, reverse=True)
    videos = video_candidates(formats)

    if media_type == "audio":
        if audios:
            return {"mode": "audio", "video": None, "audio": audios[0]}
        combined = [f for f in videos if has_audio(f)]
        if combined:
            # Pas de flux audio seul : on prend la plus petite vidéo et on garde son son.
            combined.sort(key=lambda f: (_protocol_score(f), -_height(f)), reverse=True)
            return {"mode": "extract_audio", "video": combined[0], "audio": None}
        return None

    max_height = None if quality in (None, "", BEST) else height_from_quality(quality)
    video_only = _limit([f for f in videos if not has_audio(f)], max_height) if videos else []
    combined = _limit([f for f in videos if has_audio(f)], max_height) if videos else []

    best_video_only = max(video_only, key=_video_key) if (video_only and audios) else None
    best_combined = max(combined, key=_video_key) if combined else None

    if best_video_only is not None and (
        best_combined is None or _video_key(best_video_only)[:3] > _video_key(best_combined)[:3]
    ):
        return {"mode": "merge", "video": best_video_only, "audio": audios[0]}
    if best_combined is not None:
        return {"mode": "single", "video": best_combined, "audio": None}
    if video_only:
        # Vidéo sans aucun son disponible (rare) : on la garde telle quelle.
        return {"mode": "single", "video": max(video_only, key=_video_key), "audio": None}
    return None


def qualities_from_formats(formats):
    """Qualités réellement téléchargeables sur le téléphone pour CETTE vidéo."""
    heights = {_height(f) for f in video_candidates(formats) if _height(f)}
    if not heights:
        return [BEST]
    labels = [BEST]
    for step in QUALITY_LADDER:
        if any(abs(height - step) <= max(20, step * 0.1) for height in heights):
            labels.append(QUALITY_LABELS[step])
    if len(labels) == 1:
        labels.append(f"{max(heights)}p")
    return labels


def estimated_size(fmt, duration=None):
    if not fmt:
        return 0
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size:
        return _number(size)
    tbr = _number(fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr"))
    if tbr and duration:
        return tbr * 1000 / 8 * _number(duration)
    return 0


# ----------------------------------------------------------------------
# Textes
# ----------------------------------------------------------------------

def duration_text(seconds):
    try:
        seconds = int(float(seconds or 0))
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def size_text(value):
    """Taille lisible : « 5,1 Mo » en français, « 5.1 MB » en anglais."""
    from .i18n import current
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    english = current() == "en"
    units = ("B", "KB", "MB", "GB") if english else ("o", "Ko", "Mo", "Go")
    for index, unit in enumerate(units):
        if value < 1024 or index == len(units) - 1:
            if index == 0:
                return f"{value:.0f} {unit}"
            number = f"{value:.1f}"
            return f"{number if english else number.replace('.', ',')} {unit}"
        value /= 1024
    return ""


def speed_text(value):
    text = size_text(value)
    return f"{text}/s" if text else ""


def eta_text(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds < 0:
        return ""
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def count_text(value):
    """1 234 567 (français) / 1,234,567 (anglais)."""
    from .i18n import current
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return ""
    text = f"{value:,}"
    return text if current() == "en" else text.replace(",", "\u202f")


def short(text, limit=80):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
