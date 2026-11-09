"""OmaWeb user configuration.

The config is intentionally small: a theme pin, explicit per-style overrides
and the sites where Dark Reader should stay off.  Defaults live in code so a
new Catppuccin style is on without rewriting anyone's file.
"""

from __future__ import annotations

import copy
from typing import Any

from . import util


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    # "auto" follows the desktop theme.  Any other value is an installed
    # Omarchy theme name, used only for OmaWeb (Chroma-style mixing).
    "theme": "auto",
    "overrides": {},
    "darkreader": {
        # Sites that should stay unthemed even though no OmaWeb style paints
        # them.  These are merged into the generated Dark Reader disabledFor
        # list, so the opt-out survives every settings import.
        "unthemed": [],
    },
}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def normalize(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}

    theme = raw.get("theme")
    theme = theme.strip() if isinstance(theme, str) and theme.strip() else "auto"

    overrides: dict[str, bool] = {}
    for key, value in (raw.get("overrides") or {}).items():
        if isinstance(key, str) and key:
            overrides[key] = util.truthy(value)

    dr = raw.get("darkreader")
    unthemed_raw = dr.get("unthemed") if isinstance(dr, dict) else None
    unthemed: list[str] = []
    for item in unthemed_raw or []:
        if isinstance(item, str):
            value = item.strip()
            if value and value not in unthemed:
                unthemed.append(value)

    return {
        "version": 1,
        "theme": theme,
        "overrides": overrides,
        "darkreader": {"unthemed": unthemed},
    }


def load(*, create: bool = False) -> dict[str, Any]:
    path = util.config_path()
    raw = util.read_json(path)
    if raw is None and create:
        cfg = default_config()
        save(cfg)
        return cfg
    return normalize(raw)


def save(cfg: dict[str, Any]) -> None:
    util.write_json(util.config_path(), normalize(cfg))


def default_enabled(style_id: str) -> bool:
    """Missing overrides are on; users opt out per style or with --all."""
    return True


def is_enabled(cfg: dict[str, Any], style_id: str) -> bool:
    overrides = cfg.get("overrides") or {}
    if style_id in overrides:
        return bool(overrides[style_id])
    return default_enabled(style_id)


def set_enabled(style_id: str, enabled: bool) -> dict[str, Any]:
    cfg = load(create=True)
    cfg.setdefault("overrides", {})[style_id] = bool(enabled)
    save(cfg)
    return cfg


def unthemed_sites(cfg: dict[str, Any]) -> list[str]:
    return list((cfg.get("darkreader") or {}).get("unthemed") or [])


def set_unthemed(pattern: str, unthemed: bool) -> dict[str, Any]:
    """Add or remove a site where Dark Reader should stay off."""
    cfg = load(create=True)
    sites = unthemed_sites(cfg)
    if unthemed:
        if pattern not in sites:
            sites.append(pattern)
    else:
        sites = [site for site in sites if site != pattern]
    cfg.setdefault("darkreader", {})["unthemed"] = sites
    save(cfg)
    return cfg
