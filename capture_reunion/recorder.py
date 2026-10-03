"""Enregistrement d'une fenêtre précise (Windows Graphics Capture), même cachée
derrière d'autres fenêtres, avec le son de l'ordinateur et la détection des diapos
en direct."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from . import ffmpeg_utils
from .session import Session, new_session_folder
from .slides import DetectorSettings, Slide, SlideDetector

FIRST_FRAME_TIMEOUT = 4.0


class CaptureError(RuntimeError):
    pass


class _WindowSource:
    """Reçoit les images de la fenêtre et garde toujours la plus récente.

    Windows n'envoie une image que lorsque le contenu change : c'est
    l'enregistreur qui répète la dernière image pour obtenir une vidéo fluide.
    """

    def __init__(self, hwnd: int, on_closed: Optional[Callable[[], None]] = None) -> None:
        self.hwnd = hwnd
        self.on_closed = on_closed
        self.closed = False
        self._lock = threading.Lock()
        self._latest: Optional[np.ndarray] = None
        self._first = threading.Event()
        self._control = None
        self._stopping = False

    def latest(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._latest

    def start(self) -> None:
        try:
            from windows_capture import WindowsCapture
        except ImportError as exc:
            raise CaptureError(
                "Le module windows-capture est absent (pip install windows-capture). "
                "La capture ne fonctionne que sous Windows 10/11."
            ) from exc

        last_error: Optional[Exception] = None
        # Sans curseur ni bordure jaune si Windows le permet, sinon réglages par défaut.
        for options in ({"cursor_capture": False, "draw_border": False}, {}):
            self._first.clear()
            try:
                capture = WindowsCapture(window_hwnd=self.hwnd, **options)
                capture.frame_handler = self._on_frame
                capture.closed_handler = self._on_closed
                self._control = capture.start_free_threaded()
            except Exception as exc:  # réglage non supporté par cette version de Windows
                last_error = exc
                continue
            if self._first.wait(FIRST_FRAME_TIMEOUT):
                return
            self._stop_control()
        raise CaptureError(
            "Aucune image reçue de la fenêtre. Vérifiez qu'elle n'est pas réduite dans la "
            "barre des tâches (elle peut être cachée derrière d'autres fenêtres)."
            + (f"\nDétail : {last_error}" if last_error else "")
        )

    def _on_frame(self, frame, control) -> None:
        if self._stopping:
            control.stop()
            return
        image = frame.frame_buffer.copy()
        with self._lock:
            self._latest = image
        self._first.set()

    def _on_closed(self) -> None:
        self.closed = True
        if not self._stopping and self.on_closed:
            self.on_closed()

    def _stop_control(self) -> None:
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:
                pass
            self._control = None

    def stop(self) -> None:
        self._stopping = True
        self._stop_control()


def grab_snapshot(hwnd: int) -> np.ndarray:
    """Une image (BGR) de la fenêtre, par exemple pour choisir la zone des diapos."""
    source = _WindowSource(hwnd)
    source.start()
    image = source.latest()
    source.stop()
    return np.ascontiguousarray(image[:, :, :3])


class Recorder:
    def __init__(
        self,
        hwnd: int,
        base_dir: Path,
        window_title: str = "",
        fps: int = 15,
        record_audio: bool = True,
        detect_slides: bool = True,
        detector_settings: Optional[DetectorSettings] = None,
        max_height: int = 1080,
        on_slide: Optional[Callable[[Slide], None]] = None,
        on_slide_updated: Optional[Callable[[Slide], None]] = None,
        on_window_closed: Optional[Callable[[], None]] = None,
    ) -> None:
        self.hwnd = hwnd
        self.fps = max(1, int(fps))
        self.record_audio = record_audio
        self.max_height = max_height
        self.detector_settings = detector_settings or DetectorSettings()
        self.started = datetime.now()
        self.session = Session(
            folder=new_session_folder(base_dir, window_title or "Reunion", self.started),
            started_at=self.started.isoformat(timespec="seconds"),
            window_title=window_title,
            zone=self.detector_settings.zone,
        )
        self.detector = (
            SlideDetector(
                self.detector_settings,
                output_dir=self.session.slides_dir,
                on_slide=on_slide,
                on_slide_updated=on_slide_updated,
            )
            if detect_slides
            else None
        )
        self.audio_warning = ""
        self._source = _WindowSource(hwnd, on_closed=on_window_closed)
        self._audio = None
        self._encoder = None
        self._log = None
        self._t0 = 0.0
        self._size = (0, 0)
        self._frames_written = 0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._encoder_error = ""

    @property
    def folder(self) -> Path:
        return self.session.folder

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0 if self._t0 else 0.0

    @property
    def window_closed(self) -> bool:
        return self._source.closed

    @property
    def slide_count(self) -> int:
        return len(self.detector.slides) if self.detector else 0

    # -- démarrage -------------------------------------------------------
    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.record_audio:
            from .audio import LoopbackRecorder

            self._audio = LoopbackRecorder(self.folder / "son_temp.wav")
            try:
                self._audio.start()
            except Exception as exc:
                self.audio_warning = f"Son de l'ordinateur non enregistré : {exc}"
                self._audio = None

        try:
            self._source.start()
        except Exception:
            if self._audio:
                self._audio.stop()
                self._audio.path.unlink(missing_ok=True)
            try:
                self.folder.rmdir()  # ne supprime le dossier que s'il est vide
            except OSError:
                pass
            raise

        first = self._source.latest()
        h, w = first.shape[:2]
        if h > self.max_height:
            w, h = int(w * self.max_height / h), self.max_height
        self._size = (w - w % 2, h - h % 2)
        self._log = open(self.folder / "ffmpeg.log", "wb")
        self._encoder = ffmpeg_utils.open_video_encoder(
            self.folder / "video_temp.mp4", self._size[0], self._size[1], self.fps, self._log
        )
        self._t0 = time.monotonic()
        self._threads = [threading.Thread(target=self._write_loop, daemon=True)]
        if self.detector:
            self._threads.append(threading.Thread(target=self._detect_loop, daemon=True))
        for t in self._threads:
            t.start()

    # -- boucles ---------------------------------------------------------
    def _write_loop(self) -> None:
        interval = 1.0 / self.fps
        w, h = self._size
        while not self._stop.is_set():
            target = self._t0 + self._frames_written * interval
            delay = target - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
                continue
            frame = self._source.latest()
            if frame.shape[1] != w or frame.shape[0] != h:
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
            try:
                self._encoder.stdin.write(memoryview(np.ascontiguousarray(frame)))
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._encoder_error = f"L'encodage vidéo s'est arrêté : {exc}"
                break
            self._frames_written += 1

    def _detect_loop(self) -> None:
        interval = self.detector_settings.sample_interval
        while not self._stop.wait(interval):
            frame = self._source.latest()
            try:
                self.detector.feed(time.monotonic() - self._t0, frame)
            except Exception:
                # Une erreur d'analyse ne doit jamais interrompre l'enregistrement.
                continue

    # -- arrêt -----------------------------------------------------------
    def stop(self) -> Session:
        """Arrête l'enregistrement, finalise les fichiers et renvoie la session."""
        stop_time = time.monotonic()
        self._stop.set()
        self._source.stop()
        for t in self._threads:
            t.join(timeout=10)
        if self._audio:
            self._audio.stop()

        problems: list[str] = []
        if self._encoder_error:
            problems.append(self._encoder_error)
        if self._encoder:
            try:
                self._encoder.stdin.close()
            except OSError:
                pass
            self._encoder.wait(timeout=120)
            if self._encoder.returncode != 0:
                problems.append("ffmpeg a signalé une erreur (voir ffmpeg.log).")
        if self._log:
            self._log.close()

        self.session.duration = round(min(stop_time - self._t0, self._frames_written / self.fps), 2)
        raw_video = self.folder / "video_temp.mp4"
        video = self.folder / "video.mp4"
        try:
            if self._audio and self._audio.path.exists():
                delay = max(0.0, self._t0 - self._audio.start_time)
                ffmpeg_utils.mux(raw_video, self._audio.path, video, audio_delay=delay)
                ffmpeg_utils.to_mp3(self._audio.path, self.folder / "son.mp3", delay=delay)
                self.session.audio = "son.mp3"
                raw_video.unlink()
                self._audio.path.unlink()
            else:
                raw_video.replace(video)
            self.session.video = "video.mp4"
        except Exception as exc:
            problems.append(f"Assemblage vidéo/son impossible ({exc}). Fichiers bruts conservés.")
            self.session.video = raw_video.name if raw_video.exists() else None

        if self.detector:
            self.session.set_slides_from(self.detector)
        self.session.save()
        log = self.folder / "ffmpeg.log"
        if log.exists() and log.stat().st_size == 0:
            log.unlink()
        if problems:
            raise CaptureError("\n".join(problems))
        return self.session

