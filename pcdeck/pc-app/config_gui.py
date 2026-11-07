"""Konfigurationsfenster: Kacheln pro Deck anlegen, per Drag & Drop oder Dialog."""

import copy
import logging
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

import boschshc
import deckview
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
from deck import (FOREGROUND_IGNORE, COLS_RANGE, MAX_PAGES, ROWS_RANGE, grid_ranges, is_big, SOUND_VOLUME_DEFAULT, add_extra_pages,
                  auto_apps,
                  display_opts, empty_page, tile_icon, tile_label)

try:
    from tkinterdnd2 import DND_FILES
    HAVE_DND = True
except ImportError:
    HAVE_DND = False

def _windows_colors():
    """Farben wie Windows: dunkel/hell nach der App-Einstellung, Akzent aus der Akzentfarbe des Nutzers."""
    light, accent, accent2 = False, (0, 120, 215), (0, 95, 184)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            light = winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as k:
            pal = bytes(winreg.QueryValueEx(k, "AccentPalette")[0])  # 8 Farben RGBA, hell -> dunkel
        cols = [tuple(pal[i:i + 3]) for i in range(0, 32, 4)]
        accent, accent2 = (cols[2], cols[4]) if not light else (cols[4], cols[5])
    except (OSError, ValueError, IndexError):
        pass
    hexc = "#{:02x}{:02x}{:02x}".format
    if light:
        c = dict(bg="#f3f3f3", panel="#ebebeb", field="#ffffff", button="#fbfbfb", hover="#e5e5e5",
                 border="#d0d0d0", text="#1b1b1b", dim="#616161", sel="#dcdcdc")
    else:
        c = dict(bg="#202020", panel="#1c1c1c", field="#2b2b2b", button="#2d2d2d", hover="#383838",
                 border="#3c3c3c", text="#e8e8e8", dim="#9a9a9a", sel="#343434")
    c.update(accent=hexc(*accent), accent2=hexc(*accent2), accent_rgb=accent, light=light)
    return c


C = _windows_colors()
BG = C["bg"]
TILE = C["field"]
TEXT = C["text"]
DIM = C["dim"]
_themed = []


def apply_theme(root):
    """ttk-Design wie Windows 11 (dunkel/hell, Akzentfarbe) – einmal je Programm; dunkle Titelleisten."""
    if _themed:
        return
    _themed.append(True)
    st = ttk.Style(root)
    st.theme_use("clam")
    font = ("Segoe UI", 9)
    st.configure(".", background=C["bg"], foreground=C["text"], fieldbackground=C["field"],
                 bordercolor=C["border"], darkcolor=C["bg"], lightcolor=C["bg"], troughcolor=C["field"],
                 selectbackground=C["accent2"], selectforeground="#ffffff", insertcolor=C["text"], font=font,
                 focuscolor=C["accent"])
    st.map(".", foreground=[("disabled", C["dim"])])
    st.configure("TButton", background=C["button"], bordercolor=C["border"], padding=(10, 4), relief="flat")
    st.map("TButton", background=[("pressed", C["sel"]), ("active", C["hover"])])
    st.configure("Accent.TButton", background=C["accent2"], foreground="#ffffff", bordercolor=C["accent2"])
    st.map("Accent.TButton", background=[("pressed", C["accent2"]), ("active", C["accent"])])
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        st.configure(w, fieldbackground=C["field"], foreground=C["text"], arrowcolor=C["text"],
                     bordercolor=C["border"], background=C["button"], padding=3)
        st.map(w, fieldbackground=[("readonly", C["field"]), ("disabled", C["bg"])],
               bordercolor=[("focus", C["accent"])], foreground=[("disabled", C["dim"])])
    for w in ("TCheckbutton", "TRadiobutton"):
        st.configure(w, indicatorbackground=C["field"], indicatorforeground=C["text"], background=C["bg"])
        st.map(w, indicatorbackground=[("selected", C["accent2"])], background=[("active", C["bg"])])
    st.configure("TLabelframe", background=C["bg"], bordercolor=C["border"])
    st.configure("TLabelframe.Label", background=C["bg"], foreground=C["text"])
    st.configure("Treeview", background=C["field"], fieldbackground=C["field"], foreground=C["text"],
                 bordercolor=C["border"])
    st.map("Treeview", background=[("selected", C["accent2"])])
    st.configure("Horizontal.TScale", background=C["accent"], troughcolor=C["field"], bordercolor=C["border"],
                 lightcolor=C["accent"], darkcolor=C["accent"])
    st.configure("Panel.TFrame", background=C["panel"])
    st.configure("Panel.TLabel", background=C["panel"], foreground=C["text"])
    st.configure("PanelDim.TLabel", background=C["panel"], foreground=C["dim"])
    st.configure("Head.TLabel", background=C["panel"], foreground=C["text"], font=("Segoe UI Semibold", 11))
    st.configure("Nav.TButton", background=C["panel"], bordercolor=C["panel"], anchor="w", padding=(12, 7),
                 font=("Segoe UI", 10))
    st.map("Nav.TButton", background=[("pressed", C["sel"]), ("active", C["hover"])])
    st.configure("NavOn.TButton", background=C["sel"], bordercolor=C["accent"], anchor="w", padding=(12, 7),
                 font=("Segoe UI Semibold", 10))
    st.configure("Panel.TCheckbutton", background=C["panel"])
    st.configure("TPanedwindow", background=C["border"])
    st.configure("Sash", sashthickness=6, gripcount=0, background=C["border"])
    st.configure("Vertical.TScrollbar", background=C["button"], troughcolor=C["panel"], arrowcolor=C["text"],
                 bordercolor=C["panel"])
    st.configure("Horizontal.TScrollbar", background=C["button"], troughcolor=C["panel"], arrowcolor=C["text"],
                 bordercolor=C["panel"])
    st.map("Panel.TCheckbutton", background=[("active", C["panel"])])
    for opt, val in (("*background", C["bg"]), ("*foreground", C["text"]), ("*Listbox.background", C["field"]),
                     ("*Listbox.selectBackground", C["accent2"]), ("*Menu.background", C["field"]),
                     ("*Menu.activeBackground", C["accent2"]), ("*TCombobox*Listbox.background", C["field"]),
                     ("*TCombobox*Listbox.foreground", C["text"]),
                     ("*TCombobox*Listbox.selectBackground", C["accent2"])):
        root.option_add(opt, val)

    def dark_title(ev):
        if C["light"] or not isinstance(ev.widget, (tk.Toplevel, tk.Tk)):
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(ev.widget.winfo_id())
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        except Exception:
            pass
    root.bind_class("Toplevel", "<Map>", dark_title, add="+")


class AutoScroll(ttk.Frame):
    """Bereich mit Scrollbalken, die nur bei Bedarf erscheinen (senkrecht, auf Wunsch auch waagrecht).
    Inhalt kommt in .inner; Texte mit Umbruch (wraplength) passen sich der Breite an."""
    _all = []

    def __init__(self, parent, horizontal=True, style="Panel.TFrame", bg=None):
        super().__init__(parent, style=style)
        self.horizontal = horizontal
        self.canvas = tk.Canvas(self, bg=bg or C["panel"], highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner = ttk.Frame(self.canvas, style=style)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self._later())
        self.canvas.bind("<Configure>", lambda e: self._later())
        self._after = None
        self._wrap_w = None
        AutoScroll._all.append(self)
        if len(AutoScroll._all) == 1:
            self.bind_all("<MouseWheel>", AutoScroll._wheel, add="+")
            self.bind_all("<Shift-MouseWheel>", lambda e: AutoScroll._wheel(e, True), add="+")

    def _later(self):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(30, self.update_layout)

    def update_layout(self):
        self._after = None
        if not self.winfo_exists():
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 20:
            return
        if self._wrap_w != cw:  # Texte an die Breite anpassen, dann neu messen
            self._wrap_w = cw
            self.rewrap()
            self._later()
            return
        rw = self.inner.winfo_reqwidth()
        width = cw if (not self.horizontal or rw <= cw) else rw
        self.canvas.itemconfigure(self._win, width=width)
        self.canvas.configure(scrollregion=(0, 0, width, max(self.inner.winfo_reqheight(), 1)))
        need_v = self.inner.winfo_reqheight() > ch + 1
        need_h = self.horizontal and rw > cw + 1
        for bar, need, pos in ((self.vsb, need_v, dict(row=0, column=1, sticky="ns")),
                               (self.hsb, need_h, dict(row=1, column=0, sticky="ew"))):
            if need and not bar.winfo_ismapped():
                bar.grid(**pos)
            elif not need and bar.winfo_ismapped():
                bar.grid_remove()
        if not need_v:
            self.canvas.yview_moveto(0)
        if not need_h:
            self.canvas.xview_moveto(0)

    def rewrap(self):
        """Umbrechende Texte auf die aktuelle Breite (abzüglich Einrückung) setzen."""
        cw = self.canvas.winfo_width()
        stack = list(self.inner.winfo_children())
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            try:
                if int(str(w.cget("wraplength")) or 0) > 0:
                    indent = w.winfo_rootx() - self.inner.winfo_rootx() if w.winfo_ismapped() else 20
                    w.configure(wraplength=max(160, cw - indent - 34))
            except (tk.TclError, ValueError):
                pass

    def top(self):
        self.canvas.yview_moveto(0)
        self.canvas.xview_moveto(0)
        self._wrap_w = None
        self._later()

    @staticmethod
    def _wheel(ev, horizontal=False):
        try:
            w = ev.widget.winfo_containing(ev.x_root, ev.y_root)
        except (tk.TclError, AttributeError):
            return
        if w is None or isinstance(w, (ttk.Combobox, ttk.Spinbox, tk.Listbox, ttk.Treeview)):
            return
        AutoScroll._all[:] = [a for a in AutoScroll._all if a.winfo_exists()]
        for a in AutoScroll._all:
            if str(w).startswith(str(a)):
                if horizontal and a.hsb.winfo_ismapped():
                    a.canvas.xview_scroll(-1 if ev.delta > 0 else 1, "units")
                elif not horizontal and a.vsb.winfo_ismapped():
                    a.canvas.yview_scroll(-1 if ev.delta > 0 else 1, "units")
                return


def _place_over(win, parent):
    """Mittig über dem Hauptfenster, aber immer ganz auf dem Bildschirm."""
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x = max(0, min(x, sw - w - 10))
    y = max(0, min(y, sh - h - 80))  # Platz für Titelleiste und Taskleiste
    win.geometry(f"+{x}+{y}")
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


class TileEditor(ttk.Frame):
    """Kachel bearbeiten: Typ wählen, darunter die Felder dieses Typs. Steht im Hauptfenster rechts
    (oder in einem eigenen Fenster). on_done(ergebnis): Kachel, "clear" oder None (Abbrechen)."""

    def __init__(self, parent, tile, win, on_done, wrap=340):
        super().__init__(parent)
        self.win = win            # Hauptfenster (Konfiguration des Decks)
        self.on_done = on_done
        self.wrap = wrap
        self.result = None
        self.data = dict(tile or {})          # Werte aller Typen (bleiben beim Typwechsel erhalten)
        self.knob_changes = {}                # programm -> Belegung bzw. False (= Standard), gilt mit OK
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
        self.type_box = ttk.Combobox(top, state="readonly", width=34, height=24,
                                     values=[label for _, label in self.entries])
        self.type_box.current([t for t, _ in self.entries].index(self.type))
        self.type_box.pack(side="left", padx=6)
        self.type_box.bind("<<ComboboxSelected>>", self.on_type_changed)

        self.fields_frame = ttk.Frame(f)
        self.fields_frame.grid(row=1, column=0, columnspan=4, sticky="we", pady=(10, 0))

        ttk.Label(f, text="Beschriftung (leer = automatisch):").grid(row=3, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Entry(f, textvariable=self.v_label, width=30).grid(row=4, column=0, columnspan=2, sticky="we")

        ttk.Label(f, text="Eigenes Icon (optional, .png/.ico/.jpg):").grid(row=7, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Entry(f, textvariable=self.v_icon, width=34).grid(row=8, column=0, columnspan=3, sticky="we")
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
        b.grid(row=13, column=0, columnspan=4, sticky="we", pady=(16, 0))
        ttk.Button(b, text="Kachel leeren", command=self.clear).pack(side="left")
        ttk.Button(b, text="OK", style="Accent.TButton", command=self.ok).pack(side="right")
        ttk.Button(b, text="Abbrechen", command=self.cancel).pack(side="right", padx=4)

        for v in (self.v_label, self.v_icon, self.v_text_only):
            v.trace_add("write", lambda *a: self._schedule_preview())
        self._after = None
        self._build_fields()
        self._update_preview()
        self._orig = self._tile()

    def _field_w(self, width):
        """Feldbreite in Zeichen: im schmalen rechten Bereich begrenzt."""
        return width if self.wrap > 400 or not width else min(width, 36)

    def changed(self):
        """Wurde seit dem Öffnen etwas geändert (Felder oder Drehknopf)?"""
        return bool(self.knob_changes) or self._tile() != self._orig

    def cancel(self):
        self.on_done(None)

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
                ttk.Label(self.fields_frame, text=text, foreground=DIM, wraplength=self.wrap, justify="left").grid(
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
                           command=lambda f=fld: LinksDialog(self.winfo_toplevel(), f.label.rstrip(" ."),
                                                             f.default)).grid(
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
                ttk.Label(self.fields_frame, text=fld.label, wraplength=self.wrap,
                          justify="left").grid(row=row, column=0, columnspan=4, sticky="w",
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
                ttk.Label(self.fields_frame, text=text, wraplength=self.wrap,
                          justify="left").grid(row=row, column=0, columnspan=4, sticky="w",
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
                    w = ttk.Combobox(self.fields_frame, state="readonly", width=self._field_w(fld.width),
                                     values=[lbl for _, lbl in choices])
                    labels = {v: l for v, l in choices}
                    values = {l: v for v, l in choices}
                    var.set(labels.get(var.get(), var.get()))
                    w.configure(textvariable=var)
                    var._map = values  # Anzeige -> Wert
                else:
                    w = ttk.Entry(self.fields_frame, textvariable=var, width=self._field_w(fld.width),
                                  show="•" if secret else "")
                w.grid(row=row, column=0, columnspan=4, sticky="we", pady=(2, 0))
                row += 1
                if fld.kind == "path":
                    bf = ttk.Frame(self.fields_frame)
                    bf.grid(row=row, column=0, columnspan=4, sticky="w", pady=(4, 0))
                    ttk.Button(bf, text="Programm / Datei ..." if self.wrap > 400 else "Datei ...",
                               command=lambda v=var: self.pick_file(v)).pack(side="left")
                    ttk.Button(bf, text="Ordner ...", command=lambda v=var: self.pick_folder(v)).pack(side="left", padx=4)
                    ttk.Button(bf, text="Aus dem Startmenü ..." if self.wrap > 400 else "Startmenü ...",
                               command=lambda v=var: self.pick_startmenu(v)).pack(side="left")
                    row += 1
                elif fld.kind == "file":
                    ttk.Button(self.fields_frame, text="...", width=4,
                               command=lambda v=var: self.pick_any(v)).grid(row=row - 1, column=4, sticky="w", padx=4)
            self.vars[fld.key] = var
            if fld.enabled_by:
                self._dependents.append((w, fld.enabled_by))
            var.trace_add("write", lambda *a: (self._update_dependents(), self._schedule_preview()))
        self._update_dependents()
        self.knob_lbl = None
        if self.type.id == plugin_api.DEFAULT_TYPE and "path" in self.vars:  # Programm / Ordner / Datei
            kf = ttk.Frame(self.fields_frame)
            kf.grid(row=row, column=0, columnspan=4, sticky="w", pady=(10, 0))
            tip(ttk.Button(kf, text="Drehknopf ...", command=self.edit_knob),
                "Belegung des Drehknopfs, solange dieses Programm im Vordergrund ist.").pack(side="left")
            self.knob_lbl = ttk.Label(kf, foreground=DIM, wraplength=self.wrap - 110, justify="left")
            self.knob_lbl.pack(side="left", padx=(8, 0))
            self.vars["path"].trace_add("write", lambda *a: self._update_knob_label())
            self._update_knob_label()

    # ---------------------------------------------------------- Drehknopf dieser Kachel
    def _knob_app(self):
        return winctl.knob_app(self._value(self.vars["path"]).strip().strip('"')) if "path" in self.vars else None

    def _knob_preset(self, app):
        """Belegung des Programms: in diesem Fenster geändert, sonst gespeichert; None = Standard."""
        if app in self.knob_changes:
            return self.knob_changes[app] or None
        return knob.settings(self.win.cfg)["apps"].get(app)

    def _update_knob_label(self):
        if not self.knob_lbl:
            return
        app = self._knob_app()
        if not app:
            self.knob_lbl.configure(text="(erst Programm, Ordner oder Datei wählen)")
            return
        p = self._knob_preset(app)
        if not p:
            text = f"{app}: Standard (Lautstärke)"
        else:
            text = f"{app}: drehen = {KnobDialog._label_of(knob.ROT_ACTIONS, p['rot'])}, drücken = " \
                   f"{KnobDialog._label_of(knob.PRESS_ACTIONS, p['press'])}"
        self.knob_lbl.configure(text=text)

    def edit_knob(self):
        app = self._knob_app()
        if not app:
            messagebox.showinfo("Drehknopf", "Bitte zuerst ein Programm, einen Ordner oder eine Datei wählen.",
                                parent=self)
            return

        def apply(p):
            self.knob_changes[app] = p
            self._update_knob_label()
        KnobDialog(self.winfo_toplevel(), app, self._knob_preset(app), self.win.cfg, apply)

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
        self.on_done("clear")

    def ok(self):
        t = self._tile()
        if self.type.is_valid(t):
            try:
                t = self.type.prepare(t)
            except Exception:
                logging.getLogger("pcdeck").exception("Fehler beim Speichern der Kachel")
            self.on_done(t)
        else:
            self.on_done("clear")


class TileDialog(tk.Toplevel):
    """Kachel bearbeiten in einem eigenen Fenster (TileEditor); _place_over für alle Dialoge."""
    _place_over = staticmethod(_place_over)

    def __init__(self, parent, tile, win=None):
        super().__init__(parent)
        self.title("Kachel bearbeiten")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.knob_changes = {}

        def done(res):
            self.result, self.knob_changes = res, ed.knob_changes
            self.destroy()
        ed = TileEditor(self, tile, win or parent, done, wrap=460)
        ed.pack(fill="both", expand=True)
        _place_over(self, parent)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()


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
class _Pane(ttk.Frame):
    """Inhalt im rechten Feld des Hauptfensters (statt eigenem Fenster). Die Fenster-Aufrufe der früheren
    Dialoge (title, transient, grab_set, wait_window ...) wirken hier nicht bzw. setzen die Überschrift."""

    def __init__(self, win):
        super().__init__(win.side, padding=(16, 12))
        self.win = win
        self._head = ttk.Label(self, font=("Segoe UI Semibold", 11))
        self._head.pack(anchor="w", pady=(0, 6))
        self.pack(fill="both", expand=True)
        win.pane = self

    def title(self, text=None):
        if text is not None:
            self._head.configure(text=text)
        return self._head.cget("text")

    def transient(self, *a):
        pass

    def resizable(self, *a):
        pass

    def grab_set(self):
        pass

    def wait_window(self, *a):
        pass

    def protocol(self, *a):
        pass

    def geometry(self, *a):
        pass

    def changed(self):
        """Offene Änderungen? (für die Nachfrage beim Wechsel)"""
        return False

    def accept(self):
        """Wie OK."""
        (getattr(self, "_ok", None) or getattr(self, "close"))()

    def destroy(self):
        super().destroy()
        self.win._pane_closed(self)


class _OkCancel:
    """OK übernimmt (apply: speichern und sofort ans Deck), Abbrechen verwirft alle Änderungen.
    Die Unterklasse liefert den Wert mit _current(strict) (None = Eingabe ungültig, Fenster bleibt offen)."""

    def _ok_buttons(self, frame, apply):
        self._apply = apply
        ttk.Button(frame, text="Abbrechen", command=self._cancel).pack(side="left", padx=4)
        ttk.Button(frame, text="OK", style="Accent.TButton", command=self._ok).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())
        self.after_idle(self._snapshot)

    def _snapshot(self):
        try:
            self._start = self._current(False)
        except Exception:
            self._start = None

    def changed(self):
        try:
            return self._current(False) != getattr(self, "_start", None)
        except Exception:
            return False

    def _ok(self):
        cur = self._current(True)
        if cur is None:
            return
        self._ended(True)
        self._apply(cur)
        self.destroy()

    def _cancel(self):
        self._ended(False)
        self.destroy()

    def _ended(self, ok):
        """Für Unterklassen: Fenster wird mit OK (True) bzw. Abbrechen (False) geschlossen."""


class AutoDialog(_OkCancel, _Pane):
    """Programme, bei denen das Deck automatisch auf diese Seite springt."""

    def __init__(self, parent, page_no, apps, elsewhere=None, apply=None):
        """elsewhere: {programm_klein: seitennummer} – schon auf einer anderen Seite eingetragen.
        apply(liste): bei OK (speichern und sofort ans Deck)."""
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
        ttk.Label(f, text=f"Kommt eines dieser Programme in den Vordergrund, zeigt das Deck Seite {page_no}. "
                          "Verlässt du es, springt das Deck auf die zuletzt selbst gewählte Seite zurück.",
                  justify="left", wraplength=360).pack(anchor="w")
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
        ttk.Label(f, text="Weitere Programme (laufen gerade nicht), durch Komma getrennt, z. B. LightBurn.exe:",
                  wraplength=360, justify="left").pack(anchor="w", pady=(10, 2))
        if elsewhere:
            ttk.Label(f, foreground=DIM, text="Ein Programm kann nur auf einer Seite stehen – "
                      "hier eingetragen, wird es auf der anderen Seite entfernt.", wraplength=360,
                      justify="left").pack(anchor="w")
        self.v_other = tk.StringVar(value=", ".join(others))
        ttk.Entry(f, textvariable=self.v_other, width=40).pack(anchor="w", fill="x")

        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(14, 0))
        self._ok_buttons(b, apply)
        TileDialog._place_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _current(self, strict=False):
        apps = [a for a, v in self.checks if v.get()]
        for a in self.v_other.get().replace(";", ",").split(","):
            a = a.strip().strip('"')
            if a and not a.lower().endswith(".exe"):
                a += ".exe"
            if a and a.lower() not in {x.lower() for x in apps}:
                apps.append(a)
        return apps


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
        tk.Label(self.win, text=self.text, justify="left", wraplength=420, bg=C["field"], fg=C["text"],
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
  • Änderungen gehen sofort ans Deck – in Fenstern wie „Kachel bearbeiten“ mit „OK“.

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
  • Standard: drehen = Lautstärke, gedrückt drehen = Ausgabegerät, kurz drücken = stumm,
    lang drücken (1,5 s) = herunterfahren, zweimal schnell = Energie sparen – beide mit 10 s
    Countdown am Deck, Drücken bricht ab.
  • Eigene Belegung je Programm: Kachel bearbeiten → „Drehknopf ...“ – gilt, solange das
    Programm im Vordergrund ist.

Tipp: Bleibt die Maus kurz über einer Schaltfläche stehen, erscheint eine Erklärung."""


# ======================================================================= Drehknopf-Dialog
KNOB_HELP = """Der Drehknopf am Deck kennt vier Bewegungen:
  • Drehen
  • Gedrückt drehen (Knopf festhalten und drehen)
  • Kurz drücken
  • Lang drücken (ab 1,5 Sekunden, das Deck zeigt dann einen roten Rahmen)

Standard (immer, wenn kein Programm eine eigene Belegung hat):
  Drehen = Lautstärke, gedrückt drehen = Ausgabegerät wechseln, kurz drücken = Ton stumm,
  lang drücken = Herunterfahren (10 s Countdown am Deck, Drücken bricht ab),
  zweimal schnell drücken = Energie sparen (ebenfalls 10 s Countdown, Drücken bricht ab).

Eigene Belegung für ein Programm stellst du in dessen Kachel ein („Drehknopf ...“).
Sie gilt, solange dieses Programm im Vordergrund ist
(z. B. Discord: drehen = Discord-Lautstärke, drücken = Mikrofon stumm;
Edge: drehen = scrollen, drücken = Screenshot).

Hinweise:
  • Mehrere Kacheln desselben Programms teilen sich die Belegung.
  • Ordner-Kacheln: die Belegung gilt für alle Explorer-Fenster.
  • „Lautstärke des Programms“ verstellt nur den Regler dieses Programms im
    Windows-Lautstärkemischer – andere Programme bleiben gleich laut.
    Das Programm muss dafür gerade Ton ausgeben bzw. im Mischer stehen.
  • Scrollen und Zoomen wirken auf das Fenster unter dem Mauszeiger.
  • Bei Lautstärke, Ausgabegerät und Seite zeigt das Deck kurz den neuen Wert an."""


class KnobDialog(_OkCancel, tk.Toplevel):
    """Drehknopf-Belegung für ein Programm (aus dem Kachel-Fenster): Standard oder eigene Belegung.
    apply(preset oder None) bei OK; None = Standard (keine eigene Belegung)."""

    def __init__(self, parent, app, preset, deck_cfg, apply):
        super().__init__(parent)
        self.title(f"Drehknopf – {app}")
        self.transient(parent)
        self.resizable(False, False)
        self.deck_cfg = deck_cfg
        self.p = knob.preset(preset)
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
        ttk.Label(head, text=f"Solange {app} im Vordergrund ist:").pack(side="left")
        tip(ttk.Button(head, text="?", width=3, command=self._help), "Ausführliche Erklärung zum Drehknopf").pack(
            side="right")
        self.v_own = tk.BooleanVar(value=preset is not None)
        ttk.Radiobutton(f, text="Standard (drehen = Lautstärke, gedrückt drehen = Ausgabegerät, drücken = stumm, "
                        "lang = herunterfahren, doppelt = Energie sparen)", variable=self.v_own, value=False,
                        command=self._update).pack(anchor="w", pady=(8, 0))
        ttk.Radiobutton(f, text="Eigene Belegung:", variable=self.v_own, value=True,
                        command=self._update).pack(anchor="w")
        hint = "Gilt für alle Kacheln dieses Programms."
        if app.lower() == "explorer.exe":
            hint += " Bei Ordnern: für alle Explorer-Fenster."
        ttk.Label(f, text=hint, foreground=DIM).pack(anchor="w", padx=(22, 0))

        right = ttk.Frame(f, padding=(22, 6, 0, 0))
        right.pack(fill="both", expand=True)
        self.v = {}
        self.main_rows = []
        rot_labels = [t for _, t in knob.ROT_ACTIONS]
        press_labels = [t for _, t in knob.PRESS_ACTIONS]
        rows = (("rot", "Drehen:", rot_labels, "Was beim Drehen passiert (je Raste)."),
                ("rotp", "Gedrückt drehen:", rot_labels,
                 "Knopf gedrückt halten und drehen – eine zweite Belegung, z. B. Ausgabegerät wechseln.\n"
                 "Das Loslassen danach zählt nicht als Drücken."),
                ("press", "Kurz drücken:", press_labels, "Knopf kurz drücken und loslassen."),
                ("long", "Lang drücken:", press_labels, "Knopf mindestens 1,5 Sekunden drücken (roter Rahmen am Deck), dann loslassen."),
                ("double", "Doppelt drücken:", press_labels,
                 "Zweimal schnell kurz drücken (innerhalb von 0,4 s). Ist hier etwas belegt, wartet ein einfacher "
                 "Druck kurz, ob ein zweiter kommt."))
        for r, (key, text, values, help_text) in enumerate(rows):
            lab = tip(ttk.Label(right, text=text), help_text)
            lab.grid(row=r, column=0, sticky="w", pady=2)
            v = tk.StringVar()
            cb = ttk.Combobox(right, textvariable=v, values=values, state="readonly", width=40)
            cb.grid(row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e: self._update())
            tip(cb, help_text)
            self.v[key] = v
            self.main_rows.append((lab, cb))

        extra = ttk.LabelFrame(right, text="Einstellungen dazu", padding=8)
        extra.grid(row=5, column=0, columnspan=2, sticky="we", pady=(10, 0))
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
                                      ("keys_long", "Lang drücken:", "Tastenkürzel beim langen Drücken."),
                                      ("keys_double", "Doppelt drücken:", "Tastenkürzel beim Doppeldruck.")):
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
        for key, label in (("tile_press", "Kachel kurz:"), ("tile_long", "Kachel lang:"),
                           ("tile_double", "Kachel doppelt:")):
            self.v[key] = tk.StringVar()
            row(key, label, lambda m, k=key: ttk.Combobox(m, textvariable=self.v[k], values=tile_labels,
                                                         state="readonly", width=40),
                "Diese Kachel wird ausgelöst, als hättest du sie am Deck angetippt.")

        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(14, 0))
        self._ok_buttons(b, apply)
        self._load()
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

    def _help(self):
        messagebox.showinfo("Drehknopf – so funktioniert's", KNOB_HELP, parent=self)

    @staticmethod
    def _label_of(actions, key):
        return next((t for k, t in actions if k == key), actions[-1][1])

    @staticmethod
    def _key_of(actions, label):
        return next((k for k, t in actions if t == label), "off")

    def _load(self):
        p = self.p
        self.v["rot"].set(self._label_of(knob.ROT_ACTIONS, p["rot"]))
        self.v["rotp"].set(self._label_of(knob.ROT_ACTIONS, p["rotp"]))
        self.v["press"].set(self._label_of(knob.PRESS_ACTIONS, p["press"]))
        self.v["long"].set(self._label_of(knob.PRESS_ACTIONS, p["long"]))
        self.v["double"].set(self._label_of(knob.PRESS_ACTIONS, p["double"]))
        self.v["step"].set(int(p["step"]))
        self.v["app"].set(p["app"])
        for k in ("keys_left", "keys_right", "keysp_left", "keysp_right", "keys_press", "keys_long", "keys_double"):
            self.v[k].set(p[k])
        names = dict(self.tiles)
        for k in ("tile_press", "tile_long", "tile_double"):
            self.v[k].set(names.get(p[k], "(keine)"))
        self.v["tile_rot"].set(dict(self.rot_tiles).get(p.get("tile_rot", ""), "(keine)"))
        for dev_id, v in self.dev_vars.items():
            v.set(dev_id in (p.get("devices") or []))
        self._update()

    def _store(self):
        p = knob.preset(self.p)
        p.update(rot=self._key_of(knob.ROT_ACTIONS, self.v["rot"].get()),
                 rotp=self._key_of(knob.ROT_ACTIONS, self.v["rotp"].get()),
                 press=self._key_of(knob.PRESS_ACTIONS, self.v["press"].get()),
                 long=self._key_of(knob.PRESS_ACTIONS, self.v["long"].get()),
                 double=self._key_of(knob.PRESS_ACTIONS, self.v["double"].get()),
                 step=int(self.v["step"].get()), app=self.v["app"].get().strip())
        for k in ("keys_left", "keys_right", "keysp_left", "keysp_right", "keys_press", "keys_long", "keys_double"):
            p[k] = self.v[k].get().strip()
        by_label = {t: ref for ref, t in self.tiles}
        for k in ("tile_press", "tile_long", "tile_double"):
            p[k] = by_label.get(self.v[k].get(), "")
        p["tile_rot"] = {t: ref for ref, t in self.rot_tiles}.get(self.v["tile_rot"].get(), "")
        p["devices"] = [i for i, v in self.dev_vars.items() if v.get()]
        p["devices_names"] = {d["id"]: d["name"] for d in self.devices if d["id"] in p["devices"]}
        return p

    @staticmethod
    def _set_state(widgets, on):
        for x in widgets:
            try:
                x.configure(state=("readonly" if isinstance(x, (ttk.Combobox, ttk.Spinbox)) else "normal")
                            if on else "disabled")
            except tk.TclError:
                pass

    def _update(self):
        """Felder nur bei eigener Belegung; Zusatzfelder nur, wenn sie zur gewählten Aktion gehören."""
        own = bool(self.v_own.get())
        for lab, cb in self.main_rows:
            lab.configure(foreground="" if own else DIM)
            self._set_state([cb], own)
        rot = self._key_of(knob.ROT_ACTIONS, self.v["rot"].get())
        rotp = self._key_of(knob.ROT_ACTIONS, self.v["rotp"].get())
        press = self._key_of(knob.PRESS_ACTIONS, self.v["press"].get())
        long = self._key_of(knob.PRESS_ACTIONS, self.v["long"].get())
        double = self._key_of(knob.PRESS_ACTIONS, self.v["double"].get())
        need = {"step": {"volume", "app_volume"} & {rot, rotp}, "app": "app_volume" in (rot, rotp),
                "devices": "output" in (rot, rotp, press, long, double),
                "keys_left": rot == "keys", "keys_right": rot == "keys",
                "keysp_left": rotp == "keys", "keysp_right": rotp == "keys",
                "keys_press": press == "keys", "keys_long": long == "keys", "keys_double": double == "keys",
                "tile_press": press == "tile", "tile_long": long == "tile", "tile_double": double == "tile",
                "tile_rot": "tile" in (rot, rotp)}
        for name, (lab, w) in self.extra_rows.items():
            on = own and bool(need.get(name))
            lab.configure(foreground="" if on else DIM)
            self._set_state(w.winfo_children() if name == "devices" else [w], on)

    def _current(self, strict=False):
        """Eigene Belegung (geprüft) bzw. False für Standard; None = Tastenkürzel ungültig."""
        if not self.v_own.get():
            return False
        p = self._store()
        for k in ("keys_left", "keys_right", "keysp_left", "keysp_right", "keys_press", "keys_long", "keys_double"):
            try:
                winkeys.parse_sequence(p.get(k, ""))
            except Exception as e:
                if strict:
                    messagebox.showerror("Drehknopf", f"Tastenkürzel nicht erkannt: {p[k]}\n\n{e}", parent=self)
                return None
        return p


# ======================================================================= Design
class DesignDialog(_OkCancel, _Pane):
    """Design des Decks wählen und anpassen. Jedes Design (auch die festen) merkt sich seine eigenen Änderungen;
    „Auf Standard zurücksetzen“ bringt das gewählte Design auf seine Werkseinstellung. Alles zeigt sich sofort in
    der Mitte, Hintergrund und Farben kurz danach auch am Deck; OK speichert, Abbrechen stellt den alten Stand her."""

    EDITABLE = ("standard", "red", "green", "mono", "custom")

    def __init__(self, win, select=None):
        super().__init__(win)
        self.win = win
        self.title(f"Design – {win.cfg.get('name', '')}")
        self.cfg0 = copy.deepcopy(win.cfg)
        self.work = {}  # Design -> bearbeitete Werte (nur angefasste Designs)
        self._loading = False
        link = win.manager.link(win.deck_id)
        # Vorschau am Deck nur, wenn sich der alte Stand wiederherstellen lässt (nicht bei „Auf dem Deck lassen“)
        self._deck_fn = link.request_theme_preview if link and theme.settings(win.cfg) is not None else None
        self._deck_after = None
        self._deck_sent = None

        f = ttk.Frame(self)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Design des Decks:").pack(anchor="w")
        self.v = tk.StringVar(value=select or theme.design_id(win.cfg))
        self._orig = theme.design_id(win.cfg)
        for i, name in theme.CHOICES:
            ttk.Radiobutton(f, text=name, value=i, variable=self.v, command=self._picked).pack(anchor="w", pady=1)
        self.cur = self.v.get()

        self.none_lbl = ttk.Label(f, foreground=DIM, wraplength=360, justify="left")
        self.ed = ttk.Frame(f)
        self.v_image = tk.StringVar()
        self.v_auto = tk.BooleanVar()
        self.tile_color = "#0a1b4b"
        self.v_sat = tk.IntVar()
        self.v_style = tk.StringVar()
        self.v_bri = tk.IntVar()
        self.v_alpha = tk.IntVar()
        self.v_ibri = tk.IntVar()
        self.v_icon_con = tk.IntVar()
        self.v_isat = tk.IntVar()
        self.v_itint = tk.BooleanVar()
        self.tint_color = theme.ICON_DEFAULT["icon_tint_color"]
        self.v_iamt = tk.IntVar()
        self.ed_title = ttk.Label(self.ed, font=("Segoe UI Semibold", 10))
        self.ed_title.pack(anchor="w")
        ttk.Label(self.ed, text="Bild:").pack(anchor="w", pady=(8, 0))
        ttk.Entry(self.ed, textvariable=self.v_image, width=40).pack(fill="x")
        ttk.Button(self.ed, text="Bild wählen ...", command=self.pick_image).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(self.ed, text="Kachelfarbe automatisch aus dem Bild", variable=self.v_auto).pack(
            anchor="w", pady=(10, 0))
        self._scale(self.ed, "Farbstärke:", self.v_sat, 0, 100)
        self.color_btn = tk.Button(self.ed, text="Eigene Farbe ...", command=self.pick_color, relief="flat",
                                   padx=10, pady=3)
        self.color_btn.pack(anchor="w", pady=(6, 0))
        ttk.Label(self.ed, text="Kachel-Stil:").pack(anchor="w", pady=(10, 0))
        ttk.Combobox(self.ed, state="readonly", textvariable=self.v_style, width=30,
                     values=[n for _, n in theme.STYLES]).pack(anchor="w")
        self._scale(self.ed, "Hintergrund-Helligkeit:", self.v_bri, 10, 70, pad=10)
        self._scale(self.ed, "Kachel-Deckkraft:", self.v_alpha, 40, 100)
        ttk.Label(self.ed, text="Icons (alle Kacheln)", font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(16, 0))
        self._scale(self.ed, "Helligkeit:", self.v_ibri, -50, 50)
        self._scale(self.ed, "Kontrast:", self.v_icon_con, 50, 150)
        self._scale(self.ed, "Farbe:", self.v_isat, 0, 200)
        tr = ttk.Frame(self.ed)
        tr.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(tr, text="Einfärben", variable=self.v_itint).pack(side="left")
        self.tint_btn = tk.Button(tr, text="Farbe ...", command=self.pick_tint, relief="flat", padx=10, pady=2)
        self.tint_btn.pack(side="left", padx=(10, 0))
        self._scale(self.ed, "Stärke:", self.v_iamt, 0, 100)
        self.reset_btn = tip(ttk.Button(self.ed, text="Auf Standard zurücksetzen", command=self.reset),
                             "Dieses Design auf seine Werkseinstellung zurücksetzen (gilt nach OK).")
        self.reset_btn.pack(anchor="w", pady=(14, 0))

        self.hint = ttk.Label(f, foreground=DIM, wraplength=360, justify="left",
                              text="Die Mitte zeigt jede Änderung sofort" + (", das Deck Hintergrund und Farben kurz "
                                   "danach" if self._deck_fn else "") + ". Icons ändern sich am Deck nach OK. "
                                   "Jedes Design merkt sich seine eigenen Einstellungen.")
        self.btns = ttk.Frame(f)
        self._ok_buttons(self.btns, lambda cur: self._save())
        for v in (self.v_image, self.v_auto, self.v_sat, self.v_style, self.v_bri, self.v_alpha, self.v_ibri,
                  self.v_icon_con, self.v_isat, self.v_itint, self.v_iamt):
            v.trace_add("write", self._edited)
        self._load(self.cur)
        if select:
            self._preview()

    @staticmethod
    def _scale(parent, text, var, lo, hi, pad=6):
        r = ttk.Frame(parent)
        r.pack(fill="x", pady=(pad, 0))
        ttk.Label(r, text=text, width=22).pack(side="left")
        ttk.Scale(r, from_=lo, to=hi, variable=var).pack(side="left", fill="x", expand=True)

    # ---------------------------------------------------------- Werte
    def _values_of(self, d):
        return copy.deepcopy(self.work[d]) if d in self.work else theme.design_values(self.win.cfg, d)

    def _load(self, d):
        """Einstellungen des Designs d ins Formular (bzw. Hinweis bei „Auf dem Deck lassen“/„Kein Design“)."""
        self.cur = d
        for w in (self.ed, self.none_lbl, self.hint, self.btns):
            w.pack_forget()
        if d in self.EDITABLE:
            t = self._values_of(d)
            self._loading = True
            self.v_image.set(t.get("image", ""))
            self.v_auto.set(not str(t.get("tile", "auto")).startswith("#"))
            self.tile_color = t.get("tile") if str(t.get("tile", "")).startswith("#") else "#0a1b4b"
            self.v_sat.set(int(t.get("tile_sat", 75)))
            self.v_style.set(dict(theme.STYLES).get(t.get("style", "glass"), "Glas (Verlauf + Glanz)"))
            self.v_bri.set(int(t.get("bg_brightness", 30)))
            self.v_alpha.set(int(t.get("tile_alpha", 80)))
            self.v_ibri.set(int(t.get("icon_bri", 0)))
            self.v_icon_con.set(int(t.get("icon_con", 100)))
            self.v_isat.set(int(t.get("icon_sat", 100)))
            self.v_itint.set(bool(t.get("icon_tint")))
            self.tint_color = t.get("icon_tint_color") or theme.ICON_DEFAULT["icon_tint_color"]
            self.v_iamt.set(int(t.get("icon_tint_amt", 50)))
            self._loading = False
            self.ed_title.configure(text=f"Einstellungen „{dict(theme.CHOICES)[d]}“")
            self.ed.pack(fill="x", pady=(12, 0))
            self._colors()
        else:
            self.none_lbl.configure(text="„Auf dem Deck lassen“ ändert nichts am Deck (z. B. Deck von einem "
                                    "anderen PC). Die Mitte zeigt weiter das gespeicherte Design." if d == "keep" else
                                    "Ohne Design: dunkler Hintergrund, einfarbige Kacheln, kein Startbild.")
            self.none_lbl.pack(anchor="w", pady=(12, 0))
        self.hint.pack(anchor="w", pady=(12, 0))
        self.btns.pack(anchor="e", pady=(12, 0))

    def _form(self):
        """Werte aus dem Formular (auf die Werte des Designs gelegt, z. B. bleibt „mono“ erhalten)."""
        t = self._values_of(self.cur)
        style = next((k for k, n in theme.STYLES if n == self.v_style.get()), "glass")
        t.update({"enabled": True, "image": self.v_image.get().strip().strip('"'),
                  "tile": "auto" if self.v_auto.get() else self.tile_color, "tile_sat": int(self.v_sat.get()),
                  "style": style, "bg_brightness": int(self.v_bri.get()), "tile_alpha": int(self.v_alpha.get()),
                  "icon_bri": int(self.v_ibri.get()), "icon_con": int(self.v_icon_con.get()),
                  "icon_sat": int(self.v_isat.get()), "icon_tint": bool(self.v_itint.get()),
                  "icon_tint_color": self.tint_color, "icon_tint_amt": int(self.v_iamt.get())})
        return t

    def _edited(self, *_):
        if self._loading or self.cur not in self.EDITABLE:
            return
        self.work[self.cur] = self._form()
        self._colors()
        self._preview()

    def _colors(self):
        self.color_btn.configure(bg=self.tile_color, fg="#ffffff", activebackground=self.tile_color,
                                 state="disabled" if self.v_auto.get() else "normal")
        self.tint_btn.configure(bg=self.tint_color, fg="#ffffff", activebackground=self.tint_color,
                                state="normal" if self.v_itint.get() else "disabled")

    def _cfg_with(self):
        """Konfiguration mit allen Änderungen dieses Fensters und dem gewählten Design."""
        c = copy.deepcopy(self.win.cfg)
        for d, vals in self.work.items():
            c = theme.with_design(c, d, vals)
        c["design"] = self.v.get()
        return c

    # ---------------------------------------------------------- Vorschau
    def _picked(self):
        self._load(self.v.get())
        self._preview()

    def _preview(self):
        """Sofort in der Mitte; am Deck 0,4 s nach der letzten Änderung (ohne Startbild und Icons)."""
        c = self._cfg_with()
        self.win.design_preview(None if c["design"] == "keep" else c)
        if self._deck_fn and c["design"] != "keep":
            if self._deck_after:
                self.after_cancel(self._deck_after)
            self._deck_after = self.after(400, self._send_deck)

    def _send_deck(self):
        self._deck_after = None
        t = theme.settings(self._cfg_with())
        if t is not None and t != self._deck_sent:
            self._deck_sent = t
            self._deck_fn(t)

    def _ended(self, ok):
        if self._deck_after:
            self.after_cancel(self._deck_after)
            self._deck_after = None
        if self._deck_sent is not None and not ok:
            self._deck_fn(None)  # Abbrechen: Deck zeigt wieder das gespeicherte Design
            self._deck_sent = None

    # ---------------------------------------------------------- Aktionen
    def reset(self):
        if self.cur in self.EDITABLE:
            self.work[self.cur] = theme.base_settings(self.cur)
            self._load(self.cur)
            self._preview()

    def pick_image(self):
        cur = self.v_image.get()
        start = os.path.dirname(cur) if os.path.isdir(os.path.dirname(cur)) else theme.WALLPAPER_DIR
        p = filedialog.askopenfilename(parent=self, title="Hintergrundbild wählen", initialdir=start,
                                       filetypes=[("Bilder", "*.jpg *.jpeg *.png *.bmp"), ("Alle", "*.*")])
        if p:
            self.v_image.set(os.path.normpath(p))

    def pick_color(self):
        from tkinter import colorchooser
        c = colorchooser.askcolor(color=self.tile_color, parent=self, title="Kachelfarbe")[1]
        if c:
            self.tile_color = c
            self._edited()

    def pick_tint(self):
        from tkinter import colorchooser
        c = colorchooser.askcolor(color=self.tint_color, parent=self, title="Farbe zum Einfärben der Icons")[1]
        if c:
            self.tint_color = c
            self._edited()

    def _current(self, strict=False):
        return self.v.get()

    def changed(self):
        if self.v.get() != self._orig:
            return True
        return any(vals != theme.design_values(self.cfg0, d) for d, vals in self.work.items())

    def _save(self):
        c = self._cfg_with()
        for k in ("design", "design_mods", "theme"):
            if k in c:
                self.win.cfg[k] = c[k]
            else:
                self.win.cfg.pop(k, None)
        self.win.v_design.set(dict(theme.CHOICES)[c["design"]])
        self.win._commit("design", "design_mods", "theme")


# ======================================================================= Anzeige-Dialog
class DisplayDialog(_OkCancel, _Pane):
    """Anzeige am Deck (ab FW 27): Kacheln nacheinander aufbauen, Dimmen bei Nichtbenutzung, Uhr im Ruhebild."""

    def __init__(self, parent, opts, fw, apply):
        super().__init__(parent)
        self.title(f"Anzeige – {parent.cfg.get('name', '')}")
        self.transient(parent)
        self.resizable(False, False)
        self.v_build = tk.BooleanVar(value=bool(opts["build"]))
        self.v_dim = tk.BooleanVar(value=bool(opts["dim"]))
        self.v_min = tk.IntVar(value=int(opts["dim_min"]))
        self.v_pct = tk.IntVar(value=int(opts["dim_pct"]))
        self.v_clock = tk.BooleanVar(value=bool(opts["clock"]))
        self.v_world = tk.BooleanVar(value=bool(opts["clock_world"]))
        self.v_walkers = tk.BooleanVar(value=bool(opts["walkers"]))

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        if fw is not None and fw < 27:
            ttk.Label(f, foreground="#c05000", wraplength=360, justify="left",
                      text=f"Das Deck hat Firmware {fwfile.label(fw)}. Diese Einstellungen wirken erst ab Firmware 1.027 "
                           "(„Firmware ...“ im Hauptfenster).").pack(anchor="w", pady=(0, 8))
        tip(ttk.Checkbutton(f, text="Kacheln beim Seitenwechsel nacheinander aufbauen", variable=self.v_build),
            "Nur ein Effekt: Die Seite ist fertig gerechnet und wird Kachel für Kachel gezeigt.\n"
            "Decks ohne PSRAM (Deck 2) bauen ohnehin so auf.").pack(anchor="w")
        tip(ttk.Checkbutton(f, text="Display dimmen, wenn es nicht benutzt wird", variable=self.v_dim),
            "Nach der eingestellten Zeit ohne Tipper oder Drehknopf wird das Display dunkler.\n"
            "Der erste Tipper weckt nur auf (löst nichts aus), der Drehknopf wirkt sofort.\n"
            "Ein Seitenwechsel durch die Automatik weckt das Display ebenfalls.").pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(f)
        row.pack(anchor="w", padx=(22, 0), pady=(2, 0))
        ttk.Label(row, text="nach").pack(side="left")
        ttk.Spinbox(row, from_=1, to=240, width=4, textvariable=self.v_min).pack(side="left", padx=4)
        ttk.Label(row, text="Minuten auf").pack(side="left")
        ttk.Spinbox(row, from_=1, to=100, width=4, textvariable=self.v_pct).pack(side="left", padx=4)
        ttk.Label(row, text="% Helligkeit").pack(side="left")
        tip(ttk.Checkbutton(f, text="Uhr im Ruhebild", variable=self.v_clock),
            "Ist die App nicht verbunden (PC gesperrt, Standby, Neustart), zeigt das Deck Uhrzeit und Datum "
            "statt des Logos.\nNach einem Stromausfall erst wieder, wenn die App verbunden war.").pack(
            anchor="w", pady=(8, 0))
        st = ttk.Frame(f)
        st.pack(anchor="w", padx=(22, 0), pady=(2, 0))
        tip(ttk.Radiobutton(st, text="Block-Welt", variable=self.v_world, value=True),
            "Landschaft aus Blöcken, Himmel nach Tageszeit, Sonne und Mond wandern über den Himmel "
            "(ab Firmware 1.031).").pack(side="left")
        ttk.Radiobutton(st, text="Schlicht", variable=self.v_world, value=False).pack(side="left", padx=(12, 0))
        tip(ttk.Checkbutton(f, text="Figuren laufen vorbei", variable=self.v_walkers),
            "In der Block-Welt läuft alle paar Minuten eine Figur über das Gras: tagsüber ein Wanderer, "
            "nachts ein Zombie, ein Skelett oder eine Spinne. Ganz selten kämpfen eine Riesenechse und ein "
            "Riesenaffe (ab Firmware 1.035; zum Ausprobieren 5x schnell auf die Uhr tippen).").pack(
                anchor="w", padx=(22, 0), pady=(2, 0))
        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(14, 0))
        self._ok_buttons(b, apply)
        TileDialog._place_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _current(self, strict=False):
        try:
            m, p = int(self.v_min.get()), int(self.v_pct.get())
        except (tk.TclError, ValueError):
            if strict:
                messagebox.showerror("Anzeige", "Bitte Minuten und Prozent als Zahl eingeben.", parent=self)
            return None
        return {"build": bool(self.v_build.get()), "dim": bool(self.v_dim.get()), "dim_min": max(1, min(240, m)),
                "dim_pct": max(1, min(100, p)), "clock": bool(self.v_clock.get()),
                "clock_world": bool(self.v_world.get()), "walkers": bool(self.v_walkers.get())}


# ======================================================================= WLAN-Dialog
class WifiDialog(_Pane):
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
        self.status = ttk.Label(f, text="WLAN-Status wird abgefragt ..." if link else "", wraplength=360,
                                justify="left")
        self.status.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))

        f.columnconfigure(0, weight=1)
        net = ttk.Frame(f)
        net.grid(row=2, column=0, columnspan=3, sticky="we")
        tip(ttk.Label(net, text="Netz:"), "Das WLAN, mit dem sich das Deck verbinden soll.").pack(anchor="w")
        self.v_net = tk.StringVar()
        self.net_box = ttk.Combobox(net, textvariable=self.v_net, width=30)
        self.net_box.pack(fill="x")
        tip(self.net_box, "Auswählen oder Namen eintippen (auch für versteckte Netze). Das Deck kann nur "
                          "2,4-GHz-Netze nutzen.")
        self.scan_btn = tip(ttk.Button(net, text="Netze suchen", command=self.scan),
                            "Das Deck sucht die WLANs in seiner Nähe (dauert ein paar Sekunden).")
        self.scan_btn.pack(anchor="w", pady=(4, 0))

        tip(ttk.Label(net, text="Passwort:"), "WLAN-Passwort – tippst du selbst ein.").pack(anchor="w", pady=(8, 0))
        self.v_pw = tk.StringVar()
        self.pw_entry = ttk.Entry(net, textvariable=self.v_pw, width=30, show="•")
        self.pw_entry.pack(fill="x")
        tip(self.pw_entry, "Das Passwort wird nur an das Deck geschickt und dort gespeichert – "
                           "die App speichert es nicht.")
        self.v_show = tk.BooleanVar()
        ttk.Checkbutton(net, text="Passwort anzeigen", variable=self.v_show,
                        command=lambda: self.pw_entry.configure(show="" if self.v_show.get() else "•")).pack(
            anchor="w", pady=(4, 0))

        ttk.Label(f, foreground=DIM, justify="left", wraplength=360,
                  text="Verbinden & koppeln: Das Deck speichert WLAN und Passwort und bekommt einen geheimen "
                       "Schlüssel von dieser App. Am USB ist das WLAN aus (Strom, Wärme) – nach dem Einrichten "
                       "bleibt es noch 30 s an. Ohne USB (Netzteil, Powerbank) fragt das Deck „WLAN "
                       "einschalten?“; bei „Ja“ findet die App es im WLAN automatisch. Steckt das Deck an einem "
                       "anderen PC mit PC-Deck, werden WLAN und Kopplung im Deck gelöscht.").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        if link and not usb:
            ttk.Label(f, foreground="#c05000", wraplength=360, justify="left",
                      text="Einrichten, Koppeln und Ein-/Ausschalten geht nur, "
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
        b2 = ttk.Frame(f)
        b2.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(b2, text="Schließen", command=self.destroy).pack(side="left")
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
class BoschDialog(_Pane):
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
        self.status = ttk.Label(f, text="", wraplength=360, justify="left")
        self.status.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        f.columnconfigure(0, weight=1)
        conn = ttk.Frame(f)
        conn.grid(row=1, column=0, columnspan=3, sticky="we")
        tip(ttk.Label(conn, text="Controller (IP-Adresse):"),
            "IP-Adresse des Bosch Smart Home Controllers im Heimnetz.").pack(anchor="w")
        hr = ttk.Frame(conn)
        hr.pack(fill="x")
        self.v_host = tk.StringVar(value=boschshc.settings().get("host", ""))
        ttk.Entry(hr, textvariable=self.v_host, width=20).pack(side="left", fill="x", expand=True)
        self.search_btn = tip(ttk.Button(hr, text="Suchen", command=self.search),
                              "Sucht den Controller im eigenen Netz (dauert ein paar Sekunden).")
        self.search_btn.pack(side="left", padx=(6, 0))

        tip(ttk.Label(conn, text="Systempasswort:"), "Das Passwort des Controllers aus der Einrichtung – "
                                                     "tippst du selbst ein.").pack(anchor="w", pady=(8, 0))
        self.v_pw = tk.StringVar()
        self.pw_entry = ttk.Entry(conn, textvariable=self.v_pw, width=30, show="•")
        self.pw_entry.pack(fill="x")
        tip(self.pw_entry, "Wird nur zum Koppeln an den Controller geschickt und nicht gespeichert.")

        ttk.Label(f, foreground=DIM, justify="left", wraplength=360,
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
        b2 = ttk.Frame(f)
        b2.grid(row=6, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(b2, text="Abbrechen", command=self.cancel).pack(side="left", padx=4)
        tip(ttk.Button(b2, text="OK", style="Accent.TButton", command=self.close),
            "Angehakte Geräte übernehmen und sofort ans Deck senden.").pack(side="left")

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
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

    def cancel(self):
        """Geräteauswahl verwerfen (Koppeln/Entkoppeln gilt schon, das lässt sich nicht abbrechen)."""
        self.v_pw.set("")
        self.destroy()


# ======================================================================= Soundboard-Dialog
class SoundboardDialog(_Pane):
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
        self.volumes = dict(sb.get("volumes") or {})  # pfad -> Lautstärke in % (fehlt = 50)
        self._dots = {}

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, foreground=DIM, wraplength=360, justify="left",
                  text="Angehakte Sounds erscheinen auf der Seite „Soundboard“ am Ende dieses Decks (vorne die "
                       "Kachel „Alle stoppen“). Antippen spielt, nochmal antippen stoppt. Klick auf einen Eintrag "
                       "hakt ihn an oder ab, Rechtsklick wählt Farbe des Knopfs (sonst automatisch der Reihe "
                       f"nach) und Lautstärke des Sounds (Standard {SOUND_VOLUME_DEFAULT} %).").pack(fill="x")
        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(mid, columns=("file",), show="tree", height=12, selectmode="none")
        self.tree.column("#0", width=340)
        sb_ = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb_.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb_.pack(side="left", fill="y")
        self.tree.bind("<Button-1>", self._toggle)
        self.tree.bind("<Button-3>", self._color_menu)

        bt = ttk.Frame(f)
        bt.pack(fill="x", pady=(6, 0))
        ttk.Button(bt, text="Sounds suchen ...", command=self.search).pack(side="left")
        ttk.Button(bt, text="▶ / ■", width=6, command=self.listen).pack(side="left", padx=4)
        bt2 = ttk.Frame(f)
        bt2.pack(fill="x", pady=(4, 0))
        ttk.Button(bt2, text="Sound-Ordner öffnen", command=self.open_folder).pack(side="left")
        ttk.Button(bt2, text="Liste aktualisieren", command=self.fill).pack(side="left", padx=4)

        opt = ttk.LabelFrame(f, text="Für alle Soundboard-Kacheln", padding=8)
        opt.pack(fill="x", pady=(10, 0))
        self.v = {}
        rows = (("device", "Ausgabegerät:", sound_plugin._out_choices(), "", 40),
                ("device2", "Zweites Gerät (Discord & Co.):", sound_plugin._out2_choices(), "", 40),
                ("mode", "Wenn schon ein Sound läuft:", sound_plugin.MODES, "overlay", 40))
        self.maps = {}
        for r, (key, label, choices, default, width) in enumerate(rows):
            ttk.Label(opt, text=label).grid(row=2 * r, column=0, sticky="w", pady=(4, 0))
            cur = sb.get(key, default)
            if cur not in {v for v, _ in choices}:
                choices = choices + [(cur, f"{cur} (nicht verbunden)")]
            self.maps[key] = {lbl: v for v, lbl in choices}
            var = tk.StringVar(value=next(lbl for v, lbl in choices if v == cur))
            ttk.Combobox(opt, textvariable=var, state="readonly", width=width,
                         values=[lbl for _, lbl in choices]).grid(row=2 * r + 1, column=0, sticky="w")
            self.v[key] = var
        ttk.Label(opt, text=sound_plugin._cable_hint(), foreground=DIM, wraplength=360, justify="left").grid(
            row=2 * len(rows), column=0, columnspan=2, sticky="w", pady=(4, 0))

        b = ttk.Frame(f)
        b.pack(fill="x", pady=(12, 0))
        tip(ttk.Button(b, text="OK", command=self.close), "Übernehmen und sofort ans Deck senden.").pack(side="right")
        ttk.Button(b, text="Abbrechen", command=self.cancel).pack(side="right", padx=4)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
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
            name = os.path.splitext(os.path.basename(p))[0]
            self.tree.insert("", "end", iid=p, text=f"{mark}  {name}   ({self._volume(p)} %)",
                             image=self._dot(self._color(p, on)))

    def _volume(self, p):
        return self.volumes.get(p, SOUND_VOLUME_DEFAULT)

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
        mc = tk.Menu(m, tearoff=0)
        mc.add_command(label="automatisch", command=lambda: self._set_color(p, ""))
        mc.add_separator()
        for name, hexcol in self.sp.PALETTE:
            mc.add_command(label=name, image=self._dot(hexcol), compound="left",
                           command=lambda n=name: self._set_color(p, n))
        m.add_cascade(label="Farbe", menu=mc)
        mv = tk.Menu(m, tearoff=0)
        cur = self._volume(p)
        for v, label in self.sp.VOLUMES:
            mv.add_command(label=("• " if v == cur else "   ") + label, command=lambda v=v: self._set_volume(p, v))
        m.add_cascade(label=f"Lautstärke ({cur} %)", menu=mv)
        m.tk_popup(ev.x_root, ev.y_root)

    def _set_volume(self, p, v):
        if v == SOUND_VOLUME_DEFAULT:
            self.volumes.pop(p, None)
        else:
            self.volumes[p] = v
        y = self.tree.yview()[0]
        self.fill()
        self.tree.yview_moveto(y)

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
                soundplayer.preview(fh.read(), volume=int(self._volume(p)) / 100)
        except Exception as e:
            messagebox.showerror("PC-Deck", f"Abspielen fehlgeschlagen:\n{e}", parent=self)

    def search(self):
        def downloaded(path, name):  # neu heruntergeladen -> sofort aufs Soundboard
            if path not in self.checked:
                self.checked.append(path)
            if self.winfo_exists():
                self.fill()
        SoundSearchDialog(self.winfo_toplevel(), on_download=downloaded)

    def open_folder(self):
        os.makedirs(self.ss.SOUND_DIR, exist_ok=True)
        os.startfile(self.ss.SOUND_DIR)

    def cancel(self):
        """Nichts übernehmen (heruntergeladene Dateien bleiben im Sound-Ordner)."""
        import soundplayer
        soundplayer.stop(soundplayer.PREVIEW)
        self.destroy()

    def close(self):
        import soundplayer
        soundplayer.stop(soundplayer.PREVIEW)
        sb = {"sounds": [p for p in self.checked if os.path.isfile(p)]}
        sb["colors"] = {p: c for p, c in self.colors.items() if p in sb["sounds"]}
        sb["volumes"] = {p: v for p, v in self.volumes.items() if os.path.isfile(p)}
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
class FirmwareDialog(_Pane):
    """Firmware des Decks über die bestehende USB-Verbindung aktualisieren (ab FW 16)."""

    def __init__(self, parent, manager, deck_id, auto=False):
        super().__init__(parent)
        self.title("Firmware des Decks")
        self.transient(parent)
        self.resizable(False, False)
        self.manager, self.deck_id = manager, deck_id
        self.auto = auto  # nach der Abfrage: mitgelieferte Firmware ohne weitere Nachfrage aufspielen
        self.results = queue.Queue()
        self.busy = False
        self.deck_type, self.slot = None, 0
        self.bundled = fwfile.bundled()
        link = manager.link(deck_id)

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        via = "nicht verbunden" if not link else (f"über {link.port}" if link.via_wlan else f"über USB ({link.port})")
        f.columnconfigure(0, weight=1)
        self.deck_lbl = ttk.Label(f, text=f"Deck: Firmware {fwfile.label(link.fw) if link else '?'}, {via}", wraplength=360,
                                  justify="left")
        self.deck_lbl.grid(row=0, column=0, columnspan=2, sticky="w")
        self.bundle_lbl = ttk.Label(f, text="", wraplength=360, justify="left")
        self.bundle_lbl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.bar = ttk.Progressbar(f, length=200, maximum=1000)
        self.bar.grid(row=2, column=0, columnspan=2, sticky="we", pady=(12, 4))
        self.status = ttk.Label(f, text="", wraplength=360, justify="left")
        self.status.grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(f, foreground=DIM, justify="left", wraplength=360,
                  text="Die neue Firmware wird über USB (ab Firmware 1.018 auch über WLAN) übertragen und in den "
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
        b2 = ttk.Frame(f)
        b2.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.close_btn = ttk.Button(b2, text="Schließen", command=self.close)
        self.close_btn.pack(side="left")
        for w in (self.bundle_btn, self.file_btn):
            w.configure(state="disabled")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        TileDialog._place_over(self, parent)
        self.after(100, self._poll)
        if not link:
            self.status.configure(text="Das Deck ist nicht verbunden.")
        elif link.fw < 16:
            self.status.configure(text=f"Das Deck hat Firmware {fwfile.label(link.fw)}. Das Update aus der App geht erst ab "
                                       "Firmware 1.016 – einmalig per USB mit PlatformIO aufspielen (siehe Anleitung).")
        elif link.via_wlan and link.fw < 18:
            self.status.configure(text=f"Das Deck hat Firmware {fwfile.label(link.fw)}. Update über WLAN geht erst ab Firmware 1.018 – "
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
        self.deck_lbl.configure(text=f"Deck: Firmware {fwfile.label(parts[1])}, {fwfile.type_name(self.deck_type)}, "
                                     f"über USB ({link.port if link else '?'})")
        b = self.bundled.get(self.deck_type)
        self.bundle_lbl.configure(text=f"Mitgeliefert: Firmware {fwfile.label(b[0])}" if b else
                                  "Mitgeliefert: keine Firmware für diesen Deck-Typ")
        self.bundle_btn.configure(state="normal" if b else "disabled")
        self.file_btn.configure(state="normal")
        self.status.configure(text="Bereit." if not b or b[0] > int(parts[1]) else
                              "Das Deck ist schon auf dem Stand der mitgelieferten Firmware.")
        if self.auto and b and b[0] > int(parts[1]):
            self.auto = False
            self._flash(b[1], confirm=False)

    def flash_bundled(self):
        self._flash(self.bundled[self.deck_type][1])

    def flash_file(self):
        path = filedialog.askopenfilename(parent=self, title="Firmware wählen",
                                          filetypes=[("Firmware", "*.bin"), ("Alle Dateien", "*.*")])
        if path:
            self._flash(path)

    def _flash(self, path, confirm=True):
        link = self.manager.link(self.deck_id)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            messagebox.showerror("PC-Deck", f"Datei nicht lesbar:\n{e}", parent=self)
            return
        inf = fwfile.info(data)
        if not inf:
            messagebox.showerror("PC-Deck", "Das ist keine PC-Deck-Firmware (ab Firmware 1.016).", parent=self)
            return
        if inf[1] != self.deck_type:
            messagebox.showerror("PC-Deck", f"Die Datei ist für {fwfile.type_name(inf[1])}, das Deck ist "
                                            f"{fwfile.type_name(self.deck_type)}.", parent=self)
            return
        if self.slot and len(data) > self.slot:
            messagebox.showerror("PC-Deck", "Die Firmware ist zu groß für den Programm-Platz des Decks.", parent=self)
            return
        if not link or (link.via_wlan and link.fw < 18):
            messagebox.showerror("PC-Deck", "Das Deck ist nicht per USB verbunden (über WLAN erst ab Firmware 1.018).",
                                 parent=self)
            return
        if inf[0] <= link.fw and not messagebox.askyesno(
                "PC-Deck", f"Das Deck hat schon Firmware {fwfile.label(link.fw)}, die Datei ist Firmware {fwfile.label(inf[0])}. "
                           "Trotzdem aufspielen?", parent=self):
            return
        hint = ("Deck während der Übertragung eingeschaltet und im WLAN lassen." if link.via_wlan else
                "USB-Kabel während der Übertragung nicht abziehen.")
        if confirm and not messagebox.askyesno("PC-Deck", f"Firmware {fwfile.label(inf[0])} ({len(data):,} Byte) jetzt aufspielen?".replace(",", ".")
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
        self._save_after = None  # gebündeltes Speichern nach einer Änderung (_mark_dirty)
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

    def _nav(self, parent, text, cmd, tip_text, key=None):
        b = tip(ttk.Button(parent, text=text, style="Nav.TButton", command=cmd), tip_text)
        b.pack(fill="x", pady=1)
        if key:
            self.nav_buttons[key] = b
        return b

    def _build(self):
        apply_theme(self)
        self.configure(bg=C["bg"])
        self.nav_buttons = {}
        head = ttk.Frame(self, style="Panel.TFrame", padding=(14, 8))
        head.pack(fill="x")
        ttk.Label(head, text="PC-Deck", style="Head.TLabel").pack(side="left")
        self.status = ttk.Label(head, text="", style="PanelDim.TLabel")
        self.status.pack(side="right")

        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True)

        # ---- links: Deck und Bereiche (scrollt bei Bedarf)
        self.nav_scroll = AutoScroll(self.paned, horizontal=False)
        nav = ttk.Frame(self.nav_scroll.inner, style="Panel.TFrame", padding=(8, 10))
        nav.pack(fill="both", expand=True)
        self.deck_box = ttk.Combobox(nav, state="readonly", width=24)
        self.deck_box.pack(fill="x", padx=2, pady=(0, 4))
        self.deck_box.bind("<<ComboboxSelected>>", self.on_deck_selected)
        tip(self.deck_box, "Welches Deck du bearbeitest. Jedes Deck (erkannt an seiner Chip-ID) hat ein eigenes "
                           "Layout und Design.")
        dr = ttk.Frame(nav, style="Panel.TFrame")
        dr.pack(fill="x", pady=(0, 10))
        tip(ttk.Button(dr, text="Umbenennen", command=self.rename_deck),
            "Namen des Decks ändern (Name im Netzwerk: PC-Deck-<Name>).").pack(side="left", padx=2)
        tip(ttk.Button(dr, text="Entfernen", command=self.remove_deck),
            "Deck samt Layout aus der Konfiguration löschen. Steckst du es wieder an, "
            "erscheint es leer neu.").pack(side="left", padx=2)
        self._nav(nav, "▦   Kacheln", lambda: None, "Kacheln dieses Decks: anklicken zum Bearbeiten, "
                  "Programme und Ordner aus dem Explorer draufziehen.", "tiles")
        self.nav_buttons["tiles"].configure(style="NavOn.TButton")
        self.auto_btn = self._nav(nav, "⟳   Automatik", self.edit_auto,
                                  "Kommt eines der eingetragenen Programme in den Vordergrund, zeigt das Deck "
                                  "die angezeigte Seite. Verlässt du es, springt das Deck zurück.", "auto")
        self._nav(nav, "♫   Soundboard", self.edit_soundboard,
                  "Sounds anhaken: daraus entsteht die Seite „Soundboard“ am Ende des Decks.", "sound")
        self._nav(nav, "⌂   SmartHome", self.edit_smarthome,
                  "Bosch Smart Home Controller koppeln: Kacheln für Rollladen und Licht.", "home")
        ttk.Label(nav, text="Deck", style="PanelDim.TLabel").pack(anchor="w", padx=10, pady=(14, 2))
        self._nav(nav, "◐   Design", self.edit_design, "Aussehen des Decks: fertige Designs oder eigenes.",
                  "design")
        self._nav(nav, "☀   Anzeige", self.edit_display,
                  "Kacheln nacheinander aufbauen, dimmen bei Nichtbenutzung, Uhr im Ruhebild.", "display")
        self._nav(nav, "≋   WLAN", self.edit_wifi, "Deck per WLAN anbinden (einmal per USB einrichten).", "wifi")
        self._nav(nav, "⬆   Firmware", self.edit_firmware, "Firmware des Decks aktualisieren.", "fw")
        self._nav(nav, "✛   Touch kalibrieren", self.calibrate,
                  "Am Deck die Ecken der Reihe nach antippen, wenn Tipper an der falschen Stelle ankommen.",
                  "touch")
        bot = ttk.Frame(nav, style="Panel.TFrame")
        bot.pack(fill="x", pady=(18, 0))
        ttk.Label(bot, text="Helligkeit", style="PanelDim.TLabel").pack(anchor="w", padx=10)
        self.v_bri = tk.IntVar()
        sc = ttk.Scale(bot, from_=10, to=255, variable=self.v_bri)
        sc.pack(fill="x", padx=10, pady=(2, 10))
        sc.bind("<ButtonRelease-1>", lambda e: self._live_brightness())
        tip(sc, "Hintergrundbeleuchtung des Displays – gilt beim Loslassen sofort.")
        self._nav(bot, "?   Hilfe", self.show_help, "Kurze Anleitung zu Deck, Kacheln und Drehknopf", "help")
        self.v_design = tk.StringVar()

        # ---- rechts: Kachel-Eigenschaften und Bereiche (Breite mit dem Trenner einstellbar)
        self.side_scroll = AutoScroll(self.paned, horizontal=True)
        self.side = self.side_scroll.inner
        self.editor = None
        self.selected = None
        self.pane = None           # Inhalt eines Bereichs (Automatik, Soundboard ...) im rechten Feld
        self.pane_key = None
        self._clearing = False
        self._design_override = None  # Vorschau eines angeklickten Designs im Abbild

        # ---- Mitte: Werkzeuge, Abbild des Decks (wächst mit dem Fenster)
        mid = ttk.Frame(self.paned, padding=(18, 12))
        self.mid = mid
        self.paned.add(self.nav_scroll, weight=0)
        self.paned.add(mid, weight=1)
        self.paned.add(self.side_scroll, weight=0)
        self.scale = 1.5
        self._base = None
        tools = ttk.Frame(mid)
        tools.pack(fill="x", pady=(0, 10))
        ttk.Button(tools, text="◀", width=3, command=lambda: self.goto_page(-1)).pack(side="left")
        self.page_label = ttk.Label(tools, width=12, anchor="center")
        self.page_label.pack(side="left")
        ttk.Button(tools, text="▶", width=3, command=lambda: self.goto_page(1)).pack(side="left")
        tip(ttk.Button(tools, text="+ Seite", command=self.add_page),
            "Neue leere Seite hinter der angezeigten (bis zu 8).").pack(side="left", padx=(12, 2))
        tip(ttk.Button(tools, text="Seite löschen", command=self.del_page),
            "Die angezeigte Seite mit allen Kacheln löschen.").pack(side="left", padx=2)
        row2 = ttk.Frame(mid)
        row2.pack(fill="x", pady=(0, 10))
        self.tools2 = row2
        tip(ttk.Label(row2, text="Raster"), "Spalten × Zeilen. Beim Verkleinern werden Kacheln nur "
            "ausgeblendet und kommen beim Vergrößern zurück.").pack(side="left", padx=(0, 4))
        self.v_cols = tk.IntVar()
        self.sp_cols = ttk.Spinbox(row2, from_=COLS_RANGE[0], to=COLS_RANGE[1], width=3, textvariable=self.v_cols,
                                   command=self.on_grid_changed, state="readonly")
        self.sp_cols.pack(side="left", padx=2)
        tip(ttk.Label(row2, text="×"), "Zeilen (1–3, Big-Deck 1–4)").pack(side="left")
        self.v_rows = tk.IntVar()
        self.sp_rows = ttk.Spinbox(row2, from_=ROWS_RANGE[0], to=ROWS_RANGE[1], width=3, textvariable=self.v_rows,
                                   command=self.on_grid_changed, state="readonly")
        self.sp_rows.pack(side="left", padx=(2, 18))
        self.v_suggest = tk.BooleanVar(value=True)
        tip(ttk.Checkbutton(row2, text="Plus-Kachel", variable=self.v_suggest, command=self.on_suggest),
            "Das Programm im Vordergrund erscheint blass mit „+“ auf dem ersten freien Platz. "
            "Antippen am Deck speichert es dort als Kachel.").pack(side="left")

        self.hint_lbl = ttk.Label(mid, foreground=DIM, wraplength=600, justify="center")
        self.hint_lbl.pack(side="bottom", pady=(10, 0))
        frame = tk.Frame(mid, bg="#0b0b0b", padx=8, pady=8)  # schwarzer Displayrand
        frame.pack(anchor="n")
        self.tools = tools
        self.canvas = tk.Label(frame, bd=0, bg="#000000", cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        if HAVE_DND:
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind("<<Drop>>", self._on_canvas_drop)
        self.hint_lbl.configure(text="Programme, Verknüpfungen oder Ordner aus dem Explorer auf eine Kachel "
                                     "ziehen • Klick = bearbeiten • Rechtsklick = kopieren, einfügen, leeren")
        mid.bind("<Configure>", lambda e: self._fit_later())
        self._show_side_hint()
        self._live_tick()
        self._restore_layout()

    # ---------------------------------------------------------- Größe, Trenner, Abbild skalieren
    def _restore_layout(self):
        """Fenstergröße und Breite des rechten Felds wie beim letzten Mal."""
        ui = self.config_store.data.get("ui") or {}
        self.minsize(960, 600)
        self.geometry(ui.get("geometry") or "1500x880")
        self._side_w = int(ui.get("side_w", 520))
        self.after(150, self._apply_sash)
        self.paned.bind("<ButtonRelease-1>", lambda e: self._remember_layout(), add="+")

    def _apply_sash(self):
        total = self.paned.winfo_width()
        if total < 300:
            self.after(100, self._apply_sash)
            return
        nav_w = max(self.nav_scroll.inner.winfo_reqwidth(), 190)
        try:
            self.paned.sashpos(0, nav_w)
            self.paned.sashpos(1, max(nav_w + 300, total - self._side_w))
        except tk.TclError:
            pass

    def _remember_layout(self):
        try:
            self._side_w = self.paned.winfo_width() - self.paned.sashpos(1)
        except tk.TclError:
            return
        with self.config_store.lock:
            self.config_store.data["ui"] = {"geometry": self.geometry(), "side_w": int(self._side_w)}
        self._fit_later()

    def _fit_later(self):
        if getattr(self, "_fit_after", None):
            self.after_cancel(self._fit_after)
        self._fit_after = self.after(60, self._fit)

    def _fit(self):
        """Abbild so groß wie der Platz in der Mitte erlaubt (ganzes Display, Seitenverhältnis 3:2)."""
        self._fit_after = None
        if not self.winfo_exists():
            return
        w = self.mid.winfo_width() - 36 - 16
        h = (self.mid.winfo_height() - 24 - 16 - self.tools.winfo_reqheight() - 10 - self.tools2.winfo_reqheight()
             - 10 - self.hint_lbl.winfo_reqheight() - 10)
        self.hint_lbl.configure(wraplength=max(200, w))
        dw, dh = deckview.size(self.cfg)
        sc = max(0.4 * deckview.W / dw, min(w / dw, h / dh))
        if abs(sc - self.scale) > 0.01:
            self.scale = sc
            self._show_image()

    def _load_deck_ui(self):
        ids = self._deck_ids()
        self.deck_box["values"] = [self._deck_title(i) for i in ids]
        self.deck_box.current(ids.index(self.deck_id))
        (c0, c1), (r0, r1) = grid_ranges(self.cfg)  # Big-Deck: bis 6 x 4
        self.sp_cols.configure(from_=c0, to=c1)
        self.sp_rows.configure(from_=r0, to=r1)
        self.v_cols.set(self.cfg["cols"])
        self.v_rows.set(self.cfg["rows"])
        self.v_bri.set(self.cfg.get("brightness", 200))
        self.v_suggest.set(self.cfg.get("suggest", True))
        self.v_design.set(dict(theme.CHOICES)[theme.design_id(self.cfg)])
        self.page = min(self.page, len(self.cfg["pages"]) - 1)
        self.dirty = False
        self.render()
        self._fit_later()  # anderes Deck: evtl. andere Displaygröße (Big-Deck)
        self.after(400, lambda: self.winfo_exists() and self._knob_orphans())  # alte Belegungen ohne Kachel?

    def refresh_status(self):
        """Wird von außen aufgerufen, wenn Decks kommen oder gehen."""
        if not self.winfo_exists():
            return
        ids = self._deck_ids()
        self.deck_box["values"] = [self._deck_title(i) for i in ids]
        if self.deck_id in ids:
            self.deck_box.current(ids.index(self.deck_id))

    # ---------------------------------------------------------- Raster zeichnen
    def _view_cfg(self):
        """Konfiguration so, wie das Deck sie zeigt (mit Soundboard-/SmartHome-Seiten)."""
        return add_extra_pages(copy.deepcopy(self.cfg))

    def render(self):
        view = self._view_cfg()
        if self._design_override is not None:  # Design-Bereich: Vorschau der Einstellungen
            view = add_extra_pages(copy.deepcopy(self._design_override))
        link = self.manager.link(self.deck_id)
        live = {}
        if link and link.visible_page == self.page:
            live = {i: v for (p, i), v in dict(getattr(link, "live_view", {})).items() if p == self.page}
        title = (view.get("page_titles") or {}).get(self.page)
        self._base = deckview.render(view, self.page, live=live, title=title, selected=self.selected,
                                     accent=C["accent_rgb"], volume=getattr(link, "vol_sent", None))
        self._show_image()
        self.page_label.configure(text=f"Seite {self.page + 1} / {len(view['pages'])}")
        n = len(auto_apps(self.cfg)[self.page]) if self._own() else 0
        self.auto_btn.configure(text=f"⟳   Automatik ({n})" if n else "⟳   Automatik")
        self._update_status()

    def _show_image(self):
        if self._base is None:
            return
        sc = self.scale
        bw, bh = self._base.size
        self._photo = ImageTk.PhotoImage(self._base.resize((int(bw * sc), int(bh * sc)), Image.LANCZOS))
        self.canvas.configure(image=self._photo)

    def _live_tick(self):
        """Live-Kacheln (Uhr, Lautstärke ...) im Abbild nachführen, wenn das Deck dieselbe Seite zeigt."""
        if not self.winfo_exists():
            return
        link = self.manager.link(self.deck_id)
        ver = (getattr(link, "live_version", 0), link.visible_page, link.vol_sent) if link else None
        if ver != getattr(self, "_live_ver", None):
            self._live_ver = ver
            if link and link.visible_page == self.page:
                self.render()
        self.after(1500, self._live_tick)

    def _slot_from_event(self, x, y):
        i = deckview.slot_at(self._view_cfg(), x / self.scale, y / self.scale)
        if i is not None and i >= 0 and not self._own():
            self._extra_info()
            return None
        return i

    def _on_click(self, ev):
        i = self._slot_from_event(ev.x, ev.y)
        if i == -1:
            self.goto_page(-1)
        elif i == -2:
            self.goto_page(1)
        elif i is not None:
            self.select_tile(i)

    def _on_right_click(self, ev):
        i = self._slot_from_event(ev.x, ev.y)
        if i is not None and i >= 0:
            self.context_menu(ev, i)

    def _on_canvas_drop(self, ev):
        px, py = self.canvas.winfo_pointerxy()
        i = self._slot_from_event(px - self.canvas.winfo_rootx(), py - self.canvas.winfo_rooty())
        if i is not None and i >= 0:
            self.on_drop(ev, i)

    # ---------------------------------------------------------- Kachel-Eigenschaften (rechts)
    def _clear_side(self):
        self._clearing = True
        try:
            for w in self.side.winfo_children():
                w.destroy()
        finally:
            self._clearing = False
        self.editor = None
        self.pane = None
        self.pane_key = None
        self._design_override = None
        self.side_scroll.top()

    def _set_nav(self, key):
        for k, b in self.nav_buttons.items():
            b.configure(style="NavOn.TButton" if k == key else "Nav.TButton")

    def _leave_pane(self):
        """Vor dem Wechsel: offener Bereich mit Änderungen? False = abgebrochen."""
        p = self.pane
        if p is None or not p.winfo_exists():
            return True
        if isinstance(p, FirmwareDialog) and p.__dict__.get("busy") is True:  # Übertragung läuft
            messagebox.showinfo("PC-Deck", "Bitte warten, bis die Übertragung fertig ist.", parent=self)
            return False
        if not p.changed():
            return True
        r = messagebox.askyesnocancel("PC-Deck", f"Änderungen unter „{p.title()}“ übernehmen?", parent=self)
        if r is None:
            return False
        if r:
            p.accept()
            if self.pane is p and p.winfo_exists():  # z. B. ungültige Eingabe: bleibt offen
                return False
        return True

    def _open_pane(self, key, make):
        """Bereich `key` im rechten Feld zeigen (make() baut ihn)."""
        if not self._leave_editor() or not self._leave_pane():
            return
        self.selected = None
        self._clear_side()
        self._set_nav(key)
        self.render()
        make()
        self.side_scroll.top()
        self.pane_key = key if self.pane is not None else None
        if self.pane is None:  # Bereich hat sich gleich wieder geschlossen
            self._set_nav("tiles")
            self._show_side_hint()

    def _pane_closed(self, pane):
        if self._clearing or pane is not self.pane:
            return
        self.pane = None
        self.pane_key = None
        self._design_override = None
        self._set_nav("tiles")
        self._show_side_hint()
        self.render()

    def design_preview(self, cfg):
        """Design-Bereich: Konfiguration mit den bearbeiteten Design-Werten im Abbild zeigen (None = gespeichert)."""
        self._design_override = cfg
        if getattr(self, "_tp_after", None):
            self.after_cancel(self._tp_after)
        self._tp_after = self.after(60, self.render)

    def show_help(self):
        def make():
            p = _Pane(self)
            p.title("Hilfe")
            ttk.Label(p, text=APP_HELP + f"\n\nPC-Deck {VERSION}\nEinstellungen und Protokoll: {APP_DIR}",
                      wraplength=380, justify="left").pack(anchor="w")
            ttk.Button(p, text="Schließen", command=p.destroy).pack(anchor="e", pady=(12, 0))
        self._open_pane("help", make)

    def _show_side_hint(self):
        self._clear_side()
        f = ttk.Frame(self.side, style="Panel.TFrame", padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Kachel", style="Head.TLabel").pack(anchor="w")
        ttk.Label(f, text="Klick auf eine Kachel im Deck zeigt hier ihre Einstellungen. Mit „OK“ geht die "
                  "Änderung sofort ans Deck.", style="PanelDim.TLabel", wraplength=360, justify="left").pack(
            anchor="w", pady=(8, 0))

    def _leave_editor(self):
        """Vor dem Wechsel: offene Änderungen übernehmen? False = abgebrochen (Wechsel nicht durchführen)."""
        if not self.editor or not self.editor.winfo_exists() or not self.editor.changed():
            return True
        r = messagebox.askyesnocancel("PC-Deck", "Änderungen an der Kachel übernehmen?", parent=self)
        if r is None:
            return False
        if r:
            self.editor.ok()
        return True

    def select_tile(self, i):
        if i == self.selected and self.editor:
            return
        if not self._leave_editor() or not self._leave_pane():
            return
        self._set_nav("tiles")
        self.selected = i
        self._clear_side()
        f = ttk.Frame(self.side, padding=(12, 12))
        f.pack(fill="both", expand=True)
        t = self.cfg["pages"][self.page][i]
        ttk.Label(f, text=f"Kachel {i + 1} auf Seite {self.page + 1}" + (f" – {tile_label(t)}" if t else " (leer)"),
                  font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=12)
        page, idx = self.page, i
        self.editor = TileEditor(f, t, self, lambda res: self._editor_done(page, idx, res))
        self.editor.pack(fill="both", expand=True)
        self.side_scroll.top()
        self.render()

    def _editor_done(self, page, i, res):
        ed = self.editor
        if res is None:  # Abbrechen: Feld wieder mit dem gespeicherten Stand
            self.selected = None
            self._show_side_hint()
            self.render()
            return
        old = self.cfg["pages"][page][i]
        if res == "clear":
            if old and not self._ask_clear_tile(old):
                return
            self.cfg["pages"][page][i] = None
            self._mark_dirty()
            self._knob_orphans([old])
        else:
            self._apply_knob(ed.knob_changes)
            self.cfg["pages"][page][i] = res
            self._mark_dirty()
            if old and old != res:
                self._knob_orphans([old])
        self.selected = None
        self._show_side_hint()
        self.render()

    def _ask_clear_tile(self, t):
        return self._confirm(f"Kachel „{tile_label(t)}“ leeren?")

    def _big_old(self, what):
        """Big-Deck mit Firmware vor 38 verbunden: Hinweis zeigen, True = Bereich nicht öffnen."""
        link = self.manager.link(self.deck_id)
        if not (is_big(self.cfg) and link and link.fw < 38):
            return False
        messagebox.showinfo("PC-Deck", f"{what} kann das Big-Deck erst ab Firmware 1.038 (es hat "
                            f"{fwfile.label(link.fw)}). Die Firmware kommt mit dem grblHAL-Display.", parent=self)
        return True

    def edit_design(self, select=None):
        if self._big_old("Designs"):
            return
        self._open_pane("design", lambda: DesignDialog(self, select))

    def _update_status(self):
        link = self.manager.link(self.deck_id)
        if link:
            txt = f"Deck verbunden über {link.port}" if link.via_wlan else f"Deck verbunden über USB ({link.port})"
            txt += f", Firmware {fwfile.label(link.fw)}"
            if getattr(link, "fw_newer", None):
                txt += f" (Firmware {fwfile.label(link.fw_newer)} verfügbar – „Firmware ...“)"
        else:
            txt = "Deck nicht verbunden – Änderungen werden beim Anstecken übertragen"
        n = sum(len(h) for h in (self.cfg.get("hidden") or []))
        if n:
            txt += f"   •   {n} Kachel(n) ausgeblendet – kommen beim Vergrößern des Rasters zurück"
        self.status.configure(text=txt)

    def _mark_dirty(self):
        """Änderung im Hauptfenster: kurz danach speichern und ans Deck (mehrere schnelle Änderungen gebündelt)."""
        self.dirty = True
        if self._save_after:
            self.after_cancel(self._save_after)
        self._save_after = self.after(500, self.save)

    def _flush_save(self):
        if self._save_after:
            self.after_cancel(self._save_after)
            self._save_after = None
        if self.dirty:
            self.save()

    def _confirm(self, text):
        return messagebox.askokcancel("PC-Deck", text, parent=self)

    # ---------------------------------------------------------- Kacheln
    def _set_tile(self, i, tile):
        self.cfg["pages"][self.page][i] = tile
        self._mark_dirty()
        self.render()

    def _ask_clear(self, i):
        t = self.cfg["pages"][self.page][i]
        return not t or self._confirm(f"Kachel „{tile_label(t)}“ leeren?")

    def _ask_replace(self, i):
        t = self.cfg["pages"][self.page][i]
        return not t or self._confirm(f"Kachel „{tile_label(t)}“ ersetzen?")

    def edit_tile(self, i):
        self.select_tile(i)

    def edit_tile_window(self, i):
        old = self.cfg["pages"][self.page][i]
        d = TileDialog(self, old)
        if d.result == "clear":
            if self._ask_clear(i):
                self._set_tile(i, None)
                self._knob_orphans([old])
        elif d.result:
            self._apply_knob(d.knob_changes)
            self._set_tile(i, d.result)
            if old and old != d.result:
                self._knob_orphans([old])

    # ---------------------------------------------------------- Drehknopf je Programm (über die Kacheln)
    def _apply_knob(self, changes):
        if not changes:
            return
        apps = knob.settings(self.cfg)["apps"]
        for app, p in changes.items():
            if p:
                apps[app] = p
            else:
                apps.pop(app, None)
        self.cfg["knob"] = {"apps": apps}

    def _tile_apps(self):
        out = set()
        for page in self.cfg["pages"]:
            for t in page:
                if t and t.get("type", plugin_api.DEFAULT_TYPE) == plugin_api.DEFAULT_TYPE and t.get("path"):
                    app = winctl.knob_app(t["path"])
                    if app:
                        out.add(app)
        return out

    def _knob_orphans(self, removed=None):
        """Belegungen ohne Kachel: nach dem Entfernen von Kacheln (removed) bzw. beim Öffnen (None) nachfragen."""
        apps = knob.settings(self.cfg)["apps"]
        if not apps:
            return
        have = self._tile_apps()
        if removed is None:
            cand = [a for a in apps if a not in have]
        else:
            cand = []
            for t in removed:
                if t and t.get("path") and t.get("type", plugin_api.DEFAULT_TYPE) == plugin_api.DEFAULT_TYPE:
                    a = winctl.knob_app(t["path"])
                    if a in apps and a not in have and a not in cand:
                        cand.append(a)
        if not cand:
            return
        names = ", ".join(cand)
        text = (f"Für {names} gibt es eine eigene Drehknopf-Belegung, aber keine Kachel mehr.\n"
                "Die Belegung lässt sich nur über eine Kachel ändern.\n\nBelegung löschen?")
        if messagebox.askyesno("Drehknopf", text, parent=self):
            self._apply_knob({a: False for a in cand})
            self._mark_dirty()

    def on_drop(self, event, i):
        paths = self.tk.splitlist(event.data)
        if not paths:
            return
        cols_rows = self.cfg["cols"] * self.cfg["rows"]
        page = self.cfg["pages"][self.page]
        if not self._ask_replace(i):
            return
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
        m.add_command(label="Einfügen", command=lambda: self._ask_replace(i) and self._set_tile(i, dict(self.clipboard)),
                      state="normal" if self.clipboard else "disabled")
        m.add_separator()
        m.add_command(label="Leeren", command=lambda: self._ask_clear(i) and (self._set_tile(i, None),
                                                                              self._knob_orphans([t])),
                      state="normal" if t else "disabled")
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

    def _commit(self, *keys):
        """Diese Einstellungen sofort speichern und ans Deck geben. Andere, noch ungespeicherte Änderungen
        (z. B. Kacheln) bleiben ungespeichert."""
        stored = self.config_store.deck_copy(self.deck_id)
        if stored is None:
            stored = copy.deepcopy(self.cfg)
        for k in keys:
            if k in self.cfg:
                stored[k] = copy.deepcopy(self.cfg[k])
            else:
                stored.pop(k, None)
        self.config_store.set_deck(self.deck_id, stored)
        link = self.manager.link(self.deck_id)
        if link:
            if keys == ("display",):
                link.request_options()
            elif keys != ("brightness",):
                link.request_sync(force=False)

    def on_design(self, _e=None):
        """Design gewählt: gilt sofort."""
        new = next(i for i, n in theme.CHOICES if n == self.v_design.get())
        if new != theme.design_id(self.cfg):
            self.cfg["design"] = new
            self._commit("design")

    def edit_display(self):
        """Aufbau-Effekt, Dimmen und Uhr im Ruhebild – gilt nach OK sofort."""
        link = self.manager.link(self.deck_id)
        if self._big_old("Aufbau-Effekt, Dimmen und Uhr im Ruhebild"):
            return

        def apply(opts):
            self.cfg["display"] = opts
            self._commit("display")
        self._open_pane("display", lambda: DisplayDialog(self, display_opts(self.cfg), link.fw if link else None,
                                                         apply))

    def edit_auto(self):
        if not self._own():
            self._extra_info()
            return
        self._edit_auto()

    def _edit_auto(self):
        lists = auto_apps(self.cfg)
        elsewhere = {}
        for p, apps in enumerate(lists):
            if p != self.page:
                for a in apps:
                    elsewhere.setdefault(a.lower(), p + 1)
        before = copy.deepcopy(lists)
        page = self.page

        def apply(apps):  # OK: speichern und sofort ans Deck
            new = copy.deepcopy(before)
            new[page] = apps
            # ein Programm nur auf einer Seite: von den anderen Seiten entfernen
            mine = {a.lower() for a in apps}
            for p in range(len(new)):
                if p != page:
                    new[p] = [a for a in new[p] if a.lower() not in mine]
            self.cfg["auto_apps"] = new
            self._commit("auto_apps")
            self.render()
        self._open_pane("auto", lambda: AutoDialog(self, page + 1, lists[page], elsewhere, apply=apply))

    # ---------------------------------------------------------- Seiten / Raster
    def _own(self):
        """Ist die angezeigte Seite eine eigene (bearbeitbare) Seite?"""
        return self.page < len(self.cfg["pages"])

    def _extra_info(self):
        title = (self._view_cfg().get("page_titles") or {}).get(self.page, "")
        where = "„Soundboard“" if title == "Soundboard" else "„SmartHome“"
        messagebox.showinfo("PC-Deck", f"Die Seite „{title}“ entsteht automatisch – ändern unter {where} links.",
                            parent=self)

    def goto_page(self, d):
        if not self._leave_editor():
            return
        auto = self.pane_key == "auto"
        if not self._leave_pane():
            return
        self.page = (self.page + d) % len(self._view_cfg()["pages"])
        self.selected = None
        self._clear_side()
        self._set_nav("tiles")
        self._show_side_hint()
        self.render()
        if auto and self._own():
            self.edit_auto()

    def add_page(self):
        if not self._own():
            self.page = len(self.cfg["pages"]) - 1  # neue eigene Seite hinter der letzten eigenen
        self._add_page()

    def _add_page(self):
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
        if not self._own():
            self._extra_info()
            return
        self._del_page()

    def _del_page(self):
        if len(self.cfg["pages"]) <= 1:
            if any(self.cfg["pages"][0]) and not self._confirm("Alle Kacheln dieser Seite löschen?"):
                return
            self.cfg["pages"][0] = empty_page(self.cfg["cols"], self.cfg["rows"])
            self.cfg["hidden"] = [[]]
        else:
            if any(self.cfg["pages"][self.page]) and not self._confirm("Diese Seite mit allen Kacheln löschen?"):
                return
            removed = list(self.cfg["pages"][self.page])
            self.cfg["auto_apps"] = auto_apps(self.cfg)
            del self._hidden()[self.page]
            del self.cfg["pages"][self.page]
            del self.cfg["auto_apps"][self.page]
            self.page = max(0, self.page - 1)
            self._mark_dirty()
            self.render()
            self._knob_orphans(removed)
            return
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
        lost = sum(1 for page in self.cfg["pages"] for idx, t in enumerate(page)
                   if t and (idx // oc >= rows or idx % oc >= cols))
        if lost and not self._confirm(f"{lost} Kachel(n) passen nicht mehr ins kleinere Raster und werden "
                                      "ausgeblendet.\nBeim Vergrößern kommen sie an ihre Stelle zurück."):
            self.v_cols.set(oc)
            self.v_rows.set(orow)
            return
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
    def on_deck_selected(self, _e=None):
        ids = self._deck_ids()
        new_id = ids[self.deck_box.current()]
        if new_id == self.deck_id:
            return
        if not self._leave_editor():
            self.deck_box.current(ids.index(self.deck_id))
            return
        self._flush_save()
        self.selected = None
        self._show_side_hint()
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
        """Helligkeit gilt sofort (am Deck und gespeichert)."""
        self.cfg["brightness"] = int(self.v_bri.get())
        self._commit("brightness")
        link = self.manager.link(self.deck_id)
        if link:
            link.request_brightness(self.v_bri.get())

    def edit_wifi(self):
        if not self.manager.link(self.deck_id):
            messagebox.showinfo("PC-Deck", "Das Deck ist nicht verbunden. Zum Einrichten des WLANs bitte per "
                                "USB anstecken.", parent=self)
            return
        self._open_pane("wifi", lambda: WifiDialog(self, self.manager, self.config_store, self.deck_id))

    def edit_soundboard(self):
        self._open_pane("sound", lambda: SoundboardDialog(self))

    def edit_smarthome(self):
        self._open_pane("home", lambda: BoschDialog(self))

    def edit_firmware(self, auto=False):
        if is_big(self.cfg):
            messagebox.showinfo("PC-Deck", "Die Firmware des Big-Decks gehört zum grblHAL-Display und wird dort "
                                "aktualisiert, nicht über die App.", parent=self)
            return
        self._open_pane("fw", lambda: FirmwareDialog(self, self.manager, self.deck_id, auto))

    def show_firmware_update(self, deck_id):
        """Nach „Ja“ auf die Abfrage: dieses Deck zeigen und die mitgelieferte Firmware gleich aufspielen."""
        if deck_id != self.deck_id:
            ids = self._deck_ids()
            if deck_id not in ids:
                return
            self.deck_box.current(ids.index(deck_id))
            self.on_deck_selected()
            if self.deck_id != deck_id:
                return
        self.edit_firmware(auto=True)

    def calibrate(self):
        def make():
            p = _Pane(self)
            p.title("Touch kalibrieren")
            ttk.Label(p, wraplength=380, justify="left",
                      text="Hilft, wenn Tipper am Deck an der falschen Stelle ankommen.\n\nNach dem Start erscheinen "
                           "auf dem Display nacheinander Markierungen in den Ecken – jeweils genau in die Mitte "
                           "tippen (am besten mit einem Stift). Danach speichert das Deck die Werte.").pack(anchor="w")
            msg = ttk.Label(p, foreground=DIM, wraplength=380, justify="left")
            msg.pack(anchor="w", pady=(10, 0))

            def start():
                link = self.manager.link(self.deck_id)
                if not link:
                    msg.configure(text="Das Deck ist nicht verbunden.")
                    return
                link.request_calibration()
                msg.configure(text="Läuft – jetzt am Display die Ecken der Reihe nach antippen.")
            b = ttk.Frame(p)
            b.pack(anchor="e", pady=(12, 0))
            ttk.Button(b, text="Schließen", command=p.destroy).pack(side="left", padx=4)
            ttk.Button(b, text="Kalibrieren starten", style="Accent.TButton", command=start).pack(side="left")
        self._open_pane("touch", make)

    def save(self):
        self._save_after = None
        self.cfg["brightness"] = int(self.v_bri.get())
        self.config_store.set_deck(self.deck_id, self.cfg)
        self.dirty = False
        link = self.manager.link(self.deck_id)
        if link:
            link.request_sync(force=False)
        self._update_status()

    def close(self):
        self._flush_save()
        self._remember_layout()
        self.config_store.save()
        self._clearing = True  # offener Bereich wird mit abgebaut: _pane_closed darf nichts neu aufbauen
        self.destroy()
