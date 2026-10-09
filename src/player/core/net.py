"""Réseau : certificats SSL (Python sur iPhone n'a pas accès aux certificats du système)."""

import ssl

_context = None


def ssl_context():
    """Contexte SSL avec les autorités de certifi (même source que yt-dlp)."""
    global _context
    if _context is None:
        try:
            import certifi
            _context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _context = ssl.create_default_context()
    return _context
