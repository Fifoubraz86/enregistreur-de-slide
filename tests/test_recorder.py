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


def test_recording_with_two_audio_tracks(tmp_path, monkeypatch, fast_settings):
    """Son de l'ordinateur + micro : vidéo avec son mélangé, son.mp3 et pistes séparées."""
    import wave

    from capture_reunion import audio as audio_mod

    try:
        find_ffmpeg()
    except FileNotFoundError:
        pytest.skip("ffmpeg absent")

    class FakeAudio:
        def __init__(self, path, mode="loopback"):
            self.path, self.mode, self.start_time = path, mode, None

        def start(self):
            self.start_time = time.monotonic() - 0.5  # démarré un peu avant l'image

        def stop(self):
            rate, channels = 48000, 2 if self.mode == "loopback" else 1
            n = int((time.monotonic() - self.start_time) * rate)
            t = np.arange(n) / rate
            tone = (8000 * np.sin(2 * np.pi * (440 if channels == 2 else 220) * t)).astype(np.int16)
            with wave.open(str(self.path), "wb") as w:
                w.setnchannels(channels)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(np.repeat(tone, channels).tobytes())

    monkeypatch.setattr(rec_mod, "_WindowSource", FakeSource)
    monkeypatch.setattr(audio_mod, "AudioRecorder", FakeAudio)
    r = rec_mod.Recorder(1, tmp_path, window_title="Zoom", fps=10, record_audio=True,
                         record_mic=True, detector_settings=fast_settings)
    r.start()
    time.sleep(3)
    session = r.stop()

    assert session.audio == "son.mp3" and (session.folder / "son.mp3").exists()
    assert session.tracks == {"participants": "piste_participants.m4a", "moi": "piste_moi.m4a"}
    assert all((session.folder / f).exists() for f in session.tracks.values())
    assert not list(session.folder.glob("*_temp.wav")) and not list(session.folder.glob("*temp*"))
    assert Session.load(session.folder).tracks == session.tracks


def test_image_activity_ignores_clock_but_sees_slide_change():
    slide = cv2.cvtColor(make_slide(1), cv2.COLOR_BGR2BGRA)
    small, active = rec_mod.image_activity(None, slide)
    assert not active
    clock = slide.copy()
    cv2.putText(clock, "14:32", (580, 350), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0, 255), 1)
    _, active = rec_mod.image_activity(small, clock)
    assert not active  # une horloge qui change n'est pas une activité
    _, active = rec_mod.image_activity(small, cv2.cvtColor(make_slide(2), cv2.COLOR_BGR2BGRA))
    assert active


def test_audio_level_measure():
    from capture_reunion.audio import AudioRecorder

    rec = AudioRecorder("x.wav", "loopback")
    rec._measure(np.zeros(4800, np.int16).tobytes(), 10.0)
    assert rec.level == 0 and rec.last_active is None
    tone = (3000 * np.sin(np.arange(4800) / 10)).astype(np.int16)
    rec._measure(tone.tobytes(), 11.0)
    assert rec.level > 0.05 and rec.last_active == 11.0


def test_auto_stop_after_inactivity_trims_dead_end(tmp_path, monkeypatch, fast_settings):
    try:
        find_ffmpeg()
    except FileNotFoundError:
        pytest.skip("ffmpeg absent")
    monkeypatch.setattr(rec_mod, "_WindowSource", FakeSource)
    monkeypatch.setattr(rec_mod, "ACTIVITY_INTERVAL", 0.2)
    r = rec_mod.Recorder(1, tmp_path, fps=10, record_audio=False, detector_settings=fast_settings,
                         auto_stop_after=3.0, idle_margin=0.5)
    r.start()
    deadline = time.monotonic() + 15
    while not r.should_auto_stop and time.monotonic() < deadline:
        time.sleep(0.1)
    assert r.should_auto_stop
    # Dernière activité : passage à la diapo 2 vers 2,5 s ; arrêt ~3 s plus tard.
    assert 5.0 <= r.elapsed <= 8
    time.sleep(1)  # la fin morte s'allonge encore un peu avant l'arrêt
    session = r.stop(auto=True)
    assert r.auto_stopped
    assert 2.5 <= session.duration <= 4.0  # coupé à dernière activité + 0,5 s
    cap = cv2.VideoCapture(str(session.video_path))
    seconds = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    assert abs(seconds - session.duration) < 1.0
    assert Session.load(session.folder).duration == session.duration


def test_no_auto_stop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mod, "_WindowSource", FakeSource)
    r = rec_mod.Recorder(1, tmp_path, record_audio=False, detect_slides=False)
    r._t0 = time.monotonic() - 3600
    assert r.idle_seconds > 3000 and not r.should_auto_stop
