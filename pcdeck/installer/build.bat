@echo off
rem PC-Deck – alles bauen: PC-Deck.exe und Installer (PC-Deck-Setup.exe)
rem Ergebnis: installer\Output\PC-Deck-Setup.exe
rem Voraussetzung: pc-app\.venv (siehe install.bat) und Inno Setup 6
setlocal
set "HERE=%~dp0"
set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
  if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
  echo Inno Setup 6 nicht gefunden. Installieren mit:  winget install JRSoftware.InnoSetup
  goto :fehler
)

echo [0/2] Firmware bauen (wird mit ins Setup gepackt) ...
set "PIO=%USERPROFILE%\.platformio\penv\Scripts\pio.exe"
if not exist "%PIO%" (
  echo PlatformIO nicht gefunden: %PIO%
  goto :fehler
)
pushd "%HERE%..\firmware" || goto :fehler
"%PIO%" run -e deck_s3_ts35 -e deck_esp32_tft35 || (popd & goto :fehler)
popd

echo [1/2] PC-Deck.exe bauen ...
taskkill /F /IM PC-Deck.exe >nul 2>&1
pushd "%HERE%..\pc-app" || goto :fehler
".venv\Scripts\pyinstaller.exe" --noconfirm --noconsole --onefile --name PC-Deck --collect-all tkinterdnd2 pcdeck.py || (popd & goto :fehler)
popd

echo [2/2] Installer bauen ...
pushd "%HERE%" || goto :fehler
"%ISCC%" /Q pcdeck.iss || (popd & goto :fehler)
popd

echo.
echo Fertig:
dir /b "%HERE%Output\PC-Deck-Setup.exe"
exit /b 0

:fehler
echo.
echo FEHLER beim Bauen.
exit /b 1
