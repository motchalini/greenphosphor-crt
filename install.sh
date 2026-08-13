#!/usr/bin/env bash
# install.sh — install/uninstall GreenPhosphor-CRT for the current user.
# No sudo required. Safe to re-run (idempotent).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
THEME_NAME="GreenPhosphor-CRT"
SRC_THEME_DIR="${SCRIPT_DIR}/themes/${THEME_NAME}"
DEST_THEME_DIR="${HOME}/.themes/${THEME_NAME}"

GTK4_CONFIG_DIR="${HOME}/.config/gtk-4.0"
GTK4_CSS_LINK="${GTK4_CONFIG_DIR}/gtk.css"
GTK4_ASSETS_LINK="${GTK4_CONFIG_DIR}/assets"
GTK4_CSS_BACKUP="${GTK4_CONFIG_DIR}/gtk.css.pre-greenphosphor"

TILIX_SCHEME_SRC="${SCRIPT_DIR}/tilix/green-phosphor.json"
TILIX_SCHEME_DIR="${HOME}/.config/tilix/schemes"

DO_LIBADWAITA=0
DO_TILIX=0
DO_UNINSTALL=0

usage() {
  cat <<'EOF'
Usage: install.sh [OPTIONS]

Install (or uninstall) GreenPhosphor-CRT for the current user.
No sudo required. Safe to run multiple times.

Options:
  --libadwaita   Also link ~/.config/gtk-4.0/gtk.css and assets so
                 libadwaita apps pick up the theme.
  --tilix        Copy the Tilix color scheme into
                 ~/.config/tilix/schemes/.
  --uninstall    Remove the installed theme and any links/backups
                 created by this script.
  -h, --help     Show this help and exit.

Flags may be combined, e.g.:
  ./install.sh --libadwaita --tilix

With no options, installs themes/GreenPhosphor-CRT to
~/.themes/GreenPhosphor-CRT only.
EOF
}

print_apply_instructions() {
  cat <<EOF

To apply the theme:
  gsettings set org.gnome.desktop.interface gtk-theme '${THEME_NAME}'
  gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'
  gsettings set org.gnome.shell.extensions.user-theme name '${THEME_NAME}'

(The last command requires the "User Themes" GNOME Shell extension.)
EOF
}

install_theme() {
  if [ ! -d "${SRC_THEME_DIR}" ]; then
    echo "error: ${SRC_THEME_DIR} not found" >&2
    exit 1
  fi

  mkdir -p "${HOME}/.themes"
  rm -rf "${DEST_THEME_DIR}"
  cp -a "${SRC_THEME_DIR}" "${DEST_THEME_DIR}"

  echo "Installed ${THEME_NAME} to ${DEST_THEME_DIR}"
  print_apply_instructions
}

install_libadwaita() {
  mkdir -p "${GTK4_CONFIG_DIR}"

  if [ -e "${GTK4_CSS_LINK}" ] || [ -L "${GTK4_CSS_LINK}" ]; then
    if [ -L "${GTK4_CSS_LINK}" ]; then
      rm -f "${GTK4_CSS_LINK}"
    else
      mv "${GTK4_CSS_LINK}" "${GTK4_CSS_BACKUP}"
      echo "Backed up existing ${GTK4_CSS_LINK} to ${GTK4_CSS_BACKUP}"
    fi
  fi
  ln -s "${DEST_THEME_DIR}/gtk-4.0/gtk.css" "${GTK4_CSS_LINK}"

  if [ -L "${GTK4_ASSETS_LINK}" ]; then
    rm -f "${GTK4_ASSETS_LINK}"
  elif [ -e "${GTK4_ASSETS_LINK}" ]; then
    echo "warning: ${GTK4_ASSETS_LINK} exists and is not a symlink; leaving it in place" >&2
  fi
  if [ ! -e "${GTK4_ASSETS_LINK}" ]; then
    ln -s "${DEST_THEME_DIR}/gtk-4.0/assets" "${GTK4_ASSETS_LINK}"
  fi

  echo "Linked ${GTK4_CSS_LINK} and ${GTK4_ASSETS_LINK} for libadwaita apps"
}

install_tilix() {
  if [ ! -f "${TILIX_SCHEME_SRC}" ]; then
    echo "error: ${TILIX_SCHEME_SRC} not found" >&2
    exit 1
  fi
  mkdir -p "${TILIX_SCHEME_DIR}"
  cp -a "${TILIX_SCHEME_SRC}" "${TILIX_SCHEME_DIR}/"
  echo "Installed Tilix color scheme to ${TILIX_SCHEME_DIR}/$(basename "${TILIX_SCHEME_SRC}")"
}

uninstall_all() {
  if [ -d "${DEST_THEME_DIR}" ] || [ -L "${DEST_THEME_DIR}" ]; then
    rm -rf "${DEST_THEME_DIR}"
    echo "Removed ${DEST_THEME_DIR}"
  else
    echo "${DEST_THEME_DIR} not present, nothing to remove"
  fi

  if [ -L "${GTK4_CSS_LINK}" ]; then
    target="$(readlink "${GTK4_CSS_LINK}")"
    case "${target}" in
      *"/${THEME_NAME}/"*)
        rm -f "${GTK4_CSS_LINK}"
        echo "Removed ${GTK4_CSS_LINK}"
        if [ -f "${GTK4_CSS_BACKUP}" ]; then
          mv "${GTK4_CSS_BACKUP}" "${GTK4_CSS_LINK}"
          echo "Restored ${GTK4_CSS_LINK} from backup"
        fi
        ;;
      *)
        echo "note: ${GTK4_CSS_LINK} does not point into ${THEME_NAME}, leaving it alone"
        ;;
    esac
  fi

  if [ -L "${GTK4_ASSETS_LINK}" ]; then
    target="$(readlink "${GTK4_ASSETS_LINK}")"
    case "${target}" in
      *"/${THEME_NAME}/"*)
        rm -f "${GTK4_ASSETS_LINK}"
        echo "Removed ${GTK4_ASSETS_LINK}"
        ;;
      *)
        echo "note: ${GTK4_ASSETS_LINK} does not point into ${THEME_NAME}, leaving it alone"
        ;;
    esac
  fi

  cat <<EOF

To revert your theme settings, e.g.:
  gsettings reset org.gnome.desktop.interface gtk-theme
  gsettings reset org.gnome.desktop.interface color-scheme
  gsettings reset org.gnome.shell.extensions.user-theme name
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --libadwaita)
      DO_LIBADWAITA=1
      shift
      ;;
    --tilix)
      DO_TILIX=1
      shift
      ;;
    --uninstall)
      DO_UNINSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option '$1'" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ "${DO_UNINSTALL}" -eq 1 ]; then
  uninstall_all
  exit 0
fi

install_theme

if [ "${DO_LIBADWAITA}" -eq 1 ]; then
  install_libadwaita
fi

if [ "${DO_TILIX}" -eq 1 ]; then
  install_tilix
fi
