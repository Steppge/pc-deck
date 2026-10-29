"""Sounds suchen und herunterladen (myinstants.com) – für das Fenster „Sounds suchen“.

Liest die Listen-Seiten von myinstants (Suche, Beliebt in Deutschland, Trend; je Seite ca. 36 Sounds)
und holt die MP3-Dateien. Nur Python-Standardbibliothek; geprüft wird mit soundfile, ob die Datei
wirklich abspielbar ist. Heruntergeladen wird nach %USERPROFILE%\\PCDeck\\Sounds.
"""

import html
import os
import re
import unicodedata
import urllib.parse
import urllib.request

from paths import APP_DIR

BASE = "https://www.myinstants.com"
SOUND_DIR = os.path.join(APP_DIR, "Sounds")
MAX_BYTES = 10 * 1024 * 1024
UA = {"User-Agent": "Mozilla/5.0 (PC-Deck Soundboard)"}

_ITEM = re.compile(r"play\('(/media/sounds/[^']+)'[^)]*\)[^>]*>.*?"
                   r"<a href=\"(/[^\"]*instant/[^\"]+)\"[^>]*>([^<]+)</a>", re.S)

LISTS = {"popular": "/de/index/de/", "trending": "/de/trending/"}

# Kategorien von myinstants (Kennung wie in der Adresse) mit deutschem Namen; die Liste wird live von der
# Startseite gelesen, diese hier dient als Rückfall und für die Übersetzung
CATEGORIES_DE = {
    "anime & manga": "Anime & Manga", "games": "Spiele", "memes": "Memes", "movies": "Filme", "music": "Musik",
    "politics": "Politik", "pranks": "Streiche", "reactions": "Reaktionen", "sound effects": "Soundeffekte",
    "sports": "Sport", "television": "Fernsehen", "tiktok trends": "TikTok-Trends", "viral": "Viral",
    "whatsapp audios": "WhatsApp-Audios",
}
_CAT = re.compile(r'href="/[a-z]{2}/categories/([^"/]+)/"[^>]*>\s*([^<]+?)\s*<')

# Sounds, die immer da sind: beim Start einmal herunterladen, falls sie im Sound-Ordner fehlen
# (nicht im Setup mitgeliefert – Rechte ungeklärt), und im Fenster „Sounds suchen“ fest oben.
DEFAULT_SOUNDS = [
    ("jackblack1!2!3", BASE + "/media/sounds/jack-black-plays-his-legendary-sax-a-boom-final_vVQGiA7.mp3"),
]


def _get(url, limit=MAX_BYTES):
    if not url.startswith("https://"):
        raise ValueError("nur https erlaubt")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Datei zu groß (über 10 MB)")
    return data


def fetch_categories():
    """[(deutscher Name, Kennung), ...] – live von der Startseite, sonst die bekannte Liste."""
    try:
        text = _get(BASE + LISTS["popular"], 2 * 1024 * 1024).decode("utf-8", "replace")
        found = {}
        for slug, name in _CAT.findall(text):
            slug = urllib.parse.unquote(html.unescape(slug)).strip().lower()
            found.setdefault(slug, CATEGORIES_DE.get(slug, html.unescape(name).strip()))
        if found:
            return sorted(((n, s) for s, n in found.items()), key=lambda x: x[0].lower())
    except Exception:
        pass
    return sorted(((n, s) for s, n in CATEGORIES_DE.items()), key=lambda x: x[0].lower())


def fetch_list(kind="popular", query="", page=1):
    """[(name, mp3_url, seite_url), ...]; kind "search" (query = Suchtext), "category" (query = Kennung),
    "popular" oder "trending"."""
    if kind == "search":
        path = "/de/search/?" + urllib.parse.urlencode({"name": query, "page": page})
    elif kind == "category":
        path = f"/de/categories/{urllib.parse.quote(query, safe='&')}/" + (f"?page={page}" if page > 1 else "")
    else:
        path = LISTS[kind] + (f"?page={page}" if page > 1 else "")
    text = _get(BASE + path, 2 * 1024 * 1024).decode("utf-8", "replace")
    out, seen = [], set()
    for mp3, page_url, name in _ITEM.findall(text):
        if mp3 in seen:
            continue
        seen.add(mp3)
        out.append((html.unescape(name).strip(), BASE + mp3, BASE + page_url))
    return out


def fetch_sound(mp3_url):
    """MP3-Datei als Bytes (zum Probehören oder Speichern)."""
    if not mp3_url.startswith(BASE + "/media/sounds/"):
        raise ValueError("unbekannte Adresse")
    return _get(mp3_url)


def file_name(name, mp3_url):
    """Lesbarer, sicherer Dateiname aus dem Sound-Namen, z. B. "vine-boom-sound.mp3"."""
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        name = name.replace(a, b)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "sound"
    ext = os.path.splitext(urllib.parse.urlparse(mp3_url).path)[1].lower() or ".mp3"
    return slug + (ext if ext in (".mp3", ".wav", ".ogg", ".flac") else ".mp3")


def target_path(name, mp3_url):
    return os.path.join(SOUND_DIR, file_name(name, mp3_url))


def save(name, mp3_url, data=None):
    """Herunterladen (bzw. schon geladene Bytes speichern) und prüfen. Rückgabe: Pfad der Datei.
    Gibt es den Namen schon mit anderem Inhalt, wird eine Nummer angehängt."""
    import soundfile as sf
    data = data if data is not None else fetch_sound(mp3_url)
    os.makedirs(SOUND_DIR, exist_ok=True)
    path = target_path(name, mp3_url)
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return path  # genau diese Datei ist schon da
        path = f"{base}-{n}{ext}"
        n += 1
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        sf.info(tmp)  # lässt sie sich lesen? sonst keine Audiodatei
    except Exception:
        os.remove(tmp)
        raise ValueError("keine abspielbare Audiodatei")
    os.replace(tmp, path)
    return path


def ensure_defaults():
    """Standard-Sounds herunterladen, falls sie fehlen. Rückgabe: Liste der neu geladenen Pfade."""
    new = []
    for name, mp3 in DEFAULT_SOUNDS:
        if not downloaded(name, mp3):
            new.append(save(name, mp3))
    return new


def downloaded(name, mp3_url):
    return os.path.exists(target_path(name, mp3_url))
