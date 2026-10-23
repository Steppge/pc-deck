"""Wo PC-Deck seine Daten ablegt (Konfiguration, Design, Log, eigene Plugins).

- %USERPROFILE%\\PCDeck. Bewusst nicht %APPDATA% – Programme, die aus einer Store-App
  (MSIX, z. B. der Claude-App) gestartet werden, schreiben dorthin nur in eine umgeleitete Kopie.
- Umgebungsvariable PCDECK_HOME überschreibt das (z. B. für Tests).
"""

import os

APP_DIR = os.environ.get("PCDECK_HOME") or os.path.join(os.path.expanduser("~"), "PCDeck")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
PLUGIN_DIR = os.path.join(APP_DIR, "plugins")
