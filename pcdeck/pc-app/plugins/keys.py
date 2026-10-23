"""Kachel-Typen: Tastenkürzel, Textbaustein, Seite wechseln (Unterseiten)."""

import logging

import plugin_api
import winkeys
from plugin_api import Field, TileType
from plugins.live import BLUE, DIM, WHITE, canvas, fit_text, font

log = logging.getLogger("pcdeck")


def keycap(text, color=WHITE):
    """Text in Tastenkappen-Optik."""
    img, d = canvas()
    d.rounded_rectangle((5, 12, 58, 50), 8, outline=DIM, width=2)
    d.text((32, 31), text, font=fit_text(d, text, 48, 16), fill=color, anchor="mm")
    return img


# ----------------------------------------------------------------- Tastenkürzel
class Hotkey(TileType):
    id = "hotkey"
    name = "Tastenkürzel"
    fields = [Field("keys", "Tastenkürzel, mehrere nacheinander mit Komma (z. B. Strg+Umschalt+M  oder  "
                            "Strg+K, Strg+C):", width=40)]

    def is_valid(self, tile):
        return bool(tile.get("keys", "").strip())

    def label(self, tile):
        return tile.get("keys", "").strip() or self.name

    def icon(self, tile, label):
        if tile.get("icon"):
            return super().icon(tile, label)
        keys = tile.get("keys", "").strip()
        last = keys.split(",")[-1].split("+")[-1].strip() if keys else "?"
        return keycap(last.upper() if len(last) == 1 else last)

    def prepare(self, tile):
        winkeys.parse_sequence(tile.get("keys", ""))  # Fehler früh im Log sehen
        return tile

    def on_press(self, tile, ctx):
        try:
            winkeys.send_sequence(tile.get("keys", ""))
        except ValueError as e:
            log.error("Tastenkürzel '%s': %s", tile.get("keys"), e)


# ----------------------------------------------------------------- Textbaustein
class TextSnippet(TileType):
    id = "text"
    name = "Textbaustein (Text eintippen)"
    fields = [Field("text", "Text (Zeilenumbruch mit \\n):", width=60),
              Field("enter", "Danach Enter drücken", kind="bool")]

    def is_valid(self, tile):
        return bool(tile.get("text", ""))

    def label(self, tile):
        return "Text"

    def icon(self, tile, label):
        if tile.get("icon"):
            return super().icon(tile, label)
        img, d = canvas()
        for k, w in enumerate((44, 36, 44, 24)):
            d.line((10, 18 + k * 9, 10 + w, 18 + k * 9), fill=WHITE, width=3)
        return img

    def on_press(self, tile, ctx):
        text = tile.get("text", "").replace("\\n", "\n")
        if tile.get("enter"):
            text += "\n"
        winkeys.type_text(text)


# ----------------------------------------------------------------- Seite wechseln
PAGE_CHOICES = [("back", "Zurück (vorherige Seite)"), ("next", "Nächste Seite"), ("prev", "Vorherige Seite")] + \
               [(str(n), f"Seite {n}") for n in range(1, 9)]


class GotoPage(TileType):
    id = "page"
    name = "Seite wechseln (Unterseite / zurück)"
    fields = [Field("target", "Ziel:", kind="choice", default="back", choices=PAGE_CHOICES, width=30)]

    def label(self, tile):
        t = tile.get("target", "back")
        return {"back": "Zurueck", "next": "Weiter", "prev": "Vorherige"}.get(t, f"Seite {t}")

    def icon(self, tile, label):
        if tile.get("icon"):
            return super().icon(tile, label)
        t = tile.get("target", "back")
        img, d = canvas()
        if t in ("back", "prev"):
            d.polygon([(12, 32), (34, 14), (34, 50)], fill=BLUE)
            d.rectangle((34, 26, 52, 38), fill=BLUE)
        elif t == "next":
            d.polygon([(52, 32), (30, 14), (30, 50)], fill=BLUE)
            d.rectangle((12, 26, 30, 38), fill=BLUE)
        else:
            d.rounded_rectangle((10, 8, 54, 56), 6, outline=BLUE, width=3)
            d.text((32, 32), t, font=font(26, True), fill=WHITE, anchor="mm")
        return img

    def on_press(self, tile, ctx):
        link = ctx.link if ctx else None
        if not link:
            return
        n = link.page_count()
        cur = link.visible_page
        t = tile.get("target", "back")
        if t == "back":
            target = link.prev_page
        elif t == "next":
            target = (cur + 1) % n
        elif t == "prev":
            target = (cur - 1) % n
        else:
            target = int(t) - 1
        if 0 <= target < n:
            link.goto_page(target)
        else:
            log.warning("Seite %s gibt es nicht (Deck hat %d Seiten)", t, n)


for _t in (Hotkey(), TextSnippet(), GotoPage()):
    plugin_api.register(_t)
