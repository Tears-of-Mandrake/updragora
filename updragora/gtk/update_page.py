"""System update page - dnf distro-sync frontend."""

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from updragora.core import backend
from updragora.core.config import config
from updragora.core.i18n import _
from updragora.gtk.transaction_dialog import TransactionDialog

# order and labels of transaction sections shown in the update list
SECTION_ORDER = [
    ("upgrade", "To upgrade"),
    ("downgrade", "To downgrade"),
    ("install", "New installations"),
    ("install-dep", "Dependencies to install"),
    ("install-weak", "Weak dependencies to install"),
    ("reinstall", "To reinstall"),
    ("obsolete", "Replacing obsolete packages"),
    ("remove", "To remove"),
]

REMOVAL_ACTIONS = {"remove"}


def _uptodate_paintable():
    """Load the bundled up-to-date.png (source checkout or installed)."""
    base = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    for path in (os.path.join(base, "up-to-date.png"),
                 "/usr/share/updragora/up-to-date.png"):
        if os.path.exists(path):
            try:
                return Gdk.Texture.new_from_filename(path)
            except GLib.Error:
                pass
    return None


class UpdatePage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self._updates = []

        self.stack = Gtk.Stack(vexpand=True,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.append(self.stack)

        # --- initial view -----------------------------------------------
        self.start_page = Adw.StatusPage(
            icon_name="software-update-available-symbolic",
            title=_("System Update"),
            description=_("Keep OpenMandriva up to date using distro-sync, "
                          "the recommended update method for this system."),
        )
        self.check_button = Gtk.Button(label=_("Check for Updates"),
                                       halign=Gtk.Align.CENTER)
        self.check_button.add_css_class("suggested-action")
        self.check_button.add_css_class("pill")
        self.check_button.connect("clicked", self._on_check_clicked)
        self.start_page.set_child(self.check_button)
        self.stack.add_named(self.start_page, "start")

        # --- checking view ------------------------------------------------
        checking = Adw.StatusPage(title=_("Checking for updates…"),
                                  description=_("This may take a moment."))
        spinner = Gtk.Spinner(spinning=True, width_request=32,
                              height_request=32, halign=Gtk.Align.CENTER)
        checking.set_child(spinner)
        self.stack.add_named(checking, "checking")

        # --- no updates view ----------------------------------------------
        uptodate = Adw.StatusPage(
            title=_("The system is up to date"),
            description=_("No updates are currently available."),
        )
        paintable = _uptodate_paintable()
        if paintable is not None:
            uptodate.set_paintable(paintable)
        else:
            uptodate.set_icon_name("emblem-ok-symbolic")
        recheck = Gtk.Button(label=_("Check again"), halign=Gtk.Align.CENTER)
        recheck.add_css_class("pill")
        recheck.connect("clicked", self._on_check_clicked)
        uptodate.set_child(recheck)
        self.stack.add_named(uptodate, "uptodate")

        # --- updates list view ----------------------------------------------
        self.stack.add_named(self._build_updates_view(), "updates")

        self.stack.set_visible_child_name("start")

    # ------------------------------------------------------------------
    def _build_updates_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)

        self.updates_banner = Adw.Banner(revealed=True)
        box.append(self.updates_banner)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.updates_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=6, margin_bottom=6)
        scroll.set_child(self.updates_container)
        box.append(scroll)

        # advanced options
        advanced = Gtk.Expander(label=_("Advanced options"))
        adv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          margin_top=6)

        allow_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        allow_label = Gtk.Label(
            label=_("Allow erasing conflicting packages (--allowerasing)"),
            xalign=0, hexpand=True, wrap=True)
        self.allowerasing_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        allow_row.append(allow_label)
        allow_row.append(self.allowerasing_switch)
        adv_box.append(allow_row)

        excl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        excl_label = Gtk.Label(
            label=_("Exclude packages (comma separated)"),
            xalign=0)
        self.exclude_entry = Gtk.Entry(hexpand=True,
                                       placeholder_text="kernel-desktop, foo")
        excl_row.append(excl_label)
        excl_row.append(self.exclude_entry)
        adv_box.append(excl_row)

        advanced.set_child(adv_box)
        box.append(advanced)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             spacing=6, halign=Gtk.Align.END)
        recheck = Gtk.Button(label=_("Check again"))
        recheck.connect("clicked", self._on_check_clicked)
        button_row.append(recheck)
        self.update_button = Gtk.Button(label=_("Update System"))
        self.update_button.add_css_class("suggested-action")
        self.update_button.connect("clicked", self._on_update_clicked)
        button_row.append(self.update_button)
        box.append(button_row)
        return box

    # ------------------------------------------------------------------
    def _on_check_clicked(self, *args):
        self.stack.set_visible_child_name("checking")
        threading.Thread(target=self._check_worker,
                         args=(self._current_args(),), daemon=True).start()

    def _check_worker(self, args):
        # always start from a clean slate: dnf clean all + fresh metadata
        ok, err = backend.clean_and_refresh()
        if not ok:
            GLib.idle_add(self._check_failed, err)
            return
        items, summary, error = backend.preview_transaction(args)
        if error:
            GLib.idle_add(self._check_failed, error)
            return
        versions = backend.installed_versions() if items else {}
        GLib.idle_add(self._check_done, items, versions)

    def _check_failed(self, error):
        self.stack.set_visible_child_name("start")
        self._show_error_dialog(error)
        return False

    def _check_done(self, items, versions):
        # "obsoleted" entries duplicate packages already listed for
        # upgrade/replacement and would be misleading - hide them.
        # Genuine removals (e.g. via --allowerasing) come from the
        # "Removing:" sections and keep the "remove" action.
        items = [i for i in items if i.action != "obsoleted"]
        if not items:
            self.stack.set_visible_child_name("uptodate")
            return False
        self._updates = items
        self.updates_banner.set_title(
            _("{0} packages will be changed").format(len(items)))

        child = self.updates_container.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.updates_container.remove(child)
            child = nxt

        grouped = {}
        for item in items:
            grouped.setdefault(item.action, []).append(item)

        for action, label in SECTION_ORDER:
            section_items = grouped.pop(action, [])
            if not section_items:
                continue
            self._append_section(label, action, section_items, versions)
        # any unknown sections dnf might print in the future
        for action, section_items in grouped.items():
            self._append_section(action, action, section_items, versions)

        self.stack.set_visible_child_name("updates")
        return False

    def _append_section(self, label, action, section_items, versions):
        header = Gtk.Label(
            label=f"{_(label)} ({len(section_items)})", xalign=0)
        header.add_css_class("heading")
        self.updates_container.append(header)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for item in section_items:
            listbox.append(self._make_row(item, action, versions))
        self.updates_container.append(listbox)

    def _make_row(self, item, action, versions):
        current = versions.get((item.name, item.arch))
        if current is None:
            # fall back to any architecture (e.g. noarch vs arch change)
            current = next((v for (n, _a), v in versions.items()
                            if n == item.name), None)

        if action in ("upgrade", "downgrade") and current:
            subtitle = f"{current}  →  {item.version}  •  {item.arch}"
        elif action in REMOVAL_ACTIONS:
            subtitle = f"{current or item.version}  •  {item.arch}"
        elif current and current != item.version:
            subtitle = f"{current}  →  {item.version}  •  {item.arch}"
        else:
            subtitle = f"{item.version}  •  {item.arch}"

        row = Adw.ActionRow(title=item.name, subtitle=subtitle)
        if item.size:
            size_label = Gtk.Label(label=item.size, valign=Gtk.Align.CENTER)
            size_label.add_css_class("dim-label")
            row.add_suffix(size_label)
        if item.repo:
            repo_label = Gtk.Label(label=item.repo, valign=Gtk.Align.CENTER)
            repo_label.add_css_class("dim-label")
            repo_label.set_ellipsize(Pango.EllipsizeMode.END)
            row.add_suffix(repo_label)
        if action in REMOVAL_ACTIONS:
            icon = Gtk.Image.new_from_icon_name("user-trash-symbolic")
            icon.add_css_class("error")
            row.add_prefix(icon)
        elif action.startswith("install") or action == "obsolete":
            icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
            icon.add_css_class("success")
            row.add_prefix(icon)
        return row

    # ------------------------------------------------------------------
    def _show_error_dialog(self, error_text):
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading=_("Failed to check for updates"),
            body=_("dnf reported the following problem:"),
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        scroll = Gtk.ScrolledWindow(min_content_height=220,
                                    min_content_width=560,
                                    hexpand=True, vexpand=True)
        scroll.add_css_class("card")
        view = Gtk.TextView(editable=False, cursor_visible=False,
                            monospace=True,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            left_margin=8, right_margin=8,
                            top_margin=8, bottom_margin=8)
        view.get_buffer().set_text(error_text)
        scroll.set_child(view)
        box.append(scroll)

        copy_btn = Gtk.Button(label=_("Copy log"), halign=Gtk.Align.END)

        def on_copy(*_a):
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(error_text)
                self.window.show_toast(_("Log copied to clipboard"))

        copy_btn.connect("clicked", on_copy)
        box.append(copy_btn)

        # quick access to the options that usually resolve such problems
        allow_check = Gtk.CheckButton(
            label=_("Allow erasing conflicting packages (--allowerasing)"))
        allow_check.set_active(self.allowerasing_switch.get_active())
        box.append(allow_check)

        excl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=12)
        excl_row.append(Gtk.Label(
            label=_("Exclude packages (comma separated)"), xalign=0))
        excl_entry = Gtk.Entry(hexpand=True,
                               text=self.exclude_entry.get_text(),
                               placeholder_text="kernel-desktop, foo")
        excl_row.append(excl_entry)
        box.append(excl_row)

        dialog.set_extra_child(box)
        dialog.add_response("close", _("Close"))
        dialog.add_response("retry", _("Try again"))
        dialog.set_response_appearance("retry",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("retry")

        def on_response(_d, response):
            if response != "retry":
                return
            # carry the chosen options over to the advanced options
            self.allowerasing_switch.set_active(allow_check.get_active())
            self.exclude_entry.set_text(excl_entry.get_text())
            self._on_check_clicked()

        dialog.connect("response", on_response)
        dialog.present()

    # ------------------------------------------------------------------
    def _current_args(self, refresh=False):
        excludes = [p.strip() for p in
                    self.exclude_entry.get_text().split(",") if p.strip()]
        return backend.build_distro_sync_args(
            allowerasing=self.allowerasing_switch.get_active(),
            excludes=excludes,
            refresh=refresh,
        )

    def _on_update_clicked(self, *args):
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading=_("Update the system?"),
            body=_("The system will be updated using \"dnf {0}\". "
                   "Do you want to continue?").format(
                       " ".join(self._current_args(refresh=True))),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("update", _("Update"))
        dialog.set_response_appearance("update",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("update")
        dialog.connect("response", self._on_confirm_response)
        dialog.present()

    def _on_confirm_response(self, dialog, response):
        if response != "update":
            return
        # The update check runs against the user's metadata cache, but the
        # privileged transaction uses root's own cache which may be stale
        # ("Nothing to do"). Always refresh so both see the same versions.
        TransactionDialog(
            self.window, _("Updating system"),
            self._current_args(refresh=True),
            on_done=self._on_transaction_done,
        ).present()

    def _on_transaction_done(self, success):
        if success:
            self.window.show_toast(_("System updated successfully"))
            self.stack.set_visible_child_name("start")
        else:
            self.window.show_toast(_("System update failed"))
