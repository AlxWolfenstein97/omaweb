#!/bin/bash
#
# Hermetic self-check.  Uses throwaway XDG directories, a fake theme and a fake
# store-installed Stylus, so it never reads or writes the user's real OmaWeb
# state.  Safe to run from a checkout or from the installed plugin directory.
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="$here/bin/omaweb"
fail=0

ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; fail=1; }
note() { printf '\n== %s ==\n' "$1"; }

cleanup() {
  [[ -n ${tmp:-} ]] && rm -rf "$tmp"
}
trap cleanup EXIT

note "python syntax"
if python3 -m compileall -q "$here/lib" "$here/bin"; then ok "compileall"; else bad "compileall"; fi

# Resolve lessc *before* redirecting XDG_DATA_HOME, so a normal user install is
# found even though the test uses a throwaway data dir.
lessc="${OMAWEB_LESSC:-}"
if [[ -z $lessc ]]; then
  for candidate in \
    "$(command -v lessc 2>/dev/null || true)" \
    "$HOME/.local/share/omaweb/less/node_modules/.bin/lessc" \
    "$here/node_modules/.bin/lessc"; do
    if [[ -n $candidate && -x $candidate ]]; then lessc="$candidate"; break; fi
  done
fi
if [[ -n $lessc ]]; then ok "lessc: $lessc"; else bad "lessc not found (npm install --prefix ~/.local/share/omaweb/less less@4)"; fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/omaweb-check.XXXXXX")"
export XDG_CONFIG_HOME="$tmp/config"
export XDG_STATE_HOME="$tmp/state"
export XDG_DATA_HOME="$tmp/data"
export XDG_CACHE_HOME="$tmp/cache"
export OMAWEB_THEME_NAME="omaweb-check-dark"
export OMAWEB_THEME_DIR="$tmp/theme"
[[ -n $lessc ]] && export OMAWEB_LESSC="$lessc"

mkdir -p "$XDG_CONFIG_HOME" "$XDG_STATE_HOME" "$XDG_DATA_HOME" \
         "$XDG_CACHE_HOME/omarchy/omaweb/bundle" "$OMAWEB_THEME_DIR"

asset="$XDG_DATA_HOME/omaweb/extension"

# A fake store-installed Stylus.  OmaWeb must not patch it; it only has to find
# it and point the shims at the separate asset extension.
store="$XDG_CONFIG_HOME/chromium/Default/Extensions/clngdbkpkpeebahjckkjfobafhncgmne/2.4.17"
mkdir -p "$store"
cat >"$store/manifest.json" <<'JSON'
{
  "manifest_version": 3,
  "name": "Stylus",
  "version": "2.4.17"
}
JSON

# Prefer the user's cached Catppuccin bundle; only hit the network if absent.
if [[ -f $HOME/.cache/omarchy/omaweb/bundle/import.json ]]; then
  cp "$HOME/.cache/omarchy/omaweb/bundle/import.json" \
     "$XDG_CACHE_HOME/omarchy/omaweb/bundle/import.json"
else
  note "fetch bundle"
  if "$bin" fetch --quiet; then ok "fetched bundle"; else bad "could not fetch bundle"; fi
fi

cat >"$OMAWEB_THEME_DIR/colors.toml" <<'TOML'
mode = "dark"
accent = "#7aa2f7"
selection = "#292e42"
muted = "#414868"
background = "#1a1b26"
dark_background = "#13141c"
darker_background = "#0e0e14"
lighter_background = "#24283b"
foreground = "#a9b1d6"
dark_foreground = "#565f89"
light_foreground = "#b4bee6"
red = "#f7768e"
yellow = "#e0af68"
orange = "#eb927b"
green = "#9ece6a"
cyan = "#449dab"
blue = "#7aa2f7"
magenta = "#ad8ee6"
brown = "#75493d"
TOML

mkdir -p "$XDG_CONFIG_HOME/omaweb"
cat >"$XDG_CONFIG_HOME/omaweb/config.json" <<'JSON'
{
  "theme": "auto",
  "overrides": {},
  "darkreader": {"unthemed": []}
}
JSON

note "compile and publish"
if "$bin" apply --no-fetch --quiet; then ok "apply"; else bad "apply"; fi
if [[ -f $XDG_DATA_HOME/omaweb/index.json ]]; then ok "index.json"; else bad "index.json missing"; fi
if [[ -d $asset/s ]]; then ok "asset extension web dir"; else bad "asset extension web dir missing"; fi

# One explicit opt-out gives the asset checks a disabled file to inspect.
"$bin" disable claude --quiet >/dev/null 2>&1 || true

python3 - "$XDG_DATA_HOME/omaweb" "$asset" "$here" <<'PY' || fail=1
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
asset = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(pathlib.Path(sys.argv[3]) / "lib"))
from omaweb import config, darkreader, palette, stylus

index = json.loads((root / "index.json").read_text())
export = json.loads((root / "stylus" / "omaweb.json").read_text())
manifest = json.loads((asset / "manifest.json").read_text())
web = asset

def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        raise SystemExit(1)

check(index["version"] == 2, "index version")
check(index["theme"]["mode"] == "dark", "theme is dark")
check(index["counts"]["compiled"] >= 120, f"compiled {index['counts']['compiled']} styles")
check(len(index["styles"]) >= 120, "styles in index")
check(index["baseUrl"] == f"chrome-extension://{stylus.ASSET_ID}", "index uses the asset extension")
check(index["extension"]["id"] == stylus.ASSET_ID, "index records the asset id")
check(manifest.get("key"), "asset manifest pins a key")
check(not manifest.get("permissions"), "asset manifest has no permissions")
check(not manifest.get("content_scripts"), "asset manifest has no content scripts")
check(any("s/*" in (e.get("resources") or [])
          for e in manifest.get("web_accessible_resources") or []),
      "asset manifest exposes s/*")
check(all(s["enabled"] for s in export), "all Stylus shims enabled")
check(all(f"chrome-extension://{stylus.ASSET_ID}/" in (s["sections"][0].get("code") or "")
          for s in export), "every shim imports from the asset extension")
check((web / "s" / "github" / "0.css").read_text().strip(), "enabled style has css")
check((web / "s" / "claude" / "0.css").read_text().strip() == "",
      "disabled style is an empty file")

check(config.normalize({"probe": {"urls": {}}, "extraSites": []})
      == {"version": 1, "theme": "auto", "overrides": {},
          "darkreader": {"unthemed": []}},
      "config drops probe and extra sites")
cfg_norm = config.normalize(
    {"darkreader": {"unthemed": [" a.test ", "a.test", "b.test", 3]}})
check(cfg_norm["darkreader"]["unthemed"] == ["a.test", "b.test"],
      "config: unthemed list is cleaned")
check(config.is_enabled({}, "chatgpt"), "config: styles are on by default")
check(not config.is_enabled({"overrides": {"chatgpt": False}}, "chatgpt"),
      "config: an explicit override still opts out")

dr = darkreader.build_settings(index)
check(dr["schemeVersion"] == 2, "darkreader: current scheme version")
check(dr["enabled"] and dr["enabledByDefault"], "darkreader: enabled by default")
check(dr["theme"]["engine"] == "dynamicTheme", "darkreader: dynamic engine")
check(dr["theme"]["mode"] == 1, "darkreader: dark theme -> dark mode")
check(dr["theme"]["darkSchemeBackgroundColor"] == index["theme"]["roles"]["base"],
      "darkreader: background follows the theme")
check(dr["theme"]["darkSchemeTextColor"] == index["theme"]["roles"]["text"],
      "darkreader: text follows the theme")
check(dr["theme"]["selectionColor"] == index["theme"]["roles"]["surface2"],
      "darkreader: selection follows the theme")
check(dr["theme"]["styleSystemControls"] is True,
      "darkreader: native controls follow the dark theme")
check(dr["detectDarkTheme"] is True, "darkreader: native dark sites are detected")
check("claude.ai" not in dr["disabledFor"],
      "darkreader: a disabled style is not disabled")
check(any(p.startswith("/") and "github.com" in p for p in dr["disabledFor"]),
      "darkreader: regexp matches are carried over")
check(darkreader.site_patterns([{"type": "domain", "value": "*.example.com"}])
      == ["example.com"], "darkreader: domain wildcard becomes a host")
check(darkreader.site_patterns([{"type": "prefix", "value": "https://go.dev/tour/"}])
      == ["go.dev/tour"], "darkreader: prefix keeps its path")
check(darkreader.site_patterns([{"type": "regexp", "value": r"^https?://example\..*"}])
      == [r"/^https?://example\..*/"], "darkreader: regexp is wrapped")
check(dr["disabledFor"] == sorted(dr["disabledFor"]),
      "darkreader: patterns are sorted")
check(darkreader.normalize_pattern("https://example.com/path/") == "example.com/path",
      "darkreader: URL normalizes to host/path")
check(darkreader.normalize_pattern(r"/^https?://example\./") == r"/^https?://example\./",
      "darkreader: regexp pattern is preserved")
dr_user = darkreader.build_settings(
    index, {"darkreader": {"unthemed": ["unthemed.test"]}})
check("unthemed.test" in dr_user["disabledFor"],
      "darkreader: user opt-out is disabled")
check(dr_user["disabledFor"] == sorted(dr_user["disabledFor"]),
      "darkreader: opt-out list is sorted")
dr_path = darkreader.write_settings(dr)
check(dr_path.exists(), "darkreader: canonical settings written")
check(json.loads(dr_path.read_text())["theme"]["mode"] == 1,
      "darkreader: canonical settings parse")

state = stylus.import_state()
check(state["known"] and state["imported"] is False, "import state starts empty")
db = (pathlib.Path(stylus.profile_roots()[0]) / "Default" / "IndexedDB"
      / f"chrome-extension_{stylus.STORE_ID}_0.indexeddb.leveldb")
db.mkdir(parents=True, exist_ok=True)
(db / "000003.log").write_bytes(b"junk" + stylus.SHIM_PREFIX.encode("latin-1") + b"junk")
state = stylus.import_state()
check(state["imported"] and state["count"] == 1, "import state detects imported shims")
PY

note "dark reader export"
if "$bin" darkreader export --json >"$tmp/darkreader.json"; then
  if python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d["theme"]["engine"] == "dynamicTheme" and d["disabledFor"] else 1)
' "$tmp/darkreader.json"; then
    ok "darkreader export --json"
  else
    bad "darkreader export --json is missing settings"
  fi
else
  bad "darkreader export --json"
fi
if "$bin" darkreader export --path "$tmp/darkreader-path.json" >/dev/null; then
  ok "darkreader export --path"
else
  bad "darkreader export --path"
fi
if [[ -s $tmp/darkreader-path.json ]]; then
  ok "darkreader --path file"
else
  bad "darkreader --path file missing"
fi

note "dark reader site handoff"
if "$bin" darkreader take github.com --json --path "$tmp/dr-take-settings.json" \
    >"$tmp/dr-take.json"; then
  ok "darkreader take"
else
  bad "darkreader take"
fi
python3 - "$tmp/dr-take.json" "$XDG_CONFIG_HOME/omaweb/config.json" \
  "$tmp/dr-take-settings.json" <<'PY' || fail=1
import json, sys
take = json.load(open(sys.argv[1]))
cfg = json.load(open(sys.argv[2]))
dr = json.load(open(sys.argv[3]))
assert "github" in take["disabledStyles"], take
assert cfg["overrides"]["github"] is False, cfg
assert take["unthemed"] == [], take
assert not any("github.com" == p or "github.com" in p for p in dr["disabledFor"]), dr
PY
"$bin" enable github --quiet >/dev/null 2>&1 || true
if "$bin" darkreader unthemed unthemed.test --json --path "$tmp/dr-unthemed-settings.json" \
    >"$tmp/dr-unthemed.json"; then
  ok "darkreader unthemed"
else
  bad "darkreader unthemed"
fi
python3 - "$tmp/dr-unthemed.json" "$XDG_CONFIG_HOME/omaweb/config.json" \
  "$tmp/dr-unthemed-settings.json" <<'PY' || fail=1
import json, sys
unthemed = json.load(open(sys.argv[1]))
cfg = json.load(open(sys.argv[2]))
dr = json.load(open(sys.argv[3]))
assert unthemed["unthemed"] == ["unthemed.test"], unthemed
assert cfg["darkreader"]["unthemed"] == ["unthemed.test"], cfg
assert "unthemed.test" in dr["disabledFor"], dr
PY
if "$bin" darkreader list --json | python3 -c '
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if d["unthemed"] == ["unthemed.test"] else 1)
'; then
  ok "darkreader list"
else
  bad "darkreader list"
fi
if "$bin" darkreader unthemed unthemed.test --remove --json \
    --path "$tmp/dr-theme-again-settings.json" >"$tmp/dr-theme-again.json"; then
  ok "darkreader unthemed --remove"
else
  bad "darkreader unthemed --remove"
fi
python3 - "$tmp/dr-theme-again.json" "$XDG_CONFIG_HOME/omaweb/config.json" \
  "$tmp/dr-theme-again-settings.json" <<'PY' || fail=1
import json, sys
removed = json.load(open(sys.argv[1]))
cfg = json.load(open(sys.argv[2]))
dr = json.load(open(sys.argv[3]))
assert removed["removed"] is True, removed
assert cfg["darkreader"]["unthemed"] == [], cfg
assert "unthemed.test" not in dr["disabledFor"], dr
PY

if "$bin" status --json >"$tmp/status.json"; then
  if python3 - "$tmp/status.json" <<'PY'; then
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d["darkreader"]["unthemed"] == [] and d["stylus"]["installed"] else 1)
PY
    ok "status --json"
  else
    bad "status --json payload"
  fi
else
  bad "status --json"
fi

note "live enable/disable"
if "$bin" enable twitter --quiet; then ok "enable"; else bad "enable"; fi
if [[ -s $asset/s/twitter/0.css ]]; then ok "enabled style now has css"; else bad "enable did not rewrite the file"; fi
"$bin" disable twitter --quiet || true
if [[ ! -s $asset/s/twitter/0.css ]]; then ok "disable empties the file"; else bad "disable did not empty the file"; fi
cp "$XDG_CONFIG_HOME/omaweb/config.json" "$tmp/config-before-all.json"
if "$bin" enable --all --quiet; then ok "enable --all"; else bad "enable --all"; fi
if [[ -s $asset/s/chatgpt/0.css ]]; then
  ok "enable --all includes a login-walled style"
else
  bad "enable --all missed a login-walled style"
fi
if "$bin" disable --all --quiet; then ok "disable --all"; else bad "disable --all"; fi
if [[ ! -s $asset/s/chatgpt/0.css ]]; then
  ok "disable --all empties login-walled styles"
else
  bad "disable --all left a login-walled style painted"
fi
cp "$tmp/config-before-all.json" "$XDG_CONFIG_HOME/omaweb/config.json"
"$bin" apply --no-fetch --quiet >/dev/null 2>&1 || true

note "browser wiring"
cat >"$XDG_CONFIG_HOME/chromium-flags.conf" <<EOF
--load-extension=/opt/other-extension
EOF
if "$bin" browser wire >/dev/null; then ok "wire asset extension"; else bad "wire asset extension"; fi
if grep -qF "$asset" "$XDG_CONFIG_HOME/chromium-flags.conf"; then
  ok "asset extension is wired"
else
  bad "asset extension not wired"
fi
if grep -qF "/opt/other-extension" "$XDG_CONFIG_HOME/chromium-flags.conf"; then
  ok "unrelated extension preserved"
else
  bad "unrelated extension lost"
fi

note "manual load (chrome://extensions)"
mkdir -p "$XDG_CONFIG_HOME/chromium/Default"
cat >"$XDG_CONFIG_HOME/chromium/Default/Preferences" <<JSON
{"extensions":{"settings":{"jgbhmbaknbcdkcblgalphnnhjjfbnplj":{"path":"$asset","disable_reasons":[]}}}}
JSON
if "$bin" browser status --json | python3 -c '
import json, sys
rows = json.load(sys.stdin)
c = next(r for r in rows if r["browser"] == "chromium")
sys.exit(0 if c["loaded"] and c["active"] and c["wired"] else 1)
'; then ok "profile load detected"; else bad "profile load not detected"; fi
"$bin" browser unwire >/dev/null || true
if [[ -f $XDG_STATE_HOME/omarchy/omaweb/no-wire ]]; then
  ok "unwire remembers manual mode"
else
  bad "unwire did not remember manual mode"
fi
if "$bin" browser status --json | python3 -c '
import json, sys
rows = json.load(sys.stdin)
c = next(r for r in rows if r["browser"] == "chromium")
sys.exit(0 if c["loaded"] and not c["wired"] else 1)
'; then ok "loaded by hand needs no flags"; else bad "manual load still requires flags"; fi
if "$bin" browser wire >/dev/null; then ok "wire after unwire"; else bad "wire after unwire"; fi
if [[ ! -f $XDG_STATE_HOME/omarchy/omaweb/no-wire ]]; then
  ok "wire clears manual mode"
else
  bad "wire kept manual mode"
fi

note "theme switch"
sed -i 's/^mode = "dark"/mode = "light"/; s/^background = .*/background = "#eff1f5"/; s/^foreground = .*/foreground = "#4c4f69"/' \
  "$OMAWEB_THEME_DIR/colors.toml"
if OMAWEB_THEME_NAME="omaweb-check-light" "$bin" apply --no-fetch --quiet; then ok "apply (light)"; else bad "apply (light)"; fi
python3 - "$XDG_DATA_HOME/omaweb" "$asset" "$here" <<'PY' || fail=1
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
asset = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(pathlib.Path(sys.argv[3]) / "lib"))
from omaweb import darkreader
index = json.loads((root / "index.json").read_text())

def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        raise SystemExit(1)

check(index["theme"]["mode"] == "light", "theme is light")
check((asset / "s" / "github" / "0.css").read_text().strip(),
      "enabled style still has css")
dr = darkreader.build_settings(index)
check(dr["theme"]["mode"] == 0, "darkreader: light theme -> light mode")
check(dr["theme"]["lightSchemeBackgroundColor"] == index["theme"]["roles"]["base"],
      "darkreader: light background follows the theme")
check(dr["theme"]["lightSchemeTextColor"] == index["theme"]["roles"]["text"],
      "darkreader: light text follows the theme")
check(dr["theme"]["styleSystemControls"] is False,
      "darkreader: native controls follow the light theme")
PY

note "theme override (Chroma-style)"
alt="$XDG_CONFIG_HOME/omarchy/themes/omaweb-check-alt"
mkdir -p "$alt"
cat >"$alt/colors.toml" <<'TOML'
mode = "dark"
accent = "#ebbcba"
selection = "#403d52"
muted = "#6e6a86"
background = "#191724"
dark_background = "#1f1d2e"
darker_background = "#26233a"
foreground = "#e0def4"
red = "#eb6f92"
yellow = "#f6c177"
green = "#31748f"
cyan = "#9ccfd8"
blue = "#c4a7e7"
magenta = "#ebbcba"
TOML
before_build="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["build"])' "$XDG_DATA_HOME/omaweb/index.json")"
if "$bin" theme use omaweb-check-alt --quiet; then ok "theme use"; else bad "theme use"; fi
python3 - "$XDG_DATA_HOME/omaweb" "$XDG_CONFIG_HOME/omaweb/config.json" "$before_build" "$asset" <<'PY' || fail=1
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
cfg = json.loads(pathlib.Path(sys.argv[2]).read_text())
index = json.loads((root / "index.json").read_text())
theme = json.loads((pathlib.Path(sys.argv[4]) / "theme.json").read_text())

def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        raise SystemExit(1)

check(cfg["theme"] == "omaweb-check-alt", "config pins the override")
check(index["theme"]["name"] == "omaweb-check-alt", "index names the override")
check(index["theme"]["mode"] == "dark", "override keeps its own mode")
check(theme["roles"]["base"] == "#191724", "override palette is used")
check(index["build"] != sys.argv[3], "override invalidates the build cache")
PY
if "$bin" theme show --json | python3 -c '
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if d["name"] == "omaweb-check-alt" and d["auto"] is False and d["missing"] is False else 1)
'; then ok "theme show resolves the pin"; else bad "theme show did not resolve the pin"; fi
if "$bin" theme use definitely-not-a-theme --quiet 2>/dev/null; then
  bad "unknown theme accepted"
else
  ok "unknown theme rejected"
fi
if "$bin" theme auto --quiet; then ok "theme auto"; else bad "theme auto"; fi
python3 - "$XDG_DATA_HOME/omaweb" "$XDG_CONFIG_HOME/omaweb/config.json" <<'PY' || fail=1
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
cfg = json.loads(pathlib.Path(sys.argv[2]).read_text())
index = json.loads((root / "index.json").read_text())

def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        raise SystemExit(1)

check(cfg["theme"] == "auto", "config returns to auto")
check(index["theme"]["name"] == "omaweb-check-dark", "auto follows the desktop again")
check(index["theme"]["mode"] == "light", "auto follows the desktop mode")
PY

note "portable state"
python3 - "$here" "$tmp/state-bundle.json" <<'PY' || fail=1
import json, pathlib, sys
sys.path.insert(0, sys.argv[1] + "/lib")
from omaweb import build, config, state, util

def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        raise SystemExit(1)

bundle_path = pathlib.Path(sys.argv[2])
original_cfg = config.load(create=True)
try:
    cfg = json.loads(json.dumps(original_cfg))
    cfg.setdefault("overrides", {})["state-test"] = True
    cfg.setdefault("darkreader", {})["unthemed"] = ["state-unthemed.test"]
    config.save(cfg)
    util.write_json(util.cache_dir() / "scan" / "release.json",
                    {"tag": "test", "fetchedAt": "2030-01-01T00:00:00Z"})

    bundle = state.export_bundle()
    check(bundle["config"]["overrides"]["state-test"] is True,
          "state: export carries overrides")
    check(bundle["scan"]["release"]["tag"] == "test",
          "state: export carries the scan cache")
    util.write_json(bundle_path, bundle)

    config.save(original_cfg)
    (util.cache_dir() / "scan" / "release.json").unlink()

    state.import_bundle(bundle_path)
    merged = config.load(create=True)
    check(merged["overrides"].get("state-test") is True,
          "state: import merges config overrides")
    check("state-unthemed.test" in merged["darkreader"]["unthemed"],
          "state: import merges Dark Reader opt-outs")
    check((util.cache_dir() / "scan" / "release.json").exists(),
          "state: import restores the scan cache")
finally:
    config.save(original_cfg)
    try:
        (util.cache_dir() / "scan" / "release.json").unlink()
    except OSError:
        pass
    build.apply(quiet=True, no_fetch=True)
PY

note "doctor"
if "$bin" doctor; then ok "doctor"; else bad "doctor"; fi

if (( fail )); then
  printf '\nomaweb check: FAILED\n'
  exit 1
fi
printf '\nomaweb check: ok\n'
