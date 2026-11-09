"""Portable OmaWeb state: config plus the scan cache.

The repository holds the code.  A new box gets that by installing the plugin,
but it starts with an empty config and no scan cache.  This module carries the
two pieces worth keeping between boxes:

* ``config`` — theme pin, per-style overrides and Dark Reader opt-outs;
* ``scan`` — the cached Catppuccin release and issue data, so ``omaweb scan``
  does not have to re-hit GitHub on a fresh install.

Nothing here contains credentials.  The bundle is still a dotfile: it names
the sites you care about.
"""

from __future__ import annotations

import copy
import datetime as _dt
from pathlib import Path
from typing import Any

from . import build, config, util


BUNDLE_VERSION = 1
SCAN_FILES = ("release.json", "issues.json")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scan_dir() -> Path:
    return util.cache_dir() / "scan"


# --------------------------------------------------------------------------- export

def export_bundle(*, include_config: bool = True,
                  include_scan: bool = True) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "version": BUNDLE_VERSION,
        "tool": "omaweb",
        "omaweb": util.VERSION,
        "exportedAt": _now(),
    }
    if include_config:
        bundle["config"] = config.load(create=True)
    if include_scan:
        scan = {
            name.removesuffix(".json"): util.read_json(_scan_dir() / name)
            for name in SCAN_FILES
        }
        scan = {key: value for key, value in scan.items() if value}
        if scan:
            bundle["scan"] = scan
    return bundle


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


def _newer(incoming: dict[str, Any], current: dict[str, Any]) -> bool:
    return str(incoming.get("fetchedAt") or "") >= str(current.get("fetchedAt") or "")


def import_bundle(path: Path, *, replace: bool = False,
                  include_config: bool = True,
                  include_scan: bool = True) -> dict[str, Any]:
    raw = util.read_json(path)
    if not isinstance(raw, dict) or raw.get("tool") != "omaweb":
        raise ValueError(f"{path} is not an OmaWeb state export")
    if raw.get("version") != BUNDLE_VERSION:
        raise ValueError(f"unsupported state version: {raw.get('version')}")

    summary: dict[str, Any] = {"path": str(path), "replace": replace}

    if include_config and isinstance(raw.get("config"), dict):
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

    if include_scan and isinstance(raw.get("scan"), dict):
        imported = 0
        for name in SCAN_FILES:
            key = name.removesuffix(".json")
            value = raw["scan"].get(key)
            if not isinstance(value, dict):
                continue
            target = _scan_dir() / name
            current = util.read_json(target, {}) or {}
            if replace or not current or _newer(value, current):
                util.write_json(target, value)
                imported += 1
        summary["scan"] = imported

    return summary
