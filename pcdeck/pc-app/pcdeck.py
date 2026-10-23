"""PC-Deck – Windows-App für ESP32-Touch-Decks.

Läuft im Infobereich der Taskleiste, erkennt angesteckte Decks automatisch
(beliebig viele gleichzeitig), überträgt Layout und Icons und startet
Programme / Ordner, wenn auf dem Deck eine Kachel angetippt wird.

Start:  pythonw pcdeck.py
"""

import ctypes
import logging
import os
import queue
import subprocess
import sys
import tkinter as tk

from PIL import Image, ImageDraw
import pystray

from deck import APP_DIR, Config, DeckManager
from config_gui import ConfigWindow

try:
    from tkinterdnd2 import TkinterDnD
except ImportError:
    TkinterDnD = None

APP_NAME = "PC-Deck"
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup",
                           "PC-Deck.lnk")


# ======================================================================= Hilfen
def setup_logging():
    os.makedirs(APP_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(APP_DIR, "pcdeck.log"), encoding="utf-8"),
                  logging.StreamHandler(sys.stdout) if sys.stdout else logging.NullHandler()],
    )


def single_instance():
    """Nur eine App-Instanz zulassen (sonst streiten sich zwei um die COM-Ports)."""
    if os.name != "nt":
        return True
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "PCDeck_SingleInstance")
    single_instance.handle = handle  # Referenz halten
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def tray_image(connected):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 6, 62, 58), 8, fill=(44, 48, 58))
    color = (80, 150, 255) if connected else (130, 135, 150)
    for r in range(2):
        for c in range(3):
            x, y = 9 + c * 17, 13 + r * 20
            d.rounded_rectangle((x, y, x + 13, y + 15), 3, fill=color)
    return img


def launch_command():
    """Befehl, mit dem die App beim Windows-Start aufgerufen wird."""
    if getattr(sys, "frozen", False):  # als .exe (PyInstaller)
        return sys.executable, ""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    return exe, f'"{os.path.abspath(__file__)}"'


def autostart_enabled():
    return os.path.isfile(STARTUP_LNK)


def set_autostart(enable):
    if not enable:
        if os.path.isfile(STARTUP_LNK):
            os.remove(STARTUP_LNK)
        return
    target, args = launch_command()
    workdir = os.path.dirname(os.path.abspath(sys.argv[0]))
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$s.TargetPath=$env:TGT;$s.Arguments=$env:ARGS;$s.WorkingDirectory=$env:WD;$s.Save()"
    )
    env = dict(os.environ, LNK=STARTUP_LNK, TGT=target, ARGS=args, WD=workdir)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ======================================================================= App
class App:
    def __init__(self):
        self.root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.ui_queue = queue.Queue()
        self.config = Config()
        self.manager = DeckManager(self.config, on_change=lambda msg=None: self.ui_queue.put(("changed", msg)))
        self.window = None
        self.icon = pystray.Icon(
            "pcdeck", tray_image(False), APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem("Konfigurieren ...", lambda: self.ui_queue.put(("open", None)), default=True),
                pystray.MenuItem("Alle Decks neu übertragen", lambda: self.ui_queue.put(("resync", None))),
                pystray.MenuItem("Mit Windows starten", lambda: self.ui_queue.put(("autostart", None)),
                                 checked=lambda item: autostart_enabled()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Beenden", lambda: self.ui_queue.put(("quit", None))),
            ),
        )

    def run(self):
        self.icon.run_detached()
        self.manager.start()
        self.root.after(200, self.poll)
        self.root.mainloop()

    def poll(self):
        try:
            while True:
                what, arg = self.ui_queue.get_nowait()
                getattr(self, "on_" + what)(arg)
        except queue.Empty:
            pass
        self.root.after(200, self.poll)

    # -- Ereignisse
    def on_changed(self, msg):
        connected = self.manager.connected()
        self.icon.icon = tray_image(bool(connected))
        names = [self.config.deck(i)["name"] for i in connected]
        self.icon.title = f"{APP_NAME} – " + (", ".join(names) if names else "kein Deck verbunden")
        if self.window and self.window.winfo_exists():
            self.window.refresh_status()
            self.window._update_status()
            if msg and msg.startswith("Kachel hinzugefügt"):
                self.window.merge_from_store()
            elif msg and msg.startswith("Kachel gelöscht"):
                self.window.apply_deletion(self.manager.last_deleted)
        # Neues Deck ohne Kacheln? Dann gleich das Konfigurationsfenster öffnen.
        if msg and msg.endswith(")") and "verbunden (" in msg:
            for did in connected:
                d = self.config.deck(did)
                if not any(t for page in d["pages"] for t in page):
                    self.on_open(did)
                    break

    def on_open(self, deck_id=None):
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return
        self.window = ConfigWindow(self.root, self.config, self.manager, deck_id)

    def on_resync(self, _):
        for link in self.manager.connected().values():
            link.request_sync(force=True)

    def on_autostart(self, _):
        try:
            set_autostart(not autostart_enabled())
        except Exception as e:
            logging.error("Autostart konnte nicht geändert werden: %s", e)
        self.icon.update_menu()

    def on_quit(self, _):
        self.manager.stop()
        self.icon.stop()
        self.root.quit()


def main():
    setup_logging()
    if not single_instance():
        ctypes.windll.user32.MessageBoxW(None, "PC-Deck läuft bereits (Symbol im Infobereich).", APP_NAME, 0x40)
        return
    App().run()


if __name__ == "__main__":
    main()
