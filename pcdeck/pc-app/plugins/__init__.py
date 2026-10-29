"""Eingebaute Kachel-Typen. Jedes Modul registriert seine Typen beim Import.

Neue eingebaute Plugins hier statisch importieren (sonst findet PyInstaller sie nicht).
"""

from . import launch  # noqa: F401
from . import live  # noqa: F401
from . import media  # noqa: F401
from . import audio  # noqa: F401
from . import keys  # noqa: F401
from . import obs  # noqa: F401
from . import bosch  # noqa: F401
from . import sound  # noqa: F401
