"""Windows-Audio über die eingebauten Core-Audio-Schnittstellen (ctypes, ohne Zusatzpakete).

- Geräte auflisten (Wiedergabe/Aufnahme), Name und Windows-Symbol
- Standardgerät abfragen und setzen (alle drei Rollen: Konsole, Multimedia, Kommunikation)
  über IPolicyConfig – in Windows enthalten, aber nicht offiziell dokumentiert
- Lautstärke und Stummschaltung (IAudioEndpointVolume)

COM-Methoden werden über ihre Position in der Methodentabelle (vtable) aufgerufen.
"""

import ctypes
import ctypes.wintypes as wt
import uuid

ole32 = ctypes.windll.ole32
user32 = ctypes.windll.user32

RENDER, CAPTURE = 0, 1                      # EDataFlow
E_CONSOLE, E_MULTIMEDIA, E_COMMUNICATIONS = 0, 1, 2
DEVICE_STATE_ACTIVE = 1
CLSCTX_ALL = 0x17


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s):
        u = uuid.UUID(s)
        super().__init__(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.USHORT), ("r2", wt.USHORT), ("r3", wt.USHORT),
                ("val", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


CLSID_MMDeviceEnumerator = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioEndpointVolume = GUID("5CDF2C82-841E-4546-9722-0CF74078229A")
CLSID_PolicyConfigClient = GUID("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")
IID_IPolicyConfig = GUID("F8679F50-850A-41CF-9C72-430F290290C8")
PKEY_Device_FriendlyName = PROPERTYKEY(GUID("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)
PKEY_Device_DeviceDesc = PROPERTYKEY(GUID("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 2)
PKEY_DeviceClass_IconPath = PROPERTYKEY(GUID("259ABFFC-50A7-47CE-AF08-68C9A7D73366"), 12)
VT_LPWSTR = 31

ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]


def _method(obj, index, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
    return lambda *a: fn(obj, *a)


def _check(hr, what):
    if hr < 0:
        raise OSError(f"{what} fehlgeschlagen (0x{hr & 0xFFFFFFFF:08X})")


class _Com:
    """Sammelt COM-Objekte und gibt sie am Ende frei."""

    def __enter__(self):
        ole32.CoInitialize(None)  # harmlos, falls schon initialisiert
        self.objs = []
        return self

    def __exit__(self, *exc):
        for o in reversed(self.objs):
            if o:
                vtbl = ctypes.cast(o, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(o)

    def new(self):
        p = ctypes.c_void_p()
        self.objs.append(p)
        return p

    def create(self, clsid, iid):
        p = self.new()
        _check(ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(p)),
               "CoCreateInstance")
        return p

    def enumerator(self):
        return self.create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)


def _device_id(dev):
    s = ctypes.c_void_p()
    _check(_method(dev, 5, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(s)), "GetId")
    try:
        return ctypes.wstring_at(s.value)
    finally:
        ole32.CoTaskMemFree(s)


def _prop(com, dev, key):
    store = com.new()
    _check(_method(dev, 4, wt.DWORD, ctypes.POINTER(ctypes.c_void_p))(0, ctypes.byref(store)), "OpenPropertyStore")
    pv = PROPVARIANT()
    _check(_method(store, 5, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
        ctypes.byref(key), ctypes.byref(pv)), "GetValue")
    try:
        return ctypes.wstring_at(pv.val) if pv.vt == VT_LPWSTR and pv.val else ""
    finally:
        ole32.PropVariantClear(ctypes.byref(pv))


def devices(flow):
    """Aktive Geräte: Liste von dict(id, name, desc, icon) – name z. B. "Lautsprecher (Realtek Audio)"."""
    out = []
    with _Com() as com:
        col = com.new()
        _check(_method(com.enumerator(), 3, ctypes.c_int, wt.DWORD, ctypes.POINTER(ctypes.c_void_p))(
            flow, DEVICE_STATE_ACTIVE, ctypes.byref(col)), "EnumAudioEndpoints")
        n = wt.UINT()
        _check(_method(col, 3, ctypes.POINTER(wt.UINT))(ctypes.byref(n)), "GetCount")
        for i in range(n.value):
            dev = com.new()
            _check(_method(col, 4, wt.UINT, ctypes.POINTER(ctypes.c_void_p))(i, ctypes.byref(dev)), "Item")
            out.append({"id": _device_id(dev), "name": _prop(com, dev, PKEY_Device_FriendlyName),
                        "desc": _prop(com, dev, PKEY_Device_DeviceDesc),
                        "icon": _prop(com, dev, PKEY_DeviceClass_IconPath)})
    return out


def default_id(flow, role=E_CONSOLE):
    """ID des Standardgeräts oder None."""
    try:
        with _Com() as com:
            dev = com.new()
            _check(_method(com.enumerator(), 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                flow, role, ctypes.byref(dev)), "GetDefaultAudioEndpoint")
            return _device_id(dev)
    except OSError:
        return None


def set_default(device_id):
    """Gerät für alle drei Rollen als Standard setzen (damit auch Discord/Teams folgen)."""
    with _Com() as com:
        pc = com.create(CLSID_PolicyConfigClient, IID_IPolicyConfig)
        for role in (E_CONSOLE, E_MULTIMEDIA, E_COMMUNICATIONS):
            _check(_method(pc, 13, wt.LPCWSTR, ctypes.c_int)(device_id, role), "SetDefaultEndpoint")


# ----------------------------------------------------------------- Lautstärke / Stumm
def _volume_of(com, dev):
    vol = com.new()
    _check(_method(dev, 3, ctypes.POINTER(GUID), wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None, ctypes.byref(vol)), "Activate")
    return vol


def _default_volume(com, flow):
    dev = com.new()
    _check(_method(com.enumerator(), 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
        flow, E_MULTIMEDIA if flow == RENDER else E_CONSOLE, ctypes.byref(dev)), "GetDefaultAudioEndpoint")
    return _volume_of(com, dev)


def _get_level(vol):
    v = ctypes.c_float()
    _check(_method(vol, 9, ctypes.POINTER(ctypes.c_float))(ctypes.byref(v)), "GetMasterVolumeLevelScalar")
    return v.value


def _get_mute(vol):
    m = wt.BOOL()
    _check(_method(vol, 15, ctypes.POINTER(wt.BOOL))(ctypes.byref(m)), "GetMute")
    return bool(m.value)


def _set_mute(vol, on):
    _check(_method(vol, 14, wt.BOOL, ctypes.c_void_p)(bool(on), None), "SetMute")


def volume():
    """(Lautstärke 0..100, stumm) des Standard-Wiedergabegeräts; None, wenn keins da ist."""
    try:
        with _Com() as com:
            vol = _default_volume(com, RENDER)
            return round(_get_level(vol) * 100), _get_mute(vol)
    except OSError:
        return None


def toggle_mute():
    with _Com() as com:
        vol = _default_volume(com, RENDER)
        _set_mute(vol, not _get_mute(vol))


def change_volume(delta_percent):
    with _Com() as com:
        vol = _default_volume(com, RENDER)
        new = max(0.0, min(1.0, _get_level(vol) + delta_percent / 100))
        _check(_method(vol, 7, ctypes.c_float, ctypes.c_void_p)(new, None), "SetMasterVolumeLevelScalar")
        if delta_percent > 0 and _get_mute(vol):
            _set_mute(vol, False)  # lauter machen hebt die Stummschaltung auf (wie bei Windows)


def _all_capture_volumes(com):
    col = com.new()
    _check(_method(com.enumerator(), 3, ctypes.c_int, wt.DWORD, ctypes.POINTER(ctypes.c_void_p))(
        CAPTURE, DEVICE_STATE_ACTIVE, ctypes.byref(col)), "EnumAudioEndpoints")
    n = wt.UINT()
    _check(_method(col, 3, ctypes.POINTER(wt.UINT))(ctypes.byref(n)), "GetCount")
    vols = []
    for i in range(n.value):
        dev = com.new()
        _check(_method(col, 4, wt.UINT, ctypes.POINTER(ctypes.c_void_p))(i, ctypes.byref(dev)), "Item")
        vols.append(_volume_of(com, dev))
    return vols


def mic_muted(all_mics=False):
    """True/False; bei all_mics nur True, wenn alle Mikrofone stumm sind. None = kein Mikrofon."""
    try:
        with _Com() as com:
            vols = _all_capture_volumes(com) if all_mics else [_default_volume(com, CAPTURE)]
            return all(_get_mute(v) for v in vols) if vols else None
    except OSError:
        return None


def toggle_mic(all_mics=False):
    with _Com() as com:
        vols = _all_capture_volumes(com) if all_mics else [_default_volume(com, CAPTURE)]
        if not vols:
            return
        new = not all(_get_mute(v) for v in vols)
        for v in vols:
            _set_mute(v, new)


# ----------------------------------------------------------------- Gerätesymbol
user32.PrivateExtractIconsW.argtypes = [wt.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.UINT),
                                        wt.UINT, wt.UINT]
user32.PrivateExtractIconsW.restype = wt.UINT


def device_icon(icon_path, size=256):
    """Windows-Symbol eines Audiogeräts als PIL-Bild auf Kachelfarbe (oder None).

    icon_path z. B. "%windir%\\system32\\mmres.dll,-3011" (negativ = Ressourcen-ID).
    """
    import os
    import iconextract
    if not icon_path:
        return None
    file, _, idx = icon_path.rpartition(",")
    if not file:
        file, idx = icon_path, "0"
    try:
        idx = int(idx)
    except ValueError:
        idx = 0
    hicon = ctypes.c_void_p()
    iid = wt.UINT()
    n = user32.PrivateExtractIconsW(os.path.expandvars(file), idx, size, size, ctypes.byref(hicon),
                                    ctypes.byref(iid), 1, 0)
    if n != 1 or not hicon.value:
        return None
    try:
        return iconextract._render_hicon(hicon.value, size)
    finally:
        user32.DestroyIcon(hicon)


# ----------------------------------------------------------------- Lautstärke einzelner Programme
# Lautstärkemischer: jede Audio-Sitzung gehört zu einem Prozess. Ein Programm kann mehrere
# Sitzungen haben (z. B. Discord mit mehreren Prozessen) – alle werden gemeinsam verstellt.
IID_IAudioSessionManager2 = GUID("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = GUID("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
IID_ISimpleAudioVolume = GUID("87CE5498-68D6-44E5-9215-6DA47EF883D8")


def _query(com, obj, iid):
    p = com.new()
    _check(_method(obj, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(iid), ctypes.byref(p)), "QueryInterface")
    return p


def _app_volumes(com, exe):
    """ISimpleAudioVolume aller Sitzungen des Programms exe (z. B. "discord.exe") auf allen Wiedergabegeräten."""
    import ntpath
    import winctl
    exe = exe.lower()
    out, cache = [], {}
    col = com.new()
    _check(_method(com.enumerator(), 3, ctypes.c_int, wt.DWORD, ctypes.POINTER(ctypes.c_void_p))(
        RENDER, DEVICE_STATE_ACTIVE, ctypes.byref(col)), "EnumAudioEndpoints")
    n = wt.UINT()
    _check(_method(col, 3, ctypes.POINTER(wt.UINT))(ctypes.byref(n)), "GetCount")
    for i in range(n.value):
        try:
            dev = com.new()
            _check(_method(col, 4, wt.UINT, ctypes.POINTER(ctypes.c_void_p))(i, ctypes.byref(dev)), "Item")
            mgr = com.new()
            _check(_method(dev, 3, ctypes.POINTER(GUID), wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, ctypes.byref(mgr)), "Activate")
            sessions = com.new()
            _check(_method(mgr, 5, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(sessions)), "GetSessionEnumerator")
            count = ctypes.c_int()
            _check(_method(sessions, 3, ctypes.POINTER(ctypes.c_int))(ctypes.byref(count)), "GetCount")
            for k in range(count.value):
                ctl = com.new()
                _check(_method(sessions, 4, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(k, ctypes.byref(ctl)),
                       "GetSession")
                pid = wt.DWORD()
                _method(_query(com, ctl, IID_IAudioSessionControl2), 14, ctypes.POINTER(wt.DWORD))(ctypes.byref(pid))
                if pid.value and ntpath.basename(winctl._exe_of(pid.value, cache)).lower() == exe:
                    out.append(_query(com, ctl, IID_ISimpleAudioVolume))
        except OSError:
            continue  # Gerät ohne Sitzungen bzw. gerade verschwunden
    return out


def _session_level(vol):
    v = ctypes.c_float()
    _check(_method(vol, 4, ctypes.POINTER(ctypes.c_float))(ctypes.byref(v)), "GetMasterVolume")
    return v.value


def app_volume(exe):
    """Lautstärke 0..100 des Programms im Lautstärkemischer; None, wenn es gerade keinen Ton hat."""
    try:
        with _Com() as com:
            vols = _app_volumes(com, exe)
            return round(_session_level(vols[0]) * 100) if vols else None
    except OSError:
        return None


def change_app_volume(exe, delta_percent):
    """Programm lauter/leiser (alle seine Sitzungen); gibt die neue Lautstärke zurück oder None."""
    with _Com() as com:
        vols = _app_volumes(com, exe)
        if not vols:
            return None
        new = max(0.0, min(1.0, _session_level(vols[0]) + delta_percent / 100))
        for vol in vols:
            _check(_method(vol, 3, ctypes.c_float, ctypes.c_void_p)(new, None), "SetMasterVolume")
            if delta_percent > 0:
                _method(vol, 5, wt.BOOL, ctypes.c_void_p)(False, None)  # lauter hebt Stumm auf
        return round(new * 100)


def next_output(device_ids=None, step=1):
    """Standard-Wiedergabegerät reihum wechseln (nur über device_ids, wenn angegeben); gibt das neue Gerät zurück."""
    devs = devices(RENDER)
    if device_ids:
        devs = [d for d in devs if d["id"] in device_ids] or devs
    if not devs:
        return None
    cur = default_id(RENDER)
    ids = [d["id"] for d in devs]
    target = devs[(ids.index(cur) + step) % len(devs)] if cur in ids else devs[0]
    set_default(target["id"])
    return target
