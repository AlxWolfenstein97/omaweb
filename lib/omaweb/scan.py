"""Compare the cached Catppuccin bundle with the current upstream download.

``omaweb scan`` answers one question: has Catppuccin changed anything since the
bundle OmaWeb compiled?  It downloads the current export next to the cache,
diffs style versions, and flags the changes that affect styles enabled here.
The scan is advisory; OmaWeb never disables a style on its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import bundle, config, util


REPO = "catppuccin/userstyles"


def _catalog(styles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for style in styles:
        sid = style.get("id")
        if not sid:
            continue
        out[sid] = {
            "name": style.get("name") or sid,
            "version": style.get("version") or "",
            "digest": util.sha256_text(style.get("source") or ""),
        }
    return out


def compare(cached: list[dict[str, Any]], upstream: list[dict[str, Any]],
            cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return the upstream changes and a summary, marking enabled styles."""
    before = _catalog(cached)
    after = _catalog(upstream)
    changes: list[dict[str, Any]] = []
    for sid in sorted(set(before) | set(after)):
        old = before.get(sid)
        new = after.get(sid)
        if old and not new:
            kind = "removed"
        elif new and not old:
            kind = "added"
        elif old["version"] != new["version"]:
            kind = "updated"
        elif old["digest"] != new["digest"]:
            kind = "changed"
        else:
            continue
        changes.append({
            "id": sid,
            "name": (new or old)["name"],
            "kind": kind,
            "from": (old or {}).get("version") or None,
            "to": (new or {}).get("version") or None,
            "enabled": config.is_enabled(cfg, sid),
        })

    summary = {
        "total": len(changes),
        "added": sum(1 for item in changes if item["kind"] == "added"),
        "removed": sum(1 for item in changes if item["kind"] == "removed"),
        "updated": sum(1 for item in changes if item["kind"] == "updated"),
        "changed": sum(1 for item in changes if item["kind"] == "changed"),
        "enabled": sum(1 for item in changes if item["enabled"]),
    }
    return changes, summary


def _upstream(*, offline: bool) -> tuple[Path | None, dict[str, Any]]:
    """Return a path to the current upstream export and its metadata.

    Online, this is a conditional download into ``cache/scan/``; offline, the
    last copy is reused so a scan can still show what changed.
    """
    directory = util.cache_dir() / "scan"
    dest = directory / "upstream.json"
    meta_path = directory / "upstream-meta.json"
    meta = util.read_json(meta_path, {}) or {}

    if offline:
        return (dest if dest.exists() else None), meta

    try:
        info = bundle.download(
            bundle.BUNDLE_URL, dest,
            etag=meta.get("etag"),
            last_modified=meta.get("last_modified"),
        )
        meta["checkedAt"] = util.now()
        if info.get("downloaded"):
            meta["downloadedAt"] = util.now()
            for key in ("etag", "last_modified", "size"):
                if info.get(key) is not None:
                    meta[key] = info[key]
        meta.pop("error", None)
    except Exception as exc:  # noqa: BLE001 - report, keep the last copy
        meta["error"] = f"{type(exc).__name__}: {exc}"

    try:
        util.write_json(meta_path, meta)
    except OSError:
        pass
    return (dest if dest.exists() else None), meta


def scan(*, offline: bool = False, as_json: bool = False,
         quiet: bool = False) -> dict[str, Any]:
    cfg = config.load()
    cached_path = util.bundle_cache()
    cached: list[dict[str, Any]] = []
    cached_error = None
    if cached_path.exists():
        try:
            cached = bundle.parse(cached_path)
        except Exception as exc:  # noqa: BLE001
            cached_error = f"{type(exc).__name__}: {exc}"

    upstream_path, meta = _upstream(offline=offline)
    upstream_error = meta.get("error")
    if upstream_path is None:
        upstream_error = upstream_error or (
            "no cached upstream copy — run: omaweb scan (without --offline)")

    if upstream_path is None:
        changes: list[dict[str, Any]] = []
        summary = {"total": 0, "added": 0, "removed": 0, "updated": 0,
                   "changed": 0, "enabled": 0}
    else:
        upstream = bundle.parse(upstream_path)
        changes, summary = compare(cached, upstream, cfg)

    report: dict[str, Any] = {
        "generatedAt": util.now(),
        "repository": REPO,
        "offline": offline,
        "cached": {
            "path": str(cached_path),
            "styles": len(cached),
            "error": cached_error,
        },
        "upstream": {
            "path": str(upstream_path) if upstream_path else None,
            "checkedAt": meta.get("checkedAt"),
            "downloadedAt": meta.get("downloadedAt"),
            "size": meta.get("size"),
            "error": upstream_error,
        },
        "summary": summary,
        "changes": changes,
    }
    if cached_error:
        report["error"] = f"cached bundle unreadable: {cached_error}"
    elif upstream_error:
        report["error"] = upstream_error

    try:
        util.write_json(util.state_dir() / "scan.json", report)
        _write_markdown(report)
    except OSError:
        pass

    if as_json:
        import json

        print(json.dumps(report, indent=2))
    elif not quiet:
        _print_report(report)
    return report


def _write_markdown(report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# OmaWeb scan",
        "",
        f"Generated: {report['generatedAt']}",
        f"Cached bundle: {report['cached']['path']} "
        f"({report['cached']['styles']} styles)",
        f"Upstream checked: {report['upstream'].get('checkedAt') or 'never'}"
        + (" (offline)" if report["offline"] else ""),
        "",
    ]
    if report.get("error"):
        lines += [f"**{report['error']}**", ""]
    lines += [
        f"{summary['total']} style(s) changed upstream "
        f"({summary['enabled']} enabled here): "
        f"{summary['updated']} updated, {summary['added']} added, "
        f"{summary['removed']} removed, {summary['changed']} changed in place.",
        "",
    ]
    for item in report["changes"]:
        versions = ""
        if item["from"] and item["to"] and item["from"] != item["to"]:
            versions = f" {item['from']} → {item['to']}"
        elif item["kind"] == "added":
            versions = f" {item['to'] or ''}".rstrip()
        lines.append(f"- **{item['id']}** ({item['name']}) — {item['kind']}"
                     f"{versions}{' · enabled' if item['enabled'] else ' · disabled'}")
    lines.append("")
    util.atomic_write(util.state_dir() / "scan.md", "\n".join(lines))


def _print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    cached = report["cached"]
    print(f"omaweb scan — cached bundle {cached['styles']} styles"
          + (" (offline)" if report["offline"] else ""))
    if report.get("error"):
        print(f"omaweb: {report['error']}")
        if not report["upstream"].get("path"):
            return
    if not summary["total"]:
        print("upstream: nothing changed — the cached bundle is current")
        return
    print(f"upstream: {summary['total']} style(s) changed "
          f"({summary['enabled']} enabled here)")
    for item in report["changes"][:40]:
        if item["from"] and item["to"] and item["from"] != item["to"]:
            versions = f"{item['from']} → {item['to']}"
        else:
            versions = item["kind"]
        print(f"  {item['id']:28s} {versions:28s} "
              f"{'enabled' if item['enabled'] else 'disabled'}")
    if summary["total"] > 40:
        print(f"  … {summary['total'] - 40} more")
    print(f"report: {util.state_dir() / 'scan.json'}")
