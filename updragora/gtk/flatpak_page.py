"""Flatpak page - placeholder, functionality will be added later."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from updragora.core.i18n import _


class FlatpakPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        status = Adw.StatusPage(
            icon_name="application-x-addon-symbolic",
            title=_("Flatpak"),
            description=_("Management of Flatpak applications from Flathub "
                          "is coming soon."),
            vexpand=True,
        )
        self.append(status)
