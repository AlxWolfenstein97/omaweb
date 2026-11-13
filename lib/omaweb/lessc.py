"""Compile Catppuccin LESS userstyles into plain CSS blocks.

The important trick here is *compile first, strip second*: ``@-moz-document``
preludes can contain LESS functions (Syncthing builds its regexp from a
variable), so the only reliable point to read them is after ``lessc`` has
resolved them.  We then split the compiled CSS into one file per
``@-moz-document`` block, which lets the extension inject only the blocks whose
selectors actually match the current URL.

The bundle is remote, mutable data, so it is treated as data only: the compiler
is asked to disable ``@plugin`` where the installed lessc supports it, and the
source is refused if it still contains ``@plugin``, inline JavaScript, or an
import that is not CSS or the vendored palette library.  A replaced bundle
therefore cannot turn a theme switch into code execution.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from . import util


_MOZ = "@-moz-document"
_IMPORT_RE = re.compile(
    r"@import\s+(?:\([^)]*\)\s*)?(?:url\()?['\"]"
    r"https?://userstyles\.catppuccin\.com/lib/(?:std/v1|lib)\.less"
    r"['\"]\)?\s*;",
    re.I,
)
_CALL_RE = re.compile(r"\b(domain|url-prefix|url|regexp)\s*\(", re.I)
_PLUGIN_RE = re.compile(r"@plugin\b", re.I)
_IMPORT_STMT_RE = re.compile(r"@import\s*(?:\(([^)]*)\))?\s*([^;]+);", re.I)
_FILE_FN_RE = re.compile(r"\b(data-uri|image-size|image-width|image-height)\s*\(", re.I)

# lessc flags we add when the installed compiler knows them.  Keyed by path;
# builds compile many styles through the same binary.
_SAFETY_FLAGS: dict[str, tuple[str, ...]] = {}


def _safety_flags(lessc: str) -> tuple[str, ...]:
    """Return parser-level hardening flags supported by this lessc."""
    cached = _SAFETY_FLAGS.get(lessc)
    if cached is not None:
        return cached
    flags: list[str] = []
    try:
        proc = subprocess.run(
            [lessc, "--help"], capture_output=True, text=True, timeout=15,
        )
        help_text = (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        help_text = ""
    if "--disable-plugin-rule" in help_text:
        flags.append("--disable-plugin-rule")
    _SAFETY_FLAGS[lessc] = tuple(flags)
    return _SAFETY_FLAGS[lessc]


# --------------------------------------------------------------------------- LESS

def var_lines(entry: dict[str, Any]) -> str:
    """Turn UserStyle ``@var`` metadata into LESS variables."""
    out: list[str] = []
    variables = entry.get("vars") or {}
    if not isinstance(variables, dict):
        return ""
    for key in sorted(variables):
        var = variables[key]
        if not isinstance(var, dict):
            continue
        name = str(var.get("name") or key)
        kind = str(var.get("type") or "")
        default = var.get("default")
        if default is None:
            continue
        if kind == "select":
            value = str(default)
        elif kind == "checkbox":
            value = "1" if util.truthy(default) else "0"
        elif kind == "range":
            units = var.get("units") or ""
            value = f"{default}{units}"
        elif kind == "text":
            escaped = str(default).replace("\\", "\\\\").replace('"', '\\"')
            escaped = escaped.replace("\n", "\\n").replace("\r", "")
            value = f'"{escaped}"'
        elif kind == "color":
            value = str(default)
        else:
            continue
        out.append(f"@{name}: {value};")
    return "\n".join(out)


def rewrite_imports(source: str, lib_path: Path) -> str:
    return _IMPORT_RE.sub(f'@import "{lib_path}";', source)


def compile_less(source: str, lessc: str, workdir: Path, name: str,
                 timeout: int = 90) -> tuple[str | None, str | None]:
    """Compile one style.  Returns ``(css, error)``."""
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / f"{name}.less"
    out = workdir / f"{name}.css"
    util.atomic_write(src, source)
    try:
        proc = subprocess.run(
            [lessc, "--no-color", *_safety_flags(lessc), str(src), str(out)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "lessc timed out"
    except OSError as exc:
        return None, f"lessc failed to start: {exc}"

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "lessc failed").strip()
        return None, err[:1200]
    try:
        css = out.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read compiled CSS: {exc}"
    return css, None


# --------------------------------------------------------------------------- CSS scanning

def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return n


def _skip_comment(text: str, i: int) -> int:
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return len(text) if end < 0 else end + 2
    if text.startswith("//", i):
        end = text.find("\n", i + 2)
        return len(text) if end < 0 else end + 1
    return i


def _find_open_brace(text: str, i: int) -> int:
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "*/":
            i = _skip_comment(text, i)
            continue
        if c == "{":
            return i
        i += 1
    return -1


def _find_matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "*/":
            i = _skip_comment(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_token(text: str, token: str, start: int) -> int:
    n = len(text)
    i = start
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "*/":
            i = _skip_comment(text, i)
            continue
        if text.startswith(token, i):
            return i
        i += 1
    return -1


def _strip_comments(text: str) -> str:
    """Drop comments while leaving strings alone.

    ``//`` is only treated as a comment when it starts a line or follows
    whitespace, so ``http://`` inside an unquoted ``url()`` survives.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            end = _skip_string(text, i)
            out.append(text[i:end])
            i = end
            continue
        if text.startswith("/*", i):
            i = _skip_comment(text, i)
            continue
        if text.startswith("//", i):
            prev = text[i - 1] if i else "\n"
            if prev.isspace() or prev in ";{}":
                i = _skip_comment(text, i)
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _find_js_backtick(text: str) -> int:
    """Index of an inline-JavaScript backtick outside strings and comments."""
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if text.startswith("/*", i):
            i = _skip_comment(text, i)
            continue
        if text.startswith("//", i):
            prev = text[i - 1] if i else "\n"
            if prev.isspace() or prev in ";{}":
                i = _skip_comment(text, i)
                continue
        if c == "`":
            return i
        i += 1
    return -1


def unsafe_reason(source: str, lib_path: Path) -> str | None:
    """Refuse LESS that can execute code or read arbitrary files.

    The Catppuccin bundle is fetched from a mutable upstream release and may be
    replaced independently of the plugin.  It is only ever allowed to be a
    stylesheet: no ``@plugin``, no inline JavaScript, and no LESS/inline import
    other than the vendored palette library.  CSS imports are passthrough and
    safe, so they stay.
    """
    clean = _strip_comments(source)
    if _PLUGIN_RE.search(clean):
        return "refused unsafe LESS: @plugin is not allowed"
    if _find_js_backtick(source) >= 0:
        return "refused unsafe LESS: inline JavaScript is not allowed"
    file_fn = _FILE_FN_RE.search(clean)
    if file_fn:
        return (f"refused unsafe LESS: {file_fn.group(1)}() reads local files "
                "and is not allowed")
    allowed = str(lib_path)
    for match in _IMPORT_STMT_RE.finditer(clean):
        options = {
            item.strip().lower()
            for item in (match.group(1) or "").split(",")
            if item.strip()
        }
        target = match.group(2).strip()
        if target.lower().startswith("url("):
            target = target[4:].strip()
            if target.endswith(")"):
                target = target[:-1].strip()
        bare = target.strip("\"'").strip()
        # Only `(css)` is harmless; every other option forces LESS to read and
        # process the target, including `url(...)` imports.  The extension
        # check is deliberately case-sensitive: lessc only treats lowercase
        # `.css` as a passthrough, while `.CSS` is compiled as LESS.
        if options - {"css"}:
            return f"refused unsafe LESS: import of {bare[:80]!r} is not allowed"
        if "css" in options or bare == allowed:
            continue
        if bare.endswith(".css"):
            continue
        return f"refused unsafe LESS: import of {bare[:80]!r} is not allowed"
    return None


def css_unescape_string(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        raw = raw[1:-1]
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        nxt = raw[i]
        if nxt in "\n\r\f":
            # Escaped newline: line continuation.
            i += 1
            if nxt == "\r" and i < n and raw[i] == "\n":
                i += 1
            continue
        hex_match = re.match(r"[0-9a-fA-F]{1,6}", raw[i:])
        if hex_match:
            code = int(hex_match.group(0), 16)
            i += len(hex_match.group(0))
            if i < n and raw[i] in " \t\r\n\f":
                i += 1
            try:
                out.append(chr(code))
            except ValueError:
                pass
            continue
        out.append(nxt)
        i += 1
    return "".join(out)


def _extract_arg(text: str, open_paren: int) -> tuple[str, int]:
    """Return the raw argument inside ``(...)`` and the index after ``)``."""
    depth = 0
    i = open_paren
    n = len(text)
    start = open_paren + 1
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "*/":
            i = _skip_comment(text, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    return text[start:], n


def parse_prelude(prelude: str) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    i = 0
    while True:
        m = _CALL_RE.search(prelude, i)
        if not m:
            break
        kind = m.group(1).lower()
        arg, i = _extract_arg(prelude, m.end() - 1)
        value = css_unescape_string(arg).strip()
        if not value:
            continue
        if kind == "domain":
            host = value.strip().lstrip("*.").split("/", 1)[0].strip().lower()
            if host:
                matches.append({"type": "domain", "value": host})
        elif kind == "url":
            if value.lower().startswith(("http://", "https://")):
                matches.append({"type": "url", "value": value})
        elif kind == "url-prefix":
            if value.lower().startswith(("http://", "https://")):
                matches.append({"type": "prefix", "value": value})
        elif kind == "regexp":
            try:
                re.compile(value)
            except re.error:
                continue
            matches.append({"type": "regexp", "value": value})
    return util.dedupe(matches)


def extract_blocks(css: str) -> tuple[list[dict[str, Any]], str]:
    """Split compiled CSS into ``@-moz-document`` blocks + global CSS."""
    blocks: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        idx = _find_token(css, _MOZ, pos)
        if idx < 0:
            break
        brace = _find_open_brace(css, idx + len(_MOZ))
        if brace < 0:
            break
        close = _find_matching_brace(css, brace)
        if close < 0:
            break
        prelude = css[idx + len(_MOZ):brace]
        body = css[brace + 1:close]
        blocks.append({"matches": parse_prelude(prelude), "css": body})
        spans.append((idx, close + 1))
        pos = close + 1

    if not spans:
        return blocks, css

    chunks: list[str] = []
    last = 0
    for start, end in spans:
        chunks.append(css[last:start])
        last = end
    chunks.append(css[last:])
    global_css = "".join(chunks)
    if not _strip_comments(global_css).strip():
        global_css = ""
    return blocks, global_css


# --------------------------------------------------------------------------- style build

def build_style(entry: dict[str, Any], *, lessc: str, lib_path: Path, palette_less: str,
                out_root: Path, workdir: Path, timeout: int = 90) -> tuple[dict[str, Any] | None, str | None]:
    """Compile one bundle entry and write its block files under ``out_root``."""
    sid = entry["id"]
    source = rewrite_imports(entry["source"], lib_path)
    source = source + "\n\n" + var_lines(entry) + "\n" + palette_less
    refused = unsafe_reason(source, lib_path)
    if refused:
        return None, refused
    css, error = compile_less(source, lessc, workdir, sid, timeout=timeout)
    if error or css is None:
        return None, error or "unknown lessc error"

    blocks, global_css = extract_blocks(css)
    usable = [b for b in blocks if b["matches"]]
    if not usable:
        return None, "compiled CSS has no supported @-moz-document selectors"

    (out_root / "s" / sid).mkdir(parents=True, exist_ok=True)
    global_text = (global_css.strip() + "\n") if global_css else ""
    block_entries: list[dict[str, Any]] = []
    total = 0
    for index, block in enumerate(usable):
        body = (global_text + block["css"].strip()).strip() + "\n"
        rel = f"s/{sid}/{index}.css"
        util.atomic_write(out_root / rel, body)
        total += len(body.encode("utf-8"))
        block_entries.append({
            "index": index,
            "file": rel,
            "matches": block["matches"],
            "bytes": len(body.encode("utf-8")),
        })

    return {
        "id": sid,
        "name": entry.get("name") or sid,
        "description": entry.get("description") or "",
        "author": entry.get("author") or "Catppuccin",
        "homepage": entry.get("homepage") or "",
        "support": entry.get("support") or "",
        "version": entry.get("version") or "",
        "namespace": entry.get("namespace") or "",
        "source": "catppuccin",
        "blocks": block_entries,
        "bytes": total,
    }, None
