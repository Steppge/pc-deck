"""Kachel-Typ "launch": Programm, Datei, Verknüpfung, Ordner oder Webadresse öffnen.

Läuft es schon, minimiert ein kurzer Tipper bzw. holt es zurück, ein langer (0,6 s)
schließt es höflich. Mit "multi_open" öffnet jeder kurze Tipper eine weitere Instanz.
(Das frühere Feld "close_toggle" wird ignoriert – Steuern ist jetzt Standard.)
Ist das Programm bzw. der Ordner im Vordergrund, bekommt die Kachel einen grünen Rahmen
(Live-Kachel: nur bei Änderung wird das Bild neu ans Deck geschickt).
"""

import logging
import ntpath
import os
import threading

from PIL import ImageDraw

import iconextract
import plugin_api
import winctl
from plugin_api import Field, TileType

log = logging.getLogger("pcdeck")


FRAME = (80, 200, 120)  # grün, wie "aktiv" bei den Audio-Kacheln

# Programme, die beim Schließen des Fensters nur in den Infobereich gehen: lang antippen
# beendet sie stattdessen mit diesem Parameter (Feld quit_args, "-" = abschalten)
KNOWN_QUIT = {"steam.exe": "-shutdown"}


class Launch(TileType):
    id = "launch"
    live = True  # nur für den Rahmen "im Vordergrund"
    idle_state = False  # ohne Rahmen = gespeichertes Bild, nach Seitenwechsel nichts senden
    name = "Programm / Ordner öffnen"
    fields = [
        Field("path", "Ziel (Programm, Datei, Ordner oder Webadresse):", kind="path", width=60),
        Field("args", "Startparameter (optional):", width=60),
        Field("multi_open", "Jedes Antippen öffnet neu (mehrere Fenster erlaubt)", kind="bool"),
        Field("close_proc", "Prozessname (optional, nötig bei Startern wie Fusion360.exe):", width=30),
        Field("quit_args", "Lang antippen beendet mit Parameter (optional; Steam automatisch -shutdown, "
                           "\"-\" = nur Fenster schließen):", width=30),
    ]

    def label(self, tile):
        p = tile.get("path", "").rstrip("\\/")
        base = ntpath.basename(p) or p  # Windows-Pfadregeln, unabhängig vom System
        stem, ext = ntpath.splitext(base)
        return stem if ext.lower() in (".exe", ".lnk", ".url", ".bat", ".cmd") else base

    def icon(self, tile, label):
        return iconextract.get_icon(tile.get("path", ""), tile.get("icon", ""), label)

    def is_valid(self, tile):
        return bool(tile.get("path", "").strip())

    def poll(self, tile, ctx):
        path = os.path.expandvars(tile.get("path", "")).strip()
        return bool(path) and winctl.is_foreground(tile, path)

    def render(self, tile, state, ctx):
        label = self.label(tile)
        img = self.icon(tile, label)
        if state:
            img = img.copy()
            ImageDraw.Draw(img).rounded_rectangle((1, 1, 62, 62), 9, outline=FRAME, width=3)
        return img, label

    def on_press(self, tile, ctx):
        path = os.path.expandvars(tile.get("path", "")).strip()
        # Standard: läuft es schon -> minimieren / zurückholen statt neu öffnen
        if not path:
            return
        res = False if tile.get("multi_open") else winctl.minimize_or_restore(tile, path)
        if res == winctl.ADMIN:
            self._admin_hint(ctx)
        elif not res:
            self._start(tile, path)

    @staticmethod
    def _admin_hint(ctx):
        """Programm läuft als Administrator: Windows lässt PC-Deck es nicht steuern – kurz am Deck melden."""
        if ctx and ctx.link:
            ctx.link.request_osd(None, "Admin-Programm: nicht steuerbar")

    def on_long_press(self, tile, ctx):
        path = os.path.expandvars(tile.get("path", "")).strip()
        if not path:
            return
        exe = winctl.target_exe(tile, path)
        quit_args = tile.get("quit_args", "").strip() or KNOWN_QUIT.get(ntpath.basename(exe or "").lower(), "")
        if exe and quit_args and quit_args != "-" and winctl.is_running(tile, path):
            log.info("Beende %s %s", exe, quit_args)
            try:
                os.startfile(exe, "open", quit_args, os.path.dirname(exe))
            except OSError as e:
                log.error("Konnte %s nicht beenden: %s", exe, e)
            return
        res = winctl.close_if_running(tile, path)
        if res == winctl.ADMIN:
            self._admin_hint(ctx)
        elif not res:
            self._start(tile, path)

    def _start(self, tile, path):
        args = tile.get("args", "").strip()
        cwd = os.path.dirname(path) if os.path.isfile(path) else None
        log.info("Starte %s %s", path, args)
        before = winctl.find_windows(tile, path)  # schon offene Fenster (z. B. bei "jedes Antippen neu")
        winctl.allow_foreground()  # sonst öffnet Windows das neue Fenster manchmal hinten
        try:
            if args:
                os.startfile(path, "open", args, cwd)
            else:
                os.startfile(path)
        except OSError as e:
            log.error("Konnte %s nicht starten: %s", path, e)
            return
        threading.Thread(target=winctl.front_when_ready, args=(tile, path, before), daemon=True).start()


plugin_api.register(Launch())
