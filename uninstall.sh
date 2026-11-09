#!/bin/bash
# OmaWeb uninstaller: unwire the OmaWeb asset extension from browser flags,
# remove the theme hook and drop generated data.  Stylus (the store copy) is
# left installed and its shim styles are left in place — remove them from the
# Stylus manager (they are named "OmaWeb · …").
#
# Flags:
#   --yes, -y   also remove config and ask Omarchy to remove the plugin
set -euo pipefail

assume_yes=0
for arg in "$@"; do
  case $arg in --yes|-y) assume_yes=1 ;; esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin_id="io.github.alxwolfenstein97.omaweb"

config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
cache_home="${XDG_CACHE_HOME:-$HOME/.cache}"
state="$state_home/omarchy/omaweb"

note() { printf 'omaweb: %s\n' "$1"; }

# Tombstone first so a shell-start Service cannot resurrect anything.
mkdir -p "$state"
touch "$state/uninstalled"

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin disable "$plugin_id" >/dev/null 2>&1 || true
fi

# Remove our --load-extension entry.
if [[ -x $here/bin/omaweb ]]; then
  python3 "$here/bin/omaweb" browser unwire >/dev/null 2>&1 || true
fi

rm -f "$config_home/omarchy/hooks/theme-set.d/omaweb"
rm -f "$state/armed-theme-hook"

# Remove the CLI symlink only when it points at this plugin.
cli_link="$HOME/.local/bin/omaweb"
if [[ -L $cli_link ]] && \
   [[ "$(readlink -f "$cli_link" 2>/dev/null)" == "$(readlink -f "$here/bin/omaweb" 2>/dev/null)" ]]; then
  rm -f "$cli_link"
fi

rm -rf "$data_home/omaweb"
rm -rf "$cache_home/omarchy/omaweb"
find "$state" -mindepth 1 ! -name uninstalled -delete 2>/dev/null || true
touch "$state/uninstalled"

if (( assume_yes )); then
  rm -rf "$config_home/omaweb"
fi

note "browser wiring, theme hook and generated data removed"
printf '%s\n' "omaweb: Stylus shim styles are still installed; remove the ones named" \
              "        'OmaWeb · …' in the Stylus manager if you no longer want them."

if (( assume_yes )); then
  if command -v omarchy >/dev/null 2>&1; then
    cd "${HOME:-/}" || cd /
    omarchy plugin remove "$plugin_id" --yes \
      || note "plugin remove failed — try: omarchy plugin remove $plugin_id --yes"
  else
    note "omarchy CLI missing — delete by hand: $here"
  fi
else
  note "plugin files remain at $here"
  note "  omarchy plugin remove $plugin_id"
fi

exit 0
