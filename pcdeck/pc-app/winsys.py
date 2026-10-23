"""Systemwerte direkt aus Windows (ctypes, ohne Zusatzpakete): CPU, RAM, Lautstärke."""

import ctypes
import ctypes.wintypes as wt
import threading

kernel32 = ctypes.windll.kernel32


# ----------------------------------------------------------------- CPU
class FILETIME(ctypes.Structure):
    _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]

    def value(self):
        return (self.hi << 32) | self.lo


_cpu_lock = threading.Lock()
_cpu_last = None      # (Zeitpunkt, idle, kernel, user)
_cpu_value = 0


def cpu_percent():
    """CPU-Auslastung in % seit der letzten Messung (frühestens alle 0,5 s neu gemessen,
    damit mehrere CPU-Kacheln sich nicht gegenseitig die Messung verkürzen)."""
    global _cpu_last, _cpu_value
    import time
    with _cpu_lock:
        now_t = time.monotonic()
        if _cpu_last and now_t - _cpu_last[0] < 0.5:
            return _cpu_value
        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        now = (now_t, idle.value(), kern.value(), user.value())
        last, _cpu_last = _cpu_last, now
        if last:
            d_idle = now[1] - last[1]
            d_total = (now[2] - last[2]) + (now[3] - last[3])  # Kernelzeit enthält die Leerlaufzeit
            if d_total > 0:
                _cpu_value = max(0, min(100, round(100 * (d_total - d_idle) / d_total)))
        return _cpu_value


# ----------------------------------------------------------------- RAM
class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram():
    """(Auslastung %, belegt GB, gesamt GB)."""
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    gb = 1024 ** 3
    return m.dwMemoryLoad, (m.ullTotalPhys - m.ullAvailPhys) / gb, m.ullTotalPhys / gb


# ----------------------------------------------------------------- Lautstärke
# (Umsetzung in winaudio.py, hier nur weitergereicht)
from winaudio import change_volume, toggle_mute, volume  # noqa: E402,F401
