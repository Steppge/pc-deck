"""Kachel-Typ "obs": OBS Studio steuern (Szene wechseln, Aufnahme, Stream) über obs-websocket 5.

In OBS: Werkzeuge -> WebSocket-Server-Einstellungen -> Server aktivieren. Port und Passwort
in der Kachel eintragen; das Passwort wird mit DPAPI verschlüsselt gespeichert.
"""

import logging

from PIL import ImageDraw

import obsws
import plugin_api
import winsecret
from plugin_api import Field, LiveTileType
from plugins.live import DIM, GREEN, RED, WHITE, canvas, fit_text

log = logging.getLogger("pcdeck")

ACTIONS = [("scene", "Szene wechseln"), ("record", "Aufnahme starten / stoppen"),
           ("stream", "Stream starten / stoppen")]


def _conn(tile):
    try:
        pw = winsecret.decrypt(tile.get("password", ""))
    except OSError as e:
        log.error("OBS-Passwort: %s", e)
        pw = ""
    return tile.get("host", "").strip() or "localhost", tile.get("port", "").strip() or "4455", pw


def _call(tile, request, data=None):
    host, port, pw = _conn(tile)
    return obsws.call(host, port, pw, request, data)


def _img(action, active, offline, scene=""):
    img, d = canvas()
    if action == "record":
        color = DIM if offline else (RED if active else WHITE)
        d.ellipse((14, 10, 50, 46), outline=color, width=4)
        if active:
            d.ellipse((22, 18, 42, 38), fill=RED)
        d.text((32, 56), "REC", font=fit_text(d, "REC", 40, 12), fill=color, anchor="mm")
    elif action == "stream":
        color = DIM if offline else (RED if active else WHITE)
        d.ellipse((26, 22, 38, 34), fill=color)
        for r in (14, 22):
            d.arc((32 - r, 28 - r, 32 + r, 28 + r), 200, 340, fill=color, width=3)
            d.arc((32 - r, 28 - r, 32 + r, 28 + r), 20, 160, fill=color, width=3)
        d.text((32, 56), "LIVE", font=fit_text(d, "LIVE", 40, 12), fill=color, anchor="mm")
    else:  # Szene
        color = DIM if offline else WHITE
        d.rounded_rectangle((8, 12, 56, 46), 5, outline=color, width=3)
        d.polygon([(28, 22), (28, 36), (40, 29)], fill=color)
        if active and not offline:
            ImageDraw.Draw(img).rounded_rectangle((1, 1, 62, 62), 9, outline=GREEN, width=3)
    return img


class Obs(LiveTileType):
    id = "obs"
    name = "OBS Studio"
    fields = [Field("action", "Aktion:", kind="choice", default="scene", choices=ACTIONS, width=30),
              Field("scene", "Szenenname (nur bei 'Szene wechseln', genau wie in OBS):", width=30),
              Field("host", "OBS-Rechner (leer = dieser PC):", width=30),
              Field("port", "Port (leer = 4455):", width=8),
              Field("password", "Passwort (aus OBS -> Werkzeuge -> WebSocket-Server):", kind="secret", width=30)]

    def is_valid(self, tile):
        return tile.get("action", "scene") != "scene" or bool(tile.get("scene", "").strip())

    def poll(self, tile, ctx):
        action = tile.get("action", "scene")
        try:
            if action == "record":
                return "ok", bool(_call(tile, "GetRecordStatus").get("outputActive"))
            if action == "stream":
                return "ok", bool(_call(tile, "GetStreamStatus").get("outputActive"))
            cur = _call(tile, "GetCurrentProgramScene").get("currentProgramSceneName", "")
            return "ok", cur == tile.get("scene", "").strip()
        except (OSError, obsws.OBSError):
            return "offline", False

    def render(self, tile, state, ctx):
        action = tile.get("action", "scene")
        mode, active = state if state else ("ok", False)
        offline = mode == "offline"
        if offline:
            label = "OBS aus"
        elif action == "record":
            label = "Aufnahme" if not active else "nimmt auf"
        elif action == "stream":
            label = "Stream" if not active else "LIVE"
        else:
            label = tile.get("scene", "") or "Szene"
        return _img(action, active, offline and state is not None), label

    def on_press(self, tile, ctx):
        action = tile.get("action", "scene")
        try:
            if action == "record":
                _call(tile, "ToggleRecord")
            elif action == "stream":
                _call(tile, "ToggleStream")
            else:
                _call(tile, "SetCurrentProgramScene", {"sceneName": tile.get("scene", "").strip()})
        except (OSError, obsws.OBSError) as e:
            log.error("OBS: %s", e)


plugin_api.register(Obs())
