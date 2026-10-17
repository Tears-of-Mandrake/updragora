"""Main application window."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

from updragora import (APP_AUTHOR, APP_NAME, APP_VERSION, APP_WEBSITE,
                       ICON_NAME)
from updragora.core.i18n import _
from updragora.gtk.flatpak_page import FlatpakPage
from updragora.gtk.packages_page import PackagesPage
from updragora.gtk.preferences import PreferencesWindow
from updragora.gtk.update_page import UpdatePage


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1000, 700)

        self.toast_overlay = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()

        # header bar with view switcher and menu
        header = Adw.HeaderBar()
        self.view_stack = Adw.ViewStack(vexpand=True)
        switcher = Adw.ViewSwitcher(stack=self.view_stack,
                                    policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        menu = Gio.Menu()
        menu.append(_("Preferences"), "app.preferences")
        menu.append(_("About UPDragora"), "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                     menu_model=menu)
        header.pack_end(menu_button)
        toolbar.add_top_bar(header)

        # pages
        self.update_page = UpdatePage(self)
        page = self.view_stack.add_titled(self.update_page, "update",
                                          _("Update"))
        page.set_icon_name("software-update-available-symbolic")

        self.install_page = PackagesPage(self, "install")
        page = self.view_stack.add_titled(self.install_page, "install",
                                          _("Install"))
        page.set_icon_name("list-add-symbolic")

        self.remove_page = PackagesPage(self, "remove")
        page = self.view_stack.add_titled(self.remove_page, "remove",
                                          _("Remove"))
        page.set_icon_name("list-remove-symbolic")

        self.flatpak_page = FlatpakPage(self)
        page = self.view_stack.add_titled(self.flatpak_page, "flatpak",
                                          _("Flatpak"))
        page.set_icon_name("application-x-addon-symbolic")

        self.view_stack.connect("notify::visible-child-name",
                                self._on_page_changed)

        toolbar.set_content(self.view_stack)

        # bottom switcher for narrow windows
        switcher_bar = Adw.ViewSwitcherBar(stack=self.view_stack)
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 720sp"))
        breakpoint_.add_setter(switcher_bar, "reveal", True)
        breakpoint_.add_setter(header, "title-widget", None)
        self.add_breakpoint(breakpoint_)
        toolbar.add_bottom_bar(switcher_bar)

        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

    def _on_page_changed(self, *args):
        # lazy-load package lists when a tab is opened for the first time
        name = self.view_stack.get_visible_child_name()
        if name == "install":
            self.install_page.load_packages()
        elif name == "remove":
            self.remove_page.load_packages()

    def show_toast(self, text):
        self.toast_overlay.add_toast(Adw.Toast(title=text))

    def show_preferences(self):
        PreferencesWindow(self).present()

    def show_about(self):
        about = Adw.AboutWindow(
            transient_for=self,
            application_name=APP_NAME,
            application_icon=ICON_NAME,
            version=APP_VERSION,
            developer_name=APP_AUTHOR,
            developers=[APP_AUTHOR],
            website=APP_WEBSITE,
            license_type=Gtk.License.GPL_3_0,
            comments=_("A modern system updater and package manager "
                       "for OpenMandriva Linux."),
        )
        about.present()
