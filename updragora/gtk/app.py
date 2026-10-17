"""Application entry point for the GTK4 backend."""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk

from updragora import APP_ID, ICON_NAME
from updragora.core import backend
from updragora.core.config import config
from updragora.core.i18n import _, i18n
from updragora.gtk.preferences import apply_theme
from updragora.gtk.window import MainWindow


class UPDragoraApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        self._add_action("preferences", self._on_preferences)
        self._add_action("about", self._on_about)

    def _add_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def do_activate(self):
        if not self.window:
            apply_theme(config.get("theme"))
            self._register_icon()
            self.window = MainWindow(self)
            if not backend.dnf_available():
                self.window.show_toast(
                    _("dnf was not found on this system. "
                      "UPDragora requires OpenMandriva with dnf."))
        self.window.present()

    def _register_icon(self):
        """Make updragora.png resolvable as a themed icon.

        When installed, the icon lives in the hicolor theme. When running
        from a source checkout, the project root (which contains
        updragora.png) is added as an icon search path.
        """
        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            root = os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            if os.path.exists(os.path.join(root, f"{ICON_NAME}.png")):
                theme.add_search_path(root)
        Gtk.Window.set_default_icon_name(ICON_NAME)

    def _on_preferences(self, *args):
        if self.window:
            self.window.show_preferences()

    def _on_about(self, *args):
        if self.window:
            self.window.show_about()


def main():
    i18n.load(config.get("language"))
    app = UPDragoraApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
