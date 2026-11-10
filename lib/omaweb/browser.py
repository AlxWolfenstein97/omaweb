"""Safe editing of Chromium-family ``*-flags.conf`` files.

We never rewrite unrelated flags.  The only line we touch is
``--load-extension=``; existing extension paths are preserved, ours is added
once, and uninstall removes exactly our path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import util


FLAG_FILES = {
    "chromium": "chromium-flags.conf",
    "chrome": "google-chrome-flags.conf",
    "brave": "brave-flags.conf",
    "brave-origin": "brave-origin-flags.conf",
    "edge": "microsoft-edge-flags.conf",
    "vivaldi": "vivaldi-stable.conf",
}

BINARIES = {
    "chromium": ("chromium", "chromium-browser"),
    "chrome": ("google-chrome-stable", "google-chrome"),
    "brave": ("brave", "brave-browser"),
    "brave-origin": ("brave-origin",),
    "edge": ("microsoft-edge-stable", "microsoft-edge"),
    "vivaldi": ("vivaldi", "vivaldi-stable"),
}

# Firefox-family browsers cannot load an unsigned helper extension, so they are
# detected only to tell the user why OmaWeb does not theme them.
FIREFOX_BINARIES = {
    "firefox": ("firefox", "firefox-esr"),
    "zen": ("zen", "zen-browser"),
    "librewolf": ("librewolf",),
}

# Desktop-entry fragments, longest first so brave-origin is not read as brave.
DESKTOP_KEYS = (
    ("brave-origin", "brave-origin"),
    ("brave", "brave"),
    ("google-chrome", "chrome"),
    ("chromium", "chromium"),
    ("microsoft-edge", "edge"),
    ("vivaldi", "vivaldi"),
    ("firefox", "firefox"),
    ("zen", "zen"),
    ("librewolf", "librewolf"),
)


def flags_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base).expanduser() if base else util.home() / ".config"


def flag_path(browser: str) -> Path:
    return flags_dir() / FLAG_FILES[browser]


def detect() -> list[str]:
    found: list[str] = []
    for browser, binaries in BINARIES.items():
        if any(util.which(b) for b in binaries) or flag_path(browser).exists():
            found.append(browser)
    return found


def detect_firefox() -> list[str]:
    return [browser for browser, binaries in FIREFOX_BINARIES.items()
            if any(util.which(b) for b in binaries)]


def default_browser() -> str | None:
    """The desktop entry id of the default browser, as Omarchy resolves it."""
    for cmd in (["omarchy-cmd-default-browser"],
                ["xdg-settings", "get", "default-web-browser"]):
        if not util.which(cmd[0]):
            continue
        try:
            proc = util.run(cmd, timeout=10)
        except Exception:  # noqa: BLE001
            continue
        entry = (proc.stdout or "").strip()
        if proc.returncode == 0 and entry:
            return entry
    return None


def desktop_key(entry: str | None) -> str | None:
    value = (entry or "").lower()
    for fragment, key in DESKTOP_KEYS:
        if fragment in value:
            return key
    return None


def is_firefox(key: str | None) -> bool:
    return key in FIREFOX_BINARIES


def detect_report() -> dict[str, Any]:
    """What the installer needs: installed browsers, the default, and wiring."""
    from . import stylus  # local import keeps the module graph acyclic

    installed = detect()
    firefox = detect_firefox()
    entry = default_browser()
    key = desktop_key(entry)
    if key in FIREFOX_BINARIES and key not in firefox:
        firefox.append(key)
    rows = status(stylus.asset_dir())
    return {
        "installed": installed,
        "firefox": firefox,
        "default": key,
        "defaultEntry": entry,
        "defaultFamily": "firefox" if is_firefox(key) else (
            "chromium" if key else None),
        "wired": [row["browser"] for row in rows if row.get("wired")],
        "loaded": [row["browser"] for row in rows if row.get("loaded")],
        "manual": wiring_disabled(),
    }


def _split_extensions(value: str) -> list[str]:
    out = []
    for item in value.split(","):
        item = item.strip().strip("'\"")
        if item:
            out.append(item)
    return out


def _load_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _write_lines(path: Path, lines: list[str], *, created: bool) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n"
    util.atomic_write(path, text)
    if created:
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass


def wire(extension: Path, browsers: list[str] | None = None, *, dry_run: bool = False) -> list[dict[str, Any]]:
    extension = extension.expanduser().resolve()
    results: list[dict[str, Any]] = []
    for browser in browsers or detect() or ["chromium"]:
        path = flag_path(browser)
        existed = path.exists()
        lines = _load_lines(path)
        extensions: list[str] = []
        other: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("--load-extension="):
                extensions.extend(_split_extensions(stripped[len("--load-extension="):]))
            else:
                other.append(line)
        before = list(extensions)
        if str(extension) not in extensions:
            extensions.append(str(extension))
        extensions = util.dedupe(extensions)
        new_lines = list(other)
        if extensions:
            new_lines.append("--load-extension=" + ",".join(extensions))
        if new_lines == lines and existed:
            results.append({"browser": browser, "path": str(path), "changed": False,
                            "created": False, "extensions": extensions})
            continue
        if not dry_run:
            _write_lines(path, new_lines, created=not existed)
        results.append({
            "browser": browser,
            "path": str(path),
            "changed": before != extensions or not existed,
            "created": not existed,
            "extensions": extensions,
        })
    return results


def unwire(extension: Path, browsers: list[str] | None = None, *, dry_run: bool = False) -> list[dict[str, Any]]:
    extension = extension.expanduser().resolve()
    results: list[dict[str, Any]] = []
    for browser in browsers or detect() or ["chromium"]:
        path = flag_path(browser)
        if not path.exists():
            continue
        lines = _load_lines(path)
        extensions: list[str] = []
        other: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("--load-extension="):
                extensions.extend(_split_extensions(stripped[len("--load-extension="):]))
            else:
                other.append(line)
        kept = [x for x in extensions if Path(x).expanduser().resolve() != extension]
        changed = len(kept) != len(extensions)
        if not changed:
            results.append({"browser": browser, "path": str(path), "changed": False})
            continue
        new_lines = list(other)
        if kept:
            new_lines.append("--load-extension=" + ",".join(kept))
        if not dry_run:
            meaningful = [ln for ln in new_lines if ln.strip() and not ln.lstrip().startswith("#")]
            if meaningful:
                _write_lines(path, new_lines, created=False)
            else:
                # The file only existed for our flag; remove it.
                try:
                    path.unlink()
                except OSError:
                    pass
        results.append({"browser": browser, "path": str(path), "changed": True,
                        "remaining": kept})
    return results


def wiring_marker() -> Path:
    return util.state_dir() / "no-wire"


def wiring_disabled() -> bool:
    return wiring_marker().exists()


def set_wiring_disabled(disabled: bool) -> None:
    path = wiring_marker()
    if disabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text("browser flags are managed by hand\n", encoding="utf-8")
        except OSError:
            pass
        return
    try:
        path.unlink()
    except OSError:
        pass


def status(extension: Path | None = None) -> list[dict[str, Any]]:
    from . import stylus  # local import keeps the module graph acyclic

    resolved = extension.expanduser().resolve() if extension else None
    profile_states = stylus.profile_extension_states()
    installed = detect()
    entry = desktop_key(default_browser())
    out = []
    for browser in FLAG_FILES:
        path = flag_path(browser)
        wired = False
        if path.exists() and resolved is not None:
            for line in _load_lines(path):
                if line.strip().startswith("--load-extension="):
                    if any(Path(x).expanduser().resolve() == resolved
                           for x in _split_extensions(line.strip()[len("--load-extension="):])):
                        wired = True
        profile = str(flags_dir() / stylus.PROFILE_DIRS[browser])
        state = profile_states.get(profile) or {
            "loaded": False, "active": False, "profile": None,
        }
        out.append({"browser": browser, "path": str(path), "exists": path.exists(),
                    "installed": browser in installed,
                    "default": browser == entry,
                    "wired": wired, "loaded": state["loaded"],
                    "active": state["active"], "profile": state["profile"],
                    "manual": wiring_disabled()})
    return out
