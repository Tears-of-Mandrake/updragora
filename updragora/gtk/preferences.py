"""Application preferences window."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from updragora.core.config import config
from updragora.core.i18n import _, LANGUAGES

THEMES = ["system", "light", "dark"]
THEME_LABELS = {"system": "System", "light": "Light", "dark": "Dark"}


def apply_theme(theme):
    manager = Adw.StyleManager.get_default()
    if theme == "light":
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    elif theme == "dark":
        manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    else:
        manager.set_color_scheme(Adw.ColorScheme.DEFAULT)


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True,
                         title=_("Preferences"))

        page = Adw.PreferencesPage(title=_("General"),
                                   icon_name="preferences-system-symbolic")

        # --- Appearance ----------------------------------------------------
        appearance = Adw.PreferencesGroup(title=_("Appearance"))
        theme_row = Adw.ComboRow(
            title=_("Theme"),
            subtitle=_("Follow the system style or force light/dark"))
        theme_model = Gtk.StringList()
        for t in THEMES:
            theme_model.append(_(THEME_LABELS[t]))
        theme_row.set_model(theme_model)
        current_theme = config.get("theme")
        theme_row.set_selected(
            THEMES.index(current_theme) if current_theme in THEMES else 0)
        theme_row.connect("notify::selected", self._on_theme_changed)
        appearance.add(theme_row)
        page.add(appearance)

        # --- Language --------------------------------------------------------
        language = Adw.PreferencesGroup(title=_("Language"))
        lang_row = Adw.ComboRow(
            title=_("Application language"),
            subtitle=_("Requires an application restart"))
        self._lang_codes = ["auto"] + list(LANGUAGES.keys())
        lang_model = Gtk.StringList()
        lang_model.append(_("System default"))
        for code in LANGUAGES:
            lang_model.append(LANGUAGES[code])
        lang_row.set_model(lang_model)
        current_lang = config.get("language")
        lang_row.set_selected(
            self._lang_codes.index(current_lang)
            if current_lang in self._lang_codes else 0)
        lang_row.connect("notify::selected", self._on_language_changed)
        language.add(lang_row)
        page.add(language)

        # --- Updates ---------------------------------------------------------
        updates = Adw.PreferencesGroup(title=_("Updates"))
        refresh_row = Adw.SwitchRow(
            title=_("Refresh repository metadata"),
            subtitle=_("Pass --refresh when checking for updates"))
        refresh_row.set_active(bool(config.get("refresh_metadata")))
        refresh_row.connect("notify::active", self._on_refresh_changed)
        updates.add(refresh_row)
        page.add(updates)

        self.add(page)

    def _on_theme_changed(self, row, *args):
        theme = THEMES[row.get_selected()]
        config.set("theme", theme)
        apply_theme(theme)

    def _on_language_changed(self, row, *args):
        config.set("language", self._lang_codes[row.get_selected()])

    def _on_refresh_changed(self, row, *args):
        config.set("refresh_metadata", row.get_active())
