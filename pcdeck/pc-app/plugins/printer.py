"""Kachel-Typ "printer": Klipper-3D-Drucker über Moonraker (moonraker.py), eine Funktion je Kachel (tile["func"]).

Was angeboten wird, liest available() aus dem Drucker (Heizungen, Sensoren, Makros …).
Einstellbar (kurz tippen = Drehknopf übernimmt 8 s, blauer Rahmen): Drehen ändert nur das Ziel (blau),
Drehknopf drücken bestätigt und sendet; ohne Bestätigung verfällt das Ziel nach GRAB_SECONDS.
  bed / nozzle  Solltemperatur ±5 °C (bis max_temp der printer.cfg), lang tippen = Heizung aus
  zoff          Z-Versatz ±0,01 mm (SET_GCODE_OFFSET Z_ADJUST=… MOVE=1 wenn Z gehomt, Grenze ±Z_LIMIT)
  speed / flow  M220 ±5 % / M221 ±1 %, lang tippen = 100 %
  retract       SET_RETRACTION RETRACT_LENGTH ±0,1 mm (braucht [firmware_retraction])
  fan           Bauteillüfter ±5 % (M106), lang tippen = aus
Anzeige: progress (Druckfortschritt, Restzeit), sensor:<objekt> (Temperatur).
Befehle (nur lang tippen): home, homez, ztilt, qgl, mesh, motors, macro:<NAME> – während eines Drucks gesperrt
(außer Makros SET_PAUSE…); pause (nur im Druck), resume (nur pausiert), cancel (danach Drehknopf drücken = Ja).
"""

import logging
import threading
import time

import moonraker
import plugin_api
from plugin_api import Field, LiveTileType
from plugins.live import BLUE, DIM, GREEN, RED, TRACK, WHITE, YELLOW, canvas, fit_text

log = logging.getLogger("pcdeck")

Z_LIMIT = 2.0
GRAB_SECONDS = 8
_last_url = [moonraker.DEFAULT_URL]  # für die Auswahl im Kachel-Editor


def _get(st, obj, field, scale=1.0):
    v = (st.get(obj) or {}).get(field)
    return None if v is None else float(v) * scale


# Funktion -> (Name, Schritt, min, max, Ist-Wert(st), G-Code(neu, st))
def _zoff(st):
    o = (st.get("gcode_move") or {}).get("homing_origin") or []
    return float(o[2]) if len(o) > 2 else None


ADJUST = {
    "bed": ("Bett", 5, 0, 110, lambda st: _get(st, "heater_bed", "target"),
            lambda v, st: f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={round(v)}"),
    "nozzle": ("Düse", 5, 0, 280, lambda st: _get(st, "extruder", "target"),
               lambda v, st: f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={round(v)}"),
    "zoff": ("Z-Versatz", 0.01, -Z_LIMIT, Z_LIMIT, _zoff, None),  # Skript in _send_value (Differenz)
    "speed": ("Speed", 5, 10, 300, lambda st: _get(st, "gcode_move", "speed_factor", 100),
              lambda v, st: f"M220 S{round(v)}"),
    "flow": ("Flow", 1, 50, 150, lambda st: _get(st, "gcode_move", "extrude_factor", 100),
             lambda v, st: f"M221 S{round(v)}"),
    "retract": ("Retract", 0.1, 0, 10, lambda st: _get(st, "firmware_retraction", "retract_length"),
                lambda v, st: f"SET_RETRACTION RETRACT_LENGTH={v:.2f}"),
    "fan": ("Lüfter", 5, 0, 100, lambda st: _get(st, "fan", "speed", 100),
            lambda v, st: f"M106 S{round(v * 2.55)}" if v > 0 else "M107"),
}
HEATER = {"bed": "heater_bed", "nozzle": "extruder"}
# lang tippen auf eine einstellbare Kachel: (G-Code, Meldung)
RESET = {"bed": ("SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=0", "Bett: Heizung aus"),
         "nozzle": ("SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0", "Düse: Heizung aus"),
         "speed": ("M220 S100", "Speed: 100 %"), "flow": ("M221 S100", "Flow: 100 %"),
         "fan": ("M107", "Lüfter: aus")}
# Befehl -> (Name, G-Code, Meldung beim Ausführen)
COMMANDS = {
    "home": ("Home XYZ", "G28", "Homing"),
    "homez": ("Home Z", "G28 Z", "Homing Z"),
    "ztilt": ("Z-Tilt", "Z_TILT_ADJUST", "Z-Tilt"),
    "qgl": ("QGL", "QUAD_GANTRY_LEVEL", "Gantry-Level"),
    "mesh": ("Bed-Mesh", "BED_MESH_CALIBRATE", "Bed-Mesh"),
    "motors": ("Motoren aus", "M84", "Motoren aus"),
    "pause": ("Pause", "PAUSE", "Pause"),
    "resume": ("Fortsetzen", "RESUME", "Fortsetzen"),
    "cancel": ("Abbrechen", "CANCEL_PRINT", "Abbrechen"),
}
PRINT_CONTROL = ("pause", "resume", "cancel")
NEEDS_HOMING = ("ztilt", "qgl", "mesh")  # ohne Homing vorher G28
MACRO_SKIP = ("PAUSE", "RESUME", "CANCEL_PRINT")  # schon als eigene Kacheln da
# feste Angebote: (Funktion, Anzeige, Gruppe, benötigtes Klipper-Objekt oder None)
STATIC = [
    ("bed", "Bett-Temperatur (Ist/Soll)", "Temperatur", "heater_bed"),
    ("nozzle", "Düsen-Temperatur (Ist/Soll)", "Temperatur", "extruder"),
    ("zoff", "Z-Versatz", "Einstellen", "gcode_move"),
    ("speed", "Geschwindigkeit (M220)", "Einstellen", "gcode_move"),
    ("flow", "Flow (M221)", "Einstellen", "gcode_move"),
    ("retract", "Retract-Länge", "Einstellen", "firmware_retraction"),
    ("fan", "Bauteillüfter", "Einstellen", "fan"),
    ("progress", "Druckfortschritt", "Anzeige", "print_stats"),
    ("home", "Homing alle Achsen (G28)", "Befehl", None),
    ("homez", "Homing Z (G28 Z)", "Befehl", None),
    ("ztilt", "Z_TILT_ADJUST", "Befehl", "z_tilt"),
    ("qgl", "QUAD_GANTRY_LEVEL", "Befehl", "quad_gantry_level"),
    ("mesh", "BED_MESH_CALIBRATE", "Befehl", "bed_mesh"),
    ("motors", "Motoren aus (M84)", "Befehl", None),
    ("pause", "Druck pausieren", "Druck", "pause_resume"),
    ("resume", "Druck fortsetzen", "Druck", "pause_resume"),
    ("cancel", "Druck abbrechen", "Druck", "pause_resume"),
]
OFFLINE_FUNCS = [(f, n, g) for f, n, g, need in STATIC if need in (None, "heater_bed", "extruder", "gcode_move")]

_target = {}   # (url, func) -> (Ziel, Zeit)
_pending = {}  # (url, func) -> Timer: Ziel verfällt ohne Bestätigung
_confirm = {}  # url -> Zeit, seit der „Abbrechen?“ auf den Drehknopf wartet
_busy = {}     # url -> Text des laufenden Befehls
_lock = threading.Lock()
_rot_lock = threading.Lock()


def split(func):
    kind, _, arg = (func or "").partition(":")
    return kind, arg


def available(url):
    """Was der Drucker anbietet: [(funktion, anzeige, gruppe)]. Wirft moonraker.PrinterError."""
    names, _ = moonraker.objects(url, max_age=0)
    have = set(names)
    out = [(f, n, g) for f, n, g, need in STATIC if need is None or need in have]
    for o in sorted(names):
        if o.startswith(moonraker.SENSOR_PREFIXES):
            out.append((f"sensor:{o}", f"Temperatur {func_name('sensor:' + o)}", "Anzeige"))
    for o in sorted(names, key=str.lower):
        if o.startswith("gcode_macro "):
            m = o.split(" ", 1)[1]
            if not m.startswith("_") and m.upper() not in MACRO_SKIP:
                out.append((f"macro:{m}", f"Makro {m}", "Makro"))
    return out


def func_name(func):
    kind, arg = split(func)
    if kind == "sensor":
        typ, _, n = arg.partition(" ")
        return f"{n} (Host)" if typ == "temperature_host" else n
    if kind == "macro":
        return arg
    if kind in ADJUST:
        return ADJUST[kind][0]
    if kind in COMMANDS:
        return COMMANDS[kind][0]
    return {"progress": "Druck"}.get(kind, "Drucker")


def _choices():
    try:
        items = available(_last_url[0])
    except moonraker.PrinterError:
        items = OFFLINE_FUNCS
    return [(f, f"{g}: {n}") for f, n, g in items]


def _limits(url, func):
    name, step, lo, hi = ADJUST[func][:4]
    if func in HEATER:
        try:
            hi = min(hi, moonraker.objects(url)[1].get(HEATER[func], hi))
        except moonraker.PrinterError:
            pass
    return name, step, lo, hi


def _fmt(func, v):
    if v is None:
        return "n. a."
    if func in HEATER:
        return f"{round(v)}°"
    if func == "zoff":
        return f"{v:+.3f}"
    if func in ("speed", "flow", "fan"):
        return f"{round(v)}%"
    return f"{v:.1f}mm"


def _osd_text(func, v):
    name = ADJUST[func][0]
    if func in HEATER:
        return f"{name}: {'aus' if not v else f'{round(v)} °C'}"
    if func == "zoff":
        return f"{name}: {v:+.3f} mm"
    if func in ("speed", "flow", "fan"):
        return f"{name}: {round(v)} %"
    return f"{name}: {v:.1f} mm"


def _osd_pct(url, func, v):
    _, _, lo, hi = _limits(url, func)
    return None if v is None else max(0, min(100, round((v - lo) / (hi - lo) * 100)))


def _hms(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec // 60 % 60:02d}" if sec >= 3600 else f"{sec // 60} min"


class Printer(LiveTileType):
    id = "printer"
    name = "3D-Drucker (Klipper/Moonraker)"
    fields = [Field("func", "Funktion:", kind="choice", choices=_choices, default="bed", width=50),
              Field("url", "Moonraker-Adresse:", default=moonraker.DEFAULT_URL)]
    rotatable = True

    def is_valid(self, tile):
        kind, arg = split(tile.get("func"))
        return kind in ADJUST or kind in COMMANDS or kind == "progress" or (kind in ("sensor", "macro") and arg)

    @staticmethod
    def _url(tile):
        return tile.get("url") or moonraker.DEFAULT_URL

    # -- Anzeige
    def poll(self, tile, ctx):
        url = self._url(tile)
        kind, arg = split(tile.get("func"))
        grabbed = bool(ctx and ctx.link and ctx.link.knob_grabbed(ctx.page, ctx.idx))
        try:
            st = moonraker.query(url)
        except moonraker.PrinterError:
            return ("offline",)
        state = moonraker.state(st)
        if kind in ADJUST:
            tgt = self._fresh_target(url, kind)
            v = ADJUST[kind][4](st)
            if kind in HEATER:
                cur = _get(st, HEATER[kind], "temperature")
                return ("ok", cur and round(cur), v, tgt, grabbed)
            return ("ok", v, tgt, grabbed)
        if kind == "sensor":
            t = _get(st, arg, "temperature")
            return ("ok", None if t is None else round(t, 1))
        if kind == "progress":
            ps, ds = st.get("print_stats") or {}, st.get("display_status") or {}
            prog = float(ds.get("progress") or (st.get("virtual_sdcard") or {}).get("progress") or 0)
            dur = float(ps.get("print_duration") or 0)
            left = dur / prog - dur if prog > 0.01 and dur > 0 else None
            return ("ok", state, round(prog * 100), left and round(left / 60), ps.get("filename") or "")
        busy = _busy.get(moonraker.base(url))
        allowed = self._allowed(kind, arg, state)
        confirm = kind == "cancel" and self._confirm_open(url)
        return ("ok", allowed, bool(busy) and busy == self._busy_text(kind, arg), confirm)

    def render(self, tile, state, ctx):
        kind, arg = split(tile.get("func"))
        img, d = canvas()
        offline = bool(state) and state[0] == "offline"
        ok = bool(state) and not offline
        name = func_name(tile.get("func"))
        if kind in ADJUST:
            return self._render_adjust(img, d, kind, state, ok, offline), name
        if kind == "sensor":
            t = "?" if offline else ("-" if not ok or state[1] is None else f"{state[1]:.1f}°")
            d.text((32, 14), "Temp.", font=fit_text(d, "Temp.", 56, 13), fill=DIM, anchor="mm")
            d.text((32, 40), t, font=fit_text(d, t, 58, 20), fill=DIM if offline else WHITE, anchor="mm")
            return img, name
        if kind == "progress":
            st, pct, left = (state[1], state[2], state[3]) if ok else ("", 0, None)
            active = st in ("printing", "paused")
            col = DIM if offline or not active else (YELLOW if st == "paused" else GREEN)
            d.rounded_rectangle((6, 30, 58, 40), 4, outline=TRACK, width=2)
            if active and pct:
                d.rounded_rectangle((8, 32, 8 + max(2, round(48 * pct / 100)), 38), 3, fill=col)
            top = "?" if offline else (f"{pct}%" if active else {"complete": "fertig", "cancelled": "abgebr.",
                                                                  "error": "Fehler"}.get(st, "bereit"))
            d.text((32, 15), top, font=fit_text(d, top, 58, 20), fill=col if active else WHITE, anchor="mm")
            sub = ("Pause" if st == "paused" else (f"noch {_hms(left * 60)}" if left else "")) if active else ""
            if sub:
                d.text((32, 53), sub, font=fit_text(d, sub, 58, 13), fill=DIM, anchor="mm")
            return img, name
        return self._render_command(img, d, kind, arg, state, ok, offline), name

    @staticmethod
    def _render_adjust(img, d, kind, state, ok, offline):
        grabbed = ok and state[-1]
        if grabbed:
            d.rounded_rectangle((1, 1, 62, 62), 9, outline=BLUE, width=3)
        if kind in HEATER:
            cur, tgt, pend = (state[1], state[2], state[3]) if ok else (None, None, None)
            heating = bool(tgt)
            col = DIM if offline else (RED if heating and cur is not None and cur < tgt - 2 else
                                       (GREEN if heating else WHITE))
            t = "?" if offline or cur is None else f"{cur}°"
            d.text((32, 22), t, font=fit_text(d, t, 56, 24), fill=col, anchor="mm")
            shown = pend if pend is not None else tgt
            sub = "?" if offline else ("aus" if not shown else f"→ {round(shown)}°")
            d.line((10, 40, 54, 40), fill=TRACK, width=2)
            d.text((32, 52), sub, font=fit_text(d, sub, 56, 15), fill=BLUE if pend is not None else DIM,
                   anchor="mm")
            return img
        val, pend = (state[1], state[2]) if ok else (None, None)
        shown = pend if pend is not None else val
        t = "?" if offline else _fmt(kind, shown)
        head = {"zoff": "Z-Offset"}.get(kind, ADJUST[kind][0])
        d.text((32, 16), head, font=fit_text(d, head, 56, 14), fill=DIM, anchor="mm")
        d.text((32, 42), t, font=fit_text(d, t, 58, 20),
               fill=DIM if offline else (BLUE if pend is not None else WHITE), anchor="mm")
        return img

    @staticmethod
    def _render_command(img, d, kind, arg, state, ok, offline):
        allowed, running, confirm = (state[1], state[2], state[3]) if ok else (True, False, False)
        color = DIM if offline or not allowed else (YELLOW if running else WHITE)
        acc = DIM if offline or not allowed else BLUE
        if confirm:
            d.rounded_rectangle((1, 1, 62, 62), 9, outline=RED, width=3)
        if kind in ("home", "homez"):
            d.polygon((32, 6, 10, 26, 54, 26), outline=color, width=3)
            d.rectangle((16, 26, 48, 44), outline=color, width=3)
            t = "XYZ" if kind == "home" else "Z"
            d.text((32, 35), t, font=fit_text(d, t, 28, 14), fill=color, anchor="mm")
        elif kind in ("ztilt", "qgl"):
            d.line((10, 30, 54, 22), fill=color, width=4)
            for x, y in ((14, 44), (50, 36)):
                d.line((x, y, x, y - 8), fill=acc, width=3)
                d.polygon((x - 4, y - 7, x + 4, y - 7, x, y - 12), fill=acc)
        elif kind == "mesh":
            for i in range(4):
                d.line((12 + i * 13, 8, 12 + i * 13, 44), fill=color, width=2)
                d.line((12, 8 + i * 12, 51, 8 + i * 12), fill=color, width=2)
        elif kind == "pause":
            d.rectangle((20, 10, 28, 42), fill=color)
            d.rectangle((36, 10, 44, 42), fill=color)
        elif kind == "resume":
            d.polygon((22, 8, 22, 44, 48, 26), fill=GREEN if color == WHITE else color)
        elif kind == "cancel":
            d.rectangle((18, 10, 46, 40), fill=RED if color == WHITE else color)
        else:  # motors, macro
            t = "M84" if kind == "motors" else arg
            d.text((32, 25), t, font=fit_text(d, t, 58, 18, min_size=8), fill=color, anchor="mm")
        if offline:
            text = "?"
        elif confirm:
            text = "Knopf=Ja"
        elif running:
            text = "läuft"
        elif not allowed:
            text = {"pause": "kein Druck", "resume": "-", "cancel": "kein Druck"}.get(kind, "Druck")
        else:
            text = "lang"
        d.text((32, 55), text, font=fit_text(d, text, 58, 13), fill=RED if confirm else color, anchor="mm")
        return img

    # -- Hilfen
    @staticmethod
    def _busy_text(kind, arg):
        return arg if kind == "macro" else COMMANDS.get(kind, ("", "", ""))[2]

    @staticmethod
    def _allowed(kind, arg, state):
        printing = state in ("printing", "paused")
        if kind == "pause":
            return state == "printing"
        if kind == "resume":
            return state == "paused"
        if kind == "cancel":
            return printing
        if kind == "macro" and arg.upper().startswith("SET_PAUSE"):
            return True
        return not printing

    def _osd(self, ctx, pct, text):
        if ctx and ctx.link:
            ctx.link.request_osd(pct, text)

    def _refresh(self, ctx, delay=0.3):
        if ctx and ctx.link:
            ctx.link.request_live_update(delay)

    def _send(self, tile, ctx, script, done=None):
        try:
            moonraker.gcode(self._url(tile), script, timeout=30)
        except moonraker.PrinterError as e:
            log.error("Drucker: %s: %s", script, e)
            self._osd(ctx, None, f"Drucker: {str(e)[:40]}")
            return False
        if done:
            self._osd(ctx, None, done)
        self._refresh(ctx)
        return True

    def _fresh_target(self, url, func):
        tgt, t = _target.get((moonraker.base(url), func), (None, 0))
        return tgt if tgt is not None and time.monotonic() - t <= GRAB_SECONDS else None

    def _confirm_open(self, url):
        return time.monotonic() - _confirm.get(moonraker.base(url), 0) <= GRAB_SECONDS

    def _cancel_target(self, url, func):
        key = (moonraker.base(url), func)
        with _lock:
            timer = _pending.pop(key, None)
            _target.pop(key, None)
        if timer:
            timer.cancel()

    # -- Bedienen
    def on_press(self, tile, ctx):
        url = self._url(tile)
        kind, arg = split(tile.get("func"))
        if kind in COMMANDS or kind == "macro":
            self._osd(ctx, None, f"{func_name(tile.get('func'))}: lang drücken zum Starten")
            return
        if kind not in ADJUST or not (ctx and ctx.link):
            return
        ctx.link.grab_knob(ctx.page, ctx.idx)
        self._cancel_target(url, kind)
        self._refresh(ctx, 0)
        osd = self.knob_osd(tile)
        if osd:
            self._osd(ctx, *osd)

    def on_long_press(self, tile, ctx):
        url = self._url(tile)
        kind, arg = split(tile.get("func"))
        if kind in RESET:
            self._cancel_target(url, kind)
            self._send(tile, ctx, *RESET[kind])
        elif kind in ADJUST:
            self.on_press(tile, ctx)
        elif kind == "cancel":
            self._ask_cancel(tile, ctx)
        elif kind in COMMANDS or kind == "macro":
            threading.Thread(target=self._command, args=(tile, ctx, kind, arg), daemon=True).start()

    def _ask_cancel(self, tile, ctx):
        url = self._url(tile)
        try:
            st = moonraker.query(url, max_age=0)
        except moonraker.PrinterError as e:
            self._osd(ctx, None, f"Drucker: {str(e)[:40]}")
            return
        if not moonraker.printing(st):
            self._osd(ctx, None, "Abbrechen: kein Druck aktiv")
            return
        _confirm[moonraker.base(url)] = time.monotonic()
        if ctx and ctx.link:
            ctx.link.grab_knob(ctx.page, ctx.idx, GRAB_SECONDS)
        self._osd(ctx, None, "Druck abbrechen? Drehknopf druecken = Ja")
        self._refresh(ctx, 0)

    def _command(self, tile, ctx, kind, arg):
        url = self._url(tile)
        name = func_name(tile.get("func"))
        script = arg if kind == "macro" else COMMANDS[kind][1]
        busy = self._busy_text(kind, arg)
        try:
            st = moonraker.query(url, max_age=0)
        except moonraker.PrinterError as e:
            self._osd(ctx, None, f"Drucker: {str(e)[:40]}")
            return
        state = moonraker.state(st)
        if not self._allowed(kind, arg, state):
            msg = {"pause": "kein Druck aktiv", "resume": "nicht pausiert"}.get(kind, "gesperrt, Druck läuft")
            self._osd(ctx, None, f"{name}: {msg}")
            return
        key = moonraker.base(url)
        with _lock:
            if _busy.get(key):
                self._osd(ctx, None, f"Drucker: {_busy[key]} läuft noch")
                return
            _busy[key] = busy
        homed = (st.get("toolhead") or {}).get("homed_axes") or ""
        if kind in NEEDS_HOMING and not all(a in homed for a in "xyz"):
            script = "G28\n" + script
        self._osd(ctx, None, f"{busy} ...")
        self._refresh(ctx, 0)
        log.info("Drucker: %s", script.replace("\n", " / "))
        try:
            moonraker.gcode(url, script, timeout=900)
            self._osd(ctx, None, f"{busy}: fertig")
        except moonraker.PrinterError as e:
            log.error("Drucker: %s: %s", script, e)
            self._osd(ctx, None, f"{busy}: {str(e)[:36]}")
        finally:
            with _lock:
                _busy.pop(key, None)
            self._refresh(ctx)

    def knob_osd(self, tile):
        url = self._url(tile)
        kind, _ = split(tile.get("func"))
        if kind == "cancel" and self._confirm_open(url):
            return None, "Druck abbrechen? Drehknopf druecken = Ja"
        if kind not in ADJUST:
            return None
        v = self._fresh_target(url, kind)
        if v is not None:
            return _osd_pct(url, kind, v), _osd_text(kind, v) + " - drücken = OK"
        try:
            v = ADJUST[kind][4](moonraker.query(url))
        except moonraker.PrinterError:
            return None, "Drucker nicht erreichbar"
        if v is None:
            return None, f"{ADJUST[kind][0]}: nicht eingerichtet"
        return _osd_pct(url, kind, v), _osd_text(kind, v)

    def on_knob_press(self, tile, ctx):
        """Bestätigen: das gedrehte Ziel senden bzw. „Druck abbrechen?“ mit Ja beantworten."""
        url = self._url(tile)
        kind, _ = split(tile.get("func"))
        if kind == "cancel":
            if self._confirm_open(url):
                _confirm.pop(moonraker.base(url), None)
                if ctx and ctx.link:
                    ctx.link.knob_grab = None
                log.info("Drucker: CANCEL_PRINT")
                self._send(tile, ctx, "CANCEL_PRINT", "Druck abgebrochen")
            return
        if kind not in ADJUST:
            return
        key = (moonraker.base(url), kind)
        with _lock:
            new = self._fresh_target(url, kind)
            timer = _pending.pop(key, None)
            _target.pop(key, None)
        if timer:
            timer.cancel()
        if new is None:
            osd = self.knob_osd(tile)
            if osd:
                self._osd(ctx, *osd)
            return
        self._send_value(tile, ctx, kind, new)

    def on_rotate(self, tile, ctx, steps):
        url = self._url(tile)
        kind, _ = split(tile.get("func"))
        if kind not in ADJUST:
            return
        name, step, lo, hi = _limits(url, kind)
        key = (moonraker.base(url), kind)
        with _rot_lock:
            tgt = self._fresh_target(url, kind)
            if tgt is None:
                try:
                    tgt = ADJUST[kind][4](moonraker.query(url))
                except moonraker.PrinterError:
                    self._osd(ctx, None, "Drucker nicht erreichbar")
                    return
                if tgt is None:
                    self._osd(ctx, None, f"{name}: nicht eingerichtet")
                    return
            base = tgt if kind == "zoff" else round(tgt / step) * step  # Z-Versatz: genau vom Ist-Wert aus
            new = max(lo, min(hi, round(base + steps * step, 3)))
            with _lock:
                _target[key] = (new, time.monotonic())
                old = _pending.pop(key, None)
                timer = threading.Timer(GRAB_SECONDS + 0.1, self._expire, args=(tile, ctx, key))
                timer.daemon = True
                _pending[key] = timer
            if old:
                old.cancel()
            timer.start()
        self._osd(ctx, _osd_pct(url, kind, new), _osd_text(kind, new) + " - drücken = OK")
        self._refresh(ctx, 0)

    def _expire(self, tile, ctx, key):
        """Ziel nicht bestätigt: verwerfen, Kachel zeigt wieder den Ist-Wert."""
        with _lock:
            if _pending.get(key) is not threading.current_thread():
                return  # inzwischen weitergedreht, bestätigt oder neu angetippt
            _pending.pop(key, None)
            _target.pop(key, None)
        self._osd(ctx, None, f"{ADJUST[key[1]][0]}: verworfen")
        self._refresh(ctx, 0)

    def _send_value(self, tile, ctx, kind, new):
        url = self._url(tile)
        try:
            st = moonraker.query(url, max_age=0)
        except moonraker.PrinterError as e:
            self._osd(ctx, None, f"Drucker: {str(e)[:40]}")
            return
        if kind == "zoff":
            delta = round(new - (_zoff(st) or 0), 3)
            if not delta:
                return
            homed = (st.get("toolhead") or {}).get("homed_axes") or ""
            script = f"SET_GCODE_OFFSET Z_ADJUST={delta:+.3f} MOVE={1 if 'z' in homed else 0}"
        else:
            script = ADJUST[kind][5](new, st)
        log.info("Drucker: %s", script)
        self._send(tile, ctx, script, _osd_text(kind, new) + " gesetzt")


plugin_api.register(Printer())
