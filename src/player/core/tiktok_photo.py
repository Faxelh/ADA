"""
Publications « photo » TikTok (diaporamas d'images avec musique).

yt-dlp ne les prend pas en charge : ADA lit lui-même la page publique de
la publication (données « __UNIVERSAL_DATA_FOR_REHYDRATION__ », comme le
fait gallery-dl) puis télécharge chaque photo et la musique.

Sur téléphone : les photos vont dans un dossier « Titre (photos) » (et dans
l'app Photos si l'option est active) ; en mode Audio, seule la musique est
gardée.
"""

import binascii
import hashlib
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

SHORT_HOSTS = ("vt.tiktok.com", "vm.tiktok.com")
POST_RE = re.compile(r"/(?:@[^/]*/)?(photo|video)/(\d+)")
REDIRECTS = (301, 302, 303, 307, 308)
BROWSER_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
PAGE_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}
MEDIA_HEADERS = {"User-Agent": BROWSER_UA, "Referer": "https://www.tiktok.com/"}


class PhotoError(RuntimeError):
    pass


# ------------------------------------------------------------------
# Accès HTTP (curl_cffi si présent : TikTok filtre moins un vrai « navigateur »)
# ------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Http:
    """Petit client HTTP avec cookies : curl_cffi (impersonation Chrome) ou urllib."""

    def __init__(self, timeout=20):
        self.timeout = timeout
        self.session = None
        try:
            from curl_cffi import requests as curl_requests
            self.session = curl_requests.Session(impersonate="chrome")
        except Exception:
            self.session = None
        self.jar = http.cookiejar.CookieJar()

    def get(self, url, headers=None, allow_redirects=True):
        """Renvoie (statut, url_finale, en-têtes en minuscules, contenu en octets)."""
        headers = headers or {}
        if self.session is not None:
            response = self.session.get(url, headers=headers, allow_redirects=allow_redirects,
                                        timeout=self.timeout)
            return (response.status_code, str(response.url),
                    {k.lower(): v for k, v in response.headers.items()}, response.content)

        from player.core import net
        handlers = [urllib.request.HTTPCookieProcessor(self.jar),
                    urllib.request.HTTPSHandler(context=net.ssl_context())]
        if not allow_redirects:
            handlers.append(_NoRedirect())
        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return (response.status, response.geturl(),
                        {k.lower(): v for k, v in response.headers.items()}, response.read())
        except urllib.error.HTTPError as error:
            return (error.code, url, {k.lower(): v for k, v in (error.headers or {}).items()},
                    error.read() or b"")

    def download(self, url, path, headers=None, progress=None, cancel=None):
        """Enregistre url dans path par morceaux (vidéos), avec avancement et arrêt possible."""
        headers = headers or {}
        partial = path + ".part"
        if self.session is not None:
            status, _final, _headers, body = self.get(url, headers=headers)
            if status >= 400 or not body:
                raise PhotoError(f"Téléchargement refusé par TikTok (HTTP {status}).")
            with open(partial, "wb") as file:
                file.write(body)
            os.replace(partial, path)
            return path

        from player.core import net
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                             urllib.request.HTTPSHandler(context=net.ssl_context()))
        request = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(request, timeout=self.timeout) as response:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                with open(partial, "wb") as file:
                    while True:
                        if cancel is not None and cancel():
                            raise PhotoError("player:cancel")
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        file.write(chunk)
                        done += len(chunk)
                        if progress and total:
                            progress(min(1.0, done / total))
        except urllib.error.HTTPError as error:
            raise PhotoError(f"Téléchargement refusé par TikTok (HTTP {error.code}).") from error
        if not os.path.isfile(partial) or os.path.getsize(partial) == 0:
            raise PhotoError("Téléchargement refusé par TikTok (fichier vide).")
        os.replace(partial, path)
        return path

    def set_cookie(self, name, value, domain=".tiktok.com"):
        if self.session is not None:
            self.session.cookies.set(name, value, domain=domain)
            return
        self.jar.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, domain, True, domain.startswith("."), "/", True,
            False, int(time.time()) + 60, False, None, None, {},
        ))


# ------------------------------------------------------------------
# Liens
# ------------------------------------------------------------------

def is_tiktok(url):
    host = urlparse(url or "").netloc.lower().split(":")[0]
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def is_short_link(url):
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    return any(host.endswith(short) for short in SHORT_HOSTS) or parsed.path.startswith("/t/")


def is_photo_url(url):
    match = POST_RE.search(urlparse(url or "").path)
    return bool(match and match.group(1) == "photo")


def resolve(http_client, url):
    """Suit les liens raccourcis (vt.tiktok.com/…) jusqu'à l'adresse réelle de la publication."""
    for _ in range(6):
        if not is_short_link(url):
            break
        status, _final, headers, _body = http_client.get(
            url, headers={"User-Agent": "facebookexternalhit/1.1"}, allow_redirects=False,
        )
        location = headers.get("location")
        if status in REDIRECTS and location:
            url = urljoin(url, location)
        else:
            break
    return url


# ------------------------------------------------------------------
# Lecture de la publication
# ------------------------------------------------------------------

def _between(text, start, end):
    begin = text.find(start)
    if begin < 0:
        return ""
    begin += len(start)
    stop = text.find(end, begin)
    return text[begin:stop] if stop >= 0 else ""


def extract_data(html):
    raw = _between(html, '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">', "</script>")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def solve_challenge(html):
    """
    TikTok répond parfois par une petite « preuve de travail » au lieu de la
    page : on la résout (SHA-256, comme gallery-dl) et on renvoie les
    cookies à poser avant de redemander la page. None si pas de défi.
    """
    encoded = _between(_between(html, 'id="cs"', ">"), 'class="', '"')
    if not encoded:
        return None
    try:
        challenge = json.loads(binascii.a2b_base64(encoded + "==").decode())
        expected = binascii.a2b_base64(challenge["v"]["c"] + "==")
        base = hashlib.sha256(binascii.a2b_base64(challenge["v"]["a"] + "=="))
    except (ValueError, KeyError, TypeError, binascii.Error):
        return None

    for index in range(1_000_000):
        attempt = base.copy()
        attempt.update(str(index).encode())
        if attempt.digest() == expected:
            break
    else:
        return None

    challenge["d"] = binascii.b2a_base64(str(index).encode(), newline=False).decode()
    value = binascii.b2a_base64(json.dumps(challenge, separators=(",", ":")).encode(), newline=False).decode()
    cookies = {}
    wci = _between(_between(html, 'id="wci"', ">"), 'class="', '"')
    rci = _between(_between(html, 'id="rci"', ">"), 'class="', '"')
    rs = _between(_between(html, 'id="rs"', ">"), 'class="', '"')
    if wci:
        cookies[wci] = value
    if rci and rs:
        cookies[rci] = rs
    return cookies or None


def fetch_post(http_client, url):
    """Données de la publication (itemStruct de TikTok)."""
    match = POST_RE.search(urlparse(url).path)
    if not match:
        raise PhotoError("Lien TikTok non reconnu.")
    page = url.split("?")[0].split("#")[0]

    data = None
    for attempt in range(4):
        status, _final, _headers, body = http_client.get(page, headers=PAGE_HEADERS)
        html = body.decode("utf-8", "replace")
        data = extract_data(html)
        if data:
            break
        cookies = solve_challenge(html)
        if cookies:
            for name, value in cookies.items():
                http_client.set_cookie(name, value)
            continue
        if status == 404:
            raise PhotoError("Publication TikTok introuvable (supprimée ou privée).")
        time.sleep(1.5 * (attempt + 1))

    if not data:
        raise PhotoError("TikTok n’a pas renvoyé le contenu de cette publication photo "
                         "(protection anti-robots). Réessayez dans quelques minutes.")

    detail = (data.get("__DEFAULT_SCOPE__") or {}).get("webapp.video-detail") or {}
    status_code = detail.get("statusCode")
    if status_code not in (0, None):
        message = detail.get("statusMsg") or f"code {status_code}"
        raise PhotoError(f"Publication TikTok indisponible ({message}).")
    item = (detail.get("itemInfo") or {}).get("itemStruct") or {}
    if not item:
        raise PhotoError("Publication TikTok vide ou réservée aux comptes connectés.")
    return item


def _pick_url(urls):
    urls = [u for u in (urls or []) if isinstance(u, str) and u.startswith("http")]
    for url in urls:
        if "jpeg" in url.lower() or ".jpg" in url.lower():
            return url
    return urls[0] if urls else None


def parse_item(item):
    images = []
    for image in (item.get("imagePost") or {}).get("images") or []:
        url = _pick_url((image.get("imageURL") or {}).get("urlList"))
        if url:
            images.append({"url": url, "width": image.get("imageWidth"), "height": image.get("imageHeight")})
    author = item.get("author") or {}
    music = item.get("music") or {}
    stats = item.get("stats") or {}
    return {
        "id": str(item.get("id") or ""),
        "desc": item.get("desc") or (item.get("imagePost") or {}).get("title") or "",
        "uploader": author.get("nickname") or author.get("uniqueId") or "",
        "handle": author.get("uniqueId") or "",
        "images": images,
        "music_url": music.get("playUrl") or None,
        "music_title": music.get("title") or "",
        "music_duration": music.get("duration"),
        "views": stats.get("playCount"),
    }


def load(url):
    """Résout le lien et lit la publication. Renvoie (url_réelle, données, client_http)."""
    client = Http()
    real_url = resolve(client, url)
    if not is_photo_url(real_url):
        return real_url, None, client
    return real_url, parse_item(fetch_post(client, real_url)), client


def parse_video(item):
    """Adresses directes d'une vidéo TikTok (H.264 d'abord : lisible partout)."""
    base = parse_item(item)
    video = item.get("video") or {}
    candidates = []
    for info in video.get("bitrateInfo") or []:
        if not isinstance(info, dict):
            continue
        urls = (info.get("PlayAddr") or {}).get("UrlList") or []
        codec = str(info.get("CodecType") or "").lower()
        score = 2 if "h264" in codec else (1 if ("h265" in codec or "hvc1" in codec) else 0)
        candidates.append((score, int(info.get("Bitrate") or 0), urls))
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    urls = []
    for _score, _rate, links in candidates:
        for link in links:
            if isinstance(link, str) and link.startswith("http") and link not in urls:
                urls.append(link)
    for key in ("playAddr", "downloadAddr"):
        link = video.get(key)
        if isinstance(link, str) and link.startswith("http") and link not in urls:
            urls.append(link)
    base.update({
        "urls": urls,
        "duration": video.get("duration"),
        "thumbnail": video.get("cover") or video.get("originCover") or None,
    })
    return base


def load_video(url):
    """Vidéo TikTok lue directement sur la page publique (si yt-dlp échoue).
    Renvoie (url_réelle, données, client_http)."""
    client = Http()
    real_url = resolve(client, url)
    return real_url, parse_video(fetch_post(client, real_url)), client


# ------------------------------------------------------------------
# Téléchargement
# ------------------------------------------------------------------

_TYPES = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "image/png": ".png",
          "image/heic": ".heic", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
          "audio/x-m4a": ".m4a", "video/mp4": ".m4a"}


def _extension(url, headers, default):
    content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type in _TYPES:
        return _TYPES[content_type]
    path = urlparse(url).path.lower()
    for ext in (".jpeg", ".jpg", ".webp", ".png", ".mp3", ".m4a", ".aac"):
        if ext in path:
            return ".jpg" if ext == ".jpeg" else ext
    return default


def _fetch_to(client, url, target_without_ext, default_ext):
    status, _final, headers, body = client.get(url, headers=MEDIA_HEADERS)
    if status >= 400 or not body:
        raise PhotoError(f"Téléchargement d’un élément refusé par TikTok (HTTP {status}).")
    path = target_without_ext + _extension(url, headers, default_ext)
    with open(path, "wb") as file:
        file.write(body)
    return path


def download(post, folder, stem, media_type, progress=None, cancel=None, client=None):
    """
    Télécharge une publication photo dans folder.
    Renvoie {"files": [...], "photos": [...], "music": chemin|None, "main": chemin, "label": str}.
    """
    client = client or Http()
    os.makedirs(folder, exist_ok=True)

    def check():
        if cancel is not None and cancel():
            raise PhotoError("player:cancel")

    if media_type == "audio":
        if not post["music_url"]:
            raise PhotoError("Cette publication photo n’a pas de musique à télécharger.")
        check()
        path = _fetch_to(client, post["music_url"], os.path.join(folder, stem), ".mp3")
        if progress:
            progress(1.0)
        return {"files": [path], "photos": [], "music": path, "main": path,
                "label": os.path.splitext(path)[1].lstrip(".").upper()}

    if not post["images"]:
        raise PhotoError("Cette publication TikTok ne contient aucune photo téléchargeable.")

    photos_dir = os.path.join(folder, f"{stem} (photos)")
    os.makedirs(photos_dir, exist_ok=True)
    total = len(post["images"]) + (1 if post["music_url"] else 0)
    width = max(2, len(str(len(post["images"]))))
    photos = []
    for index, image in enumerate(post["images"], start=1):
        check()
        photos.append(_fetch_to(client, image["url"], os.path.join(photos_dir, f"{index:0{width}d}"), ".jpg"))
        if progress:
            progress(index / total)

    music = None
    if post["music_url"]:
        check()
        try:
            music = _fetch_to(client, post["music_url"], os.path.join(photos_dir, "musique"), ".mp3")
        except PhotoError:
            music = None  # la musique est un bonus : les photos restent
    if progress:
        progress(1.0)
    files = photos + ([music] if music else [])
    return {"files": files, "photos": photos, "music": music, "main": photos_dir,
            "label": f"{len(photos)} photos"}
