"""Passwörter verschlüsselt speichern – mit der Windows-Datenschutz-API (DPAPI).

Der verschlüsselte Text ist an das Windows-Konto gebunden: Nur derselbe Benutzer auf
demselben PC kann ihn wieder entschlüsseln. In der config.json steht dann "dpapi:...".
"""

import base64
import ctypes
import ctypes.wintypes as wt

PREFIX = "dpapi:"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


crypt32 = ctypes.windll.crypt32
kernel32 = ctypes.windll.kernel32
kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(blob):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def encrypt(text):
    """Klartext -> "dpapi:<base64>" ("" bleibt "")."""
    if not text:
        return ""
    src, _keep = _blob(text.encode("utf-8"))
    out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(src), "PC-Deck", None, None, None,
                                    CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError("Verschlüsseln fehlgeschlagen")
    return PREFIX + base64.b64encode(_take(out)).decode("ascii")


def decrypt(value):
    """"dpapi:<base64>" -> Klartext. Andere Werte werden unverändert zurückgegeben."""
    if not value or not value.startswith(PREFIX):
        return value or ""
    src, _keep = _blob(base64.b64decode(value[len(PREFIX):]))
    out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError("Entschlüsseln fehlgeschlagen (anderes Windows-Konto oder anderer PC?)")
    return _take(out).decode("utf-8")
