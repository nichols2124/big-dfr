# big-dfr

A heavily forked [not-quite-tiny-dfr](https://github.com/emzmo/not-quite-tiny-dfr) Touch Bar daemon
(itself a fork of [tiny-dfr](https://github.com/tiny-dfr/tiny-dfr)), built out for a
MacBookPro16,1 (T2) running Arch Linux with niri + DMS.

## Layout

- `daemon/` — the Rust daemon's full source (based on upstream commit `af2d90a`), including
  everything in `touchbar-layers.patch` already applied. Build with:
  ```
  cd daemon && cargo build --release --locked
  ```
- `pkg/` — Arch packaging: `PKGBUILD`, `big-dfr.service`, and `touchbar-layers.patch` (the diff
  against upstream, applied with `git apply` — not plain `patch`, since it includes pure file
  renames).
- `touchbar-config/` — the native GTK4/libadwaita config editor (`app.py` + `tbtoml.py`); launched
  normally via a `~/.local/bin/touchbar-config` wrapper. `server.py`/`index.html` are an earlier,
  now-unused web-based prototype of the same tool.
- `config/` — a snapshot of `~/.config/big-dfr/`: the live `config.toml`, custom icons, and every
  OnClick shell script (fan redline, the performance "secret menu" toggle, TTY switch, weather,
  dotfiles backup, etc).

## Notable features over upstream

- An App Dock widget: pinned app launchers with drag-scroll, running/crashed state via a generic
  `/proc` scan, and a GTK installed-apps picker.
- CPU/GPU usage (utilization %) display mode, alongside the existing temperature/wattage modes.
- A `LayerBack` button action and a `LAYER_ENTRY_POINT` tracker: any popup-style layer (sliders,
  the performance secret menu) reachable from more than one place returns to wherever it was
  actually entered from — on close, on idle timeout, or on drag-dismiss — instead of a single
  hardcoded layer.
- A per-layer `Timeout` override (`LayerStyles = [..., { Timeout = 0 }]`) so a specific layer can
  opt out of the global `LayerTimeout` auto-return.
- The physical Fn key's hold-to-peek now restores whichever layer was active before Fn was
  pressed, instead of unconditionally landing on layer 0.
- A `Caption` field for icon buttons: small text drawn under the icon as a label alongside it
  (distinct from `Text`, which replaces the icon entirely).
- A "performance secret menu": a dedicated button toggles CPU performance profile, AMD GPU max
  clocks, fan redline (SIGSTOP/SIGCONT the system fan daemon + force max RPM), idle-sleep
  inhibition, and Do Not Disturb, together, then opens a layer reachable only from that button.

## Gotchas worth knowing before touching this

- The daemon's systemd unit runs with `NoNewPrivileges=true`, which silently no-ops `sudo` in any
  OnClick command. Privileged actions need either a narrow DAC loosening of the specific target
  (a udev rule or a drop-in on the *target* service) or an ambient capability re-raised after the
  daemon's own root→user privilege drop in `drop_privileges()` (`main.rs`) — plain
  `AmbientCapabilities=` in the unit isn't enough by itself.
- **Never** regenerate `touchbar-layers.patch` without checking `git diff --stat` for `Bin`
  markers first. A literal non-UTF8 byte in a source file makes `git diff` treat the whole file as
  binary and silently drop it from the patch with no error at all.
