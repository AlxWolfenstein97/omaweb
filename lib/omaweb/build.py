"""Compile the Catppuccin bundle for the active theme and publish it.

The compiled CSS lives in a tiny **asset extension** owned by OmaWeb:
``~/.local/share/omaweb/extension/`` is a manifest plus generated CSS, with no
permissions, no content scripts and no JavaScript.  Chromium loads it through
``chrome-extension://``; Stylus (the normal, signed store copy) injects a shim
that ``@import``s those files.

Disabled styles are written as empty files; enabled styles contain their
compiled CSS.  Rewriting the extension files is enough on a theme switch:
Chromium re-reads them on the next page load, so the shim URL never changes and
Stylus never needs a re-import.

Sites that no style covers are left to Dark Reader, which is an optional,
separate install documented in the README.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from . import bundle, config, lessc, palette, stylus, util


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _lessc_version(lessc_bin: str) -> str:
    try:
        proc = util.run([lessc_bin, "--version"], timeout=15)
        return (proc.stdout or proc.stderr or "").strip().splitlines()[0]
    except Exception:
        return "unknown"


def load_theme() -> dict[str, Any]:
    return palette.resolve_theme()


def _lib_path() -> Path:
    return util.plugin_dir() / "lib" / "vendor" / "less" / "lib" / "std" / "v1.less"


def _cache_key(theme: dict[str, Any], bundle_path: Path, lib_path: Path,
               lessc_bin: str) -> str:
    parts = [
        util.VERSION,
        theme["name"],
        theme.get("path") or "",
        theme["mode"],
        theme["source"],
        # Explicit, so a resolved palette change always invalidates the build
        # even when two themes happen to share a colors.toml byte-for-byte.
        json.dumps(theme["roles"], sort_keys=True),
        util.sha256_file(bundle_path) if bundle_path.exists() else "no-bundle",
        util.sha256_file(lib_path) if lib_path.exists() else "no-lib",
        _lessc_version(lessc_bin),
    ]
    return util.sha256_text("\n".join(parts))[:20]


# --------------------------------------------------------------------------- compile

def _compile_catalog(styles: list[dict[str, Any]], *, theme: dict[str, Any],
                     cache: Path, lessc_bin: str, lib_path: Path, jobs: int,
                     timeout: int, quiet: bool) -> dict[str, Any]:
    cache.mkdir(parents=True, exist_ok=True)
    work = cache / "work"
    work.mkdir(parents=True, exist_ok=True)
    palette_text = palette.palette_less(theme["roles"], theme["mode"])

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    done = 0

    def task(entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        return lessc.build_style(
            entry, lessc=lessc_bin, lib_path=lib_path, palette_less=palette_text,
            out_root=cache, workdir=work, timeout=timeout,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        future_map = {pool.submit(task, entry): entry for entry in styles}
        for future in concurrent.futures.as_completed(future_map):
            entry = future_map[future]
            try:
                built, error = future.result()
            except Exception as exc:  # noqa: BLE001
                built, error = None, f"{type(exc).__name__}: {exc}"
            done += 1
            if built:
                results.append(built)
            else:
                errors.append({"id": entry["id"], "error": error or "unknown error"})
            if not quiet and (done % 20 == 0 or done == len(styles)):
                print(f"omaweb: compiled {done}/{len(styles)} styles")

    results.sort(key=lambda item: item["id"])
    errors.sort(key=lambda item: item["id"])
    catalog = {
        "generatedAt": _now(),
        "theme": {"name": theme["name"], "mode": theme["mode"]},
        "styles": results,
        "errors": errors,
    }
    util.write_json(cache / "catalog.json", catalog)
    util.atomic_write(cache / ".ok", _now() + "\n")
    return catalog


# --------------------------------------------------------------------------- extension copy

def _sync_extension(*, raw: Path, styles: list[dict[str, Any]],
                    theme: dict[str, Any]) -> dict[str, Any]:
    """Rewrite the CSS files inside the OmaWeb asset extension.

    Files are replaced in place (never a whole-directory swap) so Chromium
    re-reads them on the next page load.  An enabled style gets its compiled
    CSS; a disabled style is an empty file.  Removing a style also removes its
    stale files.
    """
    info = stylus.prepare_assets()
    web = Path(info["webRoot"])

    wanted: set[str] = set()
    for style in styles:
        enabled = bool(style.get("enabled"))
        for block in style.get("blocks") or []:
            rel = str(block.get("file") or "")
            if not rel:
                continue
            wanted.add(rel)
            dest = web / rel
            if not enabled:
                util.atomic_write(dest, "")
                continue
            try:
                body = (raw / rel).read_text(encoding="utf-8")
            except OSError:
                # A block that failed to compile stays empty.
                util.atomic_write(dest, "")
                continue
            util.atomic_write(dest, body)

    styles_root = web / "s"
    if styles_root.is_dir():
        for path in sorted(styles_root.rglob("*.css")):
            rel = str(path.relative_to(web))
            if rel not in wanted:
                try:
                    path.unlink()
                except OSError:
                    pass
        for path in sorted(styles_root.rglob("*"), key=lambda p: len(p.parts),
                           reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    util.write_json(web / "theme.json", {
        "name": theme["name"],
        "mode": theme["mode"],
        "roles": theme["roles"],
        "generatedAt": _now(),
    })
    return info


# --------------------------------------------------------------------------- apply

def apply(*, force: bool = False, no_fetch: bool = False, quiet: bool = False,
          jobs: int | None = None, timeout: int = 90) -> dict[str, Any]:
    cfg = config.load(create=True)
    theme = load_theme()
    if not theme["colors"] and not quiet:
        print(f"omaweb: no colors.toml at {util.theme_dir()} — using a neutral fallback palette")

    if not no_fetch:
        try:
            bundle.fetch(quiet=True)
        except Exception as exc:  # noqa: BLE001
            if not util.bundle_cache().exists():
                raise
            if not quiet:
                print(f"omaweb: using cached bundle ({exc})")

    styles = bundle.load(required=True)
    lessc_bin = util.lessc_path()
    if not lessc_bin:
        raise RuntimeError(
            "lessc not found. Install it with: npm install --prefix "
            f"{util.share_dir()}/less less@4   (or run install.sh)"
        )
    lib_path = _lib_path()
    if not lib_path.exists():
        raise FileNotFoundError(f"vendored LESS library missing: {lib_path}")

    jobs = jobs or min(8, os.cpu_count() or 4)
    compile_key = _cache_key(theme, util.bundle_cache(), lib_path, lessc_bin)
    cache = util.cache_dir() / "builds" / compile_key
    catalog_path = cache / "catalog.json"

    if (force or not (cache / ".ok").exists() or not catalog_path.exists()
            or not (cache / "s").is_dir()):
        if cache.exists() and force:
            shutil.rmtree(cache)
        if not quiet:
            print(f"omaweb: compiling {len(styles)} styles for {theme['name']} ({theme['mode']})")
        catalog = _compile_catalog(
            styles, theme=theme, cache=cache, lessc_bin=lessc_bin, lib_path=lib_path,
            jobs=jobs, timeout=timeout, quiet=quiet,
        )
    else:
        catalog = util.read_json(catalog_path, {}) or {}
        if not quiet:
            print(f"omaweb: using cached build {compile_key}")

    # The asset extension is what actually serves the CSS to the browser.
    ext_info = stylus.prepare_assets()
    base = stylus.extension_base_url()
    if not base:
        raise RuntimeError("could not determine the OmaWeb asset extension URL")

    index_styles: list[dict[str, Any]] = []
    for item in catalog.get("styles") or []:
        entry = dict(item)
        entry["enabled"] = config.is_enabled(cfg, item["id"])
        entry["blocks"] = [
            {**block, "url": f"{base}/{block['file']}"}
            for block in entry.get("blocks") or []
        ]
        index_styles.append(entry)
    index_styles.sort(key=lambda item: item["id"])

    index = {
        "version": 2,
        "generatedAt": _now(),
        "theme": {
            "name": theme["name"],
            "mode": theme["mode"],
            "roles": theme["roles"],
        },
        "baseUrl": base,
        "extension": {
            "id": ext_info["id"],
            "root": ext_info["root"],
            "webRoot": ext_info["webRoot"],
            "manifest": ext_info["manifest"],
        },
        "build": compile_key,
        "compileKey": compile_key,
        "styles": index_styles,
        "errors": catalog.get("errors") or [],
    }
    index["counts"] = {
        "total": len(index_styles),
        "enabled": sum(1 for s in index_styles if s["enabled"]),
        "compiled": len(catalog.get("styles") or []),
        "errors": len(index["errors"]),
    }

    # Rewrite the live extension files from the same index so the two can
    # never drift apart.
    _sync_extension(raw=cache, styles=index_styles, theme=theme)
    util.write_json(util.index_path(), index)

    # Regenerate the Stylus import file from the same index.
    export_path = stylus.write_export(index)

    state = {
        "theme": theme["name"],
        "mode": theme["mode"],
        "build": compile_key,
        "compileKey": compile_key,
        "generatedAt": index["generatedAt"],
        "extension": index["extension"],
        "stylusExport": str(export_path),
        "counts": index["counts"],
        "errors": index["errors"],
    }
    util.write_json(util.state_dir() / "state.json", state)

    if not quiet:
        counts = index["counts"]
        print(f"omaweb: {counts['enabled']}/{counts['total']} styles enabled, "
              f"{counts['errors']} compile errors")
        if counts["errors"]:
            for err in index["errors"][:8]:
                print(f"omaweb:   {err['id']}: {err['error'].splitlines()[0][:180]}")
        print(f"omaweb: extension {base} → {ext_info['webRoot']}")
        print(f"omaweb: Stylus import file {export_path}")
    return index


def load_index(*, build_if_missing: bool = True) -> dict[str, Any]:
    path = util.index_path()
    index = util.read_json(path)
    if not isinstance(index, dict):
        if not build_if_missing:
            raise RuntimeError(f"no generated index at {path}; run: omaweb apply")
        index = apply(quiet=True)
    return index


# --------------------------------------------------------------------------- matching

def match_url(index: dict[str, Any], url: str) -> list[str]:
    """Python mirror of Stylus' section matcher (used by ``omaweb match``)."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    out: list[str] = []
    for style in index.get("styles") or []:
        if not style.get("enabled"):
            continue
        matched = False
        for block in style.get("blocks") or []:
            for m in block.get("matches") or []:
                kind = m.get("type")
                value = str(m.get("value") or "")
                if kind == "domain":
                    v = value.lower().lstrip("*.")
                    if host == v or host.endswith("." + v):
                        matched = True
                elif kind == "prefix":
                    if url.startswith(value):
                        matched = True
                elif kind == "url":
                    if url == value:
                        matched = True
                elif kind == "regexp":
                    try:
                        if re.search(value, url):
                            matched = True
                    except re.error:
                        pass
                if matched:
                    break
            if matched:
                break
        if matched:
            out.append(style["id"])
    return out
