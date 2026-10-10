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


class StubRecorder:
    hwnd = None
    detector = None
    slide_count = 0
    audio_warning = ""
    auto_stop_after = 180

    def __init__(self, idle, stop_now=False, silent=True):
        self.idle_seconds, self.should_auto_stop, self.elapsed = idle, stop_now, 95
        self._silent = silent

    def audio_levels(self):
        return {"participants": {"level": 0.0, "silent_for": None if self._silent else 2},
                "moi": {"level": 0.05, "silent_for": 1}}


def test_tick_shows_levels_idle_and_silence_warning(app, monkeypatch):
    w = gui.MainWindow()
    w.recorder = StubRecorder(idle=80)
    w._tick()
    text = w.status_label.text()
    assert "Son de l'ordinateur □□□□□□□□" in text and "Micro ■■■■■" in text
    assert "Aucune activité depuis 00:01:20 — arrêt automatique à 00:03:00" in text
    assert "Aucun son de l'ordinateur reçu" in w.warning_label.text()


def test_tick_triggers_auto_stop(app, monkeypatch):
    w = gui.MainWindow()
    calls = []
    monkeypatch.setattr(w, "stop_recording", lambda auto=False: calls.append(auto))
    w.recorder = StubRecorder(idle=200, stop_now=True)
    w._tick()
    assert calls == [True] and w._last_auto_stop


def test_level_bar():
    assert gui.level_bar(0) == "□" * 8
    assert gui.level_bar(1.0) == "■" * 8
    assert gui.level_bar(0.03).count("■") == 4  # environ -30 dB


def test_engine_choice_and_patient_switch(app):
    w = gui.MainWindow()
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("claude"))
    assert w.claude_model_combo.isEnabled() and not w.llm_url.isEnabled()
    assert "Anthropic" in w.llm_status.text()
    w.patient_check.setChecked(True)
    assert not w.claude_model_combo.isEnabled()
    assert w._engine_choice() == ("claude", "sonnet", False)
    w.patient_check.setChecked(False)
    w.engine_combo.setCurrentIndex(w.engine_combo.findData("lmstudio"))
    assert w.llm_url.isEnabled() and not w.claude_model_combo.isEnabled()
