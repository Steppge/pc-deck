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
#include <mbedtls/md.h>
#include <vector>
#include "lgfx_config.h"

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
#define FW_VERSION "22"  // 2 = Live-Kacheln (UPD, SHOW), 3 = langer Tipper (LONG), 4 = PING/Status,
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
                        // 22 = Icons komprimiert (ICONZ, UPD mit Typ 2) – weniger Speicher und Übertragung
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
#define USB_DATA_MS 3000       // klassischer ESP32: so lange ohne Daten der App (PING jede Sekunde) = kein USB
#define BOOT_USB_WAIT_MS 4000  // nach dem Start so lange auf die App warten, bevor "kein USB" gilt
#define PC_TIMEOUT_MS 5000    // so lange ohne Nachricht der PC-App -> nur noch das Logo
#define NET_TIMEOUT_MS 12000  // dasselbe im WLAN (Funkaussetzer überbrücken)
#define BOOT_LOGO_MS 1500   // so lange bleibt das Startbild stehen (Berühren = Touch kalibrieren)
#define LONG_MS 600     // ab so langem Drücken gilt ein Tipper als "lang" (Rahmen gelb, beim Loslassen LONG)
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

static void drawPage() {
  if (confirmActive) return;
  if (!pcOk) {  // PC-App meldet sich nicht (PC startet, Anmeldebildschirm, App beendet): nur das Logo
    drawWaiting(nullptr);
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
    frame.pushSprite(&tft, 0, 0);
    drawOsd();
    drawNetBadge();
    return;
  }
  // ohne PSRAM: Kachel für Kachel, aber jede fertig gezeichnet und ohne vorher zu löschen
  tft.startWrite();
  int ch = gridH() / gRows;
  if (gRows * ch < gridH()) tft.fillRect(0, gRows * ch, SCREEN_W, gridH() - gRows * ch, COL_BG);
  for (int i = 0; i < gCols * gRows; i++) drawTile(i);
  drawNavTo(tft, nullptr);
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
static bool netStarted = false, netAuth = false;
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

static void wifiStart() {
  if (netClient) netClient.stop();
  netAuth = false;
  if (!wifiEn || !wifiSsid.length() || !wifiActive) {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(("pcdeck-" + deckId).c_str());
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
  prefs.end();
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
    curPage = 0;
    drawPage();
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
      drawPage();
    }
    io->println("ACK");
  } else if (strncmp(line, "BRI ", 4) == 0) {
    tft.setBrightness(constrain(atoi(line + 4), 5, 255));
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
static void pollNet() {
  if (!wifiActive || WiFi.status() != WL_CONNECTED) return;
  if (!netStarted) {
    netServer.begin();
    netServer.setNoDelay(true);
    disc.begin(DISC_PORT);
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
  return HWCDC::isPlugged();
#else
  if (!lastSerialRx) return millis() - loopStartMs < BOOT_USB_WAIT_MS;
  return millis() - lastSerialRx < USB_DATA_MS;
#endif
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
  } else if (wifiActive) {  // kleiner Hinweis unten auf dem Logo
    String t = WiFi.status() == WL_CONNECTED ? "WLAN " + WiFi.localIP().toString() + " - warte auf PC"
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
    wifiAsked = false;
    autoWifiOnce = false;
    if (wifiAsk) {  // wieder am PC: Abfrage weg
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
  } else if (wifiEn && wifiSsid.length() && !wifiActive && !wifiAsked && now - noUsbSince > NO_USB_ASK_MS) {
    wifiAsked = true;  // pro Trennen einmal fragen
    if (!pcOk) {
      wifiAsk = true;
      drawPage();
    }
  }
  if (wifiActive && !pcOk && !wifiAsk && now - lastHint > 2000) {  // Hinweis unten aktuell halten (IP)
    lastHint = now;
    drawWifiOverlay();
  }
}

// ---------------------------------------------------------------- Touch-Auswertung
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

  if (pollWifiAsk(touched, down, wasDown, tx, ty)) {  // Abfrage "WLAN einschalten?" liegt obenauf
    wasDown = down;
    return;
  }
  if (!pcOk) {  // Logo: Tipper werden ignoriert
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

static void pollKnobButton() {
  uint32_t now = millis();
  bool raw = digitalRead(PIN_ENC_SW) == LOW;
  if (raw != swRaw) {  // entprellen: Zustand muss 20 ms stehen
    swRaw = raw;
    swChange = now;
  }
  if (now - swChange < 20) return;
  if (raw && !swDown) {
    swDown = true;
    swTurned = false;
    swSince = now;
  } else if (!raw && swDown) {
    swDown = false;
    if (pcOk && !swTurned) io->println(now - swSince >= LONG_MS ? "KNOBLONG" : "KNOB");
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
  tft.setBrightness(200);
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
  bool usbGone = !viaNet && !usbHost();  // per USB verbunden und USB weg: App ist sicher weg
  if (!usbGone && millis() - lastPcContact < (viaNet ? NET_TIMEOUT_MS : PC_TIMEOUT_MS)) return;
  pcOk = false;  // App meldet sich nicht mehr (Herunterfahren, Abmelden, Standby, beendet): Logo
  confirmActive = false;
  downTile = -1;
  drawPage();
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
  delay(5);
}
