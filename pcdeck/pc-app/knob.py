"""Drehknopf am Deck (ab Firmware 10): Drehen und Drücken je nach Programm im Vordergrund.

Das Deck meldet "ROT +n" / "ROT -n" (Rasten, + = im Uhrzeigersinn), ab FW 11 "ROTP +n" (gedrückt
gedreht), "KNOB" (kurz gedrückt) und "KNOBLONG" (ab 0,6 s). Pro Deck steht unter "knob" eine
Standard-Belegung und je Programm ein eigenes Preset:

    "knob": {"default": {...}, "apps": {"discord.exe": {...}, "msedge.exe": {...}}}

Seiten blättern und Kacheln auslösen erledigt der DeckLink selbst (braucht die Verbindung);
alles andere läuft hier nacheinander in einem eigenen Thread, damit die Verbindung nie wartet.
"""

import logging
import queue
import threading

import winaudio
import winctl
import winkeys

log = logging.getLogger("pcdeck")

# Aktionen fürs Drehen (auch gedrückt drehen) und fürs Drücken: (id, Anzeige im Dialog)
ROT_ACTIONS = [("volume", "Lautstärke (System)"), ("app_volume", "Lautstärke des Programms"),
               ("output", "Ausgabegerät wechseln"), ("scroll", "Scrollen"), ("hscroll", "Seitlich scrollen"),
               ("zoom", "Zoomen (Strg + Mausrad)"), ("pages", "Seiten am Deck blättern"),
               ("keys", "Tastenkürzel"), ("off", "Aus")]
PRESS_ACTIONS = [("mute", "Ton stumm / an"), ("mic_mute", "Mikrofon stumm / an"), ("play_pause", "Play / Pause"),
                 ("next", "Nächster Titel"), ("prev", "Voriger Titel"),
                 ("screenshot", "Screenshot (Ausschnitt, Win+Umschalt+S)"), ("output", "Nächstes Ausgabegerät"),
                 ("tile", "Kachel auslösen"), ("page1", "Seite 1 zeigen"), ("keys", "Tastenkürzel"), ("off", "Aus")]
LINK_ACTIONS = {"pages", "page1", "tile"}  # führt der DeckLink aus

DEFAULT_PRESET = {"rot": "volume", "rotp": "output", "press": "mute", "long": "off", "step": 2, "app": "",
                  "devices": [], "devices_names": {},
                  "keys_left": "", "keys_right": "", "keysp_left": "", "keysp_right": "",
                  "keys_press": "", "keys_long": "", "tile_press": "", "tile_long": ""}
VK_PLAY_PAUSE, VK_NEXT, VK_PREV = 0xB3, 0xB0, 0xB1
SCREENSHOT = [0x5B, 0x10, 0x53]  # Win+Umschalt+S
MAX_REPEAT = 20  # Tastenkürzel/Mausrad je Meldung höchstens so oft (sehr schnelles Drehen)

# alte Belegung (FW 10, ein fester Modus) -> Preset
_OLD_MODES = {"volume": {"rot": "volume", "press": "mute", "long": "off"},
              "pages": {"rot": "pages", "press": "page1", "long": "off"},
              "media": {"rot": "volume", "press": "play_pause", "long": "next"},
              "keys": {"rot": "keys", "press": "keys", "long": "keys"},
              "off": {"rot": "off", "rotp": "off", "press": "off", "long": "off"}}


def preset(p):
    """Preset mit Standardwerten ergänzt."""
    out = dict(DEFAULT_PRESET)
    out.update(p or {})
    return out


def settings(cfg):
    """{"default": preset, "apps": {programm klein: preset}} eines Decks (alte Einstellung wird übernommen)."""
    k = dict((cfg or {}).get("knob") or {})
    if "mode" in k:  # Stand FW 10: ein Modus für alles
        old = dict(k)
        k = {"default": dict(old, **_OLD_MODES.get(old.pop("mode"), {}))}
    return {"default": preset(k.get("default")),
            "apps": {a.lower(): preset(p) for a, p in (k.get("apps") or {}).items()}}


_last_choice = [None]


def active_preset(cfg):
    """(Programm im Vordergrund oder None, gültiges Preset): Preset des Programms, sonst die Standard-Belegung.
    Gehört das Fenster einem Hilfsprozess (z. B. steamwebhelper.exe), zählt das Programm, das ihn
    gestartet hat (steam.exe)."""
    s = settings(cfg)
    exe, pid = winctl.foreground_app()
    name = None
    if exe and exe in s["apps"]:
        name = exe
    elif pid:
        name = next((a for a in winctl.ancestors(pid) if a in s["apps"]), None)
    choice = (exe, name)
    if choice != _last_choice[0]:
        _last_choice[0] = choice
        log.info("Drehknopf: %s -> %s", exe or "?", f"Preset {name}" if name else "Standard")
    return (name or exe), (s["apps"][name] if name else s["default"])


def parse_tile(ref):
    """'2:5' -> (seite 2, platz 5) (0-basiert) oder None."""
    try:
        p, i = (int(x) for x in str(ref).split(":"))
        return p, i
    except ValueError:
        return None


def _short(name):
    return name.split(" (")[0] if name else "?"


class Worker:
    """Führt Drehknopf-Aktionen der Reihe nach aus (eigener Thread pro Deck).

    osd(prozent oder None, text): Wert-Anzeige auf dem Deck (vom DeckLink, ab FW 11)."""

    def __init__(self, osd=None):
        self.osd = osd or (lambda pct, text: None)
        self.jobs = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            fn, args = self.jobs.get()
            try:
                fn(*args)
            except Exception:
                log.exception("Fehler beim Drehknopf")

    def _do(self, fn, *args):
        self.jobs.put((fn, args))

    # -- Drehen (pushed = gedrückt gedreht)
    def rotate(self, p, n, exe, pushed=False):
        action = p["rotp" if pushed else "rot"]
        step = max(1, int(p.get("step", 2)))
        reps = min(abs(n), MAX_REPEAT) * (1 if n > 0 else -1)
        if action == "volume":
            self._do(self._volume, n * step)
        elif action == "app_volume":
            self._do(self._app_volume, (p.get("app") or exe or "").lower(), n * step)
        elif action == "output":
            self._do(self._output, p, 1 if n > 0 else -1)
        elif action == "scroll":
            self._do(winkeys.wheel, -reps)  # rechts drehen = nach unten
        elif action == "hscroll":
            self._do(winkeys.wheel, reps, True)
        elif action == "zoom":
            self._do(winkeys.wheel, reps, False, True)
        elif action == "keys":
            prefix = "keysp_" if pushed else "keys_"
            seq = p[prefix + ("right" if n > 0 else "left")]
            if seq.strip():
                for _ in range(abs(reps)):
                    self._do(winkeys.send_sequence, seq)

    # -- Drücken (kurz oder lang)
    def press(self, p, long):
        action = p["long" if long else "press"]
        if action == "mute":
            self._do(self._mute)
        elif action == "mic_mute":
            self._do(self._mic)
        elif action in ("play_pause", "next", "prev"):
            vk = {"play_pause": VK_PLAY_PAUSE, "next": VK_NEXT, "prev": VK_PREV}[action]
            self._do(winkeys.send_combo, [vk])
        elif action == "screenshot":
            self._do(winkeys.send_combo, SCREENSHOT)
        elif action == "output":
            self._do(self._output, p, 1)
        elif action == "keys":
            seq = p["keys_long" if long else "keys_press"]
            if seq.strip():
                self._do(winkeys.send_sequence, seq)

    # -- Aktionen mit Wert-Anzeige
    def _volume(self, delta):
        winaudio.change_volume(delta)
        v = winaudio.volume()
        if v:
            self.osd(None if v[1] else v[0], "Stumm" if v[1] else "Lautstaerke")

    def _app_volume(self, exe, delta):
        name = exe[:-4] if exe.endswith(".exe") else exe
        if not exe:
            self.osd(None, "Kein Programm vorne")
            return
        v = winaudio.change_app_volume(exe, delta)
        if v is None:
            self.osd(None, f"{name}: kein Ton")
        else:
            self.osd(v, name.capitalize())

    def _output(self, p, step):
        dev = winaudio.next_output(p.get("devices") or None, step)
        if dev:
            log.info("Drehknopf: Ausgabegerät -> %s", dev["name"])
            self.osd(None, _short(dev.get("desc") or dev["name"]))

    def _mute(self):
        winaudio.toggle_mute()
        v = winaudio.volume()
        if v:
            self.osd(None if v[1] else v[0], "Stumm" if v[1] else "Lautstaerke")

    def _mic(self):
        winaudio.toggle_mic()
        self.osd(None, "Mikrofon stumm" if winaudio.mic_muted() else "Mikrofon an")
