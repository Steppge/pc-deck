"""Klipper-3D-Drucker über die Moonraker-HTTP-API (nur Standardbibliothek: urllib/json).

objects(url)      -> (Objektnamen, Höchsttemperaturen) des Druckers, 60 s gemerkt (was gibt es: Sensoren, Makros …).
query(url)        -> Zustand: {objekt: {feld: wert}} für alle Objekte, die die Drucker-Kacheln brauchen;
                     kurz zwischengespeichert, damit alle Drucker-Kacheln eine Abfrage teilen.
gcode(url, text)  -> G-Code ausführen (wartet, bis Klipper fertig ist, z. B. bei G28).
info(url)         -> Klipper-Zustand und Version (für „Verbindung testen“).
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://printer.local/"
CACHE_SECONDS = 0.8
OBJECTS_SECONDS = 60
# immer mit abgefragt (fehlende ignoriert Klipper)
BASE_OBJECTS = ("heater_bed", "extruder", "fan", "print_stats", "display_status", "virtual_sdcard", "gcode_move",
                "firmware_retraction", "toolhead", "pause_resume")
SENSOR_PREFIXES = ("temperature_sensor ", "temperature_host ", "temperature_fan ", "heater_generic ")
_cache = {}    # url -> (Zeit, Zustand oder Fehler)
_objects = {}  # url -> (Zeit, (Namen, Höchsttemperaturen))
_lock = threading.Lock()


class PrinterError(Exception):
    pass


def base(url):
    url = (url or DEFAULT_URL).strip()
    if "://" not in url:
        url = "http://" + url
    return url.rstrip("/") + "/"


_addr = {}  # Rechnername -> (Zeit, IPv4-Adresse)


def _ipv4_url(url):
    """printer.local zuerst als IPv4 auflösen: Windows probiert sonst IPv6, das Moonraker oft nicht annimmt –
    jede Abfrage dauerte dann 2 s länger. Ergebnis 5 min merken. (URL, Host-Kopf oder None)"""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    try:
        socket.inet_aton(host)
        return url, None
    except OSError:
        pass
    with _lock:
        t, ip = _addr.get(host, (0, None))
    if not ip or time.monotonic() - t > 300:
        try:
            ip = socket.getaddrinfo(host, parts.port or 80, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        except (OSError, IndexError):
            return url, None
        with _lock:
            _addr[host] = (time.monotonic(), ip)
    netloc = ip + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc)), parts.netloc


def _request(url, path, data=None, timeout=3.0):
    full, host = _ipv4_url(base(url) + path)
    headers = {"Content-Type": "application/json"}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(full, method="POST" if data is not None else "GET",
                                 data=None if data is None else json.dumps(data).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("result")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8", "replace"))["error"]["message"]
        except Exception:
            msg = f"HTTP {e.code}"
        raise PrinterError(str(msg)) from None
    except (OSError, ValueError) as e:
        raise PrinterError(f"nicht erreichbar ({e})") from None


def objects(url, max_age=OBJECTS_SECONDS):
    """(Liste der Klipper-Objekte, {heizung: max_temp}) – z. B. "gcode_macro g29", "temperature_sensor CB1"."""
    key = base(url)
    with _lock:
        t, v = _objects.get(key, (0, None))
    if v is not None and time.monotonic() - t <= max_age:
        return v
    names = (_request(key, "printer/objects/list", timeout=3.0) or {}).get("objects") or []
    maxt = {}
    try:
        st = (_request(key, "printer/objects/query?configfile=settings", timeout=3.0) or {}).get("status") or {}
        for name, sec in ((st.get("configfile") or {}).get("settings") or {}).items():
            if isinstance(sec, dict) and "max_temp" in sec:
                maxt[name] = float(sec["max_temp"])
    except PrinterError:
        pass
    v = (list(names), maxt)
    with _lock:
        _objects[key] = (time.monotonic(), v)
    return v


def query(url, max_age=CACHE_SECONDS):
    """Zustand {objekt: {feld: wert}} (fehlende Objekte fehlen einfach)."""
    key = base(url)
    with _lock:
        t, v = _cache.get(key, (0, None))
    if v is not None and time.monotonic() - t <= max_age:
        if isinstance(v, Exception):
            raise v
        return v
    try:
        try:
            extra = [o for o in objects(key)[0] if o.startswith(SENSOR_PREFIXES)]
        except PrinterError:
            extra = []
        q = "&".join(urllib.parse.quote(o) for o in BASE_OBJECTS + tuple(extra))
        v = (_request(key, "printer/objects/query?" + q, timeout=2.0) or {}).get("status") or {}
    except PrinterError as e:
        v = e
    with _lock:
        _cache[key] = (time.monotonic(), v)
    if isinstance(v, Exception):
        raise v
    return v


def invalidate(url):
    with _lock:
        _cache.pop(base(url), None)


def state(st):
    return (st.get("print_stats") or {}).get("state", "")


def printing(st):
    """Läuft ein Druck (auch pausiert)? st = Zustand aus query()."""
    return state(st) in ("printing", "paused")


def gcode(url, script, timeout=300.0):
    """G-Code ausführen; Moonraker antwortet erst, wenn Klipper ihn abgearbeitet hat."""
    try:
        return _request(url, "printer/gcode/script?script=" + urllib.parse.quote(script), data={},
                        timeout=timeout)
    finally:
        invalidate(url)


def info(url):
    """(Klipper-Zustand, Version), z. B. ("ready", "v0.12.0-...")."""
    r = _request(url, "printer/info", timeout=3.0) or {}
    return r.get("state", "?"), r.get("software_version", "?")
