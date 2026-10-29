"""Kachel-Typen "sound" (Soundboard) und "sound_stop_all".

sound: spielt eine Audiodatei (MP3/WAV/OGG/FLAC) auf dem PC ab – Lautstärke, Ausgabegerät
("Standardgerät" folgt der Audio-Umschaltung), optional ein zweites Gerät (virtuelles Kabel für
Discord & Co.), Modus überlagern / vorherige stoppen. Erneut antippen stoppt den Sound. Solange er
läuft, ist die Kachel grün umrandet und zeigt den Fortschritt.
Die Wiedergabe selbst steckt in soundplayer.py (erst beim Gebrauch geladen – numpy & Co. sind groß).
"""

import logging
import os

import zlib

from PIL import Image, ImageDraw, ImageFilter

import plugin_api
from plugin_api import Field, LiveTileType, TileType
from plugins.live import DIM, GREEN, TRACK, WHITE, canvas

log = logging.getLogger("pcdeck")

VOLUMES = [(str(v), f"{v} %") for v in range(100, 0, -10)]

# Knopffarben: automatisch der Reihe nach (Platz auf der Seite bzw. Reihenfolge im Soundboard) oder fest gewählt
PALETTE = [("Rot", "#FF1A1A"), ("Orange", "#FF8A00"), ("Gelb", "#FFD400"), ("Limette", "#A8F000"),
           ("Grün", "#22C55E"), ("Mint", "#2EE6A8"), ("Türkis", "#00CFE0"), ("Hellblau", "#30A9FF"),
           ("Blau", "#2563FF"), ("Indigo", "#5B3CFF"), ("Lila", "#A425FF"), ("Magenta", "#F02BD6"),
           ("Pink", "#FF6FA5"), ("Braun", "#B5652B"), ("Grau", "#9AA0A6")]
COLORS = dict(PALETTE)
COLOR_CHOICES = [("", "automatisch (jeder Platz eine andere Farbe)")] + [(n, n) for n, _ in PALETTE]


def color_of(tile, ctx=None):
    """Knopffarbe (Hex): fest gewählt, sonst automatisch – Reihenfolge im Soundboard ("auto_color"), Platz auf
    der Seite ("_slot" beim Übertragen bzw. ctx.idx live), notfalls aus dem Dateinamen."""
    if tile.get("color") in COLORS:
        return COLORS[tile["color"]]
    n = tile.get("auto_color", tile.get("_slot"))
    if n is None and ctx is not None and ctx.idx is not None:
        n = ctx.idx
    if n is None:
        n = zlib.crc32(tile.get("file", "").encode("utf-8"))
    return PALETTE[int(n) % len(PALETTE)][1]


def button_img(color, pressed=False, size=64, bg=None):
    """Runder, glänzender Knopf wie ein Buzzer; pressed = eingedrückt (läuft)."""
    S = 4  # in vierfacher Größe zeichnen, dann glatt verkleinern
    W = size * S
    bg = bg or canvas()[0].getpixel((0, 0))
    img = Image.new("RGB", (W, W), bg)
    d = ImageDraw.Draw(img)
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    k = W / 256  # Maße bezogen auf 64 px
    cx, rad = W // 2, 84 * k
    top = (56 if pressed else 40) * k
    d.ellipse((cx - rad - 12 * k, 48 * k, cx + rad + 12 * k, 48 * k + 2 * rad + 32 * k), fill=(28, 30, 36))
    if not pressed:
        d.ellipse((cx - rad, top + 20 * k, cx + rad, top + 20 * k + 2 * rad), fill=(r * 55 // 100, g * 55 // 100,
                                                                                  b * 55 // 100))
    d.ellipse((cx - rad, top, cx + rad, top + 2 * rad), fill=(r, g, b) if not pressed else
              (r * 85 // 100, g * 85 // 100, b * 85 // 100))
    hl = Image.new("L", (W, W), 0)
    ImageDraw.Draw(hl).ellipse((cx - rad * 0.65, top + rad * 0.18, cx + rad * 0.65, top + rad * 0.95),
                               fill=90 if pressed else 120)
    light = (r + (255 - r) * 7 // 10, g + (255 - g) * 7 // 10, b + (255 - b) * 7 // 10)
    img = Image.composite(Image.new("RGB", (W, W), light), img, hl.filter(ImageFilter.GaussianBlur(16 * k)))
    return img.resize((size, size), Image.LANCZOS)

# Fenster "Freie Sounds finden ..." im Kachel-Dialog: (Überschrift, Hinweis, [(Name, Link, Beschreibung)])
SOUND_SOURCES = [
    ("Lizenzfrei", "", [
        ("Pixabay Sound Effects", "https://pixabay.com/sound-effects/",
         "große Auswahl, MP3, ohne Anmeldung – frei nutzbar, keine Namensnennung nötig"),
        ("Mixkit", "https://mixkit.co/free-sound-effects/", "kuratiert, gute Qualität, WAV – frei nutzbar"),
        ("Freesound", "https://freesound.org/",
         "riesige Community-Sammlung, Creative Commons je Sound verschieden (CC0 = völlig frei, CC-BY = Name "
         "nennen), Anmeldung nötig – Tipp: „airhorn“, „rimshot“, „fail“ suchen, oft CC0"),
        ("ZapSplat", "https://www.zapsplat.com/", "sehr viele Effekte – kostenlos mit Konto, Namensnennung erwünscht"),
        ("BBC Sound Effects", "https://sound-effects.bbcrewind.co.uk/",
         "über 30.000 Archiv-Geräusche – nur privat, Lehre, nicht kommerziell"),
        ("Sonniss GameAudioGDC", "https://sonniss.com/gameaudiogdc",
         "große Profi-Pakete (mehrere GB) – lizenzfrei, auch kommerziell"),
        ("YouTube Audio Library", "https://studio.youtube.com/",
         "im YouTube Studio unter „Audio-Mediathek“ – frei für YouTube-Videos, Google-Konto nötig"),
    ]),
    ("Memes, Filme, Spiele", "Rechte an den Schnipseln meist ungeklärt – im privaten Discord-Kreis kein Thema, "
     "für Streams oder Videos lieber die lizenzfreien Seiten oben nehmen.", [
        ("myinstants.com", "https://www.myinstants.com/",
         "größte Sammlung an Sofort-Sounds (Bruh, Vine Boom, Airhorn, Sad Trombone …), MP3 direkt herunterladbar"),
        ("101soundboards.com", "https://www.101soundboards.com/", "Soundboards zu Filmen, Serien, Spielen, Promis"),
        ("voicy.network", "https://www.voicy.network/", "Clips aus Memes, Filmen und Spielen, gut durchsuchbar"),
        ("soundboard.com", "https://www.soundboard.com/", "ältere, große Sammlung"),
    ]),
]
MODES = [("overlay", "überlagern (mehrere Sounds gleichzeitig)"), ("stop", "vorherige Sounds stoppen")]


def _sp():
    import soundplayer
    return soundplayer


def _out_choices():
    return [("", "Standardgerät (folgt der Umschaltung)")] + [(n, n) for n in _sp().output_names()]


def _out2_choices():
    return [("", "keins")] + [(n, n) for n in _sp().output_names()]


def _cable_hint():
    cable = _sp().virtual_cable()
    if cable:
        return (f"Virtuelles Kabel gefunden: „{cable}“ – als zweites Ausgabegerät wählen, dann hören Discord & Co. "
                "den Sound (dort als Mikrofon „CABLE Output“ einstellen).")
    return ("Damit Sounds auch in Discord & Co. zu hören sind, wird VB-Audio Virtual Cable benötigt "
            "(kostenlos, vb-audio.com). Danach in Discord als Mikrofon „CABLE Output“ wählen. Für Mikrofon "
            "und Sounds gleichzeitig zusätzlich VoiceMeeter Banana. Ohne virtuelles Kabel spielt das "
            "Soundboard nur über Lautsprecher/Kopfhörer.")


def _cable_url():
    return "" if _sp().virtual_cable() else _sp().VCABLE_URL


def _open_search(dlg):
    """Fenster „Sounds suchen“; „In Kachel übernehmen“ trägt Datei und (falls leer) Beschriftung ein."""
    import config_gui

    def pick(path, name):
        dlg.vars["file"].set(path)
        if not dlg.v_label.get().strip():
            dlg.v_label.set(name[:24])
        _sp().preload(path)
    config_gui.SoundSearchDialog(dlg, on_pick=pick)


def _note(d, color):
    d.ellipse((16, 36, 30, 48), fill=color)
    d.ellipse((36, 30, 50, 42), fill=color)
    d.line((29, 42, 29, 12), fill=color, width=3)
    d.line((49, 36, 49, 8), fill=color, width=3)
    d.line((29, 12, 49, 8), fill=color, width=4)


class Sound(LiveTileType):
    id = "sound"
    name = "Sound abspielen (Soundboard)"
    fields = [Field("file", "Audiodatei (MP3, WAV, OGG, FLAC):", kind="file"),
              Field("search", "Sounds suchen und herunterladen ...", kind="tool", default=_open_search),
              Field("sources", "Freie Sounds finden ...", kind="links", default=SOUND_SOURCES),
              Field("volume", "Lautstärke:", kind="choice", default="100", choices=VOLUMES, width=12),
              Field("device", "Ausgabegerät:", kind="choice", default="", choices=_out_choices, width=50),
              Field("device2", "Zweites Ausgabegerät (z. B. virtuelles Kabel für Discord & Co.):", kind="choice",
                    default="", choices=_out2_choices, width=50),
              Field("cable_hint", _cable_hint, kind="info", default=_cable_url),
              Field("mode", "Wenn schon ein Sound läuft:", kind="choice", default="overlay", choices=MODES,
                    width=40),
              Field("color", "Farbe des Knopfs:", kind="choice", default="", choices=COLOR_CHOICES, width=40)]
    idle_state = None  # spielt nicht = sieht aus wie im Flash

    def is_valid(self, tile):
        return bool(tile.get("file"))

    def prepare(self, tile):
        _sp().preload(tile.get("file", ""))
        return tile

    def label(self, tile):
        return os.path.splitext(os.path.basename(tile.get("file", "")))[0] or "Sound"

    def poll(self, tile, ctx):
        p = _sp().progress(ctx.key) if ctx else None
        return None if p is None else round(p * 20)

    def render(self, tile, state, ctx):
        if tile.get("icon"):
            import iconextract
            img = iconextract.get_icon("", tile["icon"], self.label(tile)).convert("RGB")
            d = ImageDraw.Draw(img)
        else:  # farbiger Knopf; läuft er, ist er eingedrückt
            img = button_img(color_of(tile, ctx), pressed=state is not None)
            d = ImageDraw.Draw(img)
        if state is not None:  # läuft: grüner Rahmen und Fortschritt
            d.rounded_rectangle((1, 1, 62, 62), 9, outline=GREEN, width=3)
            d.rectangle((8, 55, 56, 58), fill=TRACK)
            d.rectangle((8, 55, 8 + round(48 * state / 20), 58), fill=GREEN)
        return img, self.label(tile)

    def icon(self, tile, label):
        return self.render(tile, None, None)[0]

    def on_press(self, tile, ctx):
        sp = _sp()
        key = ctx.key if ctx else tile.get("file", "")
        if sp.progress(key) is not None:  # läuft -> stoppen
            sp.stop(key)
        else:
            devices = [tile.get("device", "")] + ([tile["device2"]] if tile.get("device2") else [])
            try:
                missing = sp.play(key, tile.get("file", ""), int(tile.get("volume", "100")) / 100, devices,
                                  stop_others=tile.get("mode") == "stop")
            except FileNotFoundError:
                missing, msg = None, "Sounddatei fehlt"
            except Exception as e:
                log.error("Soundboard: %s", e)
                missing, msg = None, "Sound nicht abspielbar"
            else:
                msg = None
                if missing:
                    msg = "Virtuelles Kabel fehlt" if tile.get("device2") in missing else "Ausgabegeraet fehlt"
            if msg and ctx and ctx.link:
                ctx.link.request_osd(None, msg)
        if ctx and ctx.link:
            ctx.link.request_live_update(0.1)


class SoundStopAll(TileType):
    id = "sound_stop_all"
    name = "Alle Sounds stoppen (Soundboard)"
    fields = []

    def label(self, tile):
        return "Sounds aus"

    def icon(self, tile, label):
        if tile.get("icon"):
            return super().icon(tile, label)
        img, d = canvas()
        _note(d, DIM)
        d.rounded_rectangle((34, 34, 60, 60), 5, fill=(235, 80, 80))
        d.rectangle((42, 42, 52, 52), fill=WHITE)
        return img

    def on_press(self, tile, ctx):
        _sp().stop_all()
        if ctx and ctx.link:
            ctx.link.request_live_update(0.1)


for _t in (Sound(), SoundStopAll()):
    plugin_api.register(_t)
