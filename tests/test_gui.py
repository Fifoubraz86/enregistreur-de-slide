import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from capture_reunion import gui  # noqa: E402
from capture_reunion.windows import MonitorInfo, WindowInfo  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_targets_order_and_zoom_warning(app, monkeypatch):
    windows = [
        WindowInfo(10, "Bloc-notes", "notepad.exe"),
        WindowInfo(11, "Réunion Zoom", "Zoom.exe"),
        WindowInfo(12, "Diaporama PowerPoint - congres.pptx", "POWERPNT.EXE"),
    ]
    windows.sort(key=lambda w: (not w.preferred, w.label.lower()))
    monkeypatch.setattr(gui, "list_windows", lambda: windows)
    monkeypatch.setattr(gui, "list_monitors", lambda: [MonitorInfo(1, 1920, 1080, True),
                                                       MonitorInfo(2, 2560, 1440, False)])
    w = gui.MainWindow()
    labels = [w.window_combo.itemText(i) for i in range(w.window_combo.count())]
    assert labels[0].startswith("[PowerPoint]") and labels[1].startswith("[Zoom]")
    assert labels[2].startswith("[Écran entier] Écran 1 (principal")
    assert labels[-1] == "Bloc-notes"

    w.window_combo.setCurrentIndex(1)  # Zoom
    assert not w.visio_warning.isHidden()
    w.window_combo.setCurrentIndex(2)  # écran entier
    assert w.visio_warning.isHidden()
    assert w._capture_args(w.window_combo.currentData()) == (None, 1)
    assert w._capture_args(windows[0]) == (12, None)
