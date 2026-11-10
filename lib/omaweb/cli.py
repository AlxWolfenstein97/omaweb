"""Command line interface for OmaWeb."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import browser, build, bundle, config, darkreader, palette, scan, state, stylus, util


# --------------------------------------------------------------------------- helpers

def _index_or_build() -> dict:
    return build.load_index()


def _print_json(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=False))


def _set_enabled(ids: list[str], enabled: bool, *, quiet: bool = False) -> int:
    if not ids:
        print("omaweb: no style ids given", file=sys.stderr)
        return 2
    cfg = config.load(create=True)
    overrides = cfg.setdefault("overrides", {})
    for style_id in ids:
        overrides[style_id] = enabled
    config.save(cfg)

    # Rewrite the extension files: a disabled style becomes an empty file, so
    # the change is live on the next page load.  The compile cache makes this
    # cheap; it does not recompile the bundle.
    build.apply(quiet=True, no_fetch=True)

    if not quiet:
        verb = "enabled" if enabled else "disabled"
        print(f"omaweb: {verb} {len(ids)} style(s) — applies on the next page load")
        index = util.read_json(util.index_path(), {}) or {}
        known = {s.get("id") for s in index.get("styles") or []}
        missing = [style_id for style_id in ids if style_id not in known]
        if missing:
            print("omaweb: note: ids not present in the current build: "
                  + ", ".join(missing))
    return 0


# --------------------------------------------------------------------------- commands

def cmd_apply(args: argparse.Namespace) -> int:
    build.apply(force=args.force, no_fetch=args.no_fetch, quiet=args.quiet,
                jobs=args.jobs, timeout=args.timeout)
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    result = bundle.fetch(force=args.force, quiet=args.quiet)
    if args.json:
        _print_json(result)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    scan.scan(offline=args.offline, as_json=args.json, quiet=args.quiet)
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    if args.action == "export":
        exported = state.export_bundle()
        if args.file == "-":
            _print_json(exported)
            return 0
        path = Path(args.file).expanduser() if args.file else util.home() / "omaweb-state.json"
        util.write_json(path, exported)
        print(f"omaweb: exported state to {path}")
        return 0

    summary = state.import_bundle(Path(args.file).expanduser(),
                                  replace=args.replace)
    if args.json:
        _print_json(summary)
        return 0
    cfg = summary.get("config")
    if cfg:
        print(f"omaweb: config imported ({cfg['overrides']} override(s), "
              f"{cfg['unthemed']} Dark Reader opt-out(s), theme {cfg['theme']})")
    if summary.get("applyError"):
        print(f"omaweb: could not re-apply the build: {summary['applyError']}",
              file=sys.stderr)
        print("omaweb: run: omaweb apply", file=sys.stderr)
    elif cfg and not summary.get("applied"):
        print("omaweb: run: omaweb apply")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    index = _index_or_build()
    styles = list(index.get("styles") or [])
    if args.enabled:
        styles = [s for s in styles if s.get("enabled")]
    elif args.disabled:
        styles = [s for s in styles if not s.get("enabled")]
    if args.json:
        _print_json(styles)
        return 0
    if not styles:
        print("omaweb: no matching styles")
        return 0
    for style in styles:
        flag = "on " if style.get("enabled") else "off"
        blocks = len(style.get("blocks") or [])
        print(f"{flag} {style['id']:<28} {blocks:>2} block(s)  {style.get('name') or ''}")
    print(f"\n{len(styles)} style(s)")
    return 0


def _all_style_ids() -> list[str]:
    index = build.load_index()
    return sorted(str(s.get("id")) for s in index.get("styles") or [] if s.get("id"))


def cmd_enable(args: argparse.Namespace) -> int:
    ids = list(args.ids)
    if args.all:
        ids = sorted(set(ids) | set(_all_style_ids()))
    return _set_enabled(ids, True, quiet=args.quiet)


def cmd_disable(args: argparse.Namespace) -> int:
    ids = list(args.ids)
    if args.all:
        ids = sorted(set(ids) | set(_all_style_ids()))
    return _set_enabled(ids, False, quiet=args.quiet)


def cmd_match(args: argparse.Namespace) -> int:
    index = _index_or_build()
    ids = build.match_url(index, args.url, enabled_only=not args.all)
    if args.json:
        _print_json(ids)
        return 0
    if not ids:
        print("omaweb: no style matches")
        return 0
    for style_id in ids:
        style = next((s for s in index.get("styles") or [] if s["id"] == style_id), None)
        name = (style or {}).get("name") or ""
        suffix = "" if (style or {}).get("enabled") else "  (disabled)"
        print(f"{style_id}  {name}{suffix}")
    return 0


def cmd_stylus(args: argparse.Namespace) -> int:
    if args.action == "status":
        info = stylus.status()
        if args.json:
            _print_json(info)
            return 0
        store = [c for c in info["copies"] if c["source"] == "store"]
        print(f"stylus: {'installed (store)' if store else 'not installed'}")
        for item in info["copies"]:
            print(f"  {item['source']:<8} {item['id']}  {item['path']}")
        if not store:
            print(f"  install from: {info['storeUrl']}")
        asset = info["asset"]
        print(f"assets: {asset['root']} (id {asset['id']})")
        print(f"  manifest: {'ok' if asset['exists'] else 'missing'}"
              f", exposed={asset['exposed']}, permissions={len(asset['permissions'])}")
        print(f"export: {info['export']} ({'present' if info['exportExists'] else 'missing'})")
        state = info["import"]
        if state["known"]:
            if state["imported"]:
                print("import: done (OmaWeb shims found in the Stylus database)")
            else:
                print("import: not yet — run: omaweb stylus import --open")
        else:
            print("import: unknown (no Stylus profile found)")
        if info["managerUrl"]:
            print(f"manager: {info['managerUrl']}")
        return 0

    if args.action == "install":
        if args.json:
            _print_json({"storeUrl": stylus.STORE_URL})
            return 0
        print(f"omaweb: install Stylus from the Chrome Web Store: {stylus.STORE_URL}")
        if not args.quiet:
            stylus.open_store(quiet=args.quiet)
        return 0

    if args.action == "open":
        return 0 if stylus.open_manager(quiet=args.quiet) else 1

    # import
    index = _index_or_build()
    path = stylus.write_export(index)
    friendly = stylus.visible_export()
    if args.print:
        print(path.read_text(encoding="utf-8"))
        return 0
    if args.json:
        _print_json({
            "path": str(path),
            "importFile": str(friendly) if friendly else None,
            "manager": stylus.manager_url(),
        })
        return 0
    print(f"omaweb: Stylus import file: {path}")
    if friendly:
        print(f"omaweb: visible copy:       {friendly}")
    state = stylus.import_state()
    if state["known"] and state["imported"]:
        print("omaweb: OmaWeb shims are already in Stylus;"
              " re-import only after adding or removing styles")
    print("omaweb: Stylus also lists your own styles under"
          ' "installed styles not in the backup" — leave that orphan list alone,'
          " its Delete button removes them too")
    target = friendly or path
    if args.open:
        stylus.open_export_folder(quiet=args.quiet,
                                  directory=friendly.parent if friendly else None)
        stylus.open_manager(quiet=args.quiet)
        print(f"omaweb: drag {target} onto the Stylus window, or click Import there")
        print("omaweb: check the result with: omaweb stylus status")
    else:
        print(f"omaweb: in Stylus, open Manage → Import, then pick: {target}")
        print("omaweb: (or run: omaweb stylus import --open)")
    return 0


def _darkreader_write(index: dict, cfg: dict,
                      path: str | None) -> tuple[dict, Path, Path | None]:
    settings = darkreader.build_settings(index, cfg)
    out = darkreader.write_settings(settings)
    if path:
        extra = Path(path).expanduser()
        util.write_json(extra, settings)
    else:
        extra = darkreader.visible_export()
    return settings, out, extra


def _darkreader_covered(index: dict, site: str, *, enabled_only: bool = True) -> list[str]:
    """OmaWeb styles that paint ``site`` (enabled ones by default)."""
    urls = [site] if "://" in site else [
        f"https://{site}", f"https://{site.rstrip('/')}/",
    ]
    found: list[str] = []
    for url in urls:
        for style_id in build.match_url(index, url, enabled_only=enabled_only):
            if style_id not in found:
                found.append(style_id)
    return found


def _style_patterns(index: dict, style_id: str) -> list[str]:
    style = next((s for s in index.get("styles") or [] if s.get("id") == style_id), None)
    patterns: list[str] = []
    for block in (style or {}).get("blocks") or []:
        for pattern in darkreader.site_patterns(block.get("matches") or []):
            if pattern not in patterns:
                patterns.append(pattern)
    return patterns


def cmd_darkreader(args: argparse.Namespace) -> int:
    action = args.action
    cfg = config.load()
    index = _index_or_build()

    if action == "list":
        sites = darkreader.user_patterns(cfg)
        if args.json:
            _print_json({"unthemed": sites})
        elif sites:
            print("omaweb: Dark Reader stays off on these sites:")
            for site in sites:
                print(f"  {site}")
        else:
            print("omaweb: no Dark Reader opt-outs; only covered sites are disabled")
        return 0

    if action == "export":
        settings, out, extra = _darkreader_write(index, cfg, args.path)
        if args.json:
            _print_json(settings)
            return 0
        covered = len(darkreader.covered_patterns(index))
        unthemed = len(darkreader.user_patterns(cfg))
        print(f"omaweb: Dark Reader settings: {out}")
        if extra and extra != out:
            print(f"omaweb: copy: {extra}")
        print(f"omaweb: {len(settings['disabledFor'])} site pattern(s) disabled in"
              f" Dark Reader ({covered} covered, {unthemed} unthemed)")
        print("omaweb: import it in Dark Reader → Settings → Advanced → Import Settings")
        print("omaweb: run this again after a theme switch; the import applies live")
        return 0

    site = args.site.strip()
    pattern = darkreader.normalize_pattern(site)
    if not pattern:
        print("omaweb: give a site such as example.com or https://example.com/path",
              file=sys.stderr)
        return 2

    if action == "unthemed" and args.remove:
        config.set_unthemed(pattern, False)
        cfg = config.load()
        _, out, extra = _darkreader_write(index, cfg, args.path)
        if args.json:
            _print_json({
                "site": pattern,
                "removed": True,
                "unthemed": darkreader.user_patterns(cfg),
                "settings": str(out),
                "visible": str(extra) if extra else None,
            })
            return 0
        print(f"omaweb: removed {pattern} from the Dark Reader opt-outs")
        print(f"omaweb: Dark Reader settings: {out}")
        if extra and extra != out:
            print(f"omaweb: copy: {extra}")
        print("omaweb: import it in Dark Reader → Settings → Advanced → Import Settings")
        return 0

    if action == "restore":
        covering = _darkreader_covered(index, site, enabled_only=False)
        restored = [style_id for style_id in covering
                    if not config.is_enabled(cfg, style_id)]
        overrides = cfg.setdefault("overrides", {})
        for style_id in covering:
            overrides[style_id] = True
        config.save(cfg)
        config.set_unthemed(pattern, False)
        if restored:
            build.apply(quiet=True, no_fetch=True)
            index = build.load_index()
        cfg = config.load()
        _, out, extra = _darkreader_write(index, cfg, args.path)
        if args.json:
            _print_json({
                "site": pattern,
                "action": "restore",
                "restoredStyles": restored,
                "coveredStyles": covering,
                "unthemed": darkreader.user_patterns(cfg),
                "settings": str(out),
                "visible": str(extra) if extra else None,
            })
            return 0
        if restored:
            print(f"omaweb: restored {len(restored)} OmaWeb style(s): "
                  f"{', '.join(restored)}")
        elif covering:
            print(f"omaweb: {len(covering)} OmaWeb style(s) already enabled for {pattern}")
        else:
            print(f"omaweb: no OmaWeb style covers {pattern}")
        print(f"omaweb: Dark Reader settings: {out}")
        if extra and extra != out:
            print(f"omaweb: copy: {extra}")
        print("omaweb: import it in Dark Reader → Settings → Advanced → Import Settings")
        return 0

    covering = _darkreader_covered(index, site)
    broad = [(style_id, len(_style_patterns(index, style_id)))
             for style_id in covering]
    broad = [(style_id, count) for style_id, count in broad if count > 1]
    if covering:
        _set_enabled(covering, False, quiet=True)
        index = build.load_index()

    if action == "take":
        config.set_unthemed(pattern, False)
    else:
        config.set_unthemed(pattern, True)
    cfg = config.load()
    _, out, extra = _darkreader_write(index, cfg, args.path)

    if args.json:
        _print_json({
            "site": pattern,
            "action": action,
            "disabledStyles": covering,
            "unthemed": darkreader.user_patterns(cfg),
            "settings": str(out),
            "visible": str(extra) if extra else None,
        })
        return 0

    if covering:
        print(f"omaweb: disabled {len(covering)} OmaWeb style(s): {', '.join(covering)}")
    else:
        print(f"omaweb: no enabled OmaWeb style covers {pattern}")
    for style_id, count in broad:
        print(f"omaweb: note: {style_id} also covers {count - 1} other site pattern(s)")
    if action == "take":
        print(f"omaweb: Dark Reader now owns {pattern}")
    else:
        print(f"omaweb: {pattern} stays unthemed; Dark Reader is off there")
    print(f"omaweb: Dark Reader settings: {out}")
    if extra and extra != out:
        print(f"omaweb: copy: {extra}")
    print("omaweb: import it in Dark Reader → Settings → Advanced → Import Settings")
    return 0


def cmd_browser(args: argparse.Namespace) -> int:
    asset = stylus.asset_dir()
    if args.action == "unwire":
        # Remove our path from every known flags file, even a browser that has
        # since been uninstalled; the file itself may still be around.
        browsers = getattr(args, "browser", None) or list(browser.FLAG_FILES)
    else:
        browsers = getattr(args, "browser", None) or browser.detect()

    if args.action == "detect":
        report = browser.detect_report()
        if args.json:
            _print_json(report)
            return 0
        print("chromium-family: " + (", ".join(report["installed"]) or "none"))
        print("firefox-family:  " + (", ".join(report["firefox"]) or "none")
              + ("  (not supported)" if report["firefox"] else ""))
        print("default:         " + (report["defaultEntry"] or "unknown")
              + (f" ({report['default']})" if report["default"] else ""))
        print("wired:           " + (", ".join(report["wired"]) or "none"))
        return 0

    if args.action == "status":
        rows = browser.status(asset)
        if args.json:
            _print_json(rows)
            return 0
        for row in rows:
            if row.get("wired") and row.get("loaded"):
                state = "wired+loaded"
            elif row.get("wired"):
                state = "wired"
            elif row.get("loaded"):
                state = "loaded"
            elif row.get("exists"):
                state = "present"
            else:
                state = "absent"
            print(f"{row['browser']:<8} {state:<13} {row['path']}")
        loaded = [row for row in rows if row.get("loaded")]
        wired_any = any(row.get("wired") for row in rows)
        manual = bool(rows and rows[0].get("manual"))
        if loaded and not wired_any:
            print("omaweb: loaded by hand (chrome://extensions); no flags needed")
        elif wired_any and not loaded:
            print("omaweb: wired but not loaded yet — restart the browser once")
        elif not wired_any and not loaded and manual:
            print("omaweb: flags left alone; load unpacked from " + str(asset))
        elif not wired_any and not loaded:
            print("omaweb: not wired — run: omaweb browser wire")
        if not stylus.web_root():
            print("omaweb: asset extension missing; run: omaweb apply")
        return 0

    if not stylus.web_root():
        print("omaweb: asset extension missing — run: omaweb apply", file=sys.stderr)
        return 1
    if args.action == "wire":
        if not args.dry_run:
            browser.set_wiring_disabled(False)
        rows = browser.wire(asset, browsers, dry_run=args.dry_run)
    else:
        rows = browser.unwire(asset, browsers, dry_run=args.dry_run)
        if not args.dry_run:
            browser.set_wiring_disabled(True)
    if args.json:
        _print_json(rows)
        return 0
    for row in rows:
        verb = "changed" if row.get("changed") else "unchanged"
        print(f"{row['browser']:<8} {verb:<9} {row['path']}")
    return 0


def cmd_theme(args: argparse.Namespace) -> int:
    cfg = config.load(create=True)

    if args.action == "list":
        names = palette.available_themes()
        pinned = str(cfg.get("theme") or palette.AUTO)
        desktop = util.theme_name()
        if args.json:
            _print_json({
                "pinned": pinned,
                "desktop": desktop,
                "themes": names,
            })
            return 0
        if not names:
            print("omaweb: no Omarchy themes found under "
                  + ", ".join(str(p) for p in palette.theme_roots()))
            return 0
        for name in names:
            marks = []
            if name == pinned:
                marks.append("pinned")
            if name == desktop:
                marks.append("desktop")
            suffix = f"  ({', '.join(marks)})" if marks else ""
            print(f"{name}{suffix}")
        if pinned == palette.AUTO:
            print(f"\nauto → {desktop} (follows the desktop)")
        return 0

    if args.action == "show":
        resolved = palette.resolve_theme()
        if args.json:
            _print_json(resolved)
            return 0
        origin = "auto" if resolved["auto"] else f"pinned, desktop {resolved['desktop']}"
        print(f"omaweb theme: {resolved['name']} ({resolved['mode']})  [{origin}]")
        if resolved["missing"]:
            print(f"omaweb: pinned theme {resolved['requested']!r} is not installed;"
                  f" falling back to {resolved['name']}")
        print(f"colors:       {resolved['path']}")
        print(f"background:   {resolved['roles']['base']}")
        print(f"text:         {resolved['roles']['text']}")
        print(f"accent:       {resolved['roles']['mauve']}")
        return 0

    if args.action == "auto":
        cfg["theme"] = palette.AUTO
    else:
        name = args.name.strip()
        if not palette.find_theme(name):
            print(f"omaweb: no installed theme named {name!r}", file=sys.stderr)
            available = palette.available_themes()
            if available:
                print("omaweb: available: " + ", ".join(available), file=sys.stderr)
            return 2
        cfg["theme"] = name

    config.save(cfg)
    if not args.no_apply:
        build.apply(quiet=args.quiet, no_fetch=True)
    if not args.quiet:
        resolved = palette.resolve_theme()
        origin = "auto" if resolved["auto"] else "pinned"
        print(f"omaweb: theme {resolved['name']} ({resolved['mode']}, {origin})"
              + ("" if args.no_apply else " — applied"))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    index = util.read_json(util.index_path())
    info = stylus.status()
    cfg = config.load()
    theme = (index or {}).get("theme") or {}
    counts = (index or {}).get("counts") or {}
    ext_base = stylus.extension_base_url()
    web = stylus.web_root()
    web_files = 0
    if web and (web / "s").is_dir():
        web_files = sum(1 for _ in (web / "s").rglob("*.css"))
    if not theme:
        # No build yet: still report the live Omarchy theme instead of "unknown".
        colors, _text = palette.read_colors()
        theme = {"name": util.theme_name() or "unknown",
                 "mode": palette.theme_mode(colors, "dark") if colors else None}
    dr_path = darkreader.settings_path()
    dr_visible = darkreader.visible_export()
    dr_unthemed = darkreader.user_patterns(cfg)
    dr_info = {
        "path": str(dr_path),
        "exists": dr_path.exists(),
        "visible": str(dr_visible) if dr_visible else None,
        "unthemed": dr_unthemed,
    }
    extension = {
        "baseUrl": ext_base,
        "webRoot": str(web) if web else None,
        "cssFiles": web_files,
        "asset": info.get("asset"),
    }
    if args.json:
        _print_json({
            "version": util.VERSION,
            "theme": theme,
            "counts": counts,
            "darkreader": dr_info,
            "extension": extension,
            "stylus": info,
            "config": cfg,
            "index": str(util.index_path()),
            "build": (index or {}).get("build"),
        })
        return 0
    pinned = str(cfg.get("theme") or palette.AUTO)
    theme_note = "" if pinned == palette.AUTO else f" — pinned; desktop {util.theme_name()}"
    print(f"OmaWeb {util.VERSION}")
    print(f"theme:   {theme.get('name') or 'unknown'} ({theme.get('mode') or '?'}){theme_note}")
    print(f"build:   {(index or {}).get('build') or 'none'}")
    print(f"styles:  {counts.get('enabled', 0)}/{counts.get('total', 0)} enabled, "
          f"{counts.get('errors', 0)} compile error(s)")
    print(f"extension: {ext_base or 'missing — run: omaweb apply'} "
          f"(asset id {stylus.ASSET_ID})")
    if web:
        print(f"web:     {web} ({web_files} css files)")
    print(f"stylus:  {'installed (store)' if info['installed'] else 'not installed — ' + info['storeUrl']}")
    print(f"export:  {info['export']} ({'present' if info['exportExists'] else 'missing'})")
    state = info["import"]
    if state["known"] and not state["imported"]:
        print("import:  not yet — run: omaweb stylus import --open")
    elif state["imported"]:
        print("import:  done (OmaWeb shims found in Stylus)")
    print(f"darkreader: {dr_info['path']} "
          f"({'present' if dr_info['exists'] else 'missing — run: omaweb darkreader export'})")
    if dr_unthemed:
        print(f"unthemed: {', '.join(dr_unthemed)} (Dark Reader off)")
    print(f"config:  {util.config_path()}")
    return 0


def _check(rows: list[tuple[str, str, str]], status: str, name: str, detail: str) -> None:
    rows.append((status, name, detail))


def cmd_doctor(args: argparse.Namespace) -> int:
    rows: list[tuple[str, str, str]] = []

    if sys.version_info >= (3, 9):
        _check(rows, "PASS", "python", sys.version.split()[0])
    else:
        _check(rows, "FAIL", "python", f"{sys.version.split()[0]} (need >= 3.9)")

    lessc = util.lessc_path()
    if lessc:
        try:
            version = util.run([lessc, "--version"], timeout=15).stdout.strip()
        except Exception:  # noqa: BLE001
            version = "unknown"
        _check(rows, "PASS", "lessc", f"{lessc} ({version})")
    else:
        _check(rows, "FAIL", "lessc", "missing — npm install --prefix "
               f"{util.share_dir()}/less less@4")

    lib = util.plugin_dir() / "lib" / "vendor" / "less" / "lib" / "std" / "v1.less"
    _check(rows, "PASS" if lib.exists() else "FAIL", "less library", str(lib))

    resolved = palette.resolve_theme()
    colors = Path(resolved["path"])
    if resolved["missing"]:
        _check(rows, "WARN", "theme",
               f"{resolved['requested']!r} not installed; using {resolved['name']} ({colors})")
    elif colors.exists():
        note = "" if resolved["auto"] else f"; desktop {resolved['desktop']}"
        _check(rows, "PASS", "theme",
               f"{resolved['name']} ({colors}){note}")
    else:
        _check(rows, "WARN", "theme", f"no colors.toml at {colors}")

    bundle_path = util.bundle_cache()
    if bundle_path.exists():
        _check(rows, "PASS", "bundle", f"{bundle_path} ({bundle_path.stat().st_size} bytes)")
    else:
        _check(rows, "WARN", "bundle", "not cached — run: omaweb fetch")

    index = util.read_json(util.index_path())
    if isinstance(index, dict) and index.get("styles"):
        counts = index.get("counts") or {}
        _check(rows, "PASS", "build", f"{(index.get('build') or '?')} "
               f"({counts.get('enabled', 0)}/{counts.get('total', 0)} enabled)")
    else:
        _check(rows, "WARN", "build", "not generated — run: omaweb apply")

    info = stylus.status()
    asset = info["asset"]
    _check(rows, "PASS" if asset["exists"] else "FAIL", "asset extension",
           f"{asset['root']} (id {asset['id']})" if asset["exists"]
           else "missing — run: omaweb apply")
    _check(rows, "PASS" if asset["exposed"] else "FAIL", "extension manifest",
           "s/* and theme.json are web-accessible" if asset["exposed"]
           else "not exposed — run: omaweb apply")
    clean = not asset["permissions"] and not asset["contentScripts"]
    _check(rows, "PASS" if clean else "WARN", "asset surface",
           "no permissions, no content scripts" if clean
           else f"permissions={asset['permissions']} "
                f"content_scripts={len(asset['contentScripts'])}")

    web = stylus.web_root()
    theme_file = (web / "theme.json") if web else None
    _check(rows, "PASS" if theme_file and theme_file.exists() else "FAIL", "extension web",
           str(web) if web else "missing")

    _check(rows, "PASS" if info["installed"] else "WARN", "stylus",
           "store install" if info["installed"] else f"not installed — {info['storeUrl']}")
    _check(rows, "PASS" if info["exportExists"] else "WARN", "stylus export", info["export"])
    state = info["import"]
    if state["known"]:
        _check(rows, "PASS" if state["imported"] else "WARN", "stylus import",
               "OmaWeb shims found" if state["imported"]
               else "not imported — run: omaweb stylus import --open")
    else:
        _check(rows, "WARN", "stylus import", "unknown (no Stylus profile found)")

    rows_status = browser.status(stylus.asset_dir())
    wired = [row["browser"] for row in rows_status if row.get("wired")]
    loaded = [row["browser"] for row in rows_status if row.get("loaded")]
    installed = [row["browser"] for row in rows_status if row.get("installed")]
    _check(rows, "PASS" if installed else "WARN", "browsers",
           ", ".join(installed) if installed else "none detected — install a Chromium-family browser")
    default_row = next((row for row in rows_status if row.get("default")), None)
    if default_row:
        _check(rows, "PASS", "default browser",
               f"{default_row['browser']} ({default_row['path']})")
    else:
        report = browser.detect_report()
        if report["firefox"]:
            _check(rows, "WARN", "default browser",
                   f"{report['defaultEntry'] or 'unknown'} — Firefox-family, not supported")
        elif report["defaultEntry"]:
            _check(rows, "WARN", "default browser",
                   f"{report['defaultEntry']} — not a browser OmaWeb wires")
        else:
            _check(rows, "WARN", "default browser", "unknown")
    if browser.detect_firefox():
        _check(rows, "WARN", "firefox",
               ", ".join(browser.detect_firefox()) + " — not supported (unsigned extensions)")
    if loaded:
        _check(rows, "PASS", "extension load",
               f"loaded in {loaded[0]}"
               + (" (by flag)" if wired else " (loaded by hand)"))
    elif wired:
        _check(rows, "PASS", "extension load",
               f"{', '.join(wired)} wired — restart the browser once")
    elif browser.wiring_disabled():
        _check(rows, "WARN", "extension load",
               f"load unpacked from {stylus.asset_dir()} (flags left alone)")
    else:
        _check(rows, "WARN", "extension load",
               "not loaded — run: omaweb browser wire, or Load unpacked in chrome://extensions")
    if wired:
        _check(rows, "PASS", "browser flags", ", ".join(wired))
    elif loaded:
        _check(rows, "PASS", "browser flags", "not needed (loaded by hand)")
    elif browser.wiring_disabled():
        _check(rows, "PASS", "browser flags", "left alone (manual load)")
    else:
        _check(rows, "WARN", "browser flags", "not wired — run: omaweb browser wire")

    try:
        config.load()
        _check(rows, "PASS", "config", str(util.config_path()))
    except Exception as exc:  # noqa: BLE001
        _check(rows, "FAIL", "config", str(exc))

    if args.json:
        _print_json([{"status": s, "check": n, "detail": d} for s, n, d in rows])
    else:
        for status, name, detail in rows:
            print(f"{status:<4} {name:<14} {detail}")
    return 1 if any(s == "FAIL" for s, _, _ in rows) else 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"omaweb {util.VERSION}")
    return 0


# --------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omaweb",
        description="Catppuccin userstyles reshaped to the active Omarchy theme, via Stylus.",
    )
    parser.add_argument("--version", action="version", version=f"omaweb {util.VERSION}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("apply", help="compile the bundle and publish it")
    p.add_argument("--force", action="store_true", help="recompile from scratch")
    p.add_argument("--no-fetch", action="store_true", help="use the cached bundle")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--jobs", type=int, default=None)
    p.add_argument("--timeout", type=int, default=90)
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("fetch", help="download the Catppuccin bundle")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("scan", help="compare the cached bundle with upstream")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("state", help="export/import the portable config")
    st = p.add_subparsers(dest="action", required=True)
    e = st.add_parser("export", help="write a portable state bundle")
    e.add_argument("file", nargs="?", default=None,
                   help="destination (default ~/omaweb-state.json; - for stdout)")
    e.set_defaults(func=cmd_state)
    i = st.add_parser("import", help="merge a portable state bundle into this box")
    i.add_argument("file")
    i.add_argument("--replace", action="store_true",
                   help="replace config instead of merging")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_state)

    p = sub.add_parser("list", help="list styles in the current build")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--enabled", action="store_true")
    group.add_argument("--disabled", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("enable", help="enable styles (live, no re-import)")
    p.add_argument("ids", nargs="*")
    p.add_argument("--all", action="store_true",
                   help="every style in the current build")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("disable", help="disable styles (live, no re-import)")
    p.add_argument("ids", nargs="*")
    p.add_argument("--all", action="store_true", help="every style in the current build")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("match", help="which styles match a URL")
    p.add_argument("url")
    p.add_argument("--all", action="store_true",
                   help="include disabled styles")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("stylus", help="Stylus integration")
    sty = p.add_subparsers(dest="action", required=True)
    s = sty.add_parser("status")
    s.add_argument("--json", action="store_true")
    s = sty.add_parser("import")
    s.add_argument("--open", action="store_true")
    s.add_argument("--print", dest="print", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s = sty.add_parser("open")
    s.add_argument("--quiet", action="store_true")
    s = sty.add_parser("install", help="open the Chrome Web Store page for Stylus")
    s.add_argument("--force", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stylus)

    p = sub.add_parser("darkreader", help="generate and manage Dark Reader settings")
    dr = p.add_subparsers(dest="action", required=True)

    d = dr.add_parser("export", help="write settings JSON for the current theme")
    d.add_argument("--json", action="store_true")
    d.add_argument("--path", default=None,
                   help="also write to FILE (default ~/Downloads/omaweb-darkreader.json)")

    t = dr.add_parser("take", help="let Dark Reader theme a site OmaWeb covers")
    t.add_argument("site", help="host, host/path or URL")
    t.add_argument("--json", action="store_true")
    t.add_argument("--path", default=None,
                   help="also write to FILE (default ~/Downloads/omaweb-darkreader.json)")

    u = dr.add_parser("unthemed",
                      help="leave a site unthemed: no Catppuccin, no Dark Reader")
    u.add_argument("site", help="host, host/path or URL")
    u.add_argument("--remove", action="store_true",
                   help="let Dark Reader theme the site again")
    u.add_argument("--json", action="store_true")
    u.add_argument("--path", default=None,
                   help="also write to FILE (default ~/Downloads/omaweb-darkreader.json)")

    r = dr.add_parser("restore",
                      help="bring Catppuccin back on a site and re-export Dark Reader")
    r.add_argument("site", help="host, host/path or URL")
    r.add_argument("--json", action="store_true")
    r.add_argument("--path", default=None,
                   help="also write to FILE (default ~/Downloads/omaweb-darkreader.json)")

    lst = dr.add_parser("list", help="list sites kept unthemed")
    lst.add_argument("--json", action="store_true")

    p.set_defaults(func=cmd_darkreader)

    p = sub.add_parser("browser", help="wire the OmaWeb asset extension into browser flags")
    br = p.add_subparsers(dest="action", required=True)
    b = br.add_parser("detect", help="what is installed, and what is the default")
    b.add_argument("--json", action="store_true")
    b = br.add_parser("status")
    b.add_argument("--json", action="store_true")
    b = br.add_parser("wire")
    b.add_argument("--browser", action="append", choices=list(browser.FLAG_FILES))
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--json", action="store_true")
    b = br.add_parser("unwire")
    b.add_argument("--browser", action="append", choices=list(browser.FLAG_FILES))
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_browser)

    p = sub.add_parser("theme", help="choose which Omarchy theme OmaWeb renders in")
    th = p.add_subparsers(dest="action", required=True)
    t = th.add_parser("show", help="show the resolved OmaWeb theme")
    t.add_argument("--json", action="store_true")
    t = th.add_parser("list", help="list installed Omarchy themes")
    t.add_argument("--json", action="store_true")
    t = th.add_parser("use", help="pin OmaWeb to an installed theme")
    t.add_argument("name")
    t.add_argument("--no-apply", action="store_true")
    t.add_argument("--quiet", action="store_true")
    t = th.add_parser("auto", help="follow the desktop theme again")
    t.add_argument("--no-apply", action="store_true")
    t.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_theme)

    p = sub.add_parser("status", help="summarise the current state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="check the installation")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("version")
    p.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        return cmd_status(argparse.Namespace(json=False))
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"omaweb: {type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("OMAWEB_DEBUG"):
            raise
        return 1
