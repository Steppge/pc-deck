"""Bosch Smart Home Controller (I und II) über die lokale Schnittstelle ("Local API").

Doku: https://github.com/BoschSmartHome/bosch-shc-api-docs

- Suchen: Port 8446 im Heimnetz, GET /smarthome/public/information (ohne Anmeldung).
- Koppeln: Die App erzeugt ein eigenes Zertifikat (Windows-Bordmittel: PowerShell
  New-SelfSignedCertificate, Schlüssel nur kurz im Zertifikatspeicher, danach gelöscht) und meldet
  es am Controller an: POST :8443/smarthome/clients mit dem Systempasswort, während der Controller im
  Kopplungsmodus ist (Taste drücken). Das Passwort wird nicht gespeichert.
- Danach alles über Port 8444 mit dem Client-Zertifikat. Gespeichert in %USERPROFILE%\\PCDeck\\bosch.json:
  Adresse, Client-ID, Zertifikat und der private Schlüssel DPAPI-verschlüsselt.

Rollladen: Dienst "ShutterControl" (level 0.0 = zu, 1.0 = offen; operationState STOPPED/MOVING/OPENING/CLOSING).
Licht: Dienst "PowerSwitch" (switchState ON/OFF).
"""

import base64
import concurrent.futures
import http.client
import ipaddress
import json
import logging
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.parse

import winsecret
from paths import APP_DIR

log = logging.getLogger("pcdeck")

FILE = os.path.join(APP_DIR, "bosch.json")
PORT_PUBLIC, PORT_PAIR, PORT_API = 8446, 8443, 8444
CREATE_NO_WINDOW = 0x08000000

_lock = threading.Lock()
_ctx = {"key": None, "ctx": None}
_cache = {"t": 0.0, "services": None, "devices": None, "dt": 0.0}


class BoschError(Exception):
    pass


# ----------------------------------------------------------------- Einstellungen
def settings():
    try:
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, FILE)


def paired():
    s = settings()
    return bool(s.get("host") and s.get("cert") and s.get("key"))


def unpair():
    """Kopplung in der App vergessen (am Controller bleibt der Eintrag, dort in der Bosch-App löschbar)."""
    with _lock:
        _ctx.update(key=None, ctx=None)
        _cache.update(t=0.0, services=None, devices=None, dt=0.0)
    try:
        os.remove(FILE)
    except OSError:
        pass


# ----------------------------------------------------------------- Verbindung
def _plain_ctx():
    # Der Controller hat ein eigenes, nicht öffentlich beglaubigtes Zertifikat
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _ctx_with(cert, key):
    """TLS mit Client-Zertifikat. ssl lädt Schlüssel nur aus Dateien: kurz in eine Temp-Datei, gleich wieder weg."""
    c = _plain_ctx()
    os.makedirs(APP_DIR, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".pem", dir=APP_DIR)
    try:
        os.write(fd, (cert + key).encode("ascii"))
        os.close(fd)
        c.load_cert_chain(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return c


def _client_ctx(s):
    with _lock:
        if _ctx["key"] != s["cert"] or not _ctx["ctx"]:
            _ctx.update(key=s["cert"], ctx=_ctx_with(s["cert"], winsecret.decrypt(s["key"])))
        return _ctx["ctx"]


def _request(host, port, ctx, method, path, body=None, headers=None, timeout=5):
    conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=timeout)
    try:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        h.update(headers or {})
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        r = conn.getresponse()
        data = r.read()
        if r.status >= 300:
            raise BoschError(f"{method} {path}: HTTP {r.status} {data[:200].decode('utf-8', 'replace')}")
        return json.loads(data) if data.strip() else None
    finally:
        conn.close()


def api(method, path, body=None, timeout=5):
    s = settings()
    if not (s.get("host") and s.get("cert")):
        raise BoschError("nicht gekoppelt")
    return _request(s["host"], PORT_API, _client_ctx(s), method, "/smarthome" + path, body, timeout=timeout)


# ----------------------------------------------------------------- Suchen
def info(host, timeout=2):
    """Öffentliche Infos des Controllers (Version, MAC …) oder None."""
    try:
        return _request(host, PORT_PUBLIC, _plain_ctx(), "GET", "/smarthome/public/information", timeout=timeout)
    except (OSError, BoschError, ValueError):
        return None


def _local_nets():
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # nur Route bestimmen, es wird nichts gesendet
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    nets = set()
    for ip in ips:
        a = ipaddress.ip_address(ip)
        if a.is_private and not a.is_loopback and not a.is_link_local:
            nets.add(ipaddress.ip_network(f"{ip}/24", strict=False))
    return nets


def discover(timeout=0.4):
    """Controller im eigenen Netz (/24) suchen: [(ip, info), ...]."""
    def probe(ip):
        try:
            with socket.create_connection((str(ip), PORT_PUBLIC), timeout=timeout):
                pass
        except OSError:
            return None
        i = info(str(ip))
        return (str(ip), i) if i else None

    hosts = [h for n in _local_nets() for h in n.hosts()]
    with concurrent.futures.ThreadPoolExecutor(64) as ex:
        return [r for r in ex.map(probe, hosts) if r]


# ----------------------------------------------------------------- Koppeln
_PS_CERT = r'''
$ErrorActionPreference = 'Stop'
$c = New-SelfSignedCertificate -Subject 'CN=%s' -KeyAlgorithm RSA -KeyLength 2048 `
  -KeyExportPolicy Exportable -CertStoreLocation 'Cert:\CurrentUser\My' -NotAfter (Get-Date).AddYears(20) `
  -Provider 'Microsoft Software Key Storage Provider'
try {
  $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($c)
  $key = $rsa.Key.Export([System.Security.Cryptography.CngKeyBlobFormat]::Pkcs8PrivateBlob)
  [Convert]::ToBase64String($c.RawData) + '|' + [Convert]::ToBase64String($key)
} finally {
  Remove-Item -LiteralPath ('Cert:\CurrentUser\My\' + $c.Thumbprint) -DeleteKey
}
'''


def _pem(kind, b64):
    body = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN {kind}-----\n{body}\n-----END {kind}-----\n"


def _make_cert(name):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_CERT % name],
                       capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    parts = r.stdout.strip().split("|")
    if r.returncode != 0 or len(parts) != 2:
        raise BoschError("Zertifikat konnte nicht erzeugt werden: " + (r.stderr.strip()[-300:] or "?"))
    return _pem("CERTIFICATE", parts[0]), _pem("PRIVATE KEY", parts[1])


def pair(host, password):
    """Am Controller anmelden (Controller muss im Kopplungsmodus sein). Passwort wird nicht gespeichert."""
    pc = "".join(ch for ch in socket.gethostname().lower() if ch.isalnum())[:20] or "pc"
    client_id = f"oss_pcdeck_{pc}"
    cert, key = _make_cert(client_id)
    body = {"@type": "client", "id": client_id, "name": f"OSS PC-Deck ({socket.gethostname()})",
            "primaryRole": "ROLE_RESTRICTED_CLIENT", "certificate": cert}
    pw = base64.b64encode(password.encode("utf-8")).decode("ascii")
    hint = "Taste am Controller drücken, bis die LEDs blinken, dann gleich „Koppeln“."
    try:
        # Der Controller verlangt das neue Zertifikat schon beim Verbindungsaufbau (nur im Kopplungsmodus angenommen)
        _request(host, PORT_PAIR, _ctx_with(cert, key), "POST", "/smarthome/clients", body,
                 headers={"Systempassword": pw}, timeout=15)
    except ssl.SSLError as e:
        if "CERTIFICATE" in str(e).upper():
            raise BoschError("Controller ist nicht im Kopplungsmodus. " + hint) from None
        raise
    except BoschError as e:
        msg = str(e)
        if "HTTP 401" in msg or "HTTP 403" in msg:
            raise BoschError("Abgelehnt – Systempasswort falsch oder Kopplungsmodus abgelaufen. " + hint) from None
        raise
    with _lock:
        _ctx.update(key=None, ctx=None)
        _cache.update(t=0.0, services=None, devices=None, dt=0.0)
    _save({"host": host, "client_id": client_id, "cert": cert, "key": winsecret.encrypt(key)})
    log.info("Bosch Smart Home: mit Controller %s gekoppelt (%s)", host, client_id)


# ----------------------------------------------------------------- Geräte und Zustände
def devices(max_age=30):
    """[{id, name, room, model, kind ("shutter"/"light"/None)}] – höchstens alle max_age Sekunden neu."""
    with _lock:
        if _cache["devices"] is not None and time.monotonic() - _cache["dt"] < max_age:
            return _cache["devices"]
    rooms = {r["id"]: r.get("name", "") for r in (api("GET", "/rooms") or [])}
    out = []
    for d in api("GET", "/devices") or []:
        sv = set(d.get("deviceServiceIds") or [])
        if "ShutterControl" in sv:
            kind = "shutter"
        elif "MultiLevelSwitch" in sv and "BinarySwitch" in sv:
            kind = "dimmer"  # z. B. Hue-Lampe über den Controller
        elif "PowerSwitch" in sv:
            kind = "light"   # Bosch-Lichtsteuerung, Zwischenstecker
        else:
            kind = None      # Raumgruppen, Hue-Geräte ohne Dimmen (z. B. Steckdose am Garagentor) usw.
        out.append({"id": d["id"], "name": d.get("name", d["id"]), "room": rooms.get(d.get("roomId"), ""),
                    "model": d.get("deviceModel", ""), "kind": kind})
    with _lock:
        _cache.update(devices=out, dt=time.monotonic())
    return out


def _services(max_age=1.0):
    """Zustände aller Dienste aller Geräte – ein Abruf für alle Kacheln, höchstens 1× je Sekunde."""
    with _lock:
        if _cache["services"] is not None and time.monotonic() - _cache["t"] < max_age:
            return _cache["services"]
    data = api("GET", "/services") or []
    st = {(s.get("deviceId"), s.get("id")): s.get("state") or {} for s in data}
    with _lock:
        _cache.update(services=st, t=time.monotonic())
    return st


def _put(dev, service, state):
    api("PUT", f"/devices/{urllib.parse.quote(dev, safe='')}/services/{service}/state", state)
    with _lock:
        _cache["t"] = 0.0  # nächste Abfrage frisch


def shutter(dev):
    """(Öffnung 0..100, fährt?) oder None."""
    s = _services().get((dev, "ShutterControl"))
    if s is None:
        return None
    moving = s.get("operationState") in ("MOVING", "OPENING", "CLOSING")
    return round(float(s.get("level", 0)) * 100), moving


def set_shutter(dev, pct):
    _put(dev, "ShutterControl", {"@type": "shutterControlState", "level": max(0, min(100, pct)) / 100})


def stop_shutter(dev):
    _put(dev, "ShutterControl", {"@type": "shutterControlState", "operationState": "STOPPED"})


def light(dev):
    """True/False (an/aus) oder None. Bosch-Lichtsteuerung: PowerSwitch, Hue: BinarySwitch."""
    svc = _services()
    s = svc.get((dev, "PowerSwitch"))
    if s is not None:
        return s.get("switchState") == "ON"
    s = svc.get((dev, "BinarySwitch"))
    return None if s is None else bool(s.get("on"))


def set_light(dev, on):
    if (dev, "PowerSwitch") in _services():
        _put(dev, "PowerSwitch", {"@type": "powerSwitchState", "switchState": "ON" if on else "OFF"})
    else:
        _put(dev, "BinarySwitch", {"@type": "binarySwitchState", "on": bool(on)})


def dimmer(dev):
    """(an?, Helligkeit 0..100) einer dimmbaren Lampe oder None."""
    svc = _services()
    sw, lv = svc.get((dev, "BinarySwitch")), svc.get((dev, "MultiLevelSwitch"))
    if sw is None or lv is None:
        return None
    return bool(sw.get("on")), int(lv.get("level", 0))


def set_dimmer(dev, level):
    """Helligkeit setzen; 0 = aus, sonst einschalten und Helligkeit setzen."""
    level = max(0, min(100, int(level)))
    if level == 0:
        set_light(dev, False)
        return
    _put(dev, "MultiLevelSwitch", {"@type": "multiLevelSwitchState", "level": level})
    _put(dev, "BinarySwitch", {"@type": "binarySwitchState", "on": True})
