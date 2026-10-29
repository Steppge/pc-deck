"""Kachel-Typen und Plugin-System.

Jede Kachel hat einen Typ (Feld "type" in der Konfiguration; fehlt es, gilt "launch"
– so bleiben alte Konfigurationen gültig). Ein Typ legt fest:

  - name        Anzeigename im Kachel-Dialog
  - fields      Konfigurationsfelder (werden direkt in der Kachel gespeichert)
  - on_press    was beim Antippen passiert
  - label/icon  Beschriftung und 64x64-Icon für das Deck
  - poll/render für Live-Kacheln (Basisklasse LiveTileType, Deck-Firmware ab Version 2)

Eingebaute Typen liegen im Paket "plugins". Eigene Plugins: .py-Dateien in
%USERPROFILE%\\PCDeck\\plugins\\ – sie werden beim Start geladen und registrieren ihre
Typen mit plugin_api.register(MeinTyp()). Beispiel:

    import plugin_api
    from plugin_api import Field, TileType

    class Hallo(TileType):
        id = "hallo"
        name = "Hallo sagen"
        fields = [Field("text", "Text:")]

        def on_press(self, tile, ctx):
            plugin_api.log.info("Hallo: %s", tile.get("text", ""))

    plugin_api.register(Hallo())
"""

import importlib.util
import logging
import os
import sys
import threading

log = logging.getLogger("pcdeck")

DEFAULT_TYPE = "launch"


class Field:
    """Ein Konfigurationsfeld eines Kachel-Typs.

    kind: "text", "bool", "path" (Programm/Datei/Ordner mit Auswahl-Knöpfen),
          "file" (Datei-Auswahl), "choice" (Auswahlliste), "multi" (Häkchen-Liste,
          Wert = Liste der angehakten Werte), "secret" (Passwort, DPAPI-verschlüsselt gespeichert),
          "info" (nur Hinweistext, nicht gespeichert; label = Text, default = Link für „Download-Seite öffnen“
          – beides darf eine Funktion sein, die beim Öffnen des Dialogs ausgewertet wird),
          "links" (Schaltfläche, die ein Fenster mit Links öffnet; label = Beschriftung,
          default = [(Überschrift, Hinweis, [(Name, Link, Beschreibung), ...]), ...]),
          "tool" (Schaltfläche; default = Funktion(kachel_dialog), z. B. ein eigenes Fenster öffnen).
    choices: Liste von (wert, anzeige) oder Funktion, die so eine Liste liefert.
    enabled_by: Schlüssel eines bool-Feldes; das Feld ist nur aktiv, wenn dieses gesetzt ist.
    """

    def __init__(self, key, label, kind="text", default=None, choices=None, enabled_by=None, width=48):
        self.key = key
        self.label = label
        self.kind = kind
        self.default = default if default is not None else (False if kind == "bool" else "")
        self.choices = choices
        self.enabled_by = enabled_by
        self.width = width

    def get_choices(self):
        c = self.choices() if callable(self.choices) else (self.choices or [])
        return [x if isinstance(x, tuple) else (x, str(x)) for x in c]


class TileType:
    id = ""
    name = ""
    group = ""  # Gruppe in der Typ-Auswahl (leer = "Eigene Plugins"), siehe GROUPS
    fields = []

    def label(self, tile):
        """Beschriftung, wenn der Nutzer keine eigene eingetragen hat."""
        return self.name

    def icon(self, tile, label):
        """64x64-PIL-Bild. Standard: eigenes Icon der Kachel oder Buchstaben-Platzhalter."""
        import iconextract
        return iconextract.get_icon("", tile.get("icon", ""), label)

    def is_valid(self, tile):
        """False = Kachel ist leer (wird beim Speichern im Dialog entfernt)."""
        return True

    def prepare(self, tile):
        """Letzte Ergänzungen beim Speichern im Dialog (z. B. Gerätenamen merken)."""
        return tile

    def on_press(self, tile, ctx):
        pass

    def on_long_press(self, tile, ctx):
        """Langer Tipper (0,6 s, Deck-Firmware ab Version 3). Standard: wie kurz."""
        self.on_press(tile, ctx)

    rotatable = False  # True = per Drehknopf einstellbar ("Kachel einstellen", z. B. Rollladen-Position)

    def on_rotate(self, tile, ctx, steps):
        """Drehknopf: steps Rasten (+ = rechts). Nur bei rotatable = True."""

    def on_knob_press(self, tile, ctx):
        """Drehknopf gedrückt, während er dieser Kachel gehört (siehe DeckLink.grab_knob)."""

    def knob_osd(self, tile):
        """(prozent oder None, text) für die Wert-Anzeige, solange der Drehknopf dieser Kachel gehört."""
        return None

    live = False  # True = Live-Kachel (siehe LiveTileType)
    # Zustand, in dem die Kachel genau wie ihr gespeichertes Bild aussieht: dann muss nach einem
    # Seitenwechsel nichts geschickt werden (das Deck zeigt ohnehin das Bild aus dem Flash)
    idle_state = object()


class LiveTileType(TileType):
    """Kachel, die sich laufend aktualisiert (Uhr, CPU, ...).

    Die App ruft etwa jede Sekunde poll() für die Kacheln der sichtbaren Seite auf.
    Ändert sich der Zustand, zeichnet render() die Kachel neu und sie wird per UPD
    nur in den RAM des Decks geschickt.
    """

    live = True

    def poll(self, tile, ctx):
        """Aktueller Zustand (vergleichbar, z. B. Zahl oder Tupel)."""
        return None

    def render(self, tile, state, ctx):
        """(PIL-Bild 64x64, Beschriftung). state=None: Ruhebild für die Grundübertragung."""
        import iconextract
        return iconextract.get_icon("", tile.get("icon", ""), self.name), self.name

    def label(self, tile):
        return self.render(tile, None, None)[1]

    def icon(self, tile, label):
        # Ruhebild ohne aktuellen Zustand – bleibt gleich, damit die Prüfsumme stabil ist
        return self.render(tile, None, None)[0]


class PressContext:
    """Was ein Plugin beim Antippen/Abfragen über die Umgebung wissen/benutzen darf."""

    def __init__(self, deck_id=None, page=None, idx=None, link=None):
        self.deck_id = deck_id
        self.page = page
        self.idx = idx
        self.link = link  # deck.DeckLink oder None

    @property
    def key(self):
        """Eindeutiger Schlüssel der Kachel, z. B. für Zustände wie bei der Stoppuhr."""
        return f"{self.deck_id}/{self.page}/{self.idx}"


class _Unknown(TileType):
    """Kachel eines Typs, dessen Plugin fehlt."""

    def __init__(self, type_id):
        self.id = type_id
        self.name = f"Unbekannt ({type_id})"

    def on_press(self, tile, ctx):
        log.warning("Kachel-Typ '%s' ist nicht installiert", self.id)


_types = {}
_loaded = False
_load_lock = threading.Lock()


def register(tile_type):
    if not tile_type.id:
        raise ValueError("Kachel-Typ ohne id")
    _types[tile_type.id] = tile_type
    return tile_type


def type_id(tile):
    return (tile or {}).get("type") or DEFAULT_TYPE


def get(tile_or_id):
    load()
    tid = tile_or_id if isinstance(tile_or_id, str) else type_id(tile_or_id)
    return _types.get(tid) or _Unknown(tid)


# Gruppen der Typ-Auswahl im Kachel-Dialog, in dieser Reihenfolge; darin die eingebauten Typen in dieser
# Reihenfolge. Eigene Plugins setzen `group` selbst (eine dieser Gruppen oder eine neue), sonst "Eigene Plugins".
GROUPS = [
    ("Programme", ["launch"]),
    ("Tastatur & Text", ["hotkey", "text"]),
    ("Medien", ["media", "nowplaying"]),
    ("Audio", ["volume", "audio_output", "audio_profile", "mic_mute"]),
    ("Soundboard", ["sound", "sound_stop_all"]),
    ("Info & Zeit", ["clock", "cpu", "ram", "stopwatch", "timer"]),
    ("Smart Home", ["bosch"]),
    ("Streaming", ["obs"]),
    ("Deck", ["page"]),
]
OWN_GROUP = "Eigene Plugins"


def all_types():
    """Alle registrierten Typen, "launch" zuerst, Rest nach Namen."""
    load()
    return sorted(_types.values(), key=lambda t: (t.id != DEFAULT_TYPE, t.name.lower()))


def grouped_types():
    """[(Gruppe, [Typen, ...]), ...] für die Typ-Auswahl – Reihenfolge wie GROUPS, leere Gruppen fehlen."""
    load()
    group_of = {tid: g for g, ids in GROUPS for tid in ids}
    rank = {tid: i for _, ids in GROUPS for i, tid in enumerate(ids)}
    order = [g for g, _ in GROUPS]
    buckets = {}
    for t in _types.values():
        g = t.group or group_of.get(t.id, OWN_GROUP)
        buckets.setdefault(g, []).append(t)
    names = order + sorted(g for g in buckets if g not in order and g != OWN_GROUP) + [OWN_GROUP]
    return [(g, sorted(buckets[g], key=lambda t: (rank.get(t.id, 99), t.name.lower())))
            for g in names if g in buckets]


def plugin_dir():
    import paths
    return paths.PLUGIN_DIR


def load():
    """Eingebaute und eigene Plugins einmalig laden."""
    global _loaded
    with _load_lock:
        if _loaded:
            return
        _loaded = True
        import plugins  # noqa: F401  (registriert die eingebauten Typen)
        d = plugin_dir()
        if not os.path.isdir(d):
            return
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            name = "pcdeck_plugin_" + fn[:-3]
            try:
                spec = importlib.util.spec_from_file_location(name, os.path.join(d, fn))
                mod = importlib.util.module_from_spec(spec)
                sys.modules[name] = mod
                spec.loader.exec_module(mod)
                log.info("Plugin geladen: %s", fn)
            except Exception:
                log.exception("Plugin %s konnte nicht geladen werden", fn)


def press(tile, ctx=None, long=False):
    """Kachel ausführen; Fehler eines Plugins landen im Log statt die App zu stören."""
    tt = get(tile)
    try:
        if long:
            tt.on_long_press(tile, ctx or PressContext())
        else:
            tt.on_press(tile, ctx or PressContext())
    except Exception:
        log.exception("Fehler in Kachel-Typ '%s'", tt.id)
