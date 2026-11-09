"""Generate a Dark Reader settings file from the live Omarchy theme.

Dark Reader is an optional second engine for pages that no OmaWeb style
covers.  OmaWeb does **not** drive it: Dark Reader keeps its settings in its
own extension storage, has no external control API, and only accepts messages
from its own UI pages.  The supported integration is therefore a settings JSON
file that the user imports in Dark Reader (Settings → Advanced → Import
Settings).  The import applies live, with no browser restart.

The file is generated from the same ``index.json`` that feeds the asset
extension:

* ``darkScheme*`` / ``lightScheme*``, ``selectionColor`` and
  ``scrollbarColor`` come from the active theme's roles;
* ``disabledFor`` lists the sites that an enabled OmaWeb style already paints,
  so Dark Reader does not double-theme them;
* regexp matches are carried over as Dark Reader regexp patterns rather than
  widened to a whole host, and path prefixes keep their path.

Dark Reader has no way to watch Omarchy's theme, so this file must be
regenerated and imported again after a theme switch.  That is the same model as
Catppuccin's manual Dark Reader recipe, but the colours come from the live
theme instead of being copied by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import util


SETTINGS_NAME = "omaweb-darkreader.json"

# Dark Reader's current settings schema version.  A lower value makes the
# extension run its old site-list migrations on the next load.
SCHEME_VERSION = 2

# Dark Reader's own defaults, used for the scheme that the active theme does
# not provide.  They only matter if the user flips Dark Reader's light/dark
# switch by hand.
DEFAULT_DARK_BACKGROUND = "#181a1b"
DEFAULT_DARK_TEXT = "#e8e6e3"
DEFAULT_LIGHT_BACKGROUND = "#dcdad7"
DEFAULT_LIGHT_TEXT = "#181a1b"


# --------------------------------------------------------------------------- matches

def _host(value: Any) -> str:
    """A bare lowercase host from a Stylus ``domain`` match."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = urlparse(text).hostname or ""
    else:
        text = text.split("/", 1)[0]
    text = text.split("@")[-1].split(":")[0].strip(".")
    if text.startswith("*."):
        text = text[2:]
    return text


def _url_pattern(value: Any) -> str:
    """A Dark Reader ``host/path`` pattern from a Stylus URL or prefix match.

    Keeping the path means a style that only themes ``go.dev/tour`` does not
    switch Dark Reader off for the rest of ``go.dev``.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" in text:
        parsed = urlparse(text)
        host = (parsed.hostname or "").lower().strip(".")
        path = parsed.path or ""
    else:
        text = text.lstrip("/")
        host, _, rest = text.partition("/")
        host = host.split("@")[-1].split(":")[0].strip(".")
        path = f"/{rest}" if rest else ""
    if not host:
        return ""
    if host.startswith("*."):
        host = host[2:]
    path = path.rstrip("/")
    return host + path


def _regexp_pattern(value: Any) -> str:
    """Wrap a Stylus regexp as a Dark Reader ``/regexp/`` site pattern."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("/") and text.endswith("/") and len(text) > 2:
        return text
    return f"/{text}/"


def normalize_pattern(value: Any) -> str:
    """A Dark Reader site pattern from a URL, host or ``/regexp/``.

    Bare ``host/path`` values keep their path, matching how coverage patterns
    are written.  A user-supplied regexp is passed through unchanged.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("/") and text.endswith("/") and len(text) > 2:
        return text
    return _url_pattern(text)


def site_patterns(matches: list[dict[str, Any]] | None) -> list[str]:
    """Dark Reader site patterns for a block's Stylus match rules."""
    patterns: list[str] = []
    for match in matches or []:
        if not isinstance(match, dict):
            continue
        kind = str(match.get("type") or "")
        value = match.get("value")
        if kind == "domain":
            pattern = _host(value)
        elif kind in ("url", "prefix"):
            pattern = _url_pattern(value)
        elif kind == "regexp":
            pattern = _regexp_pattern(value)
        else:
            pattern = ""
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    return patterns


# --------------------------------------------------------------------------- settings

def _scheme(theme: dict[str, Any]) -> dict[str, Any]:
    """Dark Reader colours for the active theme.

    ``mode`` 1 uses the dark scheme, ``mode`` 0 the light scheme.  The other
    scheme is filled with Dark Reader's stock colours so a manual light/dark
    switch still looks sane.
    """
    roles = theme.get("roles") or {}
    mode = str(theme.get("mode") or "dark").lower()
    base = roles.get("base") or (
        DEFAULT_LIGHT_BACKGROUND if mode == "light" else DEFAULT_DARK_BACKGROUND
    )
    text = roles.get("text") or (
        DEFAULT_LIGHT_TEXT if mode == "light" else DEFAULT_DARK_TEXT
    )
    selection = roles.get("surface2") or "#585b70"
    scrollbar = roles.get("surface1") or ""
    if mode == "light":
        dark_background, dark_text = DEFAULT_DARK_BACKGROUND, DEFAULT_DARK_TEXT
        light_background, light_text = base, text
    else:
        dark_background, dark_text = base, text
        light_background, light_text = DEFAULT_LIGHT_BACKGROUND, DEFAULT_LIGHT_TEXT
    return {
        "mode": 0 if mode == "light" else 1,
        "darkSchemeBackgroundColor": dark_background,
        "darkSchemeTextColor": dark_text,
        "lightSchemeBackgroundColor": light_background,
        "lightSchemeTextColor": light_text,
        "selectionColor": selection,
        "scrollbarColor": scrollbar,
    }


def covered_patterns(index: dict[str, Any]) -> list[str]:
    """Sites that enabled OmaWeb styles already paint."""
    patterns: list[str] = []
    for style in index.get("styles") or []:
        if not isinstance(style, dict) or not style.get("enabled"):
            continue
        for block in style.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            for pattern in site_patterns(block.get("matches") or []):
                if pattern not in patterns:
                    patterns.append(pattern)
    return sorted(patterns)


def user_patterns(cfg: dict[str, Any] | None) -> list[str]:
    """Sites the user explicitly wants left unthemed.

    These are persisted in OmaWeb's config and merged into ``disabledFor``, so
    a Dark Reader settings import does not wipe them.
    """
    if not isinstance(cfg, dict):
        return []
    patterns: list[str] = []
    for value in (cfg.get("darkreader") or {}).get("unthemed") or []:
        pattern = normalize_pattern(value)
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    return patterns


def build_settings(index: dict[str, Any],
                   cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a Dark Reader ``UserSettings`` payload for the index's theme."""
    theme = index.get("theme") or {}
    scheme = _scheme(theme)
    mode = scheme.pop("mode")
    disabled = set(covered_patterns(index))
    disabled.update(user_patterns(cfg))
    return {
        "schemeVersion": SCHEME_VERSION,
        "enabled": True,
        "theme": {
            "mode": mode,
            "brightness": 100,
            "contrast": 100,
            "grayscale": 0,
            "sepia": 0,
            "useFont": False,
            "fontFamily": "Open Sans",
            "textStroke": 0,
            "engine": "dynamicTheme",
            "stylesheet": "",
            **scheme,
            "styleSystemControls": mode == 1,
            "lightColorScheme": "Default",
            "darkColorScheme": "Default",
            "immediateModify": False,
        },
        "enabledByDefault": True,
        "enabledFor": [],
        "disabledFor": sorted(disabled),
        "detectDarkTheme": True,
    }


# --------------------------------------------------------------------------- files

def settings_path() -> Path:
    return util.share_dir() / "darkreader.json"


def write_settings(settings: dict[str, Any]) -> Path:
    path = settings_path()
    util.write_json(path, settings)
    return path


def visible_export() -> Path | None:
    """A copy in a non-hidden folder so Dark Reader's file picker can see it."""
    src = settings_path()
    if not src.exists():
        return None
    downloads = util.home() / "Downloads"
    target = (downloads if downloads.is_dir() else util.home()) / SETTINGS_NAME
    try:
        content = src.read_text(encoding="utf-8")
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            util.atomic_write(target, content)
        return target
    except OSError:
        return None
