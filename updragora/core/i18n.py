"""Lightweight JSON-based translation system.

Adding a new language: drop a ``<code>.json`` file into
``updragora/translations/`` mapping English source strings to
translated strings, then add the language to ``LANGUAGES``.
"""

import json
import locale
import os

TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "translations")

# code -> native name (shown in preferences)
LANGUAGES = {
    "en": "English",
    "pl": "Polski",
}


class I18n:
    def __init__(self):
        self.language = "en"
        self._catalog = {}

    @staticmethod
    def system_language():
        try:
            loc = locale.getlocale()[0] or os.environ.get("LANG", "en")
        except ValueError:
            loc = "en"
        code = (loc or "en").split("_")[0].split(".")[0].lower()
        return code if code in LANGUAGES else "en"

    def load(self, language="auto"):
        if language in (None, "", "auto"):
            language = self.system_language()
        if language not in LANGUAGES:
            language = "en"
        self.language = language
        self._catalog = {}
        path = os.path.join(TRANSLATIONS_DIR, f"{language}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._catalog = data
        except (OSError, ValueError):
            pass

    def gettext(self, text):
        translated = self._catalog.get(text)
        return translated if translated else text


i18n = I18n()


def _(text):
    return i18n.gettext(text)
