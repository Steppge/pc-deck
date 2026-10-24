"""Firmware-Dateien fürs Deck: Version und Typ aus der .bin lesen, mitgelieferte Dateien finden.

Jede Firmware ab FW 16 enthält die Kennung "PCDECK-FW <version> <typ> " (siehe FW_TAG in main.cpp).
Aufgespielt wird nur eine Datei mit demselben Typ wie das Deck (FWINFO).
"""

import glob
import os
import re
import sys

TAG = re.compile(rb"PCDECK-FW (\d+) ([a-z0-9_]+) ")
TYPES = {"s3_ts35": "ESP32-S3 + MKS TS35", "esp32_tft35": "ESP32 + BTT TFT35"}


def info(data):
    """(version, typ) einer Firmware-Datei oder None (keine PC-Deck-Firmware)."""
    m = TAG.search(data)
    if data[:1] != b"\xe9" or not m:  # 0xE9 = Kopf eines ESP32-Programms
        return None
    return int(m.group(1)), m.group(2).decode("ascii")


def type_name(t):
    return TYPES.get(t, t)


def bundled():
    """{typ: (version, pfad)}: im Setup <Programmordner>\\firmware\\*.bin, aus dem Quellcode die PlatformIO-Ausgabe."""
    if getattr(sys, "frozen", False):
        pattern = os.path.join(os.path.dirname(sys.executable), "firmware", "*.bin")
    else:
        pattern = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware", ".pio", "build", "*",
                               "firmware.bin")
    found = {}
    for p in glob.glob(pattern):
        try:
            with open(p, "rb") as f:
                i = info(f.read())
        except OSError:
            continue
        if i and (i[1] not in found or i[0] > found[i[1]][0]):
            found[i[1]] = (i[0], os.path.normpath(p))
    return found
