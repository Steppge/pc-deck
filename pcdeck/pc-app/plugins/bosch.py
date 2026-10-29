"""Kachel-Typ "bosch": Bosch Smart Home – Rollladen und Licht, lokal über den Controller (boschshc).

Rollladen:        kurz = Drehknopf übernimmt 8 s lang (10 % je Raste, Knopf drücken = anhalten; blauer Rahmen),
                  lang = auf bzw. zu (je nach Stand; fährt er gerade: anhalten).
Licht (dimmbar):  kurz = Drehknopf übernimmt 8 s lang (Helligkeit 10 % je Raste, Knopf drücken = an/aus),
                  lang = an/aus. (Hue-Lampen über den Controller)
Licht:            kurz/lang = an/aus, Drehknopf rechts = an, links = aus.
Beim Drehen zählt nur das Ziel mit (Balken); gesendet wird erst nach SEND_DELAY Sekunden Ruhe.
Die Drehknopf-Belegung "Kachel einstellen" geht weiterhin. Koppeln: Hauptfenster -> "Smart Home ...".
"""

import logging
import threading
import time

import boschshc
import plugin_api
from plugin_api import Field, LiveTileType
from plugins.live import BLUE, DIM, TRACK, WHITE, YELLOW, canvas, fit_text

log = logging.getLogger("pcdeck")

KIND_NAMES = {"shutter": "Rollladen", "dimmer": "Licht, dimmbar", "light": "Licht"}
ADJUSTABLE = ("shutter", "dimmer")  # per Drehknopf in 10-%-Schritten einstellbar
_target = {}  # Gerät -> (Ziel 0..100, Zeit): schnelles Drehen addiert auf das Ziel, nicht auf den Ist-Stand
GRAB_SECONDS = 8  # so lange gilt ein Ziel bzw. gehört der Drehknopf der Kachel
SEND_DELAY = 0.6  # Drehknopf so lange in Ruhe -> Ziel an den Controller senden
_pending = {}     # Gerät -> threading.Timer (noch nicht gesendetes Ziel)
_lock = threading.Lock()
_rot_lock = threading.Lock()  # Drehen nacheinander abrechnen


def _choices():
    if not boschshc.paired():
        return [("", "(erst im Hauptfenster unter „Smart Home ...“ koppeln)")]
    try:
        devs = boschshc.devices()
    except (OSError, ValueError, boschshc.BoschError) as e:
        return [("", f"(Controller nicht erreichbar: {e})")]
    out = [(d["id"], " – ".join(x for x in (d["room"], d["name"]) if x) + f" ({KIND_NAMES[d['kind']]})")
           for d in devs if d["kind"] in KIND_NAMES]
    return sorted(out, key=lambda c: c[1].lower()) or [("", "(keine Rollläden oder Lichter gefunden)")]


def _current(kind, dev):
    """Ist-Wert 0..100 (Rollladen: Öffnung, Lampe: Helligkeit, aus = 0) oder None."""
    if kind == "shutter":
        s = boschshc.shutter(dev)
        return s[0] if s else None
    d = boschshc.dimmer(dev)
    return (d[1] if d[0] else 0) if d else None


def _grab_frame(d, grabbed, offline):
    if grabbed and not offline:
        d.rounded_rectangle((1, 1, 62, 62), 9, outline=BLUE, width=3)  # Drehknopf gehört dieser Kachel


def _shutter_img(pct, moving, offline, grabbed=False):
    img, d = canvas()
    frame = DIM if offline else (BLUE if moving else WHITE)
    _grab_frame(d, grabbed, offline)
    d.rectangle((10, 4, 54, 44), outline=frame, width=3)
    closed = 0 if pct is None else round((100 - pct) / 100 * 36)
    for y in range(7, 7 + closed, 4):  # Lamellen von oben
        d.line((13, y, 51, y), fill=DIM if offline else TRACK, width=3)
    text = "?" if offline or pct is None else ("auf" if pct >= 100 else ("zu" if pct <= 0 else f"{pct}%"))
    d.text((32, 55), text, font=fit_text(d, text, 56, 14), fill=frame, anchor="mm")
    return img


def _light_img(on, offline, level=None, grabbed=False):
    """Glühbirne; level (dimmbar) = Helligkeit 0..100 – je heller, desto kräftiger das Gelb."""
    img, d = canvas()
    _grab_frame(d, grabbed, offline)
    lit = on and not offline
    if lit and level is not None:
        f = 0.35 + 0.65 * max(0, min(100, level)) / 100
        fill = tuple(round(c * f) for c in YELLOW)
    else:
        fill = YELLOW if lit else None
    color = DIM if not lit else fill
    if lit:
        for r, a in ((26, 40), (22, 70)):
            d.ellipse((32 - r, 22 - r, 32 + r, 22 + r), outline=(a + 60, a + 40, 20), width=2)
    d.ellipse((18, 8, 46, 36), fill=fill, outline=color, width=3)
    d.rectangle((25, 36, 39, 44), outline=color, width=3)
    if offline:
        text = "?"
    elif not on:
        text = "AUS"
    else:
        text = f"{level}%" if level is not None else "AN"
    d.text((32, 55), text, font=fit_text(d, text, 56, 14), fill=WHITE if not offline else DIM, anchor="mm")
    return img


class Bosch(LiveTileType):
    id = "bosch"
    name = "Bosch Smart Home (Rollladen, Licht)"
    fields = [Field("device", "Gerät:", kind="choice", choices=_choices, width=50)]
    rotatable = True

    def is_valid(self, tile):
        return bool(tile.get("device"))

    def prepare(self, tile):
        try:
            d = next((x for x in boschshc.devices() if x["id"] == tile.get("device")), None)
        except (OSError, ValueError, boschshc.BoschError):
            d = None
        if d:
            tile["kind"], tile["device_name"] = d["kind"], d["name"]
        return tile

    def poll(self, tile, ctx):
        dev, kind = tile.get("device", ""), tile.get("kind")
        grabbed = bool(ctx and ctx.link and ctx.link.knob_grabbed(ctx.page, ctx.idx))
        try:
            if kind == "shutter":
                s = boschshc.shutter(dev)
                return ("ok",) + s + (grabbed,) if s else ("offline",)
            if kind == "dimmer":
                s = boschshc.dimmer(dev)
                return ("ok",) + s + (grabbed,) if s else ("offline",)
            on = boschshc.light(dev)
            return ("offline",) if on is None else ("ok", on)
        except (OSError, ValueError, boschshc.BoschError):
            return ("offline",)

    def render(self, tile, state, ctx):
        kind = tile.get("kind")
        name = tile.get("device_name") or KIND_NAMES.get(kind, "Bosch")
        offline = bool(state) and state[0] == "offline"
        ok = bool(state) and not offline
        if kind == "shutter":
            pct, moving, grabbed = (state[1], state[2], state[3]) if ok else (None, False, False)
            return _shutter_img(pct, moving, offline, grabbed), name
        if kind == "dimmer":
            on, level, grabbed = (state[1], state[2], state[3]) if ok else (False, None, False)
            return _light_img(on, offline, level, grabbed), name
        return _light_img(bool(state[1]) if ok else False, offline), name

    # -- Bedienen
    def _run(self, tile, ctx, fn, *args):
        try:
            fn(*args)
        except (OSError, ValueError, boschshc.BoschError) as e:
            log.error("Bosch Smart Home: %s", e)
            if ctx and ctx.link:
                ctx.link.request_osd(None, "Bosch: nicht erreichbar")
            return
        if ctx and ctx.link:
            ctx.link.request_live_update(0.5)

    def _toggle(self, tile, ctx):
        dev = tile.get("device", "")
        self._run(tile, ctx, lambda: boschshc.set_light(dev, not boschshc.light(dev)))

    def _cancel_pending(self, dev):
        with _lock:
            timer = _pending.pop(dev, None)
            _target.pop(dev, None)
        if timer:
            timer.cancel()  # noch nicht gesendetes Ziel verwerfen

    def on_press(self, tile, ctx):
        dev, kind = tile.get("device", ""), tile.get("kind")
        if kind not in ADJUSTABLE:
            self._toggle(tile, ctx)
            return
        if not (ctx and ctx.link):
            return
        # Drehknopf übernimmt (blauer Rahmen, Wert-Anzeige)
        ctx.link.grab_knob(ctx.page, ctx.idx)
        self._cancel_pending(dev)
        self._run(tile, ctx, lambda: ctx.link.request_osd(_current(kind, dev),
                                                          tile.get("device_name") or KIND_NAMES[kind]))

    def on_long_press(self, tile, ctx):
        dev = tile.get("device", "")
        if tile.get("kind") != "shutter":
            self._cancel_pending(dev)
            self._toggle(tile, ctx)
            return

        def act():
            s = boschshc.shutter(dev)
            if s and s[1]:
                boschshc.stop_shutter(dev)
            else:
                boschshc.set_shutter(dev, 0 if (s and s[0] >= 50) else 100)
        self._run(tile, ctx, act)

    def knob_osd(self, tile):
        """Balken bleibt stehen, solange der Drehknopf der Kachel gehört: Ziel beim Drehen, sonst Ist-Stand."""
        dev, kind = tile.get("device", ""), tile.get("kind")
        name = tile.get("device_name") or KIND_NAMES.get(kind, "Bosch")
        if kind not in ADJUSTABLE:
            on = boschshc.light(dev)
            return None, f"{name}: {'an' if on else 'aus'}"
        tgt, t = _target.get(dev, (None, 0))
        if tgt is not None and time.monotonic() - t <= GRAB_SECONDS:
            return tgt, name
        return _current(kind, dev), name

    def on_knob_press(self, tile, ctx):
        dev, kind = tile.get("device", ""), tile.get("kind")
        self._cancel_pending(dev)
        if kind == "shutter":
            self._run(tile, ctx, boschshc.stop_shutter, dev)
            if ctx and ctx.link:
                ctx.link.request_osd(None, "Rollladen: Stopp")
        else:
            self._toggle(tile, ctx)

    def on_rotate(self, tile, ctx, steps):
        dev, kind = tile.get("device", ""), tile.get("kind")
        if kind not in ADJUSTABLE:
            self._run(tile, ctx, boschshc.set_light, dev, steps > 0)
            return

        # Beim Drehen nur das Ziel verschieben und anzeigen; gesendet wird erst, wenn der Knopf
        # SEND_DELAY Sekunden ruht – und dann nur das Endziel (keine Zwischenschritte).
        # _rot_lock: Rasten kommen in eigenen Threads – nacheinander abrechnen, keine geht verloren.
        with _rot_lock:
            with _lock:
                tgt, t = _target.get(dev, (None, 0))
            if tgt is None or time.monotonic() - t > GRAB_SECONDS:
                try:
                    tgt = _current(kind, dev)
                except (OSError, ValueError, boschshc.BoschError):
                    tgt = None
                tgt = tgt or 0
            new = max(0, min(100, round((tgt + steps * 10) / 10) * 10))
            with _lock:
                _target[dev] = (new, time.monotonic())
                old = _pending.pop(dev, None)
                timer = threading.Timer(SEND_DELAY, self._send_target, args=(tile, ctx, dev))
                timer.daemon = True
                _pending[dev] = timer
            if old:
                old.cancel()
            timer.start()
        if ctx and ctx.link:
            ctx.link.request_osd(new, tile.get("device_name") or KIND_NAMES[kind])

    def _send_target(self, tile, ctx, dev):
        with _lock:
            if _pending.get(dev) is not threading.current_thread():
                return  # inzwischen weitergedreht oder angehalten
            _pending.pop(dev, None)
            new = _target.get(dev, (None, 0))[0]
        if new is None:
            return
        log.info("Bosch Smart Home: %s -> %d %%", tile.get("device_name") or dev, new)
        fn = boschshc.set_shutter if tile.get("kind") == "shutter" else boschshc.set_dimmer
        self._run(tile, ctx, fn, dev, new)


plugin_api.register(Bosch())
