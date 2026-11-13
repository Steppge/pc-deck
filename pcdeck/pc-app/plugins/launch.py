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
import shutil
import subprocess
import threading
import time

from PIL import ImageDraw

import iconextract
import plugin_api
import winctl
import winkeys
from paths import APP_DIR
from plugin_api import Field, TileType

log = logging.getLogger("pcdeck")


FRAME = (80, 200, 120)  # grün, wie "aktiv" bei den Audio-Kacheln

# Programme, die beim Schließen des Fensters nur in den Infobereich gehen: lang antippen
# beendet sie stattdessen mit diesem Parameter (Feld quit_args, "-" = abschalten)
KNOWN_QUIT = {"steam.exe": "-shutdown"}

# Webadressen (ab App 1.054): wo öffnen
URL_MODES = [("tab", "Neuer Tab"), ("window", "Neues Fenster"),
             ("same", "Gleicher Tab (ersetzt den aktiven Tab, z. B. für Streams)"),
             ("stream", "Eigenes Stream-Fenster (eigenes Profil, ohne alte Tabs, immer nur ein Stream)")]
# Gecko-Browser: andere Startparameter als die Chromium-Browser (Chrome, Edge, Brave, Opera, Vivaldi)
GECKO = ("firefox.exe", "librewolf.exe", "waterfox.exe", "floorp.exe", "zen.exe")
# Sitzungsdateien im eigenen Profil, die beim Start alte Tabs zurückholen würden
SESSION_FILES = ("Sessions", "Current Session", "Current Tabs", "Last Session", "Last Tabs",  # Chromium (Default\)
                 "sessionstore.jsonlz4", "sessionstore-backups", "recovery.jsonlz4")      # Gecko
_last_same = {"hwnd": 0}  # Browser-Fenster, das zuletzt eine "Gleicher Tab"-Kachel bekam
_same_lock = threading.Lock()


def _is_url(path):
    return path.lower().startswith(("http://", "https://"))


class Launch(TileType):
    id = "launch"
    live = True  # nur für den Rahmen "im Vordergrund"
    idle_state = False  # ohne Rahmen = gespeichertes Bild, nach Seitenwechsel nichts senden
    name = "Programm / Ordner öffnen"
    fields = [
        Field("path", "Ziel (Programm, Datei, Ordner oder Webadresse):", kind="path", width=60),
        Field("args", "Startparameter (optional):", width=60),
        Field("multi_open", "Jedes Antippen öffnet neu (mehrere Fenster erlaubt)", kind="bool"),
        Field("url_browser", "Webadresse öffnen mit:", kind="choice", default="",
              choices=lambda: [("", "Standard-Browser")] + winctl.installed_browsers()),
        Field("url_mode", "Webadresse öffnen in:", kind="choice", default="tab", choices=URL_MODES),
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
        if _is_url(path) and self._close_stream(tile):
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

    @staticmethod
    def _open_url(tile, path):
        """Webadresse im eingestellten Browser öffnen: neuer Tab, neues Fenster oder aktiver Tab.
        False = wie bisher über Windows (nur ohne eigens gewählten Browser). Ein gewählter Browser wird
        immer genommen, nie ersatzweise ein anderer."""
        chosen = tile.get("url_browser", "")
        if chosen and not os.path.isfile(chosen):
            log.error("Eingestellter Browser %s nicht gefunden – nichts geöffnet", chosen)
            return True
        done = Launch._open_url_mode(tile, path, chosen)
        if done or not chosen:
            return done
        winctl.allow_foreground()  # gewählter Browser, aber Fenster/Tab ging nicht: dort neuer Tab
        try:
            os.startfile(chosen, "open", f'"{path}"')
        except OSError as e:
            log.error("Browser %s ging nicht: %s", chosen, e)
        return True

    @staticmethod
    def _open_url_mode(tile, path, chosen):
        mode = tile.get("url_mode", "tab")
        if mode == "tab":
            return False
        exe = chosen or winctl.browser_exe()
        if not exe:
            return False
        if mode == "stream":
            return Launch._open_stream(exe, path)
        if mode == "window":
            winctl.allow_foreground()
            try:
                os.startfile(exe, "open", f'--new-window "{path}"')
                log.info("Öffne %s in neuem Fenster", path)
                return True
            except OSError as e:
                log.error("Neues Browser-Fenster ging nicht: %s", e)
                return False
        with _same_lock:
            wins = winctl.browser_windows(exe)
            if not wins:
                return False
            hwnd = _last_same["hwnd"] if _last_same["hwnd"] in wins else wins[0]
            if not winctl.bring_to_front(hwnd):
                log.info("Browser ließ sich nicht nach vorne holen – öffne neuen Tab")
                return False
            # erst prüfen, dann tippen: Esc (Vollbild-Video verlassen), Strg+L (Adresszeile), Adresse, Enter
            for combo in ("Esc", "Strg+L"):
                if winctl.user32.GetForegroundWindow() != hwnd:
                    return False
                winkeys.send_combo(winkeys.parse_combo(combo))
                time.sleep(0.15)
            if winctl.user32.GetForegroundWindow() != hwnd:
                return False
            winkeys.type_text(path + "\n")
            _last_same["hwnd"] = hwnd
            log.info("Öffne %s im aktiven Tab", path)
            return True

    @staticmethod
    def _stream_dir(exe):
        """Eigener Profil-Ordner der Stream-Instanz dieses Browsers (nur PC-Deck benutzt ihn)."""
        stem = ntpath.splitext(ntpath.basename(exe))[0].lower() or "browser"
        return os.path.join(APP_DIR, "browser", stem)

    @staticmethod
    def _open_stream(exe, path):
        """Stream-Fenster: eigene Browser-Instanz mit eigenem Profil. Läuft sie, wird sie geschlossen und mit
        der neuen Adresse neu gestartet – so läuft immer genau ein Stream, alte Tabs kommen nie zurück."""
        prof = Launch._stream_dir(exe)
        with _same_lock:
            if not winctl.close_instance(exe, prof):
                log.error("Stream-Fenster ließ sich nicht schließen – nichts geöffnet")
                return True
            os.makedirs(prof, exist_ok=True)
            for base in (prof, os.path.join(prof, "Default")):
                for name in SESSION_FILES:
                    p = os.path.join(base, name)
                    try:
                        if os.path.isdir(p):
                            shutil.rmtree(p, ignore_errors=True)
                        elif os.path.isfile(p):
                            os.remove(p)
                    except OSError as e:
                        log.warning("Sitzung %s nicht gelöscht: %s", p, e)
            if ntpath.basename(exe).lower() in GECKO:
                cmd = [exe, "-profile", prof, "-no-remote", "-new-window", path]
            else:
                cmd = [exe, f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check",
                       "--hide-crash-restore-bubble", "--new-window", path]
            winctl.allow_foreground()
            try:
                subprocess.Popen(cmd, close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            except OSError as e:
                log.error("Stream-Fenster ging nicht: %s", e)
                return True
            log.info("Öffne %s im Stream-Fenster (%s)", path, ntpath.basename(exe))
            return True

    @staticmethod
    def _close_stream(tile):
        """Lang antippen bei einer Stream-Kachel: das Stream-Fenster schließen. True = war eine Stream-Kachel."""
        if tile.get("url_mode") != "stream":
            return False
        exe = tile.get("url_browser", "") or winctl.browser_exe()
        if exe:
            with _same_lock:
                winctl.close_instance(exe, Launch._stream_dir(exe))
        return True

    def _start(self, tile, path):
        if _is_url(path) and self._open_url(tile, path):
            return
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
