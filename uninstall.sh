#!/usr/bin/env bash
# SwixRaze — удаление. Сторонние расширения, шрифты, обои и иконки не трогает (они могут быть нужны и без SwixRaze).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_UUID="swixraze-widgets@mrKirillos-swix"
APP_ID="io.github.mrKirillos_swix.SwixRaze"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"

gnome-extensions disable "$EXT_UUID" 2>/dev/null || true
rm -rf "${DATA:?}/gnome-shell/extensions/$EXT_UUID" "${DATA:?}/swixraze"
rm -f "$DATA/applications/$APP_ID.desktop" "$DATA/org.gnome.Ptyxis/palettes/SwixRaze.palette" \
      "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
update-desktop-database "$DATA/applications" 2>/dev/null || true

if gnome-extensions info azclock@azclock.gitlab.com >/dev/null 2>&1; then
    python3 "$REPO/tools/azclock_widgets.py" uninstall
fi

read -r -p "Удалить и настройки SwixRaze ($CONF/swixraze)? [y/N] " a
[[ ${a,,} == y* || ${a,,} == д* ]] && rm -rf "${CONF:?}/swixraze" "${XDG_CACHE_HOME:-$HOME/.cache}/swixraze"

echo "SwixRaze удалён. Чтобы GNOME выгрузил расширение, выйдите из системы и войдите снова."
echo "Не удалялось: Desktop Widgets, Blur my Shell, Just Perfection, Space Bar, Rounded Window Corners,"
echo "шрифты в $DATA/fonts/swixraze, иконки в $DATA/icons и обои в папке «Изображения/Wallpapers»."
