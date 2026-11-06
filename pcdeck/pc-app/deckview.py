"""Abbild des Deck-Displays für das Hauptfenster: zeichnet eine Seite so, wie die Firmware sie zeigt
(Seitenbild, Glas-Kacheln, Icon mit durchsichtiger Schlüsselfarbe, Beschriftung, Navigationsleiste, Rand).
Maße und Regeln wie in firmware/src/main.cpp (tileRect, drawTileTo, fillGlassTo, drawNavTo, drawBorderTo)."""

import os

from PIL import Image, ImageDraw, ImageFont

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
    out = Image.new("RGB", (BIG_W, BIG_H), COL_BG)
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
        d.rounded_rectangle((x, y, x + w - 1, y + h - 1), 12, fill=COL_TILE)
        if lv:
            icon, label = lv
        else:
            label = ascii_label(tile_label(tl))
            icon = None if tl.get("text_only") else tile_icon(dict(tl, _slot=i), label)
        if icon is not None:
            icon = icon.convert("RGB").resize((ICON, ICON)) if icon.size != (ICON, ICON) else icon.convert("RGB")
            icon = theme.deck_icon(icon, cfg).convert("RGB").resize((icon_px, icon_px), Image.BILINEAR)
            out.paste(icon, (x + (w - icon_px) // 2, y + (h - icon_px) // 2 - BIG_LABEL_H // 2))
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
            d.rounded_rectangle((bx, y0 + 4, bx + 159, y0 + BIG_NAV_H - 7), 10, fill=COL_TILE)
        d.text((BIG_GAP + 80, y0 + BIG_NAV_H // 2), "<", font=font(24), fill=COL_TEXT, anchor="mm")
        d.text((BIG_GRID_W - BIG_GAP - 80, y0 + BIG_NAV_H // 2), ">", font=font(24), fill=COL_TEXT, anchor="mm")
        nav = ascii_label(title) if title else f"{page + 1} / {pages}"
        d.text((BIG_GRID_W // 2, y0 + BIG_NAV_H // 2), nav, font=font(22), fill=COL_TEXT, anchor="mm")
    # Lautstärke-Leiste: Lautsprecher oben, senkrechter Regler, Wert darunter
    cx = BIG_GRID_W + BIG_VOL_W // 2
    d.polygon([(cx - 12, 22), (cx - 6, 22), (cx + 2, 14), (cx + 2, 40), (cx - 6, 32), (cx - 12, 32)], fill=COL_DIM)
    sh = BIG_H - 2 * BIG_GAP - 100
    sy = (BIG_H - sh) // 2
    d.rounded_rectangle((cx - 14, sy, cx + 13, sy + sh), 14, fill=COL_TILE)
    v = 50 if volume is None else max(0, min(100, int(volume)))
    ky = sy + sh - sh * v // 100
    d.rounded_rectangle((cx - 14, ky, cx + 13, sy + sh), 14, fill=COL_ACCENT)
    d.ellipse((cx - 22, ky - 22, cx + 21, ky + 21), fill=COL_TEXT)
    d.text((cx, sy + sh + 28), str(v) if volume is not None else "", font=font(18), fill=COL_TEXT, anchor="mm")
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
