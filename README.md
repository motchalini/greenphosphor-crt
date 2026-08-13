# GreenPhosphor-CRT

A green-phosphor CRT theme for the GNOME desktop — deep green-black grounds, muted phosphor text, subtle glow. Dark, quiet, and retro.

## Screenshots

![Desktop](docs/screenshots/desktop.png)
![Apps](docs/screenshots/apps.png)
![Terminal](docs/screenshots/terminal.png)

## What's covered

- GNOME Shell (panel, popups, OSD, Alt-Tab, notifications, overview, lock screen clock)
- GTK3 apps
- GTK4 & libadwaita apps (via `--libadwaita`)
- Tilix color scheme

## Palette

| Role       | Color                        |
| ---------- | ----------------------------- |
| Ground     | `#080B08`                     |
| Window     | `#0C120D`                     |
| Surface    | `#0B140D`                     |
| Phosphor   | `#28A038`                     |
| Bright     | `#33B84A`                     |
| Highlight  | `#3DBF52`                     |
| Selection  | `#28A038` on `#080B08`        |

## Requirements

- GNOME 47/48
- User Themes extension (for the shell theme)
- git

## Install

```sh
git clone https://github.com/motchalini/greenphosphor-crt.git
cd greenphosphor-crt
./install.sh --libadwaita --tilix
```

Then apply the theme:

```sh
gsettings set org.gnome.desktop.interface gtk-theme 'GreenPhosphor-CRT'
gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'
gsettings set org.gnome.shell.extensions.user-theme name 'GreenPhosphor-CRT'
```

A re-login is recommended for the login screen and shell theme to fully apply.

## Uninstall

```sh
./install.sh --uninstall
```

## Goes well with

- Tela green (dark) icon theme
- A monospace font you like (the author uses Cica)
- GNOME accent color "green"
- Dark Style

## Development

`src/overrides/` holds the source CSS. `build.sh` regenerates the built CSS under `themes/` by appending these overrides as marked sections.

## Credits & License

Based on [Graphite GTK theme](https://github.com/vinceliuice/Graphite-gtk-theme) by vinceliuice (GPL-3.0). This theme is also licensed under GPL-3.0. The Tilix color scheme is original work.
