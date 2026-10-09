"""
Traduction des erreurs (yt-dlp, réseau, FFmpeg, système) en messages
clairs, avec une catégorie qui décide de la suite : réessayer, attendre
le retour du réseau, ou abandonner ce lien.
"""

import re

# Catégories pour lesquelles une nouvelle tentative a du sens.
RETRYABLE = {"network", "server", "rate_limit", "stalled", "crash", "parse", "timeout"}

# Catégories qui signifient « la connexion internet a probablement sauté ».
NETWORK = {"network", "timeout"}


def _clean_raw(text):
    text = (text or "").strip()
    text = re.sub(r"^(?:ERROR|WARNING):\s*", "", text)
    text = re.sub(r"^\[[\w:.-]+\]\s*[\w.:-]*:\s*", "", text)
    text = re.sub(r";?\s*please report this issue on\s+https?://\S+.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:280] + "…") if len(text) > 280 else text


def explain(error, platform=None):
    """Renvoie (message, catégorie) pour une exception ou un texte d'erreur."""
    raw = str(error or "")
    lower = raw.lower()

    def has(*terms):
        return any(term in lower for term in terms)

    # --- Cas internes à ADA -------------------------------------------------
    if has("player:live"):
        return (
            "Contenu en direct (live) : ADA ne télécharge pas les directs en cours ou "
            "à venir. Réessayez quand la rediffusion sera disponible.",
            "live",
        )
    if has("player:duplicate"):
        return ("Déjà téléchargé (fichier présent).", "duplicate")
    if has("player:stalled"):
        return ("Bloqué : aucune progression pendant trop longtemps.", "stalled")
    if has("player:cancel"):
        return ("Annulé.", "cancelled")
    if has("player:pause"):
        return ("En pause.", "paused")
    if has("player:noformat"):
        return (
            "Aucun format lisible sur iPhone n’est proposé pour ce contenu "
            "(la plateforme ne fournit que du WebM/VP9). Essayez le mode Audio ou une autre qualité.",
            "format",
        )
    if has("player:container"):
        return (
            "Ce site livre la vidéo dans un format (MPEG-TS/HLS) que ADA ne sait pas encore "
            "convertir sur téléphone.",
            "format",
        )

    # --- Contenus non pris en charge ----------------------------------------
    if has("unsupported url"):
        if "tiktok.com" in lower and "/photo/" in lower:
            return (
                "Publication « photo » TikTok : lecture impossible pour le moment "
                "(TikTok a peut-être bloqué la demande). Réessayez plus tard.",
                "unsupported",
            )
        return ("Ce lien ne correspond pas à une vidéo prise en charge.", "unsupported")

    if has("is not a valid url", "invalid url"):
        return ("Lien invalide.", "unsupported")

    if has("drm", "widevine"):
        return ("Contenu protégé par DRM : téléchargement impossible.", "unsupported")

    # --- Direct / première ---------------------------------------------------
    if has("this live event will begin", "premieres in", "is_upcoming", "live event will begin"):
        return ("Ce direct n’a pas encore commencé.", "live")


    # --- Moteur JavaScript (YouTube) -----------------------------------------
    if has("javascript runtime", "js runtime", "challenge solving failed",
           "n challenge", "signature solving failed", "jsc"):
        return (
            "YouTube n’a pas pu être déchiffré avec le moteur JavaScript du téléphone. "
            "Réessayez ; si ça persiste, mettez ADA à jour.",
            "js_runtime",
        )
    if has("sign in to confirm", "not a bot"):
        return (
            "YouTube demande de confirmer que vous n’êtes pas un robot. Attendez un peu, "
            "ou connectez-vous à YouTube dans l’onglet Navigateur puis réessayez.",
            "auth",
        )

    # --- Accès / connexion ---------------------------------------------------
    if has("login", "log in", "registered users", "cookies-from-browser",
           "not comfortable for some audiences", "for the authentication", "sign in"):
        return (
            "Connexion requise pour ce contenu (restreint, sensible ou réservé aux comptes "
            "connectés). Connectez-vous au site dans l’onglet Navigateur si vous y avez accès.",
            "auth",
        )
    if has("private video", "this video is private", "is private"):
        return ("Vidéo privée : votre compte n’y a pas accès.", "auth")
    if "age" in lower and has("confirm your age", "age-restricted", "age restricted", "inappropriate"):
        return (
            "Vidéo soumise à une vérification d’âge. Connectez-vous au site dans l’onglet "
            "Navigateur si vous y êtes autorisé.",
            "auth",
        )
    if has("not available in your country", "geo restricted", "geo-restricted", "georestricted"):
        return ("Contenu non disponible dans votre pays.", "unavailable")

    # --- Contenu disparu -----------------------------------------------------
    if has("video unavailable", "content isn't available", "content is not available",
           "this content isn", "no longer available", "has been removed", "been deleted",
           "does not exist", "http error 404", "404: not found"):
        return ("Contenu introuvable ou supprimé (lien expiré ou incorrect).", "unavailable")

    # --- Plateforme qui bloque ----------------------------------------------
    if has("http error 429", "too many requests", "rate-limit", "rate limit"):
        return ("La plateforme limite les requêtes (429). ADA réessaiera dans un instant.", "rate_limit")
    if has("http error 403", "forbidden"):
        return (
            "L’hébergeur a refusé l’accès (403). Désactivez un éventuel VPN, puis réessayez.",
            "forbidden",
        )
    if re.search(r"http error 5\d\d", lower):
        return ("La plateforme rencontre un problème temporaire (erreur serveur).", "server")

    if has("impersonat"):
        return (
            "Ce site bloque les téléchargements hors navigateur pour ce contenu. "
            "Réessayez plus tard.",
            "impersonate",
        )

    if has("cannot parse data", "unable to extract", "unable to parse"):
        site = "Facebook" if (platform == "facebook" or "facebook" in lower) else "La plateforme"
        return (
            f"{site} a renvoyé une page que yt-dlp n’a pas su lire pour ce contenu. "
            "ADA réessaie automatiquement ; si ça persiste, mettez yt-dlp à jour.",
            "parse",
        )

    # --- Réseau --------------------------------------------------------------
    if has("getaddrinfo failed", "name or service not known", "nodename nor servname",
           "temporary failure in name resolution", "no address associated", "name resolution"):
        return ("Pas de connexion internet (adresse introuvable).", "network")
    if has("timed out", "timeout", "read operation timed out"):
        return ("Délai de connexion dépassé.", "timeout")
    if has("connection reset", "connection refused", "connection aborted", "remote end closed",
           "network is unreachable", "connectionerror", "urlopen error", "unable to download webpage",
           "incompleteread", "ssl: ", "eof occurred", "connection broken", "unable to connect"):
        if has("certificate verify failed"):
            return (
                "Erreur de certificat SSL : vérifiez la date et l’heure du téléphone.",
                "network",
            )
        return ("La connexion a été interrompue.", "network")

    # --- Formats / FFmpeg ----------------------------------------------------
    if has("requested format is not available", "no video formats found", "format is not available"):
        return (
            "La qualité demandée n’existe pas pour cette vidéo. Choisissez « Meilleure ».",
            "format",
        )
    if has("mp4mux", "index mp4", "boîte mp4", "fichier mp4", "piste "):
        return ("Impossible d’assembler la vidéo et le son : " + _clean_raw(raw), "mux")

    # --- Disque --------------------------------------------------------------
    if has("no space left", "errno 28", "disk full"):
        return ("Stockage du téléphone plein : libérez de l’espace puis réessayez.", "disk")
    if has("permission denied", "errno 13", "unable to open for writing", "errno 22",
           "file name too long"):
        return ("Impossible d’écrire le fichier sur le téléphone.", "disk")

    cleaned = _clean_raw(raw) or "Erreur inconnue."
    return (cleaned, "unknown")


def friendly(error, platform=None):
    return explain(error, platform)[0]


def category(error, platform=None):
    return explain(error, platform)[1]
