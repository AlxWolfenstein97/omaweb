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
    "edge": "microsoft-edge-flags.conf",
}

BINARIES = {
    "chromium": ("chromium", "chromium-browser"),
    "chrome": ("google-chrome-stable", "google-chrome"),
    "brave": ("brave", "brave-browser"),
    "edge": ("microsoft-edge-stable", "microsoft-edge"),
}


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
    if "chromium" not in found and not found:
        found.append("chromium")
    return found


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
    for browser in browsers or ["chromium"]:
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
    for browser in browsers or ["chromium"]:
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
                    "wired": wired, "loaded": state["loaded"],
                    "active": state["active"], "profile": state["profile"],
                    "manual": wiring_disabled()})
    return out
