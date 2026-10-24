"""Konfiguration und USB-Verbindung zu den Decks.

Eine App betreibt beliebig viele Decks gleichzeitig (MKS TS35, BTT TFT35, ...).
Jedes Deck meldet beim Verbinden seine eindeutige ID (aus der Chip-MAC), und
für jede ID gibt es ein eigenes Layout in der Konfiguration.
"""

import copy
import hashlib
import json
import logging
import ntpath
import os
import queue
import secrets
import threading
import time
import unicodedata

import serial
from serial.tools import list_ports

import iconextract
import knob
import netlink
import plugin_api
import theme
import winctl
import winsecret

log = logging.getLogger("pcdeck")

from paths import APP_DIR, CONFIG_PATH  # noqa: E402  (Speicherort siehe paths.py)
LEGACY_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PCDeck")  # bis 21.10.2026


def migrate_legacy():
    """Einmalig: Konfiguration, Sicherungen und eigene Plugins vom alten Ort übernehmen."""
    import shutil
    if os.path.exists(CONFIG_PATH) or not os.path.isfile(os.path.join(LEGACY_DIR, "config.json")):
        return
    os.makedirs(APP_DIR, exist_ok=True)
    for name in os.listdir(LEGACY_DIR):
        src = os.path.join(LEGACY_DIR, name)
        if name.startswith("config.json") and os.path.isfile(src):
            shutil.copy2(src, os.path.join(APP_DIR, name))
        elif name == "plugins" and os.path.isdir(src):
            shutil.copytree(src, os.path.join(APP_DIR, name), dirs_exist_ok=True)
    log.info("Konfiguration von %s nach %s übernommen", LEGACY_DIR, APP_DIR)

MAX_PAGES = 8
COLS_RANGE = (2, 5)
ROWS_RANGE = (1, 3)
BAUD = 921600
# USB-Seriell-Chips, die auf ESP32-Boards sitzen (Espressif, WCH CH340/CH343, Silabs CP210x, FTDI)
ESP_VIDS = {0x303A, 0x1A86, 0x10C4, 0x0403}


# ======================================================================= Konfiguration
def empty_page(cols, rows):
    return [None] * (cols * rows)


def new_deck_config(name):
    return {"name": name, "cols": 4, "rows": 3, "brightness": 200, "pages": [empty_page(4, 3)]}


class Config:
    def __init__(self):
        self.lock = threading.RLock()
        self.data = {"decks": {}, "last_ports": []}
        migrate_legacy()
        self.load()

    def load(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self):
        with self.lock:
            os.makedirs(APP_DIR, exist_ok=True)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_PATH)

    def deck(self, deck_id, create=True):
        with self.lock:
            decks = self.data.setdefault("decks", {})
            if deck_id not in decks and create:
                decks[deck_id] = new_deck_config(f"Deck {len(decks) + 1}")
                self.save()
            return decks.get(deck_id)

    def deck_copy(self, deck_id):
        with self.lock:
            return copy.deepcopy(self.data["decks"].get(deck_id))

    def set_deck(self, deck_id, cfg):
        with self.lock:
            self.data["decks"][deck_id] = copy.deepcopy(cfg)
            self.save()

    def set_tile(self, deck_id, page, idx, tile):
        """Eine Kachel speichern (z. B. von der Plus-Kachel am Deck)."""
        with self.lock:
            self.data["decks"][deck_id]["pages"][page][idx] = copy.deepcopy(tile)
            self.save()

    def net_key(self, deck_id, create=False):
        """Schlüssel der WLAN-Kopplung eines Decks (32 Hex) oder None; create = neu anlegen, falls keiner da."""
        with self.lock:
            keys = self.data.setdefault("net_keys", {})
            key = winsecret.decrypt(keys.get(deck_id, "")) if keys.get(deck_id) else ""
            if not key and create:
                key = secrets.token_hex(16)
                keys[deck_id] = winsecret.encrypt(key)
                self.save()
            return key or None

    def net_keys(self):
        with self.lock:
            ids = list((self.data.get("net_keys") or {}).keys())
        return {i: self.net_key(i) for i in ids}

    def tile(self, deck_id, page, idx):
        with self.lock:
            d = self.data["decks"].get(deck_id)
            try:
                return copy.deepcopy(d["pages"][page][idx])
            except (TypeError, IndexError, KeyError):
                return None


def ascii_label(text, maxlen=24):
    """Display-Schrift kann nur ASCII: Umlaute umschreiben."""
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.split())[:maxlen]


def tile_label(tile):
    if tile.get("label"):
        return tile["label"]
    return plugin_api.get(tile).label(tile)


def tile_icon(tile, label=None):
    """64x64-Icon einer Kachel (vom Kachel-Typ bestimmt)."""
    return plugin_api.get(tile).icon(tile, label if label is not None else tile_label(tile))


def build_payload(cfg):
    """Erzeugt alles, was ans Deck geht, plus Prüfsumme."""
    tiles = []
    h = hashlib.md5()
    h.update(f"{cfg['cols']}x{cfg['rows']}x{len(cfg['pages'])}".encode())
    mono = theme.is_mono(cfg)  # Schwarz/Weiß-Design: Icons in Graustufen
    for p, page in enumerate(cfg["pages"]):
        for i, t in enumerate(page):
            if not t:
                continue
            label = ascii_label(tile_label(t))
            show_icon = not t.get("text_only")
            raw = b""
            if show_icon:
                icon = tile_icon(t, label)
                raw = iconextract.to_rgb565(theme.mono_icon(icon) if mono else icon)
            tiles.append((p, i, label, raw))
            h.update(f"|{p},{i},{label},".encode())
            h.update(raw)
    return tiles, h.hexdigest()[:16]


# ======================================================================= Automatischer Seitenwechsel
def auto_apps(cfg):
    """Programmlisten pro Seite (gleich lang wie cfg["pages"])."""
    lists = list(cfg.get("auto_apps") or [])
    return (lists + [[] for _ in cfg["pages"]])[:len(cfg["pages"])]


def auto_page(cfg, exe):
    """Erste Seite, in deren Liste das Programm steht, sonst None."""
    if not exe:
        return None
    exe = exe.lower()
    for p, apps in enumerate(auto_apps(cfg)):
        if any(a.strip().lower() == exe for a in apps):
            return p
    return None


# ======================================================================= Plus-Kachel
_sug_lock = threading.Lock()
_sug = {"key": None, "sug": None}
SUGGEST_SKIP_DIRS = (r"\windowsapps" + "\\", r"\systemapps" + "\\")  # Store-Apps: keine normale .exe


def _ghost_image(img):
    """Icon blass (ca. 40 %) mit großem Plus in der Mitte."""
    from PIL import Image, ImageDraw
    bg = Image.new("RGB", img.size, iconextract.TILE_BG)
    out = Image.blend(bg, img.convert("RGB"), 0.45)
    d = ImageDraw.Draw(out)
    c, a, w = img.size[0] // 2, 12, 3
    for col, grow in (((20, 20, 24), 2), ((235, 235, 240), 0)):  # dunkler Rand, dann helles Plus
        d.rectangle((c - a - grow, c - w - grow, c + a + grow, c + w + grow), fill=col)
        d.rectangle((c - w - grow, c - a - grow, c + w + grow, c + a + grow), fill=col)
    return out


SUGGEST_LINGER = 10.0  # so lange bleibt die Plus-Kachel, nachdem ihr Programm nicht mehr vorne ist


def current_suggestion():
    """Vorschlag für die Plus-Kachel: Programm bzw. Ordner im Vordergrund.
    {"path", "exe", "label", "img"} oder None.

    Kommt ein anderes Programm nach vorne, wird sofort darauf umgeschaltet. Ist dagegen nichts
    Passendes vorne (Programm geschlossen, PC-Deck, Desktop, Taskleiste ...), bleibt der letzte
    Vorschlag noch SUGGEST_LINGER Sekunden stehen – Zeit genug, ihn am Deck anzutippen."""
    hwnd, pid, exe = winctl.foreground_info()
    path = None
    name = ntpath.basename(exe).lower() if exe else ""
    if exe and pid != os.getpid():
        if name == "explorer.exe":
            path = winctl.foreground_folder()
        elif not (name in FOREGROUND_IGNORE or name == "pc-deck.exe"
                  or any(d in exe.lower() for d in SUGGEST_SKIP_DIRS)):
            path = iconextract.shortcut_for(exe) or exe
    now = time.monotonic()
    with _sug_lock:
        if not path:  # nichts Passendes vorne: letzten Vorschlag nur noch kurz zeigen
            if _sug["sug"] and now - _sug.get("seen", 0) > SUGGEST_LINGER:
                _sug.update(key=None, sug=None)
            return _sug["sug"]
        if _sug["key"] == path:
            _sug["seen"] = now
            return _sug["sug"]
    tile = {"label": "", "path": path, "args": "", "icon": ""}
    label = tile_label(tile)
    try:
        img = _ghost_image(tile_icon(tile, label))
    except Exception:
        log.exception("Plus-Kachel: Icon für %s", path)
        return None
    sug = {"path": path, "exe": exe if name != "explorer.exe" else "", "label": label, "img": img}
    with _sug_lock:
        _sug.update(key=path, sug=sug, seen=now)
    return sug


def _on_page(page, sug):
    """Liegt das vorgeschlagene Programm/der Ordner schon auf dieser Seite?"""
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.expandvars(p))) if p else ""  # noqa: E731
    want = {norm(sug["path"]), norm(sug["exe"])} - {""}
    for t in page:
        if t and plugin_api.type_id(t) == "launch":
            p = t.get("path", "")
            if norm(p) in want or norm(winctl.target_exe(t, os.path.expandvars(p)) or "") in want:
                return True
    return False


# Programme, die keinen Seitenwechsel auslösen (Desktop, Taskleiste, Startmenü, Suche ...)
FOREGROUND_IGNORE = {"explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
                     "textinputhost.exe", "lockapp.exe", "applicationframehost.exe"}


class ForegroundWatcher(threading.Thread):
    """Prüft alle 0,5 s das Vordergrundprogramm und meldet Wechsel an alle verbundenen Decks."""

    def __init__(self, manager):
        super().__init__(daemon=True)
        self.manager = manager
        self.last = None

    def run(self):
        own = os.getpid()
        while self.manager.running:
            try:
                exe, pid = winctl.foreground_app()
                if pid == own or not exe or exe in FOREGROUND_IGNORE:
                    # PC-Deck-Fenster, Desktop, Taskleiste ...: lösen nichts aus, aber das zuletzt
                    # erkannte Programm wird vergessen – kommt es wieder nach vorne (z. B. neu
                    # gestartet), wird das wieder als Wechsel erkannt
                    self.last = None
                elif exe != self.last:
                    self.last = exe
                    for link in self.manager.connected().values():
                        link.on_foreground(exe)
                # Programm, das die Automatik ausgelöst hat, beendet? -> zurück zur eigenen Seite
                running = None
                for link in self.manager.connected().values():
                    if link.auto_active and link.auto_exe:
                        if running is None:
                            running = {name for _, name in winctl._processes().values()}
                        if link.auto_exe not in running:
                            link.on_auto_app_ended()
            except Exception:
                log.exception("Fehler bei der Vordergrund-Prüfung")
            time.sleep(0.5)


# ======================================================================= Verbindung zu einem Deck
class DeckError(IOError):
    """Deck antwortet mit ERR oder gar nicht (Verbindung kann trotzdem weiter bestehen)."""


class DeckRefused(DeckError):
    """Deck hat mit ERR geantwortet – es lebt, nur dieser Befehl ging nicht (z. B. ERR mem)."""


class DeckLink(threading.Thread):
    LIVE_INTERVAL = 1.0  # Sekunden zwischen zwei Abfragen der Live-Kacheln
    PING_INTERVAL = 2.0  # Lebenszeichen ans Deck (FW 4: sonst roter Punkt "PC getrennt")

    def __init__(self, manager, ser, port, deck_id, deck_hash, fw=1, theme_hash="none"):
        super().__init__(daemon=True)
        self.manager = manager
        self.ser = ser
        self.port = port
        self.deck_id = deck_id
        self.deck_hash = deck_hash
        self.fw = fw                 # Firmware-Version des Decks (Live-Kacheln ab 2)
        self.theme_hash = theme_hash  # Prüfsumme des Designs auf dem Deck (ab FW 8)
        self.jobs = queue.Queue()
        self.alive = True
        self.visible_page = 0        # welche Seite das Deck gerade zeigt
        self.live_sent = {}          # (seite, kachel) -> zuletzt gesendeter Zustand
        self.live_errors = set()
        self.soft_logged = False  # ERR bei Live-Bildern nur einmal ins Log
        self.next_live = 0.0
        self.backlog = []            # PRESS/SHOW, die während einer Übertragung kamen
        self.manual_page = 0         # zuletzt selbst gewählte Seite (Ziel beim Zurückspringen)
        self.auto_active = False     # gerade auf einer Seite, die ein Programm ausgelöst hat
        self.auto_exe = None         # dieses Programm (klein geschrieben, z. B. "lightburn.exe")
        self.prev_page = 0           # Seite vor dem letzten Wechsel (Kachel "zurück")
        self.ghost = None            # angezeigte Plus-Kachel: (seite, platz, pfad) oder None
        self.next_ping = 0.0
        self.locked_notice = 0.0     # letzter Hinweis "PC gesperrt" am Deck
        self.knob = knob.Worker(self.request_osd)  # Drehknopf (ab FW 10): Aktionen im eigenen Thread

    # -- Aufrufe von außen (GUI/Tray)
    def request_sync(self, force=False):
        self.jobs.put(("sync", force))

    def request_calibration(self):
        self.jobs.put(("cal", None))

    def request_osd(self, pct, text):
        """Wert-Anzeige auf dem Deck (ab FW 11): pct 0..100 mit Balken, None = nur Text."""
        if self.fw >= 11:
            self.jobs.put(("osd", (pct, text)))

    def request_brightness(self, value):
        self.jobs.put(("bri", value))

    def goto_page(self, page, manual=True):
        """Seite wechseln (z. B. von einer Kachel "Seite wechseln"); manual = wie selbst geblättert."""
        if manual:
            self.manual_page = page
            self.auto_active = False
        self.jobs.put(("page", page))

    def page_count(self):
        cfg = self.manager.config.deck_copy(self.deck_id)
        return len(cfg["pages"]) if cfg else 0

    def on_foreground(self, exe):
        """Automatischer Seitenwechsel: ein anderes Programm ist in den Vordergrund gekommen."""
        cfg = self.manager.config.deck_copy(self.deck_id)
        if not cfg:
            return
        target = auto_page(cfg, exe)
        if target is not None:
            if not self.auto_active:
                self.manual_page = self.visible_page
                self.auto_active = True
            self.auto_exe = exe.lower()
            if target != self.visible_page:
                log.info("%s im Vordergrund -> Seite %d", exe, target + 1)
                self.jobs.put(("page", target))
        elif self.auto_active:
            self.auto_active = False
            if self.manual_page != self.visible_page and self.manual_page < len(cfg["pages"]):
                log.info("%s im Vordergrund -> zurück auf Seite %d", exe, self.manual_page + 1)
                self.jobs.put(("page", self.manual_page))

    def on_auto_app_ended(self):
        """Das Programm, das auf eine Automatik-Seite geschaltet hat, wurde beendet."""
        cfg = self.manager.config.deck_copy(self.deck_id)
        self.auto_active = False
        log.info("%s beendet -> zurück auf Seite %d", self.auto_exe, self.manual_page + 1)
        self.auto_exe = None
        if cfg and self.manual_page != self.visible_page and self.manual_page < len(cfg["pages"]):
            self.jobs.put(("page", self.manual_page))

    # -- intern
    def _readline(self, timeout):
        self.ser.timeout = timeout
        line = self.ser.readline()
        return line.decode("ascii", "replace").strip() if line else None

    def _cmd(self, text, payload=None, timeout=None, collect=None):
        """Befehl senden und auf ACK warten; collect: Liste für die übrigen Antwortzeilen (z. B. NET ...)."""
        if timeout is None:
            timeout = 10.0 if self.via_wlan else 5.0  # WLAN: Funkaussetzer abwarten
        self.ser.write((text + "\n").encode("ascii", "replace"))
        if payload:
            # in Stücken senden, damit der Empfangspuffer des Decks nicht überläuft
            for i in range(0, len(payload), 1024):
                self.ser.write(payload[i:i + 1024])
                time.sleep(0.002)
        end = time.time() + timeout
        while time.time() < end:
            line = self._readline(0.5)
            if line is None:
                continue
            if line == "ACK":
                return True
            if line.startswith("ERR"):
                raise DeckRefused(f"Deck meldet Fehler bei '{text.split()[0]}': {line}")
            if line.startswith(("PRESS ", "LONG ", "SHOW ", "DEL ", "ROT", "KNOB")):
                self.backlog.append(line)  # nach der Übertragung abarbeiten
            elif collect is not None:
                collect.append(line)
        raise DeckError(f"Keine Antwort vom Deck auf '{text.split()[0]}'")

    @property
    def via_wlan(self):
        return isinstance(self.ser, netlink.NetConn)

    def request(self, text, timeout=5.0):
        """Befehl aus einem anderen Thread (z. B. Fenster) ausführen: (ok, Antwortzeilen bzw. Fehlertext)."""
        out = queue.Queue()
        self.jobs.put(("raw", (text, timeout, out)))
        try:
            return out.get(timeout=timeout + 10)
        except queue.Empty:
            return False, "Keine Antwort (Deck beschäftigt oder getrennt)"

    def request_firmware(self, data, progress, done):
        """Firmware aufspielen (ab FW 16, nur USB). Aus dem Link-Thread: progress(0..1), done(ok, text).
        Danach startet das Deck neu; die Verbindung endet und der Manager findet es wieder."""
        self.jobs.put(("fw", (data, progress, done)))

    def _firmware(self, data, progress, done):
        try:
            self._cmd(f"FWBEGIN {len(data)} {hashlib.md5(data).hexdigest()}", timeout=30)
            for k in range(0, len(data), 4096):
                chunk = data[k:k + 4096]
                self._cmd(f"FWDATA {len(chunk)}", chunk, timeout=15)
                progress(min(1.0, (k + len(chunk)) / len(data)))
            self._cmd("FWEND", timeout=30)  # Deck prüft die MD5 und startet dann neu
        except DeckError as e:
            try:
                self._cmd("FWABORT")
            except DeckError:
                pass
            done(False, str(e))
            return False
        except (serial.SerialException, OSError) as e:
            done(False, f"Verbindung unterbrochen: {e}")
            raise
        log.info("Deck %s: Firmware aufgespielt (%d Byte), Neustart", self.deck_id, len(data))
        done(True, "Firmware aufgespielt – das Deck startet neu.")
        return True

    def _sync(self, force):
        cfg = self.manager.config.deck_copy(self.deck_id)
        tiles, digest = build_payload(cfg)
        if not force and digest == self.deck_hash:
            return
        self.manager.notify(f"{cfg['name']}: übertrage {len(tiles)} Kacheln ...")
        self._cmd(f"BEGIN {cfg['cols']} {cfg['rows']} {len(cfg['pages'])} {digest}")
        for p, i, label, raw in tiles:
            self._cmd(f"TILE {p} {i} {1 if raw else 0} {label}")
            if raw:
                self._cmd(f"ICON {p} {i} {len(raw)}", raw)
        self._cmd("END", timeout=10)
        self._cmd(f"BRI {int(cfg.get('brightness', 200))}")
        self.deck_hash = digest
        self.visible_page = 0  # END zeigt Seite 0
        self.manual_page = 0
        self.auto_active = False
        self.live_sent.clear()
        self.ghost = None
        self.next_live = 0.0
        self.manager.notify(f"{cfg['name']}: aktualisiert")

    def _sync_theme(self):
        """Design (Startbild, Seitenbild, Farben) übertragen, wenn es sich geändert hat (ab FW 8)."""
        if self.fw < 8:
            return
        cfg = self.manager.config.deck_copy(self.deck_id)
        t = theme.settings(cfg) if cfg else None
        if t is None:
            return  # "Auf dem Deck lassen" (z. B. Deck von einem anderen PC): Design nicht anrühren
        res = theme.build(t, self._logo())
        if not res:
            if self.theme_hash != "none":
                self._cmd("THEME off")
                self.theme_hash = "none"
            return
        if res["hash"] == self.theme_hash:
            return
        self.manager.notify(f"{cfg['name']}: übertrage Design ...")
        for name in ("boot", "page"):
            data = res[name]
            self._cmd(f"IMG {name} {len(data)}")
            for k in range(0, len(data), 4096):  # Blöcke mit ACK: Flash-Schreiben bremst den Empfang
                self._cmd(f"DATA {len(data[k:k + 4096])}", data[k:k + 4096], timeout=10)
            self._cmd("IMGEND", timeout=10)
        self._cmd(f"THEME {res['theme_line']}", timeout=10)
        self.theme_hash = res["hash"]
        self.live_sent.clear()  # Deck hat die Seite neu gezeichnet
        self.ghost = None
        self.manager.notify(f"{cfg['name']}: Design aktualisiert")

    def _logo(self):
        """Logo-Text des Decks (Widmung, Name, TM) fürs Startbild – fest in der Firmware (ab FW 14),
        die App liest ihn nur. Ältere Firmware: der Text von Deck 1."""
        if getattr(self, "logo", None) is None:
            self.logo = theme.LOGO
            if self.fw >= 14:
                lines = []
                try:
                    self._cmd("LOGO", collect=lines)
                    p = next((l.split() for l in lines if l.startswith("LOGO ")), [])
                    if len(p) == 4:
                        self.logo = (bytes.fromhex(p[1]).decode("utf-8", "replace"),
                                     bytes.fromhex(p[2]).decode("utf-8", "replace"), p[3] == "1")
                except (DeckError, ValueError):
                    pass
        return self.logo

    def request_live_update(self, delay=0.15):
        """Live-Kacheln bald neu abfragen (z. B. direkt nach einem Tipper)."""
        self.next_live = min(self.next_live, time.time() + delay)

    def _live(self):
        """Live-Kacheln der sichtbaren Seite abfragen und Änderungen ans Deck schicken."""
        if self.fw < 2:
            return
        cfg = self.manager.config.deck_copy(self.deck_id)
        if not cfg or self.visible_page >= len(cfg["pages"]):
            return
        p = self.visible_page
        for i, t in enumerate(cfg["pages"][p]):
            if not t:
                continue
            tt = plugin_api.get(t)
            if not tt.live:
                continue
            ctx = plugin_api.PressContext(self.deck_id, p, i, self)
            try:
                state = tt.poll(t, ctx)
                if self.live_sent.get((p, i), object()) == state:
                    continue
                if (p, i) not in self.live_sent and state == tt.idle_state:
                    self.live_sent[(p, i)] = state  # sieht aus wie im Flash – nichts zu senden
                    continue
                img, label = tt.render(t, state, ctx)
            except Exception:
                if (p, i, tt.id) not in self.live_errors:
                    self.live_errors.add((p, i, tt.id))
                    log.exception("Fehler in Live-Kachel '%s'", tt.id)
                continue
            label = ascii_label(t.get("label") or label or "")
            if t.get("text_only") or img is None:
                self._soft(f"UPD {p} {i} 0 {label}")
            else:
                raw = iconextract.to_rgb565(theme.mono_icon(img) if theme.is_mono(cfg) else img)
                self._soft(f"UPD {p} {i} 1 {label}", raw)
            self.live_sent[(p, i)] = state  # auch bei ERR: nicht jede Sekunde neu versuchen
        self._plus_tile(cfg, p)

    def _plus_tile(self, cfg, p):
        """Plus-Kachel: Programm im Vordergrund blass auf dem ersten freien Platz anbieten."""
        if self.fw < 6:
            return
        want = None
        if cfg.get("suggest", True):
            page = cfg["pages"][p][:cfg["cols"] * cfg["rows"]]
            slot = next((i for i, t in enumerate(page) if not t), None)
            sug = current_suggestion() if slot is not None else None
            if sug and not any(_on_page(pg, sug) for pg in cfg["pages"]):
                want = (p, slot, sug["path"])
        if want == self.ghost:
            return
        if self.ghost and self.ghost[0] == p and (not want or want[1] != self.ghost[1]):
            self._soft(f"CLR {p} {self.ghost[1]}")
        if want:
            img = theme.mono_icon(sug["img"]) if theme.is_mono(cfg) else sug["img"]
            self._soft(f"UPD {p} {want[1]} 1 {ascii_label(sug['label'])}", iconextract.to_rgb565(img))
        self.ghost = want

    def _soft(self, text, payload=None):
        """Wie _cmd, aber ein ERR des Decks (z. B. kein Speicher für ein Live-Icon) trennt nicht."""
        try:
            self._cmd(text, payload)
        except DeckRefused as e:
            if not self.soft_logged:
                self.soft_logged = True
                log.warning("%s (%s) – weiter ohne dieses Bild", e, self.deck_id)

    def _show_page(self, page):
        """Seite am Deck zeigen (im Thread der Verbindung)."""
        if page == self.visible_page:
            return
        self._cmd(f"PAGE {page}")
        self.prev_page = self.visible_page
        self.visible_page = page
        self.live_sent.clear()  # Deck verwirft beim Seitenwechsel die Live-Kacheln
        self.ghost = None
        self.request_live_update(0)

    def _on_knob(self, line):
        """Drehknopf: ROT/ROTP +n/-n, KNOB, KNOBLONG – Belegung nach dem Programm im Vordergrund."""
        cfg = self.manager.config.deck_copy(self.deck_id)
        exe, p = knob.active_preset(cfg)
        kind, _, arg = line.partition(" ")
        if kind in ("ROT", "ROTP"):
            try:
                n = int(arg)
            except ValueError:
                return
            pushed = kind == "ROTP"
            if p["rotp" if pushed else "rot"] == "pages":
                count = len(cfg["pages"]) if cfg else 0
                if count > 1:
                    page = (self.visible_page + n) % count
                    self.manual_page = page  # wie selbst geblättert
                    self.auto_active = False
                    self._show_page(page)
                    self.request_osd(None, f"Seite {page + 1} / {count}")
            else:
                self.knob.rotate(p, n, exe, pushed)
        elif kind in ("KNOB", "KNOBLONG"):
            long = kind == "KNOBLONG"
            action = p["long" if long else "press"]
            if action == "page1":
                self.manual_page = 0
                self.auto_active = False
                self._show_page(0)
            elif action == "tile":
                ref = knob.parse_tile(p["tile_long" if long else "tile_press"])
                tile = self.manager.config.tile(self.deck_id, *ref) if ref else None
                if tile:
                    ctx = plugin_api.PressContext(self.deck_id, ref[0], ref[1], self)
                    threading.Thread(target=self._press, args=(tile, ctx, False), daemon=True).start()
            else:
                self.knob.press(p, long)

    def _locked(self, line):
        """PC gesperrt: Tipper, Löschen, Plus-Kachel und Drehknopf lösen nichts aus (nur Blättern am Deck)."""
        if not line.startswith(("PRESS ", "LONG ", "DEL ", "ROT", "KNOB")) or not winctl.is_locked():
            return False
        now = time.time()
        if now - self.locked_notice > 2.0:  # Hinweis höchstens alle 2 s (schnelles Drehen)
            self.locked_notice = now
            log.info("PC gesperrt – Eingabe vom Deck ignoriert (%s)", line.split()[0])
            self.request_osd(None, "PC gesperrt")
        return True

    def _on_line(self, line):
        if self._locked(line):
            return
        if line.startswith(("ROT", "KNOB")):
            self._on_knob(line)
        elif line.startswith("SHOW "):
            try:
                new = int(line.split()[1])
                if new != self.visible_page:
                    self.prev_page = self.visible_page
                self.visible_page = new
            except (ValueError, IndexError):
                return
            self.live_sent.clear()  # Deck hat die Live-Kacheln verworfen
            self.ghost = None
            self.request_live_update(0)
            # selbst geblättert: hat Vorrang bis zum nächsten Programmwechsel
            self.manual_page = self.visible_page
            self.auto_active = False
        elif line.startswith("DEL "):
            try:
                _, p, i = line.split()
                self._delete_tile(int(p), int(i))
            except ValueError:
                return
        elif line.startswith(("PRESS ", "LONG ")):
            long = line.startswith("LONG ")
            try:
                _, p, i = line.split()
                tile = self.manager.config.tile(self.deck_id, int(p), int(i))
            except ValueError:
                return
            if not tile and not long and self.ghost and self.ghost[:2] == (int(p), int(i)):
                self._add_from_plus(int(p), int(i))
                return
            if tile:
                ctx = plugin_api.PressContext(self.deck_id, int(p), int(i), self)
                threading.Thread(target=self._press, args=(tile, ctx, long), daemon=True).start()

    def _delete_tile(self, p, i):
        """Am Deck nach Sicherheitsabfrage gelöscht: Kachel entfernen (vorher Sicherung)."""
        tile = self.manager.config.tile(self.deck_id, p, i)
        if not tile:
            return
        try:
            import shutil
            shutil.copy2(CONFIG_PATH, CONFIG_PATH + ".bak-delete")
        except OSError as e:
            log.warning("Sicherung vor dem Löschen fehlgeschlagen: %s", e)
        self.manager.config.set_tile(self.deck_id, p, i, None)
        self.manager.last_deleted = (self.deck_id, p, i, tile)
        log.info("Gelöschte Kachel (Seite %d, Platz %d): %s", p + 1, i + 1, json.dumps(tile, ensure_ascii=False))
        self.manager.notify(f"Kachel gelöscht: {tile_label(tile)} (Seite {p + 1})")
        self.jobs.put(("sync", False))

    def _add_from_plus(self, p, i):
        """Plus-Kachel angetippt: vorgeschlagenes Programm fest auf diesem Platz speichern."""
        sug = current_suggestion()
        if not sug or sug["path"] != self.ghost[2]:
            return
        self.manager.config.set_tile(self.deck_id, p, i, {"label": "", "path": sug["path"], "args": "", "icon": ""})
        self.ghost = None
        self.manager.notify(f"Kachel hinzugefügt: {sug['label']} (Seite {p + 1})")
        self.jobs.put(("sync", False))

    def _press(self, tile, ctx, long=False):
        plugin_api.press(tile, ctx, long)
        if plugin_api.get(tile).live:
            self.request_live_update()  # Änderung (z. B. Stoppuhr gestartet) sofort zeigen

    def _reset_foreign_wlan(self):
        """Deck per USB an einem PC ohne Kopplung (anderer PC, Neuinstallation): WLAN, Passwort und
        Schlüssel im Deck löschen – es verbindet sich nur mit dem PC, mit dem es gekoppelt ist."""
        if self.fw < 12 or self.via_wlan or self.manager.config.net_key(self.deck_id):
            return
        lines = []
        try:
            self._cmd("WIFI STATUS", collect=lines)
        except DeckError:
            return
        parts = next((l.split() for l in lines if l.startswith("WIFI ")), [])
        if len(parts) >= 5 and parts[4] != "-":
            self._cmd("WIFI RESET")
            name = (self.manager.config.deck_copy(self.deck_id) or {}).get("name", self.deck_id)
            self.manager.notify(f"{name}: WLAN-Einstellungen des Decks zurückgesetzt (anderer PC) – "
                                "bei Bedarf unter „WLAN ...“ neu einrichten")

    def run(self):
        try:
            self._reset_foreign_wlan()
            self._sync(False)
            self._sync_theme()
            while self.alive:
                try:
                    job, arg = self.jobs.get_nowait()
                    if job == "sync":
                        self._sync(arg)
                        self._sync_theme()
                    elif job == "cal":
                        self._cmd("CAL")
                    elif job == "bri":
                        self._cmd(f"BRI {int(arg)}")
                    elif job == "page":
                        self._show_page(int(arg))
                    elif job == "raw":
                        text, tmo, out = arg
                        lines = []
                        try:
                            self._cmd(text, timeout=tmo, collect=lines)
                            out.put((True, lines))
                        except DeckError as e:
                            out.put((False, str(e)))
                    elif job == "osd":
                        pct, text = arg
                        self._cmd(f"OSD {'-' if pct is None else int(pct)} {ascii_label(text, 40)}")
                    elif job == "fw":
                        if self._firmware(*arg):
                            time.sleep(1.0)  # Deck startet neu – Verbindung beenden, Manager sucht neu
                            break
                except queue.Empty:
                    pass
                while self.backlog:
                    self._on_line(self.backlog.pop(0))
                if self.fw >= 4 and time.time() >= self.next_ping:
                    self.next_ping = time.time() + self.PING_INTERVAL
                    self._cmd("PING")
                if time.time() >= self.next_live:
                    self.next_live = time.time() + self.LIVE_INTERVAL
                    self._live()
                line = self._readline(0.05)
                if line:
                    self._on_line(line)
        except (serial.SerialException, OSError) as e:
            log.warning("Deck %s an %s getrennt: %s", self.deck_id, self.port, e)
        finally:
            self.alive = False
            try:
                self.ser.close()
            except Exception:
                pass
            self.manager.link_lost(self)

    def stop(self):
        self.alive = False


# ======================================================================= Alle Decks verwalten
class DeckManager(threading.Thread):
    """Sucht regelmäßig nach neu angesteckten Decks und startet pro Deck einen DeckLink."""

    def __init__(self, config, on_change=None):
        super().__init__(daemon=True)
        self.config = config
        self.on_change = on_change or (lambda msg=None: None)
        self.links = {}          # deck_id -> DeckLink
        self.last_deleted = None  # (deck_id, seite, platz, kachel) – fürs Konfigurationsfenster
        self.busy_ports = set()
        self.lock = threading.Lock()
        self.running = True
        self.rescan = threading.Event()

    def notify(self, msg=None):
        if msg:
            log.info(msg)
        try:
            self.on_change(msg)
        except Exception:
            pass

    def connected(self):
        with self.lock:
            return {k: v for k, v in self.links.items() if v.alive}

    def link(self, deck_id):
        with self.lock:
            l = self.links.get(deck_id)
            return l if l and l.alive else None

    def link_lost(self, link):
        with self.lock:
            if self.links.get(link.deck_id) is link:
                del self.links[link.deck_id]
            self.busy_ports.discard(link.port)
        self.notify(f"Deck {self.config.deck(link.deck_id)['name']} getrennt")

    def _candidates(self):
        ports = list(list_ports.comports())
        with self.lock:
            free = [p for p in ports if p.device not in self.busy_ports]
        esp = [p for p in free if p.vid in ESP_VIDS]
        return esp or [p for p in free if p.vid is not None]

    def _probe(self, port):
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = BAUD
        ser.timeout = 0.3
        ser.write_timeout = 2
        ser.dtr = False   # verhindert Reset beim Öffnen (Auto-Reset-Schaltung)
        ser.rts = False
        try:
            ser.open()
        except serial.SerialException:
            return None
        try:
            time.sleep(0.3)
            ser.reset_input_buffer()
            for _ in range(4):
                ser.write(b"\nHELLO\n")
                end = time.time() + 1.0
                while time.time() < end:
                    line = ser.readline().decode("ascii", "replace").strip()
                    if line.startswith("DECK "):
                        parts = line.split()
                        if len(parts) >= 6:
                            try:
                                fw = int(parts[1])
                            except ValueError:
                                fw = 1
                            return ser, parts[4], parts[5], fw, (parts[7] if len(parts) >= 8 else "none")
                time.sleep(0.5)  # evtl. bootet das Board gerade noch
        except (serial.SerialException, OSError):
            pass
        ser.close()
        return None

    def scan_once(self):
        for p in self._candidates():
            res = self._probe(p.device)
            if not res:
                continue
            ser, deck_id, deck_hash, fw, theme_hash = res
            with self.lock:
                old = self.links.get(deck_id)
            if old and old.alive:
                if not old.via_wlan:
                    ser.close()
                    continue
                old.stop()  # Deck jetzt per USB da: USB hat Vorrang vor WLAN
                old.ser.close()
            cfg = self.config.deck(deck_id)  # legt neues Layout an, falls unbekannt
            link = DeckLink(self, ser, p.device, deck_id, deck_hash, fw, theme_hash)
            with self.lock:
                self.links[deck_id] = link
                self.busy_ports.add(p.device)
            link.start()
            self.notify(f"{cfg['name']} verbunden ({p.device})")

    def scan_wlan(self):
        """Gekoppelte Decks im WLAN suchen und verbinden (nur solche, die nicht schon verbunden sind)."""
        keys = {i: k for i, k in self.config.net_keys().items() if k}
        with self.lock:
            missing = [i for i in keys if not (i in self.links and self.links[i].alive)]
        if not missing:
            return
        for deck_id, (ip, port, fw, busy) in netlink.discover().items():
            if deck_id not in missing or busy:
                continue
            res = netlink.connect(ip, port, keys[deck_id])
            if not res:
                log.warning("Deck %s im WLAN (%s): Anmeldung fehlgeschlagen – neu koppeln?", deck_id, ip)
                continue
            conn, rid, deck_hash, fw, theme_hash = res
            with self.lock:
                if rid in self.links and self.links[rid].alive:
                    conn.close()
                    continue
            cfg = self.config.deck(rid)
            name = f"WLAN {ip}"
            link = DeckLink(self, conn, name, rid, deck_hash, fw, theme_hash)
            with self.lock:
                self.links[rid] = link
            link.start()
            self.notify(f"{cfg['name']} verbunden ({name})")

    def run(self):
        ForegroundWatcher(self).start()
        while self.running:
            try:
                self.scan_once()
                self.scan_wlan()
            except Exception as e:
                log.exception("Fehler bei der Suche: %s", e)
            self.rescan.wait(3.0)
            self.rescan.clear()

    def stop(self):
        self.running = False
        self.rescan.set()
        for l in list(self.links.values()):
            l.stop()
