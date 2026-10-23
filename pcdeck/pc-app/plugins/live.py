"""Live-Kacheln: Uhr, CPU, RAM, Lautstärke, Stoppuhr, Timer.

Nur Windows-Bordmittel (winsys.py, winsound). Die Bilder werden hier gezeichnet
(64x64 auf Kachelfarbe) und von der App nur bei Änderung ans Deck geschickt.
"""

import logging
import os
import threading
import time

from PIL import Image, ImageDraw, ImageFont

import plugin_api
import winsys
from iconextract import ICON_SZ, TILE_BG
from plugin_api import Field, LiveTileType

log = logging.getLogger("pcdeck")

WHITE = (235, 235, 240)
DIM = (130, 135, 150)
GREEN = (80, 200, 120)
YELLOW = (240, 190, 60)
RED = (235, 80, 80)
BLUE = (80, 150, 255)
TRACK = (70, 75, 88)

WOCHENTAG = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype("segoeuib.ttf" if bold else "segoeui.ttf", size)
        except OSError:
            _fonts[key] = ImageFont.load_default(size)
    return _fonts[key]


def canvas():
    img = Image.new("RGB", (ICON_SZ, ICON_SZ), TILE_BG)
    return img, ImageDraw.Draw(img)


def fit_text(d, text, max_w, size, bold=True, min_size=10):
    """Größte Schrift (bis size), mit der text in max_w passt."""
    while size > min_size and d.textlength(text, font=font(size, bold)) > max_w:
        size -= 1
    return font(size, bold)


def big_text(text, color=WHITE, sub=None, sub_color=DIM):
    """Großer Text in der Mitte, optional kleine Zeile darunter."""
    img, d = canvas()
    f = fit_text(d, text, ICON_SZ - 4, 30)
    y = 26 if sub else ICON_SZ // 2
    d.text((ICON_SZ // 2, y), text, font=f, fill=color, anchor="mm")
    if sub:
        d.text((ICON_SZ // 2, 52), sub, font=fit_text(d, sub, ICON_SZ - 4, 13, bold=False), fill=sub_color,
               anchor="mm")
    return img


def load_color(pct):
    return GREEN if pct < 60 else YELLOW if pct < 85 else RED


def gauge(pct, text=None):
    """Ring-Anzeige mit Prozentzahl in der Mitte."""
    img, d = canvas()
    box = (4, 4, ICON_SZ - 5, ICON_SZ - 5)
    d.arc(box, 135, 405, fill=TRACK, width=7)
    if pct is not None and pct > 0:
        d.arc(box, 135, 135 + 270 * min(pct, 100) / 100, fill=load_color(pct), width=7)
    t = text if text is not None else ("--" if pct is None else f"{pct}%")
    d.text((ICON_SZ // 2, ICON_SZ // 2 + 2), t, font=fit_text(d, t, 40, 18), fill=WHITE, anchor="mm")
    return img


def fmt_duration(sec):
    sec = int(max(0, sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def open_taskmanager():
    try:
        os.startfile("taskmgr.exe")
    except OSError as e:
        log.error("Task-Manager konnte nicht geöffnet werden: %s", e)


# ======================================================================= Uhr
class Clock(LiveTileType):
    id = "clock"
    name = "Uhr / Datum"
    fields = [Field("format", "Anzeige:", kind="choice", default="hm",
                    choices=[("hm", "Uhrzeit (14:05)"), ("hms", "Uhrzeit mit Sekunden (14:05:09)"),
                             ("date", "Datum (20.10.)")], width=34)]

    def poll(self, tile, ctx):
        now = time.localtime()
        fmt = tile.get("format", "hm")
        if fmt == "hms":
            return time.strftime("%H:%M:%S", now), self._date(now)
        if fmt == "date":
            return time.strftime("%d.%m.", now), WOCHENTAG[now.tm_wday] + time.strftime(" %Y", now)
        return time.strftime("%H:%M", now), self._date(now)

    @staticmethod
    def _date(now):
        return f"{WOCHENTAG[now.tm_wday]} {now.tm_mday}.{now.tm_mon}."

    def render(self, tile, state, ctx):
        if state is None:
            return big_text("12:00"), "Uhr"
        main, sub = state
        return big_text(main), sub


# ======================================================================= CPU / RAM
class Cpu(LiveTileType):
    id = "cpu"
    name = "CPU-Auslastung"

    def poll(self, tile, ctx):
        return winsys.cpu_percent()

    def render(self, tile, state, ctx):
        return gauge(state), "CPU"

    def on_press(self, tile, ctx):
        open_taskmanager()


class Ram(LiveTileType):
    id = "ram"
    name = "RAM-Auslastung"

    def poll(self, tile, ctx):
        pct, used, total = winsys.ram()
        return pct, round(used, 1), round(total)

    def render(self, tile, state, ctx):
        if state is None:
            return gauge(None), "RAM"
        pct, used, total = state
        return gauge(pct), f"RAM {used:.1f}/{total} GB"

    def on_press(self, tile, ctx):
        open_taskmanager()


# ======================================================================= Lautstärke
class Volume(LiveTileType):
    id = "volume"
    name = "Lautstärke (antippen = stumm)"

    def poll(self, tile, ctx):
        return winsys.volume()

    def render(self, tile, state, ctx):
        img, d = canvas()
        # Lautsprecher-Symbol
        d.polygon([(10, 24), (19, 24), (30, 14), (30, 50), (19, 40), (10, 40)], fill=WHITE)
        if state is None:
            d.text((47, 32), "?", font=font(22, True), fill=DIM, anchor="mm")
            return img, "Lautstaerke"
        level, muted = state
        if muted:
            d.line((37, 22, 57, 42), fill=RED, width=5)
            d.line((57, 22, 37, 42), fill=RED, width=5)
            return img, "stumm"
        for k, r in enumerate((8, 15, 22)):
            if level > k * 33:
                d.arc((30 - r, 32 - r, 30 + r, 32 + r), -45, 45, fill=BLUE, width=3)
        return img, f"{level}%"

    def on_press(self, tile, ctx):
        winsys.toggle_mute()


# ======================================================================= Stoppuhr / Timer
_state_lock = threading.Lock()
_states = {}  # ctx.key -> dict


def _st(ctx, **default):
    with _state_lock:
        return _states.setdefault((ctx.key if ctx else "?"), dict(default))


class Stopwatch(LiveTileType):
    id = "stopwatch"
    name = "Stoppuhr (Start / Stopp / Zurücksetzen)"

    def poll(self, tile, ctx):
        s = _st(ctx, running=False, start=0.0, acc=0.0)
        elapsed = s["acc"] + (time.monotonic() - s["start"] if s["running"] else 0)
        return s["running"], int(elapsed)

    def render(self, tile, state, ctx):
        running, sec = state if state else (False, 0)
        color = GREEN if running else (WHITE if sec else DIM)
        return big_text(fmt_duration(sec), color), ("laeuft" if running else "Stoppuhr")

    def on_press(self, tile, ctx):
        s = _st(ctx, running=False, start=0.0, acc=0.0)
        with _state_lock:
            if s["running"]:                       # laufend -> anhalten
                s["acc"] += time.monotonic() - s["start"]
                s["running"] = False
            elif s["acc"] > 0:                     # angehalten -> zurücksetzen
                s["acc"] = 0.0
            else:                                  # auf 0 -> starten
                s["start"] = time.monotonic()
                s["running"] = True


class Timer(LiveTileType):
    id = "timer"
    name = "Timer (Countdown)"
    fields = [Field("minutes", "Dauer in Minuten (z. B. 5 oder 0.5):", default="5", width=10)]

    def _duration(self, tile):
        try:
            return max(1, round(float(str(tile.get("minutes", "5")).replace(",", ".")) * 60))
        except ValueError:
            return 300

    def poll(self, tile, ctx):
        s = _st(ctx, end=None)
        if s["end"] is None:
            return "idle", self._duration(tile), 0
        left = s["end"] - time.monotonic()
        if left > 0:
            return "run", int(left + 0.999), 0
        if not s.get("beeped"):
            s["beeped"] = True
            threading.Thread(target=_alarm, daemon=True).start()
        return "done", 0, int(time.monotonic() * 2) % 2  # blinken

    def render(self, tile, state, ctx):
        if state is None:
            return big_text(fmt_duration(self._duration(tile)), DIM), "Timer"
        mode, sec, blink = state
        if mode == "done":
            return big_text("00:00", RED if blink else WHITE), "Fertig!"
        if mode == "run":
            return big_text(fmt_duration(sec), GREEN), "Timer laeuft"
        return big_text(fmt_duration(sec), WHITE), "Timer"

    def on_press(self, tile, ctx):
        s = _st(ctx, end=None)
        with _state_lock:
            if s["end"] is None:                   # starten
                s["end"] = time.monotonic() + self._duration(tile)
                s["beeped"] = False
            else:                                  # laufend oder fertig -> zurücksetzen
                s["end"] = None


def _alarm():
    import winsound
    for _ in range(3):
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        time.sleep(0.6)


for _t in (Clock(), Cpu(), Ram(), Volume(), Stopwatch(), Timer()):
    plugin_api.register(_t)
