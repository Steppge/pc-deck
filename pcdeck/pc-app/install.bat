@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
echo.
echo Fertig. Starten mit start.bat
pause
