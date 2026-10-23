# PC-Deck – Tischgehäuse für MKS TS35 V2.0 + ESP32-S3-Board + Drehgeber (EC11), Display 50° geneigt.
# Aufbau wie das bewährte grblHAL-Gehäuse (crowpanel9_case.py): Kasten aus einem Stück + Rahmen.
#   kasten - Boden, Rückwand, Seiten, Front unten und obere Fläche; zum Display hin offen.
#            Gedruckt auf dem Boden stehend.
#   rahmen - flache Platte um das Display, Glas BÜNDIG mit der Rahmenvorderseite (keine Stufe am
#            Rand beim Antippen), außen bündig mit dem Kasten (keine Einfassung). Vier Senkkopf-
#            Holzschrauben 2,5 x 12 gehen durch die Rahmenecken und die TS35-Löcher in Vorbohrungen
#            in vier Blöcken des Kastens: sie halten Rahmen und Display. Rechts der Drehgeber (EC11).
# ESP32-Board kopfüber (Stiftleisten oben) mit doppelseitigem Klebeband auf einem Sockel am Boden,
# längs zur Tiefe, USB-Buchse mittig in der Rückwand.
# Maße TS35 gemessen (Platine 110 x 65, Löcher Ø3,4); Grundlage "MKS TS35 V2.0_001 SIZE.pdf".
#
# Welt: x nach rechts (Betrachter), y nach hinten, z nach oben, Tisch bei z = 0.
# Display-lokal: X = x, S = entlang der Schräge ab der unteren Außenkante, N = Tiefe hinter der Front.
#
# Aufruf: python gehaeuse.py [Ausgabeordner] [Deck 1|2]   (Deck auch per Umgebungsvariable PCDECK_DECK)
#   Deck 1: MKS TS35 + ESP32-S3 -> pcdeck_*.stl     Deck 2: BTT TFT35 SPI V2.1 + ESP32 -> pcdeck2_*.stl

import math
import os
import sys

import cadquery as cq

sys.stdout.reconfigure(encoding="utf-8")
OUT = sys.argv[1] if len(sys.argv) > 1 else "."

# --- Parameter ---------------------------------------------------------------------------------
TILT = 50.0          # Neigung des Displays gegen den Tisch (Grad)
WALL = 2.0           # Seiten, Front unten, obere Fläche
FLOOR_T = 2.5        # Boden und Rückwand
CLR = 0.4            # Spiel um die Platine
H_FRONT = 20.0       # Höhe der unteren Displaykante (Außenseite Rahmen) über dem Tisch
L_TOP = 39.0         # obere Fläche, senkrecht zum Display: Platz hinter der oberen Kante

# MKS TS35 V2.0 (Ansicht von vorne, Ursprung oben links an der Platine, y nach unten)
PCB_W, PCB_H, PCB_T = 110.0, 65.0, 1.6          # gemessen (ohne Encoder-Ansatz der Zeichnung)
# Löcher (x vom linken Rand, y vom oberen Rand), gemessen: oben links, oben rechts, unten links, unten rechts
TS_HOLES = [(4.0, 4.0), (106.0, 3.5), (4.0, 61.5), (106.1, 61.1)]
LCD_X, LCD_Y, LCD_W, LCD_H = 12.0, 4.6, 86.0, 56.2      # Displaymodul mit Metallrand (gemessen: unten 4,2 Rand)
GLASS_T = 5.4        # Vorderseite Platine bis Oberkante Metallrahmen des Displays (gemessen)
GLASS_CLR = 0.6     # Spalt rund um das Displaymodul in der Tasche des Rahmens
BEZEL = (2.7, 8.2, 2.7, 2.7)   # Metallrand des Displays links, rechts, oben, unten (gemessen, Display normal)
COVER_T = 0.8       # Abdecklippe über dem Metallrand (Stufe zur Bildfläche)
VIEW_CLR = 0.2      # Fenster etwas größer als die Bildfläche, damit kein Pixel verdeckt wird
VIEW_CH = 0.5       # Fase an der Fensterkante (Rand-Icons gut erreichbar)
BACK_PARTS = 21.0    # EXP-Buchse + Kabelstecker hinter der Platine (gemessen)
BACK_FLAT = 3.0      # übrige Bauteile auf der Rückseite des TS35 (flach, gemessen)

# Rahmen
LIP = 1.6            # Rahmenstärke (das Glas sitzt im Ausschnitt, Vorderseite bündig)
EDGE_CH = 0.8        # Fase an der Vorderkante des Rahmens
SCREW = 2.5          # Senkkopf-Holzschrauben 2,5 × 12 (keine Einschmelzmuttern)
SCREW_L = 12.0
SCREW_D = SCREW + 0.3                 # Durchgangsloch in Rahmen und Klemme
SCREW_HEAD = 5.0                      # Kopfdurchmesser (90°-Senkung)
PILOT_D = 2.0                         # Vorbohrung im Kasten
PILOT_EXTRA = 2.0                     # Vorbohrung tiefer als die Schraubenspitze

# Drehgeber EC11 rechts neben der Platine
ENC_FIELD = 22.0     # zusätzliche Breite rechts der Platine
ENC_X_OFF = 10.4     # Mitte Drehgeber-Bohrung rechts neben der Platine (Platinenkante + Spiel)
ENC_HOLE = 7.2       # Gewinde M7
ENC_PAD_D, ENC_PAD_T = 13.5, 2.4    # Auflage innen um die Bohrung (Platte dort 4,0 mm)
ENC_BODY = (12.5, 13.5, 16.0)       # Gehäuse + Lötanschlüsse hinter dem Rahmen (B, H, Tiefe)
KNOB_D = 20.0        # größter Drehknopf (ragt vorne über den Platinenrand)

# ESP32-S3-Board kopfüber: Modul und USB-Buchsen unten, Stiftleisten + Dupont-Stecker oben
ESP_L, ESP_W, ESP_T = 64.0, 28.0, 1.6   # gemessen, Länge mit Antenne
ESP_BOT_PARTS = 3.1  # Modul bzw. USB-Buchsen (jetzt unten)
DUPONT_H = 14.0      # Stifte + Dupont-Stecker über dem Board
PIN_H = 8.5          # blanke Stifte (Plastikleiste + Stift) über dem Board
DUPONT_FREE = 3      # vorne (Antennen-Ende) je Reihe so viele Stifte ohne Dupont-Stecker (3V3, RST, 4 / GND, TX, RX)
TAPE_T = 1.5         # doppelseitiges Klebeband zwischen Modul und Sockel
PAD = (52.0, 20.0, 1.6)         # Sockel unter dem Board (Länge nach vorne, Breite, Höhe)
USB_Z = 6.55                    # Mitte der USB-Öffnung über dem Tisch (fest, unabhängig vom Sockel)
PAD_GAP = 3.0                   # Sockel beginnt so weit vor der Rückwand
USB_OPEN = (13.5, 7.5)          # Öffnung mittig in der Rückwand für EINEN USB-C-Stecker (B, H)
USB_EDGE = 2.8                  # benutzte Buchse "USB": Abstand vom (linken) Längsrand bis zur Buchse
USB_C_W = 8.94                  # Breite USB-C-Buchse
EXP_X = (45.0, 65.0)            # EXP1/EXP2 auf der Rückseite des TS35: x vom linken Rand (gemessen)
EXP_Y_BOTTOM = 52.0             # ... vom unteren Rand
# Display im Gehäuse um 180° gedreht eingebaut (Firmware: TFT_ROTATION=3). Oben stehen die Maße
# bei normaler Lage; hier werden sie auf die Einbaulage umgerechnet.
DISPLAY_ROT180 = True
if DISPLAY_ROT180:
    TS_HOLES = [(PCB_W - x, PCB_H - y) for x, y in TS_HOLES]
    LCD_X, LCD_Y = PCB_W - LCD_X - LCD_W, PCB_H - LCD_Y - LCD_H
    BEZEL = (BEZEL[1], BEZEL[0], BEZEL[3], BEZEL[2])
    EXP_X = tuple(PCB_W - x for x in EXP_X)
    EXP_Y_BOTTOM = PCB_H - EXP_Y_BOTTOM

# Verstärkung des Rahmens am Drehgeber und Lasche zum Verschrauben von außen (rechte Seitenwand)
RIB_T = 3.0          # Stege oben/unten von den rechten Schraubsäulen zum Drehgeber (Breite)
RIB_DEEP = 8.0       # Steghöhe hinter dem Rahmen rechts neben der Displayplatine
RIB_TO_TAB = False   # Stege bis zur Seitenwand und daran entlang bis zur Lasche (Deck 2)
TAB_T, TAB_L, TAB_D = 3.0, 16.0, 14.0   # Lasche: Dicke, Länge (entlang der Schräge), Tiefe ins Gehäuse
TAB_HOLE_N = 9.0     # Vorbohrung (Ø PILOT_D, waagerecht) so tief hinter der Rahmenvorderseite
CABLE_L = 50.0                  # Kabellänge

# --- Deck-Auswahl ------------------------------------------------------------------------------
DECK = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("PCDECK_DECK", "1"))
PREFIX = "pcdeck" if DECK == 1 else f"pcdeck{DECK}"
# Freiräume hinter der Platine für Stecker + Kabel: (x0, x1, y0, y1 vom oberen Rand, Höhe)
BACK_ZONES = [(x - 11, x + 11, PCB_H - EXP_Y_BOTTOM - 7, PCB_H - EXP_Y_BOTTOM + 7, BACK_PARTS) for x in EXP_X]
if DECK == 2:
    # BTT TFT35 SPI V2.1 + klassischer ESP32 (gemessen 23.10.2026, von vorne gesehen wie das Bild steht)
    PCB_W, PCB_H = 98.4, 55.9
    TS_HOLES = [(3.1, 2.9), (95.3, 2.9), (3.1, 53.0), (95.3, 53.0)]  # Ø3,2; Lochrand 1,5 / 1,3 mm vom Rand
    LCD_X, LCD_Y, LCD_W, LCD_H = 6.8, 1.0, 84.5, 54.5    # Displayglas auf der Platine
    BEZEL = (3.0, 7.3, 2.5, 2.8)    # Glasrand bis Bildfläche links, rechts, oben, unten
    GLASS_T = 4.4                   # Platine bis Glasoberfläche
    BACK_ZONES = [(27.0, 33.0, 14.4, 44.0, 20.0)]  # XH2.54 senkrecht (10 hoch) + Kabelstecker
    ESP_L, ESP_W = 52.0, 28.8
    USB_EDGE = (ESP_W - USB_C_W) / 2  # USB-C mittig an der Stirnseite
    PAD = (40.0, 20.0, 1.6)
    DUPONT_H = PIN_H = 8.0          # Stifte gekürzt, Kabel angelötet
    DUPONT_FREE = 0
    RIB_TO_TAB = True

# --- Geometrie-Hilfen --------------------------------------------------------------------------
t = math.radians(TILT)
s_dir = cq.Vector(0, math.cos(t), math.sin(t))      # die Schräge hinauf
n_dir = cq.Vector(0, math.sin(t), -math.cos(t))     # in den Kasten hinein, senkrecht zum Display
A = cq.Vector(0, 0, H_FRONT)                        # untere Vorderkante des Rahmens

BORDER = CLR + WALL
L_SLOPE = PCB_H + 2 * BORDER
W_OUT = PCB_W + 2 * BORDER + ENC_FIELD
B = A + s_dir * L_SLOPE
B2 = B + n_dir * L_TOP
Y_BACK = B2.y
cav_w = W_OUT - 2 * WALL
PCB_X0 = -W_OUT / 2 + BORDER          # linke Platinenkante in X

N_PCB = COVER_T + GLASS_T             # Vorderseite der Platine hinter der Front (Metallrand liegt unter der Lippe)
PCB_BACK = N_PCB + PCB_T


def pcb2local(px, py):
    """TS35-Koordinaten (Ansicht von vorne, y nach unten) -> (X, S) auf dem Display"""
    return PCB_X0 + px, BORDER + (PCB_H - py)


def loc(X, S, N):
    return A + cq.Vector(X, 0, 0) + s_dir * S + n_dir * N


disp_plane = cq.Plane(origin=A.toTuple(), xDir=(1, 0, 0), normal=(-n_dir).toTuple())   # z aus dem Display


def dbox(x0, x1, s0, s1, n0, n1):
    """Quader in Display-Koordinaten"""
    return cq.Workplane(disp_plane).center((x0 + x1) / 2, (s0 + s1) / 2) \
        .rect(x1 - x0, s1 - s0).extrude(-(n1 - n0)).translate((n_dir * n0).toTuple())


def dcyl(X, S, n0, n1, d):
    return cq.Workplane(disp_plane).center(X, S).circle(d / 2).extrude(-(n1 - n0)).translate((n_dir * n0).toTuple())


def wbox(x0, x1, y0, y1, z0, z1):
    """Quader in Weltkoordinaten"""
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0).translate(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))


def wcyl_z(x, y, z0, z1, d):
    return cq.Workplane("XY").add(cq.Solid.makeCylinder(d / 2, z1 - z0, cq.Vector(x, y, z0), cq.Vector(0, 0, 1)))


def vol(w):
    try:
        return w.val().Volume()
    except Exception:
        return 0.0


# --- Kasten ------------------------------------------------------------------------------------
# Seitenprofil: senkrechte Front unten (keine Überhänge beim Drucken), Schräge, obere Fläche, Rückwand
profile = [(A.y, A.z), (B.y, B.z), (B2.y, B2.z), (B2.y, 0), (A.y, 0)]
outer = cq.Workplane("YZ").polyline(profile).close().extrude(W_OUT / 2, both=True)

cav = cq.Workplane("YZ").polyline(profile).close().extrude(cav_w / 2, both=True)
cav = cav.cut(wbox(-W_OUT, W_OUT, A.y - 10, A.y + WALL, -1, H_FRONT + 0.01))   # Front unten
cav = cav.cut(dbox(-W_OUT, W_OUT, L_SLOPE - WALL, 400, -50, 400))            # obere Fläche
cav = cav.cut(wbox(-W_OUT, W_OUT, -500, 500, -10, FLOOR_T))                   # Boden
cav = cav.cut(wbox(-W_OUT, W_OUT, Y_BACK - FLOOR_T, Y_BACK + 10, -1, 300))    # Rückwand
kasten = outer.cut(cav)
kasten = kasten.cut(dbox(-W_OUT, W_OUT, -50, L_SLOPE + 50, -50, LIP))         # davor liegt der Rahmen

# Blöcke für die Holzschrauben hinter den TS35-Ecken: alle als massive Säulen vom Boden,
# die unteren an der Front; die oberen über eine 9 x 9-Rippe, die vom Boden an der Rückwand
# hochläuft und dem Knick unter der oberen Fläche bis zum Block folgt
BLK = 9.0
for hx, hy in TS_HOLES:
    X, S = pcb2local(hx, hy)
    s0, s1 = (0, S + BLK / 2) if S < L_SLOPE / 2 else (S - BLK / 2, L_SLOPE)
    blk = dbox(X - BLK / 2, X + BLK / 2, s0, s1, PCB_BACK, PCB_BACK + 9.0)
    if S < L_SLOPE / 2:   # unten: massiver senkrechter Block vom Boden bis unter die Schraubstelle
        bb = blk.val().BoundingBox()
        blk = blk.union(wbox(X - BLK / 2, X + BLK / 2, A.y - 1, bb.ymax, -1, bb.zmax)
                        .intersect(dbox(X - BLK / 2, X + BLK / 2, -50, S + BLK / 2, PCB_BACK, 500)))
    else:                 # oben: 9 x 9-Rippe an der Wand: Rückwand hoch, dann unter der oberen Fläche bis zum Block
        y_in = Y_BACK - FLOOR_T
        blk = blk.union(wbox(X - BLK / 2, X + BLK / 2, y_in - BLK, y_in + 0.01, FLOOR_T - 0.01, B2.z))
        blk = blk.union(dbox(X - BLK / 2, X + BLK / 2, L_SLOPE - WALL - BLK, L_SLOPE, PCB_BACK, 500))
    kasten = kasten.union(blk.intersect(outer))
    kasten = kasten.cut(dcyl(X, S, PCB_BACK - 0.1, SCREW_L + PILOT_EXTRA, PILOT_D))

# ESP32: kopfüber auf einem Sockel (Klebeband), längs zur Tiefe, USB-Ende an der Rückwand.
# Die benutzte Buchse sitzt genau mittig (x = 0), das Board deshalb etwas nach rechts versetzt.
y_in = Y_BACK - FLOOR_T                  # Innenseite Rückwand
esp_y1 = y_in - 0.5                      # USB-Ende (Buchsen bündig an der Boardkante)
esp_y0 = esp_y1 - ESP_L                  # Antennen-Ende
esp_x0 = -(USB_EDGE + USB_C_W / 2)
esp_x1 = esp_x0 + ESP_W
esp_xc = (esp_x0 + esp_x1) / 2
esp_z = FLOOR_T + PAD[2] + TAPE_T + ESP_BOT_PARTS   # Unterseite des Boards
kasten = kasten.union(wbox(esp_xc - PAD[1] / 2, esp_xc + PAD[1] / 2, y_in - PAD_GAP - PAD[0], y_in - PAD_GAP,
                           FLOOR_T - 0.01, FLOOR_T + PAD[2]))
# USB-Öffnung mittig in der Rückwand
usb_zc = USB_Z
kasten = kasten.cut(wbox(-USB_OPEN[0] / 2, USB_OPEN[0] / 2, y_in - 1, Y_BACK + 1,
                         usb_zc - USB_OPEN[1] / 2, usb_zc + USB_OPEN[1] / 2).edges("|Y").fillet(2.5))

# --- Rahmen --------------------------------------------------------------------------------------
rahmen = dbox(-W_OUT / 2, W_OUT / 2, 0, L_SLOPE, 0, LIP)
try:   # umlaufende Fase an der sichtbaren Vorderkante
    rahmen = rahmen.faces(cq.selectors.DirectionMinMaxSelector(-n_dir, True)).edges().chamfer(EDGE_CH)
except Exception:
    pass
lcx, lcs = pcb2local(LCD_X + LCD_W / 2, LCD_Y + LCD_H / 2)
# Tasche für das Displaymodul hinter der Lippe, vorne nur das Fenster auf die Bildfläche
rahmen = rahmen.cut(dbox(lcx - LCD_W / 2 - GLASS_CLR, lcx + LCD_W / 2 + GLASS_CLR,
                         lcs - LCD_H / 2 - GLASS_CLR, lcs + LCD_H / 2 + GLASS_CLR, COVER_T, LIP + 1))
vx0, vx1 = lcx - LCD_W / 2 + BEZEL[0] - VIEW_CLR, lcx + LCD_W / 2 - BEZEL[1] + VIEW_CLR
vs0, vs1 = lcs - LCD_H / 2 + BEZEL[3] - VIEW_CLR, lcs + LCD_H / 2 - BEZEL[2] + VIEW_CLR
rahmen = rahmen.cut(dbox(vx0, vx1, vs0, vs1, -1, COVER_T + 0.01))
c = VIEW_CH + 0.2   # 45°-Fase an der Fensterkante: Pyramidenstumpf von vorne
rahmen = rahmen.cut(cq.Workplane(disp_plane).workplane(offset=0.2).center((vx0 + vx1) / 2, (vs0 + vs1) / 2)
                    .rect(vx1 - vx0 + 2 * c, vs1 - vs0 + 2 * c).workplane(offset=-c)
                    .rect(vx1 - vx0, vs1 - vs0).loft())
for hx, hy in TS_HOLES:
    X, S = pcb2local(hx, hy)
    rahmen = rahmen.union(dcyl(X, S, LIP - 0.01, N_PCB, 5.4))           # Säule auf die Platinenecke
    rahmen = rahmen.cut(dcyl(X, S, -1, N_PCB + 1, SCREW_D))
    cone = cq.Solid.makeCone(SCREW_HEAD / 2 + 0.2, SCREW_D / 2, SCREW_HEAD / 2 + 0.2 - SCREW_D / 2,
                             loc(X, S, -0.01), n_dir)   # 90°-Senkung
    rahmen = rahmen.cut(cq.Workplane("XY").add(cone))
# Drehgeber: mittig im Feld rechts der Platine, auf Höhe der Displaymitte
enc_x = PCB_X0 + PCB_W + CLR + ENC_X_OFF
enc_s = lcs
enc_back = LIP + ENC_PAD_T   # Rückseite der Montageplatte, dort liegt der Drehgeber an
rahmen = rahmen.union(dcyl(enc_x, enc_s, LIP - 0.01, enc_back, ENC_PAD_D))
rahmen = rahmen.cut(dcyl(enc_x, enc_s, -1, enc_back + 1, ENC_HOLE))
# Stege oben und unten: rechte Schraubsäule -> bis zum Drehgeber. Über der Displayplatine flach
# (1 mm Luft zur Platine), rechts daneben hoch.
pcb_x1 = PCB_X0 + PCB_W
for hx, hy in TS_HOLES:
    X, S = pcb2local(hx, hy)
    if X < lcx:
        continue
    rahmen = rahmen.union(dbox(X, enc_x, S - RIB_T / 2, S + RIB_T / 2, LIP - 0.01, N_PCB - 1.0))
    rahmen = rahmen.union(dbox(max(pcb_x1 + CLR, X + BLK / 2 + 0.3), enc_x, S - RIB_T / 2, S + RIB_T / 2,
                               LIP - 0.01, LIP + RIB_DEEP))
# Lasche innen an der rechten Seitenwand, auf Höhe des Drehgebers, mit waagerechter Vorbohrung:
# Schraube von außen durch die Seitenwand (Loch im Kasten bohrt der Nutzer selbst)
tab_x1 = cav_w / 2 - 0.3
rahmen = rahmen.union(dbox(tab_x1 - TAB_T, tab_x1, enc_s - TAB_L / 2, enc_s + TAB_L / 2, LIP - 0.01, LIP + TAB_D))
if RIB_TO_TAB:  # Stege oben/unten bis an die Seitenwand und daran entlang bis zur Lasche (U-Form)
    for hx, hy in TS_HOLES:
        X, S = pcb2local(hx, hy)
        if X < lcx:
            continue
        rahmen = rahmen.union(dbox(enc_x - 0.01, tab_x1, S - RIB_T / 2, S + RIB_T / 2, LIP - 0.01, LIP + RIB_DEEP))
        s0, s1 = (enc_s + TAB_L / 2 - 0.01, S + RIB_T / 2) if S > enc_s else (S - RIB_T / 2, enc_s - TAB_L / 2 + 0.01)
        rahmen = rahmen.union(dbox(tab_x1 - RIB_T, tab_x1, s0, s1, LIP - 0.01, LIP + RIB_DEEP))
tab_hole = loc(tab_x1 + 1, enc_s, TAB_HOLE_N)
rahmen = rahmen.cut(cq.Workplane("XY").add(cq.Solid.makeCylinder(PILOT_D / 2, TAB_T + 2, tab_hole,
                                                                  cq.Vector(-1, 0, 0))))
# passendes Loch in der rechten Seitenwand des Kastens, außen mit 90°-Senkung für den Schraubenkopf
wall_hole = cq.Vector(W_OUT / 2 + 1, tab_hole.y, tab_hole.z)
kasten = kasten.cut(cq.Workplane("XY").add(cq.Solid.makeCylinder(SCREW_D / 2, WALL + 2, wall_hole,
                                                                  cq.Vector(-1, 0, 0))))
sink = SCREW_HEAD / 2 + 0.2
kasten = kasten.cut(cq.Workplane("XY").add(cq.Solid.makeCone(sink, SCREW_D / 2, sink - SCREW_D / 2,
                                                              cq.Vector(W_OUT / 2 + 0.01, tab_hole.y, tab_hole.z),
                                                              cq.Vector(-1, 0, 0))))

# --- Attrappen für die Prüfung -------------------------------------------------------------------
pcb = dbox(PCB_X0, PCB_X0 + PCB_W, BORDER, BORDER + PCB_H, N_PCB, PCB_BACK)
lx0, ls1 = pcb2local(LCD_X, LCD_Y)
lx1, ls0 = pcb2local(LCD_X + LCD_W, LCD_Y + LCD_H)
glas = dbox(lx0, lx1, ls0, ls1, COVER_T, N_PCB)
hinten = dbox(PCB_X0 + 10, PCB_X0 + PCB_W - 10, BORDER + 9, BORDER + PCB_H - 9, PCB_BACK, PCB_BACK + BACK_FLAT)
for zx0, zx1, zy0, zy1, zh in BACK_ZONES:  # Stecker + Kabel hinter der Platine
    X0, S1 = pcb2local(zx0, zy0)
    X1, S0 = pcb2local(zx1, zy1)
    hinten = hinten.union(dbox(X0, X1, S0, S1, PCB_BACK, PCB_BACK + zh))
ts35 = pcb.union(glas).union(hinten)
enc = dbox(enc_x - ENC_BODY[0] / 2, enc_x + ENC_BODY[0] / 2, enc_s - ENC_BODY[1] / 2, enc_s + ENC_BODY[1] / 2,
           enc_back, enc_back + ENC_BODY[2])
esp = wbox(esp_x0, esp_x1, esp_y0, esp_y1, esp_z - ESP_BOT_PARTS, esp_z + ESP_T)
top = esp_z + ESP_T
dy = esp_y0 + 4 + DUPONT_FREE * 2.54      # ab hier Dupont-Stecker, davor nur blanke Stifte
dupont = wbox(esp_x0, esp_x0 + 4.5, dy, esp_y1 - 1, top, top + DUPONT_H).union(
    wbox(esp_x1 - 4.5, esp_x1, dy, esp_y1 - 1, top, top + DUPONT_H)).union(
    wbox(esp_x0, esp_x0 + 4.5, esp_y0 + 3, dy, top, top + PIN_H)).union(
    wbox(esp_x1 - 4.5, esp_x1, esp_y0 + 3, dy, top, top + PIN_H))
esp_attrappe = esp.union(dupont)
knob = dcyl(enc_x, enc_s, -20, 0, KNOB_D)

if __name__ != "__main__":
    raise SystemExit

# --- Prüfungen und Export ------------------------------------------------------------------------
print("außen: B %.1f  T %.1f  H %.1f mm  (Rückwand %.1f mm hoch)" % (W_OUT, Y_BACK - A.y, B.z, B2.z))
checks = [("TS35/Kasten", ts35, kasten), ("TS35/Rahmen", ts35, rahmen), ("Rahmen/Kasten", rahmen, kasten),
          ("Drehgeber/Kasten", enc, kasten), ("Drehgeber/TS35", enc, ts35), ("ESP32/Kasten", esp_attrappe, kasten),
          ("ESP32/TS35", esp_attrappe, ts35), ("ESP32/Drehgeber", esp_attrappe, enc),
          ("Knopf/Fenster-Abstand", knob, dbox(lx0, lx1, ls0, ls1, -20, 0))]
bad = 0
for name, a, b in checks:
    v = vol(a.intersect(b))
    bad += v > 0.01
    print("Überschneidung %-22s %.2f mm³" % (name, v))
# Kabelweg: Stecker hinten auf EXP1/EXP2 bis zu den Pins (nächster/fernster Pin, Luftlinie)
for k, (zx0, zx1, zy0, zy1, zh) in enumerate(BACK_ZONES):
    p = loc(*pcb2local((zx0 + zx1) / 2, (zy0 + zy1) / 2), PCB_BACK + zh)
    ds = [(p - cq.Vector(x, y, top + DUPONT_H)).Length
          for y in (esp_y1 - 25, esp_y1 - 5) for x in (esp_x0 + 1.3, esp_x1 - 1.3)]
    print("EXP%d bis ESP32-Pins am USB-Ende (GPIO 8-14, 21): %.0f ... %.0f mm (Kabel %.0f mm)" % (k + 1, min(ds), max(ds), CABLE_L))
print("Unterkante USB-Öffnung über dem Tisch: %.1f mm" % (usb_zc - USB_OPEN[1] / 2))
print("Loch rechte Seitenwand (Lasche): %.1f mm über dem Tisch, %.1f mm hinter der Vorderkante unten"
      % (tab_hole.z, tab_hole.y - A.y))

asm = cq.Assembly()
asm.add(kasten, name="kasten", color=cq.Color(0.25, 0.27, 0.32))
asm.add(rahmen, name="rahmen", color=cq.Color(0.12, 0.13, 0.16))
asm.add(ts35, name="ts35_attrappe", color=cq.Color(0.1, 0.4, 0.8, 0.5))
asm.add(esp_attrappe, name="esp32_attrappe", color=cq.Color(0.1, 0.6, 0.3, 0.5))
asm.add(enc, name="ec11_attrappe", color=cq.Color(0.6, 0.6, 0.6, 0.5))
asm.export(f"{OUT}/{PREFIX}_gehaeuse_baugruppe.step")
for name, w in (("kasten", kasten), ("rahmen", rahmen)):
    cq.exporters.export(w, f"{OUT}/{PREFIX}_{name}.step")
    cq.exporters.export(w, f"{OUT}/{PREFIX}_{name}.stl", tolerance=0.05, angularTolerance=0.1)
print("exportiert" + ("" if not bad else f" – ACHTUNG: {bad} Überschneidung(en)"))
