"""Soundboard: Audiodateien auf dem PC abspielen (sounddevice/PortAudio über WASAPI, soundfile).

- Dateien werden einmal dekodiert und im Speicher gehalten (Cache nach Pfad und Änderungszeit),
  damit ein Sound sofort startet. preload() lädt im Hintergrund vor.
- Jede Wiedergabe ist ein eigener Stream je Ausgabegerät; PortAudio ruft die Daten in seinem eigenen
  Thread ab – die Deck-Verbindung wartet nie. Mehrere Sounds laufen gleichzeitig (überlagern).
- Ausgabegerät "" = Windows-Standardgerät zum Zeitpunkt des Abspielens (folgt also der Umschaltung).
- WASAPI (geteilter Modus) mit automatischer Umrechnung: Abtastrate der Datei ist egal.
"""

import logging
import os
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

import winaudio

log = logging.getLogger("pcdeck")

RENDER = 0
VCABLE_HINTS = ("cable input", "voicemeeter")  # Namen virtueller Kabel (VB-Audio)
VCABLE_URL = "https://vb-audio.com/Cable/"

_lock = threading.RLock()
_cache = {}    # pfad -> (mtime, daten float32 (frames, kanäle), abtastrate)
_playing = {}  # schlüssel (Kachel) -> [_Play, ...]


# ----------------------------------------------------------------- Geräte
def _wasapi():
    for i, h in enumerate(sd.query_hostapis()):
        if "WASAPI" in h["name"]:
            return i
    return None


def _outputs():
    """{name: index} der WASAPI-Ausgabegeräte (Namen wie in Windows)."""
    api = _wasapi()
    return {d["name"]: i for i, d in enumerate(sd.query_devices())
            if d["hostapi"] == api and d["max_output_channels"] > 0}


def _refresh():
    """PortAudio liest die Geräteliste nur beim Start – neu einlesen (nur, wenn gerade nichts spielt)."""
    with _lock:
        if any(_playing.values()):
            return False
        sd._terminate()
        sd._initialize()
        return True


def output_names():
    """Namen der Ausgabegeräte für die Auswahl im Kachel-Dialog."""
    try:
        _refresh()
        return sorted(_outputs())
    except Exception:
        log.exception("Soundboard: Ausgabegeräte nicht lesbar")
        return []


def virtual_cable():
    """Name des ersten virtuellen Kabels (VB-Audio CABLE Input, VoiceMeeter) oder None."""
    return next((n for n in output_names() if any(h in n.lower() for h in VCABLE_HINTS)), None)


def _default_name():
    """Name des aktuellen Windows-Standard-Ausgabegeräts."""
    did = winaudio.default_id(RENDER)
    return next((d["name"] for d in winaudio.devices(RENDER) if d["id"] == did), None)


def _device_index(name):
    """Index des Geräts (WASAPI) oder None; bei Bedarf Geräteliste neu einlesen."""
    idx = _outputs().get(name)
    if idx is None and _refresh():
        idx = _outputs().get(name)
    return idx


# ----------------------------------------------------------------- Dateien
def load(path):
    """Datei dekodieren (mit Cache): (daten float32 (frames, kanäle), abtastrate)."""
    mtime = os.path.getmtime(path)
    with _lock:
        c = _cache.get(path)
        if c and c[0] == mtime:
            return c[1], c[2]
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    with _lock:
        _cache[path] = (mtime, data, rate)
    return data, rate


def preload(path):
    """Im Hintergrund vorladen (Fehler werden still ignoriert – beim Abspielen gibt es eine Meldung)."""
    if not path:
        return

    def run():
        try:
            load(path)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


# ----------------------------------------------------------------- Wiedergabe
class _Play:
    def __init__(self, key, data, rate, volume, device):
        self.key = key
        self.data = data
        self.pos = 0
        self.volume = volume
        info = sd.query_devices(device)
        self.channels = 2 if info["max_output_channels"] >= 2 else 1
        self.stream = sd.OutputStream(device=device, samplerate=rate, channels=self.channels, dtype="float32",
                                      callback=self._callback, finished_callback=self._finished,
                                      extra_settings=sd.WasapiSettings(auto_convert=True))

    def _callback(self, outdata, frames, time_info, status):
        chunk = self.data[self.pos:self.pos + frames]
        n = len(chunk)
        self.pos += n
        if chunk.shape[1] != self.channels:  # Mono -> Stereo bzw. mehr Kanäle -> die ersten
            chunk = np.repeat(chunk[:, :1], self.channels, axis=1) if chunk.shape[1] == 1 \
                else chunk[:, :self.channels]
        outdata[:n] = chunk * self.volume
        if n < frames:
            outdata[n:] = 0
            raise sd.CallbackStop

    def _finished(self):
        with _lock:
            lst = _playing.get(self.key, [])
            if self in lst:
                lst.remove(self)
            if not lst:
                _playing.pop(self.key, None)

    @property
    def progress(self):
        return min(1.0, self.pos / max(1, len(self.data)))


def play(key, path, volume=1.0, devices=("",), stop_others=False):
    """Sound abspielen (je Gerät ein Stream). Rückgabe: Liste der Geräte, die fehlen. Fehler -> Ausnahme."""
    data, rate = load(path)
    if stop_others:
        stop_all()
    # erst alle Geräte auflösen (kann die Geräteliste neu einlesen), dann erst Streams öffnen
    missing, found = [], []
    for name in devices:
        dev_name = name or _default_name()
        idx = _device_index(dev_name) if dev_name else None
        if idx is None:
            missing.append(name or "Standardgerät")
        else:
            found.append(idx)
    plays = [_Play(key, data, rate, max(0.0, min(1.0, volume)), idx) for idx in found]
    with _lock:
        _playing.setdefault(key, []).extend(plays)
    for p in plays:
        p.stream.start()
    return missing


PREVIEW = "__vorhoeren__"


def preview(data, volume=0.8):
    """Probehören aus dem Speicher (Bytes einer Audiodatei) auf dem Standardgerät; stoppt das vorherige."""
    import io
    stop(PREVIEW)
    samples, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    dev = _default_name()
    idx = _device_index(dev) if dev else None
    if idx is None:
        raise OSError("kein Ausgabegerät")
    p = _Play(PREVIEW, samples, rate, volume, idx)
    with _lock:
        _playing.setdefault(PREVIEW, []).append(p)
    p.stream.start()


def stop(key):
    with _lock:
        plays = _playing.pop(key, [])
    for p in plays:
        try:
            p.stream.abort()
            p.stream.close()
        except Exception:
            pass


def stop_all():
    with _lock:
        keys = list(_playing)
    for k in keys:
        stop(k)


def progress(key):
    """Fortschritt 0..1 des Sounds dieser Kachel oder None (spielt nicht)."""
    with _lock:
        plays = [p for p in _playing.get(key, []) if p.stream.active]
    return max(p.progress for p in plays) if plays else None
