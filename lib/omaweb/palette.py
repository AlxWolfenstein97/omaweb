"""Map an Omarchy ``colors.toml`` onto the Catppuccin role names.

Catppuccin userstyles ask LESS for a palette map with 26 named roles per
flavor.  Omarchy themes expose a smaller, semantic palette.  We synthesize the
missing shades so every style compiles, and we deliberately point the Catppuccin
``mauve`` role at the theme accent (``@accentColor: mauve`` in every style), so
the user's accent follows the desktop instead of Catppuccin's stock purple.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import config, util


AUTO = "auto"


ROLE_NAMES = (
    "rosewater", "flamingo", "pink", "mauve", "red", "maroon", "peach",
    "yellow", "green", "teal", "sky", "sapphire", "blue", "lavender",
    "text", "subtext1", "subtext0", "overlay2", "overlay1", "overlay0",
    "surface2", "surface1", "surface0", "base", "mantle", "crust",
)


def _parse_simple_toml(text: str) -> dict[str, str]:
    """Tiny fallback for machines without tomllib (Python < 3.11)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def read_colors(path: Path | None = None) -> tuple[dict[str, str], str]:
    path = path or (util.theme_dir() / "colors.toml")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, ""
    try:
        import tomllib  # type: ignore

        data = tomllib.loads(text)
    except Exception:
        data = _parse_simple_toml(text)
    colors = {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
    return colors, text


def theme_roots() -> list[Path]:
    """Where Omarchy installs themes, user copies first."""
    roots: list[Path] = []
    base = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(base).expanduser() if base else util.home() / ".config"
    roots.append(config_home / "omarchy" / "themes")
    omarchy = os.environ.get("OMARCHY_PATH")
    if omarchy:
        roots.append(Path(omarchy).expanduser() / "themes")
    roots.append(Path("/usr/share/omarchy/themes"))
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def find_theme(name: str) -> Path | None:
    """Return the directory of an installed theme, or ``None``.

    Names are plain directory names: anything with a path separator or a
    leading dot is rejected so a config value cannot escape the theme roots.
    """
    name = (name or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    env = os.environ.get("OMAWEB_THEME_DIR")
    if env:
        candidate = Path(env).expanduser()
        if candidate.name == name and (candidate / "colors.toml").is_file():
            return candidate
    for root in theme_roots():
        candidate = root / name
        if (candidate / "colors.toml").is_file():
            return candidate
    return None


def available_themes() -> list[str]:
    names: set[str] = set()
    for root in theme_roots():
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and (entry / "colors.toml").is_file():
                names.add(entry.name)
    return sorted(names)


def resolve_theme(name: str | None = None) -> dict[str, Any]:
    """Resolve the theme OmaWeb should paint with.

    ``auto`` follows the theme Omarchy has applied to the desktop.  A pinned
    name renders the same userstyles in a different installed theme
    (Chroma-style mixing).  A pin that no longer exists falls back to ``auto``
    instead of breaking the build; ``missing`` says so.
    """
    requested = (name or str(config.load().get("theme") or AUTO)).strip() or AUTO
    desktop = util.theme_name()
    missing = False
    if requested == AUTO:
        path = util.theme_dir()
        display = desktop
    else:
        found = find_theme(requested)
        if found is None:
            missing = True
            path = util.theme_dir()
            display = desktop
        else:
            path = found
            display = requested

    colors, text = read_colors(path / "colors.toml")
    mode = theme_mode(colors)
    roles = roles_for(colors, mode)
    return {
        "name": display,
        "requested": requested,
        "auto": requested == AUTO,
        "missing": missing,
        "desktop": desktop,
        "dir": str(path),
        "path": str(path / "colors.toml"),
        "mode": mode,
        "colors": colors,
        "roles": roles,
        "source": text,
    }


def _pick(theme: dict[str, str], *keys: str, default: str = "#000000") -> str:
    for key in keys:
        value = theme.get(key)
        if value:
            return util.norm_hex(value, default)
    return default


def roles_for(theme: dict[str, str], mode: str | None = None) -> dict[str, str]:
    """Return the 26 Catppuccin role colours for an Omarchy palette."""
    base = _pick(theme, "background", default="#1e1e2e")
    mantle = _pick(theme, "dark_background", "background", default=base)
    crust = _pick(theme, "darker_background", "dark_background", default=mantle)
    text = _pick(theme, "foreground", default="#cdd6f4")
    sub1 = _pick(theme, "light_foreground", "foreground", default=text)
    sub0 = _pick(theme, "dark_foreground", "muted", "foreground", default=text)
    muted = _pick(theme, "muted", "dark_foreground", default=sub0)
    selection = _pick(theme, "selection", "lighter_background", default=util.blend(base, mantle))

    accent = _pick(theme, "accent", "blue", default="#89b4fa")
    red = _pick(theme, "red", default="#f38ba8")
    yellow = _pick(theme, "yellow", default="#f9e2af")
    green = _pick(theme, "green", default="#a6e3a1")
    cyan = _pick(theme, "cyan", "teal", default="#94e2d5")
    blue = _pick(theme, "blue", "accent", default=accent)
    magenta = _pick(theme, "magenta", "pink", default="#f5c2e7")
    orange = _pick(theme, "orange", "yellow", default=yellow)
    brown = _pick(theme, "brown", "maroon", "red", default=red)

    # A few shades are not in Omarchy's schema.  Derive them by blending the
    # surfaces the theme does provide.  Keeping these deterministic matters:
    # the same colors.toml must always produce the same CSS (cache-friendly).
    surface0 = util.blend(base, selection, 0.45)
    surface1 = selection if selection != base else util.blend(base, mantle, 0.5)
    surface2 = util.blend(util.blend(selection, muted, 0.5), text, 0.12)
    overlay0 = util.blend(sub0, muted, 0.5)
    overlay1 = util.blend(sub0, muted, 0.75)
    overlay2 = util.blend(sub0, text, 0.25)

    return {
        "rosewater": util.blend(text, red, 0.15),
        "flamingo": util.blend(red, magenta, 0.5),
        "pink": util.blend(magenta, text, 0.25),
        "mauve": accent,
        "red": red,
        "maroon": brown,
        "peach": orange,
        "yellow": yellow,
        "green": green,
        "teal": cyan,
        "sky": util.blend(cyan, blue, 0.35),
        "sapphire": util.blend(blue, cyan, 0.45),
        "blue": blue,
        "lavender": util.blend(blue, magenta, 0.35),
        "text": text,
        "subtext1": sub1,
        "subtext0": sub0,
        "overlay2": overlay2,
        "overlay1": overlay1,
        "overlay0": overlay0,
        "surface2": surface2,
        "surface1": surface1,
        "surface0": surface0,
        "base": base,
        "mantle": mantle,
        "crust": crust,
    }


def theme_mode(theme: dict[str, str], fallback: str = "dark") -> str:
    mode = str(theme.get("mode", fallback)).strip().lower()
    return "light" if mode == "light" else "dark"


def palette_less(roles: dict[str, str], mode: str) -> str:
    """LESS declarations that override the Catppuccin palette for every flavor."""
    flavor = "latte" if mode == "light" else "mocha"
    entries = " ".join(f"@{name}: {roles[name]};" for name in ROLE_NAMES)
    return (
        f"@lightFlavor: {flavor};\n"
        f"@darkFlavor: {flavor};\n"
        "@accentColor: mauve;\n"
        "@catppuccin: {\n"
        f"  @latte: {{ {entries} }};\n"
        f"  @frappe: {{ {entries} }};\n"
        f"  @macchiato: {{ {entries} }};\n"
        f"  @mocha: {{ {entries} }};\n"
        "};\n"
    )
