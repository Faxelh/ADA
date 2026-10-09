"""
Langues de l'interface : français (texte de référence) et anglais.

Chaque texte affiché passe par tr("Texte en français"). En anglais, il est
cherché dans lang_en.EN ; s'il manque, le français s'affiche (jamais de
clé brute à l'écran). Un test vérifie que tous les tr("…") du code ont
leur traduction.

La langue est choisie au lancement : réglage « Langue » de l'app, sinon
la langue du téléphone (français si c'est du français, anglais sinon).
"""

SUPPORTED = ("fr", "en")
CHOICES = ("system", "fr", "en")
NAMES = {"fr": "Français", "en": "English"}

_lang = "fr"


def detect(preferred):
    """Première langue prise en charge parmi celles du téléphone."""
    for code in preferred or []:
        base = str(code).replace("_", "-").split("-")[0].lower()
        if base in SUPPORTED:
            return base
    return "en" if preferred else "fr"


def init(choice="system", preferred=None):
    global _lang
    _lang = choice if choice in SUPPORTED else detect(preferred)
    return _lang


def current():
    return _lang


def tr(text, **values):
    if _lang == "en":
        from . import lang_en
        text = lang_en.EN.get(text, text)
    return text.format(**values) if values else text


def choice_label(choice):
    if choice == "system":
        return tr("Langue du téléphone")
    return NAMES.get(choice, choice)
