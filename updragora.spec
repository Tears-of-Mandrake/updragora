Name:           updragora
Version:        0.1.1
Release:        1
Summary:        Modern system updater and package manager for OpenMandriva
Group:          System/Configuration/Packaging
License:        GPLv3+
URL:            https://github.com/tears-of-mandrake/updragora
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python >= 3.9

Requires:       python >= 3.9
Requires:       python-gobject3
Requires:       dnf
Requires:       polkit
Requires:       typelib(Gtk) = 4.0
Requires:       typelib(Adw)

%description
UPDragora is a GTK4/libadwaita system updater and package manager for
OpenMandriva Linux by Tears of Mandrake. It updates the system using
dnf distro-sync (the method recommended by OpenMandriva), shows live
transaction progress, and provides tabs for installing and removing
packages with dependency preview. Privileged operations go through a
polkit-authorized helper (auth_admin_keep), so the password is not
requested for every action.

%prep
%autosetup

%build
# nothing to build - pure Python application

%install
# application code
mkdir -p %{buildroot}%{_datadir}/%{name}
cp -r updragora %{buildroot}%{_datadir}/%{name}/
# remove bytecode that may exist in the source tree
find %{buildroot}%{_datadir}/%{name} -name '__pycache__' -type d -prune -exec rm -rf {} +

# launcher
install -Dm755 bin/updragora %{buildroot}%{_bindir}/updragora

# privileged helper + polkit policy
install -Dm755 data/updragora-helper \
    %{buildroot}/usr/libexec/%{name}/updragora-helper
install -Dm644 data/org.tearsofmandrake.updragora.policy \
    %{buildroot}%{_datadir}/polkit-1/actions/org.tearsofmandrake.updragora.policy

# desktop entry and icon
install -Dm644 data/org.tearsofmandrake.updragora.desktop \
    %{buildroot}%{_datadir}/applications/org.tearsofmandrake.updragora.desktop
install -Dm644 updragora.png \
    %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/updragora.png

# in-app artwork
install -Dm644 up-to-date.png %{buildroot}%{_datadir}/%{name}/up-to-date.png

%files
%doc README.md
%{_bindir}/updragora
%{_datadir}/%{name}/
/usr/libexec/%{name}/
%{_datadir}/polkit-1/actions/org.tearsofmandrake.updragora.policy
%{_datadir}/applications/org.tearsofmandrake.updragora.desktop
%{_datadir}/icons/hicolor/256x256/apps/updragora.png

%changelog
* Sun Sep 06 2026 Tears of Mandrake <tears-of-mandrake> 0.1.1-1
- Add clean cache & refresh button in the update module
- Fix selective updates from the Install tab
- Auto-select dependencies when picking packages to install
- Fix up to date icon.

* Thu Sep 04 2026 Tears of Mandrake <tears-of-mandrake> 0.1.0-1
- Initial package
