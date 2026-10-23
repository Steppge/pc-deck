# PC-Deck

Selbstgebautes „Stream Deck“ für Windows: ESP32-Touchdisplays (3,5", 480 × 320) zeigen Kacheln mit
den echten Windows-Icons deiner Programme, dazu Live-Kacheln (Uhr, CPU, Lautstärke, Medien …),
einen frei belegbaren Drehknopf, Designs und optional WLAN. Eine PC-App (Python) betreibt beliebig
viele Decks gleichzeitig.

| Ordner | Inhalt |
|---|---|
| [`pcdeck/firmware`](pcdeck/firmware) | ESP32-Firmware (PlatformIO, LovyanGFX) – Deck 1: ESP32-S3 + MKS TS35, Deck 2: ESP32 + BTT TFT35 SPI V2.1 |
| [`pcdeck/pc-app`](pcdeck/pc-app) | Windows-App (Python): Konfiguration, Icons, Aktionen, Drehknopf, Designs, WLAN |
| [`pcdeck/gehaeuse`](pcdeck/gehaeuse) | Parametrisches Tischgehäuse (CadQuery) mit fertigen STL/STEP |
| [`pcdeck/installer`](pcdeck/installer) | Inno-Setup-Skript und Build-Skript für die .exe |

**Anleitung:** [pcdeck/README.md](pcdeck/README.md)

Lizenz: [GPL-3.0](LICENSE)
