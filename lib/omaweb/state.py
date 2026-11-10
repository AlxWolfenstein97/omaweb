"""Portable OmaWeb state: the config.

The repository holds the code; a new box gets that by installing the plugin.
What does not travel with the code is the theme pin, the per-style overrides
and the Dark Reader opt-outs, so ``omaweb state export`` writes those to one
JSON file and ``omaweb state import`` merges them into another box.

Nothing here contains credentials, but the file names the sites you care
about, so treat it like a dotfile.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from . import build, config, util


BUNDLE_VERSION = 1


# --------------------------------------------------------------------------- export

def export_bundle() -> dict[str, Any]:
    return {
        "version": BUNDLE_VERSION,
        "tool": "omaweb",
        "omaweb": util.VERSION,
        "exportedAt": util.now(),
        "config": config.load(create=True),
    }


# --------------------------------------------------------------------------- merge

def _merge_config(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(current)
    if "theme" in incoming:
        merged["theme"] = incoming["theme"]

    overrides = dict(current.get("overrides") or {})
    overrides.update(incoming.get("overrides") or {})
    merged["overrides"] = overrides

    if "darkreader" in incoming:
        merged["darkreader"] = {
            "unthemed": list(dict.fromkeys([
                *config.unthemed_sites(current),
                *config.unthemed_sites(incoming),
            ])),
        }
    return merged


def import_bundle(path: Path, *, replace: bool = False) -> dict[str, Any]:
    raw = util.read_json(path)
    if not isinstance(raw, dict) or raw.get("tool") != "omaweb":
        raise ValueError(f"{path} is not an OmaWeb state export")
    if raw.get("version") != BUNDLE_VERSION:
        raise ValueError(f"unsupported state version: {raw.get('version')}")

    summary: dict[str, Any] = {"path": str(path), "replace": replace}

    if isinstance(raw.get("config"), dict):
        incoming = config.normalize(raw["config"])
        cfg = incoming if replace else _merge_config(config.load(create=True), incoming)
        config.save(cfg)
        summary["config"] = {
            "overrides": len(cfg.get("overrides") or {}),
            "theme": cfg.get("theme"),
            "unthemed": len(config.unthemed_sites(cfg)),
        }
        if util.index_path().exists():
            try:
                build.apply(quiet=True, no_fetch=True)
                summary["applied"] = True
            except Exception as exc:  # noqa: BLE001
                summary["applyError"] = f"{type(exc).__name__}: {exc}"

    return summary
