"""Was gerade spielt (Titel, Interpret, Cover, Status) – über die Windows-Medien-Schnittstelle
GlobalSystemMediaTransportControls, dieselbe Quelle wie die Medien-Einblendung von Windows.

Python kommt ohne Zusatzpaket nicht an diese WinRT-Schnittstelle. Deshalb läuft ein kleines,
unsichtbares PowerShell-Skript (in Windows enthalten) im Hintergrund und meldet als JSON-Zeile,
sobald sich etwas ändert. Es beendet sich selbst, wenn PC-Deck nicht mehr läuft.
"""

import atexit
import base64
import io
import json
import logging
import os
import subprocess
import threading

log = logging.getLogger("pcdeck")

_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(3000) | Out-Null; $t.Result
}
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataReader,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) `
             ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$last = ''
while ($true) {
  if (-not (Get-Process -Id $env:PCDECK_PID -ErrorAction SilentlyContinue)) { exit }
  $o = @{ title = ''; artist = ''; app = ''; status = 'none'; cover = $null }
  $s = $mgr.GetCurrentSession()
  if ($s) {
    $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
    $o.title = [string]$p.Title; $o.artist = [string]$p.Artist; $o.app = [string]$s.SourceAppUserModelId
    $o.status = [string]$s.GetPlaybackInfo().PlaybackStatus
    $key = $o.title + '|' + $o.artist + '|' + $o.app
    if ($key -ne $script:coverKey) {
      $script:coverKey = $key; $script:cover = $null
      if ($p.Thumbnail) {
        $st = Await ($p.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
        if ($st -and $st.Size -gt 0 -and $st.Size -lt 4000000) {
          $r = New-Object Windows.Storage.Streams.DataReader($st.GetInputStreamAt(0))
          $n = Await ($r.LoadAsync([uint32]$st.Size)) ([uint32])
          $b = New-Object byte[] $n; $r.ReadBytes($b)
          $script:cover = [Convert]::ToBase64String($b)
        }
      }
    }
    $o.cover = $script:cover
  }
  $j = $o | ConvertTo-Json -Compress
  if ($j -ne $last) { $last = $j; [Console]::Out.WriteLine($j); [Console]::Out.Flush() }
  Start-Sleep -Milliseconds 700
}
"""

_lock = threading.Lock()
_proc = None
_state = {"title": "", "artist": "", "app": "", "status": "none", "cover": None}
_cover_img = None
_cover_b64 = None


def _reader(proc):
    global _cover_img, _cover_b64
    for raw in proc.stdout:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        cover = data.get("cover")
        img = _cover_img
        if cover != _cover_b64:
            img = None
            if cover:
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(base64.b64decode(cover))).convert("RGB")
                except Exception:
                    img = None
        with _lock:
            _state.update({k: data.get(k) or "" for k in ("title", "artist", "app", "status")})
            _cover_b64, _cover_img = cover, img
    log.info("Medien-Hilfsprogramm beendet")


def _start():
    global _proc
    if _proc and _proc.poll() is None:
        return
    try:
        _proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                 env=dict(os.environ, PCDECK_PID=str(os.getpid())),
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=_reader, args=(_proc,), daemon=True).start()
        log.info("Medien-Hilfsprogramm gestartet")
    except OSError as e:
        log.error("Medien-Hilfsprogramm konnte nicht starten: %s", e)


@atexit.register
def _stop():
    if _proc and _proc.poll() is None:
        _proc.terminate()


def now_playing():
    """(title, artist, status, cover-PIL-Bild oder None); status: Playing, Paused, Stopped, none ...
    Startet das Hilfsprogramm beim ersten Aufruf."""
    _start()
    with _lock:
        return _state["title"], _state["artist"], _state["status"], _cover_img
