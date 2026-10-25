"""DNF backend for UPDragora.

Toolkit-agnostic: all long operations run in worker threads and report
back through plain callbacks. GUI layers are responsible for
marshalling callbacks onto their main loop (e.g. GLib.idle_add).

Privileged operations are executed through ``pkexec`` and the
``updragora-helper`` script (see data/updragora-helper) which is
whitelisted by a polkit policy with ``auth_admin_keep`` so the user is
not asked for the password on every action.
"""

import os
import pty
import re
import select
import shutil
import subprocess
import threading
import time

# ---------------------------------------------------------------------------
# Helpers / command locations
# ---------------------------------------------------------------------------

HELPER_CANDIDATES = [
    "/usr/libexec/updragora/updragora-helper",
    "/usr/local/libexec/updragora/updragora-helper",
    # development fallback (repository checkout)
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "data", "updragora-helper"),
]


def find_helper():
    for path in HELPER_CANDIDATES:
        if os.access(path, os.X_OK):
            return path
    return None


def privileged_cmd(dnf_args):
    """Build a privileged dnf command line (pkexec + helper)."""
    helper = find_helper()
    if helper:
        return ["pkexec", helper] + dnf_args
    # Fallback: pkexec dnf directly (will prompt each time without policy)
    return ["pkexec", "dnf"] + dnf_args


CLEAN_ENV = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")


def release_from_repo(repo_id):
    """Guess the OpenMandriva release a repository belongs to."""
    rid = (repo_id or "").lower()
    for release in ("cooker", "rolling", "rock"):
        if release in rid:
            return release
    return "none"


def human_size(num):
    try:
        num = float(num)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return ""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class Package:
    __slots__ = ("name", "version", "arch", "size", "summary", "repo",
                 "release", "installed", "installed_version", "upgradable")

    def __init__(self, name, version, arch, size, summary, repo,
                 installed=False):
        self.name = name
        self.version = version
        self.arch = arch
        self.size = size
        self.summary = summary
        self.repo = repo
        self.release = release_from_repo(repo)
        self.installed = installed
        self.installed_version = ""
        self.upgradable = False


class UpdateInfo:
    __slots__ = ("name", "arch", "version", "repo")

    def __init__(self, name, arch, version, repo):
        self.name = name
        self.arch = arch
        self.version = version
        self.repo = repo


# ---------------------------------------------------------------------------
# Non-privileged queries (run as normal user)
# ---------------------------------------------------------------------------

QUERYFORMAT = "%{name}\t%{evr}\t%{arch}\t%{size}\t%{reponame}\t%{summary}\n"

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "updragora")
AVAILABLE_CACHE = os.path.join(CACHE_DIR, "available-packages.tsv")
CACHE_MAX_AGE = 6 * 3600  # seconds


def _run_query(args, timeout=600):
    proc = subprocess.run(
        ["dnf", "-q"] + args,
        capture_output=True, text=True, env=CLEAN_ENV, timeout=timeout,
    )
    return proc


def list_installed_names():
    """Return a set of installed package names (fast, uses rpm)."""
    try:
        proc = subprocess.run(
            ["rpm", "-qa", "--qf", "%{NAME}\n"],
            capture_output=True, text=True, env=CLEAN_ENV, timeout=120,
        )
        return set(proc.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return set()


def upgradable_names():
    """Return {name: new_version} of packages with a pending update."""
    updates, error = check_updates(refresh=False)
    if error:
        return {}
    return {u.name: u.version for u in updates}


def _cache_is_fresh():
    try:
        return (time.time() - os.path.getmtime(AVAILABLE_CACHE)) < CACHE_MAX_AGE
    except OSError:
        return False


def _write_cache(text):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(AVAILABLE_CACHE, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def list_available_packages(allow_cache=True):
    """Return list of Package available in enabled repositories.

    Results are cached on disk for CACHE_MAX_AGE seconds because the
    repoquery can take a long time on large repositories. Pass
    ``allow_cache=False`` to force a fresh query.
    """
    if allow_cache and _cache_is_fresh():
        try:
            with open(AVAILABLE_CACHE, "r", encoding="utf-8") as f:
                packages = _parse_package_lines(f.read())
            if packages:
                return packages
        except OSError:
            pass
    proc = _run_query(["repoquery", "--available", "--latest-limit=1",
                       "--qf", QUERYFORMAT])
    if proc.returncode == 0 and proc.stdout.strip():
        _write_cache(proc.stdout)
    return _parse_package_lines(proc.stdout)


def list_installed_packages():
    """Return list of Package installed on the system."""
    proc = _run_query(["repoquery", "--installed", "--qf", QUERYFORMAT])
    return _parse_package_lines(proc.stdout)


def _parse_package_lines(text):
    packages = []
    for line in text.splitlines():
        parts = line.split("\t", 5)
        if len(parts) != 6:
            continue
        name, evr, arch, size, repo, summary = parts
        if not name:
            continue
        packages.append(Package(name, evr, arch, size, summary.strip(), repo))
    return packages


def check_updates(refresh=True):
    """Return (list[UpdateInfo], error_message_or_None).

    Uses ``dnf check-update`` which works without root privileges.
    """
    args = ["check-update"]
    if refresh:
        args.insert(0, "--refresh")
    try:
        proc = subprocess.run(
            ["dnf", "-q"] + args,
            capture_output=True, text=True, env=CLEAN_ENV, timeout=900,
        )
    except subprocess.TimeoutExpired:
        return [], "Timed out while checking for updates."
    # exit code: 0 = no updates, 100 = updates available, 1 = error
    if proc.returncode not in (0, 100):
        return [], (proc.stderr or proc.stdout or "dnf failed").strip()
    updates = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Obsoleting") or line.startswith("Security:"):
            continue
        parts = line.split()
        if len(parts) < 3 or "." not in parts[0]:
            continue
        name_arch, version, repo = parts[0], parts[1], parts[2]
        name, _, arch = name_arch.rpartition(".")
        if not name:
            continue
        updates.append(UpdateInfo(name, arch, version, repo))
    return updates, None


# ---------------------------------------------------------------------------
# Transaction preview (resolution)
# ---------------------------------------------------------------------------

class TransactionItem:
    """One row of a resolved dnf transaction table."""

    __slots__ = ("action", "name", "arch", "version", "repo", "size")

    def __init__(self, action, name, arch, version, repo="", size=""):
        self.action = action    # see TRANS_SECTIONS values
        self.name = name
        self.arch = arch
        self.version = version
        self.repo = repo
        self.size = size


# section header in dnf output -> action key
TRANS_SECTIONS = {
    "Installing:": "install",
    "Installing dependencies:": "install-dep",
    "Installing weak dependencies:": "install-weak",
    "Upgrading:": "upgrade",
    "Downgrading:": "downgrade",
    "Reinstalling:": "reinstall",
    "Removing:": "remove",
    "Removing dependent packages:": "remove",
    "Removing unused dependencies:": "remove",
    "Obsoleting:": "obsolete",
}


def installed_versions():
    """Return {(name, arch): evr} for all installed packages (fast rpm)."""
    versions = {}
    try:
        proc = subprocess.run(
            ["rpm", "-qa", "--qf", "%{NAME}\t%{ARCH}\t%{EVR}\n"],
            capture_output=True, text=True, env=CLEAN_ENV, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return versions
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            versions[(parts[0], parts[1])] = parts[2]
    return versions


def _parse_transaction_table(text):
    """Parse the dnf transaction table into TransactionItem objects."""
    items = []
    action = None
    pending_name = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            pending_name = None
            continue
        if stripped in TRANS_SECTIONS:
            action = TRANS_SECTIONS[stripped]
            pending_name = None
            continue
        if stripped.startswith(("Transaction Summary", "=====", "-----")):
            action = None
            pending_name = None
            continue
        if action is None:
            continue
        # "replacing  foo.x86_64 1.0-1" - the package being obsoleted
        if stripped.startswith("replacing"):
            tokens = stripped.split()
            if len(tokens) >= 2:
                name, _, arch = tokens[1].rpartition(".")
                version = tokens[2] if len(tokens) > 2 else ""
                items.append(TransactionItem(
                    "obsoleted", name or tokens[1], arch, version))
            continue
        # header line of the table itself
        if stripped.startswith("Package") and "Arch" in stripped:
            continue
        tokens = stripped.split()
        # very long package names wrap: name alone, details on next line
        if len(tokens) == 1:
            pending_name = tokens[0]
            continue
        if pending_name is not None and len(tokens) >= 3:
            tokens = [pending_name] + tokens
            pending_name = None
        if len(tokens) < 4:
            continue
        name, arch, version, repo = tokens[0], tokens[1], tokens[2], tokens[3]
        size = " ".join(tokens[4:6]) if len(tokens) > 4 else ""
        items.append(TransactionItem(action, name, arch, version, repo, size))
    return items


def preview_transaction(dnf_args):
    """Resolve a transaction without executing it.

    Returns (items, summary_text, error_message_or_None) where ``items``
    is a list of TransactionItem covering upgrades, new installations
    (including dependencies), removals and obsoleted packages.

    Tries an unprivileged ``dnf --assumeno`` run first (dnf resolves
    transactions fine as a regular user); falls back to the privileged
    helper when dnf insists on root.
    """
    try:
        proc = subprocess.run(
            ["dnf"] + list(dnf_args) + ["--assumeno"],
            capture_output=True, text=True, env=CLEAN_ENV, timeout=900,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return [], "", "Timed out while resolving the transaction."
    except FileNotFoundError:
        return [], "", "dnf was not found on this system."

    if "superuser privileges" in out or "Permission denied" in out:
        summary, error = resolve_transaction(list(dnf_args))
        if error:
            return [], "", error
        return _parse_transaction_table(summary), summary, None

    if "Nothing to do" in out:
        return [], "", None
    if "Transaction Summary" not in out:
        err = out.strip() or "Failed to resolve transaction"
        return [], "", err
    lines = [l for l in (proc.stdout or "").splitlines()
             if "Operation aborted" not in l]
    summary = "\n".join(lines).strip()
    return _parse_transaction_table(summary), summary, None


def resolve_transaction(dnf_args):
    """Run dnf with --assumeno to preview a transaction.

    Returns (summary_text, error_message_or_None). ``dnf_args`` is e.g.
    ["install", "foo", "bar"] or ["distro-sync"].
    """
    cmd = privileged_cmd(dnf_args + ["--assumeno"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=CLEAN_ENV, timeout=900)
    except subprocess.TimeoutExpired:
        return "", "Timed out while resolving the transaction."
    except FileNotFoundError:
        return "", "pkexec was not found on this system."
    out = proc.stdout or ""
    if "Operation aborted" in out or "Transaction Summary" in out:
        # Strip trailing "Operation aborted." noise
        lines = [l for l in out.splitlines() if "Operation aborted" not in l]
        return "\n".join(lines).strip(), None
    if proc.returncode == 126 or proc.returncode == 127:
        return "", "Authorization was cancelled."
    err = (proc.stderr or out or "Failed to resolve transaction").strip()
    return "", err


# ---------------------------------------------------------------------------
# Transaction runner with live progress parsing
# ---------------------------------------------------------------------------

# (12/345): package-1.0-1.x86_64.rpm   2.1 MB/s | 4.2 MB   00:02
RE_DOWNLOAD = re.compile(
    r"^\((\d+)/(\d+)\):\s+(\S+)\s+([\d.]+\s*\S+/s)\s*\|\s*([\d.]+\s*\S+)"
)
#   Installing       : package-1.0-1.x86_64        12/345
RE_TRANS_STEP = re.compile(
    r"^\s*(Installing|Upgrading|Downgrading|Reinstalling|Removing|Erasing|"
    r"Cleanup|Verifying|Running scriptlet|Preparing)\s*:?\s*(\S*)\s*(\d+)/(\d+)\s*$"
)
RE_TOTAL_DL = re.compile(r"^Total\s+")

# In a PTY dnf enables colors/cursor control - strip them before parsing
RE_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
RE_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f]")

# dnf5 style: "[ 12/345] Installing package-1.0-1.x86_64  42% | 5 MiB/s | ..."
#             "[  3/ 50] package-1.0-1.x86_64  100% | 2.1 MiB/s | 4.2 MiB | 00m02s"
RE_DNF5 = re.compile(r"^\[\s*(\d+)/\s*(\d+)\]\s+(.*)$")
DNF5_ACTIONS = (
    "Running scriptlet", "Installing", "Upgrading", "Downgrading",
    "Reinstalling", "Removing", "Erasing", "Cleanup", "Verify",
    "Verifying", "Prepare", "Preparing",
)
RE_DNF5_SPEED = re.compile(r"([\d.]+\s*[KMGT]?i?B/s)")
RE_DNF5_SIZE = re.compile(r"/s\s*\|\s*([\d.]+\s*[KMGT]?i?B)")


class TransactionEvent:
    """Progress snapshot passed to the GUI."""

    __slots__ = ("phase", "fraction", "eta_seconds", "action", "package",
                 "detail_line", "download_speed", "downloaded")

    def __init__(self, phase, fraction, eta_seconds, action, package,
                 detail_line, download_speed="", downloaded=""):
        self.phase = phase              # "download" | "transaction" | "other"
        self.fraction = fraction        # 0.0 - 1.0 or None (unknown)
        self.eta_seconds = eta_seconds  # int or None
        self.action = action            # e.g. "Installing", "Downloading"
        self.package = package          # current package name or ""
        self.detail_line = detail_line  # raw output line for the details view
        self.download_speed = download_speed
        self.downloaded = downloaded


class TransactionRunner:
    """Runs a privileged dnf transaction in a PTY and parses progress.

    Callbacks (called from a worker thread!):
      on_event(TransactionEvent)  - progress / raw output line
      on_finished(success: bool, log_text: str)
    """

    # weight of the download phase in the overall progress
    DOWNLOAD_WEIGHT = 0.4

    def __init__(self, dnf_args, on_event, on_finished):
        self.dnf_args = dnf_args
        self.on_event = on_event
        self.on_finished = on_finished
        self._proc = None
        self._cancelled = False
        self._start_time = None
        self._log = []

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass

    # -- internals ----------------------------------------------------------

    def _run(self):
        cmd = privileged_cmd(self.dnf_args + ["-y"])
        self._start_time = time.time()
        try:
            master, slave = pty.openpty()
        except OSError:
            master = slave = None
        try:
            if master is not None:
                self._proc = subprocess.Popen(
                    cmd, stdin=slave, stdout=slave, stderr=slave,
                    env=CLEAN_ENV, close_fds=True,
                )
                os.close(slave)
                self._read_pty(master)
            else:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, env=CLEAN_ENV,
                )
                for line in self._proc.stdout:
                    self._handle_line(line.rstrip("\n"))
        except FileNotFoundError:
            self._log.append("pkexec was not found on this system.")
            self.on_finished(False, "\n".join(self._log))
            return
        finally:
            if master is not None:
                try:
                    os.close(master)
                except OSError:
                    pass
        rc = self._proc.wait() if self._proc else 1
        success = (rc == 0) and not self._cancelled
        self.on_finished(success, "\n".join(self._log))

    def _read_pty(self, master):
        buf = b""
        while True:
            try:
                ready, _, _ = select.select([master], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready:
                if self._proc.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            # dnf uses \r for in-place progress updates - treat both as EOL
            while True:
                idx_n = buf.find(b"\n")
                idx_r = buf.find(b"\r")
                idxs = [i for i in (idx_n, idx_r) if i != -1]
                if not idxs:
                    break
                idx = min(idxs)
                line = buf[:idx].decode("utf-8", errors="replace")
                buf = buf[idx + 1:]
                if line.strip():
                    self._handle_line(line)
        if buf.strip():
            self._handle_line(buf.decode("utf-8", errors="replace"))

    def _eta(self, fraction):
        if not fraction or fraction <= 0.01:
            return None
        elapsed = time.time() - self._start_time
        return int(elapsed * (1.0 - fraction) / fraction)

    def _handle_line(self, line):
        # strip colors / cursor movements dnf emits when it sees a TTY
        line = RE_CTRL.sub("", RE_ANSI.sub("", line))
        stripped = line.strip()
        if not stripped:
            return
        self._log.append(line)

        m = RE_DOWNLOAD.match(stripped)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            frac = (done / total) * self.DOWNLOAD_WEIGHT if total else None
            self.on_event(TransactionEvent(
                "download", frac, self._eta(frac), "Downloading",
                m.group(3), line, m.group(4), m.group(5)))
            return

        m = RE_TRANS_STEP.match(line)
        if m:
            action, pkg = m.group(1), m.group(2)
            done, total = int(m.group(3)), int(m.group(4))
            if total:
                frac = self.DOWNLOAD_WEIGHT + (done / total) * (1.0 - self.DOWNLOAD_WEIGHT)
            else:
                frac = None
            self.on_event(TransactionEvent(
                "transaction", frac, self._eta(frac), action, pkg, line))
            return

        m = RE_DNF5.match(stripped)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            rest = m.group(3)
            action = next((a for a in DNF5_ACTIONS if rest.startswith(a)),
                          None)
            speed_m = RE_DNF5_SPEED.search(rest)
            size_m = RE_DNF5_SIZE.search(rest)
            if action:
                tail = rest[len(action):].strip()
                pkg = tail.split()[0] if tail else ""
                if action in ("Verify", "Verifying"):
                    action = "Verifying"
                elif action in ("Prepare", "Preparing"):
                    action = "Preparing"
                frac = (self.DOWNLOAD_WEIGHT
                        + (done / total) * (1.0 - self.DOWNLOAD_WEIGHT)
                        if total else None)
                self.on_event(TransactionEvent(
                    "transaction", frac, self._eta(frac), action, pkg, line))
            else:
                # a download line: "[x/y] package ... % | speed | size | time"
                pkg = rest.split()[0] if rest.split() else ""
                frac = (done / total) * self.DOWNLOAD_WEIGHT if total else None
                self.on_event(TransactionEvent(
                    "download", frac, self._eta(frac), "Downloading", pkg,
                    line,
                    speed_m.group(1) if speed_m else "",
                    size_m.group(1) if size_m else ""))
            return

        action = None
        if stripped.startswith("Running scriptlet"):
            action = "Running scriptlet"
        elif stripped.startswith("Verifying"):
            action = "Verifying"
        elif "dracut" in stripped.lower():
            action = "Running dracut"
        elif stripped.startswith("Downloading Packages"):
            action = "Downloading"
        elif stripped.startswith("Running transaction check"):
            action = "Checking transaction"
        elif stripped.startswith("Running transaction test"):
            action = "Testing transaction"
        elif stripped.startswith("Running transaction"):
            action = "Running transaction"
        elif stripped.startswith("Importing GPG key") or "GPG" in stripped:
            action = "Importing GPG key"
        elif stripped.startswith("Complete!"):
            self.on_event(TransactionEvent(
                "other", 1.0, 0, "Complete", "", line))
            return

        self.on_event(TransactionEvent(
            "other", None, None, action or "", "", line))


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

def build_distro_sync_args(allowerasing=False, excludes=None, refresh=False):
    args = ["distro-sync"]
    if allowerasing:
        args.append("--allowerasing")
    if refresh:
        args.append("--refresh")
    for pkg in (excludes or []):
        pkg = pkg.strip()
        if pkg:
            args.append(f"--exclude={pkg}")
    return args


def build_install_args(packages, upgrades=None, allowerasing=False):
    """Build dnf args installing ``packages`` and updating ``upgrades``.

    A pure-upgrade selection uses ``dnf upgrade`` (``dnf install`` refuses
    already-installed packages when its metadata is stale). Transactions
    involving upgrades pass --refresh so the resolver sees the same
    package versions the GUI advertised.
    """
    packages = list(packages)
    upgrades = list(upgrades or [])
    if upgrades and not packages:
        args = ["upgrade"] + upgrades
    else:
        args = ["install"] + packages + upgrades
    if upgrades:
        args.append("--refresh")
    if allowerasing:
        args.append("--allowerasing")
    return args


def clean_and_refresh():
    """Force-clean the dnf cache and download fresh repository metadata.

    Equivalent to ``dnf clean all && dnf makecache --refresh`` (user
    cache). Also drops UPDragora's own package-list cache so the next
    load reflects the fresh metadata. Returns (success, error_message).
    """
    for args in (["clean", "all"], ["makecache", "--refresh"]):
        try:
            proc = subprocess.run(
                ["dnf", "-q"] + args,
                capture_output=True, text=True, env=CLEAN_ENV, timeout=900,
            )
        except subprocess.TimeoutExpired:
            return False, "Timed out while refreshing repositories."
        except FileNotFoundError:
            return False, "dnf was not found on this system."
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout
                           or f"dnf {args[0]} failed").strip()
    try:
        os.remove(AVAILABLE_CACHE)
    except OSError:
        pass
    return True, ""


def refresh_metadata():
    """Refresh the user-level dnf metadata cache (dnf makecache --refresh).

    Returns (success, error_message).
    """
    try:
        proc = subprocess.run(
            ["dnf", "-q", "makecache", "--refresh"],
            capture_output=True, text=True, env=CLEAN_ENV, timeout=900,
        )
    except subprocess.TimeoutExpired:
        return False, "Timed out while refreshing repositories."
    except FileNotFoundError:
        return False, "dnf was not found on this system."
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "makecache failed").strip()
    return True, ""


def build_remove_args(packages):
    return ["remove"] + list(packages)


def dnf_available():
    return shutil.which("dnf") is not None
