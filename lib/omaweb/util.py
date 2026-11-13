"""Small shared helpers for OmaWeb.

Standard library only.  The plugin is intentionally self-contained so the
service, the theme-set hook and the CLI all work from a plain checkout.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable


VERSION = "1.0.2"


def now() -> str:
    return (_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


# --------------------------------------------------------------------------- paths

def plugin_dir() -> Path:
    """The repository / installed plugin root."""
    env = os.environ.get("OMAWEB_PLUGIN_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # lib/omaweb/util.py -> lib/omaweb -> lib -> <plugin>
    return Path(__file__).resolve().parents[2]


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home()).expanduser()


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base).expanduser() / "omaweb" if base else home() / ".config" / "omaweb"


def config_path() -> Path:
    return config_dir() / "config.json"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base).expanduser() if base else home() / ".local" / "state") / "omarchy" / "omaweb"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base).expanduser() if base else home() / ".cache") / "omarchy" / "omaweb"


def share_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base).expanduser() if base else home() / ".local" / "share") / "omaweb"


def stylus_dir() -> Path:
    return share_dir() / "stylus"


def index_path() -> Path:
    return share_dir() / "index.json"


def theme_dir() -> Path:
    env = os.environ.get("OMAWEB_THEME_DIR")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base).expanduser() if base else home() / ".local" / "state") / "omarchy" / "current" / "theme"


def theme_name() -> str:
    env = os.environ.get("OMAWEB_THEME_NAME")
    if env:
        return env
    for candidate in (
        theme_dir().parent / "theme.name",
        theme_dir().parent.parent / "theme.name",
    ):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    return "unknown"


def bundle_cache() -> Path:
    return cache_dir() / "bundle" / "import.json"


# --------------------------------------------------------------------------- io

def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def atomic_write(path: Path, data: str | bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=False) + "\n")


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


# --------------------------------------------------------------------------- shell

def run(cmd: Iterable[str], *, cwd: Path | None = None, timeout: int = 120,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def lessc_path() -> str | None:
    env = os.environ.get("OMAWEB_LESSC")
    if env and Path(env).exists():
        return env
    for candidate in (
        share_dir() / "less" / "node_modules" / ".bin" / "lessc",
        plugin_dir() / "node_modules" / ".bin" / "lessc",
    ):
        if candidate.exists():
            return str(candidate)
    return which("lessc")


def chromium_path() -> str | None:
    return which("chromium", "chromium-browser", "google-chrome-stable", "google-chrome",
                 "brave", "brave-browser", "microsoft-edge-stable", "microsoft-edge")


# --------------------------------------------------------------------------- colour

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def norm_hex(value: str, fallback: str = "#000000") -> str:
    """Return a 6 digit lower-case hex colour (alpha dropped)."""
    m = _HEX.match(str(value or "").strip())
    if not m:
        return fallback
    h = m.group(1).lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h[:6]


def hex_rgb(value: str) -> tuple[int, int, int]:
    h = norm_hex(value).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def blend(a: str, b: str, p: float = 0.5) -> str:
    ar, ag, ab = hex_rgb(a)
    br, bg, bb = hex_rgb(b)
    return "#%02x%02x%02x" % (
        round(ar * (1 - p) + br * p),
        round(ag * (1 - p) + bg * p),
        round(ab * (1 - p) + bb * p),
    )


def slugify(value: str, fallback: str = "style") -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip("-._").lower()
    return s or fallback


# --------------------------------------------------------------------------- misc

def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def dedupe(items: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    out: list[Any] = []
    for item in items:
        key = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else item
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
