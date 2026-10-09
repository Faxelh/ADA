"""
Reconnaissance des liens : plateforme, identifiant de vidéo, nettoyage
et dédoublonnage. Aucune requête réseau ici — tout est instantané.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PLATFORMS = {
    "youtube": {"name": "YouTube", "hosts": ("youtube.com", "youtu.be", "youtube-nocookie.com"), "icon": "youtube", "symbol": "▶"},
    "facebook": {"name": "Facebook", "hosts": ("facebook.com", "fb.watch", "fb.com"), "icon": "facebook", "symbol": "f"},
    "tiktok": {"name": "TikTok", "hosts": ("tiktok.com",), "icon": "tiktok", "symbol": "♪"},
    "instagram": {"name": "Instagram", "hosts": ("instagram.com",), "icon": "instagram", "symbol": "◎"},
    "x": {"name": "X", "hosts": ("x.com", "twitter.com"), "icon": "x", "symbol": "𝕏"},
    "dailymotion": {"name": "Dailymotion", "hosts": ("dailymotion.com", "dai.ly"), "icon": "other", "symbol": "⌁"},
    "vimeo": {"name": "Vimeo", "hosts": ("vimeo.com",), "icon": "other", "symbol": "⌁"},
    "other": {"name": "Autres liens", "hosts": (), "icon": "other", "symbol": "⌁"},
}

# Onglets "vidéo unique" affichés dans le menu, dans cet ordre.
SINGLE_TABS = ("youtube", "facebook", "tiktok", "instagram", "x", "other")

# Paramètres de suivi/partage qui ne changent pas la vidéo désignée :
# ignorés pour reconnaître deux liens identiques.
TRACKING_PARAMS = {
    "si", "_r", "_t", "fbclid", "igsh", "igshid", "mibextid", "rdid", "sfnsn",
    "share_app_id", "share_link_id", "is_from_webapp", "sender_device", "sender_web_id",
    "is_copy_url", "web_id", "feature", "pp", "ab_channel", "ref", "ref_src", "s",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "__cft__",
    "__tn__", "refsrc", "_nc_ht",
}

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>»\"'…"


def _host(url):
    try:
        host = urlparse(url.strip()).netloc.lower()
    except (AttributeError, ValueError):
        return ""
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    for prefix in ("www.", "m.", "mobile.", "web.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def _host_matches(host, allowed):
    return host == allowed or host.endswith("." + allowed)


def is_http_url(url):
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc


def extract_urls(text):
    """Toutes les URL http(s) trouvées dans un texte (même collées au milieu d'autres mots)."""
    urls = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(_TRAILING)
        if is_http_url(url):
            urls.append(url)
    return urls


def detect_platform(url):
    host = _host(url)
    for key, service in PLATFORMS.items():
        if any(_host_matches(host, allowed) for allowed in service["hosts"]):
            return key
    return "other"


def platform_name(key):
    return PLATFORMS.get(key, PLATFORMS["other"])["name"]


def matches_platform(url, platform):
    """Le lien correspond-il à l'onglet choisi ? ("other" accepte tout lien http)."""
    if not is_http_url(url):
        return False
    hosts = PLATFORMS.get(platform, PLATFORMS["other"])["hosts"]
    if not hosts:
        return True
    return any(_host_matches(_host(url), allowed) for allowed in hosts)


_YT_ID = r"([A-Za-z0-9_-]{11})"


def video_id_from_url(url):
    """Identifiant de la vidéo quand il est lisible directement dans le lien (sinon None)."""
    if not url:
        return None

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None

    host = _host(url)
    path = parsed.path or ""
    query = dict(parse_qsl(parsed.query))

    if _host_matches(host, "youtu.be"):
        match = re.match(r"/" + _YT_ID, path)
        return match.group(1) if match else None

    if _host_matches(host, "youtube.com") or _host_matches(host, "youtube-nocookie.com"):
        if re.fullmatch(_YT_ID, query.get("v", "")):
            return query["v"]
        match = re.match(r"/(?:shorts|live|embed|v)/" + _YT_ID, path)
        return match.group(1) if match else None

    if _host_matches(host, "tiktok.com"):
        match = re.search(r"/(?:video|photo)/(\d+)", path)
        return match.group(1) if match else None

    if _host_matches(host, "facebook.com") or _host_matches(host, "fb.com"):
        match = re.search(r"/(?:reel|videos)/(?:[^/]+/)?(\d{6,})", path)
        if match:
            return match.group(1)
        if query.get("v", "").isdigit():
            return query["v"]
        return None

    if _host_matches(host, "instagram.com"):
        match = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", path)
        return match.group(1) if match else None

    if _host_matches(host, "x.com") or _host_matches(host, "twitter.com"):
        match = re.search(r"/status(?:es)?/(\d+)", path)
        return match.group(1) if match else None

    return None


def normalize_url(url):
    """
    Forme canonique d'un lien, utilisée uniquement pour reconnaître deux
    liens identiques (on télécharge toujours le lien d'origine).
    """
    if not url:
        return ""

    url = url.strip()

    try:
        parsed = urlparse(url)
    except ValueError:
        return url.lower()

    host = _host(url)
    video_id = video_id_from_url(url)

    if video_id and (_host_matches(host, "youtu.be") or _host_matches(host, "youtube.com")):
        return f"https://youtube.com/watch?v={video_id}"

    query = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    query.sort()
    path = (parsed.path or "").rstrip("/")
    return urlunparse(("https", host, path, "", urlencode(query), ""))


def dedupe(urls):
    """Retire les doublons (en gardant l'ordre). Renvoie (liste_unique, nombre_de_doublons)."""
    seen = set()
    unique = []

    for url in urls:
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)

    return unique, len(urls) - len(unique)


def is_tiktok_photo(url):
    """Publication « photo » TikTok (diaporama) : non prise en charge par yt-dlp."""
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return False
    return _host_matches(_host(url), "tiktok.com") and "/photo/" in path


def is_youtube_playlist(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = _host(url)
    if not (_host_matches(host, "youtube.com") or _host_matches(host, "youtu.be")):
        return False
    return "list" in dict(parse_qsl(parsed.query)) or parsed.path.rstrip("/") == "/playlist"
