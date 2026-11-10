"""Fetch and normalize the Catppuccin userstyles bundle."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import util


BUNDLE_URL = (
    "https://github.com/catppuccin/userstyles/releases/download/"
    "all-userstyles-export/import.json"
)


def download(url: str, dest: Path, *, etag: str | None = None,
             last_modified: str | None = None,
             timeout: int = 90) -> dict[str, Any]:
    """Download ``url`` to ``dest``, honouring conditional headers.

    A ``304 Not Modified`` leaves the file alone.  Returns whether anything was
    written plus the validators for the next request, so a caller can check for
    a newer bundle without downloading it twice.
    """
    headers = {
        "User-Agent": "OmaWeb/" + util.VERSION,
        "Accept": "application/octet-stream, application/json, text/plain, */*",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 304:
                return {"downloaded": False, "status": 304}
            data = response.read()
            response_headers = dict(response.headers)
            status = response.status
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return {"downloaded": False, "status": 304}
        raise

    util.atomic_write(dest, data)
    return {
        "downloaded": True,
        "status": status,
        "etag": response_headers.get("ETag"),
        "last_modified": response_headers.get("Last-Modified"),
        "size": len(data),
    }


def fetch(*, force: bool = False, quiet: bool = False) -> dict[str, Any]:
    """Refresh the cached bundle.

    Without ``force`` the download is conditional: the cached copy is kept when
    upstream has not changed.  Network failures are non-fatal when a cache
    exists, so a theme switch never depends on GitHub being reachable.
    """
    cache = util.bundle_cache()
    meta_path = cache.parent / "meta.json"
    meta = util.read_json(meta_path, {}) or {}
    result: dict[str, Any] = {
        "path": str(cache),
        "downloaded": False,
        "unchanged": False,
        "error": None,
    }

    etag = None if force else meta.get("etag")
    last_modified = None if force else meta.get("last_modified")
    if force:
        etag = last_modified = None

    try:
        info = download(BUNDLE_URL, cache, etag=etag, last_modified=last_modified)
        if info["downloaded"]:
            result["downloaded"] = True
            result["size"] = info.get("size")
            meta.update({
                "etag": info.get("etag"),
                "last_modified": info.get("last_modified"),
                "downloaded_at": util.now(),
            })
        else:
            result["unchanged"] = True
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
                "checked_at": util.now(),
            })
            meta.setdefault("downloaded_at", util.now())
            util.write_json(meta_path, meta)
        except OSError:
            pass

    if not quiet:
        size = cache.stat().st_size if cache.exists() else 0
        print(f"omaweb: bundle {cache} ({size} bytes)"
              + (" — unchanged" if result["unchanged"] else ""))
        if result.get("error"):
            print(f"omaweb: using cached bundle ({result['error']})")
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


def parse(path: Path) -> list[dict[str, Any]]:
    """Normalize an ``import.json`` export into style records."""
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


def load(*, required: bool = True) -> list[dict[str, Any]]:
    path = util.bundle_cache()
    if not path.exists():
        if required:
            fetch(quiet=True)
        if not path.exists():
            raise FileNotFoundError(f"Catppuccin bundle not found: {path}")
    return parse(path)
