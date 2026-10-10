"""Empêche la mise en veille de Windows et l'extinction de l'écran pendant
l'enregistrement (sinon la capture et le son s'arrêtent).

Windows rattache cette demande au fil d'exécution qui la fait : un fil dédié
la maintient du début à la fin, quel que soit le fil qui démarre ou arrête
l'enregistrement.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable, Optional

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
REFRESH_SECONDS = 30


def _windows_setter() -> Optional[Callable[[int], int]]:
    if sys.platform != "win32":
        return None
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint
    return kernel32.SetThreadExecutionState


class KeepAwake:
    """``start()`` : plus de mise en veille ni d'écran éteint ; ``stop()`` : retour à la normale."""

    def __init__(self, keep_display: bool = True, setter: Optional[Callable[[int], int]] = None) -> None:
        self.flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | (ES_DISPLAY_REQUIRED if keep_display else 0)
        self._setter = setter if setter is not None else _windows_setter()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.active = False

    @property
    def supported(self) -> bool:
        return self._setter is not None

    def start(self) -> None:
        if not self.supported or self._thread is not None:
            return
        ready = threading.Event()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self._thread.start()
        ready.wait(2)

    def _run(self, ready: threading.Event) -> None:
        self.active = bool(self._setter(self.flags))
        ready.set()
        # Rafraîchi régulièrement par sécurité (certains pilotes réinitialisent l'état).
        while not self._stop.wait(REFRESH_SECONDS):
            self._setter(self.flags)
        self._setter(ES_CONTINUOUS)  # libère : Windows reprend ses réglages habituels
        self.active = False

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=5)
        self._thread = None

    def __enter__(self) -> "KeepAwake":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
