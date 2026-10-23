"""Kachel-Typen für Audiogeräte: Ausgabegerät umschalten, Audio-Profil, Mikrofon stumm.

Geräte werden per ID gespeichert, dazu der Name (Feld <schlüssel>_name) als Ersatz,
falls sich die ID ändert (z. B. nach einer Treiber-Neuinstallation).
"""

import logging

from PIL import ImageDraw

import iconextract
import plugin_api
import winaudio
from plugin_api import Field, LiveTileType
from plugins.live import DIM, GREEN, RED, WHITE, canvas

log = logging.getLogger("pcdeck")

SPEAKER_ICON = r"%windir%\system32\mmres.dll,-3010"  # Windows-Symbol "Lautsprecher"


# ----------------------------------------------------------------- Hilfen
def _choices(flow, optional=False):
    def get():
        try:
            devs = winaudio.devices(flow)
        except OSError:
            devs = []
        out = [("", "(keins)")] if optional else []
        return out + [(d["id"], d["name"]) for d in devs]
    return get


def _resolve(flow, tile, key, devs=None):
    """Gerät (dict) zu Feld key: erst per ID, sonst per gespeichertem Namen. None = nicht da."""
    dev_id, name = tile.get(key, ""), tile.get(key + "_name", "")
    if not dev_id and not name:
        return None
    devs = devs if devs is not None else winaudio.devices(flow)
    return (next((d for d in devs if d["id"] == dev_id), None)
            or next((d for d in devs if name and d["name"] == name), None))


def _short(dev_or_name):
    """ "Lautsprecher (High Definition Audio Device)" -> "Lautsprecher" """
    if isinstance(dev_or_name, dict):
        name = dev_or_name.get("desc") or dev_or_name.get("name", "")
    else:
        name = dev_or_name
    return name.split(" (")[0] if name else "?"


def _store_names(tile, flow, keys):
    try:
        devs = {d["id"]: d["name"] for d in winaudio.devices(flow)}
    except OSError:
        devs = {}
    for k in keys:
        if tile.get(k) in devs:
            tile[k + "_name"] = devs[tile[k]]
        elif not tile.get(k):
            tile.pop(k + "_name", None)
    return tile


_icons = {}


def _device_img(icon_path, active, missing=False):
    key = (icon_path, active, missing)
    if key not in _icons:
        raw = None
        try:
            raw = winaudio.device_icon(icon_path)
        except OSError:
            pass
        trimmed = iconextract._trim(raw) if raw is not None else None
        if trimmed is not None:
            img = iconextract._fit(trimmed, margin=0.12)
        else:
            img, d = canvas()
            d.text((32, 32), "?", fill=DIM, anchor="mm")
        d = ImageDraw.Draw(img)
        if active:
            d.rounded_rectangle((1, 1, 62, 62), 9, outline=GREEN, width=3)
        if missing:
            d.line((8, 8, 56, 56), fill=RED, width=4)
        _icons[key] = img
    return _icons[key]


# ----------------------------------------------------------------- Ausgabegerät
class AudioOutput(LiveTileType):
    """Jedes Antippen schaltet zum nächsten angehakten Gerät (1 Haken = festes Gerät,
    2 = hin und her, mehr = reihum). Ältere Kacheln mit device_a/device_b werden übernommen."""

    id = "audio_output"
    name = "Ausgabegerät umschalten"
    fields = [Field("devices", "Geräte zum Durchschalten (jedes Antippen = nächstes angehaktes Gerät):",
                    kind="multi", choices=_choices(winaudio.RENDER))]

    @staticmethod
    def _selected(tile):
        """[(id, gespeicherter Name)] in Listenreihenfolge."""
        names = tile.get("devices_names") or {}
        if tile.get("devices"):
            return [(i, names.get(i, "")) for i in tile["devices"]]
        return [(tile[k], tile.get(k + "_name", "")) for k in ("device_a", "device_b") if tile.get(k)]

    def prepare(self, tile):
        try:
            devs = {d["id"]: d["name"] for d in winaudio.devices(winaudio.RENDER)}
        except OSError:
            devs = {}
        names = dict(tile.get("devices_names") or {})
        names.update({i: devs[i] for i in tile.get("devices", []) if i in devs})
        tile["devices_names"] = {i: names[i] for i in tile.get("devices", []) if i in names}
        return tile

    def is_valid(self, tile):
        return bool(self._selected(tile))

    def _available(self, tile, devs):
        """Angehakte Geräte, die gerade vorhanden sind (per ID, sonst per Name gefunden)."""
        out = []
        for dev_id, name in self._selected(tile):
            d = (next((x for x in devs if x["id"] == dev_id), None)
                 or next((x for x in devs if name and x["name"] == name), None))
            if d and d not in out:
                out.append(d)
        return out

    def poll(self, tile, ctx):
        devs = winaudio.devices(winaudio.RENDER)
        avail = self._available(tile, devs)
        cur = winaudio.default_id(winaudio.RENDER)
        active = next((d for d in avail if d["id"] == cur), None)
        if active:
            return "active", _short(active), active["icon"]
        current = next((d for d in devs if d["id"] == cur), None)
        if current:  # anderes, nicht angehaktes Gerät ist aktiv
            return "other", _short(current), current["icon"]
        if avail:
            return "idle", _short(avail[0]), avail[0]["icon"]
        sel = self._selected(tile)
        return "missing", sel[0][1] if sel else "?", ""

    def render(self, tile, state, ctx):
        if state is None:
            return _device_img(SPEAKER_ICON, False), "Ausgabe"
        mode, name, icon = state
        if mode == "missing":
            return _device_img(icon or SPEAKER_ICON, False, missing=True), _short(name)
        return _device_img(icon, mode == "active"), name

    def on_press(self, tile, ctx):
        avail = self._available(tile, winaudio.devices(winaudio.RENDER))
        if not avail:
            log.warning("Kein angehaktes Ausgabegerät vorhanden")
            return
        cur = winaudio.default_id(winaudio.RENDER)
        ids = [d["id"] for d in avail]
        target = avail[(ids.index(cur) + 1) % len(avail)] if cur in ids else avail[0]
        log.info("Ausgabegerät -> %s", target["name"])
        winaudio.set_default(target["id"])


# ----------------------------------------------------------------- Audio-Profil
class AudioProfile(LiveTileType):
    id = "audio_profile"
    name = "Audio-Profil (Ausgabe + Mikrofon)"
    fields = [Field("output", "Ausgabegerät:", kind="choice", choices=_choices(winaudio.RENDER, optional=True),
                    width=50),
              Field("input", "Mikrofon:", kind="choice", choices=_choices(winaudio.CAPTURE, optional=True),
                    width=50)]

    def prepare(self, tile):
        _store_names(tile, winaudio.RENDER, ("output",))
        return _store_names(tile, winaudio.CAPTURE, ("input",))

    def is_valid(self, tile):
        return bool(tile.get("output") or tile.get("input"))

    def _targets(self, tile):
        out = _resolve(winaudio.RENDER, tile, "output")
        inp = _resolve(winaudio.CAPTURE, tile, "input")
        return out, inp

    def poll(self, tile, ctx):
        out, inp = self._targets(tile)
        missing = (tile.get("output") and not out) or (tile.get("input") and not inp)
        active = ((not out or winaudio.default_id(winaudio.RENDER) == out["id"])
                  and (not inp or winaudio.default_id(winaudio.CAPTURE) == inp["id"]))
        icon = (out or inp or {}).get("icon", "")
        return bool(missing), bool(active and (out or inp)), icon

    def render(self, tile, state, ctx):
        label = _short(tile.get("output_name") or tile.get("input_name") or "Profil")
        if state is None:
            return _device_img(SPEAKER_ICON, False), label
        missing, active, icon = state
        return _device_img(icon, active, missing=missing), label

    def on_press(self, tile, ctx):
        out, inp = self._targets(tile)
        for dev in (out, inp):
            if dev:
                log.info("Audio-Profil -> %s", dev["name"])
                winaudio.set_default(dev["id"])


# ----------------------------------------------------------------- Mikrofon stumm
def _mic_img(state):
    img, d = canvas()
    color = DIM if state is None else (RED if state else WHITE)
    d.rounded_rectangle((24, 8, 40, 38), 8, fill=color)
    d.arc((16, 18, 48, 46), 0, 180, fill=color, width=3)
    d.line((32, 46, 32, 54), fill=color, width=3)
    d.line((24, 55, 40, 55), fill=color, width=3)
    if state:
        d.line((12, 8, 52, 56), fill=RED, width=5)
    return img


class MicMute(LiveTileType):
    id = "mic_mute"
    name = "Mikrofon stumm / an"
    fields = [Field("all_mics", "Alle Mikrofone (nicht nur das Standard-Mikrofon)", kind="bool")]

    def poll(self, tile, ctx):
        return winaudio.mic_muted(bool(tile.get("all_mics")))

    def render(self, tile, state, ctx):
        if state is None:
            return _mic_img(None), ("Mikro" if ctx is None else "kein Mikro")
        return _mic_img(state), ("Mikro aus" if state else "Mikro an")

    def on_press(self, tile, ctx):
        try:
            winaudio.toggle_mic(bool(tile.get("all_mics")))
        except OSError:
            log.warning("Mikrofon stumm: kein Mikrofon gefunden")


for _t in (AudioOutput(), AudioProfile(), MicMute()):
    plugin_api.register(_t)
