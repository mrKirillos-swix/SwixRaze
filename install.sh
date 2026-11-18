#!/usr/bin/env bash
# SwixRaze — установка в домашнюю папку (без sudo).
#   ./install.sh          — спросит про каждую необязательную часть
#   ./install.sh --all    — поставить всё без вопросов
#   ./install.sh --minimal — только приложение, расширение и палитру
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_UUID="swixraze-widgets@mrKirillos-swix"
APP_ID="io.github.mrKirillos_swix.SwixRaze"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
PICTURES="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")"
# если обои уже лежат в ~/Pictures/Wallpapers (а «Изображения» — другая папка), используем её, как и приложение
[[ ! -d "$PICTURES/Wallpapers" && -d "$HOME/Pictures/Wallpapers" ]] && PICTURES="$HOME/Pictures"
MODE="ask"
case "${1:-}" in --all) MODE="all" ;; --minimal) MODE="minimal" ;; "") ;; *) echo "Неизвестный параметр: $1"; exit 1 ;; esac

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
want() {  # want "вопрос" -> 0 если ставить
    [[ $MODE == all ]] && return 0
    [[ $MODE == minimal ]] && return 1
    read -r -p "$1 [y/N] " a; [[ ${a,,} == y* || ${a,,} == д* ]]
}

[[ $EUID -eq 0 ]] && { echo "Не запускайте от root: SwixRaze ставится в домашнюю папку."; exit 1; }

# ---------------------------------------------------------------- проверки
say "Проверяю зависимости"
missing=()
python3 - <<'EOF' || missing+=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gweather-4.0)
import gi
for ns, v in (("Gtk", "4.0"), ("Adw", "1"), ("GWeather", "4.0")):
    gi.require_version(ns, v)
from gi.repository import Gtk, Adw, GWeather
EOF
for cmd in gnome-shell gnome-extensions dconf git curl fc-cache; do command -v "$cmd" >/dev/null || missing+=("$cmd"); done
if ((${#missing[@]})); then
    warn "Не хватает: ${missing[*]}"
    echo "На Ubuntu поставьте их так:"
    echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gweather-4.0 dconf-cli git curl fontconfig"
    exit 1
fi
shell_major=$(gnome-shell --version | grep -oE '[0-9]+' | head -1)
if ((shell_major < 48)); then warn "Нужен GNOME Shell 48 или новее (у вас $shell_major)."; exit 1; fi

# ---------------------------------------------------------------- основное
say "Ставлю приложение SwixRaze"
install -Dm644 "$REPO/app/swixraze.py" "$DATA/swixraze/swixraze.py"
install -Dm644 "$REPO/data/icons/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true
mkdir -p "$DATA/applications"
sed "s|@APP@|$DATA/swixraze/swixraze.py|" "$REPO/app/$APP_ID.desktop" > "$DATA/applications/$APP_ID.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true

say "Ставлю расширение $EXT_UUID"
rm -rf "${DATA:?}/gnome-shell/extensions/$EXT_UUID"
mkdir -p "$DATA/gnome-shell/extensions"
cp -r "$REPO/extension/$EXT_UUID" "$DATA/gnome-shell/extensions/"

say "Палитра терминала Ptyxis"
install -Dm644 "$REPO/data/terminal/SwixRaze.palette" "$DATA/org.gnome.Ptyxis/palettes/SwixRaze.palette"

if [[ ! -f "$CONF/swixraze/config.json" ]]; then
    install -Dm644 /dev/stdin "$CONF/swixraze/config.json" <<'EOF'
{
  "preset": "swixraze",
  "accent": "#cba6f7",
  "widgets": { "player": true, "devices": true, "system": false },
  "corner": "bottom-left",
  "lockscreen": true
}
EOF
fi

# ---------------------------------------------------------------- сторонние расширения (с extensions.gnome.org)
install_remote() {  # GNOME сам покажет окно «Установить расширение?»
    local uuid=$1
    if gnome-extensions info "$uuid" >/dev/null 2>&1; then echo "  $uuid — уже есть"; return; fi
    echo "  $uuid — подтвердите установку в появившемся окне"
    gdbus call --session --dest org.gnome.Shell.Extensions --object-path /org/gnome/Shell/Extensions \
        --method org.gnome.Shell.Extensions.InstallRemoteExtension "$uuid" >/dev/null 2>&1 || true
}
if want "Поставить Desktop Widgets (нужен для часов и погоды)?"; then
    install_remote azclock@azclock.gitlab.com
fi
if want "Поставить расширения для внешнего вида: Blur my Shell, Just Perfection, Space Bar, Rounded Window Corners?"; then
    for u in blur-my-shell@aunetx just-perfection-desktop@just-perfection space-bar@luchrioh rounded-window-corners@fxgn; do
        install_remote "$u"
    done
fi
if gnome-extensions info azclock@azclock.gitlab.com >/dev/null 2>&1; then
    python3 "$REPO/tools/azclock_widgets.py" install
fi

# ---------------------------------------------------------------- шрифты (лицензия OFL, Google Fonts / Nerd Fonts)
if want "Скачать шрифты для виджетов (Inter, Manrope, Montserrat, Onest, Rubik, Comfortaa, Unbounded, JetBrains Mono Nerd)?"; then
    fdir="$DATA/fonts/swixraze"; mkdir -p "$fdir"
    for f in inter manrope montserrat onest rubik comfortaa unbounded; do
        curl -fsSL "https://api.github.com/repos/google/fonts/contents/ofl/$f" \
          | python3 -c 'import json,sys; [print(x["download_url"]) for x in json.load(sys.stdin) if x["name"].endswith(".ttf")]' \
          | while read -r url; do
                name=$(python3 -c 'import sys,urllib.parse; print(urllib.parse.unquote(sys.argv[1].rsplit("/",1)[1]))' "$url")
                curl -fsSL -o "$fdir/$name" "$url"
            done
    done
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/jb.tar.xz" https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.tar.xz
    tar -xf "$tmp/jb.tar.xz" -C "$fdir"; rm -rf "$tmp"
    fc-cache -f "$DATA/fonts" >/dev/null
fi

# ---------------------------------------------------------------- обои
if want "Скачать коллекцию обоев (~360 МБ, github.com/ilyamiro/shell-wallpapers)? Темы используют их."; then
    mkdir -p "$PICTURES/Wallpapers"
    if [[ -d "$PICTURES/Wallpapers/shell-wallpapers/.git" ]]; then
        git -C "$PICTURES/Wallpapers/shell-wallpapers" pull -q --ff-only || true
    else
        git clone -q --depth 1 https://github.com/ilyamiro/shell-wallpapers.git "$PICTURES/Wallpapers/shell-wallpapers"
    fi
fi

# ---------------------------------------------------------------- иконки (копируются готовые папки, без чужих скриптов)
if want "Скачать наборы иконок Kora, Candy, Numix Circle, Zafiro (~200 МБ)?"; then
    idir="$DATA/icons"; tmp=$(mktemp -d); mkdir -p "$idir"
    git clone -q --depth 1 https://github.com/bikass/kora.git "$tmp/kora"
    git clone -q --depth 1 https://github.com/EliverLara/candy-icons.git "$tmp/candy"
    git clone -q --depth 1 https://github.com/numixproject/numix-icon-theme.git "$tmp/numix"
    git clone -q --depth 1 https://github.com/numixproject/numix-icon-theme-circle.git "$tmp/numix-circle"
    git clone -q --depth 1 https://github.com/zayronxio/Zafiro-icons.git "$tmp/zafiro"
    rm -rf "$tmp/candy/.git" "$tmp/candy/preview"
    for pair in "kora/kora:kora" "candy:candy-icons" "numix/Numix:Numix" "numix-circle/Numix-Circle:Numix-Circle" \
                "zafiro/Dark:Zafiro-icons-Dark"; do
        src=${pair%%:*}; dst=${pair##*:}
        rm -rf "${idir:?}/$dst"; cp -a "$tmp/$src" "$idir/$dst"
        gtk-update-icon-cache -q -f -t "$idir/$dst" 2>/dev/null || true
    done
    rm -rf "$tmp"
fi

# ---------------------------------------------------------------- включение
gnome-extensions enable "$EXT_UUID" 2>/dev/null || \
    python3 - "$EXT_UUID" <<'EOF'
import sys
from gi.repository import Gio
s = Gio.Settings.new("org.gnome.shell")
lst = s.get_strv("enabled-extensions")
if sys.argv[1] not in lst:
    s.set_strv("enabled-extensions", lst + [sys.argv[1]])
EOF

say "Готово!"
echo "1. Выйдите из системы и войдите снова — GNOME загрузит расширение только при входе."
echo "2. Откройте приложение «SwixRaze» в меню приложений и выберите тему."
