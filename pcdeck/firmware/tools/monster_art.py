"""Erzeugt src/monster_art.h: Pixelbilder für den Monsterkampf im Ruhebild (Riesenechse und Riesenaffe,
eigene Figuren). Aufruf: python tools/monster_art.py  (aus dem Ordner firmware)."""
import math
import os

W, H = 30, 36  # Textur-Pixel; stehend, Blick nach rechts, Zeile 35 = Boden


def grid():
    return [["."] * W for _ in range(H)]


def put(g, x, y, ch):
    if 0 <= x < W and 0 <= y < H:
        g[y][x] = ch


def ell(g, cx, cy, rx, ry, ch):
    for y in range(H):
        for x in range(W):
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                g[y][x] = ch


def taper(g, a, b, r0, r1, ch):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy or 1
    for y in range(H):
        for x in range(W):
            t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / ll))
            px, py = ax + dx * t, ay + dy * t
            if math.hypot(x - px, y - py) <= r0 + (r1 - r0) * t:
                g[y][x] = ch


def thick(g, a, b, r, ch):
    taper(g, a, b, r, r, ch)


def rect(g, x0, y0, x1, y1, ch):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            put(g, x, y, ch)


# ---------------------------------------------------------------- Riesenechse
def lizard(legf=0, legb=0, arm="rest", mouth=False, stomp=False):
    g = grid()
    for bx, by in ((10, 11), (9, 15), (8.5, 19), (9, 23), (10.5, 27), (14, 7), (17, 2.5)):  # Rückenzacken
        taper(g, (bx + 3, by + 1), (bx - 1.5, by - 1.5), 1.9, 0.4, "o")
    taper(g, (12, 29), (5, 33), 4, 3, "G")  # Schwanz
    taper(g, (5, 33), (0, 35), 3, 1.2, "G")
    taper(g, (12, 28), (5, 32), 3.6, 2.6, "g")
    taper(g, (5, 32), (0, 34.5), 2.6, 1, "g")
    ell(g, 13, 27, 4, 4, "G")  # hinteres Bein
    taper(g, (13, 29), (12 + legb, 34), 2.5, 2, "G")
    rect(g, 10 + legb, 34, 15 + legb, 35, "G")
    ell(g, 15.5, 19.5, 6.8, 10.2, "G")  # Körper
    ell(g, 16.3, 19, 6.2, 9.6, "g")
    ell(g, 19.6, 20, 3, 7.5, "y")  # Bauch
    for yy in (15, 18, 21, 24):
        for xx in range(18, 22):
            if g[yy][xx] == "y":
                g[yy][xx] = "Y"
    thick(g, (18, 12), (21, 7), 3, "g")  # Hals und Kopf
    ell(g, 23, 6, 5, 3.5, "g")
    ell(g, 27, 7, 3, 2.2, "g")
    rect(g, 22, 3, 25, 3, "G")
    put(g, 23, 4, "e")
    put(g, 24, 4, "k")
    put(g, 29, 6, "k")
    if mouth:  # Brüllen: Maul offen
        ell(g, 26, 9.6, 3.6, 1.6, "k")
        for xx in (24, 26, 28):
            put(g, xx, 8, "w")
        for xx in (25, 27):
            put(g, xx, 11, "w")
        ell(g, 26, 12, 3.4, 0.9, "g")
    else:
        rect(g, 22, 8, 29, 8, "k")
        for xx in (24, 26, 28):
            put(g, xx, 8, "w")
    if arm == "rest":
        thick(g, (21, 15), (25, 17), 1.2, "G")
        put(g, 26, 18, "w")
    elif arm == "claw":
        thick(g, (21, 14), (28, 13), 1.3, "G")
        put(g, 29, 12, "w")
        put(g, 29, 14, "w")
    elif arm == "up":
        thick(g, (19, 14), (16, 1), 1.3, "G")
        put(g, 15, 0, "w")
        put(g, 17, 0, "w")
    if stomp:  # vorderes Bein hoch
        ell(g, 18, 26, 4, 4, "g")
        taper(g, (19, 27), (24, 30), 2.5, 2, "g")
        rect(g, 22, 30, 27, 31, "g")
        put(g, 28, 31, "w")
    else:
        ell(g, 18, 27, 4, 4.5, "g")
        taper(g, (18, 29), (19 + legf, 34), 2.5, 2, "g")
        rect(g, 17 + legf, 34, 23 + legf, 35, "g")
        put(g, 24 + legf, 35, "w")
    return g


# ---------------------------------------------------------------- Riesenaffe
def ape(step=0, arm="walk", mouth=False):
    g = grid()
    s = step
    thick(g, (12, 12), (6 - s, 33), 2.2, "F")  # hinterer Arm
    ell(g, 6 - s, 33, 2.2, 2, "F")
    ell(g, 10, 29, 3.5, 4, "F")  # Beine
    rect(g, 7 - s, 33, 13 - s, 35, "F")
    ell(g, 13, 21, 9, 10, "F")  # Körper
    ell(g, 14.5, 20, 8.5, 9.5, "f")
    ell(g, 17, 29, 3.5, 4, "f")
    rect(g, 15 + s, 33, 21 + s, 35, "f")
    rect(g, 15 + s, 35, 21 + s, 35, "F")
    ell(g, 18.5, 20, 3.5, 5, "c")  # Brust
    put(g, 18, 18, "C")
    put(g, 19, 22, "C")
    ell(g, 21, 8, 5, 4.6, "f")  # Kopf
    ell(g, 23.5, 9, 3, 3.2, "c")
    rect(g, 20, 6, 26, 6, "F")
    put(g, 22, 7, "w")
    put(g, 23, 7, "k")
    put(g, 25, 7, "k")
    put(g, 26, 9, "C")
    if mouth:
        ell(g, 24.5, 11, 1.8, 1.3, "r")
        put(g, 24, 10, "w")
        put(g, 25, 10, "w")
    else:
        rect(g, 23, 11, 26, 11, "k")
    if arm == "walk":
        thick(g, (19, 13), (24 + s, 33), 2.3, "f")
        ell(g, 24 + s, 33, 2.2, 2, "F")
    elif arm == "punch":
        thick(g, (19, 13), (27, 13), 2.3, "f")
        ell(g, 27.5, 13, 2.2, 2.4, "F")
    elif arm == "up":
        thick(g, (12, 12), (11, 1), 2.1, "F")
        thick(g, (19, 12), (26, 1), 2.2, "f")
        ell(g, 11, 1, 1.8, 1.4, "F")
        ell(g, 26, 1, 1.8, 1.4, "F")
    elif arm == "beat0":
        thick(g, (19, 13), (24, 17), 2.2, "f")
        thick(g, (24, 17), (20, 19), 2, "f")
        ell(g, 19.5, 19, 1.9, 1.9, "F")
    elif arm == "beat1":
        thick(g, (19, 13), (27, 15), 2.2, "f")
        ell(g, 27.5, 15, 1.9, 1.9, "F")
    return g


def lying(g):
    """auf den Rücken gefallen: um 90° gedreht (Kopf links, Bauch oben), leere Ränder weg"""
    r = [[g[y][W - 1 - x] for y in range(H)] for x in range(W)]  # Zeile = alte Spalte (rechts oben)
    rows = ["".join(row) for row in r]
    rows = [row for row in rows if row.strip(".")]
    left = min(len(row) - len(row.lstrip(".")) for row in rows)
    right = max(len(row.rstrip(".")) for row in rows)
    return [row[left:right] for row in rows]


def rows(g):
    return ["".join(r) for r in g]


FIGS = [
    ("MZ_WALK0", rows(lizard(2, -2))), ("MZ_WALK1", rows(lizard(-1, 1))), ("MZ_CLAW", rows(lizard(arm="claw"))),
    ("MZ_UP", rows(lizard(arm="up"))), ("MZ_STOMP", rows(lizard(stomp=True))),
    ("MZ_ROAR", rows(lizard(arm="up", mouth=True))), ("MZ_DOWN", lying(lizard())),
    ("MA_WALK0", rows(ape(2))), ("MA_WALK1", rows(ape(-2))), ("MA_PUNCH", rows(ape(arm="punch"))),
    ("MA_UP", rows(ape(arm="up"))), ("MA_BEAT0", rows(ape(arm="beat0", mouth=True))),
    ("MA_BEAT1", rows(ape(arm="beat1", mouth=True))), ("MA_DOWN", lying(ape())),
]

out = ["// automatisch erzeugt von tools/monster_art.py – nicht von Hand ändern", "#pragma once", ""]
for name, rr in FIGS:
    out.append("static const char* const %s[] = {" % name)
    for i in range(0, len(rr), 3):
        out.append("    " + ", ".join('"%s"' % r for r in rr[i:i + 3]) + ",")
    out.append("};")
    out.append("static const int %s_W = %d, %s_H = %d;" % (name, len(rr[0]), name, len(rr)))
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "..", "src", "monster_art.h"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(out) + "\n")
if __name__ == "__main__":
    for name, rr in FIGS:
        if name in ("MZ_WALK0", "MA_WALK0", "MZ_DOWN", "MA_PUNCH", "MZ_ROAR"):
            print(name)
            print("\n".join(rr))
