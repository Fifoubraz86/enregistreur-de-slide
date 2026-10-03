"""Chaîne d'enregistrement complète (encodage ffmpeg, diapos, session) avec une
fausse fenêtre, puisque la capture Windows n'existe pas ici."""

import time

import cv2
import numpy as np
import pytest

from capture_reunion import recorder as rec_mod
from capture_reunion.ffmpeg_utils import find_ffmpeg
from capture_reunion.session import Session
from capture_reunion.slides import DetectorSettings
from conftest import make_slide


class FakeSource:
    """Affiche la diapo 1 puis la diapo 2 après 2,5 s."""

    def __init__(self, hwnd, on_closed=None, monitor_index=None):
        self.closed = False
        self._t0 = None

    def start(self):
        self._t0 = time.monotonic()

    def latest(self):
        n = 1 if time.monotonic() - self._t0 < 2.5 else 2
        return cv2.cvtColor(make_slide(n), cv2.COLOR_BGR2BGRA)

    def stop(self):
        pass


@pytest.fixture
def fast_settings():
    return DetectorSettings(sample_interval=0.25, stable_duration=0.75)


def test_recording_pipeline(tmp_path, monkeypatch, fast_settings):
    try:
        find_ffmpeg()
    except FileNotFoundError:
        pytest.skip("ffmpeg absent")
    monkeypatch.setattr(rec_mod, "_WindowSource", FakeSource)
    slides = []
    r = rec_mod.Recorder(1, tmp_path, window_title="Réunion: test/Zoom", fps=10,
                         record_audio=False, detector_settings=fast_settings,
                         on_slide=lambda s: slides.append(s.index))
    r.start()
    time.sleep(5)
    session = r.stop()

    assert session.folder.parent == tmp_path
    assert "/" not in session.folder.name.split(" - ", 1)[1]
    assert slides == [1, 2]
    cap = cv2.VideoCapture(str(session.video_path))
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    assert cap.get(cv2.CAP_PROP_FRAME_WIDTH) == 640
    cap.release()
    assert 40 <= frames <= 55  # ~5 s à 10 images/s
    loaded = Session.load(session.folder)
    assert len(loaded.slides) == 2 and loaded.video == "video.mp4"
    assert not (session.folder / "ffmpeg.log").exists()


def test_mux_with_audio_delay(tmp_path):
    """Le son démarré 1 s avant l'image est recalé à l'assemblage."""
    import wave

    from capture_reunion import ffmpeg_utils

    try:
        find_ffmpeg()
    except FileNotFoundError:
        pytest.skip("ffmpeg absent")
    wav = tmp_path / "son.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.zeros(48000 * 4 * 2, np.int16).tobytes())  # 4 s
    video = tmp_path / "v.mp4"
    with open(tmp_path / "log", "wb") as log:
        enc = ffmpeg_utils.open_video_encoder(video, 320, 180, 10, log)
        enc.stdin.write(np.zeros((30, 180, 320, 4), np.uint8).tobytes())  # 3 s
        enc.stdin.close()
        assert enc.wait() == 0
    out = tmp_path / "out.mp4"
    ffmpeg_utils.mux(video, wav, out, audio_delay=1.0)
    ffmpeg_utils.to_mp3(wav, tmp_path / "son.mp3", delay=1.0)
    assert out.stat().st_size > 0 and (tmp_path / "son.mp3").stat().st_size > 0


def test_monitor_target_is_passed_to_capture(tmp_path, monkeypatch):
    seen = {}

    class Spy(FakeSource):
        def __init__(self, hwnd, on_closed=None, monitor_index=None):
            super().__init__(hwnd, on_closed)
            seen.update(hwnd=hwnd, monitor_index=monitor_index)

    monkeypatch.setattr(rec_mod, "_WindowSource", Spy)
    r = rec_mod.Recorder(None, tmp_path, window_title="Écran 1", monitor_index=1,
                         record_audio=False, detect_slides=False)
    assert seen == {"hwnd": None, "monitor_index": 1}
    assert r.session.window_title == "Écran 1"
