"""Shared page for installing / removing packages.

Presents a searchable table of packages (name, version, architecture,
size, summary, repository, release) with checkboxes, in the spirit of
rpmdrake / dnfdragora / yumex-ng.
"""

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from updragora.core import backend
from updragora.core.backend import human_size
from updragora.core.i18n import _
from updragora.gtk.transaction_dialog import TransactionDialog


class PackageItem(GObject.Object):
    __gtype_name__ = "UPDragoraPackageItem"

    selected = GObject.Property(type=bool, default=False)

    def __init__(self, pkg):
        super().__init__()
        self.pkg = pkg


class PackagesPage(Gtk.Box):
    """mode: "install" (available packages) or "remove" (installed)."""

    def __init__(self, window, mode):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         margin_top=12, margin_bottom=12,
                         margin_start=12, margin_end=12)
        self.window = window
        self.mode = mode
        self._loaded = False
        self._all_items = []

        # --- top bar: search + refresh -----------------------------------
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_entry = Gtk.SearchEntry(
            hexpand=True,
            placeholder_text=_("Search packages…"))
        self.search_entry.connect("search-changed", self._on_search_changed)
        top.append(self.search_entry)
        self.hide_installed_btn = None
        self.only_updates_btn = None
        if mode == "install":
            self.only_updates_btn = Gtk.ToggleButton(
                icon_name="software-update-available-symbolic",
                tooltip_text=_("Show only packages with updates"))
            self.only_updates_btn.connect(
                "toggled", self._on_search_changed)
            top.append(self.only_updates_btn)
            self.hide_installed_btn = Gtk.ToggleButton(
                icon_name="view-conceal-symbolic",
                tooltip_text=_("Hide installed packages"))
            self.hide_installed_btn.connect(
                "toggled", self._on_search_changed)
            top.append(self.hide_installed_btn)
            refresh_repos_btn = Gtk.Button(
                icon_name="emblem-synchronizing-symbolic",
                tooltip_text=_("Refresh repositories (--refresh) and "
                               "reload the package list"))
            refresh_repos_btn.connect("clicked", self._on_refresh_repos)
            top.append(refresh_repos_btn)
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                 tooltip_text=_("Reload package list"))
        refresh_btn.connect("clicked", lambda *a: self.load_packages(force=True))
        top.append(refresh_btn)
        self.append(top)

        # --- stack: loading / table --------------------------------------
        self.stack = Gtk.Stack(vexpand=True,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.append(self.stack)

        loading_desc = (_("The first load can take a few minutes while "
                          "repository metadata is processed. Subsequent "
                          "loads use a cache and are much faster.")
                        if mode == "install" else _("This may take a moment."))
        loading = Adw.StatusPage(title=_("Loading package list…"),
                                 description=loading_desc)
        spinner = Gtk.Spinner(spinning=True, width_request=32,
                              height_request=32, halign=Gtk.Align.CENTER)
        loading.set_child(spinner)
        self.stack.add_named(loading, "loading")

        self._build_table()
        self.stack.add_named(self._table_scroll, "table")

        empty = Adw.StatusPage(icon_name="system-search-symbolic",
                               title=_("No packages found"))
        self.stack.add_named(empty, "empty")

        # --- bottom action bar -------------------------------------------
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.selection_label = Gtk.Label(label="", xalign=0, hexpand=True)
        self.selection_label.add_css_class("dim-label")
        bottom.append(self.selection_label)
        clear_btn = Gtk.Button(label=_("Clear selection"))
        clear_btn.connect("clicked", self._on_clear_selection)
        bottom.append(clear_btn)
        if mode == "install":
            self.action_button = Gtk.Button(label=_("Install / Update Selected"))
            self.action_button.add_css_class("suggested-action")
        else:
            self.action_button = Gtk.Button(label=_("Remove Selected"))
            self.action_button.add_css_class("destructive-action")
        self.action_button.set_sensitive(False)
        self.action_button.connect("clicked", self._on_action_clicked)
        bottom.append(self.action_button)
        self.append(bottom)

        self.stack.set_visible_child_name("loading")

    # ------------------------------------------------------------------
    def _build_table(self):
        self.store = Gio.ListStore(item_type=PackageItem)
        self.filter = Gtk.CustomFilter.new(self._filter_func, None)
        self.filter_model = Gtk.FilterListModel(model=self.store,
                                                filter=self.filter)
        selection = Gtk.NoSelection(model=self.filter_model)

        self.column_view = Gtk.ColumnView(model=selection,
                                          show_row_separators=True)
        self.column_view.add_css_class("data-table")

        self._add_check_column()
        self._add_text_column(_("Package"), lambda p: p.name, expand=True)
        if self.mode == "install":
            self._add_installed_column()
        self._add_text_column(_("Version"), self._version_text)
        self._add_text_column(_("Architecture"), lambda p: p.arch)
        self._add_text_column(_("Size"), lambda p: human_size(p.size))
        self._add_text_column(_("Summary"), lambda p: p.summary, expand=True)
        self._add_text_column(_("Repository"), lambda p: p.repo)
        self._add_text_column(_("Release"), lambda p: p.release)

        self._table_scroll = Gtk.ScrolledWindow(vexpand=True)
        self._table_scroll.add_css_class("card")
        self._table_scroll.set_child(self.column_view)

    def _add_check_column(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_f, list_item):
            check = Gtk.CheckButton(halign=Gtk.Align.CENTER)
            list_item.set_child(check)

        def bind(_f, list_item):
            item = list_item.get_item()
            check = list_item.get_child()
            check.set_active(item.selected)
            # installed packages can only be selected when an update
            # is available (selective update)
            check.set_sensitive(not (self.mode == "install"
                                     and item.pkg.installed
                                     and not item.pkg.upgradable))
            handler = check.connect("toggled", self._on_check_toggled, item)
            check._handler = handler

        def unbind(_f, list_item):
            check = list_item.get_child()
            if getattr(check, "_handler", None):
                check.disconnect(check._handler)
                check._handler = None

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        col = Gtk.ColumnViewColumn(title="", factory=factory)
        col.set_fixed_width(40)
        self.column_view.append_column(col)

    def _add_installed_column(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_f, list_item):
            image = Gtk.Image(halign=Gtk.Align.CENTER)
            list_item.set_child(image)

        def bind(_f, list_item):
            item = list_item.get_item()
            image = list_item.get_child()
            image.remove_css_class("accent")
            image.remove_css_class("dim-label")
            if item.pkg.upgradable:
                image.set_from_icon_name("software-update-available-symbolic")
                image.add_css_class("accent")
                image.set_tooltip_text(_("Update available"))
            elif item.pkg.installed:
                image.set_from_icon_name("object-select-symbolic")
                image.add_css_class("dim-label")
                image.set_tooltip_text(_("Installed"))
            else:
                image.set_from_icon_name(None)
                image.set_tooltip_text(None)

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        col = Gtk.ColumnViewColumn(title=_("Status"), factory=factory)
        col.set_resizable(True)
        self.column_view.append_column(col)

    @staticmethod
    def _version_text(pkg):
        if pkg.upgradable and pkg.installed_version:
            return f"{pkg.installed_version} → {pkg.version}"
        return pkg.version

    def _add_text_column(self, title, getter, expand=False):
        factory = Gtk.SignalListItemFactory()

        def setup(_f, list_item):
            label = Gtk.Label(xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            list_item.set_child(label)

        def bind(_f, list_item):
            item = list_item.get_item()
            list_item.get_child().set_label(getter(item.pkg) or "")

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        col = Gtk.ColumnViewColumn(title=title, factory=factory)
        col.set_expand(expand)
        col.set_resizable(True)
        self.column_view.append_column(col)

    # ------------------------------------------------------------------
    def _filter_func(self, item, _data):
        pkg = item.pkg
        if (self.only_updates_btn is not None
                and self.only_updates_btn.get_active()
                and not pkg.upgradable):
            return False
        # hide installed packages, but keep the ones with pending
        # updates - those are still actionable
        if (self.hide_installed_btn is not None
                and self.hide_installed_btn.get_active()
                and pkg.installed and not pkg.upgradable):
            return False
        text = self.search_entry.get_text().strip().lower()
        if not text:
            return True
        return (text in pkg.name.lower()
                or text in (pkg.summary or "").lower())

    def _on_search_changed(self, *args):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        if self._loaded:
            name = "table" if self.filter_model.get_n_items() else "empty"
            self.stack.set_visible_child_name(name)

    # ------------------------------------------------------------------
    def load_packages(self, force=False):
        if self._loaded and not force:
            return
        self._loaded = False
        self.stack.set_visible_child_name("loading")
        threading.Thread(target=self._load_worker, args=(force,),
                         daemon=True).start()

    def _load_worker(self, force=False):
        try:
            if self.mode == "install":
                packages = backend.list_available_packages(
                    allow_cache=not force)
                versions = backend.installed_versions()
                name_version = {}
                for (name, _arch), evr in versions.items():
                    name_version.setdefault(name, evr)
                upgrades = backend.upgradable_names()
                for pkg in packages:
                    pkg.installed = pkg.name in name_version
                    if pkg.installed:
                        pkg.installed_version = versions.get(
                            (pkg.name, pkg.arch),
                            name_version[pkg.name])
                        pkg.upgradable = pkg.name in upgrades
            else:
                packages = backend.list_installed_packages()
            error = None
        except Exception as exc:  # noqa: BLE001
            packages, error = [], str(exc)
        GLib.idle_add(self._load_done, packages, error)

    def _load_done(self, packages, error):
        if error:
            self.window.show_toast(_("Failed to load packages: {0}")
                                   .format(error))
        self._all_items = [PackageItem(pkg) for pkg in packages]
        # splice() inserts everything in one signal emission - orders of
        # magnitude faster than appending tens of thousands of items
        self.store.splice(0, self.store.get_n_items(), self._all_items)
        self._loaded = True
        self._update_selection_label()
        name = "table" if self.filter_model.get_n_items() else "empty"
        self.stack.set_visible_child_name(name)
        return False

    # ------------------------------------------------------------------
    def _selected_names(self):
        return [it.pkg.name for it in self._all_items if it.selected]

    def _on_check_toggled(self, check, item):
        item.selected = check.get_active()
        self._update_selection_label()
        # auto-select the dependencies the package will pull in
        if (self.mode == "install" and item.selected
                and not item.pkg.installed):
            threading.Thread(target=self._deps_worker,
                             args=(item.pkg.name,), daemon=True).start()

    def _deps_worker(self, name):
        items, _summary, error = backend.preview_transaction(
            ["install", name])
        if error:
            return
        deps = {i.name for i in items
                if i.action.startswith("install") and i.name != name}
        if deps:
            GLib.idle_add(self._select_deps, name, deps)

    def _select_deps(self, name, deps):
        changed = 0
        for it in self._all_items:
            if (it.pkg.name in deps and not it.selected
                    and not it.pkg.installed):
                it.selected = True
                changed += 1
        if changed:
            # force visible rows to rebind their check buttons
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
            self._update_selection_label()
            self.window.show_toast(
                _("Also selected {0} dependencies of {1}")
                .format(changed, name))
        return False

    def _on_refresh_repos(self, *args):
        self.stack.set_visible_child_name("loading")
        threading.Thread(target=self._refresh_repos_worker,
                         daemon=True).start()

    def _refresh_repos_worker(self):
        ok, err = backend.refresh_metadata()
        if not ok:
            GLib.idle_add(
                self.window.show_toast,
                _("Failed to refresh repositories: {0}").format(err))
        self._load_worker(force=True)

    def _on_clear_selection(self, *args):
        for it in self._all_items:
            it.selected = False
        # rebind visible rows
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_selection_label()

    def _update_selection_label(self):
        count = len(self._selected_names())
        self.selection_label.set_label(
            _("{0} packages selected").format(count))
        self.action_button.set_sensitive(count > 0)

    # ------------------------------------------------------------------
    def _on_action_clicked(self, *args):
        selected = [it.pkg for it in self._all_items if it.selected]
        if not selected:
            return
        if self.mode == "install":
            installs = [p.name for p in selected if not p.upgradable]
            upgrades = [p.name for p in selected if p.upgradable]
            dnf_args = backend.build_install_args(installs, upgrades)
            heading = _("Install packages?")
        else:
            dnf_args = backend.build_remove_args(
                [p.name for p in selected])
            heading = _("Remove packages?")

        # Resolve the transaction (shows dependencies) in the background
        self.action_button.set_sensitive(False)
        self.window.show_toast(_("Resolving transaction…"))
        threading.Thread(target=self._resolve_worker,
                         args=(dnf_args, heading), daemon=True).start()

    def _resolve_worker(self, dnf_args, heading):
        summary, error = backend.resolve_transaction(list(dnf_args))
        GLib.idle_add(self._resolve_done, dnf_args, heading, summary, error)

    def _resolve_done(self, dnf_args, heading, summary, error):
        self.action_button.set_sensitive(True)
        if error:
            self.window.show_toast(error)
            return False
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading=heading,
            body=_("The following transaction will be executed:"),
        )
        scroll = Gtk.ScrolledWindow(min_content_height=220,
                                    min_content_width=560)
        view = Gtk.TextView(editable=False, monospace=True,
                            cursor_visible=False)
        view.get_buffer().set_text(summary)
        scroll.set_child(view)
        dialog.set_extra_child(scroll)
        dialog.add_response("cancel", _("Cancel"))
        confirm_label = (_("Install") if self.mode == "install"
                         else _("Remove"))
        dialog.add_response("confirm", confirm_label)
        dialog.set_response_appearance(
            "confirm",
            Adw.ResponseAppearance.SUGGESTED if self.mode == "install"
            else Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_confirm_response, dnf_args)
        dialog.present()
        return False

    def _on_confirm_response(self, dialog, response, dnf_args):
        if response != "confirm":
            return
        title = (_("Installing packages") if self.mode == "install"
                 else _("Removing packages"))
        TransactionDialog(self.window, title, dnf_args,
                          on_done=self._on_transaction_done).present()

    def _on_transaction_done(self, success):
        if success:
            self.window.show_toast(_("Transaction completed successfully"))
            # reload from cache; installed state is recomputed via rpm
            self._loaded = False
            self.load_packages()
        else:
            self.window.show_toast(_("Transaction failed"))
