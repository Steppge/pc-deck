"""Abbild des Deck-Displays für das Hauptfenster: zeichnet eine Seite so, wie die Firmware sie zeigt
(Seitenbild, Glas-Kacheln, Icon mit durchsichtiger Schlüsselfarbe, Beschriftung, Navigationsleiste, Rand).
Maße und Regeln wie in firmware/src/main.cpp (tileRect, drawTileTo, fillGlassTo, drawNavTo, drawBorderTo)."""

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import theme
from deck import ascii_label, is_big, tile_icon, tile_label

W, H, NAV_H, ICON = 480, 320, 40, 64
KEY = (44, 48, 58)
COL_BG, COL_TILE, COL_TEXT, COL_DIM = (18, 18, 22), (44, 48, 58), (235, 235, 240), (130, 135, 150)

# Big-Deck (Firmware deck.cpp im grblHAL-Display): Kachelbereich unter der Statusleiste (60 px), rechts die
# Lautstärke-Leiste; Abstände GAP, Navigationsleiste NAV_H, Icons 1x/1,5x/2x wie place_tiles()
BIG_W, BIG_H, BIG_NAV_H, BIG_GAP, BIG_VOL_W, BIG_LABEL_H = 1024, 540, 56, 10, 84, 30
BIG_GRID_W = BIG_W - BIG_VOL_W
COL_ACCENT = (80, 150, 255)

_fonts = {}


def font(size=13):
    """Ähnlich der Display-Schrift (LovyanGFX Font2, ~16 px Zeilenhöhe; Big-Deck: Montserrat 18/24)."""
    if size not in _fonts:
        for name in ("arial.ttf", "segoeui.ttf"):
            try:
                _fonts[size] = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name),
                                                  size)
                break
            except OSError:
                continue
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def size(cfg):
    """Größe des Abbilds (Breite, Höhe) für dieses Deck."""
    return (BIG_W, BIG_H) if is_big(cfg) else (W, H)


def grid_h(cfg):
    if is_big(cfg):
        return BIG_H - BIG_NAV_H if len(cfg["pages"]) > 1 else BIG_H
    return H - NAV_H if len(cfg["pages"]) > 1 else H


def _big_tile_wh(cfg):
    cols, rows = max(cfg["cols"], 1), max(cfg["rows"], 1)
    return (BIG_GRID_W - BIG_GAP * (cols + 1)) // cols, (grid_h(cfg) - BIG_GAP * (rows + 1)) // rows


def tile_rect(cfg, i):
    c, r = i % cfg["cols"], i // cfg["cols"]
    if is_big(cfg):
        tw, th = _big_tile_wh(cfg)
        return BIG_GAP + c * (tw + BIG_GAP), BIG_GAP + r * (th + BIG_GAP), tw, th
    cw, ch = W // cfg["cols"], grid_h(cfg) // cfg["rows"]
    return c * cw + 3, r * ch + 3, cw - 6, ch - 6


def slot_at(cfg, x, y):
    """Platz unter einem Punkt (Koordinaten des Abbilds) oder None; Navigationsleiste: -1 = zurück, -2 = weiter."""
    if is_big(cfg):
        return _big_slot_at(cfg, x, y)
    if len(cfg["pages"]) > 1 and y >= H - NAV_H:
        if x < 100:
            return -1
        if x > W - 100:
            return -2
        return None
    cw, ch = W // cfg["cols"], grid_h(cfg) // cfg["rows"]
    c, r = int(x) // cw, int(y) // ch
    if c < cfg["cols"] and r < cfg["rows"]:
        return r * cfg["cols"] + c
    return None


def _big_slot_at(cfg, x, y):
    if len(cfg["pages"]) > 1 and y >= BIG_H - BIG_NAV_H:
        if x < BIG_GAP + 160:
            return -1
        if BIG_GRID_W - BIG_GAP - 160 < x < BIG_GRID_W:
            return -2
        return None
    for i in range(cfg["cols"] * cfg["rows"]):
        tx, ty, tw, th = tile_rect(cfg, i)
        if tx <= x < tx + tw + BIG_GAP and ty <= y < ty + th + BIG_GAP:
            return i
    return None


def _mix(a, b, f):
    return tuple(int(x + (y - x) * f) for x, y in zip(a, b))


_glass_cache = {}


def _glass(out, x, y, w, h, rad, res):
    """Kachelfläche wie fillGlassTo: Stil 0 einfarbig, 1 Verlauf, 2 schräger Verlauf, 3 Glanz; Deckkraft alpha."""
    key = (res["hash"], w, h, rad)
    if key not in _glass_cache:
        if len(_glass_cache) > 64:
            _glass_cache.clear()
        _glass_cache[key] = _glass_tile(w, h, rad, res)
    tile, m = _glass_cache[key]
    out.paste(tile, (x, y), m)


def _glass_tile(w, h, rad, res):
    top, bottom = res["colors"][0], res["colors"][1]
    style, alpha = res["style"], res["alpha"]
    tile = Image.new("RGB", (w, h))
    px = tile.load()
    for r in range(h):
        tv = r / (h - 1) if h > 1 else 0
        row = top if style == 0 else _mix(top, bottom, tv)
        gloss = 0
        if style == 3:
            if r < h // 2:
                gloss = 90 * (h // 2 - r) // (h // 2) + 20
            if r == 1:
                gloss = 170
        for c in range(w):
            col = _mix(top, bottom, (c / (w - 1) + tv) / 2) if style == 2 else row
            gl = 0 if (r == 1 and (c < 6 or c > w - 7)) else gloss
            if gl:
                col = _mix(col, (255, 255, 255), gl / 255)
            px[c, r] = col
    m = Image.new("L", (w, h))
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), rad, fill=alpha)
    return tile, m


def _fit(d, text, max_w, fnt=None):
    fnt = fnt or font()
    if d.textlength(text, font=fnt) <= max_w:
        return text
    while len(text) > 1 and d.textlength(text + "..", font=fnt) > max_w:
        text = text[:-1]
    return text + ".."


def _render_big(cfg, page, live, title, selected, accent, volume):
    """Big-Deck: Seite wie deck.cpp (draw_tile, place_tiles, draw_nav, Lautstärke-Leiste) – noch ohne Designs."""
    t = theme.settings(cfg)
    res = theme.build(t, big=True) if t else None
    out = Image.new("RGB", (BIG_W, BIG_H), COL_BG)
    if res:  # Seitenbild 470x270, am Deck doppelt groß hinter der Kachelfläche
        out.paste(res["page_img"].resize((BIG_GRID_W, BIG_H), Image.BILINEAR), (0, 0))
        res = dict(res, style=1 if res["style"] == 2 else res["style"])  # schräger Verlauf: am Big-Deck senkrecht
    d = ImageDraw.Draw(out)
    tw, th = _big_tile_wh(cfg)
    isz = min(tw - 16, th - BIG_LABEL_H - 12)
    icon_px = ICON * 2 if isz >= ICON * 2 else ICON * 3 // 2 if isz >= ICON * 3 // 2 else ICON
    tiles = cfg["pages"][page] if page < len(cfg["pages"]) else []
    for i in range(cfg["cols"] * cfg["rows"]):
        tl = tiles[i] if i < len(tiles) else None
        lv = (live or {}).get(i)
        if not tl and not lv:
            continue
        x, y, w, h = tile_rect(cfg, i)
        if res:
            _glass(out, x, y, w, h, 12, res)
        else:
            d.rounded_rectangle((x, y, x + w - 1, y + h - 1), 12, fill=COL_TILE)
        if lv:
            icon, label = lv
        else:
            label = ascii_label(tile_label(tl))
            icon = None if tl.get("text_only") else tile_icon(dict(tl, _slot=i), label)
        if icon is not None:
            icon = icon.convert("RGB").resize((ICON, ICON)) if icon.size != (ICON, ICON) else icon.convert("RGB")
            icon = theme.deck_icon(icon, cfg).convert("RGB")
            mask = None
            if res:  # Schlüsselfarbe durchsichtig wie in der Firmware
                mask = Image.new("L", icon.size, 255)
                mp, ip = mask.load(), icon.load()
                for yy in range(ICON):
                    for xx in range(ICON):
                        if ip[xx, yy] == KEY:
                            mp[xx, yy] = 0
                mask = mask.resize((icon_px, icon_px), Image.NEAREST)
            icon = icon.resize((icon_px, icon_px), Image.BILINEAR)
            out.paste(icon, (x + (w - icon_px) // 2, y + (h - icon_px) // 2 - BIG_LABEL_H // 2), mask)
            if label:
                d.text((x + w // 2, y + h - 6), _fit(d, label, w - 12, font(18)), font=font(18), fill=COL_TEXT,
                       anchor="md")
        elif label:
            d.text((x + w // 2, y + h // 2), _fit(d, label, w - 12, font(24)), font=font(24), fill=COL_TEXT,
                   anchor="mm")
        if selected == i:
            for k in range(3):
                d.rounded_rectangle((x + k, y + k, x + w - 1 - k, y + h - 1 - k), 12 - k, outline=accent)
    pages = len(cfg["pages"])
    if pages > 1:
        y0 = BIG_H - BIG_NAV_H
        for bx in (BIG_GAP, BIG_GRID_W - BIG_GAP - 160):
            if res:
                _glass(out, bx, y0 + 4, 160, BIG_NAV_H - 10, 10, res)
            else:
                d.rounded_rectangle((bx, y0 + 4, bx + 159, y0 + BIG_NAV_H - 7), 10, fill=COL_TILE)
        d.text((BIG_GAP + 80, y0 + BIG_NAV_H // 2), "<", font=font(24), fill=COL_TEXT, anchor="mm")
        d.text((BIG_GRID_W - BIG_GAP - 80, y0 + BIG_NAV_H // 2), ">", font=font(24), fill=COL_TEXT, anchor="mm")
        nav = ascii_label(title) if title else f"{page + 1} / {pages}"
        d.text((BIG_GRID_W // 2, y0 + BIG_NAV_H // 2), nav, font=font(22), fill=COL_TEXT, anchor="mm")
    # Lautstärke-Leiste wie deck.cpp (Big-Deck FW 38): Mischpult-Fader – Schlitz 8x420 (y 60..480) mit Skala,
    # leuchtender Pegel, silberne Sattel-Kappe 46x64; Lautsprecher oben, Wert unten
    cx = BIG_GRID_W + BIG_VOL_W // 2
    d.polygon([(cx - 9, 12), (cx - 4, 12), (cx + 2, 6), (cx + 2, 24), (cx - 4, 18), (cx - 9, 18)], fill=COL_DIM)
    sy, sh = 60, 420
    for n in range(11):  # Skala
        y = sy + n * 42
        ln, col = (12, (0x7A, 0x7F, 0x8C)) if n in (0, 5, 10) else (7, (0x4A, 0x4E, 0x58))
        d.rectangle((cx - 10 - ln, y - 1, cx - 11, y), fill=col)
        d.rectangle((cx + 10, y - 1, cx + 9 + ln, y), fill=col)
    d.rounded_rectangle((cx - 4, sy, cx + 3, sy + sh - 1), 4, fill=(0x07, 0x08, 0x0B), outline=(0x3A, 0x3E, 0x48))
    v = 50 if volume is None else max(0, min(100, int(volume)))
    ky = sy + sh - sh * v // 100
    glow = Image.new("L", (BIG_VOL_W, BIG_H))
    ImageDraw.Draw(glow).rounded_rectangle((BIG_VOL_W // 2 - 4, ky, BIG_VOL_W // 2 + 3, sy + sh - 1), 4, fill=160)
    glow = glow.filter(ImageFilter.GaussianBlur(6))
    out.paste(Image.new("RGB", glow.size, COL_ACCENT), (BIG_GRID_W, 0), glow)
    d.rounded_rectangle((cx - 4, ky, cx + 3, sy + sh - 1), 4, fill=COL_ACCENT)
    CW, CH, E = 46, 64, 18  # Kappe Breite, Höhe, erhöhte Enden
    kx0, ky0 = cx - CW // 2, ky - CH // 2
    sh_m = Image.new("L", (BIG_VOL_W, BIG_H))
    ImageDraw.Draw(sh_m).rounded_rectangle((kx0 - BIG_GRID_W, ky0 + 4, kx0 - BIG_GRID_W + CW - 1, ky0 + CH + 3), 6,
                                           fill=180)
    sh_m = sh_m.filter(ImageFilter.GaussianBlur(7))
    out.paste(Image.new("RGB", sh_m.size, (0, 0, 0)), (BIG_GRID_W, 0), sh_m)
    # Kappe in Sattelform (wie deck.cpp): erhöhte Enden mit Riffeln, Mulde dazwischen, weiße Mittellinie
    cap = Image.new("RGB", (CW, CH))
    cp = ImageDraw.Draw(cap)

    def vgrad(y0, y1, c0, c1, x0=0, x1=CW - 1):
        for r in range(y0, y1 + 1):
            cp.line((x0, r, x1, r), fill=_mix(c0, c1, (r - y0) / max(1, y1 - y0)))

    def blend_line(y, x0, x1, col, a):
        for xx in range(x0, x1 + 1):
            cap.putpixel((xx, y), _mix(cap.getpixel((xx, y)), col, a / 255))

    cy = CH // 2
    vgrad(0, CH - 1, (0xD9, 0xDC, 0xE3), (0x7C, 0x81, 0x8C))
    vgrad(1 + E, cy - 1, (0x80, 0x84, 0x8E), (0xC6, 0xC9, 0xD0), 1, CW - 2)  # Mulde
    vgrad(cy, CH - 2 - E, (0xC6, 0xC9, 0xD0), (0x80, 0x84, 0x8E), 1, CW - 2)
    for y0, c0, c1 in ((1, (0xE6, 0xE8, 0xED), (0xA9, 0xAD, 0xB6)),
                       (CH - 1 - E, (0xA9, 0xAD, 0xB6), (0xE6, 0xE8, 0xED))):  # erhöhte Enden
        end_img = Image.new("RGB", (CW - 2, E))
        ed = ImageDraw.Draw(end_img)
        for r in range(E):
            ed.line((0, r, CW - 3, r), fill=_mix(c0, c1, r / (E - 1)))
        em = Image.new("L", (CW - 2, E))
        ImageDraw.Draw(em).rounded_rectangle((0, 0, CW - 3, E - 1), 5, fill=255)
        cap.paste(end_img, (1, y0), em)
    for y in (4, 8, 12, CH - 1 - 5, CH - 1 - 9, CH - 1 - 13):  # Riffel
        cp.line((4, y, CW - 5, y), fill=(0x3A, 0x3D, 0x46))
        blend_line(y + 1, 4, CW - 5, (255, 255, 255), 110)
    blend_line(1, 6, CW - 7, (255, 255, 255), 120)  # Glanz oben
    blend_line(cy - 2, 1, CW - 2, (0x3A, 0x3D, 0x46), 160)  # Mittellinie
    cp.line((1, cy - 1, CW - 2, cy - 1), fill=(255, 255, 255))
    cp.line((1, cy, CW - 2, cy), fill=(255, 255, 255))
    blend_line(cy + 1, 1, CW - 2, (0x3A, 0x3D, 0x46), 160)
    cp.rounded_rectangle((0, 0, CW - 1, CH - 1), 6, outline=(0x4A, 0x4E, 0x58))
    m = Image.new("L", (CW, CH))
    ImageDraw.Draw(m).rounded_rectangle((0, 0, CW - 1, CH - 1), 6, fill=255)
    out.paste(cap, (kx0, ky0), m)
    d = ImageDraw.Draw(out)
    d.text((cx, BIG_H - 6), str(v) if volume is not None else "", font=font(16), fill=COL_DIM, anchor="md")
    if res:  # Rand einfarbig (Rand oben)
        for k, f in enumerate((1, .7, .4)):
            col = tuple(int(c * f) for c in res["colors"][2])
            d.rectangle((k, k, BIG_W - 1 - k, BIG_H - 1 - k), outline=col)
    return out


def render(cfg, page, live=None, title=None, selected=None, accent=(183, 99, 203), volume=None):
    """Seite `page` als Bild (480x320, Big-Deck 1024x540). live: {platz: (icon oder None, text)} für Live-Kacheln
    dieser Seite. selected: markierter Platz (nur in der App, Rahmen in der Akzentfarbe). volume: Big-Deck-Regler."""
    if is_big(cfg):
        return _render_big(cfg, page, live, title, selected, accent, volume)
    t = theme.settings(cfg)
    res = theme.build(t) if t else None
    out = res["page_img"].resize((W, H), Image.BILINEAR) if res else Image.new("RGB", (W, H), COL_BG)
    d = ImageDraw.Draw(out)
    tiles = cfg["pages"][page] if page < len(cfg["pages"]) else []
    for i in range(cfg["cols"] * cfg["rows"]):
        tl = tiles[i] if i < len(tiles) else None
        lv = (live or {}).get(i)
        if not tl and not lv:
            continue
        x, y, w, h = tile_rect(cfg, i)
        if res:
            _glass(out, x, y, w, h, 10, res)
        else:
            d.rounded_rectangle((x, y, x + w - 1, y + h - 1), 10, fill=COL_TILE)
        if lv:
            icon, label = lv
        else:
            label = ascii_label(tile_label(tl))
            icon = None if tl.get("text_only") else tile_icon(dict(tl, _slot=i), label)
        if icon is not None:
            icon = icon.convert("RGB").resize((ICON, ICON)) if icon.size != (ICON, ICON) else icon.convert("RGB")
            icon = theme.deck_icon(icon, cfg).convert("RGB")  # Schwarz/Weiß, Icon-Anpassung wie am Deck
            iy = y + (h - ICON - 18) // 2 if label else y + (h - ICON) // 2
            ix = x + (w - ICON) // 2
            mask = None
            if res:  # Schlüsselfarbe durchsichtig wie in der Firmware
                mask = Image.new("L", icon.size, 255)
                mp, ip = mask.load(), icon.load()
                for yy in range(ICON):
                    for xx in range(ICON):
                        if ip[xx, yy] == KEY:
                            mp[xx, yy] = 0
            out.paste(icon, (ix, iy), mask)
            if label:
                d.text((x + w // 2, iy + ICON + 2), _fit(d, label, w - 6), font=font(), fill=COL_TEXT, anchor="mt")
        elif label:
            d.text((x + w // 2, y + h // 2), _fit(d, label, w - 6), font=font(), fill=COL_TEXT, anchor="mm")
        if selected == i:
            for k in range(3):
                d.rounded_rectangle((x + k, y + k, x + w - 1 - k, y + h - 1 - k), 10 - k, outline=accent)
    pages = len(cfg["pages"])
    if pages > 1:
        y0 = H - NAV_H
        if res:
            _glass(out, 4, y0 + 4, 90, NAV_H - 8, 8, res)
            _glass(out, W - 94, y0 + 4, 90, NAV_H - 8, 8, res)
        else:
            d.rectangle((0, y0, W, H), fill=COL_BG)
            for bx in (4, W - 94):
                d.rounded_rectangle((bx, y0 + 4, bx + 89, y0 + NAV_H - 5), 8, fill=COL_TILE)
        d.text((49, y0 + NAV_H // 2), "<", font=font(), fill=COL_TEXT, anchor="mm")
        d.text((W - 49, y0 + NAV_H // 2), ">", font=font(), fill=COL_TEXT, anchor="mm")
        nav = f"{ascii_label(title)}  {page + 1}/{pages}" if title else f"Seite {page + 1} / {pages}"
        d.text((W // 2, y0 + NAV_H // 2), nav, font=font(), fill=COL_TEXT if res else COL_DIM, anchor="mm")
    if res:  # Verlaufsrand wie drawBorderTo
        btop, bbottom = res["colors"][2], res["colors"][3]
        for yy in range(H):
            col = _mix(btop, bbottom, yy / (H - 1))
            for k, f in enumerate((1, .7, .4)):
                c2 = tuple(int(v * f) for v in col)
                d.point((k, yy), fill=c2)
                d.point((W - 1 - k, yy), fill=c2)
        for k, f in enumerate((1, .7, .4)):
            d.line((k, k, W - 1 - k, k), fill=tuple(int(v * f) for v in btop))
            d.line((k, H - 1 - k, W - 1 - k, H - 1 - k), fill=tuple(int(v * f) for v in bbottom))
    return out
