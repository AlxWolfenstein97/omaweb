"""Stylus integration.

OmaWeb does not style pages itself and does not replace Stylus.  Stylus stays
the userstyle manager; OmaWeb gives it a small set of *shim* styles whose entire
body is::

    @import url("chrome-extension://<omaweb-assets>/s/<style>/<block>.css");

The CSS lives in a tiny **asset extension** owned by OmaWeb: a manifest plus
generated CSS files, with no permissions, no content scripts and no JavaScript.
Stylus (the normal, signed Chrome Web Store copy) injects the shim; Chromium
loads the imported CSS from the asset extension.  That works on HTTPS pages and
under strict CSP without a local server, loopback permission, certificate or a
browser-wide security flag.

The asset extension has a pinned public ``key`` so its id is the same on every
machine.  Rewriting its files on a theme switch is enough: Chromium re-reads
them on the next page load, so the shim URL never changes and Stylus never needs
a re-import after the first one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import util


SHIM_PREFIX = "OmaWeb · "

# ASCII-only probe used to spot our styles inside Stylus's IndexedDB.  Chromium
# stores strings as one-byte latin-1 when it can (so "·" becomes 0xb7), which is
# why the full prefix is not a reliable needle.
IMPORT_MARK = "OmaWeb"

# The signed Stylus build from the Chrome Web Store.  OmaWeb never modifies it.
STORE_ID = "clngdbkpkpeebahjckkjfobafhncgmne"
STORE_URL = f"https://chromewebstore.google.com/detail/stylus/{STORE_ID}"

# Public half of a throwaway RSA key.  Pinning it in the asset manifest gives
# the extension a stable id on every machine, so the shim URLs can be documented
# and imported once.  There is no secret: the private half was discarded.
ASSET_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAldcv0GXY229XqqSmP/r7wLYsZjzv"
    "ExFW7i0oSfbKOOn5+tcq1M62FN1+Rayt3RBPiqbpnZIrIRvVk1Oun9N4L9VEZ6AmCJhn41gy"
    "nceh+Pe9HEkDLAkElmhONSGmOkJSf500inVdoijbKMejcEFFUJgIRxgnSPJfepbKA8h7YWVjM"
    "TgQx5pmMIKLUmtgLuJA726aLpwVZlG7fahgFGSxWokIKP8vAYJ0ItUrGDzUidvRFJztH4WDtE"
    "l930rP45iUl5JHz376TMhrY9i1kjXqIxZ9rotmbn8SYB2X4fje3XKL/I08Rth3SFFwnEFocZg"
    "mVAb1fU0Z/1uKbGlezya1mwIDAQAB"
)
ASSET_ID = "jgbhmbaknbcdkcblgalphnnhjjfbnplj"
ASSET_DIR = "extension"

PROFILE_DIRS = {
    "chromium": "chromium",
    "chrome": "google-chrome",
    "brave": "BraveSoftware/Brave-Browser",
    "edge": "microsoft-edge",
}


# --------------------------------------------------------------------------- shims

def _section(block: dict[str, Any]) -> dict[str, Any] | None:
    if not block.get("url") or not block.get("matches"):
        return None
    section: dict[str, Any] = {"code": f'@import url("{block["url"]}");'}
    buckets = {"domain": "domains", "url": "urls", "prefix": "urlPrefixes", "regexp": "regexps"}
    for match in block["matches"]:
        field = buckets.get(str(match.get("type")))
        value = str(match.get("value") or "")
        if field and value:
            section.setdefault(field, [])
            if value not in section[field]:
                section[field].append(value)
    if len(section) == 1:
        return None
    return section


def build_shims(index: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the generated index into Stylus style objects.

    Every shim is emitted ``enabled: true``.  Which styles actually paint is
    decided by the CSS files OmaWeb writes into the asset extension: a disabled
    style is an empty file, and an enabled one contains its compiled CSS.  So
    ``omaweb enable`` and ``omaweb disable`` take effect on the next page load
    without touching Stylus.  For the same reason the export is static: enabling
    a style never changes the Stylus database.
    """
    shims: list[dict[str, Any]] = []
    for style in index.get("styles") or []:
        sections = [s for s in (_section(b) for b in style.get("blocks") or []) if s]
        if not sections:
            continue
        shims.append({
            "name": SHIM_PREFIX + str(style.get("name") or style["id"]),
            "enabled": True,
            "sections": sections,
        })
    return shims


def write_export(index: dict[str, Any]) -> Path:
    """Write a Stylus-importable JSON export and return its path.

    Only styles are exported.  No ``settings`` object is included, so importing
    the file never overwrites the user's Stylus preferences.
    """
    payload = build_shims(index)
    path = util.stylus_dir() / "omaweb.json"
    util.write_json(path, payload)
    return path


def export_path() -> Path:
    return util.stylus_dir() / "omaweb.json"


def visible_export() -> Path | None:
    """A copy in a non-hidden folder so the browser's file picker can see it.

    ``~/.local`` is hidden in most file pickers, which makes the canonical
    export hard to select.  ``~/Downloads/omaweb-stylus.json`` is the same
    file, refreshed whenever ``omaweb stylus import`` runs.
    """
    src = export_path()
    if not src.exists():
        return None
    downloads = util.home() / "Downloads"
    target = (downloads if downloads.is_dir() else util.home()) / "omaweb-stylus.json"
    try:
        content = src.read_text(encoding="utf-8")
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            util.atomic_write(target, content)
        return target
    except OSError:
        return None


def _store_profile() -> Path | None:
    """The browser profile that actually has the store Stylus installed."""
    for profile in profile_roots():
        if (profile / "Default" / "Extensions" / STORE_ID).is_dir():
            return profile
    return None


def import_state() -> dict[str, Any]:
    """Has the export been imported into the store Stylus profile yet?

    Stylus keeps its styles in IndexedDB, and Chromium writes those values as
    plain UTF-8 JSON, so counting the shim prefix is a cheap read-only probe.
    It is a heuristic: ``count`` is a raw byte-occurrence count (LevelDB keeps
    old writes and logs, so it is not the number of distinct styles), and it
    stays positive until Stylus compacts the database, even if the user later
    deletes the shims.
    """
    profile = _store_profile()
    if not profile:
        return {"known": False, "imported": None, "count": 0, "path": None}
    db = (profile / "Default" / "IndexedDB"
          / f"chrome-extension_{STORE_ID}_0.indexeddb.leveldb")
    if not db.is_dir():
        return {"known": True, "imported": False, "count": 0, "path": str(db)}
    needle = IMPORT_MARK.encode("ascii")
    wide = IMPORT_MARK.encode("utf-16-le")
    count = 0
    try:
        entries = [p for p in db.iterdir() if p.is_file() and p.name != "LOCK"]
    except OSError:
        entries = []
    for entry in entries:
        try:
            data = entry.read_bytes()
        except OSError:
            continue
        # Latin-1/UTF-8 and UTF-16LE patterns cannot overlap, so adding is safe.
        count += data.count(needle) + data.count(wide)
    return {"known": True, "imported": count > 0, "count": count, "path": str(db)}


# --------------------------------------------------------------------------- asset extension

def asset_dir() -> Path:
    return util.share_dir() / ASSET_DIR


def prepare_assets(*, quiet: bool = True) -> dict[str, Any]:
    """Create (or refresh) the tiny extension that serves OmaWeb's CSS.

    It has no permissions, no content scripts and no background page.  The
    pinned ``key`` keeps the extension id stable, which is what lets the Stylus
    shims keep working across theme switches and reinstalls.
    """
    root = asset_dir()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest = {
        "manifest_version": 3,
        "name": "OmaWeb Assets",
        "version": util.VERSION,
        "description": "Serves OmaWeb's generated CSS to Stylus. No permissions, no scripts.",
        "key": ASSET_KEY,
        "web_accessible_resources": [{
            "resources": ["s/*", "theme.json"],
            "matches": ["<all_urls>"],
            "use_dynamic_url": False,
        }],
    }
    before = util.read_json(manifest_path, None)
    changed = before != manifest
    if changed:
        util.write_json(manifest_path, manifest)
    (root / "s").mkdir(parents=True, exist_ok=True)
    return {
        "root": str(root),
        "manifest": str(manifest_path),
        "webRoot": str(root),
        "id": ASSET_ID,
        "changed": changed,
    }


def web_root() -> Path | None:
    """Where the generated CSS lives inside the asset extension."""
    root = asset_dir()
    return root if (root / "manifest.json").exists() else None


def extension_base_url() -> str | None:
    return f"chrome-extension://{ASSET_ID}" if web_root() else None


# --------------------------------------------------------------------------- Stylus discovery

def profile_roots() -> list[Path]:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else util.home() / ".config"
    return [root / rel for rel in PROFILE_DIRS.values() if (root / rel).is_dir()]


def _prefs_extensions(profile: Path) -> dict[str, Any]:
    prefs = util.read_json(profile / "Default" / "Preferences", {}) or {}
    return ((prefs.get("extensions") or {}).get("settings") or {})


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)) or (0,)


def store_stylus() -> Path | None:
    """The signed Stylus from the Chrome Web Store, if the profile has it."""
    for profile in profile_roots():
        base = profile / "Default" / "Extensions" / STORE_ID
        if not base.is_dir():
            continue
        candidates = [
            p for p in base.iterdir()
            if p.is_dir() and (p / "manifest.json").exists()
        ]
        if candidates:
            candidates.sort(key=lambda p: _version_key(p.name), reverse=True)
            return candidates[0]
    return None


def installed_stylus() -> list[dict[str, Any]]:
    """The signed store copy of Stylus, if the profile has it."""
    store = store_stylus()
    if not store:
        return []
    return [{"source": "store", "id": STORE_ID,
             "path": str(store), "profile": None}]


def manager_url() -> str | None:
    if store_stylus():
        return f"chrome-extension://{STORE_ID}/manage.html"
    return None


def profile_extension_states() -> dict[str, dict[str, Any]]:
    """Per browser profile: is the asset extension registered there?

    Chromium records extensions loaded with ``--load-extension`` and ones added
    by hand through ``chrome://extensions`` → *Load unpacked* in the same place.
    So this answers "is the bridge active?" independently of how it got there,
    which is what lets a user skip the flags file entirely.
    """
    root = asset_dir().expanduser().resolve()
    states: dict[str, dict[str, Any]] = {}
    for profile in profile_roots():
        state: dict[str, Any] = {
            "loaded": False, "active": False, "profile": str(profile),
            "path": None, "disableReasons": [],
        }
        for name in ("Preferences", "Secure Preferences"):
            prefs = util.read_json(profile / "Default" / name, {}) or {}
            entry = (((prefs.get("extensions") or {}).get("settings") or {})
                     .get(ASSET_ID))
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                continue
            try:
                matches = Path(path).expanduser().resolve() == root
            except OSError:
                matches = False
            if not matches:
                continue
            reasons = entry.get("disable_reasons") or []
            state = {
                "loaded": True,
                "active": not reasons,
                "profile": str(profile),
                "path": path,
                "disableReasons": reasons,
            }
            break
        states[str(profile)] = state
    return states


def profile_extension_state() -> dict[str, Any]:
    """The first profile (if any) that has the asset extension loaded."""
    for state in profile_extension_states().values():
        if state["loaded"]:
            return state
    return {"loaded": False, "active": False, "profile": None,
            "path": None, "disableReasons": []}


# --------------------------------------------------------------------------- browser actions

def _open_url(url: str, *, quiet: bool = False) -> bool:
    opener = util.which("xdg-open")
    cmd = [opener, url] if opener else None
    if not cmd:
        browser = util.chromium_path()
        if not browser:
            if not quiet:
                print("omaweb: no browser found to open " + url)
            return False
        cmd = [browser, url]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError as exc:
        if not quiet:
            print(f"omaweb: could not open {url}: {exc}")
        return False


def open_store(*, quiet: bool = False) -> bool:
    return _open_url(STORE_URL, quiet=quiet)


def open_manager(*, quiet: bool = False) -> bool:
    url = manager_url()
    if not url:
        if not quiet:
            print("omaweb: Stylus is not installed — install it from: " + STORE_URL)
        return False
    return _open_url(url, quiet=quiet)


def open_export_folder(*, quiet: bool = False,
                       directory: Path | None = None) -> bool:
    path = export_path()
    if not path.exists():
        return False
    opener = util.which("xdg-open")
    if not opener:
        return False
    target = directory or path.parent
    try:
        subprocess.Popen([opener, str(target)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def status() -> dict[str, Any]:
    found = installed_stylus()
    root = asset_dir()
    manifest = util.read_json(root / "manifest.json", {}) or {}
    war = manifest.get("web_accessible_resources") or []
    exposed = any(
        isinstance(entry, dict) and any(
            str(x).startswith("s/")
            for x in entry.get("resources") or []
        )
        for entry in war
    )
    return {
        "installed": any(item["source"] == "store" for item in found),
        "copies": found,
        "managerUrl": manager_url(),
        "export": str(export_path()),
        "exportExists": export_path().exists(),
        "import": import_state(),
        "storeUrl": STORE_URL,
        "asset": {
            "id": ASSET_ID,
            "root": str(root),
            "manifest": str(root / "manifest.json"),
            "exists": (root / "manifest.json").exists(),
            "exposed": exposed,
            "permissions": manifest.get("permissions") or [],
            "contentScripts": manifest.get("content_scripts") or [],
        },
    }
