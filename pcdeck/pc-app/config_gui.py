"""Konfigurationsfenster: Kacheln pro Deck anlegen, per Drag & Drop oder Dialog."""

import logging
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import ImageTk

import boschshc
import fwfile
import iconextract
import knob
import plugin_api
import theme
import winaudio
import winctl
import winkeys
import winsecret
from paths import APP_DIR
from version import VERSION
from deck import FOREGROUND_IGNORE, COLS_RANGE, MAX_PAGES, ROWS_RANGE, auto_apps, empty_page, tile_icon, tile_label

try:
    from tkinterdnd2 import DND_FILES
    HAVE_DND = True
except ImportError:
    HAVE_DND = False

BG = "#121216"
TILE = "#2c303a"
TEXT = "#ebebf0"
DIM = "#82879a"
START_MENU = [
    os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
]


def _icon_photo(tile):
    return ImageTk.PhotoImage(tile_icon(tile))


# ======================================================================= Kachel-Dialog
class _MultiVar:
    """Wert einer Häkchen-Liste: Liste der angehakten Werte (Reihenfolge wie angezeigt)."""

    def __init__(self):
        self.items = []  # (wert, BooleanVar)

    def get(self):
        return [v for v, b in self.items if b.get()]

    def trace_add(self, mode, cb):
        for _, b in self.items:
            b.trace_add(mode, cb)


class TileDialog(tk.Toplevel):
    """Kachel bearbeiten: Typ wählen, darunter die Felder dieses Typs."""

    def __init__(self, parent, tile):
        super().__init__(parent)
        self.title("Kachel bearbeiten")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.data = dict(tile or {})          # Werte aller Typen (bleiben beim Typwechsel erhalten)
        self.type = plugin_api.get(self.data)
        self.vars = {}

        self.v_label = tk.StringVar(value=self.data.get("label", ""))
        self.v_icon = tk.StringVar(value=self.data.get("icon", ""))
        self.v_text_only = tk.BooleanVar(value=bool(self.data.get("text_only")))

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)

        top = ttk.Frame(f)
        top.grid(row=0, column=0, columnspan=4, sticky="we")
        ttk.Label(top, text="Typ:").pack(side="left")
        # nach Gruppen: graue Überschrift "── Audio ──", darunter eingerückt die Typen (Überschrift = None)
        self.entries = []
        for group, types in plugin_api.grouped_types():
            self.entries.append((None, f"──  {group}  ──"))
            self.entries += [(t, f"      {t.name}") for t in types]
        if self.type not in [t for t, _ in self.entries]:  # unbekannter Typ: trotzdem anzeigen
            self.entries.append((self.type, f"      {self.type.name}"))
        self.type_box = ttk.Combobox(top, state="readonly", width=44, height=24,
                                     values=[label for _, label in self.entries])
        self.type_box.current([t for t, _ in self.entries].index(self.type))
        self.type_box.pack(side="left", padx=6)
        self.type_box.bind("<<ComboboxSelected>>", self.on_type_changed)

        self.fields_frame = ttk.Frame(f)
        self.fields_frame.grid(row=1, column=0, columnspan=4, sticky="we", pady=(10, 0))

        ttk.Label(f, text="Beschriftung (leer = automatisch):").grid(row=3, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Entry(f, textvariable=self.v_label, width=30).grid(row=4, column=0, columnspan=2, sticky="we")

        ttk.Label(f, text="Eigenes Icon (optional, .png/.ico/.jpg):").grid(row=7, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Entry(f, textvariable=self.v_icon, width=48).grid(row=8, column=0, columnspan=3, sticky="we")
        ttk.Button(f, text="...", width=4, command=self.pick_icon).grid(row=8, column=3, sticky="w")
        ttk.Checkbutton(f, text="Nur Text anzeigen (kein Icon)", variable=self.v_text_only).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # Fester Rahmen in Pixeln: width/height eines Labels zählen ohne Bild in Zeichen/Zeilen
        pv = tk.Frame(f, width=120, height=110, bg=TILE)
        pv.grid(row=3, column=2, rowspan=4, columnspan=2, padx=(16, 0), pady=(12, 0), sticky="ne")
        pv.pack_propagate(False)
        self.preview = tk.Label(pv, bg=TILE, fg=TEXT, compound="top", font=("Segoe UI", 9), wraplength=110)
        self.preview.pack(fill="both", expand=True)

        b = ttk.Frame(f)
        b.grid(row=13, column=0, columnspan=4, sticky="e", pady=(16, 0))
        ttk.Button(b, text="Kachel leeren", command=self.clear).pack(side="left", padx=(0, 24))
        ttk.Button(b, text="Abbrechen", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(b, text="OK", command=self.ok).pack(side="left")

        for v in (self.v_label, self.v_icon, self.v_text_only):
            v.trace_add("write", lambda *a: self._schedule_preview())
        self._after = None
        self._build_fields()
        self._update_preview()
        self._place_over(parent)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def _place_over(self, parent):
        """Mittig über dem Hauptfenster, aber immer ganz auf dem Bildschirm."""
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(x, sw - w - 10))
        y = max(0, min(y, sh - h - 80))  # Platz für Titelleiste und Taskleiste
        self.geometry(f"+{x}+{y}")

    # ---------------------------------------------------------- Felder des Typs
    @staticmethod
    def _value(var):
        val = var.get()
        return getattr(var, "_map", {}).get(val, val) if isinstance(val, str) else val

    def _store_fields(self):
        for key, var in self.vars.items():
            val = self._value(var)
            if getattr(var, "_secret", False):
                if val:  # neu eingegeben -> verschlüsselt merken; leer = bisherigen Wert behalten
                    self.data[key] = winsecret.encrypt(val)
                continue
            self.data[key] = val

    def _build_fields(self):
        for w in self.fields_frame.winfo_children():
            w.destroy()
        self.vars = {}
        self._dependents = []
        row = 0
        for fld in self.type.fields:
            if fld.kind == "info":  # nur Hinweistext (Text/Link dürfen Funktionen sein), wird nicht gespeichert
                text = fld.label() if callable(fld.label) else fld.label
                url = fld.default() if callable(fld.default) else fld.default
                ttk.Label(self.fields_frame, text=text, foreground=DIM, wraplength=460, justify="left").grid(
                    row=row, column=0, columnspan=4, sticky="w", pady=(6, 0))
                row += 1
                if url:
                    ttk.Button(self.fields_frame, text="Download-Seite öffnen",
                               command=lambda u=url: webbrowser.open(u)).grid(row=row, column=0, sticky="w",
                                                                              pady=(2, 0))
                    row += 1
                continue
            if fld.kind == "tool":  # Schaltfläche -> Funktion des Plugins mit diesem Dialog (nicht gespeichert)
                ttk.Button(self.fields_frame, text=fld.label, command=lambda f=fld: f.default(self)).grid(
                    row=row, column=0, sticky="w", pady=(6, 0))
                row += 1
                continue
            if fld.kind == "links":  # Schaltfläche -> Fenster mit Links (wird nicht gespeichert)
                ttk.Button(self.fields_frame, text=fld.label,
                           command=lambda f=fld: LinksDialog(self, f.label.rstrip(" ."), f.default)).grid(
                    row=row, column=0, sticky="w", pady=(6, 0))
                row += 1
                continue
            val = self.data.get(fld.key, fld.default)
            if fld.kind == "bool":
                var = tk.BooleanVar(value=bool(val))
                w = ttk.Checkbutton(self.fields_frame, text=fld.label, variable=var)
                w.grid(row=row, column=0, columnspan=4, sticky="w", pady=(6, 0))
                row += 1
            elif fld.kind == "multi":
                ttk.Label(self.fields_frame, text=fld.label).grid(row=row, column=0, columnspan=4, sticky="w",
                                                                  pady=(6, 0))
                row += 1
                checked = list(val) if isinstance(val, list) else []
                choices = fld.get_choices()
                names = self.data.get(fld.key + "_names") or {}
                for v in checked:  # angehakt, aber gerade nicht verfügbar (z. B. abgesteckt)
                    if v not in {c for c, _ in choices}:
                        choices.append((v, f"{names.get(v, v)} (nicht verbunden)"))
                var = _MultiVar()
                w = ttk.Frame(self.fields_frame)
                w.grid(row=row, column=0, columnspan=4, sticky="w", padx=(8, 0))
                row += 1
                for v, lbl in choices:
                    b = tk.BooleanVar(value=v in checked)
                    ttk.Checkbutton(w, text=lbl, variable=b).pack(anchor="w")
                    var.items.append((v, b))
                if not choices:
                    ttk.Label(w, text="(keine gefunden)").pack(anchor="w")
            else:
                secret = fld.kind == "secret"
                var = tk.StringVar(value="" if (val is None or secret) else str(val))
                var._secret = secret
                text = fld.label
                if secret and val:
                    text += "  (gespeichert – leer lassen zum Beibehalten)"
                ttk.Label(self.fields_frame, text=text).grid(row=row, column=0, columnspan=4, sticky="w",
                                                             pady=(6, 0))
                row += 1
                if fld.kind == "choice":
                    choices = fld.get_choices()
                    cur = var.get()
                    if cur and cur not in {v for v, _ in choices}:
                        # gespeicherter Wert gerade nicht verfügbar (z. B. Gerät abgesteckt)
                        name = self.data.get(fld.key + "_name") or cur
                        choices.append((cur, f"{name} (nicht verbunden)"))
                    elif not cur and choices and not fld.default:
                        var.set(choices[0][0])
                    w = ttk.Combobox(self.fields_frame, state="readonly", width=fld.width,
                                     values=[lbl for _, lbl in choices])
                    labels = {v: l for v, l in choices}
                    values = {l: v for v, l in choices}
                    var.set(labels.get(var.get(), var.get()))
                    w.configure(textvariable=var)
                    var._map = values  # Anzeige -> Wert
                else:
                    w = ttk.Entry(self.fields_frame, textvariable=var, width=fld.width,
                                  show="•" if secret else "")
                w.grid(row=row, column=0, columnspan=4, sticky="we", pady=(2, 0))
                row += 1
                if fld.kind == "path":
                    bf = ttk.Frame(self.fields_frame)
                    bf.grid(row=row, column=0, columnspan=4, sticky="w", pady=(4, 0))
                    ttk.Button(bf, text="Programm / Datei ...", command=lambda v=var: self.pick_file(v)).pack(side="left")
                    ttk.Button(bf, text="Ordner ...", command=lambda v=var: self.pick_folder(v)).pack(side="left", padx=4)
                    ttk.Button(bf, text="Aus dem Startmenü ...", command=lambda v=var: self.pick_startmenu(v)).pack(side="left")
                    row += 1
                elif fld.kind == "file":
                    ttk.Button(self.fields_frame, text="...", width=4,
                               command=lambda v=var: self.pick_any(v)).grid(row=row - 1, column=4, sticky="w", padx=4)
            self.vars[fld.key] = var
            if fld.enabled_by:
                self._dependents.append((w, fld.enabled_by))
            var.trace_add("write", lambda *a: (self._update_dependents(), self._schedule_preview()))
        self._update_dependents()

    def _update_dependents(self):
        for w, key in self._dependents:
            on = bool(self.vars[key].get()) if key in self.vars else False
            w.configure(state="normal" if on else "disabled")

    def on_type_changed(self, _e=None):
        i = self.type_box.current()
        if self.entries[i][0] is None:  # Überschrift angeklickt -> erster Typ der Gruppe
            i += 1
            self.type_box.current(i)
        self._store_fields()
        self.type = self.entries[i][0]
        self._build_fields()
        self._update_preview()

    def _tile(self):
        t = {"label": self.v_label.get().strip(), "icon": self.v_icon.get().strip().strip('"')}
        if self.type.id != plugin_api.DEFAULT_TYPE:
            t["type"] = self.type.id
        for fld in self.type.fields:
            if fld.kind in ("info", "links", "tool"):
                continue
            var = self.vars[fld.key]
            if fld.kind == "bool":
                if var.get():
                    t[fld.key] = True
                continue
            val = self._value(var)
            if fld.kind == "multi":
                if val:
                    t[fld.key] = val
                    names = self.data.get(fld.key + "_names") or {}
                    kept = {v: names[v] for v in val if v in names}
                    if kept:
                        t[fld.key + "_names"] = kept
                continue
            if getattr(var, "_secret", False):
                if val:
                    t[fld.key] = winsecret.encrypt(val)
                elif self.data.get(fld.key):
                    t[fld.key] = self.data[fld.key]  # bisheriges (verschlüsseltes) Passwort behalten
                continue
            val = val.strip().strip('"') if isinstance(val, str) else val
            if fld.enabled_by and not self.vars[fld.enabled_by].get():
                continue
            if val != "" or fld.key in ("path", "args"):
                t[fld.key] = val
                # gemerkten Namen behalten, solange der Wert gleich bleibt (z. B. abgestecktes Gerät)
                if val == self.data.get(fld.key) and self.data.get(fld.key + "_name"):
                    t[fld.key + "_name"] = self.data[fld.key + "_name"]
        if self.v_text_only.get():
            t["text_only"] = True
        return t

    # ---------------------------------------------------------- Vorschau
    def _schedule_preview(self):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(300, self._update_preview)

    def _update_preview(self):
        t = self._tile()
        valid = self.type.is_valid(t)
        label = tile_label(t) if valid else "(leer)"
        if valid and not t.get("text_only"):
            self._photo = _icon_photo(t)
            self.preview.configure(image=self._photo, text=label)
        else:
            self._photo = None
            self.preview.configure(image="", text=label)

    # ---------------------------------------------------------- Auswahl-Dialoge
    def pick_file(self, var):
        p = filedialog.askopenfilename(parent=self, title="Programm oder Datei wählen")
        if p:
            p = os.path.normpath(p)
            var.set(iconextract.needs_shortcut(p) or p)

    def pick_folder(self, var):
        p = filedialog.askdirectory(parent=self, title="Ordner wählen")
        if p:
            var.set(os.path.normpath(p))

    def pick_startmenu(self, var):
        start = next((d for d in START_MENU if os.path.isdir(d)), None)
        p = filedialog.askopenfilename(parent=self, title="Verknüpfung im Startmenü wählen", initialdir=start)
        if p:
            p = os.path.normpath(p)
            var.set(iconextract.needs_shortcut(p) or p)

    def pick_any(self, var):
        p = filedialog.askopenfilename(parent=self, title="Datei wählen")
        if p:
            var.set(os.path.normpath(p))

    def pick_icon(self):
        p = filedialog.askopenfilename(parent=self, title="Icon wählen",
                                       filetypes=[("Bilder", "*.png *.ico *.jpg *.jpeg *.bmp *.gif"), ("Alle", "*.*")])
        if p:
            self.v_icon.set(os.path.normpath(p))

    def clear(self):
        self.result = "clear"
        self.destroy()

    def ok(self):
        t = self._tile()
        if self.type.is_valid(t):
            try:
                t = self.type.prepare(t)
            except Exception:
                logging.getLogger("pcdeck").exception("Fehler beim Speichern der Kachel")
            self.result = t
        else:
            self.result = "clear"
        self.destroy()


class SoundSearchDialog(tk.Toplevel):
    """Sounds von myinstants suchen, probehören und nach PCDeck\\Sounds herunterladen.
    on_pick(pfad, name): "In Kachel übernehmen" (aus dem Kachel-Dialog)."""

    def __init__(self, parent, on_pick=None, on_download=None):
        import soundsearch
        super().__init__(parent)
        self.ss = soundsearch
        self.title("Sounds suchen (myinstants.com)")
        self.transient(parent)
        self.on_pick = on_pick
        self.on_download = on_download  # (pfad, name) nach jedem Herunterladen, z. B. Soundboard anhaken
        self.results = queue.Queue()
        self.items = {}     # Zeilen-ID -> (name, mp3_url)
        self.bytes = {}     # mp3_url -> Bytes (schon geladen)
        self.list_args = ("popular", "", 1)
        self.busy = 0

        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.v_query = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.v_query, width=30)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.search())
        ttk.Button(top, text="Suchen", command=self.search).pack(side="left", padx=(4, 12))
        ttk.Label(top, text="Kategorie:").pack(side="left")
        self.cats = [("★ Beliebt in Deutschland", "popular", ""), ("Trend", "trending", "")] + \
            [(n, "category", s) for n, s in sorted(((n, s) for s, n in soundsearch.CATEGORIES_DE.items()),
                                                   key=lambda x: x[0].lower())]
        self.v_cat = tk.StringVar(value=self.cats[0][0])
        self.cat_box = ttk.Combobox(top, textvariable=self.v_cat, state="readonly", width=24,
                                    values=[c[0] for c in self.cats])
        self.cat_box.pack(side="left", padx=4)
        self.cat_box.bind("<<ComboboxSelected>>", lambda ev: self._on_cat())
        self.cat_box.bind("<MouseWheel>", lambda ev: "break")  # Mausrad soll nicht versehentlich wechseln

        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(mid, columns=("ok",), height=16, selectmode="browse")
        self.tree.heading("#0", text="Sound")
        self.tree.heading("ok", text="geladen")
        self.tree.column("#0", width=380)
        self.tree.column("ok", width=70, anchor="center")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda ev: self.listen())

        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="▶ Anhören", command=self.listen).pack(side="left")
        ttk.Button(b, text="■ Stopp", command=self.stop).pack(side="left", padx=4)
        ttk.Button(b, text="⬇ Herunterladen", command=self.download).pack(side="left", padx=(12, 4))
        if on_pick:
            ttk.Button(b, text="In Kachel übernehmen", command=lambda: self.download(pick=True)).pack(side="left")
        ttk.Button(b, text="Mehr laden", command=self.more).pack(side="left", padx=(12, 0))
        b2 = ttk.Frame(f)
        b2.pack(fill="x", pady=(6, 0))
        ttk.Button(b2, text="Sound-Ordner öffnen", command=self.open_folder).pack(side="left")
        ttk.Button(b2, text="Schließen", command=self.close).pack(side="right")
        self.status = ttk.Label(f, text="", foreground=DIM)
        self.status.pack(fill="x", pady=(6, 0))
        ttk.Label(f, foreground=DIM, wraplength=520, justify="left",
                  text=f"Gespeichert wird in {soundsearch.SOUND_DIR}. Rechte an Meme-Sounds aus Filmen, Spielen "
                       "und Serien sind meist ungeklärt – für privat ok, nicht für Streams/Videos.").pack(fill="x")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda ev: self.close())
        TileDialog._place_over(self, parent)
        self.after(100, self._poll)
        self.load("popular")
        self._work(soundsearch.fetch_categories, self._on_categories, "Lade Liste ...")
        e.focus_set()
        self.grab_set()  # der aufrufende Dialog hält die Eingaben fest – hier übernehmen (beim Schließen zurück)

    def _on_categories(self, ok, cats):
        if ok and cats:  # live gelesene Kategorien (neue erscheinen automatisch)
            self.cats = self.cats[:2] + [(n, "category", s) for n, s in cats]
            self.cat_box.configure(values=[c[0] for c in self.cats])

    def _on_cat(self):
        name = self.v_cat.get()
        _, kind, slug = next(c for c in self.cats if c[0] == name)
        self.load(kind, slug)

    # ---------------------------------------------------------- Hintergrund
    def _work(self, fn, done, text):
        self.busy += 1
        self.status.configure(text=text)

        def run():
            try:
                res = (True, fn())
            except Exception as ex:  # Netzwerk, Datei – Text für den Nutzer
                res = (False, str(ex))
            self.results.put((done, res))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                done, (ok, val) = self.results.get_nowait()
                self.busy -= 1
                done(ok, val)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ---------------------------------------------------------- Listen
    def search(self):
        q = self.v_query.get().strip()
        if q:
            self.load("search", q)

    def load(self, kind, query="", page=1):
        self.list_args = (kind, query, page)
        self._work(lambda: self.ss.fetch_list(kind, query, page), lambda ok, v: self._on_list(ok, v, page > 1),
                   "Lade Liste ...")

    def more(self):
        kind, query, page = self.list_args
        self.load(kind, query, page + 1)

    def _on_list(self, ok, items, append):
        if not ok:
            self.status.configure(text=f"Liste nicht ladbar: {items}")
            return
        if not append:
            self.tree.delete(*self.tree.get_children())
            self.items.clear()
            for name, mp3 in self.ss.DEFAULT_SOUNDS:  # Standard-Sounds fest oben
                iid = self.tree.insert("", "end", text="★ " + name,
                                       values=("✓" if self.ss.downloaded(name, mp3) else "",))
                self.items[iid] = (name, mp3)
        for name, mp3, _page in items:
            if any(mp3 == d for _, d in self.ss.DEFAULT_SOUNDS):
                continue
            iid = self.tree.insert("", "end", text=name, values=("✓" if self.ss.downloaded(name, mp3) else "",))
            self.items[iid] = (name, mp3)
        self.status.configure(text=f"{len(self.items)} Sounds" + ("" if items else " – keine weiteren"))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            self.status.configure(text="Bitte einen Sound in der Liste auswählen.")
            return None, None
        return sel[0], self.items[sel[0]]

    # ---------------------------------------------------------- Anhören / Laden
    def _fetch(self, mp3):
        if mp3 not in self.bytes:
            self.bytes[mp3] = self.ss.fetch_sound(mp3)
        return self.bytes[mp3]

    def listen(self):
        iid, item = self._selected()
        if not item:
            return
        name, mp3 = item

        def run():
            import soundplayer
            soundplayer.preview(self._fetch(mp3))
        self._work(run, lambda ok, v: self.status.configure(text=f"▶ {name}" if ok else f"Abspielen fehlgeschlagen: {v}"),
                   f"Lade „{name}“ ...")

    def stop(self):
        import soundplayer
        soundplayer.stop(soundplayer.PREVIEW)
        self.status.configure(text="")

    def download(self, pick=False):
        iid, item = self._selected()
        if not item:
            return
        name, mp3 = item

        def done(ok, path):
            if not ok:
                self.status.configure(text=f"Herunterladen fehlgeschlagen: {path}")
                return
            self.tree.set(iid, "ok", "✓")
            self.status.configure(text=f"Gespeichert: {path}")
            if self.on_download:
                self.on_download(path, name)
            if pick and self.on_pick:
                self.on_pick(path, name)
                self.close()
        self._work(lambda: self.ss.save(name, mp3, self._fetch(mp3)), done, f"Lade „{name}“ herunter ...")

    def open_folder(self):
        os.makedirs(self.ss.SOUND_DIR, exist_ok=True)
        os.startfile(self.ss.SOUND_DIR)

    def close(self):
        self.stop()
        parent = self.master
        self.destroy()
        try:
            parent.grab_set()  # Eingaben wieder an den aufrufenden Dialog
        except tk.TclError:
            pass


class LinksDialog(tk.Toplevel):
    """Kleines Fenster mit anklickbaren Links (öffnen im Standard-Browser), nach Gruppen."""

    def __init__(self, parent, title, groups):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        row = 0
        for head, note, links in groups:
            ttk.Label(f, text=head, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w",
                                                                        pady=(8 if row else 0, 2))
            row += 1
            if note:
                ttk.Label(f, text=note, foreground=DIM, wraplength=560, justify="left").grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))
                row += 1
            for name, url, desc in links:
                a = tk.Label(f, text=name, fg="#1a6fd8", cursor="hand2", font=("Segoe UI", 9, "underline"))
                a.grid(row=row, column=0, sticky="nw", padx=(8, 12), pady=1)
                a.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
                tip(a, url)
                ttk.Label(f, text=desc, wraplength=420, justify="left").grid(row=row, column=1, sticky="w", pady=1)
                row += 1
        ttk.Button(f, text="Schließen", command=self.destroy).grid(row=row, column=1, sticky="e", pady=(12, 0))
        self.bind("<Escape>", lambda e: self.destroy())
        TileDialog._place_over(self, parent)


# ======================================================================= Automatik-Dialog
class AutoDialog(tk.Toplevel):
    """Programme, bei denen das Deck automatisch auf diese Seite springt."""

    def __init__(self, parent, page_no, apps, elsewhere=None):
        """elsewhere: {programm_klein: seitennummer} – schon auf einer anderen Seite eingetragen."""
        super().__init__(parent)
        self.title(f"Automatik – Seite {page_no}")
        elsewhere = elsewhere or {}

        def hint(a):
            p = elsewhere.get(a.lower())
            return f"{a}   (bisher Seite {p} – beim Anhaken hierher verschoben)" if p else a
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        current = [a for a in apps if a.strip()]
        running = [a for a in winctl.running_apps() if a.lower() not in FOREGROUND_IGNORE
                   and a.lower() not in ("pc-deck.exe", "python.exe", "pythonw.exe")]

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Kommt eines dieser Programme in den Vordergrund, zeigt das Deck Seite {page_no}.\n"
                          "Verlässt du es, springt das Deck auf die zuletzt selbst gewählte Seite zurück.",
                  justify="left").pack(anchor="w")
        ttk.Label(f, text="Gerade laufende Programme:").pack(anchor="w", pady=(10, 2))
        box = ttk.Frame(f)
        box.pack(anchor="w", padx=(8, 0))
        self.checks = []
        low = {a.lower() for a in current}
        for a in running:
            v = tk.BooleanVar(value=a.lower() in low)
            ttk.Checkbutton(box, text=hint(a), variable=v).pack(anchor="w")
            self.checks.append((a, v))
        if not running:
            ttk.Label(box, text="(keine gefunden)").pack(anchor="w")
        run_low = {a.lower() for a in running}
        others = [a for a in current if a.lower() not in run_low]
        ttk.Label(f, text="Weitere Programme (laufen gerade nicht), durch Komma getrennt, z. B. LightBurn.exe:").pack(
            anchor="w", pady=(10, 2))
        if elsewhere:
            ttk.Label(f, foreground=DIM, text="Ein Programm kann nur auf einer Seite stehen – "
                      "hier eingetragen, wird es auf der anderen Seite entfernt.").pack(anchor="w")
        self.v_other = tk.StringVar(value=", ".join(others))
        ttk.Entry(f, textvariable=self.v_other, width=60).pack(anchor="w", fill="x")

        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(14, 0))
        ttk.Button(b, text="Abbrechen", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(b, text="OK", command=self.ok).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        TileDialog._place_over(self, parent)
        self.grab_set()
        self.wait_window()

    def ok(self):
        apps = [a for a, v in self.checks if v.get()]
        for a in self.v_other.get().replace(";", ",").split(","):
            a = a.strip().strip('"')
            if a and not a.lower().endswith(".exe"):
                a += ".exe"
            if a and a.lower() not in {x.lower() for x in apps}:
                apps.append(a)
        self.result = apps
        self.destroy()


# ======================================================================= Design-Dialog
class ThemeDialog(tk.Toplevel):
    """Hintergrund und Farben eines Decks: Bild, Kachelfarbe, Stil, Helligkeit, Deckkraft – mit Vorschau."""

    def __init__(self, parent, settings, icons=()):
        super().__init__(parent)
        self.title("Eigenes Design")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.icons = list(icons)[:8]
        t = dict(settings)
        self.v_on = tk.BooleanVar(value=bool(t.get("enabled")))
        self.v_image = tk.StringVar(value=t.get("image", ""))
        self.v_auto = tk.BooleanVar(value=not str(t.get("tile", "auto")).startswith("#"))
        self.tile_color = t.get("tile") if str(t.get("tile", "")).startswith("#") else "#0a1b4b"
        self.v_sat = tk.IntVar(value=int(t.get("tile_sat", 75)))
        self.v_style = tk.StringVar(value=dict(theme.STYLES).get(t.get("style", "glass"), "Glas (Verlauf + Glanz)"))
        self.v_bri = tk.IntVar(value=int(t.get("bg_brightness", 30)))
        self.v_alpha = tk.IntVar(value=int(t.get("tile_alpha", 80)))

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky="nw")
        ttk.Checkbutton(left, text="Design verwenden (Startbild, Hintergrund, Kachelfarben)", variable=self.v_on).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(left, text="Bild:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(left, textvariable=self.v_image, width=44).grid(row=2, column=0, columnspan=2, sticky="we")
        ttk.Button(left, text="Bild wählen ...", command=self.pick_image).grid(row=2, column=2, padx=(4, 0))

        ttk.Checkbutton(left, text="Kachelfarbe automatisch aus dem Bild", variable=self.v_auto).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row = ttk.Frame(left)
        row.grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Label(row, text="Farbstärke:").pack(side="left")
        ttk.Scale(row, from_=10, to=100, variable=self.v_sat, length=150).pack(side="left", padx=4)
        self.color_btn = tk.Button(row, text="Eigene Farbe ...", command=self.pick_color, relief="groove")
        self.color_btn.pack(side="left", padx=(10, 0))

        ttk.Label(left, text="Kachel-Stil:").grid(row=5, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(left, state="readonly", textvariable=self.v_style, width=26,
                     values=[n for _, n in theme.STYLES]).grid(row=5, column=1, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(left, text="Hintergrund-Helligkeit:").grid(row=6, column=0, sticky="w", pady=(10, 0))
        ttk.Scale(left, from_=10, to=70, variable=self.v_bri, length=180).grid(row=6, column=1, columnspan=2,
                                                                              sticky="w", pady=(10, 0))
        ttk.Label(left, text="Kachel-Deckkraft:").grid(row=7, column=0, sticky="w", pady=(6, 0))
        ttk.Scale(left, from_=40, to=100, variable=self.v_alpha, length=180).grid(row=7, column=1, columnspan=2,
                                                                                 sticky="w", pady=(6, 0))

        self.preview = tk.Label(f, bg="#000000")
        self.preview.grid(row=0, column=1, padx=(16, 0), sticky="n")
        b = ttk.Frame(f)
        b.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(b, text="Abbrechen", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(b, text="OK", command=self.ok).pack(side="left")

        self._after = None
        for v in (self.v_on, self.v_image, self.v_auto, self.v_sat, self.v_style, self.v_bri, self.v_alpha):
            v.trace_add("write", lambda *a: self._schedule())
        self._update()
        TileDialog._place_over(self, parent)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def _settings(self):
        style = next((k for k, n in theme.STYLES if n == self.v_style.get()), "glass")
        return {"enabled": bool(self.v_on.get()), "image": self.v_image.get().strip().strip('"'),
                "tile": "auto" if self.v_auto.get() else self.tile_color, "tile_sat": int(self.v_sat.get()),
                "style": style, "bg_brightness": int(self.v_bri.get()), "tile_alpha": int(self.v_alpha.get())}

    def _schedule(self):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(250, self._update)

    def _update(self):
        self.color_btn.configure(bg=self.tile_color, fg="#ffffff",
                                 state="disabled" if self.v_auto.get() else "normal")
        img = None
        try:
            img = theme.preview(self._settings(), self.icons) if self.v_on.get() else None
        except Exception:
            img = None
        if img is None:
            self._photo = None
            self.preview.configure(image="", text="(kein Design)", fg="#ffffff", width=40, height=10)
            return
        self._photo = ImageTk.PhotoImage(img.resize((360, 240)))
        self.preview.configure(image=self._photo, text="", width=360, height=240)

    def pick_image(self):
        start = os.path.dirname(self.v_image.get()) if os.path.isdir(os.path.dirname(self.v_image.get())) \
            else theme.WALLPAPER_DIR
        p = filedialog.askopenfilename(parent=self, title="Hintergrundbild wählen", initialdir=start,
                                       filetypes=[("Bilder", "*.jpg *.jpeg *.png *.bmp"), ("Alle", "*.*")])
        if p:
            self.v_image.set(os.path.normpath(p))

    def pick_color(self):
        from tkinter import colorchooser
        c = colorchooser.askcolor(color=self.tile_color, parent=self, title="Kachelfarbe")[1]
        if c:
            self.tile_color = c
            self._update()

    def ok(self):
        self.result = self._settings()
        self.destroy()


# ======================================================================= Info-Popups
class Tip:
    """Kurzer Erklärtext, der erscheint, wenn die Maus kurz über einem Element steht."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self.win = self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self.job = self.widget.after(self.delay, self._show)

    def _show(self):
        self.job = None
        if self.win or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.attributes("-topmost", True)
        tk.Label(self.win, text=self.text, justify="left", wraplength=420, bg="#ffffe1", fg="#000000",
                 relief="solid", borderwidth=1, padx=8, pady=5, font=("Segoe UI", 9)).pack()
        self.win.update_idletasks()
        # nicht über den rechten/unteren Bildschirmrand hinaus
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x = min(x, sw - self.win.winfo_width() - 8)
        if y + self.win.winfo_height() > sh - 40:
            y = self.widget.winfo_rooty() - self.win.winfo_height() - 6
        self.win.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None


def tip(widget, text):
    Tip(widget, text)
    return widget


TILE_TIP = ("Klick: bearbeiten  •  Rechtsklick: kopieren, einfügen, leeren\n"
            "Am Deck: kurz tippen = öffnen bzw. minimieren/zurückholen; "
            "lang (gelber Rahmen, 0,6 s) = höflich schließen; "
            "1,5 s halten (roter Rahmen) = Kachel löschen (mit Sicherheitsabfrage).")
APP_HELP = """Kacheln
  • Programme, Verknüpfungen oder Ordner aus dem Explorer auf eine Kachel ziehen –
    oder Kachel anklicken und einen Typ wählen (Uhr, Lautstärke, Medien, Tastenkürzel …).
  • „Speichern & ans Deck senden“ überträgt die Änderungen.

Am Deck
  • Kurz tippen: Programm öffnen bzw. minimieren / zurückholen.
  • Lang tippen (gelber Rahmen): Programm höflich schließen.
  • 1,5 s halten (roter Rahmen): Kachel löschen – mit Sicherheitsabfrage.
  • Unten < und >: Seiten blättern.
  • Grüner Rahmen: dieses Programm ist gerade im Vordergrund.

Automatik
  • Pro Seite Programme eintragen: kommt eines davon nach vorne, zeigt das Deck diese Seite.

Plus-Kachel
  • Das Programm im Vordergrund erscheint blass mit „+“ – antippen speichert es als Kachel.

Design
  • Oben „Design:“ – Standard, Rot/Magenta, Grün, Schwarz/Weiß (feste Designs) oder ein
    eigenes („Eigenes ...“). Das Design bleibt im Deck gespeichert, auch an einem anderen PC.

WLAN (optional)
  • „WLAN ...“: einmal per USB WLAN einrichten und koppeln. Am USB ist das WLAN aus.
    Ohne USB (Netzteil, Powerbank) fragt das Deck „WLAN einschalten?“ – bei „Ja“ findet die
    App das Deck im WLAN. USB hat immer Vorrang.
  • Ist der PC gesperrt (Win+L), lösen Deck und Drehknopf nichts aus („PC gesperrt“).

Drehknopf
  • Drehen, gedrückt drehen, kurz und lang drücken – Belegung unter „Drehknopf …“,
    als Standard und je Programm im Vordergrund.

Tipp: Bleibt die Maus kurz über einer Schaltfläche stehen, erscheint eine Erklärung."""


# ======================================================================= Drehknopf-Dialog
KNOB_HELP = """Der Drehknopf am Deck kennt vier Bewegungen:
  • Drehen
  • Gedrückt drehen (Knopf festhalten und drehen)
  • Kurz drücken
  • Lang drücken (ab 0,6 Sekunden)

Was dabei passiert, legst du hier fest:
  • „Standard“ gilt immer, wenn kein Programm-Preset passt.
  • Ein Programm-Preset gilt, solange dieses Programm im Vordergrund ist
    (z. B. Discord: drehen = Discord-Lautstärke, drücken = Mikrofon stumm;
    Edge: drehen = scrollen, drücken = Screenshot).

Hinweise:
  • „Lautstärke des Programms“ verstellt nur den Regler dieses Programms im
    Windows-Lautstärkemischer – andere Programme bleiben gleich laut.
    Das Programm muss dafür gerade Ton ausgeben bzw. im Mischer stehen.
  • Scrollen und Zoomen wirken auf das Fenster unter dem Mauszeiger.
  • Bei Lautstärke, Ausgabegerät und Seite zeigt das Deck kurz den neuen Wert an.
  • Änderungen gelten nach „Speichern & ans Deck senden“ sofort."""


class KnobDialog(tk.Toplevel):
    """Belegung des Drehknopfs: Standard und Presets pro Programm (Vordergrund)."""

    def __init__(self, parent, settings, deck_cfg):
        super().__init__(parent)
        self.title("Drehknopf")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.presets = {"": dict(settings["default"])}
        self.presets.update({a: dict(p) for a, p in settings["apps"].items()})
        self.deck_cfg = deck_cfg
        self.current = None
        try:
            self.devices = winaudio.devices(winaudio.RENDER)
        except OSError:
            self.devices = []
        self.tiles = self._tile_choices()
        self.rot_tiles = self._rot_tile_choices()

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, text="Belegung je nach Programm im Vordergrund:").pack(side="left")
        tip(ttk.Button(head, text="?", width=3, command=self._help), "Ausführliche Erklärung zum Drehknopf").pack(
            side="right")

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(8, 0))

        # links: Standard + Programme
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, width=26, height=14, exportselection=False, activestyle="none")
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        tip(self.listbox, "„Standard“ gilt für alle Programme ohne eigenes Preset.\n"
                          "Ein Programm-Preset gilt, solange das Programm im Vordergrund ist.")
        lb = ttk.Frame(left)
        lb.pack(fill="x", pady=(6, 0))
        tip(ttk.Button(lb, text="+ Programm ...", command=self._add_app),
            "Preset für ein Programm anlegen – zur Auswahl stehen die Programme deiner Kacheln "
            "und die gerade laufenden.").pack(side="left")
        self.del_btn = tip(ttk.Button(lb, text="Entfernen", command=self._del_app),
                           "Preset des markierten Programms löschen (danach gilt dort wieder „Standard“).")
        self.del_btn.pack(side="left", padx=(6, 0))

        # rechts: die vier Bewegungen + Zusatzfelder
        right = ttk.Frame(body, padding=(14, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)
        self.v = {}
        rot_labels = [t for _, t in knob.ROT_ACTIONS]
        press_labels = [t for _, t in knob.PRESS_ACTIONS]
        rows = (("rot", "Drehen:", rot_labels, "Was beim Drehen passiert (je Raste)."),
                ("rotp", "Gedrückt drehen:", rot_labels,
                 "Knopf gedrückt halten und drehen – eine zweite Belegung, z. B. Ausgabegerät wechseln.\n"
                 "Das Loslassen danach zählt nicht als Drücken."),
                ("press", "Kurz drücken:", press_labels, "Knopf kurz drücken und loslassen."),
                ("long", "Lang drücken:", press_labels, "Knopf mindestens 0,6 Sekunden drücken, dann loslassen."))
        for r, (key, text, values, help_text) in enumerate(rows):
            tip(ttk.Label(right, text=text), help_text).grid(row=r, column=0, sticky="w", pady=2)
            v = tk.StringVar()
            cb = ttk.Combobox(right, textvariable=v, values=values, state="readonly", width=40)
            cb.grid(row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e: self._update())
            tip(cb, help_text)
            self.v[key] = v

        extra = ttk.LabelFrame(right, text="Einstellungen dazu", padding=8)
        extra.grid(row=4, column=0, columnspan=2, sticky="we", pady=(10, 0))
        self.extra_rows = {}

        def row(name, label, widget_fn, help_text):
            r = len(self.extra_rows)
            lab = tip(ttk.Label(extra, text=label), help_text)
            lab.grid(row=r, column=0, sticky="nw", pady=2)
            w = widget_fn(extra)
            w.grid(row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            tip(w, help_text)
            self.extra_rows[name] = (lab, w)

        self.v["step"] = tk.IntVar()
        row("step", "Lautstärke je Raste (%):",
            lambda m: ttk.Spinbox(m, from_=1, to=10, width=4, textvariable=self.v["step"], state="readonly"),
            "Wie viel lauter/leiser je Raste (System- und Programm-Lautstärke).")
        self.v["app"] = tk.StringVar()
        row("app", "Programm für Lautstärke:", lambda m: ttk.Entry(m, textvariable=self.v["app"], width=30),
            "Leer = das Programm im Vordergrund. Oder fest ein Programm eintragen, z. B. Spotify.exe – "
            "dann regelt der Knopf immer dessen Lautstärke.")
        self.dev_vars = {}

        def dev_box(m):
            box = ttk.Frame(m)
            for d in self.devices:
                v = tk.BooleanVar()
                ttk.Checkbutton(box, text=d["name"], variable=v).pack(anchor="w")
                self.dev_vars[d["id"]] = v
            if not self.devices:
                ttk.Label(box, text="(keine Geräte gefunden)").pack(anchor="w")
            return box
        row("devices", "Ausgabegeräte:", dev_box,
            "Zwischen diesen Geräten wird gewechselt. Keins angehakt = alle Geräte reihum.")
        for key, label, help_text in (("keys_left", "Drehen links:", "Tastenkürzel je Raste nach links."),
                                      ("keys_right", "Drehen rechts:", "Tastenkürzel je Raste nach rechts."),
                                      ("keysp_left", "Gedrückt links:", "Tastenkürzel je Raste, gedrückt nach links."),
                                      ("keysp_right", "Gedrückt rechts:", "Tastenkürzel je Raste, gedrückt nach rechts."),
                                      ("keys_press", "Kurz drücken:", "Tastenkürzel beim kurzen Drücken."),
                                      ("keys_long", "Lang drücken:", "Tastenkürzel beim langen Drücken.")):
            self.v[key] = tk.StringVar()
            row(key, "Tasten " + label, lambda m, k=key: ttk.Entry(m, textvariable=self.v[k], width=30),
                help_text + "\nBeispiele: Strg+Plus, Bildab, Alt+Tab, Strg+K, Strg+C (Abfolge mit Komma).")
        self.v["tile_rot"] = tk.StringVar()
        row("tile_rot", "Kachel einstellen:",
            lambda m: ttk.Combobox(m, textvariable=self.v["tile_rot"], state="readonly", width=40,
                                   values=["(keine)"] + [t for _, t in self.rot_tiles]),
            "Diese Kachel wird per Drehen eingestellt, z. B. ein Bosch-Rollladen in 10-%-Schritten.\n"
            "Es stehen nur Kacheln zur Wahl, die sich drehen lassen.")
        tile_labels = ["(keine)"] + [t for _, t in self.tiles]
        for key, label in (("tile_press", "Kachel kurz:"), ("tile_long", "Kachel lang:")):
            self.v[key] = tk.StringVar()
            row(key, label, lambda m, k=key: ttk.Combobox(m, textvariable=self.v[k], values=tile_labels,
                                                         state="readonly", width=40),
                "Diese Kachel wird ausgelöst, als hättest du sie am Deck angetippt.")

        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(14, 0))
        ttk.Button(b, text="Abbrechen", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(b, text="OK", command=self.ok).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        self._fill_list("")
        TileDialog._place_over(self, parent)
        self.grab_set()
        self.wait_window()

    # ---------------------------------------------------------- Hilfen
    def _rot_tile_choices(self):
        """Kacheln, die sich per Drehknopf einstellen lassen (z. B. Bosch-Rollladen)."""
        out = []
        for p, page in enumerate(self.deck_cfg.get("pages", [])):
            for i, t in enumerate(page):
                if t and plugin_api.get(t).rotatable:
                    out.append((f"{p}:{i}", f"Seite {p + 1}, Platz {i + 1}: {tile_label(t)}"))
        return out

    def _tile_choices(self):
        out = []
        for p, page in enumerate(self.deck_cfg.get("pages", [])):
            for i, t in enumerate(page):
                if t:
                    out.append((f"{p}:{i}", f"Seite {p + 1}, Platz {i + 1}: {tile_label(t)}"))
        return out

    def _app_candidates(self):
        names = set()
        for page in self.deck_cfg.get("pages", []):
            for t in page:
                if t and t.get("type", "launch") == "launch" and t.get("path", "").lower().endswith((".exe", ".lnk")):
                    try:
                        exe = winctl.target_exe(t, t["path"])
                    except Exception:
                        exe = None
                    if exe:
                        names.add(os.path.basename(exe).lower())
        names.update(a.lower() for a in winctl.running_apps())
        names -= {a.lower() for a in FOREGROUND_IGNORE} | {"pc-deck.exe", "python.exe", "pythonw.exe"}
        names -= set(self.presets)
        return sorted(names)

    def _help(self):
        messagebox.showinfo("Drehknopf – so funktioniert's", KNOB_HELP, parent=self)

    @staticmethod
    def _label_of(actions, key):
        return next((t for k, t in actions if k == key), actions[-1][1])

    @staticmethod
    def _key_of(actions, label):
        return next((k for k, t in actions if t == label), "off")

    # ---------------------------------------------------------- Liste
    def _fill_list(self, select):
        self.listbox.delete(0, "end")
        self.keys = [""] + sorted(a for a in self.presets if a)
        for k in self.keys:
            self.listbox.insert("end", k if k else "Standard (alle anderen)")
        idx = self.keys.index(select) if select in self.keys else 0
        self.listbox.selection_set(idx)
        self.current = None
        self._on_select()

    def _on_select(self, _e=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        self._store()
        self.current = self.keys[sel[0]]
        p = knob.preset(self.presets[self.current])
        self.v["rot"].set(self._label_of(knob.ROT_ACTIONS, p["rot"]))
        self.v["rotp"].set(self._label_of(knob.ROT_ACTIONS, p["rotp"]))
        self.v["press"].set(self._label_of(knob.PRESS_ACTIONS, p["press"]))
        self.v["long"].set(self._label_of(knob.PRESS_ACTIONS, p["long"]))
        self.v["step"].set(int(p["step"]))
        self.v["app"].set(p["app"])
        for k in ("keys_left", "keys_right", "keysp_left", "keysp_right", "keys_press", "keys_long"):
            self.v[k].set(p[k])
        names = dict(self.tiles)
        for k in ("tile_press", "tile_long"):
            self.v[k].set(names.get(p[k], "(keine)"))
        self.v["tile_rot"].set(dict(self.rot_tiles).get(p.get("tile_rot", ""), "(keine)"))
        for dev_id, v in self.dev_vars.items():
            v.set(dev_id in (p.get("devices") or []))
        self.del_btn.configure(state="normal" if self.current else "disabled")
        self._update()

    def _store(self):
        """Felder ins Preset des gerade markierten Eintrags übernehmen."""
        if self.current is None:
            return
        p = knob.preset(self.presets[self.current])
        p.update(rot=self._key_of(knob.ROT_ACTIONS, self.v["rot"].get()),
                 rotp=self._key_of(knob.ROT_ACTIONS, self.v["rotp"].get()),
                 press=self._key_of(knob.PRESS_ACTIONS, self.v["press"].get()),
                 long=self._key_of(knob.PRESS_ACTIONS, self.v["long"].get()),
                 step=int(self.v["step"].get()), app=self.v["app"].get().strip())
        for k in ("keys_left", "keys_right", "keysp_left", "keysp_right", "keys_press", "keys_long"):
            p[k] = self.v[k].get().strip()
        by_label = {t: ref for ref, t in self.tiles}
        for k in ("tile_press", "tile_long"):
            p[k] = by_label.get(self.v[k].get(), "")
        p["tile_rot"] = {t: ref for ref, t in self.rot_tiles}.get(self.v["tile_rot"].get(), "")
        p["devices"] = [i for i, v in self.dev_vars.items() if v.get()]
        p["devices_names"] = {d["id"]: d["name"] for d in self.devices if d["id"] in p["devices"]}
        self.presets[self.current] = p

    def _update(self):
        """Nur die Zusatzfelder freigeben, die zur gewählten Belegung gehören."""
        rot = self._key_of(knob.ROT_ACTIONS, self.v["rot"].get())
        rotp = self._key_of(knob.ROT_ACTIONS, self.v["rotp"].get())
        press = self._key_of(knob.PRESS_ACTIONS, self.v["press"].get())
        long = self._key_of(knob.PRESS_ACTIONS, self.v["long"].get())
        need = {"step": {"volume", "app_volume"} & {rot, rotp}, "app": "app_volume" in (rot, rotp),
                "devices": "output" in (rot, rotp, press, long),
                "keys_left": rot == "keys", "keys_right": rot == "keys",
                "keysp_left": rotp == "keys", "keysp_right": rotp == "keys",
                "keys_press": press == "keys", "keys_long": long == "keys",
                "tile_press": press == "tile", "tile_long": long == "tile", "tile_rot": "tile" in (rot, rotp)}
        for name, (lab, w) in self.extra_rows.items():
            on = bool(need.get(name))
            lab.configure(foreground="" if on else DIM)
            widgets = w.winfo_children() if name == "devices" else [w]
            for x in widgets:
                try:
                    x.configure(state=("readonly" if isinstance(x, (ttk.Combobox, ttk.Spinbox)) else "normal")
                                if on else "disabled")
                except tk.TclError:
                    pass

    # ---------------------------------------------------------- Programme
    def _add_app(self):
        d = tk.Toplevel(self)
        d.title("Preset für Programm")
        d.transient(self)
        d.resizable(False, False)
        fr = ttk.Frame(d, padding=12)
        fr.pack()
        ttk.Label(fr, text="Programm auswählen oder Namen eintippen (z. B. Discord.exe):").pack(anchor="w")
        v = tk.StringVar()
        cb = ttk.Combobox(fr, textvariable=v, values=self._app_candidates(), width=40)
        cb.pack(anchor="w", pady=(6, 0))
        cb.focus_set()
        res = {}

        def ok(_e=None):
            name = v.get().strip().strip('"').lower()
            if name and not name.endswith(".exe"):
                name += ".exe"
            res["name"] = name
            d.destroy()
        bb = ttk.Frame(fr)
        bb.pack(anchor="e", pady=(12, 0))
        ttk.Button(bb, text="Abbrechen", command=d.destroy).pack(side="left", padx=4)
        ttk.Button(bb, text="OK", command=ok).pack(side="left")
        d.bind("<Return>", ok)
        d.bind("<Escape>", lambda e: d.destroy())
        TileDialog._place_over(d, self)
        d.grab_set()
        d.wait_window()
        name = res.get("name")
        if not name:
            return
        self._store()
        if name not in self.presets:
            self.presets[name] = knob.preset(self.presets[""])  # Start: wie Standard
        self._fill_list(name)

    def _del_app(self):
        if not self.current:
            return
        self.presets.pop(self.current, None)
        self.current = None
        self._fill_list("")

    def ok(self):
        self._store()
        for p in self.presets.values():
            for k in ("keys_left", "keys_right", "keysp_left", "keysp_right", "keys_press", "keys_long"):
                try:
                    winkeys.parse_sequence(p.get(k, ""))
                except Exception as e:
                    messagebox.showerror("Drehknopf", f"Tastenkürzel nicht erkannt: {p[k]}\n\n{e}", parent=self)
                    return
        self.result = {"default": self.presets[""], "apps": {a: p for a, p in self.presets.items() if a}}
        self.destroy()


# ======================================================================= WLAN-Dialog
class WifiDialog(tk.Toplevel):
    """WLAN des Decks einrichten und koppeln (nur per USB), Status anzeigen, ein-/ausschalten."""

    def __init__(self, parent, manager, config_store, deck_id):
        super().__init__(parent)
        self.title("WLAN des Decks")
        self.transient(parent)
        self.resizable(False, False)
        self.manager, self.store, self.deck_id = manager, config_store, deck_id
        self.results = queue.Queue()
        self.wait_until = 0  # > 0: nach dem Koppeln auf die WLAN-Verbindung warten
        self.nets = {}
        link = manager.link(deck_id)
        usb = bool(link) and not link.via_wlan

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        via = "nicht verbunden" if not link else (f"über {link.port}" if link.via_wlan else f"über USB ({link.port})")
        ttk.Label(f, text=f"Deck: {via}").grid(row=0, column=0, columnspan=3, sticky="w")
        self.status = ttk.Label(f, text="WLAN-Status wird abgefragt ..." if link else "")
        self.status.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))

        tip(ttk.Label(f, text="Netz:"), "Das WLAN, mit dem sich das Deck verbinden soll.").grid(row=2, column=0,
                                                                                            sticky="w")
        self.v_net = tk.StringVar()
        self.net_box = ttk.Combobox(f, textvariable=self.v_net, width=36)
        self.net_box.grid(row=2, column=1, sticky="w", padx=6)
        tip(self.net_box, "Auswählen oder Namen eintippen (auch für versteckte Netze). Das Deck kann nur "
                          "2,4-GHz-Netze nutzen.")
        self.scan_btn = tip(ttk.Button(f, text="Netze suchen", command=self.scan),
                            "Das Deck sucht die WLANs in seiner Nähe (dauert ein paar Sekunden).")
        self.scan_btn.grid(row=2, column=2, sticky="w")

        tip(ttk.Label(f, text="Passwort:"), "WLAN-Passwort – tippst du selbst ein.").grid(row=3, column=0,
                                                                                       sticky="w", pady=(6, 0))
        self.v_pw = tk.StringVar()
        self.pw_entry = ttk.Entry(f, textvariable=self.v_pw, width=38, show="•")
        self.pw_entry.grid(row=3, column=1, sticky="w", padx=6, pady=(6, 0))
        tip(self.pw_entry, "Das Passwort wird nur an das Deck geschickt und dort gespeichert – "
                           "die App speichert es nicht.")
        self.v_show = tk.BooleanVar()
        ttk.Checkbutton(f, text="anzeigen", variable=self.v_show,
                        command=lambda: self.pw_entry.configure(show="" if self.v_show.get() else "•")).grid(
            row=3, column=2, sticky="w", pady=(6, 0))

        ttk.Label(f, foreground=DIM, justify="left", wraplength=470,
                  text="Verbinden & koppeln: Das Deck speichert WLAN und Passwort und bekommt einen geheimen "
                       "Schlüssel von dieser App. Am USB ist das WLAN aus (Strom, Wärme) – nach dem Einrichten "
                       "bleibt es noch 30 s an. Ohne USB (Netzteil, Powerbank) fragt das Deck „WLAN "
                       "einschalten?“; bei „Ja“ findet die App es im WLAN automatisch. Steckt das Deck an einem "
                       "anderen PC mit PC-Deck, werden WLAN und Kopplung im Deck gelöscht.").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        if link and not usb:
            ttk.Label(f, foreground="#c05000", text="Einrichten, Koppeln und Ein-/Ausschalten geht nur, "
                      "wenn das Deck per USB angesteckt ist.").grid(row=5, column=0, columnspan=3, sticky="w",
                                                                   pady=(6, 0))

        b = ttk.Frame(f)
        b.grid(row=6, column=0, columnspan=3, sticky="e", pady=(14, 0))
        self.connect_btn = tip(ttk.Button(b, text="Verbinden & koppeln", command=self.connect),
                               "WLAN-Daten und Kopplungsschlüssel ans Deck schicken und WLAN einschalten.")
        self.connect_btn.pack(side="left", padx=4)
        self.off_btn = tip(ttk.Button(b, text="WLAN aus", command=lambda: self.enable(False)),
                           "WLAN am Deck ausschalten (Einstellungen bleiben gespeichert).")
        self.off_btn.pack(side="left", padx=4)
        self.on_btn = tip(ttk.Button(b, text="WLAN ein", command=lambda: self.enable(True)),
                          "WLAN am Deck wieder einschalten (mit den gespeicherten Einstellungen).")
        self.on_btn.pack(side="left", padx=4)
        ttk.Button(b, text="Schließen", command=self.destroy).pack(side="left", padx=(12, 0))
        for w in (self.connect_btn, self.off_btn, self.on_btn):
            w.configure(state="normal" if usb else "disabled")
        if not link:
            self.scan_btn.configure(state="disabled")
        self.bind("<Escape>", lambda e: self.destroy())
        TileDialog._place_over(self, parent)
        self.after(100, self._poll)
        if link:
            self.refresh()
        self.grab_set()

    # ---------------------------------------------------------- Befehle im Hintergrund
    def _send(self, text, timeout, done):
        link = self.manager.link(self.deck_id)
        if not link:
            self.status.configure(text="Deck nicht verbunden.")
            return

        def work():
            self.results.put((done, link.request(text, timeout)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                done, res = self.results.get_nowait()
                done(*res)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def refresh(self):
        self._send("WIFI STATUS", 5, self._on_status)

    def _on_status(self, ok, lines):
        if not self.winfo_exists():
            return
        if not ok:
            self.status.configure(text=f"Status nicht lesbar: {lines}  (Firmware ab Version 12 nötig)")
            return
        parts = next((l.split() for l in lines if l.startswith("WIFI ")), [])
        if len(parts) < 5:
            return
        en, conn, ip, ssid = parts[1] == "1", parts[2] == "1", parts[3], parts[4]
        active = parts[5] == "1" if len(parts) >= 6 else True  # ab FW 13: Funk an/aus
        name = bytes.fromhex(ssid).decode("utf-8", "replace") if ssid != "-" else ""
        if name and not self.v_net.get():
            self.v_net.set(name)
        paired = "   (gekoppelt)" if self.store.net_key(self.deck_id) else "   (nicht gekoppelt)"
        if self.wait_until:  # nach "Verbinden & koppeln": deutlich Erfolg oder Fehlschlag melden
            left = self.wait_until - time.time()
            if conn:
                self.wait_until = 0
                self.status.configure(text=f"✔ Verbindung erfolgreich – {name}, IP {ip}{paired}", foreground="#1a7f37")
            elif left <= 0:
                self.wait_until = 0
                self.status.configure(text=f"✖ Verbindung fehlgeschlagen – Passwort oder WLAN-Name prüfen "
                                           f"(nur 2,4 GHz){paired}", foreground="#c0392b")
            else:
                self.status.configure(text=f"Verbinde mit „{name}“ ... {int(left)} s", foreground="")
                self.after(1000, self.refresh)
            return
        self.status.configure(foreground="")
        if not en:
            txt = "WLAN am Deck: aus" + (f" (eingerichtet: {name})" if name else " (noch nicht eingerichtet)")
        elif not active:
            txt = f"WLAN am Deck: eingerichtet ({name}) – im USB-Betrieb aus"
        elif conn:
            txt = f"WLAN am Deck: verbunden mit {name}, IP {ip}"
        else:
            txt = f"WLAN am Deck: ein, verbindet sich mit {name} ..."
        self.status.configure(text=txt + paired)

    def scan(self):
        self.status.configure(text="Deck sucht WLANs ...")
        self.scan_btn.configure(state="disabled")
        self._send("WIFI SCAN", 15, self._on_scan)

    def _on_scan(self, ok, lines):
        if not self.winfo_exists():
            return
        self.scan_btn.configure(state="normal")
        if not ok:
            self.status.configure(text=f"Suche fehlgeschlagen: {lines}")
            return
        best = {}
        for l in lines:
            p = l.split()
            if len(p) == 4 and p[0] == "NET":
                name = bytes.fromhex(p[3]).decode("utf-8", "replace")
                if name not in best or int(p[1]) > best[name][0]:
                    best[name] = (int(p[1]), p[2] == "1")
        self.nets = {}
        for name, (rssi, open_) in sorted(best.items(), key=lambda kv: -kv[1][0]):
            label = f"{name}   ({rssi} dBm{', offen' if open_ else ''})"
            self.nets[label] = name
        self.net_box["values"] = list(self.nets)
        if self.nets and not self.v_net.get():
            self.v_net.set(next(iter(self.nets)))
        self.status.configure(text=f"{len(self.nets)} WLAN(s) gefunden.")

    def connect(self):
        ssid = self.nets.get(self.v_net.get(), self.v_net.get()).strip()
        pw = self.v_pw.get()
        if not ssid:
            messagebox.showinfo("WLAN", "Bitte ein WLAN auswählen oder eintippen.", parent=self)
            return
        if len(ssid.encode("utf-8")) > 32 or len(pw.encode("utf-8")) > 63:
            messagebox.showerror("WLAN", "Name (max. 32) oder Passwort (max. 63 Zeichen) zu lang.", parent=self)
            return
        key = self.store.net_key(self.deck_id, create=True)
        cmd = f"WIFI SET {ssid.encode('utf-8').hex()} {pw.encode('utf-8').hex() or '-'} {key}"
        self.v_pw.set("")  # Passwort nicht länger als nötig im Fenster halten
        self.status.configure(text="Sende WLAN-Daten ans Deck ...")
        self._send(cmd, 8, self._on_set)

    def _on_set(self, ok, lines):
        if not self.winfo_exists():
            return
        if not ok:
            self.status.configure(text=f"Fehlgeschlagen: {lines}")
            return
        self.wait_until = time.time() + 20  # bis zu 20 s auf die Verbindung warten
        self.status.configure(text="Gespeichert – Deck verbindet sich mit dem WLAN ...", foreground="")
        self.after(1500, self.refresh)

    def enable(self, on):
        self._send(f"WIFI EN {1 if on else 0}", 5, lambda ok, lines: self.after(1500 if ok else 0, self.refresh))


# ======================================================================= Smart-Home-Dialog
class BoschDialog(tk.Toplevel):
    """Bosch Smart Home Controller suchen und koppeln; Geräte für die SmartHome-Seite dieses Decks anhaken."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Bosch Smart Home")
        self.transient(parent)
        self.resizable(False, False)
        self.results = queue.Queue()
        self.busy = False
        self.win = parent  # Hauptfenster: Deck, Konfiguration
        self.checks = {}   # Geräte-ID -> (BooleanVar, Gerät)
        self.order = [d["id"] for d in (parent.cfg.get("smarthome") or {}).get("devices") or []]

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        self.status = ttk.Label(f, text="", wraplength=500, justify="left")
        self.status.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        tip(ttk.Label(f, text="Controller:"), "IP-Adresse des Bosch Smart Home Controllers im Heimnetz.").grid(
            row=1, column=0, sticky="w")
        self.v_host = tk.StringVar(value=boschshc.settings().get("host", ""))
        ttk.Entry(f, textvariable=self.v_host, width=20).grid(row=1, column=1, sticky="w", padx=6)
        self.search_btn = tip(ttk.Button(f, text="Suchen", command=self.search),
                              "Sucht den Controller im eigenen Netz (dauert ein paar Sekunden).")
        self.search_btn.grid(row=1, column=2, sticky="w")

        tip(ttk.Label(f, text="Systempasswort:"), "Das Passwort des Controllers aus der Einrichtung – "
                                                  "tippst du selbst ein.").grid(row=2, column=0, sticky="w",
                                                                                pady=(6, 0))
        self.v_pw = tk.StringVar()
        self.pw_entry = ttk.Entry(f, textvariable=self.v_pw, width=30, show="•")
        self.pw_entry.grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 0))
        tip(self.pw_entry, "Wird nur zum Koppeln an den Controller geschickt und nicht gespeichert.")

        ttk.Label(f, foreground=DIM, justify="left", wraplength=500,
                  text="Koppeln: 1. Controller suchen (oder IP eintragen), 2. Systempasswort eintippen, "
                       "3. die Taste am Controller drücken, bis die LEDs blinken, 4. gleich danach "
                       "„Koppeln“. Die App meldet sich dann mit einem eigenen Zertifikat an (im Controller als "
                       "„OSS PC-Deck“). Das Passwort wird nicht gespeichert.\n"
                       "Angehakte Geräte erscheinen auf der Seite „SmartHome“ am Ende dieses Decks. "
                       "Rollladen: kurz tippen = Drehknopf übernimmt (10 % je Raste, drücken = Stopp), "
                       "lang = auf/zu. Licht dimmbar (Hue): kurz = Drehknopf dimmt (drücken = an/aus), "
                       "lang = an/aus. Licht: tippen = an/aus.").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self.dev_frame = ttk.LabelFrame(f, text=f"Auf der SmartHome-Seite von „{parent.cfg.get('name', '')}“ zeigen",
                                        padding=8)
        self.dev_frame.grid(row=4, column=0, columnspan=3, sticky="we", pady=(10, 0))

        b = ttk.Frame(f)
        b.grid(row=5, column=0, columnspan=3, sticky="e", pady=(14, 0))
        self.pair_btn = tip(ttk.Button(b, text="Koppeln", command=self.pair),
                            "Erst die Taste am Controller drücken, bis die LEDs blinken.")
        self.pair_btn.pack(side="left", padx=4)
        self.list_btn = tip(ttk.Button(b, text="Geräte neu laden", command=self.list_devices),
                            "Geräteliste vom Controller neu abfragen.")
        self.list_btn.pack(side="left", padx=4)
        self.unpair_btn = tip(ttk.Button(b, text="Entkoppeln", command=self.unpair),
                              "Kopplung in PC-Deck vergessen. Den Eintrag „OSS PC-Deck“ im Controller "
                              "kannst du in der Bosch-App löschen.")
        self.unpair_btn.pack(side="left", padx=4)
        ttk.Button(b, text="Schließen", command=self.close).pack(side="left", padx=(12, 0))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        TileDialog._place_over(self, parent)
        self._refresh()
        self.after(100, self._poll)
        if boschshc.paired():
            self.list_devices()
        elif not self.v_host.get():
            self.search()
        self.grab_set()

    def _refresh(self):
        s = boschshc.settings()
        self.status.configure(text=f"Gekoppelt mit dem Controller {s['host']}." if boschshc.paired()
                              else "Nicht gekoppelt.")
        for w in (self.list_btn, self.unpair_btn):
            w.configure(state="normal" if boschshc.paired() and not self.busy else "disabled")
        for w in (self.pair_btn, self.search_btn):
            w.configure(state="disabled" if self.busy else "normal")

    def _work(self, fn, done):
        self.busy = True
        self._refresh()

        def run():
            try:
                res = (True, fn())
            except Exception as e:  # Netzwerk, Zertifikat, Controller – Text für den Nutzer
                res = (False, str(e))
            self.results.put((done, res))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                done, (ok, val) = self.results.get_nowait()
                self.busy = False
                done(ok, val)
                self._refresh()
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def search(self):
        self.status.configure(text="Suche den Controller im Netz ...")
        self._work(boschshc.discover, self._on_search)

    def _on_search(self, ok, found):
        if ok and found:
            self.v_host.set(found[0][0])
            gen = found[0][1].get("shcGeneration", "").replace("SHC_", "Controller ")
            self.after(10, lambda: self.status.configure(text=f"Gefunden: {gen} unter {found[0][0]}."))
        else:
            self.after(10, lambda: self.status.configure(
                text="Kein Controller gefunden – IP-Adresse von Hand eintragen." if ok else f"Suche: {found}"))

    def pair(self):
        host, pw = self.v_host.get().strip(), self.v_pw.get()
        if not host or not pw:
            messagebox.showinfo("PC-Deck", "Bitte Controller-Adresse und Systempasswort eintragen.", parent=self)
            return
        self.status.configure(text="Kopple ... (Zertifikat erzeugen, beim Controller anmelden)")
        self._work(lambda: boschshc.pair(host, pw), self._on_pair)

    def _on_pair(self, ok, val):
        self.v_pw.set("")
        if ok:
            self.list_devices()
        else:
            self.after(10, lambda: self.status.configure(text=f"Koppeln fehlgeschlagen: {val}"))

    def list_devices(self):
        self._work(lambda: boschshc.devices(max_age=0), self._on_devices)

    def _on_devices(self, ok, devs):
        for w in self.dev_frame.winfo_children():
            w.destroy()
        if not ok:
            self.after(10, lambda: self.status.configure(text=f"Controller nicht erreichbar: {devs}"))
            return
        keep = {i: v.get() for i, (v, _) in self.checks.items()}
        chosen = set(self.order)
        self.checks = {}
        names = {"shutter": "Rollladen", "dimmer": "Licht, dimmbar", "light": "Licht"}
        usable = sorted((d for d in devs if d["kind"] in names),
                        key=lambda d: (d["room"].lower(), d["kind"], d["name"]))
        for n, d in enumerate(usable):
            v = tk.BooleanVar(value=keep.get(d["id"], d["id"] in chosen))
            text = f"{names[d['kind']]}: {d['room'] + ' – ' if d['room'] else ''}{d['name']}"
            ttk.Checkbutton(self.dev_frame, text=text, variable=v).grid(row=n // 2, column=n % 2, sticky="w",
                                                                          padx=(0, 16))
            self.checks[d["id"]] = (v, d)
        if not usable:
            ttk.Label(self.dev_frame, text="(keine Rollläden oder Lichter gefunden)").grid(row=0, column=0)

    def _save_selection(self):
        """Angehakte Geräte ins Deck übernehmen (Reihenfolge: bisherige zuerst, neue nach der Liste)."""
        if not self.checks:
            return
        on = [i for i, (v, _) in self.checks.items() if v.get()]
        ids = [i for i in self.order if i in on] + [i for i in on if i not in self.order]
        devs = [{"id": i, "kind": self.checks[i][1]["kind"], "name": self.checks[i][1]["name"],
                 "room": self.checks[i][1]["room"]} for i in ids]
        old = (self.win.cfg.get("smarthome") or {}).get("devices") or []
        if devs == old:
            return
        self.win.cfg["smarthome"] = {"devices": devs}
        stored = self.win.config_store.deck_copy(self.win.deck_id)
        if stored is not None:
            stored["smarthome"] = {"devices": devs}
            self.win.config_store.set_deck(self.win.deck_id, stored)
        link = self.win.manager.link(self.win.deck_id)
        if link:
            link.request_sync()

    def unpair(self):
        if messagebox.askyesno("PC-Deck", "Kopplung mit dem Controller in PC-Deck vergessen?\n"
                                          "Bosch-Kacheln zeigen dann nichts mehr an.", parent=self):
            boschshc.unpair()
            for w in self.dev_frame.winfo_children():
                w.destroy()
            self.checks = {}
            self._refresh()
            link = self.win.manager.link(self.win.deck_id)
            if link:
                link.request_sync()  # SmartHome-Seite verschwindet

    def close(self):
        self.v_pw.set("")
        self._save_selection()
        self.destroy()


# ======================================================================= Soundboard-Dialog
class SoundboardDialog(tk.Toplevel):
    """Sounds für die Seite „Soundboard“ dieses Decks anhaken (Sound-Ordner), gemeinsame Einstellungen.
    Neu heruntergeladene Sounds werden sofort angehakt; Jack Black ist von Anfang an dabei."""

    CHECK, EMPTY = "☑", "☐"

    def __init__(self, parent):
        import soundsearch
        from plugins import sound as sound_plugin
        super().__init__(parent)
        self.ss, self.sp = soundsearch, sound_plugin
        self.win = parent
        self.title(f"Soundboard – {parent.cfg.get('name', '')}")
        self.transient(parent)
        self.resizable(False, False)
        sb = dict(parent.cfg.get("soundboard") or {})
        if "sounds" not in sb:  # erstes Öffnen: Standard-Sounds angehakt
            sb["sounds"] = [soundsearch.target_path(n, u) for n, u in soundsearch.DEFAULT_SOUNDS]
        self.checked = list(sb["sounds"])
        self.colors = dict(sb.get("colors") or {})  # pfad -> Farbname (fehlt = automatisch)
        self._dots = {}

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, foreground=DIM, wraplength=520, justify="left",
                  text="Angehakte Sounds erscheinen auf der Seite „Soundboard“ am Ende dieses Decks (vorne die "
                       "Kachel „Alle stoppen“). Antippen spielt, nochmal antippen stoppt. Klick auf einen Eintrag "
                       "hakt ihn an oder ab, Rechtsklick wählt die Farbe des Knopfs (sonst automatisch der Reihe "
                       "nach).").pack(fill="x")
        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(mid, columns=("file",), show="tree", height=12, selectmode="none")
        self.tree.column("#0", width=500)
        sb_ = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb_.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb_.pack(side="left", fill="y")
        self.tree.bind("<Button-1>", self._toggle)
        self.tree.bind("<Button-3>", self._color_menu)

        bt = ttk.Frame(f)
        bt.pack(fill="x", pady=(6, 0))
        ttk.Button(bt, text="Sounds suchen ...", command=self.search).pack(side="left")
        ttk.Button(bt, text="Sound-Ordner öffnen", command=self.open_folder).pack(side="left", padx=4)
        ttk.Button(bt, text="Liste aktualisieren", command=self.fill).pack(side="left")
        ttk.Button(bt, text="▶ / ■", width=6, command=self.listen).pack(side="left", padx=(12, 0))

        opt = ttk.LabelFrame(f, text="Für alle Soundboard-Kacheln", padding=8)
        opt.pack(fill="x", pady=(10, 0))
        self.v = {}
        rows = (("volume", "Lautstärke:", sound_plugin.VOLUMES, "100", 12),
                ("device", "Ausgabegerät:", sound_plugin._out_choices(), "", 46),
                ("device2", "Zweites Gerät (Discord & Co.):", sound_plugin._out2_choices(), "", 46),
                ("mode", "Wenn schon ein Sound läuft:", sound_plugin.MODES, "overlay", 46))
        self.maps = {}
        for r, (key, label, choices, default, width) in enumerate(rows):
            ttk.Label(opt, text=label).grid(row=r, column=0, sticky="w", pady=2)
            cur = sb.get(key, default)
            if cur not in {v for v, _ in choices}:
                choices = choices + [(cur, f"{cur} (nicht verbunden)")]
            self.maps[key] = {lbl: v for v, lbl in choices}
            var = tk.StringVar(value=next(lbl for v, lbl in choices if v == cur))
            ttk.Combobox(opt, textvariable=var, state="readonly", width=width,
                         values=[lbl for _, lbl in choices]).grid(row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            self.v[key] = var
        ttk.Label(opt, text=sound_plugin._cable_hint(), foreground=DIM, wraplength=480, justify="left").grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(4, 0))

        b = ttk.Frame(f)
        b.pack(fill="x", pady=(12, 0))
        ttk.Button(b, text="Schließen", command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        TileDialog._place_over(self, parent)
        self.fill()
        self.grab_set()

    def fill(self):
        """Angehakte (in ihrer Reihenfolge) zuerst, dann alle übrigen Dateien des Sound-Ordners."""
        self.tree.delete(*self.tree.get_children())
        os.makedirs(self.ss.SOUND_DIR, exist_ok=True)
        files = sorted(os.path.join(self.ss.SOUND_DIR, n) for n in os.listdir(self.ss.SOUND_DIR)
                       if n.lower().endswith((".mp3", ".wav", ".ogg", ".flac")))
        on = [p for p in self.checked if os.path.isfile(p)]
        for p in on + [p for p in files if p not in self.checked]:
            mark = self.CHECK if p in self.checked else self.EMPTY
            self.tree.insert("", "end", iid=p, text=f"{mark}  {os.path.splitext(os.path.basename(p))[0]}",
                             image=self._dot(self._color(p, on)))

    def _color(self, p, on):
        """Knopffarbe wie auf dem Deck: fest gewählt oder automatisch nach der Reihenfolge; nicht angehakt = grau."""
        if p not in on:
            return "#4A4E58"
        name = self.colors.get(p)
        if name in self.sp.COLORS:
            return self.sp.COLORS[name]
        return self.sp.PALETTE[on.index(p) % len(self.sp.PALETTE)][1]

    def _dot(self, color):
        if color not in self._dots:
            from PIL import Image, ImageDraw
            im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse((6, 6, 58, 58), fill=color)
            self._dots[color] = ImageTk.PhotoImage(im.resize((14, 14), Image.LANCZOS))
        return self._dots[color]

    def _color_menu(self, ev):
        p = self.tree.identify_row(ev.y)
        if not p:
            return
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="automatisch", command=lambda: self._set_color(p, ""))
        m.add_separator()
        for name, hexcol in self.sp.PALETTE:
            m.add_command(label=name, image=self._dot(hexcol), compound="left",
                          command=lambda n=name: self._set_color(p, n))
        m.tk_popup(ev.x_root, ev.y_root)

    def _set_color(self, p, name):
        if name:
            self.colors[p] = name
        else:
            self.colors.pop(p, None)
        if name and p not in self.checked:  # Farbe gewählt -> auch aufs Soundboard
            self.checked.append(p)
        self.fill()

    def _toggle(self, ev):
        p = self.tree.identify_row(ev.y)
        if not p:
            return
        if p in self.checked:
            self.checked.remove(p)
        else:
            self.checked.append(p)
        self._last = p
        y = self.tree.yview()[0]
        self.fill()  # Farbpunkte folgen der Reihenfolge
        self.tree.yview_moveto(y)

    def listen(self):
        """Zuletzt angeklickten Sound probehören (nochmal = stoppen)."""
        import soundplayer
        p = getattr(self, "_last", None)
        if not p:
            return
        if soundplayer.progress(soundplayer.PREVIEW) is not None:
            soundplayer.stop(soundplayer.PREVIEW)
            return
        try:
            with open(p, "rb") as fh:
                soundplayer.preview(fh.read())
        except Exception as e:
            messagebox.showerror("PC-Deck", f"Abspielen fehlgeschlagen:\n{e}", parent=self)

    def search(self):
        def downloaded(path, name):  # neu heruntergeladen -> sofort aufs Soundboard
            if path not in self.checked:
                self.checked.append(path)
            if self.winfo_exists():
                self.fill()
        SoundSearchDialog(self, on_download=downloaded)

    def open_folder(self):
        os.makedirs(self.ss.SOUND_DIR, exist_ok=True)
        os.startfile(self.ss.SOUND_DIR)

    def close(self):
        import soundplayer
        soundplayer.stop(soundplayer.PREVIEW)
        sb = {"sounds": [p for p in self.checked if os.path.isfile(p)]}
        sb["colors"] = {p: c for p, c in self.colors.items() if p in sb["sounds"]}
        for key, var in self.v.items():
            sb[key] = self.maps[key].get(var.get(), "")
        if sb != self.win.cfg.get("soundboard"):
            self.win.cfg["soundboard"] = sb
            stored = self.win.config_store.deck_copy(self.win.deck_id)
            if stored is not None:
                stored["soundboard"] = sb
                self.win.config_store.set_deck(self.win.deck_id, stored)
            link = self.win.manager.link(self.win.deck_id)
            if link:
                link.request_sync()
        self.destroy()


# ======================================================================= Firmware-Dialog
class FirmwareDialog(tk.Toplevel):
    """Firmware des Decks über die bestehende USB-Verbindung aktualisieren (ab FW 16)."""

    def __init__(self, parent, manager, deck_id):
        super().__init__(parent)
        self.title("Firmware des Decks")
        self.transient(parent)
        self.resizable(False, False)
        self.manager, self.deck_id = manager, deck_id
        self.results = queue.Queue()
        self.busy = False
        self.deck_type, self.slot = None, 0
        self.bundled = fwfile.bundled()
        link = manager.link(deck_id)

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        via = "nicht verbunden" if not link else (f"über {link.port}" if link.via_wlan else f"über USB ({link.port})")
        self.deck_lbl = ttk.Label(f, text=f"Deck: Firmware {link.fw if link else '?'}, {via}")
        self.deck_lbl.grid(row=0, column=0, columnspan=2, sticky="w")
        self.bundle_lbl = ttk.Label(f, text="")
        self.bundle_lbl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.bar = ttk.Progressbar(f, length=440, maximum=1000)
        self.bar.grid(row=2, column=0, columnspan=2, sticky="we", pady=(12, 4))
        self.status = ttk.Label(f, text="", wraplength=440, justify="left")
        self.status.grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(f, foreground=DIM, justify="left", wraplength=440,
                  text="Die neue Firmware wird über USB (ab Firmware 18 auch über WLAN) übertragen und in den "
                       "zweiten Programm-Platz "
                       "des Decks geschrieben. Erst wenn alles vollständig und die Prüfsumme richtig ist, startet "
                       "das Deck mit ihr neu – bei einem Abbruch läuft die alte Firmware einfach weiter. Kacheln, "
                       "Design, WLAN und Touch-Kalibrierung bleiben erhalten.").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        b = ttk.Frame(f)
        b.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
        self.bundle_btn = tip(ttk.Button(b, text="Mitgelieferte aufspielen", command=self.flash_bundled),
                              "Die Firmware, die mit dieser App-Version geliefert wurde, aufs Deck spielen.")
        self.bundle_btn.pack(side="left", padx=4)
        self.file_btn = tip(ttk.Button(b, text="Datei wählen ...", command=self.flash_file),
                            "Eine andere PC-Deck-Firmware (.bin) aufspielen, z. B. eine selbst gebaute "
                            "(.pio\\build\\<env>\\firmware.bin). Der Deck-Typ muss passen.")
        self.file_btn.pack(side="left", padx=4)
        self.close_btn = ttk.Button(b, text="Schließen", command=self.close)
        self.close_btn.pack(side="left", padx=(12, 0))
        for w in (self.bundle_btn, self.file_btn):
            w.configure(state="disabled")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        TileDialog._place_over(self, parent)
        self.after(100, self._poll)
        if not link:
            self.status.configure(text="Das Deck ist nicht verbunden.")
        elif link.fw < 16:
            self.status.configure(text=f"Das Deck hat Firmware {link.fw}. Das Update aus der App geht erst ab "
                                       "Firmware 16 – einmalig per USB mit PlatformIO aufspielen (siehe Anleitung).")
        elif link.via_wlan and link.fw < 18:
            self.status.configure(text=f"Das Deck hat Firmware {link.fw}. Update über WLAN geht erst ab Firmware 18 – "
                                       "dieses eine Mal per USB anstecken.")
        else:
            self.status.configure(text="Deck wird abgefragt ...")

            def work():
                self.results.put((self._on_info, link.request("FWINFO", 5)))
            threading.Thread(target=work, daemon=True).start()
        self.grab_set()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                done, res = self.results.get_nowait()
                done(*res)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_info(self, ok, lines):
        parts = next((l.split() for l in lines if l.startswith("FWINFO ")), []) if ok else []
        if len(parts) < 4:
            self.status.configure(text=f"Abfrage fehlgeschlagen: {lines if not ok else 'keine FWINFO-Antwort'}")
            return
        self.deck_type, self.slot = parts[2], int(parts[3])
        link = self.manager.link(self.deck_id)
        self.deck_lbl.configure(text=f"Deck: Firmware {parts[1]}, {fwfile.type_name(self.deck_type)}, "
                                     f"über USB ({link.port if link else '?'})")
        b = self.bundled.get(self.deck_type)
        self.bundle_lbl.configure(text=f"Mitgeliefert: Firmware {b[0]}" if b else
                                  "Mitgeliefert: keine Firmware für diesen Deck-Typ")
        self.bundle_btn.configure(state="normal" if b else "disabled")
        self.file_btn.configure(state="normal")
        self.status.configure(text="Bereit." if not b or b[0] > int(parts[1]) else
                              "Das Deck ist schon auf dem Stand der mitgelieferten Firmware.")

    def flash_bundled(self):
        self._flash(self.bundled[self.deck_type][1])

    def flash_file(self):
        path = filedialog.askopenfilename(parent=self, title="Firmware wählen",
                                          filetypes=[("Firmware", "*.bin"), ("Alle Dateien", "*.*")])
        if path:
            self._flash(path)

    def _flash(self, path):
        link = self.manager.link(self.deck_id)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            messagebox.showerror("PC-Deck", f"Datei nicht lesbar:\n{e}", parent=self)
            return
        inf = fwfile.info(data)
        if not inf:
            messagebox.showerror("PC-Deck", "Das ist keine PC-Deck-Firmware (ab Firmware 16).", parent=self)
            return
        if inf[1] != self.deck_type:
            messagebox.showerror("PC-Deck", f"Die Datei ist für {fwfile.type_name(inf[1])}, das Deck ist "
                                            f"{fwfile.type_name(self.deck_type)}.", parent=self)
            return
        if self.slot and len(data) > self.slot:
            messagebox.showerror("PC-Deck", "Die Firmware ist zu groß für den Programm-Platz des Decks.", parent=self)
            return
        if not link or (link.via_wlan and link.fw < 18):
            messagebox.showerror("PC-Deck", "Das Deck ist nicht per USB verbunden (über WLAN erst ab Firmware 18).",
                                 parent=self)
            return
        if inf[0] <= link.fw and not messagebox.askyesno(
                "PC-Deck", f"Das Deck hat schon Firmware {link.fw}, die Datei ist Firmware {inf[0]}. "
                           "Trotzdem aufspielen?", parent=self):
            return
        hint = ("Deck während der Übertragung eingeschaltet und im WLAN lassen." if link.via_wlan else
                "USB-Kabel während der Übertragung nicht abziehen.")
        if not messagebox.askyesno("PC-Deck", f"Firmware {inf[0]} ({len(data):,} Byte) jetzt aufspielen?".replace(",", ".")
                                   + "\n\n" + hint, parent=self):
            return
        self.busy = True
        for w in (self.bundle_btn, self.file_btn, self.close_btn):
            w.configure(state="disabled")
        self.status.configure(text="Firmware wird übertragen ...")
        link.request_firmware(data, lambda x: self.results.put((self._on_progress, (x,))),
                              lambda ok, text: self.results.put((self._on_done, (ok, text))))

    def _on_progress(self, x):
        self.bar.configure(value=int(x * 1000))
        self.status.configure(text=f"Firmware wird übertragen ... {int(x * 100)} %")

    def _on_done(self, ok, text):
        self.busy = False
        self.close_btn.configure(state="normal")
        self.status.configure(text=text if ok else f"Fehlgeschlagen: {text}\nDie alte Firmware läuft weiter.")
        if ok:
            self.bar.configure(value=1000)
        else:
            self.file_btn.configure(state="normal")
            if self.bundled.get(self.deck_type):
                self.bundle_btn.configure(state="normal")

    def close(self):
        if not self.busy:
            self.destroy()


# ======================================================================= Hauptfenster
class ConfigWindow(tk.Toplevel):
    TILE_W, TILE_H = 120, 100

    def __init__(self, root, config, manager, deck_id=None):
        super().__init__(root)
        self.config_store = config
        self.manager = manager
        self.title(f"PC-Deck {VERSION} – Konfiguration")
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.photos = []
        self.clipboard = None
        self.dirty = False
        self.page = 0

        ids = self._deck_ids()
        if not ids:
            messagebox.showinfo("PC-Deck", "Noch kein Deck bekannt.\n\nSteck ein Deck per USB an – "
                                "es wird automatisch erkannt.", parent=root)
            self.destroy()
            return
        self.deck_id = deck_id if deck_id in ids else self._preferred(ids)
        self.cfg = config.deck_copy(self.deck_id)

        self._build()
        self._load_deck_ui()

    # ---------------------------------------------------------- Aufbau
    def _deck_ids(self):
        with self.config_store.lock:
            return list(self.config_store.data.get("decks", {}).keys())

    def _preferred(self, ids):
        connected = self.manager.connected()
        return next((i for i in ids if i in connected), ids[0])

    def _deck_title(self, did):
        d = self.config_store.deck(did, create=False) or {}
        state = "verbunden" if did in self.manager.connected() else "nicht verbunden"
        return f"{d.get('name', did)}  ({state}, ID {did})"

    def _build(self):
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")

        ttk.Label(top, text="Deck:").pack(side="left")
        self.deck_box = ttk.Combobox(top, state="readonly", width=46)
        self.deck_box.pack(side="left", padx=4)
        self.deck_box.bind("<<ComboboxSelected>>", self.on_deck_selected)
        tip(self.deck_box, "Welches Deck du bearbeitest. Jedes Deck (erkannt an seiner Chip-ID) hat ein eigenes "
                           "Layout, Design und eine eigene Drehknopf-Belegung.")
        tip(ttk.Button(top, text="Umbenennen", command=self.rename_deck),
            "Namen des Decks ändern (nur hier in der App sichtbar).").pack(side="left", padx=2)
        tip(ttk.Button(top, text="Entfernen", command=self.remove_deck),
            "Deck samt Layout aus der Konfiguration löschen. Steckst du es wieder an, "
            "erscheint es leer neu.").pack(side="left", padx=2)

        tip(ttk.Label(top, text="Helligkeit:"), "Hintergrundbeleuchtung des Displays. "
            "Wird beim Loslassen des Reglers sofort am Deck übernommen.").pack(side="left", padx=(16, 2))
        self.v_bri = tk.IntVar()
        s = ttk.Scale(top, from_=10, to=255, variable=self.v_bri, length=120, command=lambda v: self._mark_dirty())
        s.pack(side="left")
        s.bind("<ButtonRelease-1>", lambda e: self._live_brightness())

        tip(ttk.Label(top, text="Design:"), "Aussehen des Decks: Startbild (auch ohne Verbindung), Hintergrund "
            "und Kachelfarben. Das Design bleibt im Deck gespeichert – auch an einem anderen PC.").pack(
            side="left", padx=(16, 2))
        self.v_design = tk.StringVar()
        self.design_box = ttk.Combobox(top, textvariable=self.v_design, state="readonly", width=20,
                                       values=[n for _, n in theme.CHOICES])
        self.design_box.pack(side="left")
        self.design_box.bind("<<ComboboxSelected>>", self.on_design)
        tip(self.design_box, "Standard, Rot/Magenta, Grün und Schwarz/Weiß sind feste Designs.\n"
            "„Eigenes Design“ stellst du mit „Eigenes ...“ selbst zusammen.\n"
            "„Auf dem Deck lassen“ ändert nichts am Deck (z. B. Deck von einem anderen PC).\n"
            "Bei Schwarz/Weiß werden auch alle Icons grau – auch neu hinzugefügte.")
        tip(ttk.Button(top, text="Eigenes ...", command=self.edit_theme),
            "Eigenes Design gestalten: Bild, Kachelfarbe, Stil, Helligkeit und Deckkraft – mit Vorschau.").pack(
            side="left", padx=(4, 2))

        row2 = ttk.Frame(self, padding=(10, 0, 10, 8))
        row2.pack(fill="x")
        tip(ttk.Label(row2, text="Spalten:"), "Kacheln nebeneinander (2–5). Beim Verkleinern werden Kacheln "
            "nur ausgeblendet und kommen beim Vergrößern zurück.").pack(side="left")
        self.v_cols = tk.IntVar()
        ttk.Spinbox(row2, from_=COLS_RANGE[0], to=COLS_RANGE[1], width=3, textvariable=self.v_cols,
                    command=self.on_grid_changed, state="readonly").pack(side="left", padx=(2, 10))
        tip(ttk.Label(row2, text="Zeilen:"), "Kacheln untereinander (1–3).").pack(side="left")
        self.v_rows = tk.IntVar()
        ttk.Spinbox(row2, from_=ROWS_RANGE[0], to=ROWS_RANGE[1], width=3, textvariable=self.v_rows,
                    command=self.on_grid_changed, state="readonly").pack(side="left", padx=(2, 20))

        ttk.Button(row2, text="◀", width=3, command=lambda: self.goto_page(-1)).pack(side="left")
        self.page_label = ttk.Label(row2, width=12, anchor="center")
        self.page_label.pack(side="left")
        ttk.Button(row2, text="▶", width=3, command=lambda: self.goto_page(1)).pack(side="left")
        tip(ttk.Button(row2, text="+ Seite", command=self.add_page),
            "Neue leere Seite anhängen (bis zu 8). Am Deck blätterst du unten mit < und >.").pack(
            side="left", padx=(10, 2))
        tip(ttk.Button(row2, text="Seite löschen", command=self.del_page),
            "Die angezeigte Seite mit allen Kacheln löschen.").pack(side="left", padx=2)
        self.auto_btn = ttk.Button(row2, text="Automatik ...", command=self.edit_auto)
        self.auto_btn.pack(side="left", padx=(10, 2))
        tip(self.auto_btn, "Automatik: Kommt eines der hier eingetragenen Programme in den Vordergrund, "
            "zeigt das Deck diese Seite. Verlässt du es, springt das Deck zurück.")
        tip(ttk.Button(row2, text="Drehknopf ...", command=self.edit_knob),
            "Belegung des Drehknopfs: Drehen, gedrückt drehen, kurz und lang drücken – "
            "als Standard und je Programm (z. B. Discord, Edge).").pack(side="left", padx=(10, 2))
        self.v_suggest = tk.BooleanVar(value=True)
        tip(ttk.Checkbutton(row2, text="Plus-Kachel", variable=self.v_suggest, command=self.on_suggest),
            "Das Programm im Vordergrund erscheint blass mit „+“ auf dem ersten freien Platz. "
            "Antippen am Deck speichert es dort als Kachel.").pack(side="left", padx=(10, 2))

        self.grid_frame = tk.Frame(self, bg=BG, padx=10, pady=10)
        self.grid_frame.pack()

        hint = ("Programme, Verknüpfungen oder Ordner aus dem Explorer auf eine Kachel ziehen, "
                "oder Kachel anklicken. Rechtsklick: kopieren / einfügen / leeren.") if HAVE_DND else \
               ("Kachel anklicken zum Bearbeiten. Rechtsklick: kopieren / einfügen / leeren.  "
                "(Für Drag & Drop: pip install tkinterdnd2)")
        tk.Label(self, text=hint, bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(pady=(0, 6))

        bottom = ttk.Frame(self, padding=(10, 4, 10, 10))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="left")
        ttk.Button(bottom, text="Schließen", command=self.close).pack(side="right")
        tip(ttk.Button(bottom, text="?", width=3, command=lambda: messagebox.showinfo(
            "PC-Deck – so funktioniert's",
            APP_HELP + f"\n\nPC-Deck {VERSION}   •   Einstellungen und Protokoll: {APP_DIR}", parent=self)), "Kurze Anleitung zu Deck, Kacheln und Drehknopf").pack(
            side="right", padx=(0, 6))
        tip(ttk.Button(bottom, text="Speichern & ans Deck senden", command=self.save),
            "Änderungen speichern. Kacheln, Icons und Design werden nur übertragen, wenn sie sich "
            "geändert haben; die Drehknopf-Belegung gilt sofort.").pack(side="right", padx=6)
        tip(ttk.Button(bottom, text="Soundboard ...", command=self.edit_soundboard),
            "Sounds anhaken: daraus entsteht die Seite „Soundboard“ am Ende des Decks. Neue Sounds direkt "
            "suchen, probehören und herunterladen.").pack(side="right", padx=6)
        tip(ttk.Button(bottom, text="Smart Home ...", command=self.edit_smarthome),
            "Bosch Smart Home Controller koppeln: danach gibt es Kacheln für Rollladen und Licht "
            "(Kachel-Typ „Bosch Smart Home“).").pack(side="right", padx=6)
        tip(ttk.Button(bottom, text="Firmware ...", command=self.edit_firmware),
            "Firmware des Decks über das USB-Kabel aktualisieren (ab Firmware 16) – mitgeliefert oder "
            "eigene .bin-Datei.").pack(side="right", padx=6)
        tip(ttk.Button(bottom, text="WLAN ...", command=self.edit_wifi),
            "Deck optional per WLAN anbinden: einmal per USB einrichten und koppeln, danach findet die App "
            "das Deck im WLAN, sobald es nicht per USB steckt.").pack(side="right", padx=6)
        tip(ttk.Button(bottom, text="Touch kalibrieren", command=self.calibrate),
            "Am Deck erscheinen nacheinander Markierungen in den Ecken – jeweils genau antippen. "
            "Hilft, wenn Tipper an der falschen Stelle ankommen.").pack(side="right", padx=6)

    def _load_deck_ui(self):
        ids = self._deck_ids()
        self.deck_box["values"] = [self._deck_title(i) for i in ids]
        self.deck_box.current(ids.index(self.deck_id))
        self.v_cols.set(self.cfg["cols"])
        self.v_rows.set(self.cfg["rows"])
        self.v_bri.set(self.cfg.get("brightness", 200))
        self.v_suggest.set(self.cfg.get("suggest", True))
        self.v_design.set(dict(theme.CHOICES)[theme.design_id(self.cfg)])
        self.page = min(self.page, len(self.cfg["pages"]) - 1)
        self.dirty = False
        self.render()

    def refresh_status(self):
        """Wird von außen aufgerufen, wenn Decks kommen oder gehen."""
        if not self.winfo_exists():
            return
        ids = self._deck_ids()
        self.deck_box["values"] = [self._deck_title(i) for i in ids]
        if self.deck_id in ids:
            self.deck_box.current(ids.index(self.deck_id))

    # ---------------------------------------------------------- Raster zeichnen
    def render(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.photos.clear()
        cols, rows = self.cfg["cols"], self.cfg["rows"]
        page = self.cfg["pages"][self.page]
        for i in range(cols * rows):
            t = page[i]
            cell = tk.Frame(self.grid_frame, width=self.TILE_W, height=self.TILE_H, bg=BG)
            cell.grid(row=i // cols, column=i % cols, padx=4, pady=4)
            cell.pack_propagate(False)
            if t:
                text = tile_label(t)
                if t.get("text_only"):
                    btn = tk.Label(cell, text=text, bg=TILE, fg=TEXT, font=("Segoe UI", 10), wraplength=self.TILE_W - 10)
                else:
                    photo = _icon_photo(t)
                    self.photos.append(photo)
                    btn = tk.Label(cell, image=photo, text=text, compound="top", bg=TILE, fg=TEXT,
                                   font=("Segoe UI", 9))
            else:
                btn = tk.Label(cell, text="+", bg="#1c1e24", fg=DIM, font=("Segoe UI", 20))
            btn.pack(fill="both", expand=True)
            tip(btn, TILE_TIP if t else "Leerer Platz: anklicken oder ein Programm, eine Verknüpfung "
                "oder einen Ordner aus dem Explorer hierher ziehen.")
            btn.bind("<Button-1>", lambda e, i=i: self.edit_tile(i))
            btn.bind("<Button-3>", lambda e, i=i: self.context_menu(e, i))
            if HAVE_DND:
                btn.drop_target_register(DND_FILES)
                btn.dnd_bind("<<Drop>>", lambda e, i=i: self.on_drop(e, i))
        self.page_label.configure(text=f"Seite {self.page + 1} / {len(self.cfg['pages'])}")
        n = len(auto_apps(self.cfg)[self.page])
        self.auto_btn.configure(text=f"Automatik ({n}) ..." if n else "Automatik ...")
        self._update_status()

    def _update_status(self):
        link = self.manager.link(self.deck_id)
        if link:
            txt = f"Deck verbunden über {link.port}" if link.via_wlan else f"Deck verbunden über USB ({link.port})"
            txt += f", Firmware {link.fw}"
        else:
            txt = "Deck nicht verbunden – Änderungen werden beim Anstecken übertragen"
        n = sum(len(h) for h in (self.cfg.get("hidden") or []))
        if n:
            txt += f"   •   {n} Kachel(n) ausgeblendet – kommen beim Vergrößern des Rasters zurück"
        if self.dirty:
            txt += "   •   ungespeicherte Änderungen"
        self.status.configure(text=txt)

    def _mark_dirty(self):
        self.dirty = True
        self._update_status()

    # ---------------------------------------------------------- Kacheln
    def _set_tile(self, i, tile):
        self.cfg["pages"][self.page][i] = tile
        self._mark_dirty()
        self.render()

    def edit_tile(self, i):
        d = TileDialog(self, self.cfg["pages"][self.page][i])
        if d.result == "clear":
            self._set_tile(i, None)
        elif d.result:
            self._set_tile(i, d.result)

    def on_drop(self, event, i):
        paths = self.tk.splitlist(event.data)
        if not paths:
            return
        cols_rows = self.cfg["cols"] * self.cfg["rows"]
        page = self.cfg["pages"][self.page]
        # mehrere Dateien: ab der Zielkachel auf freie Plätze verteilen
        slot = i
        for p in paths:
            while slot < cols_rows and page[slot] and slot != i:
                slot += 1
            if slot >= cols_rows:
                break
            p = os.path.normpath(p)
            # .exe ohne eigenes Icon: lieber die Verknüpfung speichern (Icon + übersteht Updates)
            p = iconextract.needs_shortcut(p) or p
            page[slot] = {"label": "", "path": p, "args": "", "icon": ""}
            slot += 1
        self._mark_dirty()
        self.render()

    def context_menu(self, event, i):
        m = tk.Menu(self, tearoff=0)
        t = self.cfg["pages"][self.page][i]
        m.add_command(label="Bearbeiten ...", command=lambda: self.edit_tile(i))
        m.add_command(label="Kopieren", command=lambda: setattr(self, "clipboard", dict(t)),
                      state="normal" if t else "disabled")
        m.add_command(label="Ausschneiden", command=lambda: (setattr(self, "clipboard", dict(t)), self._set_tile(i, None)),
                      state="normal" if t else "disabled")
        m.add_command(label="Einfügen", command=lambda: self._set_tile(i, dict(self.clipboard)),
                      state="normal" if self.clipboard else "disabled")
        m.add_separator()
        m.add_command(label="Leeren", command=lambda: self._set_tile(i, None), state="normal" if t else "disabled")
        m.tk_popup(event.x_root, event.y_root)

    def on_suggest(self):
        """Plus-Kachel: Programm im Vordergrund auf dem ersten freien Platz zum Übernehmen anbieten."""
        self.cfg["suggest"] = bool(self.v_suggest.get())
        self._mark_dirty()

    def merge_from_store(self):
        """Am Deck per Plus-Kachel hinzugefügte Kacheln übernehmen, ohne eigene Änderungen zu verlieren."""
        if not self.winfo_exists():
            return
        stored = self.config_store.deck_copy(self.deck_id)
        if not stored:
            return
        changed = False
        for p, page in enumerate(stored["pages"]):
            if p >= len(self.cfg["pages"]):
                break
            for i, t in enumerate(page):
                if t and i < len(self.cfg["pages"][p]) and not self.cfg["pages"][p][i]:
                    self.cfg["pages"][p][i] = t
                    changed = True
        if changed:
            self.render()

    def apply_deletion(self, deleted):
        """Am Deck gelöschte Kachel auch im offenen Fenster entfernen (nur wenn sie dort unverändert ist)."""
        if not deleted or not self.winfo_exists():
            return
        deck_id, p, i, tile = deleted
        if deck_id == self.deck_id and p < len(self.cfg["pages"]) and self.cfg["pages"][p][i] == tile:
            self.cfg["pages"][p][i] = None
            self.render()

    def on_design(self, _e=None):
        """Design gewählt (gilt nach „Speichern & ans Deck senden“)."""
        new = next(i for i, n in theme.CHOICES if n == self.v_design.get())
        if new != theme.design_id(self.cfg):
            self.cfg["design"] = new
            self._mark_dirty()

    def edit_theme(self):
        """Eigenes Design (Startbild, Hintergrund, Kachelfarben) gestalten und auswählen."""
        icons = []
        for t in self.cfg["pages"][self.page]:
            if t and not t.get("text_only") and len(icons) < 8:
                try:
                    icons.append(tile_icon(t))
                except Exception:
                    pass
        d = ThemeDialog(self, theme.custom(self.cfg), icons)
        if d.result is not None:
            self.cfg["theme"] = d.result
            self.cfg["design"] = "custom" if d.result.get("enabled") else "off"
            self.v_design.set(dict(theme.CHOICES)[self.cfg["design"]])
            self._mark_dirty()

    def edit_knob(self):
        """Belegung des Drehknopfs: Standard und Presets pro Programm."""
        d = KnobDialog(self, knob.settings(self.cfg), self.cfg)
        if d.result is not None and d.result != knob.settings(self.cfg):
            self.cfg["knob"] = d.result
            self._mark_dirty()

    def edit_auto(self):
        lists = auto_apps(self.cfg)
        elsewhere = {}
        for p, apps in enumerate(lists):
            if p != self.page:
                for a in apps:
                    elsewhere.setdefault(a.lower(), p + 1)
        d = AutoDialog(self, self.page + 1, lists[self.page], elsewhere)
        if d.result is not None and d.result != lists[self.page]:
            lists[self.page] = d.result
            # ein Programm nur auf einer Seite: von den anderen Seiten entfernen
            mine = {a.lower() for a in d.result}
            for p in range(len(lists)):
                if p != self.page:
                    lists[p] = [a for a in lists[p] if a.lower() not in mine]
            self.cfg["auto_apps"] = lists
            self._mark_dirty()
            self.render()

    # ---------------------------------------------------------- Seiten / Raster
    def goto_page(self, d):
        self.page = (self.page + d) % len(self.cfg["pages"])
        self.render()

    def add_page(self):
        if len(self.cfg["pages"]) >= MAX_PAGES:
            messagebox.showinfo("PC-Deck", f"Maximal {MAX_PAGES} Seiten.", parent=self)
            return
        self.cfg["auto_apps"] = auto_apps(self.cfg)
        self._hidden().insert(self.page + 1, [])
        self.cfg["pages"].insert(self.page + 1, empty_page(self.cfg["cols"], self.cfg["rows"]))
        self.cfg["auto_apps"].insert(self.page + 1, [])
        self.page += 1
        self._mark_dirty()
        self.render()

    def del_page(self):
        if len(self.cfg["pages"]) <= 1:
            self.cfg["pages"][0] = empty_page(self.cfg["cols"], self.cfg["rows"])
            self.cfg["hidden"] = [[]]
        else:
            if any(self.cfg["pages"][self.page]) and not messagebox.askyesno(
                    "PC-Deck", "Diese Seite mit allen Kacheln löschen?", parent=self):
                return
            self.cfg["auto_apps"] = auto_apps(self.cfg)
            del self._hidden()[self.page]
            del self.cfg["pages"][self.page]
            del self.cfg["auto_apps"][self.page]
            self.page = max(0, self.page - 1)
        self._mark_dirty()
        self.render()

    def _hidden(self):
        """Ausgeblendete Kacheln pro Seite: [{"r", "c", "tile"}, ...] (gleich lang wie die Seiten)."""
        lists = list(self.cfg.get("hidden") or [])
        lists = (lists + [[] for _ in self.cfg["pages"]])[:len(self.cfg["pages"])]
        self.cfg["hidden"] = lists
        return lists

    def on_grid_changed(self):
        """Raster ändern: Kacheln, die nicht mehr passen, werden ausgeblendet (nicht gelöscht) und
        kommen beim Vergrößern an ihrer alten Stelle (Zeile/Spalte) zurück."""
        cols, rows = self.v_cols.get(), self.v_rows.get()
        oc, orow = self.cfg["cols"], self.cfg["rows"]
        if (cols, rows) == (oc, orow):
            return
        hidden = self._hidden()
        new_pages, new_hidden = [], []
        for page, hid in zip(self.cfg["pages"], hidden):
            np_ = empty_page(cols, rows)
            keep = []
            # alle Kacheln dieser Seite mit Position: sichtbare und bisher ausgeblendete
            items = [(idx // oc, idx % oc, t) for idx, t in enumerate(page) if t]
            items += [(h["r"], h["c"], h["tile"]) for h in hid]
            for r, c, t in items:
                if r < rows and c < cols and not np_[r * cols + c]:
                    np_[r * cols + c] = t
                else:
                    keep.append({"r": r, "c": c, "tile": t})
            new_pages.append(np_)
            new_hidden.append(keep)
        self.cfg["cols"], self.cfg["rows"], self.cfg["pages"] = cols, rows, new_pages
        self.cfg["hidden"] = new_hidden
        self._mark_dirty()
        self.render()

    # ---------------------------------------------------------- Decks
    def _ask_save(self):
        if not self.dirty:
            return True
        r = messagebox.askyesnocancel("PC-Deck", "Änderungen speichern und ans Deck senden?", parent=self)
        if r is None:
            return False
        if r:
            self.save()
        return True

    def on_deck_selected(self, _e=None):
        ids = self._deck_ids()
        new_id = ids[self.deck_box.current()]
        if new_id == self.deck_id:
            return
        if not self._ask_save():
            self.deck_box.current(ids.index(self.deck_id))
            return
        self.deck_id = new_id
        self.cfg = self.config_store.deck_copy(new_id)
        self.page = 0
        self._load_deck_ui()

    def rename_deck(self):
        name = simpledialog.askstring("Deck umbenennen", "Neuer Name:", initialvalue=self.cfg["name"], parent=self)
        if name and name.strip():
            self.cfg["name"] = name.strip()
            stored = self.config_store.deck_copy(self.deck_id)
            stored["name"] = self.cfg["name"]
            self.config_store.set_deck(self.deck_id, stored)
            self.refresh_status()

    def remove_deck(self):
        if not messagebox.askyesno("PC-Deck", f"Layout von '{self.cfg['name']}' aus der Konfiguration entfernen?\n"
                                   "(Wird das Deck wieder angesteckt, beginnt es mit einem leeren Layout.)",
                                   parent=self):
            return
        with self.config_store.lock:
            self.config_store.data["decks"].pop(self.deck_id, None)
            self.config_store.save()
        ids = self._deck_ids()
        if not ids:
            self.destroy()
            return
        self.deck_id = self._preferred(ids)
        self.cfg = self.config_store.deck_copy(self.deck_id)
        self.page = 0
        self._load_deck_ui()

    # ---------------------------------------------------------- Aktionen
    def _live_brightness(self):
        link = self.manager.link(self.deck_id)
        if link:
            link.request_brightness(self.v_bri.get())

    def edit_wifi(self):
        if not self.manager.link(self.deck_id):
            messagebox.showinfo("PC-Deck", "Das Deck ist nicht verbunden. Zum Einrichten des WLANs bitte per "
                                "USB anstecken.", parent=self)
            return
        WifiDialog(self, self.manager, self.config_store, self.deck_id)

    def edit_soundboard(self):
        SoundboardDialog(self)

    def edit_smarthome(self):
        BoschDialog(self)

    def edit_firmware(self):
        FirmwareDialog(self, self.manager, self.deck_id)

    def calibrate(self):
        link = self.manager.link(self.deck_id)
        if not link:
            messagebox.showinfo("PC-Deck", "Das Deck ist nicht verbunden.", parent=self)
            return
        link.request_calibration()
        messagebox.showinfo("PC-Deck", "Auf dem Display jetzt die Ecken der Reihe nach antippen.", parent=self)

    def save(self):
        self.cfg["brightness"] = int(self.v_bri.get())
        self.config_store.set_deck(self.deck_id, self.cfg)
        self.dirty = False
        link = self.manager.link(self.deck_id)
        if link:
            link.request_sync(force=False)
        self._update_status()

    def close(self):
        if self._ask_save():
            self.destroy()
