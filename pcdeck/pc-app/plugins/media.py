"""Kachel-Typen "media" (Play/Pause, Weiter, Zurück, Lauter/Leiser, Stumm) und "nowplaying" (Titel + Cover)."""

import ctypes

from PIL import Image, ImageDraw, ImageOps

import plugin_api
import winaudio
import winmedia
from iconextract import ICON_SZ, TILE_BG
from plugin_api import Field, LiveTileType
from plugins.live import BLUE, DIM, RED, WHITE, canvas

user32 = ctypes.windll.user32
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2
VK = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}

ACTIONS = [("play_pause", "Play / Pause"), ("next", "Nächster Titel"), ("prev", "Vorheriger Titel"),
           ("vol_up", "Lauter"), ("vol_down", "Leiser"), ("mute", "Ton stumm / an")]
LABELS = {"play_pause": "Play/Pause", "next": "Weiter", "prev": "Zurueck",
          "vol_up": "Lauter", "vol_down": "Leiser", "mute": "Ton"}


def press_key(vk):
    user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


def speaker(d, color=WHITE, x=6):
    d.polygon([(x, 24), (x + 9, 24), (x + 20, 14), (x + 20, 50), (x + 9, 40), (x, 40)], fill=color)


def draw(action, state):
    img, d = canvas()
    if action == "play_pause":
        d.polygon([(10, 18), (10, 46), (32, 32)], fill=WHITE)
        d.rectangle((38, 18, 44, 46), fill=WHITE)
        d.rectangle((50, 18, 56, 46), fill=WHITE)
    elif action in ("next", "prev"):
        pts = [[(10, 18), (10, 46), (30, 32)], [(30, 18), (30, 46), (50, 32)]]
        bar = (50, 18, 55, 46)
        if action == "prev":
            pts = [[(64 - x, y) for x, y in p] for p in pts]
            bar = (9, 18, 14, 46)
        for p in pts:
            d.polygon(p, fill=WHITE)
        d.rectangle(bar, fill=WHITE)
    elif action in ("vol_up", "vol_down"):
        speaker(d)
        d.line((36, 32, 56, 32), fill=BLUE, width=5)
        if action == "vol_up":
            d.line((46, 22, 46, 42), fill=BLUE, width=5)
    else:  # mute
        muted = bool(state and state[1])
        speaker(d, DIM if state is None else WHITE)
        if muted:
            d.line((34, 22, 56, 42), fill=RED, width=5)
            d.line((56, 22, 34, 42), fill=RED, width=5)
        else:
            for r in (8, 15):
                d.arc((26 - r, 32 - r, 26 + r, 32 + r), -45, 45, fill=BLUE, width=3)
    return img


class Media(LiveTileType):
    id = "media"
    name = "Mediensteuerung"
    fields = [Field("action", "Aktion:", kind="choice", default="play_pause", choices=ACTIONS, width=30),
              Field("step", "Schritt in % (nur Lauter/Leiser):", default="5", width=8)]

    def _step(self, tile):
        try:
            return max(1, min(50, int(float(str(tile.get("step", "5")).replace(",", ".")))))
        except ValueError:
            return 5

    def poll(self, tile, ctx):
        if tile.get("action", "play_pause") in ("vol_up", "vol_down", "mute"):
            return winaudio.volume()  # (Prozent, stumm)
        return "fest"

    def render(self, tile, state, ctx):
        action = tile.get("action", "play_pause")
        label = LABELS.get(action, "Medien")
        if action in ("vol_up", "vol_down", "mute") and state not in (None, "fest"):
            level, muted = state
            label = "stumm" if muted else f"{level}%"
        return draw(action, None if state == "fest" else state), label

    def on_press(self, tile, ctx):
        action = tile.get("action", "play_pause")
        if action in VK:
            press_key(VK[action])
        elif action == "vol_up":
            winaudio.change_volume(self._step(tile))
        elif action == "vol_down":
            winaudio.change_volume(-self._step(tile))
        elif action == "mute":
            winaudio.toggle_mute()


plugin_api.register(Media())


# ----------------------------------------------------------------- Jetzt läuft


def _note(color=WHITE):
    img, d = canvas()
    d.ellipse((12, 38, 28, 52), fill=color)
    d.ellipse((36, 32, 52, 46), fill=color)
    d.rectangle((24, 12, 28, 45), fill=color)
    d.rectangle((48, 8, 52, 39), fill=color)
    d.polygon([(24, 12), (52, 6), (52, 14), (24, 20)], fill=color)
    return img


class NowPlaying(LiveTileType):
    """Titel und Cover dessen, was Windows gerade als Medium kennt (Spotify, Browser, VLC ...).
    Kurz = Play/Pause, lang = nächster Titel."""

    id = "nowplaying"
    name = "Jetzt läuft (Titel + Cover)"

    def poll(self, tile, ctx):
        title, artist, status, cover = winmedia.now_playing()
        return title, artist, status, id(cover) if cover is not None else None

    def render(self, tile, state, ctx):
        if state is None:
            return _note(DIM), "Jetzt laeuft"
        title, artist, status, _ = state
        if status in ("none", "", "Closed") or not title:
            return _note(DIM), "nichts"
        cover = winmedia.now_playing()[3]
        if cover is not None:
            img = ImageOps.fit(cover, (ICON_SZ, ICON_SZ), Image.LANCZOS)
        else:
            img = _note(WHITE)
        if status != "Playing":  # pausiert: abdunkeln + Pause-Zeichen
            img = Image.blend(img, Image.new("RGB", img.size, TILE_BG), 0.55)
            d = ImageDraw.Draw(img)
            d.rectangle((22, 20, 28, 44), fill=WHITE)
            d.rectangle((36, 20, 42, 44), fill=WHITE)
        return img, title

    def on_press(self, tile, ctx):
        press_key(VK["play_pause"])

    def on_long_press(self, tile, ctx):
        press_key(VK["next"])


plugin_api.register(NowPlaying())
