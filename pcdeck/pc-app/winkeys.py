"""Tastendrücke und Text an das Programm im Vordergrund schicken (Windows SendInput).

Tastenkürzel als Text, deutsch oder englisch, z. B. "Strg+Umschalt+M", "Alt+F4",
"Win+Shift+S", "F13". Mehrere nacheinander mit Komma: "Strg+K, Strg+C".
"""

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.windll.user32

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL, WHEEL_DELTA = 0x0800, 0x1000, 120
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.VkKeyScanW.argtypes = [wt.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short

MODIFIERS = {
    "strg": 0x11, "ctrl": 0x11, "control": 0x11,
    "umschalt": 0x10, "shift": 0x10, "hoch": 0x10,
    "alt": 0x12,
    "altgr": 0xA5,
    "win": 0x5B, "windows": 0x5B, "super": 0x5B,
}
KEYS = {
    "enter": 0x0D, "eingabe": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "leer": 0x20, "leertaste": 0x20, "space": 0x20, "rück": 0x08, "rueck": 0x08, "backspace": 0x08,
    "entf": 0x2E, "del": 0x2E, "delete": 0x2E, "einfg": 0x2D, "ins": 0x2D, "insert": 0x2D,
    "pos1": 0x24, "home": 0x24, "ende": 0x23, "end": 0x23,
    "bildauf": 0x21, "pgup": 0x21, "pageup": 0x21, "bildab": 0x22, "pgdn": 0x22, "pagedown": 0x22,
    "links": 0x25, "left": 0x25, "oben": 0x26, "up": 0x26, "rechts": 0x27, "right": 0x27,
    "unten": 0x28, "down": 0x28, "druck": 0x2C, "print": 0x2C, "printscreen": 0x2C,
    "pause": 0x13, "rollen": 0x91, "scrolllock": 0x91, "menü": 0x5D, "menu": 0x5D,
    "plus": 0xBB, "minus": 0xBD, "komma": 0xBC, "punkt": 0xBE,
    "play": 0xB3, "weiter": 0xB0, "zurück": 0xB1, "lauter": 0xAF, "leiser": 0xAE, "stumm": 0xAD,
}
for _n in range(1, 25):
    KEYS[f"f{_n}"] = 0x6F + _n
EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B, 0x5D, 0xA5, 0x2C,
            0xB3, 0xB0, 0xB1, 0xAF, 0xAE, 0xAD}


def _key(vk, up=False):
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return i


def _send(inputs):
    if inputs:
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def parse_combo(text):
    """'Strg+Umschalt+M' -> [vk, vk, vk]. ValueError bei unbekannter Taste."""
    vks = []
    for part in text.replace(" ", "").split("+"):
        p = part.lower()
        if not p:
            raise ValueError(f"Leere Taste in '{text}'")
        if p in MODIFIERS:
            vks.append(MODIFIERS[p])
        elif p in KEYS:
            vks.append(KEYS[p])
        elif len(part) == 1:
            r = user32.VkKeyScanW(part)  # Taste laut aktuellem Tastaturlayout
            if r == -1:
                raise ValueError(f"Unbekannte Taste '{part}'")
            vks.append(r & 0xFF)
        else:
            raise ValueError(f"Unbekannte Taste '{part}'")
    return vks


def parse_sequence(text):
    """'Strg+K, Strg+C' -> [[...], [...]]."""
    return [parse_combo(c) for c in text.split(",") if c.strip()]


def send_combo(vks):
    """Tasten der Reihe nach drücken und in umgekehrter Reihenfolge loslassen."""
    _send([_key(v) for v in vks] + [_key(v, up=True) for v in reversed(vks)])


def send_sequence(text, pause=0.06):
    for combo in parse_sequence(text):
        send_combo(combo)
        time.sleep(pause)


def type_text(text):
    """Text Zeichen für Zeichen eingeben (Unicode, auch Umlaute); Zeilenumbruch = Enter."""
    inputs = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            inputs += [_key(0x0D), _key(0x0D, up=True)]
            continue
        code = ch.encode("utf-16-le")  # Zeichen außerhalb der BMP = zwei UTF-16-Einheiten
        for k in range(0, len(code), 2):
            unit = int.from_bytes(code[k:k + 2], "little")
            for up in (False, True):
                i = INPUT(type=INPUT_KEYBOARD)
                i.u.ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
                inputs.append(i)
    _send(inputs)


def wheel(clicks, horizontal=False, ctrl=False, shift=False):
    """Mausrad drehen (+ = nach oben bzw. rechts) – wirkt auf das Fenster unter dem Mauszeiger.
    ctrl = Strg gedrückt halten (Zoomen), shift = Umschalt (in vielen Programmen seitlich scrollen)."""
    mods = ([0x11] if ctrl else []) + ([0x10] if shift else [])
    m = INPUT(type=INPUT_MOUSE)
    m.u.mi = MOUSEINPUT(0, 0, ctypes.c_uint32(int(clicks) * WHEEL_DELTA).value,
                        MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, 0, 0)
    _send([_key(v) for v in mods] + [m] + [_key(v, up=True) for v in reversed(mods)])
