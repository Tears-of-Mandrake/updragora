"""Shared transaction progress dialog.

Used by the update, install and remove pages so the whole application
behaves and looks consistent while dnf is working.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

from updragora.core.backend import TransactionRunner
from updragora.core.i18n import _

ACTION_LABELS = {
    "Downloading": "Downloading",
    "Installing": "Installing",
    "Upgrading": "Upgrading",
    "Downgrading": "Downgrading",
    "Reinstalling": "Reinstalling",
    "Removing": "Removing",
    "Erasing": "Removing",
    "Cleanup": "Cleaning up",
    "Verifying": "Verifying",
    "Running scriptlet": "Running scriptlet",
    "Running dracut": "Running dracut",
    "Preparing": "Preparing",
    "Checking transaction": "Checking transaction",
    "Testing transaction": "Testing transaction",
    "Running transaction": "Running transaction",
    "Importing GPG key": "Importing GPG key",
    "Complete": "Complete",
}


def format_eta(seconds):
    if seconds is None:
        return _("estimating…")
    if seconds < 60:
        return _("about {0} s left").format(max(seconds, 1))
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return _("about {0} min {1} s left").format(minutes, secs)
    hours, minutes = divmod(minutes, 60)
    return _("about {0} h {1} min left").format(hours, minutes)


class TransactionDialog(Adw.Window):
    """Modal window showing live progress of a dnf transaction."""

    def __init__(self, parent, title, dnf_args, on_done=None):
        super().__init__(transient_for=parent, modal=True)
        self.set_title(title)
        self.set_default_size(640, 420)
        self.set_hide_on_close(False)
        self._on_done = on_done
        self._finished = False
        self._success = False
        self._log_text = ""

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=18,
                      margin_start=18, margin_end=18)

        self.status_label = Gtk.Label(label=_("Starting…"), xalign=0)
        self.status_label.add_css_class("title-4")
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(self.status_label)

        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_text("0%")
        box.append(self.progress)

        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.eta_label = Gtk.Label(label=format_eta(None), xalign=0)
        self.eta_label.add_css_class("dim-label")
        info_box.append(self.eta_label)
        self.speed_label = Gtk.Label(label="", xalign=1, hexpand=True)
        self.speed_label.add_css_class("dim-label")
        info_box.append(self.speed_label)
        box.append(info_box)

        # Details expander with raw dnf output (exactly what the
        # terminal would show)
        self.details_expander = Gtk.Expander(label=_("Show details"))
        details_scroll = Gtk.ScrolledWindow(min_content_height=180,
                                            vexpand=True)
        self.details_view = Gtk.TextView(editable=False, monospace=True,
                                         cursor_visible=False)
        self.details_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        details_scroll.set_child(self.details_view)
        self.details_expander.set_child(details_scroll)
        box.append(self.details_expander)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             spacing=6, halign=Gtk.Align.END)
        self.cancel_button = Gtk.Button(label=_("Cancel"))
        self.cancel_button.add_css_class("destructive-action")
        self.cancel_button.connect("clicked", self._on_cancel)
        button_box.append(self.cancel_button)

        self.close_button = Gtk.Button(label=_("Close"), visible=False)
        self.close_button.connect("clicked", lambda *a: self.close())
        button_box.append(self.close_button)

        self.summary_button = Gtk.Button(label=_("Show summary"),
                                         visible=False)
        self.summary_button.add_css_class("suggested-action")
        self.summary_button.connect("clicked", self._on_show_summary)
        button_box.append(self.summary_button)
        box.append(button_box)

        toolbar.set_content(box)
        self.set_content(toolbar)
        self.connect("close-request", self._on_close_request)

        self._runner = TransactionRunner(
            dnf_args,
            on_event=lambda ev: GLib.idle_add(self._handle_event, ev),
            on_finished=lambda ok, log: GLib.idle_add(self._handle_finished, ok, log),
        )
        self._runner.start()

    # -- callbacks -----------------------------------------------------------

    def _on_close_request(self, *args):
        if not self._finished:
            # do not allow closing mid-transaction; use Cancel instead
            return True
        return False

    def _on_cancel(self, *args):
        self.cancel_button.set_sensitive(False)
        self._runner.cancel()

    def _handle_event(self, ev):
        if ev.fraction is not None:
            self.progress.set_fraction(min(ev.fraction, 1.0))
            self.progress.set_text(f"{int(min(ev.fraction, 1.0) * 100)}%")
            self.eta_label.set_label(format_eta(ev.eta_seconds))
        if ev.action:
            label = _(ACTION_LABELS.get(ev.action, ev.action))
            if ev.package:
                self.status_label.set_label(f"{label}: {ev.package}")
            else:
                self.status_label.set_label(label)
        if ev.download_speed or ev.downloaded:
            self.speed_label.set_label(
                f"{ev.downloaded}  •  {ev.download_speed}")
        elif ev.phase == "transaction":
            self.speed_label.set_label("")
        if ev.detail_line:
            buf = self.details_view.get_buffer()
            buf.insert(buf.get_end_iter(), ev.detail_line + "\n")
            mark = buf.create_mark(None, buf.get_end_iter(), False)
            self.details_view.scroll_mark_onscreen(mark)
        return False

    def _handle_finished(self, success, log_text):
        self._finished = True
        self._success = success
        self._log_text = log_text
        self.cancel_button.set_visible(False)
        self.close_button.set_visible(True)
        self.summary_button.set_visible(True)
        if success:
            self.progress.set_fraction(1.0)
            self.progress.set_text("100%")
            self.status_label.set_label(_("Transaction completed successfully"))
            self.eta_label.set_label(_("Done"))
            self.summary_button.set_label(_("Show summary"))
        else:
            self.status_label.set_label(_("Transaction failed"))
            self.eta_label.set_label(_("An error occurred"))
            self.summary_button.set_label(_("Show error details"))
        self.speed_label.set_label("")
        if self._on_done:
            self._on_done(success)
        return False

    def _on_show_summary(self, *args):
        title = (_("Transaction summary") if self._success
                 else _("What went wrong"))
        dialog = Adw.Window(transient_for=self, modal=True, title=title)
        dialog.set_default_size(680, 480)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        scroll = Gtk.ScrolledWindow(vexpand=True)
        view = Gtk.TextView(editable=False, monospace=True,
                            cursor_visible=False,
                            top_margin=12, bottom_margin=12,
                            left_margin=12, right_margin=12)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text = self._extract_summary() if self._success else self._extract_errors()
        view.get_buffer().set_text(text)
        scroll.set_child(view)
        toolbar.set_content(scroll)
        dialog.set_content(toolbar)
        dialog.present()

    # -- summary helpers ------------------------------------------------------

    def _extract_summary(self):
        """Pull the transaction table + Installed/Upgraded/Removed section."""
        lines = self._log_text.splitlines()
        out = []
        capture = False
        for line in lines:
            s = line.strip()
            if s.startswith(("Installing:", "Upgrading:", "Removing:",
                             "Downgrading:", "Installing dependencies:",
                             "Removing dependent packages:",
                             "Transaction Summary")):
                capture = True
            if s.startswith(("Downloading Packages", "Running transaction")):
                capture = False
            if capture:
                out.append(line)
            if s.startswith(("Installed:", "Upgraded:", "Removed:",
                             "Downgraded:", "Complete!")):
                capture = True
                out.append(line)
        return "\n".join(out).strip() or self._log_text

    def _extract_errors(self):
        lines = self._log_text.splitlines()
        errors = [l for l in lines if any(
            key in l for key in ("Error", "error:", "Failed", "failed",
                                 "Problem", "conflicts", "nothing provides"))]
        if errors:
            return "\n".join(errors) + "\n\n" + "-" * 40 + "\n" + self._log_text
        return self._log_text
