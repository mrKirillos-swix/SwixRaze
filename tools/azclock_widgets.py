#!/usr/bin/env python3
"""Создаёт или удаляет виджеты SwixRaze (часы и погода) в расширении Desktop Widgets (azclock).

    python3 tools/azclock_widgets.py install    # добавить, если их ещё нет
    python3 tools/azclock_widgets.py uninstall  # удалить только виджеты SwixRaze

ID виджетов постоянные — те же, что в app/swixraze.py, — поэтому приложение
находит их на любом компьютере. Чужие виджеты пользователя не трогаются.
"""
import subprocess
import sys

BASE = "/org/gnome/shell/extensions/azclock/"
CLOCK_WIDGET = "60fd8e73-da66-40e4-a90e-367b4d9bf1da"
WEATHER_WIDGET = "57d9c26a-2348-4ddf-8955-4a44cd164546"
WEATHER_ELEMENT = "ec3a66f2-5507-4684-b546-b6781cd6c06a"
TIME_ELEMENT = "10c19e0c-106a-446a-8519-59dac132e1ec"
DATE_ELEMENT = "752baec5-840a-4221-a4dd-5b6189626890"
OUR_WIDGETS = (CLOCK_WIDGET, WEATHER_WIDGET)


def template(font):
    """Настройки виджетов в формате `dconf load`. Город погоды выбирается в приложении."""
    fam = f"(true, '{font}')" if font else "(false, '')"
    return f"""
[widget-data/{CLOCK_WIDGET}]
name='Часы'
anchor-point='Center_Top'
location-data='{{"monitorIndex": 0, "relativeX": 0.5, "relativeY": 0.1}}'
convert-old-location-done=true
lock-widget=true
spacing=0
elements=[{{'{TIME_ELEMENT}': <{{'enabled': <true>}}>}}, {{'{DATE_ELEMENT}': <{{'enabled': <true>}}>}}]

[widget-data/{CLOCK_WIDGET}/element-data/{TIME_ELEMENT}]
name='Время'
date-format='%H:%M:%S'
font-family-override={fam}
font-size=64
font-weight=200
foreground-color='rgba(255,255,255,0.8)'
shadow=(true, 'rgba(0,0,0,0.35)', 0, 2, 0, 8)
text-align-x='Center'

[widget-data/{CLOCK_WIDGET}/element-data/{DATE_ELEMENT}]
name='Дата'
date-format='%A · %d %B'
font-family-override={fam}
font-size=13
font-weight=400
foreground-color='rgba(255,255,255,0.7)'
text-align-x='Center'

[widget-data/{WEATHER_WIDGET}]
name='Погода'
anchor-point='Bottom_Right'
location-data='{{"monitorIndex": 0, "relativeX": 0.985, "relativeY": 0.86}}'
convert-old-location-done=true
lock-widget=true
show-background=true
background-color='rgba(36,36,42,0.55)'
border-radius=18
padding=(16, 16, 12, 12)
elements=[{{'{WEATHER_ELEMENT}': <{{'enabled': <true>}}>}}]

[widget-data/{WEATHER_WIDGET}/element-data/{WEATHER_ELEMENT}]
name='Погода сейчас'
element-type='Weather_Element'
temperature-unit='Celsius'
show-hourly-forecast=false
show-daily-forecast=false
show-location=false
show-current-summary=false
current-temp-font-size=26
font-family-override={fam}
foreground-color='rgba(255,255,255,0.85)'
"""


def dconf(*args, stdin=None):
    return subprocess.run(["dconf", *args], input=stdin, capture_output=True, text=True, check=True).stdout.strip()


def font_installed(name):
    out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout
    return any(name in line.split(",") for line in out.splitlines())


def read_widget_ids():
    """ID виджетов из ключа widgets (aa{sv}) — простым разбором, без лишних зависимостей."""
    raw = dconf("read", BASE + "widgets")
    import re
    return re.findall(r"\{'([^']+)': <\{'enabled': <(true|false)>\}>\}", raw)


def write_widget_ids(entries):
    items = ", ".join(f"{{'{wid}': <{{'enabled': <{state}>}}>}}" for wid, state in entries)
    dconf("write", BASE + "widgets", f"[{items}]" if items else "@aa{sv} []")


def install():
    entries = read_widget_ids()
    have = {wid for wid, _ in entries}
    if all(w in have for w in OUR_WIDGETS):
        print("Виджеты SwixRaze уже есть — оставляю как есть.")
        return
    font = "Inter" if font_installed("Inter") else None
    dconf("load", BASE, stdin=template(font))
    dconf("write", BASE + "create-initial-widget", "false")
    for wid in OUR_WIDGETS:
        if wid not in have:
            entries.append((wid, "true"))
    write_widget_ids(entries)
    print("Добавлены виджеты: часы и погода.")


def uninstall():
    entries = [(w, s) for w, s in read_widget_ids() if w not in OUR_WIDGETS]
    write_widget_ids(entries)
    for wid in OUR_WIDGETS:
        dconf("reset", "-f", f"{BASE}widget-data/{wid}/")
    print("Виджеты SwixRaze удалены из Desktop Widgets.")


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
