"""Scan Catppuccin userstyles for post-bundle issues and maintainer gaps."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import bundle, util


REPO = "catppuccin/userstyles"
RELEASE_TAG = "all-userstyles-export"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _gh_json(path: str, params: dict[str, Any] | None = None) -> Any:
    """Prefer ``gh api`` (uses the user's auth); fall back to urllib.

    ``gh`` may be installed but not logged in — in that case anonymous (or
    ``GITHUB_TOKEN``) requests still work, so a broken ``gh`` must not turn a
    scan into "zero issues".
    """
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    gh = util.which("gh")
    gh_error = None
    if gh:
        proc = util.run([gh, "api", "-H", "Accept: application/vnd.github+json", path + query], timeout=60)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        gh_error = (proc.stderr or proc.stdout or "").strip()

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OmaWeb/" + util.VERSION,
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request("https://api.github.com/" + path + query, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        message = f"GitHub HTTP {exc.code}: {body}"
        if gh_error:
            message += f" (gh: {gh_error[:200]})"
        raise RuntimeError(message) from exc


def release_info(*, offline: bool = False) -> dict[str, Any] | None:
    cache = util.cache_dir() / "scan" / "release.json"
    cached = util.read_json(cache)
    if offline:
        return cached
    try:
        data = _gh_json(f"repos/{REPO}/releases/tags/{RELEASE_TAG}")
        assets = data.get("assets") or []
        asset = assets[0] if assets else {}
        info = {
            "tag": data.get("tag_name") or RELEASE_TAG,
            "published_at": data.get("published_at"),
            "asset": {
                "name": asset.get("name"),
                "size": asset.get("size"),
                "updated_at": asset.get("updated_at"),
                "url": asset.get("browser_download_url"),
            },
            "fetchedAt": _now(),
        }
        util.write_json(cache, info)
        return info
    except Exception as exc:  # noqa: BLE001
        if cached:
            cached = dict(cached)
            cached["warning"] = f"using cached release info ({exc})"
            return cached
        return {"error": str(exc), "tag": RELEASE_TAG, "asset": {}}


def issues_since(since: str, *, offline: bool = False, max_pages: int = 20) -> tuple[list[dict[str, Any]], str | None]:
    cache = util.cache_dir() / "scan" / "issues.json"
    cached = util.read_json(cache)
    if offline:
        return (cached.get("issues") if isinstance(cached, dict) else []) or [], "offline"

    issues: list[dict[str, Any]] = []
    warning = None
    try:
        for page in range(1, max_pages + 1):
            page_items = _gh_json(f"repos/{REPO}/issues", {
                "state": "open",
                "since": since,
                "per_page": 100,
                "page": page,
                "sort": "updated",
                "direction": "desc",
            })
            if not isinstance(page_items, list):
                break
            for item in page_items:
                if item.get("pull_request"):
                    continue
                issues.append(item)
            if len(page_items) < 100:
                break
        util.write_json(cache, {"fetchedAt": _now(), "since": since, "issues": issues})
    except Exception as exc:  # noqa: BLE001
        warning = str(exc)
        if isinstance(cached, dict) and cached.get("issues"):
            issues = cached["issues"]
            warning = f"using cached issues ({exc})"
        else:
            issues = []
    return issues, warning


def _issue_style_ids(issue: dict[str, Any], known: set[str]) -> list[str]:
    out: list[str] = []
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else str(label)
        if name in known:
            out.append(name)
    title = str(issue.get("title") or "")
    m = re.match(r"^\s*(?:fix|feat|refactor|style|chore|docs|revert)\(([^)]+)\)", title, re.I)
    if m:
        sid = m.group(1).strip()
        if sid in known:
            out.append(sid)
    return util.dedupe(out)


def _issue_row(issue: dict[str, Any], style_ids: list[str]) -> dict[str, Any]:
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "url": issue.get("html_url"),
        "state": issue.get("state"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "labels": [l.get("name") if isinstance(l, dict) else str(l) for l in (issue.get("labels") or [])],
        "styles": style_ids,
    }


def scan(*, offline: bool = False, since: str | None = None, as_json: bool = False,
         quiet: bool = False) -> dict[str, Any]:
    styles = []
    try:
        styles = bundle.load(required=False)
    except Exception:
        styles = []
    bundle_ids = {s["id"] for s in styles}

    if not offline and not util.userstyles_cache().exists():
        try:
            bundle.fetch(quiet=True)
        except Exception:
            pass
    maintainers = bundle.parse_userstyles()
    known = set(bundle_ids) | set(maintainers)

    info = release_info(offline=offline)
    asset = (info or {}).get("asset") or {}
    since_value = since or asset.get("updated_at") or "1970-01-01T00:00:00Z"
    issues, warning = issues_since(since_value, offline=offline)

    by_style: dict[str, list[dict[str, Any]]] = {}
    unmapped: list[dict[str, Any]] = []
    for issue in issues:
        ids = _issue_style_ids(issue, known)
        row = _issue_row(issue, ids)
        if not ids:
            unmapped.append(row)
        for sid in ids:
            by_style.setdefault(sid, []).append(row)

    unmaintained = []
    for sid, meta in maintainers.items():
        if meta.get("current-maintainers"):
            continue
        unmaintained.append({
            "id": sid,
            "name": meta.get("name") or sid,
            "link": meta.get("link"),
            "inBundle": sid in bundle_ids,
            "issues": len(by_style.get(sid) or []),
        })
    unmaintained.sort(key=lambda item: (not item["inBundle"], item["id"]))

    attention = []
    for sid in sorted(set(by_style) | {x["id"] for x in unmaintained}):
        in_bundle = sid in bundle_ids
        attention.append({
            "id": sid,
            "inBundle": in_bundle,
            "issues": len(by_style.get(sid) or []),
            "unmaintained": not bool((maintainers.get(sid) or {}).get("current-maintainers")),
        })

    report = {
        "generatedAt": _now(),
        "repository": REPO,
        "release": info,
        "since": since_value,
        "bundle": {
            "path": str(util.bundle_cache()),
            "styles": len(styles),
        },
        "warning": warning,
        "issues": {
            "count": len(issues),
            "mapped": sum(1 for issue in issues if _issue_style_ids(issue, known)),
            "unmapped": unmapped,
            "byStyle": {sid: rows for sid, rows in sorted(by_style.items())},
        },
        "maintainers": {
            "known": len(maintainers),
            "unmaintained": unmaintained,
        },
        "needsAttention": attention,
    }

    util.write_json(util.state_dir() / "scan.json", report)
    _write_markdown(report)

    if as_json:
        print(json.dumps(report, indent=2))
    elif not quiet:
        _print_report(report)
    return report


def _write_markdown(report: dict[str, Any]) -> None:
    lines = [
        "# OmaWeb scan",
        "",
        f"Generated: {report['generatedAt']}",
        f"Bundle styles: {report['bundle']['styles']}",
        f"Bundle asset updated: {(report.get('release') or {}).get('asset', {}).get('updated_at')}",
        f"Issues updated since: {report['since']}",
        "",
        f"Open issues after the bundle: {report['issues']['count']} "
        f"({report['issues']['mapped']} mapped to a style)",
        "",
    ]
    for sid, rows in report["issues"]["byStyle"].items():
        lines.append(f"## {sid}")
        for row in rows:
            lines.append(f"- #{row['number']} [{row['title']}]({row['url']}) — updated {row['updated_at']}")
        lines.append("")
    lines.append("## Maintainer gaps")
    for item in report["maintainers"]["unmaintained"]:
        flags = []
        if not item["inBundle"]:
            flags.append("not in bundle")
        lines.append(f"- {item['id']} — {item['name']}"
                     + (f" ({', '.join(flags)})" if flags else ""))
    lines.append("")
    util.atomic_write(util.state_dir() / "scan.md", "\n".join(lines))


def _print_report(report: dict[str, Any]) -> None:
    release = report.get("release") or {}
    asset = release.get("asset") or {}
    print(f"omaweb scan — bundle {asset.get('updated_at') or 'unknown'} "
          f"({report['bundle']['styles']} styles)")
    if report.get("warning"):
        print(f"omaweb: {report['warning']}")
    print(f"open issues updated since bundle: {report['issues']['count']} "
          f"({report['issues']['mapped']} mapped to a style)")
    for sid, rows in list(report["issues"]["byStyle"].items())[:20]:
        for row in rows:
            print(f"  {sid:28s} #{row['number']:<5} {row['title']}")
    if len(report["issues"]["byStyle"]) > 20:
        print(f"  … {len(report['issues']['byStyle']) - 20} more styles")

    gaps = report["maintainers"]["unmaintained"]
    print(f"maintainer gaps: {len(gaps)} styles with no current maintainer")
    for item in gaps[:30]:
        flags = []
        if not item["inBundle"]:
            flags.append("not in bundle")
        suffix = f" ({', '.join(flags)})" if flags else ""
        print(f"  {item['id']:28s} {item['name']}{suffix}")
    if len(gaps) > 30:
        print(f"  … {len(gaps) - 30} more")

    print(f"needs attention: {len(report['needsAttention'])} styles")
    print(f"report: {util.state_dir() / 'scan.json'}")
