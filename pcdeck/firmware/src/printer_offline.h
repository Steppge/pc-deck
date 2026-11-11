// ---------------------------------------------------------------- Drucker ohne PC-App (ab FW 38, nur Deck 2)
// Die App schickt die Seite(n) "Drucker" als Liste (PRINTER BEGIN / L <hex> / END) und speichert sie in
// /printer.txt. Ist die App weg und das WLAN verbunden, öffnet ein langer Tipper aufs Ruhebild die Drucker-
// Seite: das Deck fragt Moonraker selbst ab (HTTP im eigenen Task) und schickt die G-Codes selbst.
// Bedienung wie mit der App: kurz = Drehknopf übernimmt (8 s), drehen = Ziel, Knopf drücken = senden;
// Befehle lang tippen (im Druck gesperrt), Abbrechen danach mit Knopf-Druck bestätigen.
// Zurück zur Uhr: lang auf die Leiste unten tippen oder 2 min nichts tun.
// Diese Datei wird in main.cpp eingebunden (nutzt deren Zeichen- und Zustandsfunktionen).

#include <WiFiClient.h>

#ifdef PRINTER_OFFLINE
static const bool PR_ENABLED = true;
#else
static const bool PR_ENABLED = false;
#endif

static const uint32_t PR_GRAB_MS = 8000;
static const uint32_t PR_IDLE_MS = 120000;
static const uint32_t PR_POLL_MS = 2000;
static const int PR_MAX_TILES = 48;
static const int PR_MAX_SENS = 8;

enum { PT_ADJ, PT_SHOW, PT_CMD };
enum {
  PK_BED, PK_NOZ, PK_ZOFF, PK_SPEED, PK_FLOW, PK_RETR, PK_FAN, PK_PROG, PK_SENS,
  PK_HOME, PK_HOMEZ, PK_ZTILT, PK_QGL, PK_MESH, PK_MOTORS, PK_PAUSE, PK_RESUME, PK_CANCEL, PK_MACRO, PK_N
};
enum { PV_BED, PV_BEDT, PV_NOZ, PV_NOZT, PV_ZOFF, PV_SPEED, PV_FLOW, PV_RETR, PV_FAN, PV_PROG, PV_DUR, PV_N };

struct PrKind {
  const char* id;
  uint8_t type;
  const char* name;
  float step, lo, hi;
  int8_t pv;            // Wert in PrState (einstellbar)
  const char* gcode;    // Befehl
};
static PrKind PR_KINDS[PK_N] = {
  {"bed", PT_ADJ, "Bett", 5, 0, 110, PV_BEDT, nullptr},
  {"nozzle", PT_ADJ, "Duese", 5, 0, 280, PV_NOZT, nullptr},
  {"zoff", PT_ADJ, "Z-Offset", 0.01f, -2, 2, PV_ZOFF, nullptr},
  {"speed", PT_ADJ, "Speed", 5, 10, 300, PV_SPEED, nullptr},
  {"flow", PT_ADJ, "Flow", 1, 50, 150, PV_FLOW, nullptr},
  {"retract", PT_ADJ, "Retract", 0.1f, 0, 10, PV_RETR, nullptr},
  {"fan", PT_ADJ, "Luefter", 5, 0, 100, PV_FAN, nullptr},
  {"progress", PT_SHOW, "Druck", 0, 0, 0, -1, nullptr},
  {"sensor", PT_SHOW, "Temp.", 0, 0, 0, -1, nullptr},
  {"home", PT_CMD, "Home XYZ", 0, 0, 0, -1, "G28"},
  {"homez", PT_CMD, "Home Z", 0, 0, 0, -1, "G28 Z"},
  {"ztilt", PT_CMD, "Z-Tilt", 0, 0, 0, -1, "Z_TILT_ADJUST"},
  {"qgl", PT_CMD, "QGL", 0, 0, 0, -1, "QUAD_GANTRY_LEVEL"},
  {"mesh", PT_CMD, "Bed-Mesh", 0, 0, 0, -1, "BED_MESH_CALIBRATE"},
  {"motors", PT_CMD, "Motoren aus", 0, 0, 0, -1, "M84"},
  {"pause", PT_CMD, "Pause", 0, 0, 0, -1, "PAUSE"},
  {"resume", PT_CMD, "Fortsetzen", 0, 0, 0, -1, "RESUME"},
  {"cancel", PT_CMD, "Abbrechen", 0, 0, 0, -1, "CANCEL_PRINT"},
  {"macro", PT_CMD, "Makro", 0, 0, 0, -1, nullptr},
};

struct PrTile {
  int16_t slot;
  uint8_t kind;
  int8_t sens;  // Sensor-Nummer (PK_SENS)
  String arg;   // Makro-Name bzw. Sensor-Objekt
};

struct PrState {
  bool ok = false;
  bool has[PV_N] = {false};
  float v[PV_N] = {0};
  float sens[PR_MAX_SENS] = {0};
  bool sensHas[PR_MAX_SENS] = {false};
  char state[12] = "";
  char homed[4] = "";
};

static bool prHave = false;
static String prHost = "printer.local", prIpStr;
static int prPort = 80, prFirst = -1, prCount = 0;
static std::vector<PrTile> prTiles;
static String prSens[PR_MAX_SENS];
static int prSensN = 0;
static String prQuery;
static String prRx;  // Empfang PRINTER BEGIN … END

// geteilt mit dem HTTP-Task (unter prMutex)
static SemaphoreHandle_t prMutex = nullptr;
static PrState prSt;
static uint32_t prSeq = 0;
static String prCmd, prCmdDone, prMsg;  // wartender Befehl, Meldung danach, Meldung an die Anzeige
static bool prCmdBusy = false;
static bool prForcePoll = false;
static IPAddress prIp;
static uint32_t prIpAt = 0;

// nur Haupt-Task
static uint32_t prSeenSeq = 0;
static int prGrab = -1;  // Kachel (Index auf der Seite), der der Drehknopf gehört
static uint32_t prGrabAt = 0;
static bool prHasTarget = false;
static float prTarget = 0;
static uint32_t prConfirmAt = 0;  // "Druck abbrechen?" wartet auf den Knopf
static int prConfirmTile = -1;
static String prSig[PR_MAX_TILES];
static uint16_t COL_GREEN = 0;

static PrTile* prTileAt(int page, int i) {
  if (!prHave || page < prFirst || page >= prFirst + prCount) return nullptr;
  int slot = (page - prFirst) * gCols * gRows + i;
  for (auto& t : prTiles)
    if (t.slot == slot) return &t;
  return nullptr;
}

static String prUrlEnc(const String& s) {
  String o;
  const char* hex = "0123456789ABCDEF";
  for (size_t k = 0; k < s.length(); k++) {
    char c = s[k];
    if (isalnum((unsigned char)c) || c == '_' || c == '-' || c == '.') o += c;
    else {
      o += '%';
      o += hex[(c >> 4) & 15];
      o += hex[c & 15];
    }
  }
  return o;
}

// ------------------------------------------------------------ Einstellungen (/printer.txt)
static void prParse(const String& txt) {
  prTiles.clear();
  prSensN = 0;
  prFirst = -1;
  prCount = 0;
  prHost = "printer.local";
  prIpStr = "";
  prPort = 80;
  int pos = 0;
  while (pos < (int)txt.length()) {
    int nl = txt.indexOf('\n', pos);
    if (nl < 0) nl = txt.length();
    String l = txt.substring(pos, nl);
    pos = nl + 1;
    l.trim();
    int sp = l.indexOf(' ');
    if (sp < 0) continue;
    String key = l.substring(0, sp), val = l.substring(sp + 1);
    if (key == "host") prHost = val;
    else if (key == "ip") prIpStr = val;
    else if (key == "port") prPort = val.toInt() > 0 ? val.toInt() : 80;
    else if (key == "pages") sscanf(val.c_str(), "%d %d", &prFirst, &prCount);
    else if (key == "maxbed") PR_KINDS[PK_BED].hi = val.toFloat();
    else if (key == "maxnozzle") PR_KINDS[PK_NOZ].hi = val.toFloat();
    else if (key == "t" && (int)prTiles.size() < PR_MAX_TILES) {
      int sp2 = val.indexOf(' ');
      if (sp2 < 0) continue;
      PrTile t;
      t.slot = val.substring(0, sp2).toInt();
      String f = val.substring(sp2 + 1);
      int colon = f.indexOf(':');
      String kind = colon >= 0 ? f.substring(0, colon) : f;
      t.arg = colon >= 0 ? f.substring(colon + 1) : String("");
      t.sens = -1;
      int k = 0;
      while (k < PK_N && kind != PR_KINDS[k].id) k++;
      if (k == PK_N) continue;
      t.kind = k;
      if (k == PK_SENS) {
        int s = 0;
        while (s < prSensN && prSens[s] != t.arg) s++;
        if (s == prSensN) {
          if (prSensN >= PR_MAX_SENS) continue;
          prSens[prSensN++] = t.arg;
        }
        t.sens = s;
      }
      prTiles.push_back(t);
    }
  }
  prHave = PR_ENABLED && prFirst >= 0 && prCount > 0 && prTiles.size() > 0;
  prQuery = "/printer/objects/query?heater_bed=temperature,target&extruder=temperature,target"
            "&print_stats=state,print_duration&display_status=progress"
            "&gcode_move=speed_factor,extrude_factor,homing_origin&firmware_retraction=retract_length"
            "&fan=speed&toolhead=homed_axes";
  for (int s = 0; s < prSensN; s++) prQuery += "&" + prUrlEnc(prSens[s]) + "=temperature";
  if (xSemaphoreTake(prMutex, portMAX_DELAY)) {
    prIpAt = 0;
    prSt = PrState();
    prSeq++;
    xSemaphoreGive(prMutex);
  }
}

static void prLoad() {
  File f = LittleFS.open("/printer.txt", "r");
  if (!f) {
    prHave = false;
    return;
  }
  String txt = f.readString();
  f.close();
  prParse(txt);
}

// PRINTER BEGIN | L <hex-zeile> | END | CLEAR
static void prHandleLine(char* arg) {
  if (strcmp(arg, "BEGIN") == 0) {
    prRx = "";
  } else if (strncmp(arg, "L ", 2) == 0) {
    if (prRx.length() < 6000) prRx += fromHex(arg + 2) + "\n";
  } else if (strcmp(arg, "END") == 0) {
    File f = LittleFS.open("/printer.txt", "w");
    if (f) {
      f.print(prRx);
      f.close();
    }
    prParse(prRx);
    prRx = "";
  } else if (strcmp(arg, "CLEAR") == 0) {
    LittleFS.remove("/printer.txt");
    prParse("");
  } else {
    io->println("ERR printer");
    return;
  }
  io->println("ACK");
}

// ------------------------------------------------------------ HTTP (nur im Drucker-Task)
static bool prResolve(IPAddress& ip) {
  if (prIpAt && millis() - prIpAt < 300000) {
    ip = prIp;
    return true;
  }
  IPAddress a;
  String h = prHost;
  if (h.endsWith(".local") && mdnsOn) {
    a = MDNS.queryHost(h.substring(0, h.length() - 6).c_str(), 1500);
  } else if (!a.fromString(h)) {
    if (!WiFi.hostByName(h.c_str(), a)) a = IPAddress();
  }
  if ((uint32_t)a == 0 && !a.fromString(prIpStr)) return false;  // Rückfall: IP, die die App kannte
  prIp = a;
  prIpAt = millis();
  ip = a;
  return true;
}

// Antwort-Code (oder -1) und Inhalt
static int prHttp(const char* method, const String& path, String& body, uint32_t timeoutMs) {
  IPAddress ip;
  body = "";
  if (!prResolve(ip)) return -1;
  WiFiClient c;
  if (!c.connect(ip, prPort, 1500)) {
    prIpAt = 0;  // beim nächsten Mal neu auflösen
    return -1;
  }
  c.printf("%s %s HTTP/1.0\r\nHost: %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", method, path.c_str(),
           prHost.c_str());
  String resp;
  resp.reserve(2048);
  uint32_t t0 = millis();
  while (c.connected() || c.available()) {
    int n = c.available();
    if (n > 0) {
      while (n-- > 0 && resp.length() < 8000) resp += (char)c.read();
      if (resp.length() >= 8000) break;
    } else {
      if (millis() - t0 > timeoutMs) break;
      delay(5);
    }
  }
  c.stop();
  int code = -1;
  if (resp.startsWith("HTTP/")) code = resp.substring(resp.indexOf(' ') + 1).toInt();
  int b = resp.indexOf("\r\n\r\n");
  body = b >= 0 ? resp.substring(b + 4) : String("");
  return code;
}

// kleiner JSON-Leser: Wert von "key" im Objekt "obj" (roh: Zahl, "text" ohne Anführungszeichen, [liste])
static bool prJson(const String& b, const String& obj, const char* key, String& out) {
  int p = b.indexOf("\"" + obj + "\":");
  if (p < 0) return false;
  p = b.indexOf('{', p);
  if (p < 0) return false;
  int depth = 0, e = p;
  for (; e < (int)b.length(); e++) {
    if (b[e] == '{') depth++;
    else if (b[e] == '}' && --depth == 0) break;
  }
  int k = b.indexOf(String("\"") + key + "\":", p);
  if (k < 0 || k > e) return false;
  k += strlen(key) + 3;
  while (k < e && b[k] == ' ') k++;
  if (b[k] == '"') {
    int q = b.indexOf('"', k + 1);
    out = b.substring(k + 1, q);
  } else if (b[k] == '[') {
    out = b.substring(k + 1, b.indexOf(']', k));
  } else {
    int q = k;
    while (q < e && b[q] != ',' && b[q] != '}') q++;
    out = b.substring(k, q);
  }
  out.trim();
  return out.length() > 0 && out != "null";
}

static void prNum(PrState& s, const String& b, const char* obj, const char* key, int pv, float scale = 1) {
  String v;
  if (prJson(b, obj, key, v)) {
    s.v[pv] = v.toFloat() * scale;
    s.has[pv] = true;
  }
}

static void prPoll() {
  String body;
  int code = prHttp("GET", prQuery, body, 2500);
  PrState s;
  if (code == 200) {
    s.ok = true;
    prNum(s, body, "heater_bed", "temperature", PV_BED);
    prNum(s, body, "heater_bed", "target", PV_BEDT);
    prNum(s, body, "extruder", "temperature", PV_NOZ);
    prNum(s, body, "extruder", "target", PV_NOZT);
    prNum(s, body, "gcode_move", "speed_factor", PV_SPEED, 100);
    prNum(s, body, "gcode_move", "extrude_factor", PV_FLOW, 100);
    prNum(s, body, "firmware_retraction", "retract_length", PV_RETR);
    prNum(s, body, "fan", "speed", PV_FAN, 100);
    prNum(s, body, "display_status", "progress", PV_PROG, 100);
    prNum(s, body, "print_stats", "print_duration", PV_DUR);
    String v;
    if (prJson(body, "gcode_move", "homing_origin", v)) {
      int c1 = v.indexOf(','), c2 = v.indexOf(',', c1 + 1), c3 = v.indexOf(',', c2 + 1);
      if (c2 > 0) {
        s.v[PV_ZOFF] = v.substring(c2 + 1, c3 > 0 ? c3 : v.length()).toFloat();
        s.has[PV_ZOFF] = true;
      }
    }
    if (prJson(body, "print_stats", "state", v)) strlcpy(s.state, v.c_str(), sizeof(s.state));
    if (prJson(body, "toolhead", "homed_axes", v)) strlcpy(s.homed, v.c_str(), sizeof(s.homed));
    for (int k = 0; k < prSensN; k++) {
      if (prJson(body, prSens[k], "temperature", v)) {
        s.sens[k] = v.toFloat();
        s.sensHas[k] = true;
      }
    }
  }
  if (xSemaphoreTake(prMutex, portMAX_DELAY)) {
    prSt = s;
    prSeq++;
    xSemaphoreGive(prMutex);
  }
}

static void prTask(void*) {
  uint32_t last = 0;
  for (;;) {
    if (!prOn || WiFi.status() != WL_CONNECTED) {
      delay(200);
      continue;
    }
    String cmd, done;
    if (xSemaphoreTake(prMutex, portMAX_DELAY)) {
      cmd = prCmd;
      done = prCmdDone;
      prCmd = "";
      xSemaphoreGive(prMutex);
    }
    if (cmd.length()) {
      String body;
      int code = prHttp("POST", "/printer/gcode/script?script=" + prUrlEnc(cmd), body, 900000);
      String msg = done;
      if (code != 200) {
        String err;
        if (!prJson(body, "error", "message", err)) err = code < 0 ? "nicht erreichbar" : "Fehler " + String(code);
        msg = "Drucker: " + err.substring(0, 28);
      }
      if (xSemaphoreTake(prMutex, portMAX_DELAY)) {
        prMsg = msg;
        prCmdBusy = false;
        xSemaphoreGive(prMutex);
      }
      last = 0;
    }
    if (prForcePoll || !last || millis() - last >= PR_POLL_MS) {
      prForcePoll = false;
      last = millis() ? millis() : 1;
      prPoll();
    }
    delay(30);
  }
}

// ------------------------------------------------------------ Anzeige
static PrState prState() {
  PrState s;
  if (xSemaphoreTake(prMutex, portMAX_DELAY)) {
    s = prSt;
    xSemaphoreGive(prMutex);
  }
  return s;
}

static bool prPrinting(const PrState& s) { return !strcmp(s.state, "printing") || !strcmp(s.state, "paused"); }

static String prFmt(int kind, float v) {
  char b[16];
  switch (kind) {
    case PK_BED: case PK_NOZ: snprintf(b, sizeof(b), "%d C", (int)lroundf(v)); break;
    case PK_ZOFF: snprintf(b, sizeof(b), "%+.3f", v); break;
    case PK_RETR: snprintf(b, sizeof(b), "%.1f mm", v); break;
    default: snprintf(b, sizeof(b), "%d %%", (int)lroundf(v)); break;
  }
  return b;
}

static bool prAllowed(const PrTile& t, const PrState& s) {
  bool printing = prPrinting(s);
  if (t.kind == PK_PAUSE) return !strcmp(s.state, "printing");
  if (t.kind == PK_RESUME) return !strcmp(s.state, "paused");
  if (t.kind == PK_CANCEL) return printing;
  if (t.kind == PK_MACRO && t.arg.startsWith("SET_PAUSE")) return true;
  return !printing;
}

static bool prBusy() {
  bool b = false;
  if (xSemaphoreTake(prMutex, portMAX_DELAY)) {
    b = prCmdBusy;
    xSemaphoreGive(prMutex);
  }
  return b;
}

// Inhalt einer Drucker-Kachel als Text (Haupt-, Unter-, Kopfzeile, Farbe, Rahmen)
struct PrView {
  String top, main, sub;
  uint16_t col, subCol, frame;
  bool own;  // true = Deck zeichnet die Kachel selbst (sonst gespeichertes Icon)
};

static PrView prView(int i) {
  PrView w{"", "", "", COL_TEXT, COL_DIM, 0, false};
  PrTile* t = prTileAt(curPage, i);
  if (!t) return w;
  PrState s = prState();
  const PrKind& k = PR_KINDS[t->kind];
  if (k.type == PT_ADJ) {
    w.own = true;
    w.top = k.name;
    bool pend = prGrab == i && prHasTarget;
    if (prGrab == i && millis() - prGrabAt < PR_GRAB_MS) w.frame = COL_ACCENT;
    if (!s.ok) {
      w.main = "?";
      w.col = COL_DIM;
    } else if (t->kind == PK_BED || t->kind == PK_NOZ) {
      int cur = t->kind == PK_BED ? PV_BED : PV_NOZ;
      float tgt = s.v[k.pv];
      w.main = s.has[cur] ? String((int)lroundf(s.v[cur])) + " C" : String("-");
      w.col = tgt > 0 ? (s.v[cur] < tgt - 2 ? COL_LONG : COL_GREEN) : COL_TEXT;
      float shown = pend ? prTarget : tgt;
      w.sub = shown > 0 ? "-> " + String((int)lroundf(shown)) : String("aus");
      if (pend) w.subCol = COL_ACCENT;
    } else {
      bool has = s.has[k.pv];
      w.main = pend ? prFmt(t->kind, prTarget) : (has ? prFmt(t->kind, s.v[k.pv]) : String("n. a."));
      if (pend) w.col = COL_ACCENT;
    }
  } else if (t->kind == PK_SENS) {
    w.own = true;
    w.top = t->arg.substring(t->arg.indexOf(' ') + 1);
    w.main = s.ok && t->sens >= 0 && s.sensHas[t->sens] ? String(s.sens[t->sens], 1) + " C" : String("?");
  } else if (t->kind == PK_PROG) {
    w.own = true;
    w.top = "Druck";
    bool active = prPrinting(s);
    if (!s.ok) w.main = "?";
    else if (active) {
      float p = s.v[PV_PROG];
      w.main = String((int)p) + " %";
      w.col = !strcmp(s.state, "paused") ? COL_WARN : COL_GREEN;
      if (!strcmp(s.state, "paused")) w.sub = "Pause";
      else if (p > 1 && s.v[PV_DUR] > 0) w.sub = "noch " + String((int)(s.v[PV_DUR] * (100 - p) / p / 60)) + " min";
    } else {
      w.main = !strcmp(s.state, "complete") ? "fertig" : !strcmp(s.state, "error") ? "Fehler" : "bereit";
    }
  } else {  // Befehl: gespeichertes Icon, Rahmen zeigt den Zustand
    if (t->kind == PK_CANCEL && prConfirmTile == i && millis() - prConfirmAt < PR_GRAB_MS) w.frame = COL_LONG;
    else if (prBusy()) w.frame = COL_WARN;
    else if (s.ok && !prAllowed(*t, s)) w.frame = COL_DIM;
  }
  return w;
}

static String prSigOf(int i) {
  PrView w = prView(i);
  return w.top + "|" + w.main + "|" + w.sub + "|" + String(w.col) + "|" + String(w.subCol) + "|" + String(w.frame);
}

// Hook in drawTileTo: true = Kachel fertig gezeichnet (Icon/Beschriftung weglassen)
static bool prDrawTile(lgfx::LovyanGFX& g, int x, int y, int w, int h, int i) {
  if (!prOn) return false;
  PrView v = prView(i);
  if (i < PR_MAX_TILES) prSig[i] = prSigOf(i);
  if (v.frame)
    for (int k = 0; k < 3; k++) g.drawRoundRect(x + k, y + k, w - 2 * k, h - 2 * k, 10 - k, v.frame);
  if (!v.own) return false;
  g.setTextDatum(middle_center);
  g.setFont(&fonts::Font2);
  g.setTextColor(COL_DIM);
  g.drawString(fitText(g, v.top, w - 8), x + w / 2, y + 14);
  g.setFont(&fonts::FreeSansBold12pt7b);
  g.setTextColor(v.col);
  g.drawString(fitText(g, v.main, w - 8), x + w / 2, y + h / 2 + (v.sub.length() ? -2 : 4));
  if (v.sub.length()) {
    g.setFont(&fonts::Font2);
    g.setTextColor(v.subCol);
    g.drawString(fitText(g, v.sub, w - 8), x + w / 2, y + h - 14);
  }
  return true;
}

static void prOsd(const String& t) {
  osdText = t;
  osdPct = -1;
  osdUntil = millis() + 2500;
  if (!osdUntil) osdUntil = 1;
  tft.startWrite();
  drawOsd();
  tft.endWrite();
}

static void prRedraw(int i) {
  tft.startWrite();
  drawTile(i);
  tft.endWrite();
}

// ------------------------------------------------------------ Bedienen
static void prQueue(const String& script, const String& done, const String& startMsg) {
  bool ok = false;
  if (xSemaphoreTake(prMutex, portMAX_DELAY)) {
    if (!prCmdBusy) {
      prCmd = script;
      prCmdDone = done;
      prCmdBusy = true;
      ok = true;
    }
    xSemaphoreGive(prMutex);
  }
  prOsd(ok ? startMsg : String("Drucker: Befehl laeuft noch"));
}

static String prValueText(int kind, float v) {
  String n = PR_KINDS[kind].name;
  if ((kind == PK_BED || kind == PK_NOZ) && v <= 0) return n + ": aus";
  return n + ": " + prFmt(kind, v);
}

static void prSendValue(const PrTile& t, float v) {
  PrState s = prState();
  char b[80];
  switch (t.kind) {
    case PK_BED: snprintf(b, sizeof(b), "SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=%d", (int)lroundf(v)); break;
    case PK_NOZ: snprintf(b, sizeof(b), "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=%d", (int)lroundf(v)); break;
    case PK_SPEED: snprintf(b, sizeof(b), "M220 S%d", (int)lroundf(v)); break;
    case PK_FLOW: snprintf(b, sizeof(b), "M221 S%d", (int)lroundf(v)); break;
    case PK_RETR: snprintf(b, sizeof(b), "SET_RETRACTION RETRACT_LENGTH=%.2f", v); break;
    case PK_FAN:
      if (v > 0) snprintf(b, sizeof(b), "M106 S%d", (int)lroundf(v * 2.55f));
      else strcpy(b, "M107");
      break;
    case PK_ZOFF: {
      float d = v - s.v[PV_ZOFF];
      if (fabsf(d) < 0.0005f) return;
      snprintf(b, sizeof(b), "SET_GCODE_OFFSET Z_ADJUST=%+.3f MOVE=%d", d, strchr(s.homed, 'z') ? 1 : 0);
      break;
    }
    default: return;
  }
  prQueue(b, prValueText(t.kind, v) + " gesetzt", prValueText(t.kind, v) + " ...");
}

static void prPress(int i, bool isLong) {
  PrTile* t = prTileAt(curPage, i);
  if (!t) return;
  const PrKind& k = PR_KINDS[t->kind];
  PrState s = prState();
  uint32_t now = millis();
  if (k.type == PT_ADJ) {
    if (isLong && (t->kind == PK_BED || t->kind == PK_NOZ || t->kind == PK_SPEED || t->kind == PK_FLOW ||
                   t->kind == PK_FAN)) {
      prHasTarget = false;
      float v = (t->kind == PK_SPEED || t->kind == PK_FLOW) ? 100 : 0;
      prSendValue(*t, v);
      return;
    }
    int old = prGrab;
    prGrab = i;
    prGrabAt = now;
    prHasTarget = false;
    if (old >= 0 && old != i) prRedraw(old);
    prRedraw(i);
    if (!s.ok) prOsd("Drucker nicht erreichbar");
    else if (!s.has[k.pv]) prOsd(String(k.name) + ": nicht eingerichtet");
    else prOsd(prValueText(t->kind, s.v[k.pv]));
    return;
  }
  if (k.type == PT_SHOW) return;
  String name = t->kind == PK_MACRO ? t->arg : String(k.name);
  if (!isLong) {
    prOsd(name + ": lang druecken");
    return;
  }
  if (!s.ok) {
    prOsd("Drucker nicht erreichbar");
    return;
  }
  if (!prAllowed(*t, s)) {
    prOsd(name + (t->kind == PK_PAUSE || t->kind == PK_CANCEL ? ": kein Druck aktiv"
                  : t->kind == PK_RESUME ? ": nicht pausiert" : ": gesperrt, Druck laeuft"));
    return;
  }
  if (t->kind == PK_CANCEL) {
    prConfirmAt = now;
    prConfirmTile = i;
    prRedraw(i);
    prOsd("Druck abbrechen? Drehknopf druecken");
    return;
  }
  String script = t->kind == PK_MACRO ? t->arg : String(k.gcode);
  bool homed = strchr(s.homed, 'x') && strchr(s.homed, 'y') && strchr(s.homed, 'z');
  if ((t->kind == PK_ZTILT || t->kind == PK_QGL || t->kind == PK_MESH) && !homed) script = "G28\n" + script;
  prQueue(script, name + ": fertig", name + " ...");
}

static void prKnobPress() {
  uint32_t now = millis();
  if (prConfirmTile >= 0 && now - prConfirmAt < PR_GRAB_MS) {
    int i = prConfirmTile;
    prConfirmTile = -1;
    prQueue("CANCEL_PRINT", "Druck abgebrochen", "Breche Druck ab ...");
    prRedraw(i);
    return;
  }
  if (prGrab < 0 || now - prGrabAt >= PR_GRAB_MS) return;
  PrTile* t = prTileAt(curPage, prGrab);
  if (!t) return;
  prGrabAt = now;
  if (prHasTarget) {
    prHasTarget = false;
    prSendValue(*t, prTarget);
    prRedraw(prGrab);
  } else {
    PrState s = prState();
    const PrKind& k = PR_KINDS[t->kind];
    if (s.ok && s.has[k.pv]) prOsd(prValueText(t->kind, s.v[k.pv]));
  }
}

static void prRotate(int n) {
  uint32_t now = millis();
  if (prGrab < 0 || now - prGrabAt >= PR_GRAB_MS) return;
  PrTile* t = prTileAt(curPage, prGrab);
  if (!t || PR_KINDS[t->kind].type != PT_ADJ) return;
  const PrKind& k = PR_KINDS[t->kind];
  float base;
  if (prHasTarget) {
    base = prTarget;
  } else {
    PrState s = prState();
    if (!s.ok || !s.has[k.pv]) {
      prOsd(s.ok ? String(k.name) + ": nicht eingerichtet" : String("Drucker nicht erreichbar"));
      return;
    }
    base = s.v[k.pv];
    if (t->kind != PK_ZOFF) base = roundf(base / k.step) * k.step;  // Z-Offset genau vom Ist-Wert aus
  }
  float v = base + n * k.step;
  v = roundf(v * 1000) / 1000;
  if (v < k.lo) v = k.lo;
  if (v > k.hi) v = k.hi;
  prTarget = v;
  prHasTarget = true;
  prGrabAt = now;
  prRedraw(prGrab);
  prOsd(prValueText(t->kind, v) + " - druecken = OK");
}

static void prExit() {
  if (!prOn) return;
  prOn = false;
  prGrab = -1;
  prHasTarget = false;
  prConfirmTile = -1;
  osdUntil = 0;
  downTile = -1;
  drawPage();
}

// langer Tipper aufs Ruhebild
static void prTryEnter() {
  if (!prHave || pcOk) return;
  if (!wifiActive || WiFi.status() != WL_CONNECTED) {
    reopenWifiAsk();
    return;
  }
  prOn = true;
  fightOn = false;
  walkKind = -1;
  wifiAsk = false;
  curPage = prFirst;
  clearLive();
  for (auto& s : prSig) s = "";
  prSeenSeq = 0;
  prForcePoll = true;
  lastUse = millis();
  drawPage();
}

// Touch im Drucker-Betrieb (statt PRESS/LONG an die App)
static void prTouch(bool touched, bool down, int32_t tx, int32_t ty, uint32_t now) {
  if (down && !wasDown && touched) {
    downTile = -1;
    downNav = 0;
    longSent = false;
    downSince = now;
    if (ty >= gridH()) {
      downNav = tx < 120 ? -1 : (tx > SCREEN_W - 120 ? 1 : 2);  // 2 = Mitte der Leiste
    } else {
      int c = tx / (SCREEN_W / gCols), r = ty / (gridH() / gRows);
      int i = r * gCols + c;
      if (c < gCols && r < gRows && prTileAt(curPage, i)) {
        downTile = i;
        tft.startWrite();
        drawTile(i, true);
        tft.endWrite();
      }
    }
  }
  if (down && !longSent && now - downSince >= LONG_MS && (downTile >= 0 || downNav)) {
    longSent = true;
    if (downTile >= 0) {
      tft.startWrite();
      drawTile(downTile, true, true);
      tft.endWrite();
    }
  }
  if (!down && wasDown) {
    if (downTile >= 0) {
      int i = downTile;
      downTile = -1;
      prRedraw(i);
      prPress(i, longSent);
    } else if (downNav) {
      if (longSent) {
        prExit();  // lang auf die Leiste: zurück zur Uhr
        return;
      }
      if (downNav != 2 && prCount > 1) {
        curPage = prFirst + (curPage - prFirst + (downNav > 0 ? 1 : prCount - 1)) % prCount;
        prGrab = -1;
        prHasTarget = false;
        for (auto& s : prSig) s = "";
        drawPage();
      }
    }
    downTile = -1;
    downNav = 0;
    longSent = false;
  }
}

static void pollPrinter() {
  if (!prOn) return;
  uint32_t now = millis();
  if (pcOk || !wifiActive || now - lastUse > PR_IDLE_MS) {
    if (pcOk) prOn = false;  // App ist wieder da: sie zeichnet die Seite
    else prExit();
    return;
  }
  if (prHasTarget && now - prGrabAt >= PR_GRAB_MS) {  // nicht bestätigt: verwerfen
    prHasTarget = false;
    PrTile* t = prTileAt(curPage, prGrab);
    if (t) prOsd(String(PR_KINDS[t->kind].name) + ": verworfen");
  }
  String msg;
  uint32_t seq = 0;
  if (xSemaphoreTake(prMutex, portMAX_DELAY)) {
    msg = prMsg;
    prMsg = "";
    seq = prSeq;
    xSemaphoreGive(prMutex);
  }
  if (msg.length()) prOsd(msg);
  static uint32_t lastCheck = 0;
  if (seq == prSeenSeq && now - lastCheck < 500) return;  // Rahmen (Knopf, Abfrage) laufen auch ab
  prSeenSeq = seq;
  lastCheck = now;
  if (osdUntil) return;  // Wert-Anzeige liegt drüber; nach ihrem Ende zeichnet pollOsd die Seite neu
  for (int i = 0; i < gCols * gRows && i < PR_MAX_TILES; i++) {
    if (!prTileAt(curPage, i)) continue;
    if (prSigOf(i) != prSig[i]) prRedraw(i);
  }
}

static void prSetup() {
  COL_GREEN = tft.color565(80, 200, 120);
  prMutex = xSemaphoreCreateMutex();
  prLoad();
  if (PR_ENABLED) xTaskCreatePinnedToCore(prTask, "printer", 8192, nullptr, 1, nullptr, 0);
}
