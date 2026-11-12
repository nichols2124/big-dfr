"""Read/write ~/.config/big-dfr/config.toml as plain data.

The daemon's config is: top-level scalar keys, then `Layers` (an array of
arrays of inline tables, one button per table), then a `[Style]` table, which
must come last. tomllib reads it; `dump` writes it back in that shape, one
button per line so the file stays readable and hand-editable.
"""
import json
import math
import tomllib
from pathlib import Path

import os
# TOUCHBAR_CONFIG overrides the path for isolated testing; unset in normal use.
CONFIG = Path(os.environ["TOUCHBAR_CONFIG"]) if "TOUCHBAR_CONFIG" in os.environ \
    else Path.home() / ".config/big-dfr/config.toml"

# Keys in a fixed, readable order; anything else follows alphabetically.
TOP_ORDER = [
    "MediaLayerDefault", "MotionStyle", "LayerSwipe", "LayerTimeout", "DimTimeout", "OffTimeout",
    "AdaptiveBrightness", "ActiveBrightness", "DoublePressSwitchLayers",
    "ShowButtonOutlines", "EnablePixelShift", "FontFamily", "FontBold",
    "VisibleButtons", "ScrollLoop", "ScrollRubberBand", "PinnedIgnoreScroll",
    "PinnedIgnoreLayerSwipe", "LyricOffset", "MediaCoverBlur", "MediaArtCache",
    "MediaLyricsCache",
]
BUTTON_ORDER = [
    "Icon", "Text", "Caption", "Time", "Battery", "Cpu", "Gpu", "Media", "Dock", "DockApps", "Command",
    "Action", "OnClick", "Stretch", "Pinned",
    "SliderGet", "SliderSet", "SliderMute", "SliderLowIcon", "SliderMuteIcon",
    "SliderStretch", "SliderOpen", "Interval", "ExpandCommand", "ExpandStretch",
    "IconWidth", "IconHeight", "Theme", "Locale", "CpuLabel", "GpuLabel",
    "Color", "ColorActive", "TextColor",
]
STYLE_ORDER = [
    "Background", "BackgroundImage", "BackgroundImageBlur", "ButtonColor",
    "ButtonColorActive", "TextColor", "ButtonSpacing", "EdgePadding",
    "CornerRadius", "SeparatorWidth", "SeparatorOpacity", "HeightPercent",
    "FontSize", "IconSize", "BatteryChargingColor", "BatteryLowColor",
]


def load(path=CONFIG):
    with open(path, "rb") as f:
        return tomllib.load(f)


def value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError("non-finite float")
        return repr(v)
    if isinstance(v, str):
        # JSON string escapes are valid TOML basic-string escapes.
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(value(x) for x in v) + "]"
    if isinstance(v, dict):
        return inline(v, [])
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def ordered(d, order):
    keys = [k for k in order if k in d]
    keys += sorted(k for k in d if k not in order)
    return keys


def inline(d, order):
    if not d:
        return "{}"
    return "{ " + ", ".join(f"{k} = {value(d[k])}" for k in ordered(d, order)) + " }"


def dump(cfg, header=None):
    out = []
    if header:
        out += [f"# {line}" if line else "#" for line in header.splitlines()]
        out.append("")
    for k in ordered(cfg, TOP_ORDER):
        if k in ("Layers", "PrimaryLayerKeys", "MediaLayerKeys", "Style"):
            continue
        out.append(f"{k} = {value(cfg[k])}")
    for k in ("PrimaryLayerKeys", "MediaLayerKeys"):
        if k in cfg:
            out.append("")
            out.append(f"{k} = [")
            out += [f"    {inline(b, BUTTON_ORDER)}," for b in cfg[k]]
            out.append("]")
    if "Layers" in cfg:
        out.append("")
        out.append("Layers = [")
        for layer in cfg["Layers"]:
            out.append("  [")
            out += [f"    {inline(b, BUTTON_ORDER)}," for b in layer]
            out.append("  ],")
        out.append("]")
    if "Style" in cfg:
        out.append("")
        out.append("[Style]")
        for k in ordered(cfg["Style"], STYLE_ORDER):
            out.append(f"{k} = {value(cfg['Style'][k])}")
    text = "\n".join(out) + "\n"
    # Round-trip check: never write something the daemon would read differently.
    if tomllib.loads(text) != cfg:
        raise ValueError("TOML round-trip mismatch; refusing to write")
    return text
