# UPDragora

A modern GTK4 system updater and package manager for **OpenMandriva Linux**.

Author: **Tears of Mandrake** · License: GPL-3.0-or-later

UPDragora updates the system using `sudo dnf distro-sync` (the method
recommended by OpenMandriva) instead of a plain `dnf update`. It also
provides tabs for installing and removing packages, and a Flatpak tab
(functionality coming soon).

## Features

- **System update** via `dnf distro-sync`, with optional
  `--allowerasing` and `--exclude=<package>` (advanced options)
- Live progress: progress bar with percentage, ETA, current package and
  action (downloading, installing, scriptlets, dracut, …), plus a
  details view showing the raw dnf output exactly as in a terminal
- Post-transaction **summary** (what was installed/upgraded/removed) or
  **error details** on failure
- **Install / Remove** tabs listing packages with version,
  architecture, size, summary, repository and release
  (cooker/rolling/rock/none), with multi-selection and dependency
  preview before confirming
- **Polkit integration** (`auth_admin_keep`) so the password is not
  requested for every action
- **Multi-language**: English and Polish included; easy to extend
- Theme selection: system / light / dark

## Architecture

```
updragora/
├── core/           # toolkit-agnostic logic (dnf backend, config, i18n)
│   ├── backend.py  # queries, transaction runner with progress parsing
│   ├── config.py   # JSON config in ~/.config/updragora/
│   └── i18n.py     # JSON-based translations
├── gtk/            # GTK4/libadwaita frontend (a Qt6 one can be added later)
└── translations/   # en.json, pl.json, ...
```

The `core` package never imports GTK, so an alternative GUI backend
(e.g. Python + Qt6) can be added later on top of the same core.

## Requirements

- Python >= 3.9
- GTK 4 and libadwaita >= 1.4 with GObject introspection
- PyGObject
- dnf, polkit (pkexec)

On OpenMandriva:

```sh
sudo dnf install python-gobject3 libadwaita-common lib64adwaita-gir1 polkit
```

## Running from source

```sh
./bin/updragora
# or
python3 -m updragora
```

Without the installed polkit policy, pkexec will ask for the password
for each privileged action.

## System installation

```sh
# application
sudo mkdir -p /usr/share/updragora
sudo cp -r updragora /usr/share/updragora/
sudo install -Dm755 bin/updragora /usr/bin/updragora

# privileged helper + polkit policy (enables auth_admin_keep)
sudo install -Dm755 data/updragora-helper /usr/libexec/updragora/updragora-helper
sudo install -Dm644 data/org.tearsofmandrake.updragora.policy \
    /usr/share/polkit-1/actions/org.tearsofmandrake.updragora.policy

# desktop entry
sudo install -Dm644 data/org.tearsofmandrake.updragora.desktop \
    /usr/share/applications/org.tearsofmandrake.updragora.desktop
```

## Adding a translation

1. Copy `updragora/translations/pl.json` to `<lang>.json` and translate
   the values (keys are the English source strings).
2. Add the language code and native name to `LANGUAGES` in
   `updragora/core/i18n.py`.
