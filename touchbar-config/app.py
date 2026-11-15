#!/usr/bin/env python3
"""Touch Bar: native GTK4/libadwaita editor for big-dfr.

Edits ~/.config/big-dfr/config.toml; the daemon live-reloads it.
Widths are edited in "buttons" (any value > 0). The daemon only knows whole
slots, so each screen is saved on the smallest grid that represents its widths
and that grid is remembered in touchbar-config.json (layerScale).
"""
import copy
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import tomllib
import warnings
from pathlib import Path

import gi

# Gdk.Texture.new_for_pixbuf and Gdk.cairo_set_source_pixbuf are flagged as
# deprecated by GTK but remain the direct way to show the bar's SVG icons.
warnings.filterwarnings("ignore", category=DeprecationWarning)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_foreign("cairo")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402
import cairo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tbtoml  # noqa: E402

APP_ID = "dev.t2setup.TouchBarConfig"
CONF_DIR = tbtoml.CONFIG.parent
META = CONF_DIR / "touchbar-config.json"
BACKUPS = CONF_DIR / "backups"
CACHE = Path("/var/cache/big-dfr")
ICON_DIRS = [CONF_DIR, Path("/etc/big-dfr"), Path("/usr/share/big-dfr")]
REARRANGE_REQUEST = CACHE / "rearrange-request"
REARRANGE_RESULT = CACHE / "rearrange-result.json"
BAR_W, BAR_H = 2008, 64  # MacBookPro16,1 panel, from the daemon's own frame

DEFAULT_HEADER = "Touch Bar (big-dfr). Edited with the Touch Bar app."
DEFAULT_STYLE = {
    "Background": "#000000", "ButtonColor": "#333333", "ButtonColorActive": "#666666",
    "TextColor": "#ffffff", "ButtonSpacing": 16, "EdgePadding": 0, "CornerRadius": 8,
    "SeparatorWidth": 2, "SeparatorOpacity": 0.4, "HeightPercent": 90, "FontSize": 32,
    "IconSize": 48, "BatteryChargingColor": "#00b300", "BatteryLowColor": "#b30000",
}
# key, kind, default, title, description
GENERAL = [
    ("LayerTimeout", "num", 0, "Popup timeout", "Seconds without a touch before screens 2+ return to screen 0 (0 = never)"),
    ("LayerSwipe", "bool", True, "Two-finger swipe", "Swipe between the Control Strip (0) and Fn (1)"),
    ("MotionStyle", "motion", None, "Motion style", "How everything on the bar moves"),
    ("TransitionMs", "num", 500, "Screen animation", "Opening and closing screens 2+, in ms (500 = as designed, 0 = instant)"),
    ("DragCloseTimeout", "num", 0, "Close after drag", "Seconds before a press-and-drag slider closes"),
    ("DimTimeout", "int", 30, "Dim after", "Seconds until the bar dims (0 = never)"),
    ("OffTimeout", "int", 60, "Turn off after", "Seconds until the bar turns off (0 = never)"),
    ("DimBrightness", "num", "", "Dimmed brightness", "Percent after the dim timeout (empty = hardware dim)"),
    ("OffBrightness", "num", 0, "Off brightness", "Percent after the off timeout (0 = really off)"),
    ("AdaptiveBrightness", "bool", True, "Adaptive brightness", "Follow the screen brightness"),
    ("ActiveBrightness", "int", 128, "Fixed brightness", "0–255, when adaptive brightness is off"),
    ("ShowButtonOutlines", "bool", True, "Button backgrounds", "Draw the filled shape behind buttons"),
    ("FontFamily", "text", "", "Font", "Empty = system default"),
    ("FontBold", "bool", True, "Bold text", ""),
    ("EnablePixelShift", "bool", False, "Pixel shift", "Move the image by a pixel now and then (burn-in relief)"),
    ("VisibleButtons", "int", 0, "Visible slots", "More slots than this makes a screen scroll (0 = fit all)"),
    ("ScrollLoop", "bool", True, "Scroll wraps around", ""),
    ("DoublePressSwitchLayers", "int", 0, "Double-press Fn", "Window in ms to swap screens permanently (0 = off)"),
    ("LyricOffset", "num", 0, "Lyrics offset", "Seconds to shift synced lyrics"),
    ("MediaCoverBlur", "bool", False, "Blur album cover", ""),
    ("MediaArtCache", "bool", True, "Cache album covers", ""),
    ("MediaLyricsCache", "bool", True, "Cache lyrics", ""),
]
STYLES = [
    ("ButtonSpacing", "num", "Button spacing", "Pixels between buttons. 0 = joined group with dividers"),
    ("EdgePadding", "num", "Edge padding", "Pixels at both ends of the bar"),
    ("CornerRadius", "num", "Corner radius", "Pixels"),
    ("SeparatorWidth", "num", "Divider width", "Between joined buttons, in pixels"),
    ("SeparatorOpacity", "num", "Divider opacity", "0 to 1"),
    ("HeightPercent", "num", "Button height", "Percent of the bar"),
    ("IconSize", "num", "Icon size", "Pixels"),
    ("FontSize", "num", "Font size", "Pixels"),
    ("ButtonColor", "color", "Button color", ""),
    ("ButtonColorActive", "color", "Pressed color", ""),
    ("TextColor", "color", "Text and icon color", ""),
    ("Background", "color", "Bar background", ""),
    ("BatteryChargingColor", "color", "Battery charging", ""),
    ("BatteryLowColor", "color", "Battery low", ""),
]
LAYER_STYLE_KEYS = [("ButtonSpacing", "Spacing"), ("CornerRadius", "Corner radius"), ("EdgePadding", "Edge padding")]
KEYS = """Esc F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 F11 F12 BrightnessDown BrightnessUp IllumDown IllumUp
PlayPause NextSong PreviousSong Mute VolumeDown VolumeUp MicMute Search Delete Enter Space Tab Home End
PageUp PageDown Left Right Up Down LeftCtrl LeftShift LeftAlt LeftMeta Print Calc Sleep
TouchBarBrightnessUp TouchBarBrightnessDown Layer0 Layer1 Layer2 Layer3 Layer4 Layer5 Layer6 Layer7
Layer8 Layer9""".split()
CONTENT_KEYS = {"Icon", "Svg", "Text", "Caption", "Time", "Locale", "Battery", "Cpu", "CpuTemp", "CpuLabel", "Gpu", "GpuLabel",
                "Media", "Command", "Interval", "SliderGet", "SliderSet", "SliderMute", "SliderLowIcon",
                "SliderMuteIcon", "SliderStretch", "SliderOpen", "ExpandCommand", "ExpandStretch"}
TYPES = [("icon", "Icon"), ("text", "Text"), ("spacer", "Empty space"), ("slider", "Slider"), ("media", "Media player"),
         ("command", "Script widget"), ("time", "Clock"), ("battery", "Battery"), ("cpu", "CPU"), ("gpu", "GPU")]
ADD_TYPES = (
    TYPES[:4]
    + [
        ("mediabtn", "Media button (only while playing)"),
        ("weather", "Weather"),
        ("ttytoggle", "TTY toggle"),
    ]
    + TYPES[4:]
)
DMS_RUN = "systemd-run --user --quiet --collect "
MEDIA_SCRIPT = "sh ~/.config/big-dfr/media-button.sh"
WEATHER_SCRIPT = "sh /usr/share/big-dfr/widgets/weather.sh"
TTY_TOGGLE_SCRIPT = "sh /usr/share/big-dfr/widgets/tty-toggle.sh"
VOLUME_GET = "wpctl get-volume @DEFAULT_AUDIO_SINK@ | awk '{print int($2*100) ($3==\"[MUTED]\" ? \" muted\" : \"\")}'"
# Names the older Romanian editor stored; the app is English now.
NAME_MIGRATION = {"Luminozitate": "Brightness", "Volum": "Volume", "Control Strip extins": "Expanded Control Strip"}


def half_up(x):
    return math.floor(x + 0.5)


def btn_type(b):
    if "SliderGet" in b or "SliderSet" in b:
        return "slider"
    if b.get("Media"):
        return "media"
    for key, kind in (("Command", "command"), ("Time", "time"), ("Battery", "battery"), ("Cpu", "cpu"),
                      ("CpuTemp", "cpu"), ("Gpu", "gpu"), ("Icon", "icon"), ("Svg", "icon"), ("Text", "text")):
        if key in b:
            return kind
    return "spacer"


# ---- widths: "buttons" in the editor, whole slots in the file ----
W_KEYS = [("Stretch", 1), ("SliderStretch", 2), ("ExpandStretch", 2)]
MAX_GRID = 240  # slots per button


def uses_key(b, key):
    return (key == "Stretch" or (key == "SliderStretch" and btn_type(b) == "slider")
            or (key == "ExpandStretch" and btn_type(b) == "command" and b.get("OnClick") == "Expand"))


def grid_for(layer, pref=None):
    ws = [b.get(key, def_) for b in layer for key, def_ in W_KEYS if uses_key(b, key)]

    def fits(k):
        return all(abs(w * k - half_up(w * k)) <= 0.005 * k and half_up(w * k) >= 1 for w in ws)

    if pref and fits(pref):
        return pref
    return next((k for k in range(1, MAX_GRID + 1) if fits(k)), MAX_GRID)


def to_slots(layer, k):
    out = []
    for b in layer:
        o = copy.deepcopy(b)
        for key, _ in W_KEYS:
            if not uses_key(b, key):
                continue
            if key not in o:
                if key == "Stretch" and k != 1:
                    o[key] = k
                continue
            s = max(1, half_up(o[key] * k))
            if key == "Stretch" and s == 1:
                del o[key]
            else:
                o[key] = s
        out.append(o)
    return out


def to_units(layer, k):
    out = []
    for b in layer:
        o = copy.deepcopy(b)
        for key, def_ in W_KEYS:
            if not uses_key(o, key) or (key != "Stretch" and key not in o and k == 1):
                continue
            u = round(o.get(key, def_) / k, 4)
            if key == "Stretch" and u == 1:
                o.pop(key, None)
            else:
                o[key] = u
        out.append(o)
    return out


def parse_color(text, fallback=(0.2, 0.2, 0.2, 1.0)):
    s = (text or "").lstrip("#")
    if len(s) in (3, 4):
        s = "".join(c * 2 for c in s)
    try:
        v = [int(s[i:i + 2], 16) / 255 for i in range(0, len(s), 2)]
    except ValueError:
        return fallback
    if len(v) == 3:
        return (*v, 1.0)
    return tuple(v) if len(v) == 4 else fallback


def find_icon(name):
    for d in ICON_DIRS:
        for ext in (".svg", ".png"):
            p = d / f"{name}{ext}"
            if p.is_file():
                return p
    return None


def list_icons():
    names = {p.stem for d in ICON_DIRS if d.is_dir() for p in d.iterdir() if p.suffix in (".svg", ".png")}
    return sorted(names)


_pix = {}


def icon_pixbuf(name, w, h):
    key = (name, int(w), int(h))
    if key not in _pix:
        path = find_icon(name)
        try:
            _pix[key] = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), max(1, int(w)), max(1, int(h)), True) if path else None
        except GLib.Error:
            _pix[key] = None
    return _pix[key]


class Model:
    """The config being edited, with widths in buttons."""

    def __init__(self):
        self.load()

    def load(self):
        text = tbtoml.CONFIG.read_text()
        self.cfg = tomllib.loads(text)
        self.meta = json.loads(META.read_text()) if META.exists() else {}
        self.header = "\n".join(l[2:] for l in text.splitlines() if l.startswith("# ")) or None
        cfg, meta = self.cfg, self.meta
        if "Layers" not in cfg:
            p = cfg.pop("PrimaryLayerKeys", [{"Text": "esc", "Action": "Esc"}])
            m = cfg.pop("MediaLayerKeys", [{"Icon": "play_pause", "Action": "PlayPause"}])
            cfg["Layers"] = [m, p] if cfg.pop("MediaLayerDefault", False) else [p, m]
        n = len(cfg["Layers"])
        names = [NAME_MIGRATION.get(x, x) for x in meta.get("layerNames", [])]
        meta["layerNames"] = (names + [""] * n)[:n]
        scale = list(meta.get("layerScale", []))
        meta["layerScale"] = (scale + [1] * n)[:n]
        cfg["Layers"] = [to_units(l, meta["layerScale"][i] or 1) for i, l in enumerate(cfg["Layers"])]
        self.saved = self.snapshot()

    def snapshot(self):
        # Empty per-screen styles and an empty [Style] mean nothing has changed.
        cfg = dict(self.cfg)
        if not any(cfg.get("LayerStyles", [])):
            cfg.pop("LayerStyles", None)
        if not cfg.get("Style"):
            cfg.pop("Style", None)
        return json.dumps([cfg, self.meta], sort_keys=True)

    @property
    def dirty(self):
        return self.snapshot() != self.saved

    def layer_name(self, i):
        name = self.meta["layerNames"][i] if i < len(self.meta["layerNames"]) else ""
        return name or {0: "Control Strip", 1: "Fn keys"}.get(i, f"Screen {i}")

    def global_style(self):
        return {**DEFAULT_STYLE, **self.cfg.get("Style", {})}

    def layer_style(self, i):
        styles = self.cfg.setdefault("LayerStyles", [])
        while len(styles) < len(self.cfg["Layers"]):
            styles.append({})
        return styles[i]

    def style(self, i):
        s = self.global_style()
        over = self.cfg.get("LayerStyles", [])
        if i < len(over):
            s.update(over[i])
        return s

    def tidy_layer_styles(self):
        styles = self.cfg.get("LayerStyles")
        if styles is None:
            return
        n = len(self.cfg["Layers"])
        self.cfg["LayerStyles"] = (styles + [{} for _ in range(n)])[:n]
        if not any(self.cfg["LayerStyles"]):
            del self.cfg["LayerStyles"]

    def layout(self, i):
        units = self.cfg["Layers"][i]
        k = grid_for(units, self.meta["layerScale"][i])
        slots = to_slots(units, k)
        st = self.style(i)
        n = sum(b.get("Stretch", 1) for b in slots) or 1
        sp, edge = st["ButtonSpacing"], st["EdgePadding"]
        slot = (BAR_W - 2 * edge - (n - 1) * sp) / n
        items, start = [], 0
        for unit, b in zip(units, slots):
            span = b.get("Stretch", 1)
            items.append((unit, math.floor(start * (slot + sp)) + edge, slot + math.floor((span - 1) * (slot + sp))))
            start += span
        return {"n": n, "k": k, "slot": slot, "items": items}

    # LayerN actions follow their screen when screens move or disappear.
    def remap_layer_actions(self, mapping):
        def conv(a):
            if isinstance(a, str) and len(a) == 6 and a.startswith("Layer") and a[5].isdigit():
                new = mapping(int(a[5]))
                return None if new is None else f"Layer{new}"
            return a

        for layer in self.cfg["Layers"]:
            for b in layer:
                if "Action" not in b:
                    continue
                if isinstance(b["Action"], list):
                    acts = [x for x in map(conv, b["Action"]) if x]
                    if acts:
                        b["Action"] = acts
                    else:
                        del b["Action"]
                else:
                    a = conv(b["Action"])
                    if a:
                        b["Action"] = a
                    else:
                        del b["Action"]

    def swap_layers(self, a, c):
        for seq in (self.cfg["Layers"], self.meta["layerNames"], self.meta["layerScale"]):
            seq[a], seq[c] = seq[c], seq[a]
        if "LayerStyles" in self.cfg:
            self.layer_style(max(a, c))
            s = self.cfg["LayerStyles"]
            s[a], s[c] = s[c], s[a]
            self.tidy_layer_styles()
        self.remap_layer_actions(lambda n: c if n == a else a if n == c else n)

    def delete_layer(self, i):
        for seq in (self.cfg["Layers"], self.meta["layerNames"], self.meta["layerScale"]):
            del seq[i]
        if "LayerStyles" in self.cfg:
            self.layer_style(i)
            del self.cfg["LayerStyles"][i]
            self.tidy_layer_styles()
        self.remap_layer_actions(lambda n: None if n == i else (n - 1 if n > i else n))

    def problems(self):
        out, layers = [], self.cfg["Layers"]
        if not layers:
            out.append("There must be at least one screen.")
        for i, layer in enumerate(layers):
            if not layer:
                out.append(f"Screen {i} has no buttons.")
            for j, b in enumerate(layer):
                acts = b.get("Action", [])
                for a in acts if isinstance(acts, list) else [acts]:
                    if len(a) == 6 and a.startswith("Layer") and a[5].isdigit() and int(a[5]) >= len(layers):
                        out.append(f"Screen {i}, button {j + 1}: {a} does not exist.")
                for key, _ in W_KEYS:
                    if key in b and not b[key] > 0:
                        out.append(f"Screen {i}, button {j + 1}: {key} must be greater than 0.")
            if grid_for(layer, self.meta["layerScale"][i]) == MAX_GRID:
                out.append(f"Screen {i}: those widths can't be represented exactly. Try values like 0.25, 0.33 or 0.5.")
        return out

    def render(self):
        """Config as it goes to disk: whole slots, plus the grid of each screen."""
        out, scales = copy.deepcopy(self.cfg), []
        if not any(out.get("LayerStyles", [])):
            out.pop("LayerStyles", None)
        if not out.get("Style"):
            out.pop("Style", None)
        layers = []
        for i, layer in enumerate(self.cfg["Layers"]):
            k = grid_for(layer, self.meta["layerScale"][i])
            scales.append(k)
            layers.append(to_slots(layer, k))
        out["Layers"] = layers
        return out, scales

    def save(self):
        out, scales = self.render()
        text = tbtoml.dump(out, header=self.header or DEFAULT_HEADER)
        BACKUPS.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(tbtoml.CONFIG, BACKUPS / f"config-{stamp}.toml")
        for old in sorted(BACKUPS.glob("config-*.toml"))[:-30]:
            old.unlink()
        tmp = tbtoml.CONFIG.with_suffix(".toml.tmp")
        tmp.write_text(text)
        tmp.replace(tbtoml.CONFIG)
        self.meta["layerScale"] = scales
        META.write_text(json.dumps(self.meta, indent=2, ensure_ascii=False))
        self.saved = self.snapshot()
        return f"backups/config-{stamp}.toml"


def capture_frame():
    """Ask the daemon for its next frames; touching the config forces a redraw."""
    req, frame = CACHE / "dump-request", CACHE / "frame.png"
    try:
        before = frame.stat().st_mtime if frame.exists() else 0
        req.touch()
        os.utime(tbtoml.CONFIG)
        deadline = time.time() + 4
        while time.time() < deadline:
            time.sleep(0.25)
            if frame.exists() and frame.stat().st_mtime > before:
                time.sleep(1.2)  # widgets settle; later frames overwrite this one
                break
    finally:
        req.unlink(missing_ok=True)
    return str(frame) if frame.exists() else None


def rounded(cr, x, y, w, h, rl, rr):
    cr.new_sub_path()
    cr.arc(x + w - rr, y + rr, rr, -math.pi / 2, 0)
    cr.arc(x + w - rr, y + h - rr, rr, 0, math.pi / 2)
    cr.arc(x + rl, y + h - rl, rl, math.pi / 2, math.pi)
    cr.arc(x + rl, y + rl, rl, math.pi, 3 * math.pi / 2)
    cr.close_path()


def sensor_preview_text(b, kind):
    """Mock label for the Cpu/Gpu preview, following the daemon's own
    label -> temperature -> usage -> watts order and its empty-spec ->
    Celsius fallback (see Button::new_cpu/new_gpu in button.rs)."""
    key = "Cpu" if kind == "cpu" else "Gpu"
    spec = set((b.get(key) or "").split())
    label_on = b.get(key + "Label", True)
    unit = "fahrenheit" if "fahrenheit" in spec else ("celsius" if "celsius" in spec else None)
    watts = "watts" in spec or "power" in spec
    usage = "usage" in spec or "load" in spec
    if not (unit or watts or usage):
        unit = "celsius"
    parts = []
    if label_on:
        parts.append("CPU" if kind == "cpu" else "AMD")
    if unit:
        parts.append("125°F" if unit == "fahrenheit" else "52°C")
    if usage:
        parts.append("45%")
    if watts:
        parts.append("12W")
    return " ".join(parts) if parts else ("CPU" if kind == "cpu" else "GPU")


class Window(Adw.ApplicationWindow):
    def __init__(self, app, opts):
        super().__init__(application=app, title="Touch Bar", default_width=1360, default_height=880)
        self.opts = opts
        self.m = Model()
        self.L = min(opts.get("layer", 0), len(self.m.cfg["Layers"]) - 1)
        self.B = opts.get("button", -1)
        self.icons = list_icons()
        self.items = []
        self.zoom = 0.5
        self.rebuild_id = 0
        self.loading_layers = False
        self.closing = False
        self.ra = None  # rearrange session: {layer, base, mtime, poll, beat}
        REARRANGE_REQUEST.unlink(missing_ok=True)  # left over from a crash

        self.title = Adw.WindowTitle(title="Touch Bar")
        header = Adw.HeaderBar(title_widget=self.title)
        self.save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"], tooltip_text="Save and apply (Ctrl+S)")
        self.save_btn.connect("clicked", lambda *_: self.save())
        revert = Gtk.Button(label="Revert", tooltip_text="Discard unsaved changes")
        revert.connect("clicked", lambda *_: self.revert())
        header.pack_start(self.save_btn)
        header.pack_start(revert)
        self.capture_btn = Gtk.Button(label="Capture bar", tooltip_text="Show what the Touch Bar displays right now")
        self.capture_btn.connect("clicked", lambda *_: self.capture())
        header.pack_end(self.capture_btn)
        self.rearrange_btn = Gtk.Button(label="Rearrange", tooltip_text="Move this screen's buttons with your fingers on the Touch Bar")
        self.rearrange_btn.connect("clicked", lambda *_: self.toggle_rearrange())
        header.pack_end(self.rearrange_btn)
        self.revert_btn = revert

        self.toasts = Adw.ToastOverlay(child=self.build_body())
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        act = Gio.SimpleAction(name="save")
        act.connect("activate", lambda *_: self.save())
        self.add_action(act)
        app.set_accels_for_action("win.save", ["<Ctrl>s"])
        self.connect("close-request", self.on_close)
        self.refresh_all()

    # ---- layout ----
    def build_body(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.ra_banner = Adw.Banner(title="Drag buttons and spaces on the Touch Bar, then press Done.", button_label="Cancel")
        self.ra_banner.connect("button-clicked", lambda *_: self.end_rearrange(False))
        root.append(self.ra_banner)

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=8, margin_start=14, margin_end=14)
        self.bar = Gtk.DrawingArea(hexpand=True, content_height=44)
        self.bar.set_draw_func(self.draw_bar)
        self.bar.connect("resize", self.on_bar_resize)
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_bar_click)
        self.bar.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-end", self.on_bar_drag)
        self.bar.add_controller(drag)
        top.append(self.bar)

        self.capture_pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.FILL, hexpand=True, visible=False,
                                       tooltip_text="Captured from the Touch Bar")
        top.append(self.capture_pic)

        row = Gtk.Box(spacing=8)
        self.add_type = Gtk.DropDown.new_from_strings([label for _, label in ADD_TYPES])
        add = Gtk.Button(label="Add", tooltip_text="Add after the selected button")
        add.connect("clicked", lambda *_: self.add_button())
        row.append(self.add_type)
        row.append(add)
        row.append(Gtk.Label(label="Click a button to edit it, drag to reorder.", css_classes=["dim-label"]))
        self.info = Gtk.Label(hexpand=True, xalign=1, css_classes=["dim-label", "caption"])
        row.append(self.info)
        self.add_row = row
        top.append(row)
        root.append(top)
        root.append(Gtk.Separator())

        columns = Gtk.Box(vexpand=True)
        self.columns = columns
        columns.append(self.scrolled(self.build_sidebar(), 270))
        columns.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        columns.append(self.build_center())
        columns.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        columns.append(self.scrolled(self.editor, 420))
        root.append(columns)
        return root

    @staticmethod
    def scrolled(child, width=-1):
        sw = Gtk.ScrolledWindow(child=child, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=False)
        if width > 0:
            sw.set_size_request(width, -1)
        else:
            sw.set_hexpand(True)
        sw.set_vexpand(True)
        return sw

    def build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        box.append(Gtk.Label(label="Screens", xalign=0, css_classes=["heading"]))
        self.layer_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.SINGLE)
        self.layer_list.connect("row-selected", self.on_layer_selected)
        box.append(self.layer_list)
        row = Gtk.Box(spacing=6)
        for icon, tip, fn in (("list-add-symbolic", "Add a screen", self.add_layer),
                              ("go-up-symbolic", "Move up", lambda: self.move_layer(-1)),
                              ("go-down-symbolic", "Move down", lambda: self.move_layer(1))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda _b, f=fn: f())
            row.append(b)
        self.del_layer_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete this screen", css_classes=["destructive-action"], halign=Gtk.Align.END, hexpand=True)
        self.del_layer_btn.connect("clicked", lambda *_: self.delete_layer())
        row.append(self.del_layer_btn)
        box.append(row)
        self.layer_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(self.layer_detail)
        box.append(Gtk.Label(label="Screen 0 is the default. Screen 1 shows while you hold Fn. Others open from a button with a LayerN action.",
                             wrap=True, xalign=0, css_classes=["dim-label", "caption"]))
        return box

    def build_center(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.stack = Adw.ViewStack(vexpand=True)
        self.general_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        self.style_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        self.stack.add_titled(self.scrolled(self.general_box), "general", "General")
        self.stack.add_titled(self.scrolled(self.style_box), "style", "Style")
        json_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        self.json_view = Gtk.TextView(monospace=True, vexpand=True, top_margin=8, left_margin=8, css_classes=["card"])
        json_box.append(Gtk.Label(label="The whole config as JSON. Widths are in buttons, not slots.", xalign=0, css_classes=["dim-label"]))
        json_box.append(Gtk.ScrolledWindow(child=self.json_view, vexpand=True))
        apply_json = Gtk.Button(label="Apply JSON", halign=Gtk.Align.START)
        apply_json.connect("clicked", lambda *_: self.apply_json())
        json_box.append(apply_json)
        self.stack.add_titled(json_box, "json", "JSON")
        self.stack.connect("notify::visible-child-name", lambda *_: self.refresh_json())
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE, margin_top=8, halign=Gtk.Align.CENTER)
        box.append(switcher)
        box.append(self.stack)
        return box

    # ---- field helpers: an empty field removes the key, so the default applies ----
    def on_edit(self, rebuild=False):
        self.bar.queue_draw()
        self.refresh_status()
        self.refresh_layer_rows()
        if rebuild:
            self.queue_editor()

    @staticmethod
    def coerce(text, kind):
        text = text.strip().replace(",", ".")
        try:
            if kind == "int":
                return int(text)
            v = float(text)
            if not math.isfinite(v):
                raise ValueError
            return int(v) if v == int(v) else v
        except ValueError:
            return None

    def number_row(self, title, subtitle, obj, key, default, kind="num", after=None):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        entry = Gtk.Entry(text=str(obj.get(key, "")), placeholder_text=str(default), width_chars=8, max_width_chars=8, valign=Gtk.Align.CENTER, xalign=1)

        def changed(e):
            text = e.get_text().strip()
            if not text:
                obj.pop(key, None)
            else:
                v = self.coerce(text, kind)
                if v is None:
                    e.add_css_class("error")
                    return
                obj[key] = v
            e.remove_css_class("error")
            if after:
                after()
            self.on_edit()

        entry.connect("changed", changed)
        row.add_suffix(entry)
        return row

    def text_row(self, title, obj, key, rebuild=False):
        row = Adw.EntryRow(title=title, text=str(obj.get(key, "")))

        def changed(r):
            text = r.get_text()
            if text:
                obj[key] = text
            else:
                obj.pop(key, None)
            self.on_edit(rebuild)

        row.connect("changed", changed)
        return row

    def combo_row(self, title, subtitle, options, current, on_change):
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new([label for _, label in options]))
        values = [v for v, _ in options]
        row.set_selected(values.index(current) if current in values else 0)
        row.connect("notify::selected", lambda r, _p: on_change(values[r.get_selected()]))
        return row

    def switch_row(self, title, subtitle, active, on_toggle):
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(active))
        row.connect("notify::active", lambda r, _p: on_toggle(r.get_active()))
        return row

    def setter(self, obj, key):
        def set_value(v):
            if v is None:
                obj.pop(key, None)
            else:
                obj[key] = v
            self.on_edit()

        return set_value

    def bool_row(self, title, subtitle, obj, key, default):
        def changed(v):
            if v is None:
                obj.pop(key, None)
            else:
                obj[key] = v
            self.on_edit()

        return self.combo_row(title, subtitle, [(None, f"Default ({'on' if default else 'off'})"), (True, "On"), (False, "Off")], obj.get(key), changed)

    def color_row(self, title, obj, key, default):
        row = Adw.ActionRow(title=title)
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue, rgba.alpha = parse_color(obj.get(key, default))
        btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True), rgba=rgba, valign=Gtk.Align.CENTER)
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Use the default", css_classes=["flat"], sensitive=key in obj)

        def changed(b, _p):
            c = b.get_rgba()
            v = "#%02x%02x%02x" % tuple(half_up(x * 255) for x in (c.red, c.green, c.blue))
            if c.alpha < 0.999:
                v += "%02x" % half_up(c.alpha * 255)
            obj[key] = v
            reset.set_sensitive(True)
            self.on_edit()

        handler = btn.connect("notify::rgba", changed)

        def do_reset(_b):
            obj.pop(key, None)
            c = Gdk.RGBA()
            c.red, c.green, c.blue, c.alpha = parse_color(default)
            btn.handler_block(handler)
            btn.set_rgba(c)
            btn.handler_unblock(handler)
            reset.set_sensitive(False)
            self.on_edit()

        reset.connect("clicked", do_reset)
        row.add_suffix(reset)
        row.add_suffix(btn)
        return row

    @staticmethod
    def group(title="", description=""):
        return Adw.PreferencesGroup(title=title, description=description or None)

    # ---- refresh ----
    def refresh_all(self):
        self.refresh_layers()
        self.refresh_settings()
        self.refresh_editor()
        self.refresh_status()
        self.bar.queue_draw()

    def refresh_status(self):
        dirty = self.m.dirty
        self.title.set_subtitle("Unsaved changes" if dirty else "Saved")
        self.save_btn.set_sensitive(dirty)
        layer = self.m.cfg["Layers"][self.L]
        info = self.m.layout(self.L)
        units = sum(b.get("Stretch", 1) for b in layer)
        self.info.set_label(f"{units:g} buttons wide · 1 button ≈ {info['slot'] * info['k'] + (info['k'] - 1) * self.m.style(self.L)['ButtonSpacing']:.1f} px · grid ×{info['k']} ({info['n']} slots)")

    def layer_subtitle(self, i):
        layer = self.m.cfg["Layers"][i]
        return f"{len(layer)} items · {sum(b.get('Stretch', 1) for b in layer):g} wide"

    def refresh_layers(self):
        self.loading_layers = True
        self.layer_list.remove_all()
        self.layer_rows = []
        for i in range(len(self.m.cfg["Layers"])):
            row = Adw.ActionRow(title=self.m.layer_name(i), subtitle=self.layer_subtitle(i))
            row.add_prefix(Gtk.Label(label=str(i), css_classes=["dim-label", "numeric"]))
            self.layer_list.append(row)
            self.layer_rows.append(row)
        self.layer_list.select_row(self.layer_list.get_row_at_index(self.L))
        self.loading_layers = False
        self.del_layer_btn.set_sensitive(len(self.m.cfg["Layers"]) > 1)
        self.refresh_layer_detail()

    def refresh_layer_rows(self):
        for i, row in enumerate(self.layer_rows):
            row.set_title(self.m.layer_name(i))
            row.set_subtitle(self.layer_subtitle(i))

    def refresh_layer_detail(self):
        clear(self.layer_detail)
        name = Adw.EntryRow(title="Screen name", text=self.m.meta["layerNames"][self.L])

        def renamed(r):
            self.m.meta["layerNames"][self.L] = r.get_text()
            self.on_edit()

        name.connect("changed", renamed)
        g = self.group()
        g.add(name)
        self.layer_detail.append(g)
        g = self.group("This screen only", "Empty = use the global style")
        gs = self.m.global_style()
        over = self.m.layer_style(self.L)
        for key, label in LAYER_STYLE_KEYS:
            g.add(self.number_row(label, "", over, key, gs[key]))
        self.layer_detail.append(g)

    def refresh_settings(self):
        clear(self.general_box)
        g = self.group()
        for key, kind, default, title, desc in GENERAL:
            if kind == "motion":
                g.add(self.combo_row(title, desc, [(None, "Smooth (default)"), ("smooth", "Smooth"), ("expressive", "Expressive")],
                                     self.m.cfg.get(key), self.setter(self.m.cfg, key)))
            elif kind == "bool":
                g.add(self.bool_row(title, desc, self.m.cfg, key, default))
            elif kind == "text":
                row = self.text_row(title, self.m.cfg, key)
                g.add(row)
            else:
                g.add(self.number_row(title, desc, self.m.cfg, key, default, kind))
        self.general_box.append(g)

        clear(self.style_box)
        style = self.m.cfg.setdefault("Style", {})
        g = self.group()
        for key, kind, title, desc in STYLES:
            if kind == "color":
                g.add(self.color_row(title, style, key, DEFAULT_STYLE[key]))
            else:
                g.add(self.number_row(title, desc, style, key, DEFAULT_STYLE[key]))
        self.style_box.append(g)
        self.refresh_json()

    def refresh_json(self):
        if self.stack.get_visible_child_name() == "json":
            self.json_view.get_buffer().set_text(json.dumps(self.m.cfg, indent=2, ensure_ascii=False))

    def queue_editor(self):
        if not self.rebuild_id:
            self.rebuild_id = GLib.idle_add(self.rebuild_editor_idle)

    def rebuild_editor_idle(self):
        self.rebuild_id = 0
        self.refresh_editor()
        self.bar.queue_draw()
        self.refresh_status()
        return GLib.SOURCE_REMOVE

    # ---- preview ----
    def on_bar_resize(self, area, width, _h):
        self.zoom = width / (BAR_W + 16)
        height = max(30, int(BAR_H * self.zoom) + 8)
        if area.get_content_height() != height:
            GLib.idle_add(area.set_content_height, height)

    def draw_bar(self, _area, cr, _w, _h):
        z, m = self.zoom, self.m
        st = m.style(self.L)
        cr.translate(8 * z, 4)
        cr.scale(z, z)
        rounded(cr, 0, 0, BAR_W, BAR_H, 10, 10)
        cr.set_source_rgba(*parse_color(st["Background"], (0, 0, 0, 1)))
        cr.fill()
        info = m.layout(self.L)
        self.items = info["items"]
        h = BAR_H * st["HeightPercent"] / 100
        top = (BAR_H - h) / 2
        joined = st["ButtonSpacing"] == 0
        n = len(self.items)
        for i, (b, left0, width0) in enumerate(self.items):
            t = btn_type(b)
            il, ir = max(0, b.get("InsetLeft", 0)), max(0, b.get("InsetRight", 0))
            left, width = left0 + il, max(1, width0 - il - ir)
            prev, nxt = self.items[i - 1][0] if i else None, self.items[i + 1][0] if i + 1 < n else None
            rl = not joined or prev is None or btn_type(prev) == "spacer" or il > 0 or prev.get("InsetRight", 0) > 0
            rr = not joined or nxt is None or btn_type(nxt) == "spacer" or ir > 0 or nxt.get("InsetLeft", 0) > 0
            r = min(b.get("CornerRadius", st["CornerRadius"]), h / 2, width / 2)
            ghost = t == "command" and "media-button" in b.get("Command", "")
            if t == "spacer":
                cr.set_source_rgba(1, 1, 1, 0.13)
                cr.set_dash([6, 6])
                cr.set_line_width(1.5)
                cr.rectangle(left + 2, top + 2, width - 4, h - 4)
                cr.stroke()
                cr.set_dash([])
            else:
                rounded(cr, left, top, width, h, r if rl else 0, r if rr else 0)
                if t == "media":
                    cr.set_source_rgba(0.13, 0.13, 0.14, 1)
                    cr.fill()
                elif m.cfg.get("ShowButtonOutlines", True) or "Color" in b:
                    cr.set_source_rgba(*parse_color(b.get("Color", st["ButtonColor"])))
                    cr.fill()
                else:
                    cr.new_path()
                self.draw_content(cr, b, t, left, top, width, h, st)
                if ghost:
                    rounded(cr, left + 1, top + 1, width - 2, h - 2, r, r)
                    cr.set_source_rgba(1, 1, 1, 0.5)
                    cr.set_dash([8, 6])
                    cr.set_line_width(2)
                    cr.stroke()
                    cr.set_dash([])
                if joined and not rl:
                    cr.set_source_rgba(0, 0, 0, st["SeparatorOpacity"])
                    cr.rectangle(round(left), top, st["SeparatorWidth"], h)
                    cr.fill()
            if i == self.B:
                rounded(cr, left - 2, top - 2, width + 4, h + 4, r + 2, r + 2)
                cr.set_source_rgba(0.21, 0.52, 0.89, 1)
                cr.set_line_width(4)
                cr.stroke()

    def draw_content(self, cr, b, t, left, top, width, h, st):
        cx, cy = left + width / 2, top + h / 2
        iw, ih = b.get("IconWidth", st["IconSize"]), b.get("IconHeight", st["IconSize"])

        def icon(name, x=None):
            pb = icon_pixbuf(name, iw * 2, ih * 2)  # drawn at 2x, scaled by the preview
            if not pb:
                return
            cr.save()
            w, hh = pb.get_width() / 2, pb.get_height() / 2
            cr.translate((cx if x is None else x) - w / 2, cy - hh / 2)
            cr.scale(0.5, 0.5)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
            cr.restore()

        def text(s, size=None, alpha=1.0):
            c = parse_color(b.get("TextColor", st["TextColor"]), (1, 1, 1, 1))
            cr.set_source_rgba(c[0], c[1], c[2], alpha)
            cr.select_font_face(self.m.cfg.get("FontFamily") or "sans", cairo.FONT_SLANT_NORMAL,
                                cairo.FONT_WEIGHT_BOLD if self.m.cfg.get("FontBold", True) else cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(size or st["FontSize"])
            ext = cr.text_extents(s)
            if ext.width > width - 8:  # keep long labels inside the button
                cr.set_font_size((size or st["FontSize"]) * (width - 8) / ext.width)
                ext = cr.text_extents(s)
            cr.move_to(cx - ext.width / 2 - ext.x_bearing, cy - ext.height / 2 - ext.y_bearing)
            cr.show_text(s)

        if t == "icon":
            icon(b.get("Icon") or b.get("Svg"))
        elif t == "text":
            text(b.get("Text", ""))
        elif t == "time":
            text(time.strftime(b.get("Time") or "%H:%M"))
        elif t == "battery":
            text("84%")
        elif t in ("cpu", "gpu"):
            text(sensor_preview_text(b, t))
        elif t == "media":
            text("Media player", 24, 0.6)
        elif t == "command":
            if "media-button" in b.get("Command", ""):
                icon("play_pause")
            else:
                text(b.get("Command", "").split()[-1].rsplit("/", 1)[-1] if b.get("Command", "").strip() else "script", 24, 0.7)
        elif t == "slider":
            ix = left + 12 + iw / 2
            icon(b.get("Icon", "volume_up"), ix if b.get("SliderOpen") else None)
            if b.get("SliderOpen"):
                x0, x1 = left + 12 + iw + 22, left + width - 24
                cr.set_source_rgba(0.35, 0.35, 0.36, 1)
                rounded(cr, x0, cy - 7, x1 - x0, 14, 7, 7)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 1)
                rounded(cr, x0, cy - 7, (x1 - x0) * 0.6, 14, 7, 7)
                cr.fill()

    def bar_index(self, x):
        px = (x - 8 * self.zoom) / self.zoom
        for i, (_b, left, width) in enumerate(self.items):
            if left <= px < left + width:
                return i
        return -1

    def on_bar_click(self, _g, _n, x, _y):
        if self.ra:
            self.drag_from = -1
            return
        self.drag_from = self.bar_index(x)
        self.drag_x = x
        i = self.drag_from
        if i != self.B and i >= 0:
            self.B = i
            self.refresh_editor()
            self.bar.queue_draw()

    def on_bar_drag(self, _g, dx, _dy):
        if abs(dx) < 12 or getattr(self, "drag_from", -1) < 0:
            return
        target = self.bar_index(self.drag_x + dx)
        src = self.drag_from
        layer = self.m.cfg["Layers"][self.L]
        if target < 0 or target == src:
            return
        layer.insert(target, layer.pop(src))
        self.B = target
        self.on_edit(rebuild=True)

    # ---- button editor ----
    def refresh_editor(self):
        clear(self.editor)
        layer = self.m.cfg["Layers"][self.L]
        if not 0 <= self.B < len(layer):
            self.B = -1
            self.editor.append(Adw.StatusPage(title="No button selected", description="Click a button in the preview to edit it.", vexpand=True))
            return
        b, t = layer[self.B], btn_type(layer[self.B])
        st = self.m.style(self.L)

        nav = Gtk.Box(spacing=6)
        left = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Move left", sensitive=self.B > 0)
        left.connect("clicked", lambda *_: self.move_button(-1))
        right = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Move right", sensitive=self.B < len(layer) - 1)
        right.connect("clicked", lambda *_: self.move_button(1))
        dup = Gtk.Button(label="Duplicate")
        dup.connect("clicked", lambda *_: self.duplicate_button())
        rm = Gtk.Button(label="Delete", css_classes=["destructive-action"], halign=Gtk.Align.END, hexpand=True)
        rm.connect("clicked", lambda *_: self.delete_button())
        for w in (left, right, dup, rm):
            nav.append(w)
        self.editor.append(nav)

        g = self.group("Button")

        def change_type(v):
            if v != btn_type(self.m.cfg["Layers"][self.L][self.B]):
                self.m.cfg["Layers"][self.L][self.B] = self.set_type(self.m.cfg["Layers"][self.L][self.B], v)
                self.on_edit(rebuild=True)

        g.add(self.combo_row("Type", "", TYPES, t, change_type))
        g.add(self.number_row("Width", "In buttons, any value above 0 (0.33, 0.5, 1.25…)", b, "Stretch", 1))
        self.editor.append(g)

        if t == "icon":
            self.editor.append(self.icon_picker(b, "Svg" if "Svg" in b else "Icon"))
        elif t == "text":
            g = self.group()
            g.add(self.text_row("Label", b, "Text"))
            self.editor.append(g)
        elif t == "time":
            g = self.group("Clock", "strftime format: %H:%M, %a %d %b …")
            g.add(self.text_row("Format", b, "Time"))
            g.add(self.text_row("Locale", b, "Locale"))
            self.editor.append(g)
        elif t == "battery":
            g = self.group("Battery")

            def set_mode(v):
                b["Battery"] = v
                self.on_edit()

            g.add(self.combo_row("Show", "", [("both", "Icon and percentage"), ("icon", "Icon"), ("percentage", "Percentage")], b.get("Battery"), set_mode))
            self.editor.append(g)
        elif t in ("cpu", "gpu"):
            key = "Cpu" if t == "cpu" else "Gpu"
            spec = set((b.get(key) or "").split())
            g = self.group(t.upper(), None if spec else "Nothing selected yet shows Celsius (the daemon's own fallback)")

            def set_spec(update, b=b, key=key):
                spec = set((b.get(key) or "").split())
                update(spec)
                text = " ".join(sorted(spec))
                if text:
                    b[key] = text
                else:
                    b.pop(key, None)
                self.on_edit()

            def set_unit(v, b=b, key=key, set_spec=set_spec):
                def upd(s):
                    s.discard("celsius")
                    s.discard("fahrenheit")
                    if v:
                        s.add(v)
                set_spec(upd)

            unit = "fahrenheit" if "fahrenheit" in spec else ("celsius" if "celsius" in spec else None)
            g.add(self.combo_row("Temperature", "", [(None, "Hidden"), ("celsius", "Celsius"), ("fahrenheit", "Fahrenheit")], unit, set_unit))

            def set_watts(v, set_spec=set_spec):
                def upd(s):
                    s.discard("watts")
                    s.discard("power")
                    if v:
                        s.add("watts")
                set_spec(upd)

            g.add(self.switch_row("Power draw", "Watts", "watts" in spec or "power" in spec, set_watts))

            def set_usage(v, b=b, spec=spec, set_spec=set_spec):
                was_on = "usage" in spec or "load" in spec
                def upd(s):
                    s.discard("usage")
                    s.discard("load")
                    if v:
                        s.add("usage")
                set_spec(upd)
                if v and not was_on and b.get("Stretch", 1) < 2:
                    self.toast(f"Usage needs more room to read -- widen this button (now {b.get('Stretch', 1):g}; try 2 or more).")

            g.add(self.switch_row("Usage", "Utilization %", "usage" in spec or "load" in spec, set_usage))
            g.add(self.bool_row("Label", "", b, key + "Label", True))
            self.editor.append(g)
        elif t == "command":
            g = self.group("Script", 'Prints text or JSON {"text", "icon", "color"}. No output hides the button; it collapses into the nearest empty space.')
            g.add(self.text_row("Command", b, "Command"))
            g.add(self.number_row("Interval", "Seconds between runs", b, "Interval", 2))
            g.add(self.text_row("Expand command", b, "ExpandCommand"))
            g.add(self.number_row("Expanded width", "In buttons, used with Expand", b, "ExpandStretch", 2))
            self.editor.append(g)
        elif t == "slider":
            self.editor.append(self.icon_picker(b, "Icon"))
            g = self.group("Slider")
            g.add(self.text_row("Icon below 50%", b, "SliderLowIcon"))
            g.add(self.text_row("Muted icon", b, "SliderMuteIcon"))
            g.add(self.text_row("Read value command", b, "SliderGet"))
            g.add(self.text_row("Set value command ({} = value)", b, "SliderSet"))
            g.add(self.text_row("Mute command ({} = toggle or 0)", b, "SliderMute"))
            g.add(self.number_row("Interval", "Seconds between reads", b, "Interval", 2))
            g.add(self.number_row("Open width", "In buttons", b, "SliderStretch", 2))
            g.add(self.bool_row("Always open", "", b, "SliderOpen", False))
            self.editor.append(g)
            self.editor.append(self.chips("Presets", [
                ("Volume", self.preset_volume), ("Screen brightness", self.preset_brightness), ("Keyboard light", self.preset_keyboard)]))
        elif t == "media":
            g = self.group("Media player", "Command to run when the panel is tapped outside its controls")
            g.add(self.text_row("On tap", b, "OnClick"))
            self.editor.append(g)
        if t not in ("spacer", "media"):
            self.editor.append(self.action_group(b))
        if t != "spacer":
            g = self.group("Appearance")
            g.add(self.color_row("Color", b, "Color", st["ButtonColor"]))
            g.add(self.color_row("Pressed color", b, "ColorActive", st["ButtonColorActive"]))
            g.add(self.color_row("Text color", b, "TextColor", st["TextColor"]))
            g.add(self.number_row("Corner radius", "This button only", b, "CornerRadius", st["CornerRadius"]))
            g.add(self.number_row("Space on the left", "Free pixels; this side rounds and stops joining its neighbour", b, "InsetLeft", 0))
            g.add(self.number_row("Space on the right", "", b, "InsetRight", 0))
            g.add(self.number_row("Icon width", "", b, "IconWidth", st["IconSize"], "int"))
            g.add(self.number_row("Icon height", "", b, "IconHeight", st["IconSize"], "int"))
            self.editor.append(g)
        self.editor.append(self.raw_button_editor(b))

    def set_type(self, b, kind):
        keep = {k: v for k, v in b.items() if k not in CONTENT_KEYS}
        if kind == "spacer":
            keep.pop("Action", None)
            keep.pop("OnClick", None)
        keep.update(self.new_button(kind))
        return keep

    def new_button(self, kind):
        if kind == "icon":
            return {"Icon": "launchpad" if "launchpad" in self.icons else (self.icons[0] if self.icons else "search"), "OnClick": "Action"}
        if kind == "text":
            return {"Text": "Text", "OnClick": "Action"}
        if kind == "slider":
            return {"Icon": "volume_up", "SliderGet": VOLUME_GET, "SliderSet": "wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ {}%",
                    "SliderMute": "wpctl set-mute @DEFAULT_AUDIO_SINK@ {}", "Interval": 1}
        if kind == "media":
            return {"Media": True}
        if kind == "mediabtn":
            out = {"Command": MEDIA_SCRIPT, "Interval": 1, "OnClick": "Action"}
            for i, layer in enumerate(self.m.cfg["Layers"]):
                if any(x.get("Media") for x in layer):
                    out["Action"] = f"Layer{i}"
                    break
            return out
        if kind == "weather":
            # wttr.in is rate-limited; nothing shorter than several minutes.
            return {"Command": WEATHER_SCRIPT, "Interval": 900}
        if kind == "ttytoggle":
            return {
                "Icon": "utilities-terminal-symbolic",
                "Theme": "Adwaita",
                "OnClick": TTY_TOGGLE_SCRIPT,
            }
        return {"spacer": {}, "command": {"Command": "date +%H:%M", "Interval": 30}, "time": {"Time": "%H:%M"},
                "battery": {"Battery": "both"}, "cpu": {"Cpu": "celsius"}, "gpu": {"Gpu": "celsius"}}[kind]

    def preset_volume(self, b):
        b.update(self.new_button("slider"))
        b.update({"SliderLowIcon": "volume_down", "SliderMuteIcon": "volume_off"})

    @staticmethod
    def preset_brightness(b):
        b.pop("SliderMute", None)
        b.pop("SliderMuteIcon", None)
        b.update({"Icon": "brightness_high", "SliderLowIcon": "brightness_low", "Interval": 1,
                  "SliderGet": "brightnessctl -m -d gmux_backlight | cut -d, -f4 | tr -d %",
                  "SliderSet": "brightnessctl -q -n1 -d gmux_backlight set {}%"})

    @staticmethod
    def preset_keyboard(b):
        b.pop("SliderMute", None)
        b.pop("SliderMuteIcon", None)
        b.update({"Icon": "backlight_high", "SliderLowIcon": "backlight_low", "Interval": 1,
                  "SliderGet": "brightnessctl -m -d ':white:kbd_backlight' | cut -d, -f4 | tr -d %",
                  "SliderSet": "brightnessctl -q -d ':white:kbd_backlight' set {}%"})

    def chips(self, title, entries):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6, max_children_per_line=3)
        b = self.m.cfg["Layers"][self.L][self.B]
        for label, fn in entries:
            btn = Gtk.Button(label=label)

            def clicked(_w, f=fn):
                f(b)
                self.on_edit(rebuild=True)

            btn.connect("clicked", clicked)
            flow.append(btn)
        box.append(flow)
        return box

    def icon_picker(self, b, key):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        g = self.group("Icon", f"Add white .svg files to {str(CONF_DIR).replace(str(Path.home()), '~')}/ to see them here")
        entry = self.text_row("Icon name", b, key)
        g.add(entry)
        box.append(g)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=9, min_children_per_line=7, column_spacing=2, row_spacing=2, homogeneous=True, activate_on_single_click=True)
        current = None
        for name in self.icons:
            pb = icon_pixbuf(name, 24, 24)
            if pb is None:
                continue
            img = Gtk.Image.new_from_paintable(Gdk.Texture.new_for_pixbuf(pb))
            img.set_pixel_size(24)
            child = Gtk.FlowBoxChild(child=img, tooltip_text=name)
            child.icon_name_value = name
            flow.append(child)
            if name == b.get(key):
                current = child
        if current:
            flow.select_child(current)
        flow.connect("child-activated", lambda _f, c: entry.set_text(c.icon_name_value))
        sw = Gtk.ScrolledWindow(child=flow, min_content_height=150, max_content_height=150, hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["card"])
        box.append(sw)
        return box

    def action_group(self, b):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        g = self.group("Action", "A key (VolumeUp, F5…), LayerN to open a screen, or several separated by commas for a chord")
        cur = b.get("Action", "")
        entry = Adw.EntryRow(title="Sends", text=", ".join(cur) if isinstance(cur, list) else cur)

        def act_changed(r):
            parts = [p.strip() for p in r.get_text().split(",") if p.strip()]
            if not parts:
                b.pop("Action", None)
            else:
                b["Action"] = parts[0] if len(parts) == 1 else parts
            self.on_edit()

        entry.connect("changed", act_changed)
        pop_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        for key in KEYS:
            pop_list.append(Gtk.Label(label=key, xalign=0, margin_start=8, margin_end=8, margin_top=3, margin_bottom=3))
        popover = Gtk.Popover(child=Gtk.ScrolledWindow(child=pop_list, min_content_height=280, min_content_width=220))

        def picked(_l, row):
            entry.set_text(row.get_child().get_label())
            popover.popdown()

        pop_list.connect("row-activated", picked)
        entry.add_suffix(Gtk.MenuButton(popover=popover, icon_name="view-more-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Pick a key"))
        g.add(entry)

        click = b.get("OnClick")
        mode = None if click is None else click if click in ("Action", "Expand") else "cmd"
        cmd = Adw.EntryRow(title="Command", text=click if mode == "cmd" else "", visible=mode == "cmd")

        def set_mode(v):
            cmd.set_visible(v == "cmd")
            if v is None:
                b.pop("OnClick", None)
            elif v == "cmd":
                b["OnClick"] = cmd.get_text() or "true"
            else:
                b["OnClick"] = v
            self.on_edit()

        def cmd_changed(r):
            if r.get_visible():
                b["OnClick"] = r.get_text() or "true"
                self.on_edit()

        cmd.connect("changed", cmd_changed)
        g.add(self.combo_row("On press", "", [("Action", "Highlight while pressed"), (None, "No highlight"), ("cmd", "Run a shell command"), ("Expand", "Expand (script widget)")], mode, set_mode))
        g.add(cmd)
        box.append(g)

        def run(c):
            return lambda x: (x.pop("Action", None), x.update({"OnClick": DMS_RUN + c}))

        def send(a):
            return lambda x: x.update({"Action": a, "OnClick": "Action"})

        quick = [("Launchpad", run("dms ipc call spotlight toggle")), ("Close screen", send("Layer0")),
                 ("Play/Pause", send("PlayPause")), ("Firefox", run("firefox"))]
        quick += [(f"Open: {self.m.layer_name(i)}", send(f"Layer{i}")) for i in range(len(self.m.cfg["Layers"])) if i != self.L]
        box.append(self.chips("Quick actions", quick))
        return box

    def raw_button_editor(self, b):
        view = Gtk.TextView(monospace=True, top_margin=6, left_margin=6, css_classes=["card"])
        view.get_buffer().set_text(json.dumps(b, indent=2, ensure_ascii=False))
        apply_btn = Gtk.Button(label="Apply", halign=Gtk.Align.START)

        def apply(_w):
            buf = view.get_buffer()
            try:
                data = json.loads(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
            except ValueError as e:
                self.toast(f"Invalid JSON: {e}")
                return
            self.m.cfg["Layers"][self.L][self.B] = data
            self.on_edit(rebuild=True)

        apply_btn.connect("clicked", apply)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        inner.append(view)
        inner.append(apply_btn)
        return Gtk.Expander(label="Advanced: JSON for this button", child=inner)

    # ---- actions ----
    def on_layer_selected(self, _l, row):
        if self.loading_layers or row is None:
            return
        i = row.get_index()
        if i != self.L:
            self.L, self.B = i, -1
            self.refresh_layer_detail()
            self.refresh_editor()
            self.bar.queue_draw()
            self.refresh_status()

    def add_button(self):
        kind = ADD_TYPES[self.add_type.get_selected()][0]
        layer = self.m.cfg["Layers"][self.L]
        at = self.B + 1 if self.B >= 0 else len(layer)
        layer.insert(at, self.new_button(kind))
        self.B = at
        self.on_edit(rebuild=True)

    def move_button(self, d):
        layer = self.m.cfg["Layers"][self.L]
        j = self.B + d
        layer[self.B], layer[j] = layer[j], layer[self.B]
        self.B = j
        self.on_edit(rebuild=True)

    def duplicate_button(self):
        layer = self.m.cfg["Layers"][self.L]
        layer.insert(self.B + 1, copy.deepcopy(layer[self.B]))
        self.B += 1
        self.on_edit(rebuild=True)

    def delete_button(self):
        layer = self.m.cfg["Layers"][self.L]
        del layer[self.B]
        self.B = min(self.B, len(layer) - 1)
        self.on_edit(rebuild=True)

    def add_layer(self):
        m = self.m
        m.cfg["Layers"].append([{"Icon": "close", "Action": "Layer0", "OnClick": "Action"}, {"Stretch": 3}])
        m.meta["layerNames"].append("")
        m.meta["layerScale"].append(1)
        if "LayerStyles" in m.cfg:
            m.cfg["LayerStyles"].append({})
        self.L, self.B = len(m.cfg["Layers"]) - 1, -1
        self.refresh_after_layers()

    def move_layer(self, d):
        c = self.L + d
        if 0 <= c < len(self.m.cfg["Layers"]):
            self.m.swap_layers(self.L, c)
            self.L = c
            self.refresh_after_layers()

    def delete_layer(self):
        if len(self.m.cfg["Layers"]) > 1:
            self.m.delete_layer(self.L)
            self.L, self.B = max(0, self.L - 1), -1
            self.refresh_after_layers()

    def refresh_after_layers(self):
        self.refresh_layers()
        self.refresh_editor()
        self.refresh_status()
        self.bar.queue_draw()

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def save(self):
        problems = self.m.problems()
        if problems:
            self.toast(problems[0])
            return False
        try:
            backup = self.m.save()
        except (OSError, ValueError, TypeError) as e:
            self.toast(f"Could not save: {e}")
            return False
        self.refresh_status()
        self.toast(f"Saved. Backup: {backup}")
        return True

    def revert(self):
        if not self.m.dirty:
            self.do_revert()
            return
        d = Adw.AlertDialog(heading="Discard changes?", body="Your unsaved changes will be lost.")
        d.add_response("cancel", "Cancel")
        d.add_response("discard", "Discard")
        d.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _d, r: self.do_revert() if r == "discard" else None)
        d.present(self)

    def do_revert(self):
        self.m.load()
        self.L, self.B = min(self.L, len(self.m.cfg["Layers"]) - 1), -1
        self.refresh_all()

    def apply_json(self):
        buf = self.json_view.get_buffer()
        try:
            data = json.loads(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
            if not isinstance(data, dict) or not isinstance(data.get("Layers"), list) or not data["Layers"]:
                raise ValueError("expected an object with a non-empty Layers list")
        except ValueError as e:
            self.toast(f"Invalid JSON: {e}")
            return
        self.m.cfg = data
        n = len(data["Layers"])
        self.m.meta["layerNames"] = (self.m.meta["layerNames"] + [""] * n)[:n]
        self.m.meta["layerScale"] = (self.m.meta["layerScale"] + [1] * n)[:n]
        self.L, self.B = min(self.L, n - 1), -1
        self.refresh_all()

    # ---- rearrange on the Touch Bar itself ----
    def toggle_rearrange(self):
        if self.ra:
            self.end_rearrange(True)
        else:
            self.start_rearrange()

    def start_rearrange(self):
        if not self.m.cfg["Layers"][self.L]:
            self.toast("This screen has no buttons to rearrange.")
            return
        if self.m.dirty and not self.save():  # the bar must show what is on disk
            return
        try:
            REARRANGE_RESULT.unlink(missing_ok=True)
            REARRANGE_REQUEST.write_text(str(self.L))
        except OSError as e:
            self.toast(f"Could not start: {e}")
            return
        self.ra = {"layer": self.L, "base": copy.deepcopy(self.m.cfg["Layers"][self.L]), "mtime": 0,
                   "poll": GLib.timeout_add(120, self.poll_rearrange),
                   "beat": GLib.timeout_add_seconds(5, self.beat_rearrange)}
        self.B = -1
        self.set_rearranging_ui(True)

    def beat_rearrange(self):
        """Tell the daemon the app is alive; without this it leaves the mode."""
        try:
            os.utime(REARRANGE_REQUEST)
        except OSError:
            pass
        return GLib.SOURCE_CONTINUE

    def poll_rearrange(self):
        try:
            mtime = REARRANGE_RESULT.stat().st_mtime_ns
            if mtime != self.ra["mtime"]:
                self.ra["mtime"] = mtime
                data = json.loads(REARRANGE_RESULT.read_text())
                base = self.ra["base"]
                if data.get("layer") == self.ra["layer"] and sorted(data.get("order", [])) == list(range(len(base))):
                    self.m.cfg["Layers"][self.ra["layer"]] = [copy.deepcopy(base[i]) for i in data["order"]]
                    self.on_edit(rebuild=True)
        except (OSError, ValueError, TypeError):
            pass  # no result yet, or caught mid-update
        return GLib.SOURCE_CONTINUE

    def end_rearrange(self, keep):
        ra = self.ra
        if not ra:
            return
        self.poll_rearrange()
        if keep and self.m.dirty:
            if not self.save():
                return
        elif not keep:
            self.m.cfg["Layers"][ra["layer"]] = ra["base"]
        GLib.source_remove(ra["poll"])
        GLib.source_remove(ra["beat"])
        self.ra = None
        REARRANGE_REQUEST.unlink(missing_ok=True)
        self.set_rearranging_ui(False)
        self.on_edit(rebuild=True)

    def set_rearranging_ui(self, on):
        self.ra_banner.set_revealed(on)
        self.rearrange_btn.set_label("Done" if on else "Rearrange")
        if on:
            self.rearrange_btn.add_css_class("suggested-action")
        else:
            self.rearrange_btn.remove_css_class("suggested-action")
        for w in (self.columns, self.add_row, self.revert_btn, self.capture_btn):
            w.set_sensitive(not on)
        self.save_btn.set_visible(not on)
        self.bar.queue_draw()

    def capture(self):
        self.capture_btn.set_sensitive(False)
        self.capture_btn.set_label("Capturing…")

        def worker():
            try:
                path = capture_frame()
            except OSError:
                path = None
            GLib.idle_add(self.show_capture, path)

        threading.Thread(target=worker, daemon=True).start()

    def show_capture(self, path):
        self.capture_btn.set_sensitive(True)
        self.capture_btn.set_label("Capture bar")
        try:
            if not path:
                raise GLib.Error("no frame")
            pb = GdkPixbuf.Pixbuf.new_from_file(path).rotate_simple(GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE)
        except GLib.Error:
            self.toast("Capture failed. Is the Touch Bar daemon running?")
            return GLib.SOURCE_REMOVE
        self.capture_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
        self.capture_pic.set_size_request(-1, max(24, int(BAR_H * self.zoom)))
        self.capture_pic.set_visible(True)
        return GLib.SOURCE_REMOVE

    def on_close(self, _w):
        if self.ra:
            self.end_rearrange(False)
        if self.closing or not self.m.dirty:
            return False
        d = Adw.AlertDialog(heading="Save changes?", body="You have unsaved changes to the Touch Bar.")
        d.add_response("cancel", "Cancel")
        d.add_response("discard", "Discard")
        d.add_response("save", "Save")
        d.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("save")

        def answered(_d, r):
            if r == "save" and not self.save():
                return
            if r in ("save", "discard"):
                self.closing = True
                self.close()

        d.connect("response", answered)
        d.present(self)
        return True

    # ---- test hook: render the window to a PNG, then quit ----
    def screenshot(self, path):
        if self.ra:
            GLib.source_remove(self.ra["poll"])
            GLib.source_remove(self.ra["beat"])
            REARRANGE_REQUEST.unlink(missing_ok=True)
        w, h = self.get_width(), self.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(self).snapshot(snap, w, h)
        self.get_native().get_renderer().render_texture(snap.to_node(), None).save_to_png(path)
        self.closing = True
        self.get_application().quit()
        return GLib.SOURCE_REMOVE


def clear(box):
    while (child := box.get_first_child()) is not None:
        box.remove(child)


def selftest():
    """Load and re-render without writing: the file must come back identical."""
    m = Model()
    out, _ = m.render()
    text = tbtoml.dump(out, header=m.header or DEFAULT_HEADER)
    same = tomllib.loads(text) == tomllib.loads(tbtoml.CONFIG.read_text())
    print(f"selftest: round-trip {'identical' if same else 'DIFFERENT'}; {len(m.cfg['Layers'])} screens; problems: {m.problems()}")
    return 0 if same else 1


def main():
    opts, argv = {}, sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    for flag, key, conv in (("--screenshot", "shot", str), ("--layer", "layer", int), ("--button", "button", int), ("--tab", "tab", str)):
        if flag in argv:
            opts[key] = conv(argv[argv.index(flag) + 1])
    opts["rearrange"] = "--rearrange" in argv
    flags = Gio.ApplicationFlags.NON_UNIQUE if "shot" in opts else Gio.ApplicationFlags.DEFAULT_FLAGS
    app = Adw.Application(application_id=APP_ID, flags=flags)

    def activate(a):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)  # icons are white
        win = a.get_active_window() or Window(a, opts)
        win.present()
        if "tab" in opts:
            win.stack.set_visible_child_name(opts["tab"])
        if "shot" in opts:
            if opts.get("rearrange"):
                win.start_rearrange()
            GLib.timeout_add(1500, win.screenshot, opts["shot"])

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
