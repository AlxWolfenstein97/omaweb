"""Fetch and normalize the Catppuccin userstyles bundle."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import util


BUNDLE_URL = (
    "https://github.com/catppuccin/userstyles/releases/download/"
    "all-userstyles-export/import.json"
)
USERSTYLES_URL = (
    "https://raw.githubusercontent.com/catppuccin/userstyles/main/scripts/userstyles.yml"
)


def _download(url: str, dest: Path, *, timeout: int = 90) -> None:
    req = urllib.request.Request(url, headers={
        "User-Agent": "OmaWeb/" + util.VERSION,
        "Accept": "application/octet-stream, application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read()
    util.atomic_write(dest, data)


def fetch(*, force: bool = False, quiet: bool = False) -> dict[str, Any]:
    """Download the bundle (and the maintainer map) into the cache.

    Network failures are non-fatal when a cached copy exists; callers can keep
    using the last good bundle.
    """
    cache = util.bundle_cache()
    meta_path = cache.parent / "meta.json"
    meta = util.read_json(meta_path, {}) or {}
    result: dict[str, Any] = {"path": str(cache), "downloaded": False, "error": None}

    if force or not cache.exists():
        try:
            _download(BUNDLE_URL, cache)
            result["downloaded"] = True
        except Exception as exc:  # noqa: BLE001 - report, don't explode the shell
            result["error"] = f"{type(exc).__name__}: {exc}"
            if not cache.exists():
                raise RuntimeError(
                    f"could not fetch the Catppuccin bundle and no cache exists at {cache}: {exc}"
                ) from exc

    if cache.exists():
        try:
            stat = cache.stat()
            meta.update({
                "size": stat.st_size,
                "sha256": util.sha256_file(cache),
                "downloaded_at": meta.get("downloaded_at") or None,
            })
            util.write_json(meta_path, meta)
        except OSError:
            pass

    yml = util.userstyles_cache()
    if force or not yml.exists():
        try:
            _download(USERSTYLES_URL, yml)
            result["maintainersDownloaded"] = True
        except Exception as exc:  # noqa: BLE001
            result.setdefault("warnings", []).append(
                f"could not fetch userstyles.yml: {type(exc).__name__}: {exc}"
            )

    if not quiet:
        size = cache.stat().st_size if cache.exists() else 0
        print(f"omaweb: bundle {cache} ({size} bytes)")
        if result.get("error"):
            print(f"omaweb: using cached bundle ({result['error']})")
        if result.get("warnings"):
            for warning in result["warnings"]:
                print(f"omaweb: {warning}")
    return result


def style_id(entry: dict[str, Any]) -> str | None:
    data = entry.get("usercssData") or {}
    namespace = str(data.get("namespace") or "")
    if namespace:
        tail = namespace.rstrip("/").rsplit("/", 1)[-1]
        if tail:
            return util.slugify(tail)
    name = str(entry.get("name") or data.get("name") or "")
    return util.slugify(name) if name else None


def load(*, required: bool = True) -> list[dict[str, Any]]:
    path = util.bundle_cache()
    if not path.exists():
        if required:
            fetch(quiet=True)
        if not path.exists():
            raise FileNotFoundError(f"Catppuccin bundle not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError(f"unexpected bundle shape in {path}")

    styles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        source = entry.get("sourceCode")
        if not isinstance(source, str) or not source.strip():
            continue
        sid = style_id(entry)
        if not sid or sid in seen:
            continue
        seen.add(sid)
        data = entry.get("usercssData") or {}
        styles.append({
            "id": sid,
            "name": str(data.get("name") or entry.get("name") or sid),
            "description": str(data.get("description") or entry.get("description") or ""),
            "author": str(data.get("author") or entry.get("author") or "Catppuccin"),
            "homepage": str(data.get("homepageURL") or ""),
            "support": str(data.get("supportURL") or ""),
            "version": str(data.get("version") or ""),
            "namespace": str(data.get("namespace") or ""),
            "license": str(data.get("license") or "MIT"),
            "source": source,
            "vars": data.get("vars") or {},
            "bundleEnabled": bool(entry.get("enabled", True)),
        })
    return styles


def parse_userstyles(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Parse ``scripts/userstyles.yml`` into ``{style_id: metadata}``.

    PyYAML is present on Omarchy, but keep a regex fallback so the scan still
    works on a minimal Python.
    """
    path = path or util.userstyles_cache()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        raw = data.get("userstyles") or {}
        out: dict[str, dict[str, Any]] = {}
        if isinstance(raw, dict):
            for sid, item in raw.items():
                if not isinstance(item, dict):
                    continue
                out[str(sid)] = {
                    "name": item.get("name"),
                    "link": item.get("link"),
                    "categories": item.get("categories") or [],
                    "current-maintainers": item.get("current-maintainers") or [],
                    "past-maintainers": item.get("past-maintainers") or [],
                }
        if out:
            return out
    except Exception:
        pass

    # Fallback: walk the ``userstyles:`` block by indentation.
    out = {}
    lines = text.splitlines()
    in_styles = False
    current: str | None = None
    for line in lines:
        if re.match(r"^userstyles:\s*$", line):
            in_styles = True
            continue
        if not in_styles:
            continue
        if line and not line.startswith((" ", "\t")):
            break
        m = re.match(r"^  ([A-Za-z0-9._-]+):\s*$", line)
        if m:
            current = m.group(1)
            out[current] = {"name": current, "current-maintainers": [], "past-maintainers": []}
            continue
        if current is None:
            continue
        for key in ("name", "link"):
            m = re.match(rf"^    {key}:\s*(.+?)\s*$", line)
            if m:
                out[current][key] = m.group(1).strip().strip("'\"")
        m = re.match(r"^    current-maintainers:\s*\[(.*)\]\s*$", line)
        if m:
            inner = m.group(1).strip()
            out[current]["current-maintainers"] = [
                x.strip().lstrip("*") for x in inner.split(",") if x.strip()
            ]
        m = re.match(r"^    past-maintainers:\s*\[(.*)\]\s*$", line)
        if m:
            inner = m.group(1).strip()
            out[current]["past-maintainers"] = [
                x.strip().lstrip("*") for x in inner.split(",") if x.strip()
            ]
    return out
