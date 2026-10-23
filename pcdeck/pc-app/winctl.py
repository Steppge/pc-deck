"""Laufende Programme / offene Ordner einer Kachel finden, minimieren, zurückholen, schließen.

"Schließen" heißt: WM_CLOSE an die Fenster schicken, wie ein Klick aufs X.
Programme mit ungespeicherten Änderungen fragen also wie gewohnt nach – dafür wird
ein minimiertes Fenster vorher zurückgeholt, damit der Speichern-Dialog sichtbar ist.
Es wird nie ein Prozess hart beendet.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import ntpath
import os
import subprocess
import threading
import time

import iconextract

log = logging.getLogger("pcdeck")

if os.name == "nt":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
    user32.GetWindow.restype = wt.HWND
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.IsIconic.argtypes = [wt.HWND]
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.OpenInputDesktop.restype = wt.HANDLE
    user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    user32.CloseDesktop.argtypes = [wt.HANDLE]
    user32.GetUserObjectInformationW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                                 ctypes.POINTER(wt.DWORD)]
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.AllowSetForegroundWindow.argtypes = [wt.DWORD]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.BringWindowToTop.argtypes = [wt.HWND]
    user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]

GW_OWNER = 4
WM_CLOSE = 0x0010
SW_MINIMIZE, SW_RESTORE = 6, 9
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _exe_of(pid, cache):
    if pid not in cache:
        cache[pid] = ""
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            buf = ctypes.create_unicode_buffer(1024)
            n = wt.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                cache[pid] = buf.value
            kernel32.CloseHandle(h)
    return cache[pid]


# ----------------------------------------------------------------- Prozessliste
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


TH32CS_SNAPPROCESS = 0x2
if os.name == "nt":
    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

_procs_cache = {"t": -1.0, "procs": {}}


def _processes():
    """{pid: (eltern-pid, programmname klein)} aller Prozesse, höchstens alle 0,5 s neu gelesen."""
    if time.monotonic() - _procs_cache["t"] < 0.5:
        return _procs_cache["procs"]
    procs = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap and snap != wt.HANDLE(-1).value:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            procs[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile.lower())
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
        kernel32.CloseHandle(snap)
    _procs_cache.update(t=time.monotonic(), procs=procs)
    return procs


def _family(match):
    """(Programm-Prozesse, Programm-Prozesse + alle von ihnen gestarteten Unterprozesse).
    Beispiel Steam: Das Fenster gehört nicht steam.exe, sondern steamwebhelper.exe."""
    procs = _processes()
    cache = {}
    roots = {pid for pid, (_, name) in procs.items()
             if name == match.name and (match(_exe_of(pid, cache)) or not _exe_of(pid, cache))}
    tree = set(roots)
    grown = True
    while grown:
        grown = False
        for pid, (ppid, _) in procs.items():
            if ppid in tree and pid not in tree and pid != ppid:
                tree.add(pid)
                grown = True
    # Nur Unterprozesse aus dem Programmordner zählen (steamwebhelper ja) – nicht aber
    # Programme, die vom Starter aus gestartet wurden (Spiele unter Steam\steamapps)
    family = set(roots)
    for pid in tree - roots:
        exe = os.path.normcase(_exe_of(pid, cache))
        if any(exe.startswith(d) and SEP_STEAMAPPS not in exe[len(d) - 1:] for d in _root_dirs(roots, cache)):
            family.add(pid)
    return roots, family


SEP_STEAMAPPS = os.path.normcase(r"\steamapps" + "\\")


def _root_dirs(roots, cache):
    dirs = set()
    for pid in roots:
        exe = _exe_of(pid, cache)
        if exe:
            dirs.add(os.path.normcase(os.path.dirname(exe)) + os.sep)
    return dirs


def is_running(tile, path):
    """Läuft das Programm der Kachel (auch ohne sichtbares Fenster, z. B. im Infobereich)?"""
    match = _matcher(tile, path) if os.name == "nt" and not os.path.isdir(path) else None
    return bool(match and _family(match)[0])


def _app_windows(match):
    """Sichtbare Hauptfenster des Programms – auch die seiner Unterprozesse."""
    found, cache = [], {}
    family = _family(match)[1] if getattr(match, "name", None) else set()

    def cb(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER)
                and user32.GetWindowTextLengthW(hwnd) > 0):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in family:
                found.append(hwnd)
            else:
                exe = _exe_of(pid.value, cache)
                if exe and match(exe):
                    found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


_FOLDER_SCRIPT = r"""
$want = [IO.Path]::GetFullPath($env:PCDECK_DIR).TrimEnd('\')
foreach ($w in @((New-Object -ComObject Shell.Application).Windows())) {
  try { $p = $w.Document.Folder.Self.Path } catch { continue }
  if ($p -and $p.TrimEnd('\') -ieq $want) {
    $w.HWND  # vor Quit() abfragen, danach ist das Objekt ungültig
    if ($env:PCDECK_ACTION -eq 'close') { $w.Quit() }
  }
}
"""


def _folder(path, action):
    """Explorer-Fenster/-Tabs mit genau diesem Ordner: action "find" (Fensterliste) oder "close"."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _FOLDER_SCRIPT], capture_output=True,
                             timeout=15, env=dict(os.environ, PCDECK_DIR=path, PCDECK_ACTION=action),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return sorted({int(x) for x in out.decode("ascii", "ignore").split() if x.strip().isdigit()})
    except Exception as e:
        log.error("Ordnerfenster konnten nicht geprüft werden: %s", e)
        return []


def _foreground(hwnd):
    """Fenster nach vorne holen. Windows erlaubt das Hintergrund-Programmen nur eingeschränkt.
    Zwei übliche Wege kombiniert: ein kurzer Alt-Tastendruck (gilt als Benutzereingabe) und
    die Eingabe kurz an das aktuelle Vordergrund-Programm koppeln (AttachThreadInput)."""
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_tid = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != my_tid and user32.AttachThreadInput(my_tid, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)


ASFW_ANY = 0xFFFFFFFF


def allow_foreground():
    """Vor einem Programmstart: Das Antippen am Deck zählt für Windows nicht als Eingabe am PC, und
    der Fokusschutz würde das neue Fenster hinten öffnen. Ein kurzer Alt-Tastendruck gilt als Eingabe;
    damit darf PC-Deck das Vordergrund-Recht an das gestartete Programm weitergeben."""
    if os.name != "nt":
        return
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.AllowSetForegroundWindow(ASFW_ANY)


def front_when_ready(tile, path, before=(), timeout=15.0, max_times=3):
    """Nach dem Start: neue Fenster des Programms nach vorne holen, sobald sie erscheinen (auch nach
    einem Startbild). Klickt der Benutzer inzwischen selbst woanders hin, bleibt alles, wie es ist."""
    if os.name != "nt":
        return
    folder = os.path.isdir(path)
    step = 1.0 if folder else 0.25  # Ordner-Suche läuft über PowerShell, also seltener
    start_fg = user32.GetForegroundWindow()
    known, mine, done = set(before), set(), 0
    end = time.time() + (8.0 if folder else timeout)
    match = None if folder else _matcher(tile, path)

    def belongs(hwnd):
        """Gehört das Fenster zum gestarteten Programm (auch Startbild/Unterprozess)?"""
        if hwnd in known:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _exe_of(pid.value, {})
        if folder:
            return ntpath.basename(exe).lower() == "explorer.exe"
        if not match or not exe:
            return False
        return bool(match(exe) or (getattr(match, "name", None) and pid.value in _family(match)[1]))

    while time.time() < end and done < max_times:
        time.sleep(step)
        new = [h for h in find_windows(tile, path) if h not in known]
        known.update(new)
        mine.update(new)
        fg = user32.GetForegroundWindow()
        if fg and fg != start_fg and not belongs(fg):
            return  # anderes Fenster vorne, das nicht zum Programm gehört: Benutzer hat gewechselt
        if new:
            if fg not in mine:
                _foreground(new[0])
                log.info("Hole neues Fenster von %s nach vorne", path)
            done += 1


def _matcher(tile, path):
    """Wie erkennen wir das laufende Programm dieser Kachel? None = gar nicht."""
    proc = tile.get("close_proc", "").strip().strip('"')
    if proc:
        name = ntpath.basename(proc).lower()
        if not name.endswith(".exe"):
            name += ".exe"
        m = lambda exe: ntpath.basename(exe).lower() == name  # noqa: E731
        m.name = name
        return m
    target = path
    if target.lower().endswith(".lnk"):
        target = iconextract.lnk_target(target) or ""
    if not target.lower().endswith(".exe") or ntpath.basename(target).lower() == "explorer.exe":
        return None
    want = os.path.normcase(os.path.normpath(target))
    m = lambda exe: os.path.normcase(os.path.normpath(exe)) == want  # noqa: E731
    m.name = ntpath.basename(target).lower()
    return m


def target_exe(tile, path):
    """Programmdatei einer Kachel (Verknüpfung aufgelöst) oder None."""
    target = iconextract.lnk_target(path) if path.lower().endswith(".lnk") else path
    return target if target and target.lower().endswith(".exe") else None


def find_windows(tile, path):
    """Hauptfenster des laufenden Programms bzw. Explorer-Fenster des Ordners (leer = läuft nicht)."""
    if os.name != "nt":
        return []
    if os.path.isdir(path):
        return _folder(path, "find")
    match = _matcher(tile, path)
    return _app_windows(match) if match else []


ADMIN = "admin"  # Rückgabe: Programm läuft als Administrator, Windows lässt es nicht steuern
TOKEN_QUERY, TOKEN_ELEVATION = 0x0008, 20


def _elevated(pid):
    """True, wenn der Prozess Administratorrechte hat (False auch, wenn nicht feststellbar)."""
    adv = ctypes.windll.advapi32
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    tok = wt.HANDLE()
    try:
        if not adv.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return False
        e, n = wt.DWORD(), wt.DWORD()
        ok = adv.GetTokenInformation(tok, TOKEN_ELEVATION, ctypes.byref(e), 4, ctypes.byref(n))
        kernel32.CloseHandle(tok)
        return bool(ok and e.value)
    finally:
        kernel32.CloseHandle(h)


_self_admin = None


def _blocked(hwnds):
    """Fenster von Programmen mit Administratorrechten kann ein normales Programm nicht minimieren,
    zurückholen oder schließen (Windows-Schutz UIPI). True, wenn das für alle gefundenen Fenster gilt."""
    global _self_admin
    if _self_admin is None:
        _self_admin = _elevated(os.getpid())
    if _self_admin:
        return False
    for h in hwnds:
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if not _elevated(pid.value):
            return False
    return True


def minimize_or_restore(tile, path):
    """Kurzer Tipper bei laufendem Programm: sichtbar -> minimieren, minimiert -> zurückholen.
    False, wenn nichts läuft; ADMIN, wenn das Programm als Administrator läuft (nicht steuerbar)."""
    hwnds = find_windows(tile, path)
    if not hwnds:
        return False
    if _blocked(hwnds):
        log.warning("%s läuft als Administrator – Windows lässt Minimieren/Zurückholen nicht zu", path)
        return ADMIN
    if any(not user32.IsIconic(h) for h in hwnds):
        for h in hwnds:
            user32.ShowWindow(h, SW_MINIMIZE)
        log.info("Minimiere %s (%d Fenster)", path, len(hwnds))
    else:
        for h in hwnds:
            user32.ShowWindow(h, SW_RESTORE)
        _foreground(hwnds[0])
        log.info("Hole %s zurück (%d Fenster)", path, len(hwnds))
    return True


def close_if_running(tile, path):
    """Langer Tipper: höflich schließen. True, wenn etwas zum Schließen gefunden wurde."""
    if os.name != "nt":
        return False
    if os.path.isdir(path):
        n = len(_folder(path, "close"))
        if n:
            log.info("Schließe Ordner %s (%d Fenster)", path, n)
        return n > 0
    hwnds = find_windows(tile, path)
    if hwnds and _blocked(hwnds):
        log.warning("%s läuft als Administrator – Windows lässt Schließen nicht zu", path)
        return ADMIN
    for h in hwnds:
        if user32.IsIconic(h):
            user32.ShowWindow(h, SW_RESTORE)  # sonst bliebe ein Speichern-Dialog unsichtbar
    if hwnds:
        _foreground(hwnds[0])
    for h in hwnds:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    if hwnds:
        log.info("Schließe %s (%d Fenster)", path, len(hwnds))
    return bool(hwnds)


# ----------------------------------------------------------------- Vordergrund (Etappe E)
if os.name == "nt":
    user32.GetForegroundWindow.restype = wt.HWND


DESKTOP_READOBJECTS, UOI_NAME = 0x0001, 2


WTS_SESSION_INFO_EX, WTS_SESSIONSTATE_LOCK = 25, 0


class _WTSINFOEX_LEVEL1(ctypes.Structure):
    _fields_ = [("SessionId", wt.ULONG), ("SessionState", ctypes.c_int), ("SessionFlags", wt.LONG)]


def _session_locked():
    """Sitzungsstatus von Windows: True = gesperrt, False = entsperrt, None = unbekannt."""
    wts = ctypes.windll.wtsapi32
    buf, n = ctypes.c_void_p(), wt.DWORD()
    if not wts.WTSQuerySessionInformationW(None, wt.DWORD(0xFFFFFFFF), WTS_SESSION_INFO_EX,
                                           ctypes.byref(buf), ctypes.byref(n)) or not buf.value:
        return None
    try:
        if ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] != 1:  # nur Level 1 bekannt
            return None
        info = ctypes.cast(buf.value + 8, ctypes.POINTER(_WTSINFOEX_LEVEL1))[0]  # Level + Ausrichtung
        return info.SessionFlags == WTS_SESSIONSTATE_LOCK
    finally:
        wts.WTSFreeMemory(buf)


def is_locked():
    """True, wenn Windows gesperrt ist (Sperr-/Anmeldebildschirm). Zuerst der Sitzungsstatus von
    Windows (erkennt auch den Sperrbildschirm von Windows 10/11), dann der Eingabe-Desktop.
    Kostet nur Mikrosekunden – wird bei jedem Tipper geprüft."""
    if os.name != "nt":
        return False
    try:
        if _session_locked():
            return True
    except OSError:
        pass
    h = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not h:
        return True  # Eingabe-Desktop nicht zugänglich (z. B. Winlogon) = gesperrt
    try:
        buf = ctypes.create_unicode_buffer(64)
        n = wt.DWORD()
        if not user32.GetUserObjectInformationW(h, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(n)):
            return True
        return buf.value.lower() != "default"
    finally:
        user32.CloseDesktop(h)


def foreground_app():
    """(Programmname klein, z. B. "lightburn.exe", Prozess-ID) des Vordergrundfensters oder (None, None)."""
    if os.name != "nt":
        return None, None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = _exe_of(pid.value, {})
    return (ntpath.basename(exe).lower() if exe else None), pid.value


def running_apps():
    """Programmnamen aller Programme mit sichtbarem Hauptfenster (für die Auswahl im Dialog)."""
    names = set()
    cache = {}

    def cb(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER)
                and user32.GetWindowTextLengthW(hwnd) > 0):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = _exe_of(pid.value, cache)
            if exe:
                names.add(ntpath.basename(exe))
        return True

    if os.name == "nt":
        user32.EnumWindows(WNDENUMPROC(cb), 0)
    return sorted(names, key=str.lower)


# ----------------------------------------------------------------- Rahmen fürs Vordergrund-Programm
_EXPLORER_PATH_SCRIPT = r"""
foreach ($w in @((New-Object -ComObject Shell.Application).Windows())) {
  try { if ($w.HWND -eq [int64]$env:PCDECK_HWND) { $w.Document.Folder.Self.Path; break } } catch {}
}
"""
_fg_lock = threading.Lock()
_fg_cache = {"t": 0.0, "info": (None, None, ""), "folder_hwnd": None, "folder": None}


def foreground_info():
    """(hwnd, pid, voller Programmpfad) des Vordergrundfensters, höchstens alle 0,5 s neu ermittelt."""
    with _fg_lock:
        if time.monotonic() - _fg_cache["t"] < 0.5:
            return _fg_cache["info"]
        info = (None, None, "")
        if os.name == "nt":
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                info = (hwnd, pid.value, _exe_of(pid.value, {}))
        _fg_cache["t"], _fg_cache["info"] = time.monotonic(), info
        return info


def foreground_folder():
    """Ordner des Explorer-Fensters im Vordergrund (oder None). Nur neu gefragt, wenn sich das
    Fenster geändert hat bzw. nach 1,5 s (Ordnerwechsel im selben Fenster)."""
    hwnd, _, exe = foreground_info()
    if not hwnd or ntpath.basename(exe).lower() != "explorer.exe":
        return None
    with _fg_lock:
        if _fg_cache["folder_hwnd"] == hwnd and time.monotonic() - _fg_cache.get("folder_t", 0) < 1.5:
            return _fg_cache["folder"]
    folder = None
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _EXPLORER_PATH_SCRIPT], capture_output=True,
                             timeout=10, env=dict(os.environ, PCDECK_HWND=str(hwnd)),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        folder = out.decode("utf-8", "ignore").strip() or None
    except Exception:
        pass
    with _fg_lock:
        _fg_cache.update(folder_hwnd=hwnd, folder=folder, folder_t=time.monotonic())
    return folder


def is_foreground(tile, path):
    """Ist das Programm bzw. der Ordner dieser Kachel gerade im Vordergrund?"""
    if os.path.isdir(path):
        f = foreground_folder()
        return bool(f) and os.path.normcase(os.path.normpath(f)) == os.path.normcase(os.path.normpath(path))
    _, pid, exe = foreground_info()
    match = _matcher(tile, path)
    if not (exe and match):
        return False
    return match(exe) or pid in _family(match)[1]  # auch Fenster von Unterprozessen (Steam)
