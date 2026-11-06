// PC-Deck Firmware – Touch-Display als Programmstarter für Windows
// Hardware: ESP32 / ESP32-S3 + ST7796 480x320 + XPT2046 (MKS TS35 V2.0, BTT TFT35 SPI V2.1)
//
// Das Deck selbst startet nichts. Es zeigt die Kacheln an, die ihm die PC-App
// geschickt hat, und meldet Tipper per USB-Seriell zurück ("PRESS <seite> <kachel>").
// Layout und Icons werden im LittleFS gespeichert und sind nach dem Einschalten
// sofort wieder da.

#include <Arduino.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <mbedtls/md.h>
#include <esp_sntp.h>
#include <vector>
#include "lgfx_config.h"
#include "monster_art.h"

#ifndef SERIAL_BAUD
#define SERIAL_BAUD 921600
#endif

#ifndef TFT_ROTATION
#define TFT_ROTATION 1
#endif
// Logo-Text, fest pro Deck: Widmung, großer leuchtender Name, hochgestelltes TM.
// Die PC-App liest ihn per "LOGO" und zeichnet ihn genauso ins Startbild der Designs.
// Eigene Texte gehören in include/logo_local.h (nicht im Repository), z. B.:
//   #ifdef TFT_ILI9488  // Deck 2
//   #define LOGO_DEDICATION "exclusive for ..."
//   #define LOGO_NAME "..."
//   #define LOGO_TM 0
//   #endif
#if __has_include("logo_local.h")
#include "logo_local.h"
#endif
#ifndef LOGO_DEDICATION
#define LOGO_DEDICATION "exclusive for you"
#endif
#ifndef LOGO_NAME
#define LOGO_NAME "Maker"
#endif
#ifndef LOGO_TM
#define LOGO_TM 0
#endif
#define FW_VERSION "37"  // 2 = Live-Kacheln (UPD, SHOW), 3 = langer Tipper (LONG), 4 = PING/Status,
                        // 5 = flimmerfreies Zeichnen (Seite im PSRAM), 6 = Plus-Kachel (CLR, leere Plätze),
                        // 7 = Löschen per Sicherheitsabfrage (DEL), 8 = Design (Startbild, Seitenbild, Glas-Kacheln),
                        // 9 = ohne Verbindung zur PC-App nur das Logo, 10 = Drehgeber (ROT, KNOB, KNOBLONG),
                        // 11 = gedrückt drehen (ROTP), Wert-Anzeige (OSD), 12 = WLAN (TCP, Kopplung, Suche),
                        // 13 = WLAN im USB-Betrieb aus, ohne USB Abfrage "WLAN einschalten?",
                        // 14 = Logo-Text fest pro Deck, Abfrage per LOGO,
                        // 15 = Seitenbild ohne PSRAM aus dem Flash, Logo im WLAN erst nach 12 s,
                        // 16 = Firmware-Update über die Verbindung (FWINFO, FWBEGIN, FWDATA, FWEND),
                        // 17 = Seitenname in der Navigationsleiste (TITLE), aktuelle Seite abfragen (CURPAGE),
                        // 18 = Firmware-Update auch über WLAN (signiert mit dem Kopplungsschlüssel),
                        // 19 = klassischer ESP32: "kein USB" schon nach 3 s ohne App-Daten, Startbild 1,5 s,
                        // 20/21 = Icon-Hintergrund (Schlüsselfarbe) auf Design-Kacheln wirklich durchsichtig,
                        // 22 = Icons komprimiert (ICONZ, UPD mit Typ 2) – weniger Speicher und Übertragung,
                        // 23 = Name im Netzwerk (Hostname + mDNS, z. B. PC-Deck-2), von der App per NAME
                        // 24 = Name auch per NetBIOS (Windows, IP-Scanner)
                        // 25 = eigener NetBIOS-Responder (Namens- und Statusabfrage, z. B. Advanced IP Scanner)
                        // 26 = "App weg" nur noch über ausbleibende Nachrichten (S3: isPlugged() flackert)
                        // 27 = Aufbau-Effekt, Dimmen bei Nichtbenutzung, Uhr im Ruhebild (OPT, TIME)
                        // 28 = einzelne Kacheln ändern ohne Neuübertragung (TSET, TDEL, LHASH); END bleibt auf der Seite
                        // 29 = ohne PSRAM: Rand auch neben und unter der Navigationsleiste
                        // 30 = Drehknopf lang erst ab 1,5 s, roter Rahmen sobald erreicht
                        // 31 = Uhr im Ruhebild als Block-Welt mit Figuren (OPT: Stil, Figuren)
                        // 32 = "WLAN einschalten?" kommt wieder nach dem Abziehen (erst fragen, wenn die App weg ist)
                        // 33 = Block-Welt ohne Streifen (DMA abwarten); ab hier zeigt die App die Nummer als 1.033
                        // 34 = neue Spinne; nach "Nein" öffnet Tipper/Drehknopf die WLAN-Abfrage erneut
                        // 35 = Monsterkampf, Anzeige-Optionen im NVS, Uhrzeit per NTP (ohne PC kurz WLAN),
                        //      WLAN-Abfrage nur unten antippbar
                        // 36 = Wanderer als Block-Figur (12x32, steif schwingende Arme/Beine)
                        // 37 = WLAN-Diagnose (WSTAT): Signal und Grund der letzten Trennung, auch im Display
// Deck-Typ: die PC-App spielt nur eine Firmware mit demselben Typ auf
#ifndef DECK_TYPE
#if CONFIG_IDF_TARGET_ESP32S3
#define DECK_TYPE "s3_ts35"
#else
#define DECK_TYPE "esp32_tft35"
#endif
#endif
// Kennung in der .bin, an der die PC-App Version und Typ einer Firmware-Datei erkennt
__attribute__((used)) static const char FW_TAG[] = "PCDECK-FW " FW_VERSION " " DECK_TYPE " ";
// Drehgeber (EC11): A/B und Taster gegen GND, interne Pull-ups. -1 = kein Drehgeber angeschlossen.
#ifndef PIN_ENC_A
#define PIN_ENC_A -1
#endif
#ifndef PIN_ENC_B
#define PIN_ENC_B -1
#endif
#ifndef PIN_ENC_SW
#define PIN_ENC_SW -1
#endif
#ifndef ENC_STEPS
#define ENC_STEPS 4     // Flanken pro Raste (EC11 meist 4, manche 2)
#endif
#ifndef ENC_REVERSE
#define ENC_REVERSE 0   // 1 = Drehrichtung umkehren (statt A und B umzulöten)
#endif
#define ENC_SEND_MS 25
#define OSD_MS 1200     // so lange bleibt die Wert-Anzeige (OSD) stehen  // Rasten so lange sammeln und gebündelt melden ("ROT +3")
#ifndef NET_PORT
#define NET_PORT 5555   // TCP: dasselbe Protokoll wie über USB (ab FW 12)
#endif
#define DISC_PORT 5556  // UDP: Suche der PC-App ("PCDECK?" -> "PCDECK <id> <port> <fw> <belegt>")
#define WIFI_USB_OFF_MS 30000  // so lange darf WLAN im USB-Betrieb noch an bleiben (z. B. nach dem Einrichten)
#define NO_USB_ASK_MS 1000     // so lange ohne USB-PC, dann die Abfrage "WLAN einschalten?"
#define WIFI_TAP_H 60          // nach "Nein": nur Tipper so weit unten öffnen die Abfrage wieder
#define USB_DATA_MS 3000       // klassischer ESP32: so lange ohne Daten der App (PING jede Sekunde) = kein USB
#define BOOT_USB_WAIT_MS 4000  // nach dem Start so lange auf die App warten, bevor "kein USB" gilt
#define PC_TIMEOUT_MS 5000    // so lange ohne Nachricht der PC-App -> nur noch das Logo
#define NET_TIMEOUT_MS 12000  // dasselbe im WLAN (Funkaussetzer überbrücken)
#define BOOT_LOGO_MS 1500   // so lange bleibt das Startbild stehen (Berühren = Touch kalibrieren)
#define LONG_MS 600     // ab so langem Drücken gilt ein Tipper als "lang" (Rahmen gelb, beim Loslassen LONG)
#define KNOB_LONG_MS 1500  // Drehknopf-Taster: ab so lange gedrückt KNOBLONG (roter Rahmen ums Display, ab FW 30)
#define DELETE_MS 1500  // ab so langem Drücken: Sicherheitsabfrage "Kachel loeschen?" (Rahmen rot)
#define CONFIRM_TIMEOUT_MS 10000  // Abfrage ohne Antwort -> abbrechen
#define SCREEN_W 480
#define SCREEN_H 320
#define ICON_SZ 64
#define ICON_BYTES (ICON_SZ * ICON_SZ * 2)
#define NAV_H 40
#define MAX_PAGES 8
#define MAX_COLS 5
#define MAX_ROWS 3

static LGFX tft;

// Farben – die PC-App legt die Icons auf exakt diese Kachelfarbe (44,48,58)
static uint16_t COL_BG, COL_TILE, COL_TEXT, COL_DIM, COL_ACCENT, COL_LONG, COL_WARN, COL_LOGO;

// ---------------------------------------------------------------- Design (ab FW 8)
// Die PC-App schickt ein Startbild (480x320) und ein Seitenbild (240x160, wird doppelt groß und
// damit weich angezeigt) sowie die Farben. Kacheln werden dann halbtransparent mit Verlauf/Glanz
// über das Seitenbild gelegt; der Icon-Hintergrund (Schlüsselfarbe 44,48,58) wird durchsichtig.
#define BOOT_IMG "/boot.bin"
#define PAGE_IMG "/page.bin"
#define THEME_FILE "/theme.txt"
#define BOOT_BYTES (480 * 320 * 2)
#define PAGE_W 240
#define PAGE_H 160
#define PAGE_BYTES (PAGE_W * PAGE_H * 2)
static const uint16_t KEY565 = 0x2987;  // RGB(44,48,58) als RGB565 – so rendert die PC-App die Icons
struct Theme {
  bool on = false;
  String hash = "none";
  uint16_t tileTop = 0, tileBot = 0;      // Kachel-Verlauf (RGB565)
  uint16_t borderTop = 0, borderBot = 0;  // Rand-Verlauf
  uint8_t style = 3;                      // 0 flach, 1 senkrecht, 2 diagonal, 3 Glas
  uint8_t alpha = 204;                    // Deckkraft der Kacheln
};
static Theme theme;
static uint16_t* pageBg = nullptr;  // Seitenbild 240x160 im PSRAM
static File pageBgFile;             // ohne PSRAM: Seitenbild zeilenweise aus dem LittleFS lesen
static uint16_t bgRowBuf[240];
static int bgRowY = -1;

static bool hasPageBg() { return pageBg || pageBgFile; }

// eine Zeile des Seitenbilds (0..159)
static const uint16_t* bgRow(int y) {
  if (pageBg) return pageBg + y * 240;
  if (y != bgRowY) {
    pageBgFile.seek((size_t)y * 240 * 2);
    if (pageBgFile.read((uint8_t*)bgRowBuf, sizeof(bgRowBuf)) != sizeof(bgRowBuf)) memset(bgRowBuf, 0, sizeof(bgRowBuf));
    bgRowY = y;
  }
  return bgRowBuf;
}

struct Tile {
  uint8_t page;
  uint8_t idx;
  bool icon;
  String label;
};

static std::vector<Tile> tiles;
static int gCols = 4, gRows = 3, gPages = 0;
static String gHash = "none";
static int curPage = 0;
static String pageTitle[MAX_PAGES];  // Name je Seite (nur RAM, von der PC-App; leer = "Seite x / y")
static uint16_t iconBuf[ICON_SZ * ICON_SZ];
static String deckId;
static uint32_t lastPcContact = 0;
static bool pcSeen = false;
static bool pcOk = false;  // PC-App verbunden? false: nur das Logo (PC startet, Anmeldung, App beendet)

// Anzeige-Optionen der PC-App (OPT, ab FW 27; nur RAM, die App schickt sie bei jeder Verbindung)
static bool optBuild = false;     // Seitenwechsel: Kacheln nacheinander zeigen (wie ohne PSRAM)
static uint32_t dimAfterMs = 0;   // 0 = nie dimmen
static int briNormal = 200, briDim = 25;
static bool dimmed = false;
static uint32_t lastUse = 0;      // letzter Tipper/Dreh/Druck oder Seitenwechsel der Automatik
static bool optClock = false;     // Uhr statt Logo, solange die App weg ist
static bool optWorld = true;      // Uhr als Block-Welt (ab FW 31), sonst schlicht
static bool optWalkers = true;    // in der Block-Welt laufen Figuren vorbei
static uint32_t clockUnix = 0, clockAtMs = 0;  // Zeit der App (UTC) und millis() beim Empfang
static int32_t clockOffset = 0;   // Versatz zur Ortszeit in s (inkl. Sommerzeit)
static int clockShownMin = -1;    // angezeigte Minute (neu zeichnen beim Wechsel)
// Aufbau-Effekt: die Seite liegt fertig im PSRAM-Bild, sichtbar wird sie Kachel für Kachel
static int buildNext = -1;        // nächste aufzudeckende Kachel, -1 = kein Aufbau läuft
static uint32_t buildAt = 0;
static int lastDrawnPage = -1;    // zuletzt mit Kacheln gezeigte Seite (-1 = Logo/Uhr)
#define BUILD_STEP_MS 40
static bool wakeUp();  // Bedienung: Dimm-Uhr neu starten, ggf. aufwecken (siehe unten)

// Live-Kacheln: nur im RAM und nur für die sichtbare Seite (kein Flash-Verschleiß).
// Beim Seitenwechsel werden sie verworfen; die PC-App schickt sie nach "SHOW" neu.
struct Live {
  bool used = false;
  bool icon = false;
  String label;
  uint8_t* buf = nullptr;  // Icon-Daten: roh (len == ICON_BYTES) oder komprimiert (ab FW 22)
  uint16_t len = 0;
  uint16_t cap = 0;        // angelegte Größe von buf
};
static Live live[MAX_COLS * MAX_ROWS];
static int downTile = -1;   // gerade gedrückte Kachel (für Rahmen beim Neuzeichnen)
static bool longSent = false;     // 0,6 s erreicht: beim Loslassen LONG statt PRESS (Rahmen gelb)
static bool deleteArmed = false;  // 1,5 s erreicht: Sicherheitsabfrage "loeschen?" (Rahmen rot)

// Sicherheitsabfrage "Kachel loeschen?" – solange sie offen ist, wird nichts anderes gezeichnet
static bool confirmActive = false;
static bool confirmWaitRelease = false;  // erst Finger weg, dann zählt ein Tipper auf die Knöpfe
static int confirmTile = -1;
static uint32_t confirmSince = 0;

static void clearLive() {
  for (auto& l : live) {
    l.used = false;
    l.icon = false;
    l.label = "";
  }
}

// ---------------------------------------------------------------- Hilfen
static String iconPath(int p, int i) { return "/i" + String(p) + "_" + String(i) + ".bin"; }

// Icons komprimiert (ab FW 22, Lauflängen): Steuerbyte c; c & 0x80: (c & 0x7F) + 1 mal das folgende Pixel,
// sonst c + 1 Pixel wörtlich (Pixel = RGB565 little endian). Eine Datei mit genau ICON_BYTES ist roh.
static uint8_t zBuf[ICON_BYTES];  // komprimierte Daten beim Lesen/Empfangen

static bool unpackIcon(const uint8_t* src, size_t n, uint16_t* dst) {
  size_t o = 0, i = 0;
  const size_t total = ICON_SZ * ICON_SZ;
  while (i < n && o < total) {
    uint8_t c = src[i++];
    size_t cnt = (c & 0x7F) + 1;
    if (o + cnt > total) return false;
    if (c & 0x80) {
      if (i + 2 > n) return false;
      uint16_t v = src[i] | (src[i + 1] << 8);
      i += 2;
      while (cnt--) dst[o++] = v;
    } else {
      if (i + 2 * cnt > n) return false;
      while (cnt--) {
        dst[o++] = src[i] | (src[i + 1] << 8);
        i += 2;
      }
    }
  }
  return o == total && i == n;
}

// Icon (roh oder komprimiert) nach dst (ICON_SZ*ICON_SZ Pixel) bringen
static bool iconFrom(const uint8_t* data, size_t n, uint16_t* dst) {
  if (n == ICON_BYTES) {
    if ((const void*)data != (const void*)dst) memcpy(dst, data, ICON_BYTES);
    return true;
  }
  return n < ICON_BYTES && unpackIcon(data, n, dst);
}

// gespeichertes Icon einer Kachel nach iconBuf laden
static bool loadIcon(int p, int i) {
  File f = LittleFS.open(iconPath(p, i), "r");
  if (!f) return false;
  size_t n = f.size();
  bool ok = false;
  if (n == ICON_BYTES) {
    ok = f.read((uint8_t*)iconBuf, ICON_BYTES) == ICON_BYTES;
  } else if (n > 0 && n < ICON_BYTES) {
    ok = f.read(zBuf, n) == n && unpackIcon(zBuf, n, iconBuf);
  }
  f.close();
  return ok;
}

static Tile* findTile(int p, int i) {
  for (auto& t : tiles)
    if (t.page == p && t.idx == i) return &t;
  return nullptr;
}

static int gridH() { return gPages > 1 ? SCREEN_H - NAV_H : SCREEN_H; }

static void tileRect(int i, int& x, int& y, int& w, int& h) {
  int cw = SCREEN_W / gCols, ch = gridH() / gRows;
  int c = i % gCols, r = i / gCols;
  x = c * cw + 3;
  y = r * ch + 3;
  w = cw - 6;
  h = ch - 6;
}

static String fitText(lgfx::LovyanGFX& g, const String& s, int maxW) {
  if (g.textWidth(s) <= maxW) return s;
  String t = s;
  while (t.length() > 1 && g.textWidth(t + "..") > maxW) t.remove(t.length() - 1);
  return t + "..";
}

// ---------------------------------------------------------------- Design-Hilfen
static inline uint16_t swap16(uint16_t v) { return (v >> 8) | (v << 8); }  // Sprite-Puffer: Big-Endian

// Farbe a nach b mischen, t = 0..255 (0 = a, 255 = b)
static inline uint16_t blend565(uint16_t a, uint16_t b, int t) {
  int r1 = a >> 11, g1 = (a >> 5) & 63, b1 = a & 31;
  int r2 = b >> 11, g2 = (b >> 5) & 63, b2 = b & 31;
  return ((r1 + (r2 - r1) * t / 255) << 11) | ((g1 + (g2 - g1) * t / 255) << 5) | (b1 + (b2 - b1) * t / 255);
}

// Pixel im Puffer eines 16-Bit-Sprites; (X, Y) = Bildschirmkoordinaten, (ox, oy) = Lage des Sprites
static inline uint16_t* spritePx(LGFX_Sprite& s, int ox, int oy, int X, int Y) {
  int x = X - ox, y = Y - oy;
  if (x < 0 || y < 0 || x >= s.width() || y >= s.height()) return nullptr;
  return (uint16_t*)s.getBuffer() + y * s.width() + x;
}

// Seitenbild (doppelt groß) in das Rechteck (X0, Y0, W, H) zeichnen (Bildschirmkoordinaten);
// ohne Rechteck: der ganze Sprite-Bereich
static void drawBgTo(LGFX_Sprite& s, int ox, int oy, int X0 = INT16_MIN, int Y0 = 0, int W = 0, int H = 0) {
  if (X0 == INT16_MIN) {
    X0 = ox;
    Y0 = oy;
    W = s.width();
    H = s.height();
  }
  for (int Y = Y0; Y < Y0 + H; Y++) {
    if (Y < 0 || Y >= SCREEN_H) continue;
    const uint16_t* src = bgRow(Y / 2);
    for (int X = X0; X < X0 + W; X++) {
      uint16_t* p = spritePx(s, ox, oy, X, Y);
      if (p) *p = (X >= 0 && X < SCREEN_W) ? swap16(src[X / 2]) : swap16(COL_BG);
    }
  }
}

// Halbtransparente Fläche mit Verlauf/Glanz und runden Ecken über den Sprite-Inhalt legen
static void fillGlassTo(LGFX_Sprite& s, int ox, int oy, int x, int y, int w, int h, int rad) {
  for (int r = 0; r < h; r++) {
    int d = r < rad ? rad - r : (r >= h - rad ? r - (h - rad - 1) : 0);
    int inset = d > 0 ? rad - (int)sqrtf((float)(rad * rad - d * d)) : 0;  // runde Ecken
    int tv = h > 1 ? r * 255 / (h - 1) : 0;
    uint16_t rowCol = theme.style == 0 ? theme.tileTop : blend565(theme.tileTop, theme.tileBot, tv);
    int gloss = 0;
    if (theme.style == 3) {  // Glanz: oben weißer Schimmer, nach unten auslaufend, helle Oberkante
      if (r < h / 2) gloss = 90 * (h / 2 - r) / (h / 2) + 20;
      if (r == 1) gloss = 170;
    }
    for (int c = inset; c < w - inset; c++) {
      uint16_t* p = spritePx(s, ox, oy, x + c, y + r);
      if (!p) continue;
      uint16_t col = rowCol;
      if (theme.style == 2) col = blend565(theme.tileTop, theme.tileBot, (c * 255 / (w - 1) + tv) / 2);
      int gl = (r == 1 && (c < 6 || c > w - 7)) ? 0 : gloss;
      if (gl) col = blend565(col, 0xFFFF, gl);
      *p = swap16(blend565(swap16(*p), col, theme.alpha));
    }
  }
}

// Verlaufsrand (3 px, außen kräftig, innen auslaufend) – nur der Teil, der im Sprite liegt
static void drawBorderTo(LGFX_Sprite& s, int ox, int oy) {
  static const int fade[3] = {255, 179, 102};
  for (int Y = oy; Y < oy + s.height(); Y++) {
    if (Y < 0 || Y >= SCREEN_H) continue;
    uint16_t col = blend565(theme.borderTop, theme.borderBot, Y * 255 / (SCREEN_H - 1));
    for (int k = 0; k < 3; k++) {
      uint16_t c = swap16(blend565(0, col, fade[k]));
      uint16_t* p;
      if ((p = spritePx(s, ox, oy, k, Y))) *p = c;
      if ((p = spritePx(s, ox, oy, SCREEN_W - 1 - k, Y))) *p = c;
    }
  }
  for (int k = 0; k < 3; k++) {
    uint16_t ct = swap16(blend565(0, theme.borderTop, fade[k]));
    uint16_t cb = swap16(blend565(0, theme.borderBot, fade[k]));
    for (int X = ox; X < ox + s.width(); X++) {
      if (X < k || X >= SCREEN_W - k) continue;
      uint16_t* p;
      if ((p = spritePx(s, ox, oy, X, k))) *p = ct;
      if ((p = spritePx(s, ox, oy, X, SCREEN_H - 1 - k))) *p = cb;
    }
  }
}

static bool fancy(LGFX_Sprite* spr) { return theme.on && hasPageBg() && spr; }

// ---------------------------------------------------------------- Zeichnen
// Gezeichnet wird immer erst unsichtbar in einen Zwischenspeicher (Sprite) und dann auf
// einen Schlag angezeigt – sonst sieht man, wie die Kacheln nacheinander aufgebaut werden.
//  - frame:   ganze Seite (480x320, 300 KB) im PSRAM, falls vorhanden (ESP32-S3 N16R8)
//  - Kachel-Sprite: eine Kachel, für einzelne Kacheln und als Rückfall ohne PSRAM
static LGFX_Sprite frame(&tft);
static bool haveFrame = false;

// Kachel i in das Ziel g zeichnen; (ox, oy) = Lage des Ziels auf dem Bildschirm.
// spr = dasselbe Ziel als Sprite (für das Design mit Bild/Transparenz), sonst nullptr
static void drawTileTo(lgfx::LovyanGFX& g, LGFX_Sprite* spr, int ox, int oy, int i, bool pressed, bool isLong) {
  int x, y, w, h;
  tileRect(i, x, y, w, h);
  bool fx = fancy(spr);
  Tile* t = findTile(curPage, i);
  Live& l = live[i];
  if (fx) drawBgTo(*spr, ox, oy, x - 3, y - 3, w + 6, h + 6);  // Seitenbild nur unter dieser Zelle
  else g.fillRect(x - 3 - ox, y - 3 - oy, w + 6, h + 6, COL_BG);
  x -= ox;
  y -= oy;
  if (t || l.used) {  // auch ein leerer Platz kann eine Live-Kachel zeigen (Plus-Kachel, ab FW 6)
    if (fx) fillGlassTo(*spr, 0, 0, x, y, w, h, 10);
    else g.fillRoundRect(x, y, w, h, 10, theme.on ? theme.tileTop : COL_TILE);
    if (pressed) {
      uint16_t c = isLong ? (deleteArmed ? COL_LONG : COL_WARN) : COL_ACCENT;
      g.drawRoundRect(x, y, w, h, 10, c);
      g.drawRoundRect(x + 1, y + 1, w - 2, h - 2, 9, c);
      g.drawRoundRect(x + 2, y + 2, w - 4, h - 4, 8, c);
    }

    g.setFont(&fonts::Font2);
    g.setTextColor(COL_TEXT);  // ohne Hintergrund: Schrift direkt auf Kachel bzw. Verlauf
    const String& label = l.used ? l.label : t->label;
    bool haveLabel = label.length() > 0;
    bool drewIcon = false;

    const uint16_t* img = nullptr;
    if (l.used) {
      if (l.icon && l.buf && iconFrom(l.buf, l.len, iconBuf)) img = iconBuf;
    } else if (t->icon) {
      if (loadIcon(curPage, i)) img = iconBuf;
    }
    if (img) {
      int iy = haveLabel ? y + (h - ICON_SZ - 18) / 2 : y + (h - ICON_SZ) / 2;
      int ix = x + (w - ICON_SZ) / 2;
      if (theme.on && spr) {
        // Icon-Hintergrund (Schlüsselfarbe) durchsichtig: Pixel selbst in den Kachel-Puffer schreiben und die
        // Schlüsselfarbe auslassen (die Transparenz von pushImage griff mit unseren Little-Endian-Daten nicht)
        for (int r = 0; r < ICON_SZ; r++) {
          for (int c = 0; c < ICON_SZ; c++) {
            uint16_t v = img[r * ICON_SZ + c];
            if (v == KEY565) continue;
            uint16_t* p = spritePx(*spr, 0, 0, ix + c, iy + r);  // x/y sind hier schon relativ zum Puffer
            if (p) *p = swap16(v);
          }
        }
      } else {
        g.pushImage(ix, iy, ICON_SZ, ICON_SZ, (const lgfx::rgb565_t*)img);
      }
      drewIcon = true;
      if (haveLabel) {
        g.setTextDatum(top_center);
        g.drawString(fitText(g, label, w - 6), x + w / 2, iy + ICON_SZ + 2);
      }
    }
    if (!drewIcon && haveLabel) {
      g.setTextDatum(middle_center);
      g.drawString(fitText(g, label, w - 6), x + w / 2, y + h / 2);
    }
  }
  if (fx) drawBorderTo(*spr, ox, oy);
}

// ---------------------------------------------------------------- Wert-Anzeige (OSD)
// Kasten mit Text und Balken über der Seite; wird nach jedem Neuzeichnen wieder darübergelegt.
static uint32_t osdUntil = 0;  // 0 = keine Anzeige
static int osdPct = -1;        // -1 = nur Text, kein Balken
static String osdText;
static const int OSD_X = 60, OSD_Y = 116, OSD_W = 360, OSD_H = 88;

// WLAN-Zeichen oben rechts, solange die PC-App über WLAN verbunden ist (dauerhaft sichtbar)
static bool viaNet = false;

static void drawNetBadge() {
  if (!viaNet || !pcOk || confirmActive) return;
  const int cx = SCREEN_W - 13, cy = 19;
  tft.startWrite();
  tft.fillCircle(cx, cy - 6, 12, COL_BG);                              // dunkler Grund, auf jedem Design lesbar
  tft.fillArc(cx, cy, 13, 11, 225, 315, COL_ACCENT);                   // äußerer Bogen
  tft.fillArc(cx, cy, 8, 6, 225, 315, COL_ACCENT);                     // innerer Bogen
  tft.fillCircle(cx, cy - 1, 2, COL_ACCENT);                           // Punkt
  tft.endWrite();
}

static void drawOsd() {
  if (!osdUntil || confirmActive || !pcOk) return;
  LGFX_Sprite s(&tft);
  s.setPsram(haveFrame);
  s.setColorDepth(16);
  bool spr = s.createSprite(OSD_W, OSD_H) != nullptr;
  lgfx::LovyanGFX& g = spr ? (lgfx::LovyanGFX&)s : (lgfx::LovyanGFX&)tft;
  int x = spr ? 0 : OSD_X, y = spr ? 0 : OSD_Y;
  g.fillRect(x, y, OSD_W, OSD_H, COL_TILE);
  g.drawRect(x, y, OSD_W, OSD_H, COL_ACCENT);
  g.drawRect(x + 1, y + 1, OSD_W - 2, OSD_H - 2, COL_ACCENT);
  g.setTextDatum(middle_center);
  g.setTextColor(COL_TEXT, COL_TILE);
  g.setFont(&fonts::FreeSansBold12pt7b);
  String t = osdPct >= 0 ? osdText + "  " + String(osdPct) + " %" : osdText;
  g.drawString(fitText(g, t, OSD_W - 20), x + OSD_W / 2, y + (osdPct >= 0 ? 28 : OSD_H / 2));
  if (osdPct >= 0) {
    int bw = OSD_W - 40, bx = x + 20, by = y + 54;
    g.drawRect(bx, by, bw, 16, COL_DIM);
    g.fillRect(bx + 2, by + 2, (bw - 4) * min(osdPct, 100) / 100, 12, COL_ACCENT);
  }
  if (spr) {
    s.pushSprite(OSD_X, OSD_Y);
    s.deleteSprite();
  }
}

// Eine Kachel neu zeichnen und auf einen Schlag anzeigen
static void drawTile(int i, bool pressed = false, bool isLong = false) {
  if (confirmActive || !pcOk) return;  // Sicherheitsabfrage bzw. Logo liegt darüber
  if (buildNext >= 0) {  // Aufbau läuft (z. B. Live-Kachel nach SHOW): im Seitenbild nachtragen
    drawTileTo(frame, &frame, 0, 0, i, pressed, isLong);
    if (i >= buildNext) return;  // wird gleich der Reihe nach aufgedeckt
  }
  int x, y, w, h;
  tileRect(i, x, y, w, h);
  int cx = x - 3, cy = y - 3, cw = w + 6, ch = h + 6;  // ganze Zelle samt Rand
  LGFX_Sprite spr(&tft);
  spr.setPsram(haveFrame);  // mit PSRAM dort anlegen, sonst im normalen RAM
  spr.setColorDepth(16);
  if (spr.createSprite(cw, ch)) {
    drawTileTo(spr, &spr, cx, cy, i, pressed, isLong);
    spr.pushSprite(cx, cy);
    spr.deleteSprite();
  } else {
    drawTileTo(tft, nullptr, 0, 0, i, pressed, isLong);  // zu wenig Speicher: direkt zeichnen
  }
  drawOsd();  // Wert-Anzeige bleibt obenauf
  drawNetBadge();
}

static void drawNavTo(lgfx::LovyanGFX& g, LGFX_Sprite* spr) {
  if (gPages <= 1) return;
  int y0 = SCREEN_H - NAV_H;
  bool fx = fancy(spr);
  if (fx) {  // Hintergrund liegt schon (drawPage), Knöpfe halbtransparent darüber
    fillGlassTo(*spr, 0, 0, 4, y0 + 4, 90, NAV_H - 8, 8);
    fillGlassTo(*spr, 0, 0, SCREEN_W - 94, y0 + 4, 90, NAV_H - 8, 8);
  } else {
    uint16_t tc = theme.on ? theme.tileTop : COL_TILE;
    g.fillRect(0, y0, SCREEN_W, NAV_H, COL_BG);
    g.fillRoundRect(4, y0 + 4, 90, NAV_H - 8, 8, tc);
    g.fillRoundRect(SCREEN_W - 94, y0 + 4, 90, NAV_H - 8, 8, tc);
  }
  g.setFont(&fonts::Font2);
  g.setTextDatum(middle_center);
  g.setTextColor(COL_TEXT);
  g.drawString("<", 49, y0 + NAV_H / 2);
  g.drawString(">", SCREEN_W - 49, y0 + NAV_H / 2);
  g.setTextColor(fx ? COL_TEXT : COL_DIM);
  String nav = pageTitle[curPage].length() ? pageTitle[curPage] + "  " + String(curPage + 1) + "/" + String(gPages)
                                            : "Seite " + String(curPage + 1) + " / " + String(gPages);
  g.drawString(nav, SCREEN_W / 2, y0 + NAV_H / 2);
}

// Fertiges Startbild der PC-App aus dem Flash anzeigen (false = keins vorhanden)
static bool drawBootImage() {
  if (!theme.on) return false;
  File f = LittleFS.open(BOOT_IMG, "r");
  if (!f || f.size() != BOOT_BYTES) {
    if (f) f.close();
    return false;
  }
  const int rows = 16;
  uint16_t* buf = (uint16_t*)malloc(SCREEN_W * rows * 2);
  if (!buf) {
    f.close();
    return false;
  }
  tft.startWrite();
  for (int y = 0; y < SCREEN_H; y += rows) {
    f.read((uint8_t*)buf, SCREEN_W * rows * 2);
    tft.pushImage(0, y, SCREEN_W, rows, (const lgfx::rgb565_t*)buf);
  }
  tft.endWrite();
  free(buf);
  f.close();
  return true;
}

// Start-/Wartebildschirm: großes "PC-Deck", Widmung mit hochgestelltem TM,
// unten Geräte-ID und die Zeile line2 (Hinweis bzw. Status). line2 = nullptr: nur das Logo
static void drawWaiting(const char* line2) {
  if (drawBootImage()) {  // fertiges Startbild der PC-App; nur ID und Hinweis kommen dazu
    if (!line2) return;
    tft.setTextDatum(middle_center);
    tft.setFont(&fonts::FreeSans9pt7b);
    tft.setTextColor(COL_DIM);
    tft.drawString("ID " + deckId, SCREEN_W / 2, 262);
    tft.setTextColor(COL_TEXT);
    tft.setFont(&fonts::FreeSans12pt7b);
    if (tft.textWidth(line2) > SCREEN_W - 10) tft.setFont(&fonts::FreeSans9pt7b);
    tft.drawString(line2, SCREEN_W / 2, SCREEN_H - 20);
    return;
  }
  tft.fillScreen(COL_BG);
  tft.setTextColor(COL_TEXT, COL_BG);

  tft.setFont(&fonts::FreeSansBold24pt7b);
  tft.setTextSize(2);
  tft.setTextDatum(middle_center);
  tft.drawString("PC-Deck", SCREEN_W / 2, 90);
  tft.setTextSize(1);

  // Widmung (LOGO_DEDICATION) klein und grau, darunter der Name (LOGO_NAME) groß, fett und cyan,
  // ggf. mit hochgestelltem "TM" (die Schriften kennen kein ™-Zeichen); Name+TM gemeinsam zentriert
  tft.setTextDatum(middle_center);
  tft.setTextColor(COL_DIM, COL_BG);
  tft.setFont(&fonts::FreeSans12pt7b);
  tft.drawString(LOGO_DEDICATION, SCREEN_W / 2, 152);

  tft.setFont(&fonts::FreeSansBold24pt7b);
  int wName = tft.textWidth(LOGO_NAME);
  tft.setFont(&fonts::FreeSansBold12pt7b);
  int wTm = LOGO_TM ? 3 + tft.textWidth("TM") : 0;
  int x0 = (SCREEN_W - (wName + wTm)) / 2;
  const int base = 212;
  tft.setTextColor(COL_LOGO, COL_BG);
  tft.setTextDatum(baseline_left);
  tft.setFont(&fonts::FreeSansBold24pt7b);
  tft.drawString(LOGO_NAME, x0, base);
  if (LOGO_TM) {
    tft.setFont(&fonts::FreeSansBold12pt7b);
    tft.drawString("TM", x0 + wName + 3, base - 18);
  }
  if (!line2) return;

  tft.setTextDatum(middle_center);
  tft.setTextColor(COL_DIM, COL_BG);
  tft.drawString("ID " + deckId, SCREEN_W / 2, 262);

  // Hinweis ganz unten; zu lange Texte in der kleineren Schrift
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.setFont(&fonts::FreeSans12pt7b);
  if (tft.textWidth(line2) > SCREEN_W - 10) tft.setFont(&fonts::FreeSans9pt7b);
  tft.drawString(line2, SCREEN_W / 2, SCREEN_H - 20);
}

static void drawWifiOverlay();  // WLAN-Hinweis/-Abfrage über dem Logo (siehe WLAN)

static bool clockValid() { return optClock && clockAtMs; }

// Ortszeit aus der zuletzt von der App gemeldeten Zeit plus der seither vergangenen Zeit
static void localTime(struct tm& t) {
  time_t now = (time_t)clockUnix + (millis() - clockAtMs) / 1000 + clockOffset;
  gmtime_r(&now, &t);
}

// ---------------------------------------------------------------- Block-Welt (Uhr im Ruhebild, ab FW 31)
// Landschaft aus 16x16-Blöcken (Gras, Erde, Stein, Baum), Himmel nach Tageszeit, Sonne/Mond auf ihrer Bahn,
// Uhrzeit und Datum in Pixelschrift. Alles wird aus festen Regeln erzeugt (kein Zufall beim Zeichnen), daher
// lässt sich jeder Ausschnitt jederzeit gleich neu zeichnen – so laufen Figuren auch ohne PSRAM flüssig vorbei.
// Gezeichnet wird in Streifen von 480x16 Pixeln (Puffer 15 KB), die Figuren liegen schon darin (kein Flackern).
#define RGB(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))
static const int WB = 32;            // Block = 16 Textur-Pixel zu je 2x2
static const int W_GROUND = SCREEN_H - 2 * WB;  // Oberkante Gras
static const int W_TIME_Y = 70, W_TIME_S = 11, W_DATE_S = 4;
static const int W_TREE = 13;        // Baum in Spalte 13
static LGFX_Sprite worldStrip(&tft);
static bool worldStripOk = false;

static uint32_t whash(uint32_t x, uint32_t y, uint32_t salt) {
  uint32_t h = x * 374761393u + y * 668265263u + salt * 1442695041u;
  h = (h ^ (h >> 13)) * 1274126177u;
  return h ^ (h >> 16);
}

static uint16_t shade565(uint8_t r, uint8_t g, uint8_t b, int var, bool night) {
  int R = r + var, G = g + var, B = b + var;
  if (night) {
    R = R * 55 / 100;
    G = G * 55 / 100;
    B = B * 60 / 100;
  }
  return RGB(constrain(R, 0, 255), constrain(G, 0, 255), constrain(B, 0, 255));
}

// Pixelschrift 5x7 (Ziffern, Doppelpunkt, Punkt, Leerzeichen und die Buchstaben der Wochentage)
static const char* const* wGlyph(char c) {
  static const char* const G0[] = {"01110", "10001", "10011", "10101", "11001", "10001", "01110"};
  static const char* const G1[] = {"00100", "01100", "00100", "00100", "00100", "00100", "11111"};
  static const char* const G2[] = {"01110", "10001", "00001", "00110", "01000", "10001", "11111"};
  static const char* const G3[] = {"01110", "10001", "00001", "00110", "00001", "10001", "01110"};
  static const char* const G4[] = {"00011", "00101", "01001", "10001", "11111", "00001", "00001"};
  static const char* const G5[] = {"11111", "10000", "11110", "00001", "00001", "10001", "01110"};
  static const char* const G6[] = {"00110", "01000", "10000", "11110", "10001", "10001", "01110"};
  static const char* const G7[] = {"11111", "10001", "00001", "00010", "00100", "00100", "00100"};
  static const char* const G8[] = {"01110", "10001", "10001", "01110", "10001", "10001", "01110"};
  static const char* const G9[] = {"01110", "10001", "10001", "01111", "00001", "00010", "01100"};
  static const char* const GC[] = {"0", "1", "1", "0", "1", "1", "0"};
  static const char* const GP[] = {"0", "0", "0", "0", "0", "1", "1"};
  static const char* const GS[] = {"000", "000", "000", "000", "000", "000", "000"};
  static const char* const GM[] = {"10001", "11011", "10101", "10101", "10001", "10001", "10001"};
  static const char* const GD[] = {"11110", "10001", "10001", "10001", "10001", "10001", "11110"};
  static const char* const GF[] = {"11111", "10000", "10000", "11110", "10000", "10000", "10000"};
  static const char* const GSS[] = {"01111", "10000", "10000", "01110", "00001", "00001", "11110"};
  static const char* const Go[] = {"00000", "00000", "01110", "10001", "10001", "10001", "01110"};
  static const char* const Gi[] = {"1", "0", "1", "1", "1", "1", "1"};
  static const char* const Gr[] = {"0000", "0000", "1011", "1100", "1000", "1000", "1000"};
  static const char* const Ga[] = {"00000", "00000", "01110", "00001", "01111", "10001", "01111"};
  switch (c) {
    case '0': return G0; case '1': return G1; case '2': return G2; case '3': return G3; case '4': return G4;
    case '5': return G5; case '6': return G6; case '7': return G7; case '8': return G8; case '9': return G9;
    case ':': return GC; case '.': return GP; case 'M': return GM; case 'D': return GD; case 'F': return GF;
    case 'S': return GSS; case 'o': return Go; case 'i': return Gi; case 'r': return Gr; case 'a': return Ga;
    default: return GS;
  }
}

static int wTextWidth(const char* s, int sc) {
  int w = 0;
  for (; *s; s++) w += ((int)strlen(wGlyph(*s)[0]) + 1) * sc;
  return w - sc;
}

// Text in den Streifen (oy = Bildschirm-y der Streifen-Oberkante); harter Schatten wie bei Block-Spielen
// skip: Bit i gesetzt = Zeichen i fehlt (im Monsterkampf als Waffe geklaut), der Platz bleibt frei
static void wText(LGFX_Sprite& s, int oy, const char* str, int x, int y, int sc, uint16_t col, uint16_t sh,
                  uint8_t skip = 0) {
  int h = 7 * sc + sc;
  if (y + h < oy || y > oy + s.height()) return;
  for (int pass = 0; pass < 2; pass++) {
    int d = pass == 0 ? max(2, sc / 2) : 0;
    uint16_t c = pass == 0 ? sh : col;
    int cx = x;
    for (const char* p = str; *p; p++) {
      const char* const* g = wGlyph(*p);
      int gw = strlen(g[0]);
      if (!((skip >> (p - str)) & 1))
        for (int r = 0; r < 7; r++)
          for (int k = 0; k < gw; k++)
            if (g[r][k] == '1') s.fillRect(cx + k * sc + d, y + r * sc + d - oy, sc, sc, c);
      cx += (gw + 1) * sc;
    }
  }
}

// Figuren (Seitenansicht nach rechts, Textur-Pixel 3x3): 0 Wanderer, 1 Zombie, 2 Skelett, 3 Spinne
struct WFig {
  int w, h, px;                // Größe in Textur-Pixeln, Textur-Pixel in Display-Pixeln
  const char* const* rows[3];  // drei Geh-Bilder
};
static uint16_t wFigColor(int kind, char ch) {
  switch (ch) {
    case 'h': return RGB(92, 58, 30);   case 'H': return RGB(70, 44, 22);   case 's': return RGB(226, 176, 130);
    case 'S': return RGB(196, 146, 104); case 'e': return RGB(30, 30, 40); case 'b': return RGB(60, 110, 205);
    case 'B': return RGB(42, 84, 165);  case 'p': return RGB(86, 64, 40);   case 'P': return RGB(66, 48, 30);
    case 't': return RGB(70, 110, 56);  case 'T': return RGB(54, 88, 44);   case 'o': return RGB(60, 40, 26);
    case 'g': return RGB(96, 150, 84);  case 'G': return RGB(70, 118, 60);  case 'c': return RGB(112, 82, 52);
    case 'C': return RGB(88, 64, 40);   case 'a': return RGB(92, 92, 104);  case 'A': return RGB(70, 70, 82);
    case 'w': return RGB(222, 222, 212); case 'W': return RGB(170, 170, 164); case 'v': return RGB(122, 82, 40);
    case 'l': return RGB(210, 210, 210); case 'd': return RGB(96, 78, 62);  case 'D': return RGB(70, 56, 46);
    case 'r': return RGB(255, 40, 40);  case 'L': return RGB(140, 124, 108); case 'K': return RGB(100, 88, 76);
    case 'n': return RGB(110, 90, 72);  case 'x': return RGB(214, 196, 150);
    case 'q': return RGB(186, 48, 44);  case 'Q': return RGB(134, 32, 30);  case 'j': return RGB(96, 98, 110);
    case 'J': return RGB(68, 70, 80);   case 'u': return RGB(52, 42, 36);
  }
  return 0;
}
// Wanderer (36): Block-Figur – quadratischer Kopf, gerader Körper, steif schwingende Arme und Beine
// (Seitenansicht, eigene Farben: rotes Shirt mit dunklem Streifen, graue Hose)
static const char* const FW0[] = {"..HHHHHHHH..", "..Hhhhhhhh..", "..Hhsssssh..", "..Hhssssss..", "..Hhsssles..",
                                  "..Hhssssss..", "..ssssssSs..", "..ssssssss..", "....qqqq....", "....qqqq....",
                                  "....qqqq....", "....qqqq....", "....qqqq....", "....qqqq....", "....ssss....",
                                  "....ssss....", "....ssss....", "....ssss....", "....ssss....", "....ssss....",
                                  "....jjjj....", "....jjjj....", "....jjjj....", "....jjjj....", "....jjjj....",
                                  "....jjjj....", "....jjjj....", "....jjjj....", "....jjjj....", "....jjjj....",
                                  "....uuuu....", "....uuuu...."};
static const char* const FW1[] = {"............", "..HHHHHHHH..", "..Hhhhhhhh..", "..Hhsssssh..", "..Hhssssss..",
                                  "..Hhsssles..", "..Hhssssss..", "..ssssssSs..", "..ssssssss..", "....qqqq....",
                                  "...qqqqqQ...", "...qqqqqQ...", "...qqqqqQ...", "..qqqqqqQQ..", "..qqqqqqQQ..",
                                  ".ssssqqqSSS.", ".ssssqqqSSS.", ".ssssqqqSSS.", "ssssQQQQSSSS", "ssssqqqqSSSS",
                                  ".sssqqqjSSS.", "...JJjjjj...", "...JJjjjj...", "..JJJjjjjj..", "..JJJJjjjj..",
                                  ".JJJJJjjjjj.", ".JJJJ..jjjj.", "JJJJJ..jjjjj", "JJJJ....jjjj", "uJJJ....jjju",
                                  "uuu......uuu", "uuu......uuu"};
static const char* const FW2[] = {"............", "..HHHHHHHH..", "..Hhhhhhhh..", "..Hhsssssh..", "..Hhssssss..",
                                  "..Hhsssles..", "..Hhssssss..", "..ssssssSs..", "..ssssssss..", "....qqqq....",
                                  "...Qqqqqq...", "...Qqqqqq...", "...Qqqqqq...", "..QQqqqqqq..", "..QQqqqqqq..",
                                  ".SSSqqqssss.", ".SSSqqqssss.", ".SSSqqqssss.", "SSSSQQQQssss", "SSSSqqqqssss",
                                  ".SSSjqqqsss.", "...jjjjJJ...", "...jjjjJJ...", "..jjjjjJJJ..", "..jjjjJJJJ..",
                                  ".jjjjjJJJJJ.", ".jjjj..JJJJ.", "jjjjj..JJJJJ", "jjjj....JJJJ", "ujjj....JJJu",
                                  "uuu......uuu", "uuu......uuu"};
static const char* const FZ0[] = {"..GGGG......", ".Gggggg.....", ".gggggge....", ".gggggge....", ".Gggggg.....",
                                  "..Gggg......", "..GGgg......", "...gg.......", "..cccc......", "..cccCgggggg",
                                  "..cccCgggggg", "..cCcc......", "..cccc......", "..cccc......", "...aA.......",
                                  "...aA.......", "...aA.......", "...aA.......", "...oo.......", "...oo......."};
static const char* const FZ1[] = {"..GGGG......", ".Gggggg.....", ".gggggge....", ".gggggge....", ".Gggggg.....",
                                  "..Gggg......", "..GGgg......", "...gg.......", "..cccc......", "..cccCgggggg",
                                  "..cccCgggggg", "..cCcc......", "..cccc......", "..cccc......", "..aa.A......",
                                  "..a...A.....", ".aa...A.....", ".a....A.....", ".oo...oo....", "............"};
static const char* const FZ2[] = {"..GGGG......", ".Gggggg.....", ".gggggge....", ".gggggge....", ".Gggggg.....",
                                  "..Gggg......", "..GGgg......", "...gg.......", "..cccc......", "..cccCgggggg",
                                  "..cccCgggggg", "..cCcc......", "..cccc......", "..cccc......", "...aA.......",
                                  "..A.a.......", "..A..a......", ".A...a......", ".oo..oo.....", "............"};
static const char* const FS0[] = {"..WWWW......", ".Wwwwww.....", ".wwwwwww....", ".wwwwewe....", ".Wwwwww.....",
                                  "..WwWw......", "..WwwW......", "...ww.......", "...ww....v..", "..wWww..lv..",
                                  "..wwwwwwwv..", "..wWww..lv..", "...ww....v..", "...ww.......", "...wW.......",
                                  "...wW.......", "...wW.......", "...wW.......", "...ww.......", "...ww......."};
static const char* const FS1[] = {"..WWWW......", ".Wwwwww.....", ".wwwwwww....", ".wwwwewe....", ".Wwwwww.....",
                                  "..WwWw......", "..WwwW......", "...ww.......", "...ww....v..", "..wWww..lv..",
                                  "..wwwwwwwv..", "..wWww..lv..", "...ww....v..", "...ww.......", "..ww.W......",
                                  "..w...W.....", ".ww...W.....", ".w....W.....", ".ww...ww....", "............"};
static const char* const FS2[] = {"..WWWW......", ".Wwwwww.....", ".wwwwwww....", ".wwwwewe....", ".Wwwwww.....",
                                  "..WwWw......", "..WwwW......", "...ww.......", "...ww....v..", "..wWww..lv..",
                                  "..wwwwwwwv..", "..wWww..lv..", "...ww....v..", "...ww.......", "...wW.......",
                                  "..W.w.......", "..W..w......", ".W...w......", ".ww..ww.....", "............"};
// Spinne (34): Kreuz auf dem Hinterleib, Kopf mit roten Augen, je zwei Beine vor/zurück, Körper wippt
static const char* const FP0[] = {".....dddd...............", "..ddddxddddd............", ".Dddxxxxxdddd.nnnr..LK..",
                                  ".DddddxddddddnnnnnrL..L.", "..DDDDDDDDDD.nnnnnn...L.", ".....DDDDLLLLLnnnK....L.",
                                  "...KLLLLL.KLL....L.....L", "..L.......L......L......", "..L.......L......L......",
                                  ".L.......L.......L......", "L........L.......L......"};
static const char* const FP1[] = {"........................", ".....dddd...............", "..ddddxddddd............",
                                  ".Dddxxxxxdddd.nnnr..K...", ".DddddxddddddnnnnnrLL...", "..DDDDDDDDDD.nnnnnn..L..",
                                  ".....DDDDLLLLLnnnLK..L..", "....KLLLLKLL......L...L.", "...L....L.........L...L.",
                                  "...L....L..........L...L", "..L....L...........L...L"};
static const char* const FP2[] = {".....dddd...............", "..ddddxddddd............", ".Dddxxxxxdddd.nnnr.K....",
                                  ".DddddxddddddnnnnnrL....", "..DDDDDDDDDD.nnnnnn.L...", ".....DDDD.LLLLnnnLLKL...",
                                  ".....KLLLLLL.......LL...", ".....L.L............L...", "....L.L.............L...",
                                  "....L.L..............L..", "....LL...............L.."};
// Bild 0 = Mitte, 1 und 2 = Schritt (Reihenfolge beim Gehen 0,1,0,2)
static const WFig W_FIGS[4] = {{12, 32, 2, {FW0, FW1, FW2}}, {12, 20, 3, {FZ0, FZ1, FZ2}},
                               {12, 20, 3, {FS0, FS1, FS2}}, {24, 11, 3, {FP1, FP0, FP2}}};
static const int W_FIG_MAXH = 72;  // höchste Figur (Wanderer 64 px) plus Rand: so hoch wird das Band neu gezeichnet

// laufende Figur: kind -1 = keine
static int walkKind = -1, walkDir = 1, walkFrame = 0;
static float walkX = 0;
static uint32_t walkNextStart = 0, walkLastStep = 0;

static void wFigure(LGFX_Sprite& s, int oy) {
  if (walkKind < 0) return;
  const WFig& f = W_FIGS[walkKind];
  int top = W_GROUND - f.h * f.px;
  if (top > oy + s.height() || W_GROUND < oy) return;
  static const uint8_t SEQ[4] = {0, 1, 0, 2};
  const char* const* rows = f.rows[SEQ[walkFrame % 4]];
  for (int r = 0; r < f.h; r++)
    for (int k = 0; k < f.w; k++) {
      char ch = rows[r][walkDir > 0 ? k : f.w - 1 - k];
      if (ch == '.') continue;
      s.fillRect((int)walkX + k * f.px, top + r * f.px - oy, f.px, f.px, wFigColor(walkKind, ch));
    }
}

// ---------------------------------------------------------------- Monsterkampf (ab FW 35)
// Ganz selten statt einer Figur: Riesenechse gegen Riesenaffe (eigene Figuren, Bilder in monster_art.h),
// etwa 20 s. Sie reißen den Baum aus, werfen Blöcke und Ziffern der Uhr, der Sieger ist Zufall. Der Schaden
// bleibt bis zur nächsten vollen Minute stehen. Ablauf fest nach Zeit (fightT in ms), siehe pollFight().
struct MFig {
  const char* const* rows;
  int w, h;
};
enum { PZ_WALK0, PZ_WALK1, PZ_CLAW, PZ_UP, PZ_STOMP, PZ_ROAR, PZ_DOWN,
       PA_WALK0, PA_WALK1, PA_PUNCH, PA_UP, PA_BEAT0, PA_BEAT1, PA_DOWN };
static const MFig M_FIGS[] = {
    {MZ_WALK0, MZ_WALK0_W, MZ_WALK0_H}, {MZ_WALK1, MZ_WALK1_W, MZ_WALK1_H}, {MZ_CLAW, MZ_CLAW_W, MZ_CLAW_H},
    {MZ_UP, MZ_UP_W, MZ_UP_H},          {MZ_STOMP, MZ_STOMP_W, MZ_STOMP_H}, {MZ_ROAR, MZ_ROAR_W, MZ_ROAR_H},
    {MZ_DOWN, MZ_DOWN_W, MZ_DOWN_H},    {MA_WALK0, MA_WALK0_W, MA_WALK0_H}, {MA_WALK1, MA_WALK1_W, MA_WALK1_H},
    {MA_PUNCH, MA_PUNCH_W, MA_PUNCH_H}, {MA_UP, MA_UP_W, MA_UP_H},          {MA_BEAT0, MA_BEAT0_W, MA_BEAT0_H},
    {MA_BEAT1, MA_BEAT1_W, MA_BEAT1_H}, {MA_DOWN, MA_DOWN_W, MA_DOWN_H}};
static const int M_PX = 3;  // Textur-Pixel in Display-Pixeln

static uint16_t mColor(char ch) {
  switch (ch) {
    case 'g': return RGB(70, 150, 60);   case 'G': return RGB(44, 104, 40);  case 'y': return RGB(232, 176, 86);
    case 'Y': return RGB(200, 140, 60);  case 'o': return RGB(240, 124, 40); case 'e': return RGB(255, 230, 60);
    case 'k': return RGB(24, 20, 20);    case 'w': return RGB(240, 240, 230); case 'f': return RGB(110, 72, 42);
    case 'F': return RGB(74, 48, 28);    case 'c': return RGB(160, 150, 138); case 'C': return RGB(118, 108, 98);
    case 'r': return RGB(200, 40, 40);
  }
  return 0;
}

// Gegenstände: 1 Erdblock, 2 Steinblock, 3 Baum, 4 Zeichen der Uhr
struct MMon {
  int8_t fig;
  int16_t x;
  int8_t dir;  // 1 = Blick nach rechts
  int8_t hold;
  char holdCh;
  bool hidden;
};
struct MShot {
  int8_t kind;
  char ch;
  int16_t x0, y0, x1, y1;
  uint16_t t0, dur;
  int16_t arc;
};
struct MMark {  // Treffer-Stern bzw. Staubwolke
  uint16_t t;
  int16_t x, y;
};
static bool fightOn = false;
static uint32_t fightAt = 0, fightDone = 0;
static uint16_t fightT = 0, fightShakeUntil = 0, fightHitAt[2];
static int fightWinner = 0;  // 0 Echse, 1 Affe
static int8_t fightDigit[2];
static MMon mMon[2];
static MShot mShots[8];
static MMark mHits[4], mDust;
static uint8_t wCrater[SCREEN_W / WB];  // Gras-Block der Spalte zerstört
static bool wTreeGone = false, wDamaged = false;
static uint8_t wTimeGone = 0;  // Bit i = Zeichen i von "hh:mm" fehlt
static int wShake = 0;

static void wResetDamage() {
  memset(wCrater, 0, sizeof(wCrater));
  wTreeGone = false;
  wTimeGone = 0;
  wDamaged = false;
}

static void wObj(LGFX_Sprite& s, int oy, int kind, char ch, int cx, int cy) {
  int y = cy - oy;
  if (y < -60 || y > s.height() + 60) return;
  if (kind == 1 || kind == 2) {
    s.fillRect(cx - 12, y - 12, 24, 24, kind == 1 ? RGB(134, 96, 67) : RGB(125, 125, 125));
    if (kind == 1) s.fillRect(cx - 12, y - 12, 24, 7, RGB(106, 170, 64));
    else s.fillRect(cx - 6, y - 4, 6, 6, RGB(92, 92, 92));
  } else if (kind == 3) {  // Baum quer: Stamm und Krone
    s.fillRect(cx - 34, y - 5, 46, 10, RGB(104, 83, 50));
    s.fillRect(cx + 4, y - 20, 34, 34, RGB(60, 140, 40));
    s.fillRect(cx + 12, y - 12, 10, 10, RGB(44, 110, 30));
  } else if (kind == 4) {
    const char* const* g = wGlyph(ch);
    int gw = strlen(g[0]), sc = 6;
    for (int pass = 0; pass < 2; pass++)
      for (int r = 0; r < 7; r++)
        for (int k = 0; k < gw; k++)
          if (g[r][k] == '1')
            s.fillRect(cx - gw * sc / 2 + k * sc + (pass ? 0 : 3), y - 21 + r * sc + (pass ? 0 : 3), sc, sc,
                       pass ? RGB(255, 255, 255) : RGB(63, 63, 63));
  }
}

static int mTop(const MMon& m) { return W_GROUND - M_FIGS[m.fig].h * M_PX; }

// Mitte über den erhobenen Händen (Posen PZ_UP/PA_UP)
static void mHand(const MMon& m, int& hx, int& hy) {
  const MFig& f = M_FIGS[m.fig];
  float gx = m.fig == PZ_UP ? 16 : 18.5f;
  hx = m.x + (int)((m.dir > 0 ? gx : f.w - 1 - gx) * M_PX) + 1;
  hy = mTop(m) - 14;
}

static void wFight(LGFX_Sprite& s, int oy) {
  if (!fightOn) return;
  for (int i = 0; i < 2; i++) {
    const MMon& m = mMon[i];
    if (m.hidden) continue;
    const MFig& f = M_FIGS[m.fig];
    int top = mTop(m);
    for (int r = 0; r < f.h; r++) {
      int yy = top + r * M_PX - oy;
      if (yy + M_PX <= 0 || yy >= s.height()) continue;
      for (int k = 0; k < f.w; k++) {
        char ch = f.rows[r][m.dir > 0 ? k : f.w - 1 - k];
        if (ch != '.') s.fillRect(m.x + k * M_PX, yy, M_PX, M_PX, mColor(ch));
      }
    }
    if (m.hold) {
      int hx, hy;
      mHand(m, hx, hy);
      wObj(s, oy, m.hold, m.holdCh, hx, hy);
    }
  }
  for (const MShot& p : mShots) {
    if (!p.kind || fightT < p.t0 || fightT >= p.t0 + p.dur) continue;
    float u = (float)(fightT - p.t0) / p.dur;
    int x = p.x0 + (int)((p.x1 - p.x0) * u), y = p.y0 + (int)((p.y1 - p.y0) * u - p.arc * sinf(u * PI));
    wObj(s, oy, p.kind, p.ch, x, y);
  }
  for (const MMark& h : mHits) {  // Treffer: gelber Stern
    if (!h.t || fightT < h.t || fightT >= h.t + 250) continue;
    int y = h.y - oy;
    s.fillRect(h.x - 15, y - 3, 30, 6, RGB(255, 230, 60));
    s.fillRect(h.x - 3, y - 15, 6, 30, RGB(255, 230, 60));
    s.fillRect(h.x - 8, y - 8, 16, 16, RGB(255, 255, 200));
  }
  if (mDust.t && fightT >= mDust.t && fightT < mDust.t + 700) {  // Staubwolke: der Verlierer verschwindet
    int g = 150 + (fightT - mDust.t) / 10;
    for (int k = 0; k < 5; k++)
      s.fillCircle(mDust.x - 40 + k * 20, mDust.y - 20 + (k & 1) * 14 - oy, 18 + (fightT - mDust.t) / 50,
                   RGB(g, g, g));
  }
}

// Himmel, Sonne/Mond, Wolken, Landschaft für einen Streifen (Bildschirm-y oy .. oy+Höhe)
static void wScene(LGFX_Sprite& s, int oy, int hh, int mm) {
  int day = hh * 60 + mm;
  bool night = day < 6 * 60 || day >= 20 * 60;
  bool dusk = (day >= 6 * 60 && day < 7 * 60) || (day >= 19 * 60 && day < 20 * 60);
  uint8_t top[3], hor[3];
  if (night) { top[0] = 6; top[1] = 8; top[2] = 20; hor[0] = 24; hor[1] = 30; hor[2] = 60; }
  else if (dusk) { top[0] = 90; top[1] = 110; top[2] = 190; hor[0] = 250; hor[1] = 160; hor[2] = 110; }
  else { top[0] = 120; top[1] = 167; top[2] = 255; hor[0] = 192; hor[1] = 216; hor[2] = 255; }
  // Sonne bzw. Mond: Bogen von links nach rechts, mittags/mitternachts oben
  float f = night ? (float)((day - 20 * 60 + 1440) % 1440) / (10 * 60) : (float)(day - 6 * 60) / (14 * 60);
  int cx = 30 + (int)(f * (SCREEN_W - 100)), cy = 54 - (int)(36 * (1 - (2 * f - 1) * (2 * f - 1)));
  int y1 = oy + s.height();
  for (int y = oy; y < y1; y += 2) {
    int ty = y / 2;
    for (int x = 0; x < SCREEN_W; x += 2) {
      int tx = x / 2;
      uint16_t c;
      int col = x / WB, blkRow = (SCREEN_H - 1 - y) / WB;  // Block-Reihe von unten (0 = unterste)
      int bx = (x % WB) / 2, by = (y % WB) / 2;          // Textur-Pixel im Block
      uint32_t hsh = whash(tx, ty, 1);
      int var = (int)(hsh % 5) * 6 - 12;
      bool treeTrunk = !wTreeGone && col == W_TREE && blkRow >= 2 && blkRow <= 4;
      bool leaves = !wTreeGone && col >= W_TREE - 1 && col <= W_TREE + 1 && blkRow >= 5 && blkRow <= 6;
      bool crater = blkRow == 1 && col < SCREEN_W / WB && wCrater[col];  // Monsterkampf: Block zerstört
      if (blkRow == 0) {
        c = shade565(125, 125, 125, var * 3 / 2, night);                               // Stein
      } else if (crater && by >= 8) {
        c = shade565(84, 60, 42, var, night);                                          // Loch: dunkle Erde
      } else if (blkRow == 1 && !crater) {
        int grass = 3 + ((whash(bx, col, 7) & 1) ? 1 + (bx % 3 == 0) : 0);
        c = by < grass ? shade565(106, 170, 64, var, night) : shade565(134, 96, 67, var, night);  // Gras/Erde
      } else if (treeTrunk) {
        c = shade565(104, 83, 50, (bx == 4 || bx == 11) ? -20 : var / 2, night);
      } else if (leaves) {
        c = shade565(60, 140, 40, (int)(hsh % 7) * 7 - 25, night);
      } else {
        // Himmel mit Verlauf, Sterne, Wolken, Sonne/Mond
        int fy = constrain(y, 0, 200);  // y < 0 beim Beben im Monsterkampf
        c = RGB(top[0] + (hor[0] - top[0]) * fy / 200, top[1] + (hor[1] - top[1]) * fy / 200,
                top[2] + (hor[2] - top[2]) * fy / 200);
        if (night && y < 100 && (hsh % 211) == 0) c = RGB(255, 255, 255);
        bool cloud = (y >= 26 && y < 40 && x >= 20 && x < 140) || (y >= 16 && y < 26 && x >= 44 && x < 100) ||
                     (y >= 40 && y < 54 && x >= 300 && x < 450) || (y >= 30 && y < 40 && x >= 324 && x < 410);
        if (cloud) c = night ? RGB(40, 46, 80) : RGB(255, 255, 255);
        if (night) {
          if (x >= cx && x < cx + 32 && y >= cy && y < cy + 32) {
            int mx = x - cx, my = y - cy;
            bool crater = (mx >= 6 && mx < 12 && my >= 8 && my < 14) || (mx >= 18 && mx < 24 && my >= 4 && my < 10) ||
                          (mx >= 12 && mx < 18 && my >= 20 && my < 26) || (mx >= 22 && mx < 28 && my >= 18 && my < 24);
            c = crater ? RGB(160, 165, 185) : RGB(205, 210, 225);
          }
        } else {
          if (x >= cx - 6 && x < cx + 38 && y >= cy - 6 && y < cy + 38) c = RGB(255, 250, 200);
          if (x >= cx && x < cx + 32 && y >= cy && y < cy + 32) c = RGB(255, 255, 220);
        }
      }
      s.fillRect(x, y - oy, 2, 2, c);
    }
  }
}

// Bereich y0..y1 der Block-Welt neu zeichnen (Streifen für Streifen, mit Uhrzeit, Datum und Figur)
static void drawWorld(int y0, int y1) {
  if (!worldStripOk) {
    worldStrip.setColorDepth(16);
    worldStripOk = worldStrip.createSprite(SCREEN_W, 16) != nullptr;
    if (!worldStripOk) return;
  }
  static const char* const WD[] = {"So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"};
  struct tm t;
  localTime(t);
  char hm[6], date[20];
  snprintf(hm, sizeof(hm), "%02d:%02d", t.tm_hour, t.tm_min);
  snprintf(date, sizeof(date), "%s %02d.%02d.%04d", WD[t.tm_wday], t.tm_mday, t.tm_mon + 1, t.tm_year + 1900);
  int tw = wTextWidth(hm, W_TIME_S), dw = wTextWidth(date, W_DATE_S);
  int dy = W_TIME_Y + 7 * W_TIME_S + 16;
  y0 = max(0, y0 / 16 * 16);
  tft.startWrite();
  int sh = fightOn ? wShake : 0;  // Beben: Inhalt um sh verschoben zeichnen
  for (int oy = y0; oy < min(y1, SCREEN_H); oy += 16) {
    int sy = oy + sh;
    wScene(worldStrip, sy, t.tm_hour, t.tm_min);
    wText(worldStrip, sy, hm, (SCREEN_W - tw) / 2, W_TIME_Y, W_TIME_S, RGB(255, 255, 255), RGB(63, 63, 63),
          wTimeGone);
    wText(worldStrip, sy, date, (SCREEN_W - dw) / 2, dy, W_DATE_S, RGB(255, 255, 85), RGB(63, 63, 21));
    wFigure(worldStrip, sy);
    wFight(worldStrip, sy);
    worldStrip.pushSprite(0, oy);
    tft.waitDMA();
  }
  tft.endWrite();
}

// Ruhebild mit Uhr (App weg, Zeit bekannt): klein "PC-Deck", groß die Uhrzeit, darunter das Datum
static void drawClock() {
  if (!fightOn && wDamaged) wResetDamage();  // nach dem Monsterkampf: Landschaft wieder heil
  if (optWorld) {
    struct tm t;
    localTime(t);
    drawWorld(0, SCREEN_H);
    clockShownMin = t.tm_hour * 60 + t.tm_min;
    return;
  }
  static const char* const WD[] = {"So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"};
  struct tm t;
  localTime(t);
  char hm[6], date[24];
  snprintf(hm, sizeof(hm), "%02d:%02d", t.tm_hour, t.tm_min);
  snprintf(date, sizeof(date), "%s %02d.%02d.%04d", WD[t.tm_wday], t.tm_mday, t.tm_mon + 1, t.tm_year + 1900);
  lgfx::LovyanGFX& g = haveFrame ? (lgfx::LovyanGFX&)frame : (lgfx::LovyanGFX&)tft;
  if (haveFrame || clockShownMin < 0) {
    g.fillScreen(COL_BG);
    g.setTextDatum(middle_center);
    g.setFont(&fonts::FreeSansBold12pt7b);
    g.setTextColor(COL_LOGO, COL_BG);
    g.drawString("PC-Deck", SCREEN_W / 2, 40);
  } else {
    g.fillRect(0, 85, SCREEN_W, 200, COL_BG);  // nur Uhrzeit und Datum erneuern
  }
  g.setTextDatum(middle_center);
  g.setFont(&fonts::FreeSansBold24pt7b);
  g.setTextSize(3);
  g.setTextColor(COL_TEXT, COL_BG);
  g.drawString(hm, SCREEN_W / 2, 155);
  g.setTextSize(1);
  g.setFont(&fonts::FreeSans18pt7b);
  g.setTextColor(COL_DIM, COL_BG);
  g.drawString(date, SCREEN_W / 2, 255);
  if (haveFrame) frame.pushSprite(&tft, 0, 0);
  clockShownMin = t.tm_hour * 60 + t.tm_min;
}

// Aufbau beenden: das ganze fertige Bild zeigen (Navigationsleiste, Rand, übrige Kacheln)
static void finishBuild() {
  if (buildNext < 0) return;
  buildNext = -1;
  frame.pushSprite(&tft, 0, 0);
  drawOsd();
  drawNetBadge();
}

// Aufbau-Effekt: alle BUILD_STEP_MS die nächste Kachel aus dem fertigen Bild aufdecken
static void pollBuild() {
  if (buildNext < 0 || (int32_t)(millis() - buildAt) < 0) return;
  if (buildNext >= gCols * gRows) {
    finishBuild();
    return;
  }
  int x, y, w, h;
  tileRect(buildNext, x, y, w, h);
  tft.setClipRect(x - 3, y - 3, w + 6, h + 6);
  frame.pushSprite(&tft, 0, 0);
  tft.clearClipRect();
  drawOsd();
  drawNetBadge();
  buildNext++;
  buildAt = millis() + BUILD_STEP_MS;
}

// Ohne PSRAM: den Rand (wie drawBorderTo) unterhalb der Kacheln direkt aufs Display zeichnen – neben und unter
// der Navigationsleiste; über den Kacheln zeichnet ihn schon jede Kachel selbst.
static void drawBorderBelow(int y0) {
  if (!theme.on || !hasPageBg()) return;
  static const int fade[3] = {255, 179, 102};
  for (int Y = y0; Y < SCREEN_H; Y++) {
    uint16_t col = blend565(theme.borderTop, theme.borderBot, Y * 255 / (SCREEN_H - 1));
    for (int k = 0; k < 3; k++) {
      uint16_t c = blend565(0, col, fade[k]);
      tft.drawPixel(k, Y, c);
      tft.drawPixel(SCREEN_W - 1 - k, Y, c);
    }
  }
  for (int k = 0; k < 3; k++) tft.drawFastHLine(k, SCREEN_H - 1 - k, SCREEN_W - 2 * k, blend565(0, theme.borderBot, fade[k]));
  if (y0 == 0)
    for (int k = 0; k < 3; k++) tft.drawFastHLine(k, k, SCREEN_W - 2 * k, blend565(0, theme.borderTop, fade[k]));
}

// Roter Rahmen (3 px am Rand), solange der Drehknopf-Taster lang genug gedrückt ist; danach den Rand wiederherstellen
static void drawKnobFrame(bool on) {
  tft.startWrite();
  if (on) {
    for (int k = 0; k < 3; k++) tft.drawRect(k, k, SCREEN_W - 2 * k, SCREEN_H - 2 * k, COL_LONG);
  } else if (haveFrame && pcOk && !confirmActive) {  // Rand aus dem fertigen Seitenbild zurückholen
    const int r[4][4] = {{0, 0, SCREEN_W, 3}, {0, SCREEN_H - 3, SCREEN_W, 3}, {0, 0, 3, SCREEN_H}, {SCREEN_W - 3, 0, 3, SCREEN_H}};
    for (auto& q : r) {
      tft.setClipRect(q[0], q[1], q[2], q[3]);
      frame.pushSprite(&tft, 0, 0);
    }
    tft.clearClipRect();
  } else if (theme.on && hasPageBg() && pcOk) {
    drawBorderBelow(0);
  } else {
    for (int k = 0; k < 3; k++) tft.drawRect(k, k, SCREEN_W - 2 * k, SCREEN_H - 2 * k, COL_BG);
  }
  tft.endWrite();
}

static void drawPage() {
  if (confirmActive) return;
  if (!pcOk) {  // PC-App meldet sich nicht (PC startet, Anmeldebildschirm, App beendet): Logo bzw. Uhr
    buildNext = -1;
    lastDrawnPage = -1;
    clockShownMin = -1;
    if (clockValid()) drawClock();
    else drawWaiting(nullptr);
    drawWifiOverlay();
    return;
  }
  if (gPages == 0) {
    drawWaiting("Warte auf Konfiguration von der PC-App ...");
    return;
  }
  if (curPage >= gPages) curPage = 0;
  if (haveFrame) {  // ganze Seite unsichtbar zeichnen, dann auf einen Schlag anzeigen
    if (fancy(&frame)) drawBgTo(frame, 0, 0);
    else frame.fillScreen(COL_BG);
    for (int i = 0; i < gCols * gRows; i++) drawTileTo(frame, &frame, 0, 0, i, false, false);
    drawNavTo(frame, &frame);
    if (fancy(&frame)) drawBorderTo(frame, 0, 0);
    bool newPage = curPage != lastDrawnPage;
    lastDrawnPage = curPage;
    if (optBuild && newPage && !osdUntil) {  // neue Seite: Kachel für Kachel aufdecken (pollBuild)
      buildNext = 0;
      buildAt = millis();
      pollBuild();
      return;
    }
    buildNext = -1;
    frame.pushSprite(&tft, 0, 0);
    drawOsd();
    drawNetBadge();
    return;
  }
  lastDrawnPage = curPage;
  // ohne PSRAM: Kachel für Kachel, aber jede fertig gezeichnet und ohne vorher zu löschen
  tft.startWrite();
  int ch = gridH() / gRows;
  if (gRows * ch < gridH()) tft.fillRect(0, gRows * ch, SCREEN_W, gridH() - gRows * ch, COL_BG);
  for (int i = 0; i < gCols * gRows; i++) drawTile(i);
  drawNavTo(tft, nullptr);
  drawBorderBelow(gRows * ch);
  tft.endWrite();
  drawNetBadge();
}

// ---------------------------------------------------------------- Speicher
static void saveLayout() {
  File f = LittleFS.open("/layout.txt", "w");
  if (!f) return;
  f.printf("%d %d %d %s\n", gCols, gRows, gPages, gHash.c_str());
  for (auto& t : tiles) f.printf("%d %d %d %s\n", t.page, t.idx, t.icon ? 1 : 0, t.label.c_str());
  f.close();
}

static void loadLayout() {
  tiles.clear();
  gPages = 0;
  File f = LittleFS.open("/layout.txt", "r");
  if (!f) return;
  String head = f.readStringUntil('\n');
  char hash[64] = {0};
  int c, r, p;
  if (sscanf(head.c_str(), "%d %d %d %63s", &c, &r, &p, hash) == 4) {
    gCols = constrain(c, 1, MAX_COLS);
    gRows = constrain(r, 1, MAX_ROWS);
    gPages = constrain(p, 0, MAX_PAGES);
    gHash = hash;
  }
  while (f.available()) {
    String line = f.readStringUntil('\n');
    int tp, ti, ic, off = 0;
    if (sscanf(line.c_str(), "%d %d %d %n", &tp, &ti, &ic, &off) >= 3) {
      Tile t{(uint8_t)tp, (uint8_t)ti, ic != 0, off > 0 ? line.substring(off) : String("")};
      t.label.trim();
      tiles.push_back(t);
    }
  }
  f.close();
}

static void clearStore() {
  std::vector<String> names;
  File root = LittleFS.open("/");
  File f = root.openNextFile();
  while (f) {
    String n = f.path();
    f.close();
    if (n != "/touch.cal" && n != BOOT_IMG && n != PAGE_IMG && n != THEME_FILE) names.push_back(n);
    f = root.openNextFile();
  }
  root.close();
  for (auto& n : names) LittleFS.remove(n);
}

// ---------------------------------------------------------------- Design laden
static void loadPageBg() {
  if (pageBgFile) pageBgFile.close();
  bgRowY = -1;
  if (!psramFound()) {
    // klassischer ESP32: 77 KB RAM fehlen sonst dem WLAN und den Live-Icons
    pageBgFile = LittleFS.open(PAGE_IMG, "r");
    if (pageBgFile && pageBgFile.size() != PAGE_BYTES) pageBgFile.close();
    return;
  }
  if (!pageBg) pageBg = (uint16_t*)ps_malloc(PAGE_BYTES);
  File f = LittleFS.open(PAGE_IMG, "r");
  bool ok = pageBg && f && f.size() == PAGE_BYTES && f.read((uint8_t*)pageBg, PAGE_BYTES) == PAGE_BYTES;
  if (f) f.close();
  if (!ok && pageBg) {
    free(pageBg);
    pageBg = nullptr;
  }
}

// Zeile: <hash> <kachel oben> <kachel unten> <rand oben> <rand unten> <stil> <deckkraft> (Farben RGB565 hex)
static bool parseTheme(const char* line) {
  char hash[40] = {0};
  unsigned t1, t2, b1, b2, st, al;
  if (sscanf(line, "%39s %x %x %x %x %u %u", hash, &t1, &t2, &b1, &b2, &st, &al) != 7) return false;
  theme.hash = hash;
  theme.tileTop = t1;
  theme.tileBot = t2;
  theme.borderTop = b1;
  theme.borderBot = b2;
  theme.style = st;
  theme.alpha = constrain(al, 0, 255);
  theme.on = true;
  return true;
}

static void loadTheme() {
  theme = Theme();
  File f = LittleFS.open(THEME_FILE, "r");
  if (f) {
    String line = f.readStringUntil('\n');
    f.close();
    parseTheme(line.c_str());
  }
  if (theme.on) loadPageBg();
}

// ---------------------------------------------------------------- Touch
static void calibrateTouch() {
  tft.fillScreen(TFT_BLACK);
  tft.setFont(&fonts::Font2);
  tft.setTextDatum(middle_center);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString("Touch-Kalibrierung: die Ecken der Reihe nach antippen", SCREEN_W / 2, SCREEN_H / 2);
  uint16_t cal[8];
  tft.calibrateTouch(cal, TFT_WHITE, TFT_BLACK, 20);
  File f = LittleFS.open("/touch.cal", "w");
  if (f) {
    f.write((uint8_t*)cal, sizeof(cal));
    f.close();
  }
}

static bool loadCalibration() {
  File f = LittleFS.open("/touch.cal", "r");
  if (!f) return false;
  uint16_t cal[8];
  bool ok = f.size() == sizeof(cal) && f.read((uint8_t*)cal, sizeof(cal)) == sizeof(cal);
  f.close();
  if (ok) tft.setTouchCalibrate(cal);
  return ok;
}

// ---------------------------------------------------------------- Protokoll
// PC -> Deck (eine Zeile pro Befehl, Antwort "ACK" oder "ERR ..."):
//   HELLO                         -> DECK <fw> <breite> <hoehe> <id> <hash>
//   BEGIN <cols> <rows> <pages> <hash>
//   TILE <seite> <kachel> <hatIcon> <beschriftung>
//   ICON <seite> <kachel> <bytes>  + danach <bytes> Rohdaten RGB565 (little endian, 64x64)
//   END                            -> speichern und anzeigen
//   PAGE <n>                       -> Seite wechseln
//   BRI <0..255>                   -> Helligkeit
//   CAL                            -> Touch neu kalibrieren
//   CLR <seite> <kachel>           -> Live-Kachel entfernen (ab FW 6)
//   IMG <boot|page> <bytes>, DATA <n> + n Byte, IMGEND -> Start-/Seitenbild speichern (ab FW 8)
//   THEME off | THEME <hash> <kachel o> <kachel u> <rand o> <rand u> <stil> <deckkraft> (ab FW 8)
//   PING                           -> Lebenszeichen (ab FW 4; ab FW 9 ohne Lebenszeichen nach 5 s nur das Logo)
//   UPD <seite> <kachel> <hatIcon> <beschriftung> [+ 8192 Byte Icon]
//                                  -> Live-Kachel, nur RAM, nur sichtbare Seite (ab FW 2)
// Deck -> PC:
//   PRESS <seite> <kachel>          -> kurz angetippt (beim Loslassen)
//   LONG <seite> <kachel>           -> 0,6-1,5 s gedrückt, beim Loslassen (statt PRESS; ab FW 3, ab FW 7 beim Loslassen)
//   DEL <seite> <kachel>            -> Kachel nach Sicherheitsabfrage löschen (1,5 s halten; ab FW 7)
//   SHOW <seite>                   -> am Deck geblättert (ab FW 2)
//   ROT <+n|-n>                    -> Drehgeber um n Rasten gedreht, + = im Uhrzeigersinn (ab FW 10)
//   KNOB / KNOBLONG                 -> Drehgeber-Taster kurz / ab 0,6 s gedrückt, beim Loslassen (ab FW 10)
//   ROTP <+n|-n>                   -> gedrückt gedreht; das Loslassen danach meldet nichts (ab FW 11)
// PC -> Deck (ab FW 11):
//   OSD <prozent|-> <text>          -> Wert-Anzeige für 1,2 s über der Seite (z. B. "OSD 45 Lautstaerke")
//   TDIAG                          -> Wartung: Touch-Rohwerte (TRAW ...) und MISO-Pegel (TMISO ...)
//   LOGO                           -> LOGO <widmung-hex> <name-hex> <tm 0/1> (ab FW 14, fürs Startbild der App)
//   FWINFO                         -> FWINFO <fw> <typ> <platz> (ab FW 16; platz = max. Größe der .bin)
//   FWBEGIN <bytes> <md5> [<sig>], FWDATA <n> + Daten (<= 4096), FWEND   Firmware aufspielen, Neustart
//                                  über WLAN (ab FW 18) nur mit sig = HMAC-SHA256(Schlüssel, "F"+md5+bytes)
//   FWABORT                        Firmware-Übertragung abbrechen
//   TITLE <seite> <name-hex|->     Name der Seite in der Navigationsleiste (ab FW 17, nur RAM; - = Nummer)
//   CURPAGE                        -> CURPAGE <seite> (ab FW 17; nach einem Neustart der App)
//   OPT <aufbau 0/1> <dimmen s|0> <dimm-hell> <uhr 0/1> [<block-welt 0/1> <figuren 0/1>]   Anzeige-Optionen
//                                  (ab FW 27, die letzten beiden ab FW 31; nur RAM)
//   TIME <unix-utc> <versatz-s>    Uhrzeit für die Uhr im Ruhebild (ab FW 27)
//   TSET <seite> <platz> <hatIcon> <beschriftung>   eine Kachel setzen; Icon vorher per ICON/ICONZ (ab FW 28)
//   TDEL <seite> <platz>           eine Kachel samt Icon löschen (ab FW 28)
//   LHASH <hash>                   Prüfsumme des Layouts nach TSET/TDEL speichern (ab FW 28)
// WLAN (ab FW 12; Texte als Hex, weil Namen/Passwörter Leerzeichen enthalten können):
//   Anmeldung über TCP (gegenseitig, der Schlüssel aus der Kopplung geht nie übers Netz):
//     PC: AUTH1 <nonce_pc>  -> Deck: CHAL <nonce_deck> <HMAC-SHA256(schlüssel, "D" + nonce_pc)>
//     PC: AUTH2 <HMAC-SHA256(schlüssel, "A" + nonce_deck)>  -> Deck: ACK bzw. ERR auth
//   WIFI STATUS                    -> WIFI <ein 0/1> <verbunden 0/1> <ip|-> <ssid-hex|-> <funk an 0/1> <usb 0/1>
//   (ab FW 13: im USB-Betrieb ist der Funk aus; ohne USB fragt das Deck "WLAN einschalten?")
//   WIFI SCAN                      -> NET <rssi> <offen 0/1> <ssid-hex> ...
//   WIFI SET <ssid-hex> <pass-hex> <schlüssel-hex>   (nur USB) WLAN einrichten + koppeln, einschalten
//   WIFI EN <0|1>                   (nur USB) WLAN aus/ein
//   WIFI RESET                     (nur USB) WLAN, Passwort und Kopplung löschen (Deck an anderem PC)
static std::vector<Tile> pending;
static File imgFile;          // Bild-Übertragung (IMG/DATA/IMGEND)
static String imgName;
static unsigned imgLeft = 0;
static int pCols, pRows, pPages;
static String pHash;

// ---------------------------------------------------------------- Kanal: USB oder WLAN
// Geantwortet und gemeldet wird auf dem Kanal, über den zuletzt eine Zeile der PC-App kam.
static Print* io = &Serial;
static Stream* in = &Serial;
static bool fromNet = false;  // Zeile kam über WLAN (dort keine WIFI SET/EN)

// genau n Byte Rohdaten lesen (Icons, Bilder); wartet höchstens ms ohne neue Daten
static size_t readExact(uint8_t* buf, size_t n, uint32_t ms = 3000) {
  size_t got = 0;
  uint32_t t0 = millis();
  while (got < n && millis() - t0 < ms) {
    int a = in->available();
    if (a > 0) {
      got += in->readBytes(buf + got, min((size_t)a, n - got));
      t0 = millis();
    } else {
      delay(1);
    }
  }
  return got;
}

// ---------------------------------------------------------------- WLAN
static Preferences prefs;
static bool wifiEn = false;
static String wifiSsid, wifiPass, netKey;
static WiFiServer netServer(NET_PORT);
static WiFiClient netClient;
static WiFiUDP disc;
static WiFiUDP nbns;  // NetBIOS-Namensdienst (UDP 137): Windows und IP-Scanner zeigen den Namen
static bool netStarted = false, netAuth = false;
static String hostName;      // Name im Netzwerk (Router, mDNS), z. B. "PC-Deck-2"; von der App per NAME
static bool mdnsOn = false;
static String netNonce;  // Zufallszahl des Decks für die laufende Anmeldung
static uint32_t netLast = 0;
static char netBuf[256];
static size_t netLen = 0;

static String fromHex(const char* h) {
  String out;
  for (; h[0] && h[1]; h += 2) {
    char b[3] = {h[0], h[1], 0};
    out += (char)strtol(b, nullptr, 16);
  }
  return out;
}

static String toHex(const String& s) {
  static const char* d = "0123456789abcdef";
  String out;
  for (size_t i = 0; i < s.length(); i++) {
    out += d[(uint8_t)s[i] >> 4];
    out += d[(uint8_t)s[i] & 15];
  }
  return out;
}

// HMAC-SHA256(schlüssel, text) als Hex
static String hmacHex(const String& key, const String& text) {
  uint8_t out[32];
  mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), (const uint8_t*)key.c_str(), key.length(),
                  (const uint8_t*)text.c_str(), text.length(), out);
  static const char* d = "0123456789abcdef";
  String h;
  for (int i = 0; i < 32; i++) {
    h += d[out[i] >> 4];
    h += d[out[i] & 15];
  }
  return h;
}

// Vergleich ohne frühen Abbruch (verrät keine Zeit-Unterschiede)
static bool sameText(const String& a, const String& b) {
  if (a.length() != b.length()) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < a.length(); i++) diff |= a[i] ^ b[i];
  return diff == 0;
}

// Funk: im USB-Betrieb aus (Strom, Wärme). An nur ohne USB nach "Ja", beim Einrichten
// und danach noch WIFI_USB_OFF_MS lang, damit die App den Erfolg sehen kann.
static bool wifiActive = false;
static uint32_t wifiOnAt = 0;
static bool wifiAsk = false;      // Abfrage "WLAN einschalten?" wird gezeigt
#define NET_RESTART_MAGIC 0x57494649u
RTC_NOINIT_ATTR static uint32_t netRestart;  // überlebt ESP.restart(): Update kam über WLAN
static bool autoWifiOnce = false;  // nach so einem Neustart: ohne USB WLAN gleich wieder an
static bool wifiAsked = false;    // seit dem Trennen vom USB schon gefragt
static uint32_t usbSince = 0, noUsbSince = 0;
static bool usbWas = true;
static uint32_t lastSerialRx = 0;

// WLAN-Diagnose (ab FW 37): warum und bei welchem Signal das Deck den Router verloren hat
static volatile bool wifiUp = false;         // mit dem Router verbunden (aus dem WLAN-Ereignis)
static volatile uint8_t wifiLostReason = 0;  // letzter Trenngrund (wifi_err_reason_t), 0 = noch keiner
static volatile uint16_t wifiLostCount = 0;  // so oft seit dem Start verloren
static int8_t wifiRssi = 0;                  // zuletzt gemessenes Signal (dBm), solange verbunden
static volatile int8_t wifiLostRssi = 0;     // Signal kurz vor der letzten Trennung

static void onWifiEvent(arduino_event_id_t ev, arduino_event_info_t info) {
  if (ev == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    wifiUp = true;
  } else if (ev == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    if (wifiUp) {  // nur echte Verluste zählen, nicht jeden erfolglosen Wiederverbindungsversuch
      wifiLostCount++;
      wifiLostRssi = wifiRssi;
      wifiLostReason = info.wifi_sta_disconnected.reason;
    }
    wifiUp = false;
  }
}

// Trenngrund kurz und verständlich (Display)
static const char* wifiReasonText(uint8_t r) {
  switch (r) {
    case WIFI_REASON_BEACON_TIMEOUT: return "Signal verloren";
    case WIFI_REASON_NO_AP_FOUND: return "Router nicht gefunden";
    case WIFI_REASON_AUTH_FAIL: return "Anmeldung abgelehnt";
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT: return "Schluessel-Timeout";
    case WIFI_REASON_ASSOC_LEAVE: return "selbst abgemeldet";
    case WIFI_REASON_AUTH_EXPIRE:
    case WIFI_REASON_AUTH_LEAVE:
    case WIFI_REASON_ASSOC_EXPIRE:
    case WIFI_REASON_ASSOC_TOOMANY:
    case WIFI_REASON_NOT_AUTHED:
    case WIFI_REASON_NOT_ASSOCED: return "vom Router getrennt";
    case WIFI_REASON_CONNECTION_FAIL: return "Verbindung fehlgeschlagen";
    default: return "anderer Grund";
  }
}

static void wifiStart() {
  if (netClient) netClient.stop();
  netAuth = false;
  if (mdnsOn) {
    MDNS.end();
    mdnsOn = false;
  }
  if (!wifiEn || !wifiSsid.length() || !wifiActive) {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    return;
  }
  String host = hostName.length() ? hostName : "PC-Deck-" + deckId.substring(deckId.length() - 4);
  WiFi.setHostname(host.c_str());  // vor mode()/begin(): so meldet es sich beim Router (DHCP)
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(host.c_str());
  WiFi.setSleep(false);  // kurze Reaktionszeit
  WiFi.setAutoReconnect(true);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
}

static void wifiRadio(bool on) {
  wifiActive = on && wifiEn && wifiSsid.length();
  if (wifiActive) wifiOnAt = millis();
  wifiStart();
}

static void loadWifi() {
  prefs.begin("pcdeck", true);
  wifiEn = prefs.getBool("en", false);
  wifiSsid = prefs.getString("ssid", "");
  wifiPass = prefs.getString("pass", "");
  netKey = prefs.getString("key", "");
  hostName = prefs.getString("host", "");
  prefs.end();
  WiFi.onEvent(onWifiEvent);
  wifiRadio(false);  // beim Start aus; ohne USB fragt das Deck
}

static void saveWifi() {
  prefs.begin("pcdeck", false);
  prefs.putBool("en", wifiEn);
  prefs.putString("ssid", wifiSsid);
  prefs.putString("pass", wifiPass);
  prefs.putString("key", netKey);
  prefs.end();
}

static String wifiIp() { return WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : String("-"); }

static void handleWifi(char* arg) {
  if (strcmp(arg, "STATUS") == 0) {
    io->printf("WIFI %d %d %s %s %d %d\n", wifiEn ? 1 : 0, WiFi.status() == WL_CONNECTED ? 1 : 0, wifiIp().c_str(),
               wifiSsid.length() ? toHex(wifiSsid).c_str() : "-", wifiActive ? 1 : 0, usbWas ? 1 : 0);
    io->println("ACK");
  } else if (strcmp(arg, "SCAN") == 0) {
    if (WiFi.getMode() == WIFI_OFF) WiFi.mode(WIFI_STA);
    int n = WiFi.scanNetworks();
    for (int k = 0; k < n; k++) {
      if (!WiFi.SSID(k).length()) continue;
      io->printf("NET %d %d %s\n", WiFi.RSSI(k), WiFi.encryptionType(k) == WIFI_AUTH_OPEN ? 1 : 0,
                 toHex(WiFi.SSID(k)).c_str());
    }
    WiFi.scanDelete();
    if (!wifiActive) WiFi.mode(WIFI_OFF);
    io->println("ACK");
  } else if (fromNet) {
    io->println("ERR usb");  // Einrichten nur per USB
  } else if (strncmp(arg, "SET ", 4) == 0) {
    char ssid[80] = {0}, pass[140] = {0}, key[80] = {0};
    if (sscanf(arg + 4, "%79s %139s %79s", ssid, pass, key) != 3) {
      io->println("ERR wifi");
      return;
    }
    wifiSsid = fromHex(ssid);
    wifiPass = strcmp(pass, "-") == 0 ? String("") : fromHex(pass);
    netKey = key;
    wifiEn = true;
    saveWifi();
    wifiRadio(true);  // verbinden; am USB nach WIFI_USB_OFF_MS wieder aus
    io->println("ACK");
  } else if (strcmp(arg, "RESET") == 0) {  // anderer PC: WLAN, Passwort und Kopplung löschen
    wifiSsid = "";
    wifiPass = "";
    netKey = "";
    wifiEn = false;
    saveWifi();
    wifiRadio(false);
    io->println("ACK");
  } else if (strncmp(arg, "EN ", 3) == 0) {
    wifiEn = atoi(arg + 3) != 0;
    saveWifi();
    wifiRadio(wifiEn);
    io->println("ACK");
  } else {
    io->println("ERR wifi");
  }
}

// ---------------------------------------------------------------- Firmware-Update
static size_t fwTotal = 0, fwDone = 0;
static bool fwViaNet = false;  // Update kam über WLAN
static uint32_t fwLastData = 0;  // letzte Daten des laufenden Updates (Abbruch nach 15 s Stille)
static int fwShown = -1;

static void drawFwProgress(const char* text) {
  int pct = fwTotal ? (int)(fwDone * 100 / fwTotal) : 0;
  tft.startWrite();
  if (fwShown < 0) {
    tft.fillScreen(COL_BG);
    tft.setTextDatum(middle_center);
    tft.setFont(&fonts::FreeSansBold12pt7b);
    tft.setTextColor(COL_TEXT, COL_BG);
    tft.drawString("Firmware-Update", SCREEN_W / 2, 110);
    tft.drawRect(60, 150, SCREEN_W - 120, 24, COL_DIM);
  }
  tft.fillRect(62, 152, (SCREEN_W - 124) * pct / 100, 20, COL_ACCENT);
  tft.fillRect(0, 190, SCREEN_W, 40, COL_BG);
  tft.setFont(&fonts::FreeSans9pt7b);
  tft.setTextDatum(middle_center);
  tft.setTextColor(COL_DIM, COL_BG);
  tft.drawString(text ? String(text) : String(pct) + " %  -  USB nicht trennen", SCREEN_W / 2, 205);
  tft.endWrite();
  fwShown = pct;
}

// OPT <aufbau> <dimm-s> <dimm-hell> <uhr> <block-welt> <figuren>; ab FW 35 im NVS, damit Uhr und Block-Welt
// auch nach dem Einschalten ohne PC laufen
static void applyOpt(const char* s, bool save) {
  int b = 0, sec = 0, bd = 25, c = 0, wo = 1, wa = 1;
  sscanf(s, "%d %d %d %d %d %d", &b, &sec, &bd, &c, &wo, &wa);
  if ((bool)wo != optWorld) clockShownMin = -1;
  optWorld = wo;
  optWalkers = wa;
  optBuild = b && haveFrame;
  dimAfterMs = sec > 0 ? (uint32_t)sec * 1000 : 0;
  briDim = constrain(bd, 1, 255);
  if (dimmed && !dimAfterMs) wakeUp();
  else if (dimmed) tft.setBrightness(min(briDim, briNormal));
  optClock = c;
  if (!save) return;
  static String saved;
  char norm[48];
  snprintf(norm, sizeof(norm), "%d %d %d %d %d %d", b, sec, bd, c, wo, wa);
  if (!saved.length()) {
    prefs.begin("pcdeck", true);
    saved = prefs.getString("opt", "");
    prefs.end();
  }
  if (saved == norm) return;  // nur bei Änderung in den Flash schreiben
  saved = norm;
  prefs.begin("pcdeck", false);
  prefs.putString("opt", norm);
  prefs.end();
}

static void loadOpt() {
  prefs.begin("pcdeck", true);
  String s = prefs.getString("opt", "");
  prefs.end();
  if (s.length()) applyOpt(s.c_str(), false);
}

static void handleLine(char* line) {
  lastPcContact = millis();
  bool firstContact = !pcSeen;
  pcSeen = true;
  if (!pcOk) {  // App ist (wieder) da: Logo weg, Kacheln zeigen
    pcOk = true;
    drawPage();
  }

  if (strncmp(line, "HELLO", 5) == 0) {
    // 7. Feld (ab FW 5): 1 = Seite wird im PSRAM gezeichnet (flimmerfrei), 0 = Kachel für Kachel
    // 8. Feld (ab FW 8): Prüfsumme des gespeicherten Designs ("none" = keins)
    // 9. Feld (ab FW 12): IP im WLAN oder "-"
    io->printf("DECK %s %d %d %s %s %d %s %s\n", FW_VERSION, SCREEN_W, SCREEN_H, deckId.c_str(), gHash.c_str(),
               haveFrame ? 1 : 0, theme.on ? theme.hash.c_str() : "none", wifiIp().c_str());
    if (firstContact && gPages == 0) drawWaiting("Mit PC verbunden - warte auf Konfiguration ...");
  } else if (strncmp(line, "BEGIN ", 6) == 0) {
    char hash[64] = {0};
    if (sscanf(line + 6, "%d %d %d %63s", &pCols, &pRows, &pPages, hash) != 4) {
      io->println("ERR begin");
      return;
    }
    pCols = constrain(pCols, 1, MAX_COLS);
    pRows = constrain(pRows, 1, MAX_ROWS);
    pPages = constrain(pPages, 0, MAX_PAGES);
    pHash = hash;
    pending.clear();
    for (auto& t : pageTitle) t = "";
    clearStore();
    clearLive();
    drawWaiting("Empfange Konfiguration ...");
    io->println("ACK");
  } else if (strncmp(line, "TILE ", 5) == 0) {
    int p, i, ic, off = 0;
    if (sscanf(line + 5, "%d %d %d %n", &p, &i, &ic, &off) < 3) {
      io->println("ERR tile");
      return;
    }
    Tile t{(uint8_t)p, (uint8_t)i, ic != 0, off > 0 ? String(line + 5 + off) : String("")};
    t.label.trim();
    pending.push_back(t);
    io->println("ACK");
  } else if (strncmp(line, "ICON ", 5) == 0) {
    int p, i, n;
    if (sscanf(line + 5, "%d %d %d", &p, &i, &n) != 3 || n != ICON_BYTES) {
      io->println("ERR icon");
      return;
    }
    size_t got = readExact((uint8_t*)iconBuf, ICON_BYTES);
    if (got != ICON_BYTES) {
      io->printf("ERR short %u\n", (unsigned)got);
      return;
    }
    File f = LittleFS.open(iconPath(p, i), "w");
    if (!f) {
      io->println("ERR fs");
      return;
    }
    size_t w = f.write((uint8_t*)iconBuf, ICON_BYTES);
    f.close();
    io->println(w == ICON_BYTES ? "ACK" : "ERR full");
  } else if (strncmp(line, "ICONZ ", 6) == 0) {
    // ICONZ <seite> <platz> <bytes> + komprimierte Daten (ab FW 22)
    int p, i, n;
    if (sscanf(line + 6, "%d %d %d", &p, &i, &n) != 3 || n <= 0 || n >= ICON_BYTES) {
      io->println("ERR iconz");
      return;
    }
    if (readExact(zBuf, n) != (size_t)n) {
      io->println("ERR short");
      return;
    }
    if (!unpackIcon(zBuf, n, iconBuf)) {  // prüfen, bevor es gespeichert wird
      io->println("ERR iconz");
      return;
    }
    File f = LittleFS.open(iconPath(p, i), "w");
    if (!f) {
      io->println("ERR fs");
      return;
    }
    size_t w = f.write(zBuf, n);
    f.close();
    io->println(w == (size_t)n ? "ACK" : "ERR full");
  } else if (strcmp(line, "END") == 0) {
    tiles = pending;
    pending.clear();
    gCols = pCols;
    gRows = pRows;
    gPages = pPages;
    gHash = pHash;
    saveLayout();
    if (curPage >= gPages) curPage = 0;  // ab FW 28: auf der angezeigten Seite bleiben
    drawPage();
    io->println("ACK");
  } else if (strncmp(line, "TSET ", 5) == 0 || strncmp(line, "TDEL ", 5) == 0) {
    // eine Kachel setzen bzw. löschen, ohne das übrige Layout anzufassen (ab FW 28)
    bool del = line[1] == 'D';
    int p, i, ic = 0, off = 0;
    int n = del ? sscanf(line + 5, "%d %d", &p, &i) : sscanf(line + 5, "%d %d %d %n", &p, &i, &ic, &off);
    if (n < (del ? 2 : 3) || p < 0 || p >= gPages || i < 0 || i >= gCols * gRows) {
      io->println("ERR tset");
      return;
    }
    for (size_t k = 0; k < tiles.size(); k++) {
      if (tiles[k].page == p && tiles[k].idx == i) {
        tiles.erase(tiles.begin() + k);
        break;
      }
    }
    if (del || !ic) LittleFS.remove(iconPath(p, i));
    if (!del) {
      Tile t{(uint8_t)p, (uint8_t)i, ic != 0, off > 0 ? String(line + 5 + off) : String("")};
      t.label.trim();
      tiles.push_back(t);
    }
    saveLayout();
    if (p == curPage) {  // nur diese Kachel neu zeichnen
      live[i].used = false;
      tft.startWrite();
      drawTile(i, i == downTile, longSent);
      tft.endWrite();
    }
    io->println("ACK");
  } else if (strncmp(line, "LHASH ", 6) == 0) {
    char hash[64] = {0};
    sscanf(line + 6, "%63s", hash);
    gHash = hash;
    saveLayout();
    io->println("ACK");
  } else if (strncmp(line, "UPD ", 4) == 0) {
    // Live-Kachel: nur RAM, nur sichtbare Seite. UPD <seite> <platz> <icon> [<bytes>] <text>
    // icon: 0 = keins, 1 = roh (ICON_BYTES folgen), 2 = komprimiert (ab FW 22, <bytes> folgen)
    int p, i, ic, off = 0, n = ICON_BYTES;
    if (sscanf(line + 4, "%d %d %d %n", &p, &i, &ic, &off) < 3 || i < 0 || i >= MAX_COLS * MAX_ROWS) {
      io->println("ERR upd");
      return;
    }
    if (ic == 2) {
      int off2 = 0;
      if (sscanf(line + 4 + off, "%d %n", &n, &off2) < 1 || n <= 0 || n >= ICON_BYTES) {
        io->println("ERR upd");
        return;
      }
      off += off2;
    }
    Live& l = live[i];
    bool visible = (p == curPage && gPages > 0);
    if (ic) {
      if (l.cap < n) {  // Puffer passend anlegen (komprimiert meist nur 1-3 KB statt 8 KB)
        free(l.buf);
        l.buf = (uint8_t*)malloc(n);
        l.cap = l.buf ? n : 0;
      }
      uint8_t* dst = l.buf ? l.buf : zBuf;  // ohne Speicher: Daten trotzdem abholen
      if (readExact(dst, n) != (size_t)n) {
        io->println("ERR short");
        return;
      }
      if (!l.buf) {
        io->println("ERR mem");
        return;
      }
      l.len = n;
    }
    if (visible) {
      l.used = true;
      l.icon = ic != 0;
      l.label = off > 0 ? String(line + 4 + off) : String("");
      l.label.trim();
      tft.startWrite();
      drawTile(i, i == downTile, longSent);
      tft.endWrite();
    }
    io->println("ACK");
  } else if (strncmp(line, "CLR ", 4) == 0) {
    // Live-Kachel entfernen (z. B. Plus-Kachel), zurück zum gespeicherten Bild bzw. leeren Platz
    int p, i;
    if (sscanf(line + 4, "%d %d", &p, &i) != 2 || i < 0 || i >= MAX_COLS * MAX_ROWS) {
      io->println("ERR clr");
      return;
    }
    if (p == curPage && live[i].used) {
      live[i].used = false;
      tft.startWrite();
      drawTile(i, i == downTile, longSent);
      tft.endWrite();
    }
    io->println("ACK");
  } else if (strcmp(line, "CURPAGE") == 0) {
    io->printf("CURPAGE %d\n", curPage);
    io->println("ACK");
  } else if (strncmp(line, "TITLE ", 6) == 0) {
    int p = -1, off = 0;
    if (sscanf(line + 6, "%d %n", &p, &off) < 1 || p < 0 || p >= MAX_PAGES || off == 0) {
      io->println("ERR title");
      return;
    }
    String t = strcmp(line + 6 + off, "-") == 0 ? String("") : fromHex(line + 6 + off);
    t.trim();
    if (t.length() > 28) t = t.substring(0, 28);
    if (t != pageTitle[p]) {
      pageTitle[p] = t;
      if (p == curPage && pcOk && gPages > 1) drawPage();
    }
    io->println("ACK");
  } else if (strncmp(line, "PAGE ", 5) == 0) {
    int p = atoi(line + 5);
    if (p >= 0 && p < gPages && p != curPage) {
      curPage = p;
      clearLive();
      wakeUp();
      drawPage();
    }
    io->println("ACK");
  } else if (strncmp(line, "BRI ", 4) == 0) {
    briNormal = constrain(atoi(line + 4), 5, 255);
    if (!dimmed) tft.setBrightness(briNormal);
    io->println("ACK");
  } else if (strncmp(line, "OPT ", 4) == 0) {
    applyOpt(line + 4, true);
    io->println("ACK");
  } else if (strncmp(line, "TIME ", 5) == 0) {
    unsigned long u = 0;
    long off = 0;
    sscanf(line + 5, "%lu %ld", &u, &off);
    clockUnix = u;
    clockOffset = off;
    clockAtMs = millis() ? millis() : 1;
    io->println("ACK");
  } else if (strncmp(line, "IMG ", 4) == 0) {
    // Bild-Übertragung starten: IMG <boot|page> <bytes>; danach DATA-Blöcke und IMGEND
    char name[8] = {0};
    unsigned n = 0;
    sscanf(line + 4, "%7s %u", name, &n);
    imgName = strcmp(name, "boot") == 0 ? BOOT_IMG : (strcmp(name, "page") == 0 ? PAGE_IMG : "");
    imgLeft = n;
    if (!imgName.length() || n != (imgName == BOOT_IMG ? BOOT_BYTES : PAGE_BYTES)) {
      io->println("ERR img");
      return;
    }
    if (imgFile) imgFile.close();
    imgFile = LittleFS.open(imgName + ".tmp", "w");
    io->println(imgFile ? "ACK" : "ERR fs");
  } else if (strncmp(line, "DATA ", 5) == 0) {
    // Block mit Bilddaten (höchstens 4096 Byte); ACK erst nach dem Schreiben = Flusskontrolle
    unsigned n = atoi(line + 5);
    static uint8_t blk[4096];
    if (!imgFile || n == 0 || n > sizeof(blk) || n > imgLeft) {
      io->println("ERR data");
      return;
    }
    if (readExact(blk, n) != n) {
      io->println("ERR short");
      return;
    }
    imgFile.write(blk, n);
    imgLeft -= n;
    io->println("ACK");
  } else if (strcmp(line, "IMGEND") == 0) {
    if (!imgFile || imgLeft != 0) {
      if (imgFile) imgFile.close();
      io->println("ERR imgend");
      return;
    }
    imgFile.close();
    bool page = imgName == PAGE_IMG;
    if (page && pageBgFile) pageBgFile.close();
    LittleFS.remove(imgName);
    LittleFS.rename(imgName + ".tmp", imgName);
    if (page && theme.on) loadPageBg();
    io->println("ACK");
  } else if (strncmp(line, "THEME ", 6) == 0) {
    // THEME off | THEME <hash> <kachel oben> <kachel unten> <rand oben> <rand unten> <stil> <deckkraft>
    if (strcmp(line + 6, "off") == 0) {
      LittleFS.remove(THEME_FILE);
      theme = Theme();
    } else {
      File f = LittleFS.open(THEME_FILE, "w");
      if (f) {
        f.println(line + 6);
        f.close();
      }
      loadTheme();
    }
    clearLive();
    drawPage();
    io->println("ACK");
  } else if (strcmp(line, "LS") == 0) {
    // Wartung: Dateien im Flash auflisten ("FILE <name> <bytes>" ..., dann "FS <belegt> <gesamt>")
    File root = LittleFS.open("/");
    File f = root.openNextFile();
    while (f) {
      io->printf("FILE %s %u\n", f.path(), (unsigned)f.size());
      f.close();
      f = root.openNextFile();
    }
    root.close();
    io->printf("FS %u %u theme=%d pagebg=%d\n", (unsigned)LittleFS.usedBytes(), (unsigned)LittleFS.totalBytes(),
                  theme.on ? 1 : 0, hasPageBg() ? 1 : 0);
    io->println("ACK");
  } else if (strncmp(line, "OSD ", 4) == 0) {
    // OSD <prozent|-> <text>
    char* p = line + 4;
    osdPct = (*p == '-') ? -1 : atoi(p);
    char* sp = strchr(p, ' ');
    osdText = sp ? String(sp + 1) : String("");
    osdUntil = millis() + OSD_MS;
    if (!osdUntil) osdUntil = 1;
    tft.startWrite();
    drawOsd();
    tft.endWrite();
    io->println("ACK");
  } else if (strcmp(line, "TDIAG") == 0) {
    // Wartung: Touch-Chip prüfen. TRAW <anzahl> <x> <y> <druck> (Rohwerte), dann der Pegel der
    // MISO-Leitung mit Pull-up/-down, Touch-CS inaktiv (Chip hochohmig) und aktiv (Chip treibt).
    for (int k = 0; k < 10; k++) {
      lgfx::touch_point_t tp;
      int n = tft.getTouchRaw(&tp, 1);
      io->printf("TRAW %d %d %d %d\n", n, n ? tp.x : 0, n ? tp.y : 0, n ? tp.size : 0);
      delay(20);
    }
#ifdef PIN_TOUCH_CS  // nur beim SPI-Touch (XPT2046); der NS2009 hängt am I2C
    gpio_num_t miso = (gpio_num_t)PIN_MISO;
    int lv[2][2];
    for (int cs = 0; cs < 2; cs++) {
      digitalWrite(PIN_TOUCH_CS, cs ? LOW : HIGH);
      gpio_set_pull_mode(miso, GPIO_PULLUP_ONLY);
      delayMicroseconds(200);
      lv[cs][0] = gpio_get_level(miso);
      gpio_set_pull_mode(miso, GPIO_PULLDOWN_ONLY);
      delayMicroseconds(200);
      lv[cs][1] = gpio_get_level(miso);
    }
    digitalWrite(PIN_TOUCH_CS, HIGH);
    gpio_set_pull_mode(miso, GPIO_FLOATING);
    io->printf("TMISO cs_aus=%d%d cs_an=%d%d\n", lv[0][0], lv[0][1], lv[1][0], lv[1][1]);
#endif
    io->println("ACK");
  } else if (strncmp(line, "NAME ", 5) == 0) {
    // NAME <name-hex> (ab FW 23): Name im Netzwerk. Nur Buchstaben, Ziffern, Bindestrich, max. 32 Zeichen;
    // gilt ab dem nächsten WLAN-Verbinden
    String raw = fromHex(line + 5), host;
    for (size_t k = 0; k < raw.length() && host.length() < 32; k++) {
      char c = raw[k];
      if (isalnum((unsigned char)c)) host += c;
      else if (host.length() && host[host.length() - 1] != '-') host += '-';
    }
    while (host.endsWith("-")) host.remove(host.length() - 1);
    if (!host.length()) {
      io->println("ERR name");
      return;
    }
    if (host != hostName) {
      hostName = host;
      prefs.begin("pcdeck", false);
      prefs.putString("host", hostName);
      prefs.end();
      if (mdnsOn) {  // <name>.local sofort umstellen (pollNet startet mDNS neu); Router erst beim nächsten Verbinden
        MDNS.end();
        mdnsOn = false;
      }
    }
    io->println("ACK");
  } else if (strcmp(line, "LOGO") == 0) {
    io->printf("LOGO %s %s %d\n", toHex(LOGO_DEDICATION).c_str(), toHex(LOGO_NAME).c_str(), LOGO_TM ? 1 : 0);
    io->println("ACK");
  } else if (strcmp(line, "FWINFO") == 0) {
    const esp_partition_t* next = esp_ota_get_next_update_partition(nullptr);
    io->printf("FWINFO %s%u\n", FW_TAG + 10, next ? (unsigned)next->size : 0u);  // FW_TAG: "PCDECK-FW <fw> <typ> "
    io->println("ACK");
  } else if (strncmp(line, "FWBEGIN ", 8) == 0) {
    unsigned n = 0;
    char md5[40] = {0}, sig[80] = {0};
    if (sscanf(line + 8, "%u %39s %79s", &n, md5, sig) < 2 || strlen(md5) != 32) {
      io->println("ERR fwbegin");
      return;
    }
    // über WLAN nur mit Unterschrift der gekoppelten App: Die MD5 wird am Ende geprüft, die Unterschrift
    // über MD5 und Größe beweist, dass die Datei von der App mit dem Kopplungsschlüssel kommt
    if (fromNet && !(netKey.length() && sameText(String(sig), hmacHex(netKey, "F" + String(md5) + String(n))))) {
      io->println("ERR fwsig");
      return;
    }
    fwViaNet = fromNet;
    if (Update.isRunning()) Update.abort();
    if (!Update.begin(n, U_FLASH)) {
      io->printf("ERR fw %s\n", Update.errorString());
      return;
    }
    Update.setMD5(md5);
    fwTotal = n;
    fwDone = 0;
    fwLastData = millis();
    fwShown = -1;
    drawFwProgress(nullptr);
    io->println("ACK");
  } else if (strncmp(line, "FWDATA ", 7) == 0) {
    static uint8_t fblk[4096];
    unsigned n = atoi(line + 7);
    if (!Update.isRunning() || n == 0 || n > sizeof(fblk) || fwDone + n > fwTotal) {
      io->println("ERR fwdata");
      return;
    }
    if (readExact(fblk, n) != n) {
      io->println("ERR short");
      return;
    }
    if (Update.write(fblk, n) != n) {
      io->printf("ERR fw %s\n", Update.errorString());
      return;
    }
    fwDone += n;
    fwLastData = millis();
    if ((int)(fwDone * 100 / fwTotal) != fwShown) drawFwProgress(nullptr);
    io->println("ACK");
  } else if (strcmp(line, "FWEND") == 0) {
    if (!Update.isRunning() || fwDone != fwTotal || !Update.end()) {
      io->printf("ERR fw %s\n", Update.errorString());
      if (Update.isRunning()) Update.abort();
      fwShown = -1;
      drawPage();
      return;
    }
    drawFwProgress("Fertig - Neustart ...");
    io->println("ACK");
    io->flush();
    delay(500);
    if (fwViaNet) netRestart = NET_RESTART_MAGIC;  // nach dem Neustart WLAN ohne Abfrage wieder an
    ESP.restart();
  } else if (strcmp(line, "FWABORT") == 0) {
    if (Update.isRunning()) Update.abort();
    fwShown = -1;
    drawPage();
    io->println("ACK");
  } else if (strcmp(line, "PING") == 0) {
    io->println("ACK");  // Lebenszeichen der PC-App (ab FW 4)
  } else if (strcmp(line, "WSTAT") == 0) {
    // WLAN-Diagnose (ab FW 37): Signal, letzter Trenngrund, Anzahl Verluste, Signal davor, Laufzeit, Startgrund
    io->printf("WSTAT %d %d %d %d %lu %d\n", WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0, wifiLostReason,
               wifiLostCount, wifiLostRssi, (unsigned long)(millis() / 1000), (int)esp_reset_reason());
    io->println("ACK");
  } else if (strcmp(line, "CAL") == 0) {
    io->println("ACK");
    calibrateTouch();
    drawPage();
  } else if (strncmp(line, "WIFI ", 5) == 0) {
    handleWifi(line + 5);
  } else if (line[0] != 0) {
    io->println("ERR unknown");
  }
}

static char lineBuf[256];
static size_t lineLen = 0;

static void pollSerial() {
  while (Serial.available()) {
    int c = Serial.read();
    if (c < 0) break;
    lastSerialRx = millis();
    if (c == '\n') {
      lineBuf[lineLen] = 0;
      lineLen = 0;
      io = &Serial;
      in = &Serial;
      fromNet = false;
      bool wasNet = viaNet;
      viaNet = false;
      handleLine(lineBuf);
      if (wasNet && pcOk) drawPage();  // wieder per USB: WLAN-Zeichen weg
    } else if (c != '\r' && lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = (char)c;
    }
  }
}

// WLAN: Suche beantworten, eine gekoppelte PC-App per TCP annehmen und ihre Zeilen auswerten
// NetBIOS (RFC 1002): beantwortet Namensabfragen (NB) für den eigenen Namen und Statusabfragen (NBSTAT),
// über die z. B. Advanced IP Scanner und "nbtstat -A" den Namen eines Geräts ermitteln.
static void nbnsPoll() {
  int n = nbns.parsePacket();
  if (n <= 0) return;
  uint8_t q[64];
  int len = nbns.read(q, sizeof(q));
  // Kopf 12 Byte, Name 34 Byte (Länge 0x20 + 32 Zeichen + 0), Typ, Klasse; nur Anfragen mit einer Frage
  if (len < 50 || (q[2] & 0x80) || q[5] != 1 || q[12] != 0x20 || q[45] != 0) return;
  uint16_t type = (q[46] << 8) | q[47];
  char asked[17];
  for (int i = 0; i < 16; i++) asked[i] = ((q[13 + 2 * i] - 'A') << 4) | ((q[14 + 2 * i] - 'A') & 0x0F);
  asked[16] = 0;
  String host = hostName.length() ? hostName : "PC-Deck-" + deckId.substring(deckId.length() - 4);
  host = host.substring(0, 15);
  host.toUpperCase();
  char mine[16];
  snprintf(mine, sizeof(mine), "%-15s", host.c_str());
  bool isMine = memcmp(asked, mine, 15) == 0;
  bool isAll = asked[0] == '*';
  uint8_t r[160];
  memcpy(r, q, 2);            // gleiche Kennung
  r[2] = (type == 0x20) ? 0x85 : 0x84;  // Antwort, maßgeblich
  r[3] = 0;
  r[4] = 0; r[5] = 0;          // keine Frage
  r[6] = 0; r[7] = 1;          // eine Antwort
  memset(r + 8, 0, 4);
  memcpy(r + 12, q + 12, 34);  // erfragter Name
  r[46] = q[46]; r[47] = q[47];
  r[48] = 0; r[49] = 1;        // Klasse IN
  int p = 50;
  if (type == 0x20 && isMine && asked[15] == 0x00) {
    uint32_t ttl = 300;
    r[p++] = ttl >> 24; r[p++] = ttl >> 16; r[p++] = ttl >> 8; r[p++] = ttl;
    r[p++] = 0; r[p++] = 6;    // Datenlänge
    r[p++] = 0; r[p++] = 0;    // eindeutiger Name, B-Knoten
    IPAddress ip = WiFi.localIP();
    for (int i = 0; i < 4; i++) r[p++] = ip[i];
  } else if (type == 0x21 && (isMine || isAll)) {
    // zwei Namen: <Name><00> (Arbeitsstation) und WORKGROUP<00> (Gruppe), dann 46 Byte Statistik (MAC vorn)
    const int names = 2, rdlen = 1 + names * 18 + 46;
    memset(r + p, 0, 4); p += 4;  // TTL 0
    r[p++] = rdlen >> 8; r[p++] = rdlen & 0xFF;
    r[p++] = names;
    memcpy(r + p, mine, 15); r[p + 15] = 0x00; r[p + 16] = 0x04; r[p + 17] = 0x00; p += 18;  // aktiv, eindeutig
    char wg[16];
    snprintf(wg, sizeof(wg), "%-15s", "WORKGROUP");
    memcpy(r + p, wg, 15); r[p + 15] = 0x00; r[p + 16] = 0x84; r[p + 17] = 0x00; p += 18;    // aktiv, Gruppe
    memset(r + p, 0, 46);
    WiFi.macAddress(r + p);
    p += 46;
  } else {
    return;
  }
  nbns.beginPacket(nbns.remoteIP(), nbns.remotePort());
  nbns.write(r, p);
  nbns.endPacket();
}

static void pollNet() {
  // Update hängt (Verbindung weg, z. B. WLAN-Aussetzer): angefangenen Rest verwerfen, alte Firmware bleibt
  if (Update.isRunning() && millis() - fwLastData > 15000) {
    Update.abort();
    fwShown = -1;
    drawPage();
  }
  // WLAN hängt bei "verbinde ...": nach 30 s den Verbindungsaufbau neu starten
  static uint32_t notConnSince = 0;
  if (wifiActive && WiFi.status() != WL_CONNECTED) {
    if (!notConnSince) {
      notConnSince = millis();
    } else if (millis() - notConnSince > 30000) {
      notConnSince = millis();
      wifiStart();
    }
    return;
  }
  notConnSince = 0;
  if (!wifiActive) return;
  static uint32_t rssiAt = 0;
  if (millis() - rssiAt > 2000) {  // Signal merken: nach einer Trennung zeigt es, wie schwach es davor war
    rssiAt = millis();
    wifiRssi = WiFi.RSSI();
  }
  if (!mdnsOn) {  // erreichbar als <name>.local
    String host = hostName.length() ? hostName : "PC-Deck-" + deckId.substring(deckId.length() - 4);
    mdnsOn = MDNS.begin(host.c_str());
  }
  if (!netStarted) {
    netServer.begin();
    netServer.setNoDelay(true);
    disc.begin(DISC_PORT);
    nbns.begin(137);
    netStarted = true;
  }
  bool busy = netClient.connected() && netAuth && millis() - netLast < PC_TIMEOUT_MS;
  if (disc.parsePacket() > 0) {
    char b[16] = {0};
    disc.read(b, sizeof(b) - 1);
    if (strncmp(b, "PCDECK?", 7) == 0) {
      disc.beginPacket(disc.remoteIP(), disc.remotePort());
      disc.printf("PCDECK %s %d %s %d\n", deckId.c_str(), NET_PORT, FW_VERSION, busy ? 1 : 0);
      disc.endPacket();
    }
  }
  nbnsPoll();
  if (netServer.hasClient()) {
    WiFiClient c = netServer.accept();
    if (busy) {  // es ist schon eine PC-App verbunden
      c.println("ERR busy");
      c.stop();
    } else {
      if (netClient) netClient.stop();
      netClient = c;
      netClient.setNoDelay(true);
      netAuth = false;
      netNonce = "";
      netLen = 0;
      netLast = millis();
    }
  }
  if (!netClient.connected()) return;
  if (!netAuth && millis() - netLast > 5000) {  // ohne Anmeldung nicht lange offen halten
    netClient.stop();
    return;
  }
  while (netClient.available()) {
    int c = netClient.read();
    if (c < 0) break;
    if (c == '\n') {
      netBuf[netLen] = 0;
      netLen = 0;
      netLast = millis();
      if (!netAuth) {  // Anmeldung: AUTH1 <nonce_pc>, dann AUTH2 <beweis>
        if (netKey.length() && !netNonce.length() && strncmp(netBuf, "AUTH1 ", 6) == 0 && strlen(netBuf) <= 70) {
          char n[33];
          for (int i = 0; i < 4; i++) snprintf(n + i * 8, 9, "%08lx", (unsigned long)esp_random());
          netNonce = n;
          netClient.printf("CHAL %s %s\n", netNonce.c_str(), hmacHex(netKey, "D" + String(netBuf + 6)).c_str());
          continue;
        }
        netAuth = netNonce.length() && strncmp(netBuf, "AUTH2 ", 6) == 0 &&
                  sameText(String(netBuf + 6), hmacHex(netKey, "A" + netNonce));
        netNonce = "";
        netClient.println(netAuth ? "ACK" : "ERR auth");
        if (!netAuth) {
          netClient.stop();
          return;
        }
        continue;
      }
      io = &netClient;
      in = &netClient;
      fromNet = true;
      bool wasNet = viaNet;
      viaNet = true;
      handleLine(netBuf);
      if (!wasNet && pcOk) drawNetBadge();
      if (!netClient.connected()) return;
    } else if (c != '\r' && netLen < sizeof(netBuf) - 1) {
      netBuf[netLen++] = (char)c;
    }
  }
}

// ---------------------------------------------------------------- WLAN je nach USB
// PC am USB? S3 mit nativem USB: der Host sendet USB-Rahmen (auch ohne laufende App).
// Klassischer ESP32 (USB-Seriell-Wandler): erkennbar nur an Daten der PC-App – sie schickt per USB jede
// Sekunde ein Lebenszeichen. Nach dem Start bekommt die App BOOT_USB_WAIT_MS Zeit, sich zu melden.
static uint32_t loopStartMs = 0;

static bool usbHost() {
#if ARDUINO_USB_CDC_ON_BOOT && defined(CONFIG_IDF_TARGET_ESP32S3)
  // isPlugged() beruht auf einem SOF-Wächter im Systemtakt und springt laut Arduino-Kern auch bei intakter
  // Verbindung kurz auf false -> erst nach 1 s ununterbrochen "nicht da" als USB weg werten
  static uint32_t lastPlugged = 0;
  if (HWCDC::isPlugged()) lastPlugged = millis();
  return millis() - lastPlugged < 1000;
#else
  if (!lastSerialRx) return millis() - loopStartMs < BOOT_USB_WAIT_MS;
  return millis() - lastSerialRx < USB_DATA_MS;
#endif
}

// nach "Nein": ohne USB lässt sich die Abfrage per Tipper oder Drehknopf-Druck wieder öffnen (34)
static bool wifiCanAsk() {
  return !pcOk && !usbWas && wifiEn && wifiSsid.length() && !wifiActive && wifiAsked && !wifiAsk;
}
static bool reopenWifiAsk() {
  if (!wifiCanAsk()) return false;
  wifiAsk = true;
  drawPage();
  return true;
}

static const int WA_Y = 200, WA_H = 64, WA_W = 170;
static const int WA_NO_X = 50, WA_YES_X = SCREEN_W - 50 - WA_W;

static void drawWifiOverlay() {
  if (pcOk) return;
  if (wifiAsk) {
    tft.startWrite();
    tft.fillRoundRect(20, 40, SCREEN_W - 40, 250, 14, COL_TILE);
    tft.drawRoundRect(20, 40, SCREEN_W - 40, 250, 14, COL_ACCENT);
    tft.drawRoundRect(21, 41, SCREEN_W - 42, 248, 13, COL_ACCENT);
    tft.setTextColor(COL_TEXT, COL_TILE);
    tft.setTextDatum(middle_center);
    tft.setFont(&fonts::FreeSansBold12pt7b);
    tft.drawString("Kein USB verbunden", SCREEN_W / 2, 90);
    tft.setFont(&fonts::FreeSans12pt7b);
    tft.drawString("WLAN einschalten?", SCREEN_W / 2, 135);
    tft.fillRoundRect(WA_NO_X, WA_Y, WA_W, WA_H, 10, COL_BG);
    tft.drawRoundRect(WA_NO_X, WA_Y, WA_W, WA_H, 10, COL_DIM);
    tft.fillRoundRect(WA_YES_X, WA_Y, WA_W, WA_H, 10, COL_ACCENT);
    tft.setFont(&fonts::FreeSansBold12pt7b);
    tft.setTextColor(COL_TEXT, COL_BG);
    tft.drawString("Nein", WA_NO_X + WA_W / 2, WA_Y + WA_H / 2);
    tft.setTextColor(COL_TEXT, COL_ACCENT);
    tft.drawString("Ja", WA_YES_X + WA_W / 2, WA_Y + WA_H / 2);
    tft.endWrite();
  } else if (wifiActive || wifiCanAsk()) {  // kleiner Hinweis unten auf dem Logo
    String t = !wifiActive ? String("Antippen: WLAN einschalten")
               : WiFi.status() == WL_CONNECTED
                   ? "WLAN " + WiFi.localIP().toString() + " " + String(WiFi.RSSI()) + " dBm - warte auf PC"
               : wifiLostCount ? "WLAN weg (" + String(wifiReasonText(wifiLostReason)) + ", vorher " +
                                     String(wifiLostRssi) + " dBm) - verbinde ..."
                               : String("WLAN an - verbinde ...");
    tft.startWrite();
    tft.fillRect(0, SCREEN_H - 26, SCREEN_W, 26, COL_BG);
    tft.setTextDatum(middle_center);
    tft.setFont(&fonts::FreeSans9pt7b);
    tft.setTextColor(COL_DIM, COL_BG);
    tft.drawString(t, SCREEN_W / 2, SCREEN_H - 13);
    tft.endWrite();
  }
}

// Tipper auf der Abfrage (läuft auch ohne Verbindung zur App)
static bool pollWifiAsk(bool touched, bool down, bool wasDownBefore, int32_t tx, int32_t ty) {
  if (!wifiAsk) return false;
  if (touched && !wasDownBefore) {
    bool inY = ty >= WA_Y && ty < WA_Y + WA_H;
    bool yes = inY && tx >= WA_YES_X && tx < WA_YES_X + WA_W;
    bool no = inY && tx >= WA_NO_X && tx < WA_NO_X + WA_W;
    if (yes || no) {
      wifiAsk = false;
      if (yes) wifiRadio(true);
      drawPage();
    }
  }
  return true;
}

static void pollWifiPower() {
  static uint32_t lastHint = 0;
  uint32_t now = millis();
  bool usb = usbHost();
  if (usb != usbWas) {
    usbWas = usb;
    if (usb) usbSince = now;
    else noUsbSince = now;
  }
  if (usb) {
    bool hint = wifiCanAsk();  // Hinweis "Antippen: WLAN einschalten" steht noch
    wifiAsked = false;
    autoWifiOnce = false;
    if (wifiAsk || hint) {  // wieder am PC: Abfrage bzw. Hinweis weg
      wifiAsk = false;
      drawPage();
    }
    // im USB-Betrieb aus – nach dem Einstecken bzw. Einschalten noch WIFI_USB_OFF_MS
    if (wifiActive && now - usbSince > WIFI_USB_OFF_MS && now - wifiOnAt > WIFI_USB_OFF_MS &&
        !(netClient.connected() && netAuth)) {
      wifiRadio(false);
      if (!pcOk) drawPage();
    }
  } else if (wifiEn && wifiSsid.length() && !wifiActive && !wifiAsked && autoWifiOnce) {
    wifiAsked = true;  // Neustart nach Update über WLAN: ohne Abfrage wieder verbinden
    autoWifiOnce = false;
    wifiRadio(true);
  } else if (wifiEn && wifiSsid.length() && !wifiActive && !wifiAsked && now - noUsbSince > NO_USB_ASK_MS &&
             !pcOk) {
    // pro Trennen einmal fragen – erst, wenn die App nicht mehr da ist (ab FW 26 erst nach 5 s ohne PING);
    // vorher nicht als "gefragt" merken, sonst käme die Abfrage nach dem Abziehen nie
    wifiAsked = true;
    wifiAsk = true;
    drawPage();
  }
  if (wifiActive && !pcOk && !wifiAsk && now - lastHint > 2000) {  // Hinweis unten aktuell halten (IP)
    lastHint = now;
    drawWifiOverlay();
  }
}

// ---------------------------------------------------------------- Touch-Auswertung
static bool worldIdle();   // Block-Welt mit Figuren sichtbar (siehe Monsterkampf)
static void startFight();
static bool wasDown = false;
static uint32_t lastSeenDown = 0;
static uint32_t downSince = 0;
static int downNav = 0;     // -1 zurück, +1 weiter

// ---------------------------------------------------------------- Sicherheitsabfrage
static const int CB_Y = 200, CB_H = 64, CB_W = 170;          // Knöpfe der Abfrage
static const int CB_CANCEL_X = 50, CB_DEL_X = SCREEN_W - 50 - CB_W;

static void drawConfirm() {
  Tile* t = findTile(curPage, confirmTile);
  String name = t ? t->label : String("");
  tft.startWrite();
  tft.fillRoundRect(20, 40, SCREEN_W - 40, 250, 14, COL_TILE);
  tft.drawRoundRect(20, 40, SCREEN_W - 40, 250, 14, COL_LONG);
  tft.drawRoundRect(21, 41, SCREEN_W - 42, 248, 13, COL_LONG);
  tft.setTextColor(COL_TEXT, COL_TILE);
  tft.setTextDatum(middle_center);
  tft.setFont(&fonts::FreeSansBold12pt7b);
  tft.drawString("Kachel loeschen?", SCREEN_W / 2, 90);
  if (name.length()) {
    tft.setFont(&fonts::FreeSans12pt7b);
    tft.drawString(fitText(tft, "\"" + name + "\"", SCREEN_W - 80), SCREEN_W / 2, 135);
  }
  tft.fillRoundRect(CB_CANCEL_X, CB_Y, CB_W, CB_H, 10, COL_BG);
  tft.drawRoundRect(CB_CANCEL_X, CB_Y, CB_W, CB_H, 10, COL_DIM);
  tft.fillRoundRect(CB_DEL_X, CB_Y, CB_W, CB_H, 10, COL_LONG);
  tft.setFont(&fonts::FreeSansBold12pt7b);
  tft.setTextColor(COL_TEXT, COL_BG);
  tft.drawString("Abbrechen", CB_CANCEL_X + CB_W / 2, CB_Y + CB_H / 2);
  tft.setTextColor(COL_TEXT, COL_LONG);
  tft.drawString("Loeschen", CB_DEL_X + CB_W / 2, CB_Y + CB_H / 2);
  tft.endWrite();
}

static void openConfirm(int tile) {
  confirmActive = true;
  confirmWaitRelease = true;
  confirmTile = tile;
  confirmSince = millis();
  drawConfirm();
}

static void closeConfirm(bool doDelete) {
  if (doDelete) io->printf("DEL %d %d\n", curPage, confirmTile);  // PC-App löscht und überträgt neu
  confirmActive = false;
  confirmTile = -1;
  drawPage();
}

// Touch während der Abfrage: nur die beiden Knöpfe zählen
static void pollConfirm(bool touched, bool down, int32_t tx, int32_t ty) {
  if (millis() - confirmSince > CONFIRM_TIMEOUT_MS) {
    closeConfirm(false);
    return;
  }
  if (confirmWaitRelease) {
    if (!down) confirmWaitRelease = false;  // Finger vom langen Druck ist weg
    return;
  }
  if (touched && !wasDown) {
    bool inY = ty >= CB_Y && ty < CB_Y + CB_H;
    if (inY && tx >= CB_DEL_X && tx < CB_DEL_X + CB_W) closeConfirm(true);
    else if (inY && tx >= CB_CANCEL_X && tx < CB_CANCEL_X + CB_W) closeConfirm(false);
  }
}

static void pollTouch() {
  int32_t tx, ty;
  bool touched = tft.getTouch(&tx, &ty) > 0;
  uint32_t now = millis();
  if (touched) lastSeenDown = now;
  bool down = touched || (now - lastSeenDown < 60);  // kurze Aussetzer des resistiven Touch überbrücken

  static bool wakeHold = false;  // Tipper hat nur aufgeweckt: bis zum Loslassen nichts auslösen
  if (down && !wasDown && touched && wakeUp()) wakeHold = true;
  if (wakeHold) {
    if (!down) wakeHold = false;
    wasDown = down;
    return;
  }
  if (down) lastUse = now;

  if (pollWifiAsk(touched, down, wasDown, tx, ty)) {  // Abfrage "WLAN einschalten?" liegt obenauf
    wasDown = down;
    return;
  }
  if (!pcOk) {  // Logo: Tipper werden ignoriert – außer unten (WLAN-Abfrage) und 5x schnell oben (Monsterkampf)
    if (down && !wasDown && touched) {
      static uint32_t tapAt[5] = {0};
      static int tapN = 0;
      if (ty >= SCREEN_H - WIFI_TAP_H) {
        reopenWifiAsk();
      } else {
        tapAt[tapN] = now;
        tapN = (tapN + 1) % 5;
        if (tapAt[tapN] && now - tapAt[tapN] < 2500 && worldIdle() && !fightOn) {
          memset(tapAt, 0, sizeof(tapAt));
          startFight();
        }
      }
    }
    wasDown = down;
    return;
  }
  if (confirmActive) {
    pollConfirm(touched, down, tx, ty);
    if (!down) downTile = -1;
    wasDown = down;
    return;
  }

  if (down && !wasDown && touched && gPages > 0) {
    finishBuild();  // Tipper während des Aufbaus: sofort die ganze Seite
    downTile = -1;
    downNav = 0;
    longSent = false;
    deleteArmed = false;
    downSince = now;
    if (ty >= gridH()) {
      if (tx < 120) downNav = -1;
      else if (tx > SCREEN_W - 120) downNav = 1;
    } else {
      int c = tx / (SCREEN_W / gCols), r = ty / (gridH() / gRows);
      int i = r * gCols + c;
      if (c < gCols && r < gRows && (findTile(curPage, i) || live[i].used)) {
        downTile = i;
        tft.startWrite();
        drawTile(i, true);
        tft.endWrite();
      }
    }
  }

  // 0,6 s: Rahmen gelb – loslassen = langer Tipper (z. B. Programm schließen)
  if (down && downTile >= 0 && !longSent && now - downSince >= LONG_MS) {
    longSent = true;
    tft.startWrite();
    drawTile(downTile, true, true);
    tft.endWrite();
  }
  // 1,5 s: Rahmen rot und sofort die Sicherheitsabfrage (nur für gespeicherte Kacheln)
  if (down && downTile >= 0 && !deleteArmed && now - downSince >= DELETE_MS && findTile(curPage, downTile)) {
    deleteArmed = true;
    tft.startWrite();
    drawTile(downTile, true, true);
    tft.endWrite();
    delay(150);  // roten Rahmen kurz zeigen
    openConfirm(downTile);
    downTile = -1;
    wasDown = down;
    return;
  }

  if (!down && wasDown) {
    if (downTile >= 0) {
      tft.startWrite();
      drawTile(downTile, false);
      tft.endWrite();
      io->printf(longSent ? "LONG %d %d\n" : "PRESS %d %d\n", curPage, downTile);
    } else if (downNav != 0 && gPages > 1) {
      curPage = (curPage + downNav + gPages) % gPages;
      clearLive();
      drawPage();
      io->printf("SHOW %d\n", curPage);  // PC-App schickt die Live-Kacheln dieser Seite
    }
    downTile = -1;
    downNav = 0;
    longSent = false;
    deleteArmed = false;
  }
  wasDown = down;
}

// ---------------------------------------------------------------- Drehgeber
// Drehen per Interrupt (geht auch beim Zeichnen nicht verloren), gemeldet wird in loop().
// Ohne Verbindung zur PC-App wird nichts gemeldet (wie die Tipper auf dem Logo).
#if PIN_ENC_SW >= 0
static bool swRaw = false, swDown = false;
static uint32_t swChange = 0, swSince = 0;
static bool swTurned = false;  // gedrückt gedreht: Loslassen meldet nichts
static bool knobFrame = false; // roter Rahmen zeigt "lang erreicht"

static void pollKnobButton() {
  uint32_t now = millis();
  bool raw = digitalRead(PIN_ENC_SW) == LOW;
  if (raw != swRaw) {  // entprellen: Zustand muss 20 ms stehen
    swRaw = raw;
    swChange = now;
  }
  if (now - swChange < 20) return;
  if (raw && !swDown) {
    wakeUp();
    swDown = true;
    swTurned = false;
    swSince = now;
  } else if (!raw && swDown) {
    swDown = false;
    if (pcOk && !swTurned) io->println(now - swSince >= KNOB_LONG_MS ? "KNOBLONG" : "KNOB");
    else if (!pcOk && !swTurned) reopenWifiAsk();
  }
  bool want = swDown && !swTurned && pcOk && !confirmActive && now - swSince >= KNOB_LONG_MS;
  if (want != knobFrame) {
    knobFrame = want;
    drawKnobFrame(want);
  }
}
#else
static const bool swDown = false;
static bool swTurned = false;
static void pollKnobButton() {}
#endif

#if PIN_ENC_A >= 0 && PIN_ENC_B >= 0
static volatile int32_t encCount = 0;
static volatile uint8_t encState = 0;
static uint32_t encLastSend = 0;

static void IRAM_ATTR encIsr() {
  // Gray-Code: alter und neuer Zustand von A/B -> -1, 0 oder +1 (ungültige Sprünge zählen nicht)
  static const int8_t step[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
  uint8_t ab = (gpio_get_level((gpio_num_t)PIN_ENC_A) << 1) | gpio_get_level((gpio_num_t)PIN_ENC_B);
  encState = ((encState << 2) | ab) & 0x0F;
  encCount += step[encState];
}

static void setupEncoder() {
  pinMode(PIN_ENC_A, INPUT_PULLUP);
  pinMode(PIN_ENC_B, INPUT_PULLUP);
  encState = (digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_A), encIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_B), encIsr, CHANGE);
}

static void pollEncoder() {
  uint32_t now = millis();
  if (now - encLastSend < ENC_SEND_MS) return;
  noInterrupts();
  int32_t detents = encCount / ENC_STEPS;  // angefangene Raste bleibt stehen
  encCount -= detents * ENC_STEPS;
  interrupts();
  if (detents == 0) return;
  encLastSend = now;
  wakeUp();
  if (ENC_REVERSE) detents = -detents;
  if (swDown) swTurned = true;  // gedrückt gedreht: zweite Belegung, Loslassen zählt nicht als Druck
  if (pcOk) io->printf(swDown ? "ROTP %+d\n" : "ROT %+d\n", (int)detents);
}
#else
static void setupEncoder() {}
static void pollEncoder() {}
#endif


// ---------------------------------------------------------------- Setup/Loop
void setup() {
  if (netRestart == NET_RESTART_MAGIC) autoWifiOnce = true;
  netRestart = 0;
  // Puffer muss ein ganzes Icon (8 KB) + Befehle fassen. Gilt auch für native USB:
  // HWCDC hat ab Werk nur 256 Byte und verwirft bei vollem Puffer Daten.
  Serial.setRxBufferSize(16384);
  Serial.begin(SERIAL_BAUD);
  Serial.setTimeout(3000);

  uint64_t mac = ESP.getEfuseMac();
  char id[13];
  snprintf(id, sizeof(id), "%04X%08X", (uint16_t)(mac >> 32), (uint32_t)mac);
  deckId = id;

  tft.init();
  tft.setRotation(TFT_ROTATION);  // 1 = quer, 3 = quer um 180° gedreht (Einbaulage im Gehäuse)
  tft.setBrightness(briNormal);
  COL_BG = tft.color565(18, 18, 22);
  COL_TILE = tft.color565(44, 48, 58);
  COL_TEXT = tft.color565(235, 235, 240);
  COL_DIM = tft.color565(130, 135, 150);
  COL_ACCENT = tft.color565(80, 150, 255);
  COL_LONG = tft.color565(235, 80, 80);
  COL_WARN = tft.color565(240, 190, 60);
  COL_LOGO = tft.color565(0, 230, 255);

  // Zwischenspeicher für die ganze Seite – nur mit PSRAM (sonst zu groß fürs RAM)
  if (psramFound()) {
    frame.setPsram(true);
    frame.setColorDepth(16);
    haveFrame = frame.createSprite(SCREEN_W, SCREEN_H) != nullptr;
  }

  if (!LittleFS.begin(true)) {
    drawWaiting("Fehler: Dateisystem konnte nicht gestartet werden");
    delay(3000);
  }

  loadTheme();

  // Kalibrierung: beim ersten Start oder wenn beim Einschalten der Bildschirm berührt wird
  bool haveCal = loadCalibration();
  drawWaiting(haveCal ? "Zum Kalibrieren jetzt Bildschirm beruehren" : "Erster Start");
  bool wantCal = !haveCal;
  if (haveCal) {
    uint32_t t0 = millis();
    while (millis() - t0 < BOOT_LOGO_MS) {
      int32_t x, y;
      if (tft.getTouch(&x, &y) > 0) {
        wantCal = true;
        break;
      }
      delay(20);
    }
  }
  if (wantCal) {
    calibrateTouch();
    // warten, bis der Finger weg ist
    int32_t x, y;
    while (tft.getTouch(&x, &y) > 0) delay(20);
  }

  loadOpt();
  loadLayout();
  drawPage();
#if PIN_ENC_SW >= 0
  pinMode(PIN_ENC_SW, INPUT_PULLUP);
#endif
  setupEncoder();
  loadWifi();
}

// Statuspunkt aktualisieren: PC-App gilt als getrennt, wenn sie sich PC_TIMEOUT_MS nicht meldet
static void pollPcStatus() {
  if (!pcOk) return;
  // allein die App entscheidet: sie meldet sich per USB jede Sekunde (PING), im WLAN alle 2 s
  if (millis() - lastPcContact < (viaNet ? NET_TIMEOUT_MS : PC_TIMEOUT_MS)) return;
  pcOk = false;  // App meldet sich nicht mehr (Herunterfahren, Abmelden, Standby, beendet): Logo
  confirmActive = false;
  downTile = -1;
  drawPage();
}

// Bedienung: Dimm-Uhr neu starten; true = Display war gedimmt (der Tipper weckt nur auf)
static bool wakeUp() {
  lastUse = millis();
  if (!dimmed) return false;
  dimmed = false;
  tft.setBrightness(briNormal);
  return true;
}

static void pollDim() {
  if (!dimAfterMs || dimmed || millis() - lastUse < dimAfterMs) return;
  dimmed = true;
  tft.setBrightness(min(briDim, briNormal));
}

// Uhr im Ruhebild zur vollen Minute erneuern
static void pollClock() {
  if (pcOk || !clockValid() || confirmActive || fightOn) return;  // im Kampf zeichnet pollFight alles
  struct tm t;
  localTime(t);
  if (t.tm_hour * 60 + t.tm_min == clockShownMin) return;
  if (optWorld && clockShownMin >= 0 && !wifiAsk && !wDamaged) {  // Block-Welt: nur Himmel, Zeit und Baum neu (Boden bleibt)
    drawWorld(0, W_GROUND);
    clockShownMin = t.tm_hour * 60 + t.tm_min;
  } else {
    drawClock();
  }
  drawWifiOverlay();
}

// Figuren: zufällig alle 2–5 Minuten eine; tagsüber der Wanderer, nachts Zombie, Skelett oder Spinne
static bool worldIdle() { return !pcOk && clockValid() && optWorld && optWalkers && !confirmActive && !wifiAsk; }

static void startFight() {
  wResetDamage();
  memset(mShots, 0, sizeof(mShots));
  memset(mHits, 0, sizeof(mHits));
  memset(&mDust, 0, sizeof(mDust));
  fightHitAt[0] = fightHitAt[1] = 0xFFFF;
  fightShakeUntil = 0;
  fightDone = 0;
  fightT = 0;
  fightWinner = esp_random() & 1;
  walkKind = -1;
  fightAt = millis();
  fightOn = true;
}

static void pollWalker() {
  if (!worldIdle()) {
    walkKind = -1;
    return;
  }
  if (fightOn) return;
  uint32_t now = millis();
  if (walkKind < 0) {
    if (!walkNextStart) walkNextStart = now + 20000;  // erste Figur nach 20 s
    if ((int32_t)(now - walkNextStart) < 0) return;
    if (esp_random() % 100 < 3) {  // ganz selten statt einer Figur: der Monsterkampf
      walkNextStart = now + 120000 + esp_random() % 180000;
      startFight();
      return;
    }
    struct tm t;
    localTime(t);
    int day = t.tm_hour * 60 + t.tm_min;
    bool night = day < 6 * 60 || day >= 20 * 60;
    walkKind = night ? 1 + (int)(esp_random() % 3) : 0;
    walkDir = (esp_random() & 1) ? 1 : -1;
    walkX = walkDir > 0 ? -W_FIGS[walkKind].w * W_FIGS[walkKind].px : SCREEN_W;
    walkFrame = 0;
    walkLastStep = 0;
  }
  if (now - walkLastStep < 150) return;
  walkLastStep = now;
  walkX += walkDir * (walkKind == 3 ? 6 : 5);
  walkFrame++;
  const WFig& f = W_FIGS[walkKind];
  bool gone = walkDir > 0 ? walkX > SCREEN_W : walkX < -f.w * f.px;
  if (gone) {
    walkKind = -1;
    walkNextStart = now + 120000 + esp_random() % 180000;
  }
  drawWorld(W_GROUND - W_FIG_MAXH, W_GROUND);  // nur das Band, in dem die Figuren laufen
}

static const int FZ_X = 130, FA_X = 240;  // Kampfstellung (linke Kante) von Echse und Affe
static const uint16_t FIGHT_MS = 21000;

static char timeChar(int idx) {
  struct tm t;
  localTime(t);
  char hm[6];
  snprintf(hm, sizeof(hm), "%02d:%02d", t.tm_hour, t.tm_min);
  return hm[idx];
}

// Mitte des Zeichens idx der Uhrzeit (wie drawWorld sie setzt)
static void timeCharPos(int idx, int& cx, int& cy) {
  struct tm t;
  localTime(t);
  char hm[6];
  snprintf(hm, sizeof(hm), "%02d:%02d", t.tm_hour, t.tm_min);
  int x = (SCREEN_W - wTextWidth(hm, W_TIME_S)) / 2;
  for (int i = 0; i < idx; i++) x += ((int)strlen(wGlyph(hm[i])[0]) + 1) * W_TIME_S;
  cx = x + (int)strlen(wGlyph(hm[idx])[0]) * W_TIME_S / 2;
  cy = W_TIME_Y + 7 * W_TIME_S / 2;
}

static void addShot(int kind, char ch, int x0, int y0, int x1, int y1, uint16_t t0, uint16_t dur, int arc) {
  for (MShot& p : mShots)
    if (!p.kind || fightT >= p.t0 + p.dur) {
      p = {(int8_t)kind, ch, (int16_t)x0, (int16_t)y0, (int16_t)x1, (int16_t)y1, t0, dur, (int16_t)arc};
      return;
    }
}

static void addHit(int who, int dy = 0) {  // who: 0 Echse, 1 Affe wird getroffen
  static int next = 0;
  const MMon& m = mMon[who];
  mHits[next] = {fightT, (int16_t)(m.x + M_FIGS[m.fig].w * M_PX / 2), (int16_t)(mTop(m) + 50 + dy)};
  next = (next + 1) % 4;
  fightHitAt[who] = fightT;
}

static void addCrater(int x) {
  int col = constrain(x / WB, 0, SCREEN_W / WB - 1);
  if (col >= W_TREE - 1 && !wTreeGone) return;  // unter dem Baum bleibt es heil, solange er steht
  wCrater[col] = 1;
  wDamaged = true;
  fightShakeUntil = fightT + 300;
}

// Posen und Plätze nach der Zeit im Kampf
static void fightPose(uint16_t t) {
  MMon& z = mMon[0];
  MMon& a = mMon[1];
  bool step = (t / 300) & 1;
  z = {(int8_t)(step ? PZ_WALK1 : PZ_WALK0), FZ_X, 1, 0, 0, false};
  a = {(int8_t)(step ? PA_WALK1 : PA_WALK0), FA_X, -1, 0, 0, false};
  if (t < 3000) {  // beide stampfen herein
    z.x = -90 + (FZ_X + 90) * t / 3000;
    a.x = SCREEN_W - (SCREEN_W - FA_X) * t / 3000;
    return;
  }
  if (t < 16500) {
    z.fig = PZ_WALK0;
    a.fig = PA_WALK0;
    if (t >= 3000 && t < 3600) a.fig = PA_PUNCH;
    if (t >= 4000 && t < 4600) z.fig = PZ_STOMP;
    if (t >= 6000 && t < 6400) {  // Affe geht zum Baum ...
      a.dir = 1;
      a.x = FA_X + (t - 6000) * 80 / 400;
      a.fig = step ? PA_WALK1 : PA_WALK0;
    } else if (t >= 6400 && t < 6800) {  // ... packt ihn ...
      a.dir = 1;
      a.x = FA_X + 80;
      a.fig = PA_PUNCH;
    } else if (t >= 6800 && t < 7700) {  // ... und trägt ihn zurück
      a.x = FA_X + 80 - (t - 6800) * 80 / 900;
      a.fig = PA_UP;
      a.hold = 3;
    } else if (t >= 7700 && t < 8000) {
      a.fig = PA_UP;
    }
    if (t >= 9000 && t < 10000) {  // Echse holt sich eine Ziffer
      z.fig = PZ_UP;
      if (t >= 9300 && t < 9800) {
        z.hold = 4;
        z.holdCh = timeChar(fightDigit[0]);
      }
    }
    if (t >= 11000 && t < 12000) {  // Affe ebenso
      a.fig = PA_UP;
      if (t >= 11300 && t < 11800) {
        a.hold = 4;
        a.holdCh = timeChar(fightDigit[1]);
      }
    }
    if (t >= 13000 && (t - 13000) % 700 < 400) {  // Schlagabtausch
      if (((t - 13000) / 700) % 2 == 0) z.fig = PZ_CLAW;
      else a.fig = PA_PUNCH;
    }
    for (int i = 0; i < 2; i++)  // getroffen: kurz zurückgeworfen
      if (fightHitAt[i] != 0xFFFF && t >= fightHitAt[i] && t - fightHitAt[i] < 300) mMon[i].x -= mMon[i].dir * 10;
    return;
  }
  MMon& los = mMon[1 - fightWinner];
  if (t >= 17000) {  // Verlierer liegt
    los.fig = fightWinner ? PZ_DOWN : PA_DOWN;
    los.x += fightWinner ? -8 : -3;
    los.hidden = t >= 20500;
  }
  if (t < 19000) {  // Sieger jubelt
    if (fightWinner == 0) z.fig = t >= 17000 ? PZ_ROAR : PZ_CLAW;
    else a.fig = t >= 17000 ? ((t / 250) & 1 ? PA_BEAT1 : PA_BEAT0) : PA_PUNCH;
  } else {  // und geht ab, wo er hergekommen ist
    int d = (t - 19000) * 340 / 2000;
    if (fightWinner == 0) {
      z.dir = -1;
      z.x = FZ_X - d;
    } else {
      a.dir = 1;
      a.x = FA_X + d;
    }
  }
}

static void pollFight() {
  if (!fightOn) return;
  if (!worldIdle()) {  // App meldet sich, Abfrage o. Ä.: Kampf sofort vorbei (das neue Bild zeichnet der Auslöser)
    fightOn = false;
    wResetDamage();
    return;
  }
  static uint32_t lastDraw = 0;
  uint32_t now = millis();
  if (now - lastDraw < 100) return;
  lastDraw = now;
  uint32_t el = now - fightAt;
  if (el >= FIGHT_MS) {
    fightOn = false;  // Schaden bleibt bis zur nächsten vollen Minute
    drawWorld(0, W_GROUND + WB);
    return;
  }
  uint16_t t = fightT = el;
  fightPose(t);
  auto ev = [&](int id, uint16_t at) {
    if (t < at || (fightDone >> id) & 1) return false;
    fightDone |= 1u << id;
    return true;
  };
  MMon& z = mMon[0];
  MMon& a = mMon[1];
  int hx, hy, cx, cy;
  if (ev(0, 3300)) addHit(0);
  if (ev(1, 4600)) {  // Stampfer: Blöcke fliegen
    addCrater(FZ_X + 80);
    addShot(1, 0, FZ_X + 80, W_GROUND, a.x + 40, 190, t, 700, 70);
    addShot(2, 0, FZ_X + 60, W_GROUND + 20, -30, 120, t, 900, 80);
  }
  if (ev(2, 5300)) addHit(1);
  if (ev(3, 6800)) {
    wTreeGone = true;
    wDamaged = true;
  }
  if (ev(4, 7700)) {
    mHand(a, hx, hy);
    addShot(3, 0, hx, hy, z.x + 50, 180, t, 600, 40);
  }
  if (ev(5, 8300)) {
    addHit(0);
    addCrater(z.x + 30);
  }
  if (ev(6, 9000)) {  // Echse greift sich eine Ziffer
    fightDigit[0] = esp_random() & 1;
    timeCharPos(fightDigit[0], cx, cy);
    wTimeGone |= 1 << fightDigit[0];
    wDamaged = true;
    z.fig = PZ_UP;
    mHand(z, hx, hy);
    addShot(4, timeChar(fightDigit[0]), cx, cy, hx, hy, t, 300, 0);
  }
  if (ev(7, 9800)) {
    mHand(z, hx, hy);
    addShot(4, timeChar(fightDigit[0]), hx, hy, a.x + 40, 175, t, 600, 50);
  }
  if (ev(8, 10400)) addHit(1, -10);
  if (ev(9, 11000)) {  // Affe ebenso (hintere Ziffern)
    fightDigit[1] = 3 + (esp_random() & 1);
    timeCharPos(fightDigit[1], cx, cy);
    wTimeGone |= 1 << fightDigit[1];
    a.fig = PA_UP;
    mHand(a, hx, hy);
    addShot(4, timeChar(fightDigit[1]), cx, cy, hx, hy, t, 300, 0);
  }
  if (ev(10, 11800)) {
    mHand(a, hx, hy);
    addShot(4, timeChar(fightDigit[1]), hx, hy, z.x + 50, 175, t, 600, 50);
  }
  if (ev(11, 12400)) addHit(0, -10);
  for (int k = 0; k < 5; k++)  // Schlagabtausch: Treffer, Löcher, Brocken
    if (ev(12 + k, 13300 + 700 * k)) {
      addHit(k % 2 ? 0 : 1);
      int x = 120 + (int)(esp_random() % 200);
      addCrater(x);
      addShot(1 + (esp_random() & 1), 0, x, W_GROUND, x + (int)(esp_random() % 160) - 80, -30, t, 800, 60);
    }
  if (ev(17, 16600)) addHit(1 - fightWinner, -20);
  if (ev(18, 17000)) addCrater(mMon[1 - fightWinner].x + 40);  // Verlierer fällt
  if (ev(19, 20400)) {
    const MMon& l = mMon[1 - fightWinner];
    mDust = {t, (int16_t)(l.x + M_FIGS[l.fig].w * M_PX / 2), (int16_t)(W_GROUND - 30)};
  }
  bool stomp = t < 3000 ? (t % 300) < 100 : t < fightShakeUntil;  // Beben
  wShake = stomp ? ((t / 100) & 1 ? 4 : -4) : 0;
  drawWorld(0, W_GROUND + WB);  // Stein-Reihe unten und der WLAN-Hinweis bleiben stehen
}



// ---------------------------------------------------------------- Uhrzeit aus dem Internet (ab FW 35)
// Läuft die App, kommt die Zeit von ihr (TIME). Ohne PC holt das Deck sie per NTP: im WLAN-Betrieb nebenbei,
// sonst schaltet es den Funk kurz nur dafür ein (auch nach "Nein"), höchstens 20 s, alle 6 h bzw. nach
// Fehlschlag in 10 min erneut. Zeitzone fest Deutschland (Sommerzeit automatisch).
#define NTP_EVERY_MS (6UL * 3600 * 1000)
#define NTP_RETRY_MS (10UL * 60 * 1000)
#define NTP_RADIO_MS 20000
static volatile bool ntpGot = false;
static bool ntpRadio = false, sntpOn = false;
static uint32_t ntpOnAt = 0, ntpNextTry = 0, ntpLastOk = 0;

static void ntpSynced(struct timeval*) { ntpGot = true; }

static void pollNtp() {
  uint32_t now = millis();
  static bool conn = false;
  bool c = WiFi.status() == WL_CONNECTED;
  if (c && !conn) {  // gerade verbunden: Zeit abfragen
    if (!sntpOn) {
      sntp_set_time_sync_notification_cb(ntpSynced);
      configTzTime("CET-1CEST,M3.5.0,M10.5.0/3", "pool.ntp.org", "time.google.com");
      sntpOn = true;
    } else {
      sntp_restart();
    }
  }
  conn = c;
  if (ntpGot) {
    ntpGot = false;
    time_t u = time(nullptr);
    if (u > 1700000000 && !pcOk) {  // App hat Vorrang
      struct tm lt;
      localtime_r(&u, &lt);
      bool was = clockAtMs != 0;
      clockUnix = u;
      clockOffset = lt.tm_isdst > 0 ? 7200 : 3600;
      clockAtMs = now ? now : 1;
      ntpLastOk = clockAtMs;
      if (!was) drawPage();  // bisher Logo: jetzt Uhr bzw. Block-Welt
    }
  }
  bool haveTime = clockAtMs && now - clockAtMs < NTP_EVERY_MS;
  if (ntpRadio) {
    bool done = ntpLastOk && now - ntpLastOk < 5000 && ntpLastOk >= ntpOnAt;
    if (wifiActive) {  // "Ja" gedrückt: der normale WLAN-Betrieb übernimmt
      ntpRadio = false;
    } else if (done || usbWas || pcOk || now - ntpOnAt > NTP_RADIO_MS) {
      ntpRadio = false;
      WiFi.disconnect(true);
      WiFi.mode(WIFI_OFF);
      ntpNextTry = now + (done ? NTP_EVERY_MS : NTP_RETRY_MS);
    }
  } else if (!usbWas && !pcOk && !wifiActive && wifiEn && wifiSsid.length() && !haveTime &&
             now - noUsbSince > 3000 && (!ntpNextTry || (int32_t)(now - ntpNextTry) >= 0)) {
    ntpRadio = true;  // Funk nur für die Uhrzeit
    ntpOnAt = now;
    String host = hostName.length() ? hostName : "PC-Deck-" + deckId.substring(deckId.length() - 4);
    WiFi.setHostname(host.c_str());
    WiFi.mode(WIFI_STA);
    WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  }
}

// Wert-Anzeige abgelaufen: Seite wieder vollständig zeigen
static void pollOsd() {
  if (!osdUntil || (int32_t)(millis() - osdUntil) < 0) return;
  osdUntil = 0;
  drawPage();
}

void loop() {
  if (!loopStartMs) loopStartMs = millis() ? millis() : 1;
  pollSerial();
  pollNet();
  pollWifiPower();
  pollTouch();
  pollEncoder();
  pollKnobButton();
  pollOsd();
  pollPcStatus();
  pollBuild();
  pollDim();
  pollClock();
  pollWalker();
  pollFight();
  pollNtp();
  delay(5);
}
