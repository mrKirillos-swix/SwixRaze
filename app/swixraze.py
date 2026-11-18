#!/usr/bin/env python3
"""SwixRaze («Оформление») — темы, обои, цвета, шрифты, иконки и виджеты рабочего стола GNOME.

«Смешай стиль. Снеси скучное.»

Всё, что здесь меняется, — обычные настройки GNOME (gsettings) и файл
~/.config/swixraze/config.json, который читает расширение SwixRaze Widgets.
"""
import json
import hashlib
import re
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GWeather", "4.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, GWeather  # noqa: E402

HOME = Path.home()
# Папка обоев: «Изображения/Wallpapers» по XDG; если обои уже лежат в ~/Pictures/Wallpapers — берём её.
_XDG_PICTURES = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or HOME / "Pictures")
WALL_DIR = next((d for d in (_XDG_PICTURES / "Wallpapers", HOME / "Pictures" / "Wallpapers") if d.is_dir()),
                _XDG_PICTURES / "Wallpapers")
COLLECTION = WALL_DIR / "shell-wallpapers" / "images"
CONFIG = Path(GLib.get_user_config_dir()) / "swixraze" / "config.json"
THUMBS = Path(GLib.get_user_cache_dir()) / "swixraze" / "thumbs"
EXT_DIRS = [HOME / ".local/share/gnome-shell/extensions", Path("/usr/share/gnome-shell/extensions")]
EXT_UUID = "swixraze-widgets@mrKirillos-swix"
# Постоянные ID виджетов Desktop Widgets, которые создаёт install.sh (одинаковые на любом компьютере).
CLOCK_WIDGET = "60fd8e73-da66-40e4-a90e-367b4d9bf1da"
WEATHER_WIDGET = "57d9c26a-2348-4ddf-8955-4a44cd164546"
WEATHER_ELEMENT = "ec3a66f2-5507-4684-b546-b6781cd6c06a"
TIME_ELEMENT = "10c19e0c-106a-446a-8519-59dac132e1ec"
DATE_ELEMENT = "752baec5-840a-4221-a4dd-5b6189626890"
CARDS_CSS = EXT_DIRS[0] / EXT_UUID / "stylesheet.css"


def ext_schemas(ext_uuid):
    """Папка со схемами расширения: из домашней папки или системная (None, если нет)."""
    for base in EXT_DIRS:
        d = base / ext_uuid / "schemas"
        if (d / "gschemas.compiled").is_file():
            return d
    return None

# Виджеты на схеме экрана: ключ, подпись, примерный размер (логические px), точка привязки.
LAYOUT_ITEMS = [
    ("clock", "Часы", (360, 110), "top-center"),
    ("weather", "Погода", (140, 64), "bottom-right"),
    ("system", "Система", (320, 150), "top-left"),
    ("player", "Плеер", (320, 80), "top-left"),
    ("devices", "Устройства", (320, 122), "top-left"),
]
DEFAULT_CLOCK_POS = (0.5, 0.10)
DEFAULT_WEATHER_POS = (0.985, 0.86)

# Иконки для предпросмотра стилей: первое найденное имя из каждого списка.
ICON_SAMPLES = [["firefox"], ["org.gnome.Nautilus", "system-file-manager"], ["org.gnome.Settings", "preferences-system"],
                ["telegram", "org.telegram.desktop"], ["org.gnome.Ptyxis", "utilities-terminal"], ["steam"],
                ["discord"], ["code", "visual-studio-code", "com.visualstudio.code"],
                ["thunderbird"], ["org.gnome.TextEditor", "accessories-text-editor"],
                ["org.gnome.Calculator", "accessories-calculator"], ["libreoffice-writer"],
                ["org.gnome.clocks"], ["folder"], ["user-home"], ["user-trash"]]
ICON_SKIP = {"hicolor", "HighContrast", "default", "locolor"}


def list_icon_themes():
    """Темы иконок (не курсоров) из системных и домашних папок: [(id, имя)]."""
    found = {}
    for base in (Path("/usr/share/icons"), HOME / ".local/share/icons", HOME / ".icons"):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            idx = d / "index.theme"
            if d.name in ICON_SKIP or not idx.is_file():
                continue
            # цветовые копии Yaru не показываем отдельно
            if d.name.startswith("Yaru-"):
                continue
            text = idx.read_text(errors="ignore")
            if "\nDirectories=" not in text and not text.startswith("Directories="):
                continue
            name = next((l.split("=", 1)[1].strip() for l in text.splitlines() if l.startswith("Name=")), d.name)
            found[d.name] = name
    return sorted(found.items(), key=lambda kv: kv[1].lower())


# Шрифты, которые хорошо смотрятся в виджетах (все с кириллицей) — сверху списка.
FONT_FAVORITES = ["Inter", "Manrope", "Montserrat", "Onest", "Rubik", "Comfortaa", "Unbounded",
                  "Ubuntu Sans", "Ubuntu", "Lato", "Noto Sans", "JetBrainsMono Nerd Font", "Ubuntu Sans Mono"]
FONT_WEIGHTS = [(100, "Тончайший"), (200, "Очень тонкий"), (300, "Тонкий"), (400, "Обычный"),
                (500, "Средний"), (600, "Полужирный"), (700, "Жирный"), (800, "Очень жирный")]

# виджет -> (заголовок, текст для предпросмотра, размер предпросмотра)
FONT_TARGETS = [
    ("time", "Часы", "20:56:31", 40),
    ("date", "Дата под часами", "Вторник · 17 ноября", 16),
    ("weather", "Погода", "14°  Облачно", 22),
    ("cards", "Карточки: плеер, устройства, система", "Процессор  12% · Наушники 73%", 14),
]

# База городов GWeather только на латинице: переводим русский запрос.
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
EXONYMS = {
    "москва": "moscow", "санкт-петербург": "saint petersburg", "питер": "saint petersburg",
    "петербург": "saint petersburg", "екатеринбург": "yekaterinburg", "нижний новгород": "nizhny novgorod",
    "киев": "kyiv", "київ": "kyiv", "харьков": "kharkiv", "одесса": "odesa", "львов": "lviv",
    "алматы": "almaty", "астана": "astana", "ташкент": "tashkent", "тбилиси": "tbilisi", "ереван": "yerevan",
    "варшава": "warsaw", "прага": "prague", "вена": "vienna", "рим": "rome", "мюнхен": "munich",
    "кёльн": "cologne", "кельн": "cologne", "лиссабон": "lisbon", "афины": "athens", "белград": "belgrade",
    "стамбул": "istanbul", "нью-йорк": "new york", "лондон": "london", "париж": "paris", "берлин": "berlin",
    "ярославль": "yaroslavl", "якутск": "yakutsk", "ульяновск": "ulyanovsk", "уфа": "ufa",
}


def city_query_variants(text):
    q = text.strip().lower()
    out = {q}
    for k, v in EXONYMS.items():
        if k.startswith(q) and len(q) >= 3:
            out.add(v)
    out.add("".join(TRANSLIT.get(ch, ch) for ch in q))
    out.add("".join(TRANSLIT.get(ch, ch) for ch in q).replace("iy", "y").replace("yy", "y"))
    return {v for v in out if v}


_CITIES = None


def all_cities():
    global _CITIES
    if _CITIES is None:
        found = []

        def walk(loc):
            c = loc.next_child(None)
            while c:
                if c.get_level() == GWeather.LocationLevel.CITY:
                    found.append(c)
                else:
                    walk(c)
                c = loc.next_child(c)
        walk(GWeather.Location.get_world())
        _CITIES = found
    return _CITIES


def search_cities(text, limit=6):
    variants = city_query_variants(text)
    starts, contains = [], []
    for c in all_cities():
        name = (c.get_english_name() or c.get_name() or "").lower()
        if any(name.startswith(v) for v in variants):
            starts.append(c)
        elif any(len(v) >= 4 and v in name for v in variants):
            contains.append(c)
    return (starts + contains)[:limit]
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")

ACCENTS = [  # системные акцентные цвета GNOME
    ("blue", "Синий", "#3584e4"), ("teal", "Бирюзовый", "#2190a4"),
    ("green", "Зелёный", "#3a944a"), ("yellow", "Жёлтый", "#c88800"),
    ("orange", "Оранжевый", "#ed5b00"), ("red", "Красный", "#e62d42"),
    ("pink", "Розовый", "#d56199"), ("purple", "Фиолетовый", "#9141ac"),
    ("slate", "Графитовый", "#6f8396"),
]

PRESETS = [
    dict(id="swixraze", name="SwixRaze", wall=COLLECTION / "ddg_1776968625235767653.jpg", accent="purple",
         widget="#cba6f7", palette="SwixRaze", dots=["#1c1b22", "#cba6f7", "#f5c2e7", "#a6e3a1", "#89b4fa"]),
    dict(id="night-city", name="Ночной город", wall=COLLECTION / "ddg_15_1788816512258034258.jpg", accent="blue",
         widget="#7aa2f7", palette="Tokyo Night", dots=["#1a1b26", "#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68"]),
    dict(id="catppuccin", name="Catppuccin", wall=COLLECTION / "catpuccin.jpg", accent="pink",
         widget="#f5c2e7", palette="Catppuccin Mocha", dots=["#1e1e2e", "#f5c2e7", "#cba6f7", "#89dceb", "#a6e3a1"]),
    dict(id="gruvbox", name="Gruvbox", wall=COLLECTION / "ddg_1777585756434463627.jpg", accent="orange",
         widget="#fe8019", palette="Gruvbox", dots=["#282828", "#fe8019", "#fabd2f", "#b8bb26", "#83a598"]),
    dict(id="nord", name="Nord", wall=COLLECTION / "daniel-leone-v7daTKlZzaw-unsplash.jpg", accent="teal",
         widget="#88c0d0", palette="nord", dots=["#2e3440", "#88c0d0", "#81a1c1", "#a3be8c", "#ebcb8b"]),
    dict(id="everforest", name="Everforest", wall=COLLECTION / "ddg_1785707947548579929.jpg", accent="green",
         widget="#a7c080", palette="Everforest", dots=["#2d353b", "#a7c080", "#dbbc7f", "#7fbbb3", "#e67e80"]),
    dict(id="rose-pine", name="Rosé Pine", wall=COLLECTION / "ddg_8_1787861397985667521.jpg", accent="pink",
         widget="#ebbcba", palette="Rosé Pine", dots=["#191724", "#ebbcba", "#c4a7e7", "#9ccfd8", "#f6c177"]),
    dict(id="dracula", name="Dracula", wall=COLLECTION / "ddg_1_1787483202206757186.jpg", accent="purple",
         widget="#bd93f9", palette="dracula", dots=["#282a36", "#bd93f9", "#ff79c6", "#50fa7b", "#8be9fd"]),
    dict(id="kanagawa", name="Kanagawa", wall=COLLECTION / "ddg_2_1791584273358875790.jpg", accent="blue",
         widget="#7e9cd8", palette="Kanagawa", dots=["#1f1f28", "#7e9cd8", "#957fb8", "#98bb6c", "#e6c384"]),
]

CSS = """
.card { padding: 0; border-radius: 16px; background: alpha(@card_fg_color, 0.04); }
.card:hover { background: alpha(@card_fg_color, 0.09); }
.card.active { box-shadow: inset 0 0 0 3px @accent_bg_color; }
.thumb { border-radius: 12px; }
.card-title { font-weight: bold; }
.dot { min-width: 12px; min-height: 12px; border-radius: 99px; }
.accent-btn { min-width: 36px; min-height: 36px; border-radius: 99px; padding: 0; }
.accent-btn.active { box-shadow: 0 0 0 3px @window_bg_color, 0 0 0 5px @window_fg_color; }
.page-title { font-size: 20pt; font-weight: 800; }
.layout-map { border-radius: 14px; }
.layout-item { background: alpha(@window_bg_color, 0.55); border: 2px solid @accent_bg_color; border-radius: 10px;
               font-size: 9pt; font-weight: bold; }
.layout-item.dragging { background: alpha(@accent_bg_color, 0.45); }
.layout-dock { background: alpha(black, 0.45); border-radius: 7px; }
"""


# ----------------------------------------------------------------- helpers
def schema_settings(schema_id, schema_dir=None, path=None):
    """Gio.Settings для схемы из системы или из папки расширения."""
    src = Gio.SettingsSchemaSource.get_default()
    if schema_dir:
        try:
            src = Gio.SettingsSchemaSource.new_from_directory(str(schema_dir), src, False)
        except GLib.Error:
            pass
    schema = src.lookup(schema_id, True) if src else None
    if schema is None:
        return None
    return Gio.Settings.new_full(schema, None, path)


def read_config():
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {"preset": "swixraze", "accent": "#cba6f7", "widgets": {"player": True, "devices": True},
                "corner": "bottom-left"}


def write_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    os.replace(tmp, CONFIG)


def thumb_path(src: Path):
    key = hashlib.sha1(f"{src}:{src.stat().st_mtime_ns}".encode()).hexdigest()
    return THUMBS / f"{key}.jpg"


def make_thumb(src: Path):
    """Выполняется в потоке: создаёт миниатюру 320x180, возвращает путь."""
    try:
        out = thumb_path(src)
        if not out.exists():
            THUMBS.mkdir(parents=True, exist_ok=True)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(src), 320, -1, True)
            if pb.get_has_alpha():
                pb = pb.composite_color_simple(pb.get_width(), pb.get_height(), GdkPixbuf.InterpType.BILINEAR, 255, 8, 0x1c1b22, 0x1c1b22)
            pb.savev(str(out), "jpeg", ["quality"], ["85"])
        return out
    except Exception:  # битые или экзотические файлы просто пропускаем
        return None


def list_wallpapers():
    files = []
    for folder in (WALL_DIR, COLLECTION):
        if folder.is_dir():
            files += sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT and p.is_file()
                            and "(Copy)" not in p.name)
    return files


def extension_state(uuid):
    try:
        out = subprocess.run(["gnome-extensions", "info", uuid], capture_output=True, text=True, timeout=5).stdout
        return "ACTIVE" in out
    except Exception:
        return False


# ----------------------------------------------------------------- backend
class Backend:
    def __init__(self):
        self.iface = Gio.Settings.new("org.gnome.desktop.interface")
        self.bg = Gio.Settings.new("org.gnome.desktop.background")
        self.saver = Gio.Settings.new("org.gnome.desktop.screensaver")
        self.dock = schema_settings("org.gnome.shell.extensions.dash-to-dock")
        self.ptyxis = Gio.Settings.new("org.gnome.Ptyxis") if schema_settings("org.gnome.Ptyxis") else None
        self.spacebar = schema_settings("org.gnome.shell.extensions.space-bar.appearance",
                                        ext_schemas("space-bar@luchrioh"))
        self.azclock = schema_settings("org.gnome.shell.extensions.azclock",
                                       ext_schemas("azclock@azclock.gitlab.com"))

    # обои
    def wallpaper(self):
        return self.bg.get_string("picture-uri-dark") or self.bg.get_string("picture-uri")

    def set_wallpaper(self, path: Path):
        uri = path.as_uri()
        for key in ("picture-uri", "picture-uri-dark"):
            self.bg.set_string(key, uri)
        self.bg.set_string("picture-options", "zoom")
        self.saver.set_string("picture-uri", uri)

    # цвета
    def set_accent(self, name):
        self.iface.set_string("accent-color", name)

    def set_dark(self, dark):
        self.iface.set_string("color-scheme", "prefer-dark" if dark else "default")
        self.iface.set_string("gtk-theme", "adw-gtk3-dark" if dark else "adw-gtk3")

    def set_widget_accent(self, hex_color):
        cfg = read_config()
        cfg["accent"] = hex_color
        write_config(cfg)
        if self.spacebar:
            self.spacebar.set_string("active-workspace-background-color", hex_color)

    def set_terminal_palette(self, palette):
        if not self.ptyxis:
            return
        uuid = self.ptyxis.get_string("default-profile-uuid")
        prof = schema_settings("org.gnome.Ptyxis.Profile", path=f"/org/gnome/Ptyxis/Profiles/{uuid}/")
        if prof:
            prof.set_string("palette", palette)

    def terminal_palette(self):
        if not self.ptyxis:
            return None
        uuid = self.ptyxis.get_string("default-profile-uuid")
        prof = schema_settings("org.gnome.Ptyxis.Profile", path=f"/org/gnome/Ptyxis/Profiles/{uuid}/")
        return prof.get_string("palette") if prof else None

    def apply_preset(self, p):
        if p["wall"].exists():
            self.set_wallpaper(p["wall"])
        self.set_accent(p["accent"])
        self.set_widget_accent(p["widget"])
        self.set_terminal_palette(p["palette"])
        cfg = read_config()
        cfg["preset"] = p["id"]
        write_config(cfg)

    # виджеты Desktop Widgets (часы и погода)
    def azclock_enabled(self, widget_id):
        if not self.azclock:
            return False
        for entry in self.azclock.get_value("widgets").unpack():
            if widget_id in entry:
                return bool(entry[widget_id].get("enabled", True))
        return False

    def set_azclock_enabled(self, widget_id, enabled):
        if not self.azclock:
            return
        out = []
        for entry in self.azclock.get_value("widgets").unpack():
            (wid, data), = entry.items()
            data = dict(data)
            if wid == widget_id:
                data["enabled"] = enabled
            inner = {k: GLib.Variant("b", v) for k, v in data.items() if isinstance(v, bool)}
            out.append({wid: GLib.Variant("a{sv}", inner)})
        self.azclock.set_value("widgets", GLib.Variant("aa{sv}", out))

    def _weather_element(self):
        src = ext_schemas("azclock@azclock.gitlab.com")
        return schema_settings("org.gnome.shell.extensions.azclock.element-data", src,
                               f"/org/gnome/shell/extensions/azclock/widget-data/{WEATHER_WIDGET}/element-data/{WEATHER_ELEMENT}/")

    def weather_city(self):
        el = self._weather_element()
        if not el:
            return None
        locs = el.get_value("locations")
        if locs.n_children() == 0:
            return None
        loc = GWeather.Location.get_world().deserialize(locs.get_child_value(0).get_variant())
        if not loc:
            return None
        return f"{loc.get_name()}, {loc.get_country_name() or ''}".strip(", ")

    def set_weather_city(self, loc):
        el = self._weather_element()
        if el:
            el.set_value("locations", GLib.Variant("av", [loc.serialize()]))

    # экран блокировки: лёгкое размытие из Blur my Shell и аватар
    LOCK_RE = re.compile(r"('pipeline_lock_soft'.*?'unscaled_radius': <)(\d+)(>.*?'brightness': <)([\d.]+)(>)", re.S)

    def lock_blur(self):
        cur = subprocess.run(["dconf", "read", "/org/gnome/shell/extensions/blur-my-shell/pipelines"],
                             capture_output=True, text=True).stdout
        m = self.LOCK_RE.search(cur)
        return (int(m.group(2)), float(m.group(4))) if m else None

    def set_lock_blur(self, radius, brightness):
        key = "/org/gnome/shell/extensions/blur-my-shell/pipelines"
        cur = subprocess.run(["dconf", "read", key], capture_output=True, text=True).stdout.strip()
        new, n = self.LOCK_RE.subn(lambda m: f"{m.group(1)}{int(radius)}{m.group(3)}{brightness:.2f}{m.group(5)}", cur, 1)
        if n:
            subprocess.run(["dconf", "write", key, new])

    def avatar_path(self):
        try:
            res = Gio.bus_get_sync(Gio.BusType.SYSTEM, None).call_sync(
                "org.freedesktop.Accounts", f"/org/freedesktop/Accounts/User{os.getuid()}",
                "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.freedesktop.Accounts.User", "IconFile")), None, 0, -1, None)
            path = res.unpack()[0]
            return path if path and os.path.exists(path) else None
        except GLib.Error:
            return None

    def set_avatar(self, src):
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(src), 512, 512, True)
        side = min(pb.get_width(), pb.get_height())
        pb = pb.new_subpixbuf((pb.get_width() - side) // 2, (pb.get_height() - side) // 2, side, side)
        out = HOME / ".face"
        pb.savev(str(out), "png", [], [])
        Gio.bus_get_sync(Gio.BusType.SYSTEM, None).call_sync(
            "org.freedesktop.Accounts", f"/org/freedesktop/Accounts/User{os.getuid()}",
            "org.freedesktop.Accounts.User", "SetIconFile", GLib.Variant("(s)", (str(out),)), None, 0, -1, None)

    # шрифты виджетов
    def _azclock_element(self, widget, element):
        return schema_settings("org.gnome.shell.extensions.azclock.element-data",
                               ext_schemas("azclock@azclock.gitlab.com"),
                               f"/org/gnome/shell/extensions/azclock/widget-data/{widget}/element-data/{element}/")

    def _font_target(self, key):
        return {"time": (CLOCK_WIDGET, TIME_ELEMENT), "date": (CLOCK_WIDGET, DATE_ELEMENT),
                "weather": (WEATHER_WIDGET, WEATHER_ELEMENT)}.get(key)

    def get_font(self, key):
        if key == "cards":
            f = read_config().get("cards_font", {})
            return f.get("family", "JetBrainsMono Nerd Font"), int(f.get("weight", 400))
        el = self._azclock_element(*self._font_target(key))
        if not el:
            return "Ubuntu Sans", 400
        use, family = el.get_value("font-family-override").unpack()
        weight = el.get_int("font-weight") or 400
        return (family if use and family else "Ubuntu Sans"), weight

    def set_font(self, key, family, weight):
        if key == "cards":
            cfg = read_config()
            cfg["cards_font"] = {"family": family, "weight": weight}
            write_config(cfg)
            self._write_cards_css(family, weight)
            return
        el = self._azclock_element(*self._font_target(key))
        if el:
            el.set_value("font-family-override", GLib.Variant("(bs)", (True, family)))
            el.set_int("font-weight", weight)

    def _write_cards_css(self, family, weight):
        """Шрифт карточек живёт в stylesheet расширения; перечитывается при его перезапуске."""
        if not CARDS_CSS.exists():
            return
        css = CARDS_CSS.read_text()
        block = (f"/* sxr-font-start */\n.sxr-card {{ font-family: \"{family}\", sans-serif; "
                 f"font-weight: {weight}; }}\n/* sxr-font-end */\n")
        css = re.sub(r"/\* sxr-font-start \*/.*?/\* sxr-font-end \*/\n?", "", css, flags=re.S)
        CARDS_CSS.write_text(css.rstrip("\n") + "\n" + block)
        if extension_state(EXT_UUID):
            # GNOME обрабатывает включение асинхронно: без паузы повторное включение теряется.
            # Отдельный процесс, чтобы включение не потерялось, даже если окно закроют сразу.
            subprocess.Popen(["sh", "-c", "gnome-extensions disable swixraze-widgets@mrKirillos-swix; sleep 1.5; "
                              "gnome-extensions enable swixraze-widgets@mrKirillos-swix"], start_new_session=True)

    # расположение виджетов
    def _azclock_widget(self, widget):
        return schema_settings("org.gnome.shell.extensions.azclock.widget-data",
                               ext_schemas("azclock@azclock.gitlab.com"),
                               f"/org/gnome/shell/extensions/azclock/widget-data/{widget}/")

    def get_anchor_pos(self, key):
        """Относительные координаты точки привязки виджета (0..1) или None."""
        if key in ("clock", "weather"):
            w = self._azclock_widget(CLOCK_WIDGET if key == "clock" else WEATHER_WIDGET)
            try:
                d = json.loads(w.get_string("location-data"))
                return d["relativeX"], d["relativeY"]
            except (ValueError, KeyError, AttributeError):
                return DEFAULT_CLOCK_POS if key == "clock" else DEFAULT_WEATHER_POS
        pos = read_config().get("positions", {}).get(key)
        return (pos["x"], pos["y"]) if pos else None

    def set_anchor_pos(self, key, rx, ry):
        rx, ry = round(min(max(rx, 0), 1), 4), round(min(max(ry, 0), 1), 4)
        if key in ("clock", "weather"):
            w = self._azclock_widget(CLOCK_WIDGET if key == "clock" else WEATHER_WIDGET)
            if w:
                w.set_string("location-data", json.dumps({"monitorIndex": 0, "relativeX": rx, "relativeY": ry}))
            return
        cfg = read_config()
        cfg.setdefault("positions", {})[key] = {"x": rx, "y": ry}
        write_config(cfg)

    def reset_positions(self):
        self.set_anchor_pos("clock", *DEFAULT_CLOCK_POS)
        self.set_anchor_pos("weather", *DEFAULT_WEATHER_POS)
        cfg = read_config()
        cfg.pop("positions", None)
        write_config(cfg)

    def widget_visible(self, key):
        if key == "clock":
            return self.azclock_enabled(CLOCK_WIDGET)
        if key == "weather":
            return self.azclock_enabled(WEATHER_WIDGET)
        return read_config().get("widgets", {}).get(key, key != "system")

    def set_card(self, key, value):
        cfg = read_config()
        if key in ("corner", "lockscreen"):
            cfg[key] = value
        else:
            cfg.setdefault("widgets", {})[key] = value
        write_config(cfg)


# ----------------------------------------------------------------- UI
class ThumbLoader:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=4)

    def load(self, src: Path, picture: Gtk.Picture):
        def done(fut):
            out = fut.result()
            if out:
                GLib.idle_add(lambda: (picture.set_filename(str(out)), False)[1])
        self.pool.submit(make_thumb, src).add_done_callback(done)


def page_header(title, subtitle):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_bottom=12)
    t = Gtk.Label(label=title, xalign=0, css_classes=["page-title"])
    s = Gtk.Label(label=subtitle, xalign=0, wrap=True, css_classes=["dim-label"])
    box.append(t)
    box.append(s)
    return box


def scrolled(child):
    clamp = Adw.Clamp(maximum_size=1100, child=child, margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
    return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)


class AppearanceWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Оформление", default_width=1100, default_height=760)
        self.be = Backend()
        self.loader = ThumbLoader()
        self.toasts = Adw.ToastOverlay()

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        pages = [
            ("themes", "Темы", "applications-graphics-symbolic", self.build_themes()),
            ("walls", "Обои", "image-x-generic-symbolic", self.build_walls()),
            ("colors", "Цвет", "preferences-color-symbolic", self.build_colors()),
            ("widgets", "Виджеты", "view-grid-symbolic", self.build_widgets()),
            ("layout", "Расположение", "view-pin-symbolic", self.build_layout()),
            ("icons", "Иконки", "applications-system-symbolic", self.build_icons()),
            ("fonts", "Шрифты", "font-x-generic-symbolic", self.build_fonts()),
            ("dock", "Док", "user-desktop-symbolic", self.build_dock()),
        ]
        sidebar_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        for pid, title, icon, widget in pages:
            self.stack.add_titled(widget, pid, title)
            row = Gtk.ListBoxRow()
            row.pid = pid
            rb = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6)
            rb.append(Gtk.Image(icon_name=icon))
            rb.append(Gtk.Label(label=title, xalign=0))
            row.set_child(rb)
            sidebar_list.append(row)
        sidebar_list.connect("row-selected", lambda _l, r: r and self.stack.set_visible_child_name(r.pid))
        sidebar_list.select_row(sidebar_list.get_row_at_index(0))
        self.sidebar_list = sidebar_list

        side_tv = Adw.ToolbarView()
        side_tv.add_top_bar(Adw.HeaderBar(show_title=False))
        side_tv.set_content(sidebar_list)
        content_tv = Adw.ToolbarView()
        content_tv.add_top_bar(Adw.HeaderBar(show_title=False))
        content_tv.set_content(self.stack)

        split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="Оформление", child=side_tv),
            content=Adw.NavigationPage(title="Оформление", child=content_tv),
            min_sidebar_width=200, max_sidebar_width=240)
        self.toasts.set_child(split)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(split, "collapsed", True)
        self.add_breakpoint(bp)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=2))

    # --------------------------------------------------- Темы
    def build_themes(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(page_header("Темы", "Одним нажатием меняет обои, системный цвет, цвет виджетов и палитру терминала."))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=3,
                           max_children_per_line=4, column_spacing=16, row_spacing=16)
        self.preset_cards = {}
        active = read_config().get("preset")
        for p in PRESETS:
            btn = Gtk.Button(css_classes=["card", "flat"])
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10, margin_bottom=12,
                        margin_start=10, margin_end=10)
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, css_classes=["thumb"], overflow=Gtk.Overflow.HIDDEN,
                              height_request=130, width_request=220)
            if p["wall"].exists():
                self.loader.load(p["wall"], pic)
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=p["name"], xalign=0, hexpand=True, css_classes=["card-title"]))
            for c in p["dots"]:
                d = Gtk.Box(css_classes=["dot"], valign=Gtk.Align.CENTER)
                self._paint(d, c)
                row.append(d)
            v.append(pic)
            v.append(row)
            btn.set_child(v)
            btn.connect("clicked", self.on_preset, p)
            if p["id"] == active:
                btn.add_css_class("active")
            self.preset_cards[p["id"]] = btn
            flow.append(btn)
        box.append(flow)
        return scrolled(box)

    def on_preset(self, _btn, p):
        self.be.apply_preset(p)
        for pid, card in self.preset_cards.items():
            (card.add_css_class if pid == p["id"] else card.remove_css_class)("active")
        self.sync_colors()
        self.toast(f"Тема «{p['name']}» применена")

    _paint_css = {}
    _paint_provider = None

    def _paint(self, widget, color):
        """Красит виджет в цвет через общий CSS-класс."""
        cls = "c-" + color.lstrip("#").lower()
        if cls not in self._paint_css:
            self._paint_css[cls] = f".{cls} {{ background-color: {color}; }}"
            if AppearanceWindow._paint_provider is None:
                AppearanceWindow._paint_provider = Gtk.CssProvider()
                Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), AppearanceWindow._paint_provider,
                                                          Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            AppearanceWindow._paint_provider.load_from_string("\n".join(self._paint_css.values()))
        widget.add_css_class(cls)

    # --------------------------------------------------- Обои
    def build_walls(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=12)
        hdr = page_header("Обои", f"Коллекция из папки «Изображения/Wallpapers». Нажмите на картинку, чтобы поставить её.")
        hdr.set_hexpand(True)
        head.append(hdr)
        open_btn = Gtk.Button(label="Открыть папку", valign=Gtk.Align.CENTER)
        open_btn.connect("clicked", lambda *_: Gio.AppInfo.launch_default_for_uri(WALL_DIR.as_uri(), None))
        add_btn = Gtk.Button(label="Добавить…", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        add_btn.connect("clicked", self.on_add_wallpaper)
        head.append(open_btn)
        head.append(add_btn)
        box.append(head)
        self.wall_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=3,
                                     max_children_per_line=5, column_spacing=12, row_spacing=12)
        box.append(self.wall_flow)
        self.fill_walls()
        return scrolled(box)

    def fill_walls(self):
        while (child := self.wall_flow.get_first_child()):
            self.wall_flow.remove(child)
        current = self.be.wallpaper()
        self.wall_buttons = {}
        for path in list_wallpapers():
            btn = Gtk.Button(css_classes=["card", "flat"], tooltip_text=path.name)
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, css_classes=["thumb"], overflow=Gtk.Overflow.HIDDEN,
                              height_request=110, width_request=190, margin_top=6, margin_bottom=6,
                              margin_start=6, margin_end=6)
            btn.set_child(pic)
            self.loader.load(path, pic)
            btn.connect("clicked", self.on_wallpaper, path)
            if path.as_uri() == current:
                btn.add_css_class("active")
            self.wall_buttons[path.as_uri()] = btn
            self.wall_flow.append(btn)

    def on_wallpaper(self, _btn, path):
        self.be.set_wallpaper(path)
        for uri, b in self.wall_buttons.items():
            (b.add_css_class if uri == path.as_uri() else b.remove_css_class)("active")
        self.toast("Обои поставлены")

    def on_add_wallpaper(self, *_):
        dlg = Gtk.FileDialog(title="Выберите картинку")
        flt = Gtk.FileFilter(name="Изображения")
        flt.add_mime_type("image/*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            src = Path(f.get_path())
            dst = WALL_DIR / src.name
            if src != dst:
                dst.write_bytes(src.read_bytes())
            self.be.set_wallpaper(dst)
            self.fill_walls()
            self.toast("Картинка добавлена и поставлена")
        dlg.open(self, None, done)

    # --------------------------------------------------- Цвет
    def build_colors(self):
        page = Adw.PreferencesPage()
        g1 = Adw.PreferencesGroup(title="Системный цвет",
                                  description="Цвет кнопок, переключателей и выделения во всех приложениях.")
        row = Gtk.Box(spacing=12, halign=Gtk.Align.START, margin_top=6, margin_bottom=6)
        self.accent_buttons = {}
        for name, label, hexc in ACCENTS:
            b = Gtk.Button(css_classes=["accent-btn"], tooltip_text=label)
            self._paint(b, hexc)
            b.connect("clicked", self.on_accent, name)
            self.accent_buttons[name] = b
            row.append(b)
        g1.add(row)
        g0 = Adw.PreferencesGroup(title="Режим")
        self.dark_row = Adw.SwitchRow(title="Тёмный режим")
        self.dark_row.connect("notify::active", lambda r, _p: self._syncing or self.be.set_dark(r.get_active()))
        g0.add(self.dark_row)
        page.add(g0)
        page.add(g1)

        g2 = Adw.PreferencesGroup(title="Цвет виджетов",
                                  description="Кнопка плеера, полоски заряда и активный рабочий стол на панели.")
        self.widget_color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.widget_color.connect("notify::rgba", self.on_widget_color)
        r = Adw.ActionRow(title="Акцент виджетов")
        r.add_suffix(self.widget_color)
        g2.add(r)
        page.add(g2)

        g3 = Adw.PreferencesGroup(title="Терминал")
        self.palettes = sorted({p["palette"] for p in PRESETS} | {"Ubuntu"}, key=str.lower)
        self.palette_row = Adw.ComboRow(title="Палитра терминала", model=Gtk.StringList.new(self.palettes))
        self.palette_row.connect("notify::selected", self.on_palette)
        g3.add(self.palette_row)
        page.add(g3)
        self._syncing = False
        self.sync_colors()
        return page

    def sync_colors(self):
        self._syncing = True
        acc = self.be.iface.get_string("accent-color")
        for name, b in self.accent_buttons.items():
            (b.add_css_class if name == acc else b.remove_css_class)("active")
        self.dark_row.set_active(self.be.iface.get_string("color-scheme") == "prefer-dark")
        rgba = Gdk.RGBA()
        rgba.parse(read_config().get("accent", "#cba6f7"))
        self.widget_color.set_rgba(rgba)
        pal = self.be.terminal_palette()
        if pal in self.palettes:
            self.palette_row.set_selected(self.palettes.index(pal))
        self._syncing = False

    def on_accent(self, _b, name):
        self.be.set_accent(name)
        self.sync_colors()

    def on_widget_color(self, btn, _p):
        if self._syncing:
            return
        c = btn.get_rgba()
        self.be.set_widget_accent("#%02x%02x%02x" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255)))

    def on_palette(self, row, _p):
        if self._syncing:
            return
        self.be.set_terminal_palette(self.palettes[row.get_selected()])
        self.toast("Палитра применится к новым окнам терминала")

    # --------------------------------------------------- Виджеты
    def build_widgets(self):
        page = Adw.PreferencesPage()
        cfg = read_config()
        g1 = Adw.PreferencesGroup(title="Часы и погода")
        clock = Adw.SwitchRow(title="Большие часы", subtitle="Время и дата сверху по центру",
                              active=self.be.azclock_enabled(CLOCK_WIDGET))
        clock.connect("notify::active", lambda r, _p: self.be.set_azclock_enabled(CLOCK_WIDGET, r.get_active()))
        weather = Adw.SwitchRow(title="Погода", subtitle="Карточка справа внизу",
                                active=self.be.azclock_enabled(WEATHER_WIDGET))
        weather.connect("notify::active", lambda r, _p: self.be.set_azclock_enabled(WEATHER_WIDGET, r.get_active()))
        for r in (clock, weather):
            g1.add(r)
        page.add(g1)

        gc = Adw.PreferencesGroup(title="Город для погоды")
        self.city_row = Adw.ActionRow(title="Сейчас", subtitle=self.be.weather_city() or "Не выбран")
        self.city_row.add_prefix(Gtk.Image(icon_name="mark-location-symbolic"))
        gc.add(self.city_row)
        self.city_entry = Adw.EntryRow(title="Найти город (по-русски или латиницей)")
        self.city_entry.connect("changed", self.on_city_typed)
        gc.add(self.city_entry)
        self.city_results = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                        margin_top=12, visible=False)
        self.city_results.connect("row-activated", self.on_city_chosen)
        gc.add(self.city_results)
        page.add(gc)
        self._city_timer = 0

        g2 = Adw.PreferencesGroup(title="Карточки")
        if not extension_state(EXT_UUID):
            g2.set_description("Карточки появятся после выхода из системы и повторного входа.")
        player = Adw.SwitchRow(title="Плеер", subtitle="Что сейчас играет: обложка, название, кнопки",
                               active=cfg.get("widgets", {}).get("player", True))
        player.connect("notify::active", lambda r, _p: self.be.set_card("player", r.get_active()))
        devices = Adw.SwitchRow(title="Устройства", subtitle="Наушники, мышь, телефон и заряд батареи",
                                active=cfg.get("widgets", {}).get("devices", True))
        devices.connect("notify::active", lambda r, _p: self.be.set_card("devices", r.get_active()))
        corner = Adw.ComboRow(title="Где показывать", model=Gtk.StringList.new(["Слева внизу", "Справа внизу"]),
                              selected=1 if cfg.get("corner") == "bottom-right" else 0)
        corner.connect("notify::selected", lambda r, _p: self.be.set_card(
            "corner", "bottom-right" if r.get_selected() == 1 else "bottom-left"))
        system = Adw.SwitchRow(title="Система", subtitle="Загрузка процессора, память и температура",
                               active=cfg.get("widgets", {}).get("system", False))
        system.connect("notify::active", lambda r, _p: self.be.set_card("system", r.get_active()))
        for r in (player, devices, system, corner):
            g2.add(r)
        page.add(g2)

        gl = Adw.PreferencesGroup(title="Экран блокировки")
        lock_cards = Adw.SwitchRow(title="Карточки на экране блокировки",
                                   subtitle="Система, плеер и устройства в ряд внизу экрана",
                                   active=cfg.get("lockscreen", True))
        lock_cards.connect("notify::active", lambda r, _p: self.be.set_card("lockscreen", r.get_active()))
        gl.add(lock_cards)
        notif = Gio.Settings.new("org.gnome.desktop.notifications")
        gnome_lock = Adw.SwitchRow(title="Уведомления и плеер GNOME",
                                   subtitle="Встроенные; с карточками получится два плеера",
                                   active=notif.get_boolean("show-in-lock-screen"))
        gnome_lock.connect("notify::active", lambda r, _p: notif.set_boolean("show-in-lock-screen", r.get_active()))
        gl.add(gnome_lock)
        blur = self.be.lock_blur()
        if blur:
            radius = Adw.SpinRow.new_with_range(0, 40, 2)
            radius.set_title("Размытие фона")
            radius.set_value(blur[0])
            bright = Adw.SpinRow.new_with_range(30, 100, 5)
            bright.set_title("Яркость фона, %")
            bright.set_value(round(blur[1] * 100))
            apply = lambda *_: self.be.set_lock_blur(radius.get_value(), bright.get_value() / 100)
            radius.connect("notify::value", apply)
            bright.connect("notify::value", apply)
            gl.add(radius)
            gl.add(bright)
        self.avatar = Adw.Avatar(size=40, show_initials=True, text=GLib.get_real_name() or GLib.get_user_name())
        self._load_avatar()
        av_row = Adw.ActionRow(title="Аватар", subtitle="Показывается при входе и на экране блокировки")
        av_row.add_prefix(self.avatar)
        av_btn = Gtk.Button(label="Выбрать…", valign=Gtk.Align.CENTER)
        av_btn.connect("clicked", self.on_pick_avatar)
        av_row.add_suffix(av_btn)
        gl.add(av_row)
        page.add(gl)

        ding = schema_settings("org.gnome.shell.extensions.ding")
        if ding:
            g3 = Adw.PreferencesGroup(title="Рабочий стол")
            home = Adw.SwitchRow(title="Значок «Домашняя папка»", active=ding.get_boolean("show-home"))
            home.connect("notify::active", lambda r, _p: ding.set_boolean("show-home", r.get_active()))
            g3.add(home)
            page.add(g3)
        return page

    def _load_avatar(self):
        path = self.be.avatar_path()
        if path:
            try:
                self.avatar.set_custom_image(Gdk.Texture.new_from_filename(path))
            except GLib.Error:
                pass

    def on_pick_avatar(self, *_):
        dlg = Gtk.FileDialog(title="Выберите фото для аватара")
        flt = Gtk.FileFilter(name="Изображения")
        flt.add_mime_type("image/*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            try:
                self.be.set_avatar(f.get_path())
                self._load_avatar()
                self.toast("Аватар обновлён")
            except GLib.Error as e:
                self.toast(f"Не удалось поставить аватар: {e.message}")
        dlg.open(self, None, done)

    def on_city_typed(self, entry):
        if self._city_timer:
            GLib.source_remove(self._city_timer)
        self._city_timer = GLib.timeout_add(250, self._run_city_search)

    def _run_city_search(self):
        self._city_timer = 0
        text = self.city_entry.get_text()
        while (row := self.city_results.get_first_child()):
            self.city_results.remove(row)
        if len(text.strip()) < 2:
            self.city_results.set_visible(False)
            return False
        found = search_cities(text)
        if not found:
            r = Adw.ActionRow(title="Ничего не найдено", subtitle="Попробуйте латиницей, например «Moscow»")
            self.city_results.append(r)
        for loc in found:
            r = Adw.ActionRow(title=loc.get_name(), subtitle=loc.get_country_name() or "", activatable=True)
            r.loc = loc
            self.city_results.append(r)
        self.city_results.set_visible(True)
        return False

    def on_city_chosen(self, _lb, row):
        loc = getattr(row, "loc", None)
        if not loc:
            return
        self.be.set_weather_city(loc)
        self.city_row.set_subtitle(self.be.weather_city() or loc.get_name())
        self.city_entry.set_text("")
        self.city_results.set_visible(False)
        self.toast(f"Погода: {loc.get_name()}")

    # --------------------------------------------------- Док
    # --------------------------------------------------- Расположение
    def build_layout(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        head = Gtk.Box(spacing=12)
        hdr = page_header("Расположение", "Перетащите виджеты мышкой — на рабочем столе они переедут сразу. "
                                          "По центру экрана есть притягивание.")
        hdr.set_hexpand(True)
        head.append(hdr)
        reset = Gtk.Button(label="Вернуть как было", valign=Gtk.Align.CENTER)
        reset.connect("clicked", self.on_layout_reset)
        head.append(reset)
        box.append(head)

        mon = Gdk.Display.get_default().get_monitors().get_item(0).get_geometry()
        self.mon_w, self.mon_h = mon.width, mon.height
        self.map_w = 760
        self.map_h = round(self.map_w * mon.height / mon.width)
        self.map_scale = self.map_w / mon.width

        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER, hexpand=False, vexpand=False, css_classes=["layout-map"],
                              overflow=Gtk.Overflow.HIDDEN)
        overlay.set_size_request(self.map_w, self.map_h)
        # основа фиксированного размера; обои — оверлеем, чтобы их пропорции не меняли размер схемы
        base = Gtk.Box()
        base.set_size_request(self.map_w, self.map_h)
        overlay.set_child(base)
        self.map_bg = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        overlay.add_overlay(self.map_bg)
        self.map_fixed = Gtk.Fixed()
        overlay.add_overlay(self.map_fixed)
        # полоска дока снизу для ориентира
        dock = Gtk.Box(css_classes=["layout-dock"])
        dock.set_size_request(round(self.map_w * 0.36), 14)
        self.map_fixed.put(dock, round(self.map_w * 0.32), self.map_h - 22)
        holder = Gtk.Box(halign=Gtk.Align.CENTER)
        holder.append(overlay)
        box.append(holder)
        hint = Gtk.Label(label="Размеры прямоугольников примерные: настоящие виджеты зависят от шрифта и содержимого.",
                         css_classes=["dim-label", "caption"], wrap=True)
        box.append(hint)
        self.layout_boxes = {}
        self.refresh_layout()
        return scrolled(box)

    def refresh_layout(self):
        wall = self.be.wallpaper()
        if wall.startswith("file://"):
            self.loader.load(Path(GLib.filename_from_uri(wall)[0]), self.map_bg)
        for child in list(self.layout_boxes.values()):
            self.map_fixed.remove(child)
        self.layout_boxes = {}
        defaults = self._default_card_positions()
        for key, label, (w, h), anchor in LAYOUT_ITEMS:
            if not self.be.widget_visible(key):
                continue
            mw, mh = round(w * self.map_scale), round(h * self.map_scale)
            pos = self.be.get_anchor_pos(key) or defaults.get(key)
            ax, ay = pos[0] * self.map_w, pos[1] * self.map_h
            x = ax - (mw / 2 if anchor == "top-center" else mw if anchor == "bottom-right" else 0)
            y = ay - (mh if anchor == "bottom-right" else 0)
            item = Gtk.Label(label=label, css_classes=["layout-item"], tooltip_text="Перетащите",
                             xalign=0.5, ellipsize=3)
            item.set_size_request(mw, mh)
            item.set_cursor(Gdk.Cursor.new_from_name("grab"))
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self.on_item_drag_begin, item)
            drag.connect("drag-update", self.on_item_drag_update, item, mw, mh)
            drag.connect("drag-end", self.on_item_drag_end, item, key, mw, mh, anchor)
            item.add_controller(drag)
            self.map_fixed.put(item, *self._clamp(x, y, mw, mh))
            self.layout_boxes[key] = item

    def _default_card_positions(self):
        """Где расширение ставит карточки без своего места: столбиком в углу над доком."""
        cfg = read_config()
        right = cfg.get("corner") == "bottom-right"
        bottom = self.mon_h - 96
        out = {}
        sizes = {k: s for k, _l, s, _a in LAYOUT_ITEMS}
        for key in ("devices", "player", "system"):
            if not self.be.widget_visible(key):
                continue
            w, h = sizes[key]
            x = self.mon_w - w - 28 if right else 28
            y = bottom - h
            if key not in cfg.get("positions", {}):
                bottom = y - 12
            out[key] = (x / self.mon_w, y / self.mon_h)
        return out

    def _clamp(self, x, y, w, h):
        return (round(min(max(x, 0), self.map_w - w)), round(min(max(y, 0), self.map_h - h)))

    def on_item_drag_begin(self, _g, _x, _y, item):
        self._drag_origin = self.map_fixed.get_child_position(item)
        item.add_css_class("dragging")

    def on_item_drag_update(self, _g, dx, dy, item, w, h):
        x, y = self._drag_origin[0] + dx, self._drag_origin[1] + dy
        # притягивание к центру по горизонтали
        if abs(x + w / 2 - self.map_w / 2) < 8:
            x = self.map_w / 2 - w / 2
        self.map_fixed.move(item, *self._clamp(x, y, w, h))

    def on_item_drag_end(self, _g, _dx, _dy, item, key, w, h, anchor):
        item.remove_css_class("dragging")
        x, y = self.map_fixed.get_child_position(item)
        ax = x + (w / 2 if anchor == "top-center" else w if anchor == "bottom-right" else 0)
        ay = y + (h if anchor == "bottom-right" else 0)
        self.be.set_anchor_pos(key, ax / self.map_w, ay / self.map_h)

    def on_layout_reset(self, *_):
        self.be.reset_positions()
        self.refresh_layout()
        self.toast("Виджеты вернулись на свои места")

    def build_icons(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(page_header("Иконки", "Стиль значков приложений и папок. Нажмите на набор, чтобы применить."))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                           max_children_per_line=3, column_spacing=16, row_spacing=16)
        display = Gdk.Display.get_default()
        search_path = Gtk.IconTheme.get_for_display(display).get_search_path()
        current = self.be.iface.get_string("icon-theme")
        self.icon_cards = {}
        for theme_id, name in list_icon_themes():
            it = Gtk.IconTheme(search_path=search_path, theme_name=theme_id)
            btn = Gtk.Button(css_classes=["card", "flat"], tooltip_text=theme_id)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=14, margin_bottom=14,
                        margin_start=14, margin_end=14)
            v.append(Gtk.Label(label=name, xalign=0, css_classes=["card-title"]))
            grid = Gtk.Grid(column_spacing=10, row_spacing=10, halign=Gtk.Align.START)
            # 8 иконок, которые в наборе действительно есть
            picks = [next(n for n in names if it.has_icon(n)) for names in ICON_SAMPLES
                     if any(it.has_icon(n) for n in names)][:8]
            for i, icon in enumerate(picks):
                paintable = it.lookup_icon(icon, None, 40, 1, Gtk.TextDirection.NONE, 0)
                img = Gtk.Image(paintable=paintable, pixel_size=40)
                grid.attach(img, i % 4, i // 4, 1, 1)
            v.append(grid)
            btn.set_child(v)
            btn.connect("clicked", self.on_icon_theme, theme_id, name)
            if theme_id == current:
                btn.add_css_class("active")
            self.icon_cards[theme_id] = btn
            flow.append(btn)
        box.append(flow)
        return scrolled(box)

    def on_icon_theme(self, _btn, theme_id, name):
        self.be.iface.set_string("icon-theme", theme_id)
        for tid, card in self.icon_cards.items():
            (card.add_css_class if tid == theme_id else card.remove_css_class)("active")
        self.toast(f"Иконки: {name}")

    def build_fonts(self):
        page = Adw.PreferencesPage()
        installed = sorted({f.get_name() for f in Gtk.Label().get_pango_context().list_families()}, key=str.lower)
        favs = [f for f in FONT_FAVORITES if f in installed]
        self.font_families = favs + [f for f in installed if f not in favs]
        for key, title, sample, size in FONT_TARGETS:
            family, weight = self.be.get_font(key)
            if family not in self.font_families:
                self.font_families.insert(0, family)
            group = Adw.PreferencesGroup(title=title)
            preview = Gtk.Label(label=sample, xalign=0, margin_top=12, margin_bottom=12, margin_start=14,
                                margin_end=14, ellipsize=3)
            frame = Gtk.Box(css_classes=["card"])
            frame.append(preview)
            group.add(frame)
            fam_row = Adw.ComboRow(title="Шрифт", enable_search=True,
                                   model=Gtk.StringList.new(self.font_families),
                                   selected=self.font_families.index(family))
            fam_row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            weights = [w for w, _ in FONT_WEIGHTS]
            nearest = min(weights, key=lambda w: abs(w - weight))
            w_row = Adw.ComboRow(title="Начертание", model=Gtk.StringList.new([n for _, n in FONT_WEIGHTS]),
                                 selected=weights.index(nearest))
            group.add(fam_row)
            group.add(w_row)
            page.add(group)

            def update(*_a, key=key, preview=preview, fam_row=fam_row, w_row=w_row, size=size, sample=sample,
                       apply=True):
                fam = self.font_families[fam_row.get_selected()]
                wt = FONT_WEIGHTS[w_row.get_selected()][0]
                preview.set_markup(f'<span font_family="{GLib.markup_escape_text(fam)}" font_weight="{wt}" '
                                   f'size="{size * 1024}">{GLib.markup_escape_text(sample)}</span>')
                if apply:
                    self.be.set_font(key, fam, wt)
            fam_row.connect("notify::selected", update)
            w_row.connect("notify::selected", update)
            update(apply=False)
        return page

    def build_dock(self):
        page = Adw.PreferencesPage()
        d = self.be.dock
        g = Adw.PreferencesGroup(title="Док")
        if d is None:
            g.set_description("Ubuntu Dock не найден.")
            page.add(g)
            return page
        autohide = Adw.SwitchRow(title="Прятать док", subtitle="Появляется, когда окна его не закрывают",
                                 active=not d.get_boolean("dock-fixed"))

        def on_autohide(r, _p):
            d.set_boolean("dock-fixed", not r.get_active())
            d.set_boolean("autohide", r.get_active())
            d.set_boolean("intellihide", r.get_active())
        autohide.connect("notify::active", on_autohide)
        size = Adw.SpinRow.new_with_range(24, 64, 2)
        size.set_title("Размер иконок")
        size.set_value(d.get_int("dash-max-icon-size"))
        size.connect("notify::value", lambda r, _p: d.set_int("dash-max-icon-size", int(r.get_value())))
        opacity = Adw.SpinRow.new_with_range(0, 100, 5)
        opacity.set_title("Непрозрачность фона, %")
        opacity.set_value(round(d.get_double("background-opacity") * 100))
        opacity.connect("notify::value", lambda r, _p: d.set_double("background-opacity", r.get_value() / 100))
        positions = ["BOTTOM", "LEFT", "RIGHT"]
        pos = Adw.ComboRow(title="Положение", model=Gtk.StringList.new(["Снизу", "Слева", "Справа"]))
        cur = d.get_string("dock-position")
        pos.set_selected(positions.index(cur) if cur in positions else 0)
        pos.connect("notify::selected", lambda r, _p: d.set_string("dock-position", positions[r.get_selected()]))
        for r in (autohide, size, opacity, pos):
            g.add(r)
        page.add(g)
        return page


class App(Adw.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if "--screenshot" in sys.argv:
            # проверочный запуск — отдельный экземпляр, открытое окно пользователя не трогаем
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id="io.github.mrKirillos_swix.SwixRaze", flags=flags)
        self.shot_dir = None

    def do_command_line(self, cmd):
        args = cmd.get_arguments()[1:]
        if args and args[0] == "--screenshot" and len(args) > 1:
            self.shot_dir = Path(args[1])
        self.activate()
        return 0

    def do_startup(self):
        Adw.Application.do_startup(self)
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.props.active_window or AppearanceWindow(self)
        win.present()
        if self.shot_dir:
            GLib.timeout_add(2500, self._shoot, win, 0)

    def _shoot(self, win, idx):
        """Режим проверки: сохраняет снимок каждой страницы и выходит."""
        names = ["themes", "walls", "colors", "widgets", "layout", "icons", "fonts", "dock"]
        if idx > 0:
            paintable = Gtk.WidgetPaintable(widget=win)
            w, h = win.get_width(), win.get_height()
            snap = Gtk.Snapshot()
            paintable.snapshot(snap, w, h)
            node = snap.to_node()
            if node:
                tex = win.get_renderer().render_texture(node, None)
                self.shot_dir.mkdir(parents=True, exist_ok=True)
                tex.save_to_png(str(self.shot_dir / f"{idx - 1}-{names[idx - 1]}.png"))
        if idx >= len(names):
            self.quit()
            return False
        win.sidebar_list.select_row(win.sidebar_list.get_row_at_index(idx))
        GLib.timeout_add(2500 if idx == 1 else 900, self._shoot, win, idx + 1)
        return False


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
