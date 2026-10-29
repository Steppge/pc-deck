"""Holt das echte Windows-Icon einer Datei, Verknüpfung, eines Programms oder Ordners.

Es wird dasselbe Icon verwendet, das auch der Explorer anzeigt (System-Imageliste,
in der größten verfügbaren Auflösung "Jumbo" 256x256). Das Icon wird auf die
Kachelfarbe des Decks gelegt, zugeschnitten und auf 64x64 skaliert.
"""

import ctypes
import ctypes.wintypes as wt
import os
import threading

from PIL import Image, ImageChops

ICON_SZ = 64
TILE_BG = (44, 48, 58)  # muss zur Kachelfarbe in der Firmware passen

_lock = threading.Lock()
_cache = {}

# ----------------------------------------------------------------- WinAPI
if os.name == "nt":
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    comctl32 = ctypes.windll.comctl32
    ole32 = ctypes.windll.ole32

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [
            ("hIcon", ctypes.c_void_p),
            ("iIcon", ctypes.c_int),
            ("dwAttributes", wt.DWORD),
            ("szDisplayName", wt.WCHAR * 260),
            ("szTypeName", wt.WCHAR * 80),
        ]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    def _guid(s):
        import uuid
        u = uuid.UUID(s)
        g = GUID()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g

    IID_IImageList = _guid("46EB5926-582E-4017-9FDF-E8998DAA0950")
    SHGFI_SYSICONINDEX = 0x4000
    SHIL_EXTRALARGE = 2  # 48x48
    SHIL_JUMBO = 4       # 256x256
    ILD_TRANSPARENT = 1
    DI_NORMAL = 3

    shell32.SHGetFileInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(SHFILEINFOW), wt.UINT, wt.UINT]
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    shell32.SHGetImageList.argtypes = [ctypes.c_int, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetImageList.restype = ctypes.c_long
    comctl32.ImageList_GetIcon.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.UINT]
    comctl32.ImageList_GetIcon.restype = ctypes.c_void_p
    user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    user32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_int, ctypes.c_int, wt.UINT, ctypes.c_void_p, wt.UINT]
    user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT), ctypes.c_void_p]
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wt.DWORD]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateSolidBrush.argtypes = [wt.DWORD]
    gdi32.CreateSolidBrush.restype = ctypes.c_void_p

    _imagelists = {}

    def _imagelist(kind):
        if kind not in _imagelists:
            ptr = ctypes.c_void_p()
            hr = shell32.SHGetImageList(kind, ctypes.byref(IID_IImageList), ctypes.byref(ptr))
            _imagelists[kind] = ptr.value if hr == 0 else None
        return _imagelists[kind]

    def _render_hicon(hicon, size):
        """Zeichnet ein HICON auf Kachelfarbe und gibt ein PIL-RGB-Bild zurück."""
        hdc = gdi32.CreateCompatibleDC(None)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = size
        bmi.bmiHeader.biHeight = -size  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(hdc, hbmp)
        r, g, b = TILE_BG
        brush = gdi32.CreateSolidBrush(r | (g << 8) | (b << 16))
        rect = wt.RECT(0, 0, size, size)
        user32.FillRect(hdc, ctypes.byref(rect), brush)
        user32.DrawIconEx(hdc, 0, 0, hicon, size, size, 0, None, DI_NORMAL)
        gdi32.GdiFlush()
        data = ctypes.string_at(bits, size * size * 4)
        img = Image.frombuffer("RGB", (size, size), data, "raw", "BGRX", 0, 1).copy()
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(brush)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc)
        return img

    def _shell_icon(path):
        ole32.CoInitialize(None)  # harmlos, falls schon initialisiert
        info = SHFILEINFOW()
        if not shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_SYSICONINDEX):
            return None
        for kind, size in ((SHIL_JUMBO, 256), (SHIL_EXTRALARGE, 48)):
            himl = _imagelist(kind)
            if not himl:
                continue
            hicon = comctl32.ImageList_GetIcon(himl, info.iIcon, ILD_TRANSPARENT)
            if not hicon:
                continue
            try:
                img = _render_hicon(hicon, size)
            finally:
                user32.DestroyIcon(hicon)
            if _trim(img) is not None:
                return img
        return None
else:  # zum Testen unter Linux
    def _shell_icon(path):
        return None


# ----------------------------------------------------------------- Verknüpfungen
# Manche Programme (z. B. Autodesk Fusion) haben in der .exe kein Icon – das Icon
# steckt nur in der Verknüpfung. Deshalb Desktop und Startmenü nach einer
# Verknüpfung auf dieselbe .exe durchsuchen.
_LNK_SCAN = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$sh = New-Object -ComObject WScript.Shell
$dirs = 'Desktop','CommonDesktopDirectory','StartMenu','CommonStartMenu' |
        ForEach-Object { [Environment]::GetFolderPath($_) } | Where-Object { $_ }
Get-ChildItem -LiteralPath $dirs -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
  try { $t = $sh.CreateShortcut($_.FullName).TargetPath; if ($t) { $t + '|' + $_.FullName } } catch {}
}
"""
_lnk_map = None      # Ziel -> Verknüpfung
_lnk_targets = {}    # Verknüpfung -> Ziel


def _norm(p):
    return os.path.normcase(os.path.normpath(os.path.expandvars(p)))


def _scan(script, env=None):
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=30,
                         env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    for line in out.decode("utf-8", "replace").splitlines():
        target, _, lnk = line.strip().partition("|")
        if target and lnk:
            _lnk_targets[_norm(lnk)] = target
            yield target, lnk


def shortcut_for(path):
    """Verknüpfung (Desktop/Startmenü), die auf diese .exe zeigt, oder None."""
    global _lnk_map
    if os.name != "nt" or not path.lower().endswith(".exe"):
        return None
    if _lnk_map is None:
        _lnk_map = {}
        try:
            for target, lnk in _scan(_LNK_SCAN):
                _lnk_map.setdefault(_norm(target), lnk)
        except Exception:
            pass
    return _lnk_map.get(_norm(path))


def lnk_target(lnk):
    """Ziel einer Verknüpfung (.lnk) oder None."""
    if os.name != "nt":
        return None
    key = _norm(lnk)
    if key not in _lnk_targets:
        _lnk_targets[key] = None
        try:
            script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
                      "$t = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PCDECK_LNK).TargetPath;"
                      "if ($t) { $t + '|' + $env:PCDECK_LNK }")
            list(_scan(script, dict(os.environ, PCDECK_LNK=os.path.expandvars(lnk))))
        except Exception:
            pass
    return _lnk_targets.get(key)


def needs_shortcut(path):
    """Verknüpfung, falls die .exe selbst kein Icon hat, eine Verknüpfung aber schon."""
    if not path.lower().endswith(".exe") or _shell_icon(os.path.expandvars(path)) is not None:
        return None
    return shortcut_for(path)


# ----------------------------------------------------------------- Bildbearbeitung
def _trim(img):
    """Schneidet den Rand in Kachelfarbe ab (kleine Icons liegen sonst oben links im 256er-Bild)."""
    bg = Image.new("RGB", img.size, TILE_BG)
    diff = ImageChops.difference(img.convert("RGB"), bg).convert("L").point(lambda v: 255 if v > 6 else 0)
    box = diff.getbbox()
    return img.crop(box) if box else None


def _fit(img, margin=0.04):
    """Quadratisch auf Kachelfarbe setzen und auf ICON_SZ skalieren."""
    w, h = img.size
    side = int(max(w, h) * (1 + 2 * margin))
    canvas = Image.new("RGB", (side, side), TILE_BG)
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    return canvas.resize((ICON_SZ, ICON_SZ), Image.LANCZOS)


def _from_image_file(path):
    img = Image.open(path)  # bei .ico lädt Pillow automatisch die größte Auflösung
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, TILE_BG + (255,))
    bg.alpha_composite(img)
    trimmed = _trim(bg.convert("RGB"))
    return trimmed if trimmed is not None else bg.convert("RGB")


def _placeholder(text):
    from PIL import ImageDraw
    img = Image.new("RGB", (ICON_SZ, ICON_SZ), TILE_BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, ICON_SZ - 8, ICON_SZ - 8), 10, outline=(130, 135, 150), width=3)
    letter = (text or "?").strip()[:1].upper() or "?"
    d.text((ICON_SZ // 2, ICON_SZ // 2), letter, fill=(235, 235, 240), anchor="mm")
    return img


def get_icon(path, custom_icon="", label=""):
    """Liefert ein 64x64-PIL-Bild für eine Kachel."""
    key = (path, custom_icon, label)
    try:
        stamp = os.path.getmtime(custom_icon or path)
    except OSError:
        stamp = 0
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
        img = None
        try:
            if custom_icon and os.path.isfile(custom_icon):
                img = _from_image_file(custom_icon)
            elif path:
                raw = _shell_icon(os.path.expandvars(path))
                if raw is None and shortcut_for(path):
                    raw = _shell_icon(shortcut_for(path))
                img = _trim(raw) if raw is not None else None
        except Exception:
            img = None
        img = _fit(img) if img is not None else _placeholder(label or os.path.basename(path))
        _cache[key] = (stamp, img)
        return img


def pack_icon(raw):
    """RGB565-Rohdaten (8192 Byte) mit Lauflängen packen (Firmware ab 22, siehe unpackIcon).
    Steuerbyte c: c & 0x80 -> (c & 0x7F) + 1 mal das folgende Pixel, sonst c + 1 Pixel wörtlich.
    Rückgabe: gepackte Bytes oder None, wenn das nicht kleiner wird (dann roh senden)."""
    px = [raw[k] | (raw[k + 1] << 8) for k in range(0, len(raw), 2)]
    out = bytearray()
    lit = []

    def flush():
        while lit:
            chunk = lit[:128]
            del lit[:128]
            out.append(len(chunk) - 1)
            for v in chunk:
                out.extend((v & 0xFF, v >> 8))

    i, n = 0, len(px)
    while i < n:
        j = i
        while j < n and j - i < 128 and px[j] == px[i]:
            j += 1
        if j - i >= 3:  # Wiederholung lohnt sich ab 3 gleichen Pixeln
            flush()
            out.append(0x80 | (j - i - 1))
            out += bytes((px[i] & 0xFF, px[i] >> 8))
            i = j
        else:
            lit.append(px[i])
            i += 1
    flush()
    return bytes(out) if len(out) < len(raw) else None


def unpack_icon(data):
    """Gegenstück zu pack_icon (zum Prüfen)."""
    out, i = bytearray(), 0
    while i < len(data):
        c = data[i]
        i += 1
        cnt = (c & 0x7F) + 1
        if c & 0x80:
            out += data[i:i + 2] * cnt
            i += 2
        else:
            out += data[i:i + 2 * cnt]
            i += 2 * cnt
    return bytes(out)


def to_rgb565(img):
    """64x64 RGB -> Rohdaten RGB565 little endian, wie die Firmware sie erwartet."""
    px = img.convert("RGB").tobytes()
    out = bytearray(ICON_SZ * ICON_SZ * 2)
    for i in range(ICON_SZ * ICON_SZ):
        r, g, b = px[3 * i], px[3 * i + 1], px[3 * i + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[2 * i] = v & 0xFF
        out[2 * i + 1] = v >> 8
    return bytes(out)
