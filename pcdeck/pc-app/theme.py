"""Design eines Decks: Startbild, schemenhaftes Seitenbild, Kachel- und Randfarben.

Aus einem Bild (z. B. einem der Windows-Hintergründe) entstehen:
  - Startbild 480x320 mit "PC-Deck", Widmung und leuchtendem Cyan-Namen (Logo-Text des Decks)
  - Seitenbild 240x160 (abgedunkelt, weichgezeichnet; das Deck zeigt es doppelt groß)
  - Kachel-Verlauf und Randverlauf, automatisch aus dem Farbton des Bildes oder von Hand
Das Deck (Firmware ab 8) speichert alles im Flash; übertragen wird nur bei Änderung (Prüfsumme).

Konfiguration pro Deck:
  cfg["design"] = "standard" | "red" | "green" | "mono" (feste Designs) | "custom" (eigenes) |
                  "keep" (Design auf dem Deck lassen – z. B. Deck von einem anderen PC) | "off"
  cfg["theme"]  = das eigene Design: {
    "enabled": bool, "image": pfad oder "gen:<name>", "tile": "auto" oder "#rrggbb", "tile_sat": 0..100,
    "style": "flat" | "vertical" | "diagonal" | "glass", "bg_brightness": 0..100, "tile_alpha": 0..100 }
Fehlt "design", gilt: kein "theme" = "keep", ausgeschaltet = "off", sonst "standard" bzw. "custom".
"""

import colorsys
import hashlib
import os
import threading

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

W, H = 480, 320
PAGE_W, PAGE_H = 240, 160
CYAN = (0, 230, 255)
# Logo-Text für Decks mit älterer Firmware ohne "LOGO"-Befehl; neuere melden ihren selbst.
# Eigener Text: Datei logo_local.py (nicht im Repository) mit LOGO = ("Widmung", "Name", True/False für TM).
try:
    from logo_local import LOGO
except ImportError:
    LOGO = ("exclusive for you", "Maker", False)
STYLES = [("flat", "Flach"), ("vertical", "Verlauf senkrecht"), ("diagonal", "Verlauf diagonal"),
          ("glass", "Glas (Verlauf + Glanz)")]
STYLE_CODE = {"flat": 0, "vertical": 1, "diagonal": 2, "glass": 3}
WALLPAPER_DIR = r"C:\Windows\Web\Wallpaper"
DEFAULT = {"enabled": True, "image": os.path.join(WALLPAPER_DIR, "ThemeA", "img21.jpg"), "tile": "auto",
           "tile_sat": 75, "style": "glass", "bg_brightness": 30, "tile_alpha": 80}


# Hintergründe, die die App selbst erzeugt (gleich auf jedem PC): Grundfarbe, Ringfarbe links, rechts
GENERATED = {"blue": ((4, 4, 14), (40, 110, 255), (170, 40, 230)),
             "red": ((10, 2, 8), (235, 30, 50), (230, 0, 170)),
             "green": ((2, 10, 6), (40, 230, 90), (0, 190, 170)),
             "mono": ((5, 5, 5), (150, 150, 150), (255, 255, 255))}
_GEN = {"enabled": True, "tile": "auto", "style": "glass", "bg_brightness": 70, "tile_alpha": 80}
# feste Designs (unveränderbar): (id, Name, Einstellungen)
DESIGNS = [("standard", "Standard (Blau)", dict(DEFAULT)),
           ("red", "Rot / Magenta", dict(_GEN, image="gen:red", tile_sat=80)),
           ("green", "Grün", dict(_GEN, image="gen:green", tile_sat=70)),
           ("mono", "Schwarz / Weiß", dict(_GEN, image="gen:mono", tile_sat=0, mono=True))]
CHOICES = [(i, n) for i, n, _ in DESIGNS] + [("custom", "Eigenes Design"), ("keep", "Auf dem Deck lassen"),
                                              ("off", "Kein Design")]


def design_id(cfg):
    """Gewähltes Design eines Decks (ältere Konfigurationen ohne "design" werden eingeordnet)."""
    d = (cfg or {}).get("design")
    if d in dict(CHOICES):
        return d
    t = (cfg or {}).get("theme")
    if not t:
        return "keep"  # unbekanntes/neues Deck: Design auf dem Deck nicht anrühren
    if not t.get("enabled"):
        return "off"
    same = all(t.get(k, v) == v for k, v in DEFAULT.items() if k != "enabled")
    return "standard" if same else "custom"


def custom(cfg):
    """Das eigene Design eines Decks (zum Bearbeiten), mit Standardwerten ergänzt."""
    t = dict(DEFAULT)
    t.update((cfg or {}).get("theme") or {})
    t["enabled"] = True
    return t


def settings(cfg):
    """Design, das ans Deck geht. None = Design auf dem Deck lassen; {"enabled": False} = keins."""
    d = design_id(cfg)
    if d == "keep":
        return None
    if d == "off":
        return {"enabled": False}
    return custom(cfg) if d == "custom" else dict(next(t for i, _, t in DESIGNS if i == d))


def is_mono(cfg):
    """Schwarz/Weiß-Design aktiv: Icons gehen in Graustufen ans Deck."""
    t = settings(cfg)
    return bool(t and t.get("enabled") and t.get("mono"))


def mono_icon(img):
    """Icon in Graustufen; die Schlüsselfarbe (44,48,58) bleibt exakt erhalten (durchsichtig am Deck)."""
    rgb = img.convert("RGB")
    gray = ImageOps.grayscale(rgb).convert("RGB")
    key = Image.merge("RGB", [c.point(lambda v, k=k: 255 if v == k else 0)
                              for c, k in zip(rgb.split(), (44, 48, 58))]).convert("L").point(
        lambda v: 255 if v == 255 else 0)
    return Image.composite(rgb, gray, key)


def generated_image(name):
    """Dunkler Hintergrund mit leuchtendem Ring im Farbverlauf und weichem Farbschleier (1920x1080)."""
    base, c1, c2 = GENERATED.get(name, GENERATED["blue"])
    w, h = 480, 270  # klein rechnen, dann weich vergrößern
    img = Image.new("RGB", (w, h), base)
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    for x in range(w):
        f = x / (w - 1)
        gd.line((x, 0, x, h), fill=tuple(int(a + (b - a) * f) for a, b in zip(c1, c2)))
    haze = Image.new("L", (w, h), 0)
    ImageDraw.Draw(haze).ellipse((-80, 150, w + 80, h + 160), fill=150)
    img = Image.composite(grad, img, haze.filter(ImageFilter.GaussianBlur(45)).point(lambda v: int(v * 0.55)))
    cx, cy, r = w * 0.52, h * 0.62, h * 0.55
    fade = Image.linear_gradient("L").resize((w, h)).point(lambda v: int(60 + v * 0.75))  # unten heller
    for width, blur, strength in ((26, 22, 0.55), (8, 6, 0.9), (2, 1, 1.0)):
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=width)
        m = Image.composite(m.filter(ImageFilter.GaussianBlur(blur)), Image.new("L", (w, h), 0), fade)
        img = Image.composite(grad, img, m.point(lambda v, s=strength: int(v * s)))
    inner = Image.new("L", (w, h), 0)
    ImageDraw.Draw(inner).ellipse((cx - r + 10, cy - r + 10, cx + r - 10, cy + r - 10), fill=110)
    img = Image.composite(Image.new("RGB", (w, h), base), img, inner.filter(ImageFilter.GaussianBlur(20)))
    return img.resize((1920, 1080), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2))


def _source_key(path):
    """(Quelle, Stand) des Hintergrunds oder None. Fehlt das Windows-Bild des Standard-Designs
    (anderer PC), nimmt die App den selbst erzeugten blauen Hintergrund."""
    if path.startswith("gen:"):
        return path, 0
    if os.path.isfile(path):
        return path, os.path.getmtime(path)
    if path == DEFAULT["image"]:
        return "gen:blue", 0
    return None


def _load(src):
    if src.startswith("gen:"):
        return generated_image(src[4:])
    img = Image.open(src)
    img.load()
    return img


def _font(name, size):
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default(size)


def _tint(h, l, s):
    return tuple(int(c * 255) for c in colorsys.hls_to_rgb((h % 360) / 360, max(0, min(1, l)), max(0, min(1, s))))


def accent_hue(img):
    """Häufigster kräftige Farbton im Bild (Grad)."""
    small = img.convert("RGB").resize((160, 100))
    counts = {}
    for r, g, b in small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata():
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s > 0.5 and l > 0.3:
            k = round(h * 36)
            counts[k] = counts.get(k, 0) + 1
    return (max(counts, key=counts.get) * 10) if counts else 220


def colors(t, img):
    """(kachel_oben, kachel_unten, rand_oben, rand_unten) als RGB."""
    if t.get("mono"):
        return (88, 88, 92), (44, 44, 48), (150, 150, 150), (255, 255, 255)
    hue = accent_hue(img)
    sat = t.get("tile_sat", 75) / 100
    if t.get("tile", "auto").startswith("#"):
        r, g, b = (int(t["tile"][k:k + 2], 16) / 255 for k in (1, 3, 5))
        th, tl, ts = colorsys.rgb_to_hls(r, g, b)
        th *= 360
        top, bottom = _tint(th, tl + 0.10, ts), _tint(th, tl, ts)
    else:
        top, bottom = _tint(hue - 8, 0.27, sat), _tint(hue - 5, 0.17, sat)
    return top, bottom, _tint(hue - 10, 0.55, 1.0), _tint(hue + 35, 0.55, 1.0)


def boot_image(img, mono=False, logo=LOGO):
    """Startbild 480x320: Bild abgedunkelt, Schriftzug, Widmung, leuchtender Name (Logo-Text), unten
    dunkler für ID/Hinweis. logo = (Widmung, Name, TM) – fest im Deck, die App liest ihn nur.
    mono: alles in Graustufen (der Name leuchtet dann weiß)."""
    dedication, name, with_tm = logo
    out = ImageEnhance.Brightness(ImageOps.fit(img.convert("RGB"), (W, H), Image.LANCZOS)).enhance(0.55)
    shade = Image.new("L", (W, H))
    sd = ImageDraw.Draw(shade)
    for y in range(240, H):  # unten abdunkeln, damit ID und Hinweis lesbar sind
        sd.line((0, y, W, y), fill=int(150 * (y - 240) / (H - 240)))
    out = Image.composite(Image.new("RGB", (W, H)), out, shade)
    d = ImageDraw.Draw(out)
    d.text((W // 2, 90), "PC-Deck", font=_font("arialbd.ttf", 70), fill=(240, 240, 245), anchor="mm")
    d.text((W // 2, 152), dedication, font=_font("arial.ttf", 20), fill=(215, 215, 225), anchor="mm")
    big, tm = _font("arialbd.ttf", 46), _font("arialbd.ttf", 20)
    wn = d.textlength(name, font=big)
    wt = 3 + d.textlength("TM", font=tm) if with_tm else 0
    x0 = (W - (wn + wt)) // 2
    glow = Image.new("L", (W, H))
    gd = ImageDraw.Draw(glow)
    gd.text((x0, 212), name, font=big, fill=255, anchor="ls")
    if with_tm:
        gd.text((x0 + wn + 3, 194), "TM", font=tm, fill=255, anchor="ls")
    out = Image.composite(Image.new("RGB", (W, H), CYAN), out,
                          glow.filter(ImageFilter.GaussianBlur(5)).point(lambda v: int(v * 0.85)))
    d = ImageDraw.Draw(out)
    d.text((x0, 212), name, font=big, fill=(200, 250, 255), anchor="ls")
    if with_tm:
        d.text((x0 + wn + 3, 194), "TM", font=tm, fill=(200, 250, 255), anchor="ls")
    return ImageOps.grayscale(out).convert("RGB") if mono else out


def page_image(img, brightness):
    """Seitenbild 240x160: weichgezeichnet und abgedunkelt (schemenhaft)."""
    small = ImageOps.fit(img.convert("RGB"), (PAGE_W, PAGE_H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.5))
    return ImageEnhance.Brightness(small).enhance(brightness / 100)


def rgb565(img):
    """PIL-RGB-Bild -> RGB565 little endian (wie die Icons)."""
    px = img.convert("RGB").tobytes()
    out = bytearray(len(px) // 3 * 2)
    j = 0
    for i in range(0, len(px), 3):
        v = ((px[i] >> 3) << 11) | ((px[i + 1] >> 2) << 5) | (px[i + 2] >> 3)
        out[j] = v & 0xFF
        out[j + 1] = v >> 8
        j += 2
    return bytes(out)


def c565(rgb):
    r, g, b = rgb
    return f"{((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3):04x}"


_cache = {}
_lock = threading.Lock()


def build(t, logo=None):
    """Fertiges Design: {"hash", "boot", "page" (RGB565-Bytes), "theme_line", "colors", Vorschaubilder}.
    logo = Logo-Text des Decks (Widmung, Name, TM), sonst der von Deck 1. None, wenn das Design aus ist
    oder das Bild fehlt."""
    if not t or not t.get("enabled"):
        return None
    skey = _source_key(t.get("image", ""))
    if skey is None:
        return None
    logo = tuple(logo or LOGO)
    key = skey + (t.get("tile"), t.get("tile_sat"), t.get("style"), t.get("bg_brightness"), t.get("tile_alpha"),
                  bool(t.get("mono")))
    if logo != LOGO:
        key += logo  # Deck 1 behält seine bisherige Prüfsumme
    with _lock:
        if key in _cache:
            return _cache[key]
    img = _load(skey[0])
    top, bottom, btop, bbottom = colors(t, img)
    boot, page = boot_image(img, bool(t.get("mono")), logo), page_image(img, t.get("bg_brightness", 30))
    style = STYLE_CODE.get(t.get("style", "glass"), 3)
    alpha = int(t.get("tile_alpha", 80) * 255 / 100)
    digest = hashlib.md5(repr(key).encode() + b"v1").hexdigest()[:16]
    res = {"hash": digest, "boot": rgb565(boot), "page": rgb565(page), "boot_img": boot, "page_img": page,
           "colors": (top, bottom, btop, bbottom), "style": style, "alpha": alpha,
           "theme_line": f"{digest} {c565(top)} {c565(bottom)} {c565(btop)} {c565(bbottom)} {style} {alpha}"}
    with _lock:
        _cache[key] = res
    return res


# ----------------------------------------------------------------- Vorschau fürs Konfigurationsfenster
def preview(t, icons=()):
    """Vorschau der Kachelseite (480x320) wie am Deck: Seitenbild, Glas-Kacheln, Rand."""
    res = build(t)
    if not res:
        return None
    top, bottom, btop, bbottom = res["colors"]
    out = res["page_img"].resize((W, H), Image.BILINEAR)
    style, alpha = res["style"], res["alpha"]
    for k in range(8):
        c, r = k % 4, k // 4
        x, y, w, h = c * 120 + 3, r * 120 + 30, 114, 100
        tile = Image.new("RGB", (w, h))
        td = ImageDraw.Draw(tile)
        for yy in range(h):
            tv = yy / (h - 1)
            for xx in (range(w) if style == 2 else [None]):
                f = tv if style != 2 else (xx / (w - 1) + tv) / 2
                col = top if style == 0 else tuple(int(a + (b - a) * f) for a, b in zip(top, bottom))
                if style == 3 and yy < h // 2:
                    g = (90 * (h // 2 - yy) / (h // 2) + 20) / 255
                    col = tuple(int(v + (255 - v) * g) for v in col)
                if xx is None:
                    td.line((0, yy, w, yy), fill=col)
                else:
                    td.point((xx, yy), fill=col)
        m = Image.new("L", (w, h))
        ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), 10, fill=alpha)
        out.paste(tile, (x, y), m)
        if k < len(icons):
            icon = icons[k].convert("RGB")
            key = Image.new("L", icon.size, 255)
            kp, ip = key.load(), icon.load()
            for yy in range(icon.size[1]):
                for xx in range(icon.size[0]):
                    if ip[xx, yy] == (44, 48, 58):
                        kp[xx, yy] = 0
            out.paste(icon, (x + 25, y + 18), key)
    d = ImageDraw.Draw(out)
    for yy in range(H):
        col = tuple(int(a + (b - a) * yy / (H - 1)) for a, b in zip(btop, bbottom))
        for k, f in enumerate((1, .7, .4)):
            c2 = tuple(int(v * f) for v in col)
            d.point((k, yy), fill=c2)
            d.point((W - 1 - k, yy), fill=c2)
    for k, f in enumerate((1, .7, .4)):
        d.line((0, k, W, k), fill=tuple(int(v * f) for v in btop))
        d.line((0, H - 1 - k, W, H - 1 - k), fill=tuple(int(v * f) for v in bbottom))
    return out
