# PC-Deck – Touch-Programmstarter mit ESP32 (Stand 23.10.2026, App 1.3.0, Firmware 13)

Ein selbstgebautes „Stream Deck“: Auf einem 3,5"-Touchdisplay erscheinen Kacheln mit den
**echten Windows-Icons** deiner Programme und Ordner, dazu Live-Kacheln (Uhr, CPU, Lautstärke,
Medien …). Antippen startet ein Programm, holt es nach vorne oder minimiert es; lang tippen
schließt es. Ein **Drehknopf** am Deck regelt z. B. die Lautstärke – je nach Programm im
Vordergrund anders belegt.

- **Eine PC-App für alle Decks** – jedes Deck hat sein eigenes Layout.
- **USB oder WLAN** – am PC per USB; mit USB-Netzteil oder Powerbank auch per WLAN.
- **Design bleibt im Deck** – Startbild und Farben sind gespeichert, auch an einem anderen PC.

---

## 1. Installieren

1. `PC-Deck-Setup.exe` ausführen (keine Administratorrechte nötig). Bei einer neuen Version
   einfach drüberinstallieren – vorher PC-Deck im Infobereich beenden (Rechtsklick → Beenden).
2. Auf Wunsch „Mit Windows starten“ und eine Desktop-Verknüpfung anhaken.
3. Unten rechts im Infobereich erscheint das PC-Deck-Symbol. **Doppelklick** öffnet die Konfiguration.

Deine Einstellungen liegen in `C:\Users\<Name>\PCDeck` (Konfiguration, Protokoll `pcdeck.log`).
Sie bleiben bei Updates erhalten; beim Deinstallieren wirst du gefragt, ob sie gelöscht werden sollen.

## 2. Erster Start

- Deck per USB anstecken. Ohne laufende App zeigt das Display nur das Logo
  („PC-Deck“ mit Widmung und leuchtendem Namen), mit App erscheinen die Kacheln.
- Beim **allerersten** Start des Decks erscheint die Touch-Kalibrierung: die Markierungen in den
  Ecken der Reihe nach genau antippen. Neu kalibrieren: in den ersten 3 Sekunden nach dem
  Einstecken aufs Display tippen oder in der App „Touch kalibrieren“.

## 3. Kacheln einrichten

- **Drag & Drop:** Programme, Verknüpfungen oder Ordner aus dem Explorer auf eine Kachel ziehen.
- **Kachel anklicken:** Typ wählen – Programm/Ordner/Datei/Webadresse, Uhr, CPU, RAM, Lautstärke,
  Stoppuhr, Timer, Medien, „Jetzt läuft“, Ausgabegerät, Audio-Profil, Mikrofon stumm,
  Tastenkürzel, Textbaustein, Seite wechseln, OBS.
- **Rechtsklick:** kopieren, ausschneiden, einfügen, leeren.
- **Spalten/Zeilen** (bis 5 × 3), **Seiten** (bis 8), **Helligkeit**.
- **Automatik:** pro Seite Programme eintragen – kommt eines nach vorne, springt das Deck auf diese Seite.
- **Plus-Kachel:** Das Programm im Vordergrund erscheint blass mit „+“ auf einem freien Platz –
  antippen speichert es als Kachel.
- **„Speichern & ans Deck senden“** überträgt die Änderungen.
- Maus kurz über eine Schaltfläche halten → Erklärung. **„?“** öffnet eine kurze Anleitung.

**Store-Apps** (Rechner, Fotos …): Win+R → `shell:AppsFolder`, App auf den Desktop ziehen
(erzeugt eine Verknüpfung), diese auf eine Kachel ziehen.

## 4. Bedienung am Deck

| Aktion | Wirkung |
|---|---|
| kurz tippen | Programm öffnen bzw. minimieren / zurückholen |
| lang tippen (gelber Rahmen, 0,6 s) | Programm höflich schließen (Speichern-Dialog erscheint) |
| 1,5 s halten (roter Rahmen) | Kachel löschen – mit Sicherheitsabfrage |
| `<` `>` unten | Seiten blättern |
| grüner Rahmen | dieses Programm ist gerade im Vordergrund |

- **PC gesperrt (Win+L):** Das Deck löst nichts aus, es erscheint kurz „PC gesperrt“.
- **Programme mit Administratorrechten** (z. B. FanControl) kann Windows nicht von PC-Deck aus
  minimieren oder schließen – das Deck zeigt dann „Admin-Programm: nicht steuerbar“.

## 5. Drehknopf

Unter **„Drehknopf …“** legst du fest, was Drehen, gedrückt Drehen (Knopf halten und drehen),
kurz und lang Drücken tun:

- **Standard** gilt immer; dazu **Presets je Programm** im Vordergrund, z. B.
  - Discord: drehen = Discord-Lautstärke, drücken = Mikrofon stumm
  - Edge: drehen = scrollen, gedrückt drehen = zoomen, drücken = Screenshot
- Drehen: Lautstärke (System oder nur ein Programm), Ausgabegerät, Scrollen, seitlich scrollen,
  Zoomen, Seiten blättern, Tastenkürzel.
- Drücken: Ton stumm, Mikrofon stumm, Play/Pause, nächster/voriger Titel, Screenshot,
  nächstes Ausgabegerät, Kachel auslösen, Seite 1, Tastenkürzel.
- Ab Werk: drehen = Lautstärke, gedrückt drehen = Ausgabegerät, drücken = stumm.
- Bei Lautstärke, Ausgabegerät und Seite zeigt das Deck kurz den neuen Wert an.

## 6. Design

Oben **„Design:“** – vier feste Designs: **Standard (Blau)**, **Rot / Magenta**, **Grün**,
**Schwarz / Weiß** (auch alle Icons grau). Dazu **„Eigenes …“** (Bild, Kachelfarbe, Stil,
Helligkeit, Deckkraft mit Vorschau), **„Auf dem Deck lassen“** und **„Kein Design“**.
Das Design ist im Deck gespeichert: Auch an einem anderen PC leuchtet das Startbild.

## 7. WLAN (optional)

1. Deck per USB anstecken, **„WLAN …“** öffnen.
2. **„Netze suchen“**, WLAN wählen, Passwort eintippen (wird nur im Deck gespeichert),
   **„Verbinden & koppeln“**. Das Deck kann nur 2,4-GHz-Netze nutzen.
3. Am USB ist das WLAN aus (spart Strom und Wärme). Steckst du das Deck an ein **USB-Netzteil
   oder eine Powerbank**, fragt es „Kein USB verbunden – WLAN einschalten?“ → **Ja**.
   Die App findet das Deck dann von selbst; oben rechts zeigt ein **WLAN-Symbol** die Verbindung.
- USB hat immer Vorrang. Nur der gekoppelte PC kann sich verbinden (gegenseitige Anmeldung,
  der Schlüssel geht nie übers Netz).
- Steckt das Deck an einem **anderen PC** mit PC-Deck, werden WLAN und Kopplung im Deck gelöscht;
  am eigenen PC dann einfach neu einrichten.
- Hängt das Deck an einem ausgeschalteten PC, der weiter Strom liefert, kann die Frage
  „WLAN einschalten?“ erscheinen – einfach ignorieren, sie verschwindet, sobald der PC läuft.

## 8. Hardware und Verdrahtung (Deck 1)

ESP32-S3 N16R8 + MKS TS35 V2.0 (ST7796 480 × 320, Touch XPT2046), Drehgeber EC11.
Kabel ab Ader 1 (roter Streifen) gezählt, ESP32-Pins von der Antenne aus gezählt:

| Signal | Kabel/Ader | ESP32-S3 | Pin |
|---|---|---|---|
| 5 V | EXP1/1 | 5Vin | J1-21 |
| GND | EXP1/2, EXP2/2 | GND | J3-1 |
| LCD DC | EXP1/3 | GPIO4 | J1-4 |
| LCD CS | EXP1/4 | GPIO5 | J1-5 |
| Touch CS | EXP1/6 | GPIO6 | J1-6 |
| LCD Reset | EXP1/7 | GPIO7 | J1-7 |
| Hintergrundlicht | EXP1/8 | GPIO15 | J1-8 |
| SPI MOSI | EXP2/5 | GPIO11 | J1-17 |
| SPI SCK | EXP2/9 | GPIO12 | J1-18 |
| SPI MISO | EXP2/10 | GPIO13 | J1-19 |
| Drehgeber A (blau) | – | GPIO1 | J3-4 |
| Drehgeber B (lila) | – | GPIO2 | J3-5 |
| Drehgeber Taster (weiß) | – | GPIO42 | J3-6 |
| Drehgeber gemeinsam (schwarz) | – | GND | J3-1 |

Das Display ist im Gehäuse um 180° gedreht eingebaut; die Firmware dreht das Bild passend.

## 9. Firmware flashen

1. PC-Deck beenden (belegt sonst den COM-Port).
2. Ordner `firmware` in VS Code mit PlatformIO öffnen, Umgebung **`deck_s3_ts35`** wählen,
   **Upload** (bzw. `pio run -e deck_s3_ts35 -t upload --upload-port COM7`).
3. Hängt der Upload bei „Connecting…“: abbrechen und erneut starten, sonst BOOT halten,
   kurz RST drücken, BOOT loslassen.

## 10. Gehäuse

Im Ordner `gehaeuse`: `pcdeck_kasten.stl` und `pcdeck_rahmen.stl` (Bilder:
`pcdeck_gehaeuse_ansichten.png`, Schnitte: `pcdeck_gehaeuse_details.png`).
- Drucken: Rahmen mit der Vorderseite nach unten, Kasten auf dem Boden stehend
  (die obere Innenfläche ist ein Überhang von ca. 50°).
- Display und Rahmen: 4 Senkkopf-Holzschrauben **2,5 × 12**.
- Rahmen seitlich: 1 Senkkopfschraube **2,5 × 6** durch die rechte Seitenwand in die Lasche
  (nicht länger – dahinter sitzt der Drehgeber).
- ESP32 kopfüber mit doppelseitigem Klebeband auf den Sockel, USB-Buchse an die Öffnung in der Rückwand.
- `pcdeck_klemme.*` wird nicht mehr gebraucht.

## 11. Fehlersuche

| Problem | Lösung |
|---|---|
| Deck wird nicht erkannt | Läuft PC-Deck zweimal oder ein serieller Monitor? Beenden. USB-Kabel/Buchse „USB“ prüfen. `pcdeck.log` ansehen. |
| Nur Logo, keine Kacheln | PC-Deck läuft nicht oder ist nicht verbunden (Symbol im Infobereich). |
| Kacheln leer nach anderem PC | Der andere PC hat seine (leeren) Kacheln übertragen – hier einmal „Speichern & ans Deck senden“. |
| Touch reagiert nicht | Touch CS (EXP1/6 → GPIO6) und MISO (EXP2/10 → GPIO13) auf Durchgang prüfen; neu kalibrieren. |
| Touch versetzt | Neu kalibrieren (in den ersten 3 s nach dem Einstecken tippen). |
| Drehrichtung falsch | In `platformio.ini` `-DENC_REVERSE` umstellen und neu flashen. |
| Programm öffnet sich hinten | Ab 1.3.0 behoben; bei Admin-Programmen geht es nicht (Windows-Schutz). |
| WLAN findet das Deck nicht | Am Deck „WLAN einschalten?“ mit Ja bestätigt? PC und Deck im selben Netz? Neu koppeln. |
| Farben/Bild falsch | `-DTFT_INVERT=1` (invertiert) bzw. `-DTFT_BGR=1` (Rot/Blau vertauscht). |
