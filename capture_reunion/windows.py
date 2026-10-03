"""Liste des fenêtres (Zoom, Teams, Chrome, PowerPoint…) et des écrans capturables,
via l'API Win32."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# Applications de visio / navigateurs proposés en tête de liste.
PREFERRED = {
    "zoom.exe": "Zoom",
    "ms-teams.exe": "Teams",
    "teams.exe": "Teams",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "webex.exe": "Webex",
    "ciscocollabhost.exe": "Webex",
    "powerpnt.exe": "PowerPoint",
}

# Applications de visio : quand c'est vous qui partagez votre écran, elles
# n'affichent plus ce que vous partagez dans leur propre fenêtre.
VISIO = {"zoom.exe", "ms-teams.exe", "teams.exe", "webex.exe", "ciscocollabhost.exe"}


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process: str

    @property
    def label(self) -> str:
        app = PREFERRED.get(self.process.lower())
        prefix = f"[{app}] " if app else ""
        return f"{prefix}{self.title}"

    @property
    def preferred(self) -> bool:
        return self.process.lower() in PREFERRED

    @property
    def is_visio(self) -> bool:
        return self.process.lower() in VISIO


@dataclass
class MonitorInfo:
    index: int
    """Numéro de l'écran à partir de 1, dans l'ordre de Windows (celui de windows-capture)."""
    width: int
    height: int
    primary: bool

    @property
    def title(self) -> str:
        return f"Écran {self.index}"

    @property
    def label(self) -> str:
        primary = "principal, " if self.primary else ""
        return f"[Écran entier] Écran {self.index} ({primary}{self.width}×{self.height})"


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]

    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    GW_OWNER = 4
    DWMWA_CLOAKED = 14
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _title(hwnd) -> str:
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _process_name(hwnd) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name
            return ""
        finally:
            kernel32.CloseHandle(handle)

    def _cloaked(hwnd) -> bool:
        value = ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
        return value.value != 0

    def list_windows() -> list[WindowInfo]:
        own_pid = kernel32.GetCurrentProcessId()
        result: list[WindowInfo] = []

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            title = _title(hwnd)
            if not title or _cloaked(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True
            result.append(WindowInfo(int(hwnd), title, _process_name(hwnd)))
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        result.sort(key=lambda w: (not w.preferred, w.label.lower()))
        return result

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MonitorEnumProc, wintypes.LPARAM]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    MONITORINFOF_PRIMARY = 1

    def list_monitors() -> list[MonitorInfo]:
        result: list[MonitorInfo] = []

        def callback(hmonitor, _hdc, _rect, _lparam):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(hmonitor, ctypes.byref(info))
            r = info.rcMonitor
            result.append(MonitorInfo(len(result) + 1, r.right - r.left, r.bottom - r.top,
                                      bool(info.dwFlags & MONITORINFOF_PRIMARY)))
            return True

        user32.EnumDisplayMonitors(None, None, MonitorEnumProc(callback), 0)
        return result

    def window_exists(hwnd: int) -> bool:
        return bool(user32.IsWindow(hwnd))

    def is_minimized(hwnd: int) -> bool:
        return bool(user32.IsIconic(hwnd))

    def window_title(hwnd: int) -> str:
        return _title(hwnd)

else:  # Permet d'importer le module (tests, CLI d'extraction) hors Windows.

    def list_windows() -> list[WindowInfo]:
        return []

    def list_monitors() -> list[MonitorInfo]:
        return []

    def window_exists(hwnd: int) -> bool:
        return False

    def is_minimized(hwnd: int) -> bool:
        return False

    def window_title(hwnd: int) -> str:
        return ""
