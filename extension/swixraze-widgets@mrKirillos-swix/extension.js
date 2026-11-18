// SwixRaze Widgets — карточки плеера и устройств на рабочем столе.
// Настройки читаются из ~/.config/swixraze/config.json (его пишет приложение «Оформление»).

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Soup from 'gi://Soup?version=3.0';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const CONFIG_PATH = GLib.build_filenamev([GLib.get_user_config_dir(), 'swixraze', 'config.json']);
const COVER_DIR = GLib.build_filenamev([GLib.get_user_cache_dir(), 'swixraze', 'covers']);
const DEFAULTS = {accent: '#cba6f7', widgets: {player: true, devices: true, system: false}, corner: 'bottom-left', lockscreen: true};
const CARD_WIDTH = 320;
const DOCK_GAP = 96;

const MprisPlayerProxy = Gio.DBusProxy.makeProxyWrapper(`
<node><interface name="org.mpris.MediaPlayer2.Player">
  <method name="PlayPause"/><method name="Next"/><method name="Previous"/>
  <property name="PlaybackStatus" type="s" access="read"/>
  <property name="Metadata" type="a{sv}" access="read"/>
</interface></node>`);

const UPowerProxy = Gio.DBusProxy.makeProxyWrapper(`
<node><interface name="org.freedesktop.UPower">
  <method name="EnumerateDevices"><arg type="ao" direction="out"/></method>
  <signal name="DeviceAdded"><arg type="o"/></signal>
  <signal name="DeviceRemoved"><arg type="o"/></signal>
</interface></node>`);

const UPowerDeviceProxy = Gio.DBusProxy.makeProxyWrapper(`
<node><interface name="org.freedesktop.UPower.Device">
  <property name="Type" type="u" access="read"/>
  <property name="Model" type="s" access="read"/>
  <property name="Percentage" type="d" access="read"/>
  <property name="State" type="u" access="read"/>
  <property name="IsPresent" type="b" access="read"/>
  <property name="PowerSupply" type="b" access="read"/>
</interface></node>`);

// UPower device types -> иконка
const TYPE_ICONS = {
    2: 'battery-symbolic', 5: 'input-mouse-symbolic', 6: 'input-keyboard-symbolic',
    8: 'phone-symbolic', 12: 'input-gaming-symbolic', 17: 'audio-headphones-symbolic',
    18: 'audio-headphones-symbolic', 19: 'audio-headset-symbolic', 22: 'audio-speakers-symbolic',
};

function readConfig() {
    try {
        const [ok, bytes] = GLib.file_get_contents(CONFIG_PATH);
        if (ok) {
            const cfg = JSON.parse(new TextDecoder().decode(bytes));
            return {...DEFAULTS, ...cfg, widgets: {...DEFAULTS.widgets, ...(cfg.widgets ?? {})}};
        }
    } catch (e) {
        console.warn(`swixraze-widgets: config: ${e.message}`);
    }
    return structuredClone(DEFAULTS);
}

function unpackMeta(proxy) {
    const v = proxy.get_cached_property('Metadata');
    return v ? v.recursiveUnpack() : {};
}

// ---------------------------------------------------------------- Player
class PlayerCard {
    constructor(session) {
        this._session = session;
        this._players = new Map(); // busName -> {proxy, lastActive}
        this._current = null;
        this._accent = DEFAULTS.accent;

        this.actor = new St.BoxLayout({style_class: 'sxr-card sxr-player', width: CARD_WIDTH, reactive: true});
        this._cover = new St.Bin({style_class: 'sxr-cover'});
        this._coverIcon = new St.Icon({icon_name: 'audio-x-generic-symbolic', icon_size: 24, style_class: 'sxr-dim'});
        this._cover.set_child(this._coverIcon);

        const text = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, y_align: Clutter.ActorAlign.CENTER, style_class: 'sxr-player-text'});
        this._title = new St.Label({style_class: 'sxr-title'});
        this._artist = new St.Label({style_class: 'sxr-subtitle'});
        this._title.clutter_text.ellipsize = 3;
        this._artist.clutter_text.ellipsize = 3;
        text.add_child(this._title);
        text.add_child(this._artist);

        const controls = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER, style_class: 'sxr-controls'});
        this._prev = this._button('media-skip-backward-symbolic', () => this._call('PreviousAsync'));
        this._play = this._button('media-playback-start-symbolic', () => this._call('PlayPauseAsync'), true);
        this._next = this._button('media-skip-forward-symbolic', () => this._call('NextAsync'));
        controls.add_child(this._prev);
        controls.add_child(this._play);
        controls.add_child(this._next);

        this.actor.add_child(this._cover);
        this.actor.add_child(text);
        this.actor.add_child(controls);
        this.actor.hide();

        this._ownerSub = Gio.DBus.session.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus',
            'NameOwnerChanged', '/org/freedesktop/DBus', null, Gio.DBusSignalFlags.NONE,
            (_c, _s, _p, _i, _sig, params) => {
                const [name, oldOwner, newOwner] = params.deepUnpack();
                if (!name.startsWith('org.mpris.MediaPlayer2.'))
                    return;
                if (oldOwner)
                    this._removePlayer(name);
                if (newOwner)
                    this._addPlayer(name);
            });

        Gio.DBus.session.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
            'ListNames', null, null, Gio.DBusCallFlags.NONE, -1, null, (conn, res) => {
                try {
                    const [names] = conn.call_finish(res).deepUnpack();
                    names.filter(n => n.startsWith('org.mpris.MediaPlayer2.')).forEach(n => this._addPlayer(n));
                } catch (e) {
                    console.warn(`swixraze-widgets: ListNames: ${e.message}`);
                }
            });
    }

    _button(icon, cb, primary = false) {
        const b = new St.Button({style_class: primary ? 'sxr-btn sxr-btn-primary' : 'sxr-btn', can_focus: true});
        b.set_child(new St.Icon({icon_name: icon, icon_size: 16}));
        b.connect('clicked', cb);
        return b;
    }

    _call(method) {
        this._current?.proxy[method]?.().catch?.(e => console.warn(`swixraze-widgets: ${method}: ${e.message}`));
    }

    _addPlayer(name) {
        if (this._players.has(name))
            return;
        const entry = {proxy: null, lastActive: 0, sigId: 0};
        this._players.set(name, entry);
        new MprisPlayerProxy(Gio.DBus.session, name, '/org/mpris/MediaPlayer2', (proxy, error) => {
            if (error || !this._players.has(name) || this._players.get(name) !== entry)
                return;
            entry.proxy = proxy;
            entry.lastActive = proxy.PlaybackStatus === 'Playing' ? Date.now() : 0;
            entry.sigId = proxy.connect('g-properties-changed', () => {
                if (proxy.PlaybackStatus === 'Playing')
                    entry.lastActive = Date.now();
                this._sync();
            });
            this._sync();
        });
    }

    _removePlayer(name) {
        const entry = this._players.get(name);
        if (entry?.proxy && entry.sigId)
            entry.proxy.disconnect(entry.sigId);
        this._players.delete(name);
        this._sync();
    }

    _pick() {
        const usable = [...this._players.values()].filter(e => e.proxy && unpackMeta(e.proxy)['xesam:title']);
        const playing = usable.filter(e => e.proxy.PlaybackStatus === 'Playing');
        const pool = playing.length ? playing : usable;
        pool.sort((a, b) => b.lastActive - a.lastActive);
        return pool[0] ?? null;
    }

    _sync() {
        this._current = this._pick();
        if (!this._current || !this._enabled) {
            this.actor.hide();
            return;
        }
        const meta = unpackMeta(this._current.proxy);
        const artist = meta['xesam:artist'];
        this._title.text = String(meta['xesam:title'] ?? '');
        this._artist.text = Array.isArray(artist) ? artist.join(', ') : String(artist ?? meta['xesam:album'] ?? '');
        const playing = this._current.proxy.PlaybackStatus === 'Playing';
        this._play.child.icon_name = playing ? 'media-playback-pause-symbolic' : 'media-playback-start-symbolic';
        this._setCover(meta['mpris:artUrl']);
        this.actor.show();
    }

    _setCover(url) {
        if (url === this._coverUrl)
            return;
        this._coverUrl = url;
        const show = path => {
            this._cover.child = null;
            this._cover.style = `background-image: url("${path}"); background-size: cover;`;
        };
        const reset = () => {
            this._cover.style = null;
            this._cover.child = this._coverIcon;
        };
        if (!url) {
            reset();
            return;
        }
        if (url.startsWith('file://')) {
            show(GLib.filename_from_uri(url)[0]);
            return;
        }
        if (!/^https?:\/\//.test(url)) {
            reset();
            return;
        }
        const path = GLib.build_filenamev([COVER_DIR, GLib.compute_checksum_for_string(GLib.ChecksumType.SHA1, url, -1)]);
        if (GLib.file_test(path, GLib.FileTest.EXISTS)) {
            show(path);
            return;
        }
        reset();
        GLib.mkdir_with_parents(COVER_DIR, 0o755);
        const msg = Soup.Message.new('GET', url);
        if (!msg)
            return;
        this._session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (s, res) => {
            try {
                const bytes = s.send_and_read_finish(res);
                if (msg.get_status() !== 200)
                    return;
                GLib.file_set_contents(path, bytes.get_data());
                if (this._coverUrl === url && this.actor)
                    show(path);
            } catch (e) {
                console.warn(`swixraze-widgets: cover: ${e.message}`);
            }
        });
    }

    setConfig(cfg, locked = false) {
        this._enabled = locked || cfg.widgets.player;
        this._play.style = `background-color: ${cfg.accent};`;
        this._sync();
    }

    destroy() {
        Gio.DBus.session.signal_unsubscribe(this._ownerSub);
        for (const name of [...this._players.keys()])
            this._removePlayer(name);
        this.actor.destroy();
        this.actor = null;
    }
}

// ---------------------------------------------------------------- Devices
class DevicesCard {
    constructor() {
        this._devices = new Map(); // path -> {proxy, sigId}
        this._accent = DEFAULTS.accent;
        this.actor = new St.BoxLayout({style_class: 'sxr-card sxr-devices', width: CARD_WIDTH,
            orientation: Clutter.Orientation.VERTICAL});
        const header = new St.Label({text: 'Устройства', style_class: 'sxr-header'});
        this._list = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'sxr-device-list'});
        this.actor.add_child(header);
        this.actor.add_child(this._list);
        this.actor.hide();

        new UPowerProxy(Gio.DBus.system, 'org.freedesktop.UPower', '/org/freedesktop/UPower', (proxy, error) => {
            if (error || !this.actor)
                return;
            this._upower = proxy;
            this._addedId = proxy.connectSignal('DeviceAdded', (_p, _s, [path]) => this._add(path));
            this._removedId = proxy.connectSignal('DeviceRemoved', (_p, _s, [path]) => this._remove(path));
            proxy.EnumerateDevicesRemote((res, err) => { if (!err) res?.[0]?.forEach(p => this._add(p)); });
        });
    }

    _add(path) {
        if (this._devices.has(path) || !this.actor)
            return;
        const entry = {proxy: null, sigId: 0};
        this._devices.set(path, entry);
        new UPowerDeviceProxy(Gio.DBus.system, 'org.freedesktop.UPower', path, (proxy, error) => {
            if (error || this._devices.get(path) !== entry)
                return;
            entry.proxy = proxy;
            entry.sigId = proxy.connect('g-properties-changed', () => this._rebuild());
            this._rebuild();
        });
    }

    _remove(path) {
        const entry = this._devices.get(path);
        if (entry?.proxy && entry.sigId)
            entry.proxy.disconnect(entry.sigId);
        this._devices.delete(path);
        this._rebuild();
    }

    _rebuild() {
        if (!this.actor)
            return;
        this._list.destroy_all_children();
        const rows = [];
        for (const {proxy} of this._devices.values()) {
            if (!proxy || !proxy.IsPresent || proxy.Type === 1)
                continue;
            const laptop = proxy.Type === 2 && proxy.PowerSupply;
            const name = laptop ? 'Этот компьютер' : (proxy.Model || 'Устройство');
            const charging = proxy.State === 1;
            rows.push({name, pct: Math.round(proxy.Percentage), icon: TYPE_ICONS[proxy.Type] ?? 'bluetooth-active-symbolic', laptop, charging});
        }
        rows.sort((a, b) => (a.laptop - b.laptop) || a.name.localeCompare(b.name));
        for (const r of rows)
            this._list.add_child(this._row(r));
        this.actor.visible = this._enabled && rows.length > 0;
    }

    _row({name, pct, icon, charging}) {
        const row = new St.BoxLayout({style_class: 'sxr-device'});
        row.add_child(new St.Icon({icon_name: icon, icon_size: 18, style_class: 'sxr-device-icon'}));
        const col = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        const top = new St.BoxLayout();
        const label = new St.Label({text: name, x_expand: true, style_class: 'sxr-device-name'});
        label.clutter_text.ellipsize = 3;
        top.add_child(label);
        top.add_child(new St.Label({text: `${charging ? '⚡ ' : ''}${pct}%`, style_class: 'sxr-device-pct'}));
        const bar = new St.Widget({style_class: 'sxr-bar', x_expand: true});
        const fill = new St.Widget({style_class: 'sxr-bar-fill', style: `background-color: ${this._accent};`});
        bar.add_child(fill);
        bar.connect('notify::width', () => {
            fill.width = Math.max(4, Math.round(bar.width * pct / 100));
            fill.height = bar.height;
        });
        col.add_child(top);
        col.add_child(bar);
        row.add_child(col);
        return row;
    }

    setConfig(cfg, locked = false) {
        this._enabled = locked || cfg.widgets.devices;
        this._accent = cfg.accent;
        this._rebuild();
    }

    destroy() {
        if (this._upower) {
            this._upower.disconnectSignal(this._addedId);
            this._upower.disconnectSignal(this._removedId);
        }
        for (const path of [...this._devices.keys()])
            this._remove(path);
        this.actor.destroy();
        this.actor = null;
    }
}

// ---------------------------------------------------------------- System
function readText(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        return ok ? new TextDecoder().decode(bytes) : null;
    } catch {
        return null;
    }
}

function findTempSensor() {
    for (let i = 0; i < 16; i++) {
        const name = readText(`/sys/class/hwmon/hwmon${i}/name`)?.trim();
        if (name === 'coretemp' || name === 'k10temp' || name === 'zenpower')
            return `/sys/class/hwmon/hwmon${i}/temp1_input`;
    }
    return GLib.file_test('/sys/class/thermal/thermal_zone0/temp', GLib.FileTest.EXISTS)
        ? '/sys/class/thermal/thermal_zone0/temp' : null;
}

class BarRow {
    constructor(icon, title, accent) {
        this.pct = 0;
        this.actor = new St.BoxLayout({style_class: 'sxr-device'});
        this.actor.add_child(new St.Icon({gicon: icon, icon_size: 18, style_class: 'sxr-device-icon'}));
        const col = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        const top = new St.BoxLayout();
        top.add_child(new St.Label({text: title, x_expand: true, style_class: 'sxr-device-name'}));
        this._value = new St.Label({text: '…', style_class: 'sxr-device-pct'});
        top.add_child(this._value);
        this._bar = new St.Widget({style_class: 'sxr-bar', x_expand: true});
        this._fill = new St.Widget({style_class: 'sxr-bar-fill'});
        this._bar.add_child(this._fill);
        this._bar.connect('notify::width', () => this._layout());
        col.add_child(top);
        col.add_child(this._bar);
        this.actor.add_child(col);
        this.setAccent(accent);
    }

    setAccent(color) {
        this._fill.style = `background-color: ${color};`;
    }

    set(text, pct) {
        this._value.text = text;
        this.pct = Math.max(0, Math.min(100, pct));
        this._layout();
    }

    _layout() {
        this._fill.width = Math.max(4, Math.round(this._bar.width * this.pct / 100));
        this._fill.height = this._bar.height;
    }
}

class SystemCard {
    constructor(dir) {
        const icon = name => Gio.FileIcon.new(dir.get_child('icons').get_child(`${name}.svg`));
        this.actor = new St.BoxLayout({style_class: 'sxr-card sxr-system', width: CARD_WIDTH,
            orientation: Clutter.Orientation.VERTICAL});
        this.actor.add_child(new St.Label({text: 'Система', style_class: 'sxr-header'}));
        const list = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'sxr-device-list'});
        this._cpu = new BarRow(icon('sxr-cpu-symbolic'), 'Процессор', DEFAULTS.accent);
        this._ram = new BarRow(icon('sxr-memory-symbolic'), 'Память', DEFAULTS.accent);
        this._temp = new BarRow(icon('sxr-temp-symbolic'), 'Температура', DEFAULTS.accent);
        this._tempPath = findTempSensor();
        list.add_child(this._cpu.actor);
        list.add_child(this._ram.actor);
        if (this._tempPath)
            list.add_child(this._temp.actor);
        this.actor.add_child(list);
        this.actor.hide();
        this._prev = null;
        this._timer = 0;
    }

    _tick() {
        const stat = readText('/proc/stat')?.split('\n')[0].trim().split(/\s+/).slice(1).map(Number);
        if (stat) {
            const idle = stat[3] + (stat[4] || 0);
            const total = stat.reduce((a, b) => a + b, 0);
            if (this._prev) {
                const dt = total - this._prev.total;
                const pct = dt > 0 ? 100 * (1 - (idle - this._prev.idle) / dt) : 0;
                this._cpu.set(`${Math.round(pct)}%`, pct);
            }
            this._prev = {idle, total};
        }
        const mem = readText('/proc/meminfo');
        if (mem) {
            const get = k => Number(mem.match(new RegExp(`^${k}:\\s+(\\d+)`, 'm'))?.[1] ?? 0);
            const total = get('MemTotal');
            const used = total - get('MemAvailable');
            if (total)
                this._ram.set(`${(used / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(0)} ГБ`, 100 * used / total);
        }
        if (this._tempPath) {
            const t = Number(readText(this._tempPath)) / 1000;
            if (t)
                this._temp.set(`${Math.round(t)}°`, (t - 30) * 100 / 70);
        }
        return GLib.SOURCE_CONTINUE;
    }

    _setRunning(on) {
        if (on && !this._timer) {
            this._tick();
            this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => this._tick());
        } else if (!on && this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
            this._prev = null;
        }
    }

    setConfig(cfg, locked = false) {
        const show = locked || cfg.widgets.system;
        for (const r of [this._cpu, this._ram, this._temp])
            r.setAccent(cfg.accent);
        this.actor.visible = show;
        this._setRunning(show);
    }

    destroy() {
        this._setRunning(false);
        this.actor.destroy();
        this.actor = null;
    }
}

// ---------------------------------------------------------------- Extension
const CARD_ORDER = ['system', 'player', 'devices'];
const CARD_GAP = 12;

export default class SwixRazeWidgets extends Extension {
    enable() {
        this._session = new Soup.Session({timeout: 10});
        this._cards = {
            system: new SystemCard(this.dir),
            player: new PlayerCard(this._session),
            devices: new DevicesCard(),
        };
        // ряд для экрана блокировки
        this._row = new St.BoxLayout({style_class: 'sxr-column'});
        Main.layoutManager.screenShieldGroup.add_child(this._row);
        this._row.hide();

        for (const key of CARD_ORDER) {
            const actor = this._cards[key].actor;
            actor.y_align = Clutter.ActorAlign.FILL;
            Main.layoutManager._backgroundGroup.add_child(actor);
            actor.connect('notify::height', () => this._place());
            actor.connect('notify::visible', () => this._place());
        }
        this._row.connect('notify::width', () => this._place());
        this._row.connect('notify::height', () => this._place());
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._place());
        this._sessionId = Main.sessionMode.connect('updated', () => this._applyConfig());

        this._configFile = Gio.File.new_for_path(CONFIG_PATH);
        this._configMonitor = this._configFile.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, null);
        this._configMonitor.connect('changed', () => this._applyConfig());
        this._applyConfig();
    }

    _applyConfig() {
        if (!this._cards)
            return;
        this._cfg = readConfig();
        const isLocked = Main.sessionMode.isLocked;
        this._locked = isLocked && this._cfg.lockscreen !== false;

        // На экране блокировки карточки встают в ряд, на рабочем столе каждая стоит на своём месте.
        const parent = this._locked ? this._row : Main.layoutManager._backgroundGroup;
        for (const key of CARD_ORDER) {
            const actor = this._cards[key].actor;
            if (actor.get_parent() !== parent) {
                actor.get_parent()?.remove_child(actor);
                parent.add_child(actor);
            }
        }
        this._row.visible = this._locked;
        if (this._locked)
            Main.layoutManager.screenShieldGroup.set_child_above_sibling(this._row, null);

        for (const key of CARD_ORDER)
            this._cards[key].setConfig(this._cfg, this._locked);
        if (isLocked && !this._locked) {
            for (const key of CARD_ORDER)
                this._cards[key].actor.hide();
        }
        this._place();
    }

    _place() {
        const mon = Main.layoutManager.primaryMonitor;
        if (!mon || !this._cards || !this._cfg)
            return;
        if (this._locked) {
            const [, natW] = this._row.get_preferred_width(-1);
            const [, natH] = this._row.get_preferred_height(natW);
            this._row.set_position(Math.round(mon.x + (mon.width - natW) / 2), Math.round(mon.y + mon.height - natH - 72));
            return;
        }
        const positions = this._cfg.positions ?? {};
        const right = this._cfg.corner === 'bottom-right';
        // карточки без своего места складываются столбиком в углу, снизу вверх
        let bottom = mon.y + mon.height - DOCK_GAP;
        for (const key of [...CARD_ORDER].reverse()) {
            const actor = this._cards[key].actor;
            if (!actor.visible)
                continue;
            const [, w] = actor.get_preferred_width(-1);
            const [, h] = actor.get_preferred_height(w);
            const pos = positions[key];
            let x, y;
            if (pos && Number.isFinite(pos.x) && Number.isFinite(pos.y)) {
                x = mon.x + pos.x * mon.width;
                y = mon.y + pos.y * mon.height;
            } else {
                x = right ? mon.x + mon.width - w - 28 : mon.x + 28;
                y = bottom - h;
                bottom = y - CARD_GAP;
            }
            x = Math.max(mon.x, Math.min(x, mon.x + mon.width - w));
            y = Math.max(mon.y, Math.min(y, mon.y + mon.height - h));
            actor.set_position(Math.round(x), Math.round(y));
        }
    }

    disable() {
        this._configMonitor?.cancel();
        this._configMonitor = null;
        if (this._monitorsId)
            Main.layoutManager.disconnect(this._monitorsId);
        if (this._sessionId)
            Main.sessionMode.disconnect(this._sessionId);
        this._monitorsId = this._sessionId = 0;
        if (this._cards) {
            for (const key of CARD_ORDER)
                this._cards[key].destroy();
        }
        this._cards = null;
        this._row?.destroy();
        this._row = null;
        this._session?.abort();
        this._session = null;
    }
}
