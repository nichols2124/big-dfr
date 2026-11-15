#!/usr/bin/env bash
# Install big-dfr: a customizable Touch Bar daemon for Apple T2 and Apple
# Silicon Macs. Builds and installs the package, lets you pick a config, and
# sets up daily pruning of old config backups.
#
# This script only supports Arch Linux / pacman, since that's the only
# packaging this project ships (pkg/PKGBUILD). It does not attempt to
# install any other distro's packages, and it never installs a kernel
# module itself -- if the Touch Bar's kernel driver isn't present, it tells
# you where to get it and stops, rather than guessing.

set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CONF_DIR="$HOME/.config/big-dfr"

say() { printf '==> %s\n' "$1"; }
die() { printf 'error: %s\n' "$1" >&2; exit 1; }

[[ $EUID -ne 0 ]] || die "run this as your normal user, not root -- it uses sudo itself where needed"

# ---- 1. Dependencies ----
command -v pacman >/dev/null 2>&1 || die "this installer only supports Arch Linux (pacman not found)"

say "Checking dependencies..."
missing=()
for pkg_bin in brightnessctl:brightnessctl wireplumber:wpctl playerctl:playerctl curl:curl; do
    pkg=${pkg_bin%%:*}
    bin=${pkg_bin##*:}
    command -v "$bin" >/dev/null 2>&1 || missing+=("$pkg")
done
if ((${#missing[@]})); then
    say "Installing missing dependencies: ${missing[*]}"
    sudo pacman -S --needed "${missing[@]}"
else
    say "All dependencies already present."
fi

# ---- 2. Touch Bar kernel driver present? ----
# Same detection the daemon's own wait-display helper uses: is there an
# appletbdrm-backed DRM card. We don't touch kernel modules ourselves --
# a missing driver means stop and point at where to get it.
has_touchbar_display() {
    for c in /sys/class/drm/card*; do
        [[ -e "$c/device/driver" ]] || continue
        [[ "$(basename "$(readlink -f "$c/device/driver")" 2>/dev/null)" == appletbdrm ]] && return 0
    done
    return 1
}
if ! has_touchbar_display && ! lsmod | grep -q '^apple_bce'; then
    cat >&2 <<'EOF'
error: no T2/Apple Silicon Touch Bar kernel driver detected (no appletbdrm
DRM card, no apple_bce module loaded).

big-dfr needs the Touch Bar kernel driver stack from the t2-linux project
first: https://wiki.t2linux.org/guides/postinstall/ (apple-bce provides the
bridge; appletbdrm is the Touch Bar's DRM display driver that comes with it).

This installer will not attempt to build or load a kernel module itself --
install that driver stack, reboot, and run this script again.
EOF
    exit 1
fi
say "Touch Bar kernel driver detected."

# ---- 3. Build and install the package ----
say "Building and installing the big-dfr package (this builds from source, may take a minute)..."
( cd "$SOURCE_DIR/pkg" && makepkg -sif )

# ---- 4. Config variant ----
mkdir -p "$CONF_DIR/backups"
echo
echo "Which config do you want to start from?"
echo "  1) generic           -- no desktop-shell integration"
echo "  2) dux-shell / DMS    -- adds a launchpad button and DMS-driven keyboard backlight"
read -r -p "Choice [1]: " choice
choice=${choice:-1}
case "$choice" in
    1) variant="$SOURCE_DIR/shipped-defaults/config.default.toml" ;;
    2) variant="$SOURCE_DIR/shipped-defaults/config.dux-shell.toml" ;;
    *) die "invalid choice: $choice" ;;
esac

if [[ -f "$CONF_DIR/config.toml" ]]; then
    backup="$CONF_DIR/backups/config-$(date +%Y%m%d-%H%M%S).toml"
    say "Backing up existing config to $backup"
    cp "$CONF_DIR/config.toml" "$backup"
fi
install -Dm644 "$variant" "$CONF_DIR/config.toml"
install -Dm755 "$SOURCE_DIR/shipped-defaults/scripts/media-button.sh" "$CONF_DIR/media-button.sh"
say "Installed config.toml ($([[ $choice == 2 ]] && echo dux-shell || echo generic)) and media-button.sh to $CONF_DIR"
say "A TTY-toggle widget is also available -- add it from the GTK config editor's 'Add' menu (it's packaged, no extra files needed)."

# ---- 5. Daily backup pruning (systemd --user timer) ----
say "Setting up daily pruning of config backups older than 5 days..."
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/big-dfr-backup-prune.service" <<EOF
[Unit]
Description=Prune old big-dfr config backups

[Service]
Type=oneshot
ExecStart=/usr/bin/find %h/.config/big-dfr/backups -type f -mtime +5 -delete
EOF

cat > "$UNIT_DIR/big-dfr-backup-prune.timer" <<EOF
[Unit]
Description=Daily prune of old big-dfr config backups

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now big-dfr-backup-prune.timer
say "Backup pruning timer installed and enabled (systemctl --user status big-dfr-backup-prune.timer)."

# ---- 6. Done ----
say "Restarting big-dfr.service..."
sudo systemctl restart big-dfr.service || say "Could not restart the service automatically -- reboot or run: sudo systemctl restart big-dfr.service"

echo
say "Done. Edit your Touch Bar with: touchbar-config"
