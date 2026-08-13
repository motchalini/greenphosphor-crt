#!/usr/bin/env bash
# Rebuild the shipped CSS in themes/ from src/overrides/.
# Each target keeps its (vendored) base untouched; the override block is
# delimited by BEGIN/END markers and regenerated in place on every run.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

BEGIN='/* >>> GreenPhosphor-CRT overrides — generated from src/overrides; edit there and re-run build.sh >>> */'
END='/* <<< GreenPhosphor-CRT overrides <<< */'

append() {
  local src=$1; shift
  local t
  for t in "$@"; do
    awk -v b="$BEGIN" -v e="$END" '
      $0 == b { skip = 1; next }
      $0 == e { skip = 0; next }
      skip    { next }
      /^$/    { blanks++; next }
      { for (i = 0; i < blanks; i++) print ""; blanks = 0; print }
    ' "$t" > "$t.tmp" && mv "$t.tmp" "$t"
    { echo; echo "$BEGIN"; cat "$src"; echo "$END"; } >> "$t"
    echo "built: $t"
  done
}

T=themes/GreenPhosphor-CRT
append src/overrides/gnome-shell-overrides.css "$T/gnome-shell/gnome-shell.css"
append src/overrides/gtk-3.0-overrides.css     "$T/gtk-3.0/gtk.css" "$T/gtk-3.0/gtk-dark.css"
append src/overrides/gtk-4.0-overrides.css     "$T/gtk-4.0/gtk.css" "$T/gtk-4.0/gtk-dark.css"
