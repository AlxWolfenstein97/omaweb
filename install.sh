#!/bin/bash
#
# OmaWeb installer. Safe to re-run: rewrites what it owns, leaves the rest alone.
#
# OmaWeb compiles the Catppuccin userstyles bundle for the active Omarchy theme
# and writes the CSS into a tiny *asset extension* of its own: a manifest plus
# CSS files, with no permissions and no scripts.  The normal, signed Stylus from
# the Chrome Web Store injects an @import shim that reads those files through
# chrome-extension://, so there is no server and no network at runtime.
#
# install.sh therefore installs:
#
#   - the LESS compiler (npm, no root)
#   - the OmaWeb asset extension and its browser flag
#   - optionally, the theme-set hook that recompiles on every theme switch
#
# Flags:
#   --quiet             shell service: restore armed wiring; no prompts/packages
#   --yes, -y           non-interactive: arm the theme hook and apply
#   --with-theme-hook   arm the theme-set hook
#   --no-theme-hook     remove the theme-set hook
#   --no-pkgs           do not install lessc
#   --no-wire           do not touch browser flags; load the asset extension by hand
#   --wire              undo --no-wire and add the extension to the flags again
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin_id="io.github.alxwolfenstein97.omaweb"
quiet=0
assume_yes=0
with_theme_hook=0
no_theme_hook=0
no_pkgs=0
no_wire=0
force_wire=0
for arg in "$@"; do
  case $arg in
    --quiet) quiet=1; no_pkgs=1 ;;
    --yes|-y) assume_yes=1; with_theme_hook=1 ;;
    --with-theme-hook) with_theme_hook=1 ;;
    --no-theme-hook) no_theme_hook=1 ;;
    --no-pkgs) no_pkgs=1 ;;
    --no-wire) no_wire=1 ;;
    --wire) force_wire=1 ;;
  esac
done

note() { (( quiet )) || printf 'omaweb: %s\n' "$1"; }
warn() { printf 'omaweb: %s\n' "$1" >&2; }

config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
state="$state_home/omarchy/omaweb"
hooks="$config_home/omarchy/hooks/theme-set.d"
index="$data_home/omaweb/index.json"

mkdir -p "$state" "$hooks"

# Tombstone from uninstall: a later quiet Service run is a re-enable.
if [[ -f $state/uninstalled ]]; then
  rm -f "$state/uninstalled" 2>/dev/null || true
fi

chmod 755 "$here/bin/omaweb" "$here/omarchy/theme-set-hook" 2>/dev/null || true

# --------------------------------------------------------------- helpers
py() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$@"
  else
    "$@"
  fi
}

have_lessc() {
  python3 - "$here" <<'PY' >/dev/null 2>&1
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "lib"))
from omaweb import util
raise SystemExit(0 if util.lessc_path() else 1)
PY
}

have_assets() {
  python3 - "$here" <<'PY' >/dev/null 2>&1
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "lib"))
from omaweb import stylus
raise SystemExit(0 if stylus.web_root() else 1)
PY
}

extension_loaded() {
  python3 - "$here" <<'PY' >/dev/null 2>&1
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "lib"))
from omaweb import stylus
state = stylus.profile_extension_state()
raise SystemExit(0 if state["loaded"] and state["active"] else 1)
PY
}

have_store_stylus() {
  python3 - "$here" <<'PY' >/dev/null 2>&1
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "lib"))
from omaweb import stylus
raise SystemExit(0 if stylus.store_stylus() else 1)
PY
}

browser_field() {
  python3 - "$here" "$1" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1] + "/lib")
from omaweb import browser
report = browser.detect_report()
value = report.get(sys.argv[2])
if isinstance(value, list):
    print(" ".join(value))
elif value is None:
    print("")
else:
    print(value)
PY
}

raw() { printf '%s\n' "$1" >&2; }

firefox_nono() {
  raw ""
  raw "  +----------------------------------------------------------+"
  raw "  |  Sorry - OmaWeb does not support Firefox, Zen or         |"
  raw "  |  LibreWolf.                                              |"
  raw "  +----------------------------------------------------------+"
  raw ""
  raw "  OmaWeb is an adapter for Stylus: a signed store extension plus"
  raw "  a tiny unpacked helper that feeds it CSS. Firefox-family builds"
  raw "  refuse unsigned extensions, and Omarchy does not theme their"
  raw "  outer shell either, so there is no automatic half to build on."
  raw "  OmaWeb extends Omarchy's theming; it does not reimplement it."
  raw ""
  raw "  Use Chromium, Chrome, Brave, Brave Origin, Edge or Vivaldi."
  raw ""
}

chromium_browsers=()
firefox_browsers=()
wired_browsers=()
loaded_browsers=()
default_family=""
default_entry=""

detect_browsers() {
  chromium_browsers=($(browser_field installed))
  firefox_browsers=($(browser_field firefox))
  wired_browsers=($(browser_field wired))
  loaded_browsers=($(browser_field loaded))
  default_family="$(browser_field defaultFamily)"
  default_entry="$(browser_field defaultEntry)"
}

index_current() {
  [[ -f $index ]] || return 1
  python3 - "$index" "$here" <<'PY' >/dev/null 2>&1
import json, os, sys
sys.path.insert(0, os.path.join(sys.argv[2], "lib"))
from omaweb import stylus
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if data.get("version") == 2
                 and (data.get("extension") or {}).get("id") == stylus.ASSET_ID else 1)
PY
}

# --------------------------------------------------------------- browser detection
if (( ! quiet )); then
  detect_browsers
  if [[ $default_family == firefox ]]; then
    firefox_nono
    if [[ ${#chromium_browsers[@]} -eq 0 ]]; then
      raw "omaweb: refusing to continue - install a Chromium-family browser first."
      exit 1
    fi
  fi
  if [[ ${#chromium_browsers[@]} -eq 0 ]]; then
    warn "no Chromium-family browser detected; OmaWeb will still build, but there is nothing to wire"
  fi
fi

# --------------------------------------------------------------- LESS compiler
install_lessc() {
  have_lessc && return 0
  (( no_pkgs )) && return 1
  if ! command -v npm >/dev/null 2>&1; then
    warn "lessc missing and npm not found — install nodejs/npm, then re-run"
    return 1
  fi
  note "installing the LESS compiler (npm, user prefix)"
  npm install --silent --no-audit --no-fund --prefix "$data_home/omaweb/less" less@4 >/dev/null 2>&1 \
    || { warn "npm install of less failed"; return 1; }
  have_lessc
}

# --------------------------------------------------------------- asset extension
ensure_assets() {
  have_assets && return 0
  py "$here/bin/omaweb" apply --quiet --no-fetch >/dev/null 2>&1 || true
  have_assets
}

ensure_store_stylus() {
  have_store_stylus && return 0
  warn "Stylus is not installed — install it once from:"
  warn "  https://chromewebstore.google.com/detail/stylus/clngdbkpkpeebahjckkjfobafhncgmne"
  return 1
}

wire_browser() {
  have_assets || return 0
  if (( no_wire )); then
    py "$here/bin/omaweb" browser unwire >/dev/null 2>&1 || true
    note "leaving browser flags alone; load this folder by hand instead:"
    note "  $data_home/omaweb/extension"
    note "  chrome://extensions → Developer mode → Load unpacked"
    return 0
  fi
  if (( ! force_wire )) && [[ -f $state/no-wire ]]; then
    note "browser flags left alone (manual load); use --wire to switch back"
    return 0
  fi
  if (( ! force_wire )) && extension_loaded; then
    note "asset extension is already loaded in the browser profile; flags unchanged"
    return 0
  fi
  if ! py "$here/bin/omaweb" browser wire >/dev/null 2>&1; then
    warn "could not wire browser flags — run: $here/bin/omaweb browser wire"
    return 1
  fi
  note "browser flags point at the OmaWeb asset extension"
}

link_cli() {
  local bindir="$HOME/.local/bin"
  mkdir -p "$bindir"
  if [[ -e $bindir/omaweb && ! -L $bindir/omaweb ]]; then
    warn "not replacing existing $bindir/omaweb"
    return 0
  fi
  ln -sfn "$here/bin/omaweb" "$bindir/omaweb"
}

# --------------------------------------------------------------- theme hook
arm_theme_hook=0
[[ -f $hooks/omaweb ]] && arm_theme_hook=1
[[ -f $state/armed-theme-hook ]] && arm_theme_hook=1
(( with_theme_hook )) && arm_theme_hook=1
(( no_theme_hook )) && arm_theme_hook=0

if (( ! quiet && ! assume_yes && ! no_theme_hook && ! with_theme_hook )); then
  if (( ! arm_theme_hook )); then
    printf '%s' "omaweb: install theme-set auto-sync hook? [Y/n] "
    read -r _ans || _ans=
    case ${_ans:-Y} in
      [nN]|[nN][oO]) arm_theme_hook=0 ;;
      *) arm_theme_hook=1 ;;
    esac
  fi
fi

if (( arm_theme_hook )); then
  install -m 755 "$here/omarchy/theme-set-hook" "$hooks/omaweb"
  touch "$state/armed-theme-hook"
  note "hook: $hooks/omaweb"
else
  rm -f "$hooks/omaweb"
  rm -f "$state/armed-theme-hook"
  note "theme-set hook not armed — run: $here/install.sh --with-theme-hook"
fi

# --------------------------------------------------------------- apply
theme_name() {
  cat "$state_home/omarchy/current/theme.name" 2>/dev/null || true
}

state_theme() {
  python3 - "$state/state.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("theme") or "")
except Exception:
    pass
PY
}

run_apply() {
  py "$here/bin/omaweb" apply "$@"
}

need_apply=0
index_current || need_apply=1
[[ "$(state_theme)" != "$(theme_name)" ]] && need_apply=1

if (( need_apply )); then
  if (( quiet )); then
    # A first run may need the bundle; a later one must not block a shell start
    # on the network.
    if [[ -f $index ]]; then
      run_apply --quiet --no-fetch >/dev/null 2>&1 || warn "apply failed — see: omaweb doctor"
    else
      run_apply --quiet >/dev/null 2>&1 || warn "first apply failed — run: omaweb apply"
    fi
  else
    install_lessc || warn "continuing without lessc; run: omaweb doctor"
    run_apply || warn "apply failed — run: omaweb doctor"
  fi
elif (( ! quiet )); then
  note "build is current for $(theme_name)"
fi

ensure_assets
ensure_store_stylus || true
wire_browser
link_cli

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin enable "$plugin_id" >/dev/null 2>&1 || true
fi

if (( ! quiet )); then
  wired_browsers=($(browser_field wired))
  loaded_browsers=($(browser_field loaded))
  note "done"
  printf '%s\n' ""
  printf '%s\n' "  extensions (both from the Chrome Web Store):"
  printf '%s\n' "    Stylus:      https://chromewebstore.google.com/detail/stylus/clngdbkpkpeebahjckkjfobafhncgmne"
  printf '%s\n' "    Dark Reader: https://chromewebstore.google.com/detail/dark-reader/eimadpbcbfnmbkopoojfekhnkhdbieeh"
  printf '%s\n' ""
  if [[ ${#wired_browsers[@]} -gt 0 ]]; then
    printf '%s\n' "  browser flags: ${wired_browsers[*]}"
  elif [[ ${#loaded_browsers[@]} -gt 0 ]]; then
    printf '%s\n' "  browser flags: none needed — loaded by hand in ${loaded_browsers[*]}"
  elif [[ ${#chromium_browsers[@]} -gt 0 ]]; then
    printf '%s\n' "  browser flags: none — run: omaweb browser wire"
  fi
  if [[ ${#firefox_browsers[@]} -gt 0 ]]; then
    printf '%s\n' "  firefox-family: ${firefox_browsers[*]} (not supported)"
  fi
  printf '%s\n' ""
  printf '%s\n' "  next: import the Stylus shims once:"
  printf '%s\n' "        omaweb stylus import --open"
  printf '%s\n' "  optional: omaweb darkreader export   # import the JSON in Dark Reader"
  printf '%s\n' ""
  printf '%s\n' "  check for a newer Catppuccin bundle: omaweb scan"
  printf '%s\n' "  then refresh it:                    omaweb fetch && omaweb apply"
  printf '%s\n' "  check: omaweb doctor"
  printf '%s\n' "  site:  omaweb status"
fi
exit 0
