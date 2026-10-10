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
ACTIVITY_INTERVAL = 2.0  # secondes entre deux vérifications d'activité de l'image
IMAGE_CHANGE_RATIO = 0.003  # part des pixels qui doit changer pour parler d'activité


def image_activity(previous: Optional[np.ndarray], frame: np.ndarray) -> tuple[np.ndarray, bool]:
    """Image réduite en gris, et vrai si elle a nettement changé depuis ``previous``.

    Une horloge qui avance ou un curseur qui clignote ne suffisent pas : il faut
    qu'au moins 0,3 % de l'image change (diapo, vidéo, personne qui bouge).
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    small = cv2.resize(gray, (240, max(1, int(h * 240 / w))), interpolation=cv2.INTER_AREA)
    if previous is None or previous.shape != small.shape:
        return small, False
    changed = float((cv2.absdiff(small, previous) > 25).mean())
    return small, changed >= IMAGE_CHANGE_RATIO


class CaptureError(RuntimeError):
    pass


class _WindowSource:
    """Reçoit les images de la fenêtre (ou de l'écran entier) et garde la plus récente.

    Windows n'envoie une image que lorsque le contenu change : c'est
    l'enregistreur qui répète la dernière image pour obtenir une vidéo fluide.
    """

    def __init__(
        self,
        hwnd: Optional[int],
        on_closed: Optional[Callable[[], None]] = None,
        monitor_index: Optional[int] = None,
    ) -> None:
        self.hwnd = hwnd
        self.monitor_index = monitor_index
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

        target = (
            {"monitor_index": self.monitor_index}
            if self.monitor_index is not None
            else {"window_hwnd": self.hwnd}
        )
        last_error: Optional[Exception] = None
        # Sans curseur ni bordure jaune si Windows le permet, sinon réglages par défaut.
        for options in ({"cursor_capture": False, "draw_border": False}, {}):
            self._first.clear()
            try:
                capture = WindowsCapture(**target, **options)
                capture.frame_handler = self._on_frame
                capture.closed_handler = self._on_closed
                self._control = capture.start_free_threaded()
            except Exception as exc:  # réglage non supporté par cette version de Windows
                last_error = exc
                continue
            if self._first.wait(FIRST_FRAME_TIMEOUT):
                return
            self._stop_control()
        if self.monitor_index is not None:
            raise CaptureError(
                f"Aucune image reçue de l'écran {self.monitor_index}."
                + (f"\nDétail : {last_error}" if last_error else "")
            )
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


def grab_snapshot(hwnd: Optional[int], monitor_index: Optional[int] = None) -> np.ndarray:
    """Une image (BGR) de la fenêtre ou de l'écran, par exemple pour choisir la zone des diapos."""
    source = _WindowSource(hwnd, monitor_index=monitor_index)
    source.start()
    image = source.latest()
    source.stop()
    return np.ascontiguousarray(image[:, :, :3])


class Recorder:
    def __init__(
        self,
        hwnd: Optional[int],
        base_dir: Path,
        window_title: str = "",
        fps: int = 15,
        record_audio: bool = True,
        record_mic: bool = False,
        detect_slides: bool = True,
        detector_settings: Optional[DetectorSettings] = None,
        max_height: int = 1080,
        on_slide: Optional[Callable[[Slide], None]] = None,
        on_slide_updated: Optional[Callable[[Slide], None]] = None,
        on_window_closed: Optional[Callable[[], None]] = None,
        monitor_index: Optional[int] = None,
        auto_stop_after: Optional[float] = None,
        idle_margin: float = 10.0,
    ) -> None:
        """``hwnd`` : fenêtre à enregistrer, ou ``monitor_index`` (1, 2…) pour un écran entier.

        ``auto_stop_after`` : secondes sans activité (ni image qui change, ni son) au bout
        desquelles ``should_auto_stop`` devient vrai ; la fin morte est alors coupée en ne
        gardant que ``idle_margin`` secondes après la dernière activité.
        """
        self.hwnd = hwnd
        self.monitor_index = monitor_index
        self.fps = max(1, int(fps))
        self.record_audio = record_audio
        self.record_mic = record_mic
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
        self._source = _WindowSource(hwnd, on_closed=on_window_closed, monitor_index=monitor_index)
        self._audio_tracks: list[tuple[str, object]] = []  # (piste, AudioRecorder)
        self._encoder = None
        self._log = None
        self._t0 = 0.0
        self._size = (0, 0)
        self._frames_written = 0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._encoder_error = ""
        self.auto_stop_after = auto_stop_after
        self.idle_margin = idle_margin
        self.auto_stopped = False
        self._last_image_activity: Optional[float] = None
        from .power import KeepAwake

        self._keep_awake = KeepAwake()

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

    # -- activité --------------------------------------------------------
    @property
    def last_activity(self) -> float:
        """Dernier instant (time.monotonic) où l'image a changé ou du son est arrivé."""
        times = [self._t0]
        if self._last_image_activity is not None:
            times.append(self._last_image_activity)
        times += [a.last_active for _, a in self._audio_tracks if getattr(a, "last_active", None)]
        return max(times)

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity if self._t0 else 0.0

    @property
    def should_auto_stop(self) -> bool:
        return bool(self.auto_stop_after) and self.idle_seconds >= self.auto_stop_after

    def audio_levels(self) -> dict[str, dict]:
        """Par piste : niveau actuel (0-1) et secondes depuis le dernier son audible
        (None : rien d'audible depuis le début)."""
        now = time.monotonic()
        out = {}
        for name, audio in self._audio_tracks:
            last = getattr(audio, "last_active", None)
            out[name] = {"level": getattr(audio, "level", 0.0),
                         "silent_for": now - last if last else None}
        return out

    # -- démarrage -------------------------------------------------------
    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        from .audio import AudioRecorder

        warnings = []
        wanted = [("participants", "loopback", self.record_audio, "Son de l'ordinateur"),
                  ("moi", "micro", self.record_mic, "Micro")]
        for track, mode, enabled, name in wanted:
            if not enabled:
                continue
            audio = AudioRecorder(self.folder / f"{track}_temp.wav", mode)
            try:
                audio.start()
                self._audio_tracks.append((track, audio))
            except Exception as exc:
                warnings.append(f"{name} non enregistré : {exc}")
        self.audio_warning = "\n".join(warnings)

        try:
            self._source.start()
        except Exception:
            for _, audio in self._audio_tracks:
                audio.stop()
                audio.path.unlink(missing_ok=True)
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
        self._last_image_activity = self._t0
        self._keep_awake.start()  # pas de mise en veille ni d'écran éteint pendant l'enregistrement
        self._threads = [threading.Thread(target=self._write_loop, daemon=True),
                         threading.Thread(target=self._activity_loop, daemon=True)]
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

    def _activity_loop(self) -> None:
        previous = None
        while not self._stop.wait(ACTIVITY_INTERVAL):
            try:
                previous, active = image_activity(previous, self._source.latest())
            except Exception:
                continue
            if active:
                self._last_image_activity = time.monotonic()

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
    def stop(self, auto: bool = False) -> Session:
        """Arrête l'enregistrement, finalise les fichiers et renvoie la session.

        ``auto`` : arrêt pour inactivité ; la fin sans activité est coupée.
        """
        stop_time = time.monotonic()
        self.auto_stopped = auto
        trim_to = None
        if auto and self._t0:
            trim_to = max(1.0, self.last_activity - self._t0 + self.idle_margin)
        self._stop.set()
        self._keep_awake.stop()
        self._source.stop()
        for t in self._threads:
            t.join(timeout=10)
        for _, audio in self._audio_tracks:
            audio.stop()

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
        tracks = [(name, a) for name, a in self._audio_tracks if a.path.exists()]
        try:
            if tracks:
                # Chaque piste est recalée sur le début de la vidéo.
                inputs = [(a.path, max(0.0, self._t0 - a.start_time)) for _, a in tracks]
                mixed = self.folder / "mix_temp.wav"
                ffmpeg_utils.mix_audio(inputs, mixed)
                ffmpeg_utils.mux(raw_video, mixed, video)
                ffmpeg_utils.to_mp3(mixed, self.folder / "son.mp3")
                self.session.audio = "son.mp3"
                # Pistes séparées (16 kHz mono) : la transcription sait ainsi qui parle.
                for (name, a), (path, delay) in zip(tracks, inputs):
                    out = f"piste_{name}.m4a"
                    ffmpeg_utils.export_track(path, self.folder / out, delay)
                    self.session.tracks[name] = out
                raw_video.unlink()
                mixed.unlink()
                for path, _ in inputs:
                    path.unlink()
            else:
                raw_video.replace(video)
            self.session.video = "video.mp4"
            if trim_to is not None and trim_to < self.session.duration - 1:
                # Arrêt pour inactivité : on retire la fin morte.
                for rel in [self.session.video, self.session.audio, *self.session.tracks.values()]:
                    if rel:
                        ffmpeg_utils.truncate(self.folder / rel, trim_to)
                self.session.duration = round(trim_to, 2)
        except Exception as exc:
            problems.append(f"Assemblage vidéo/son impossible ({exc}). Fichiers bruts conservés.")
            self.session.video = raw_video.name if raw_video.exists() else None

        if self.detector:
            self.session.set_slides_from(self.detector)
            if trim_to is not None:
                self.session.timeline = [(t, i) for t, i in self.session.timeline if t <= trim_to]
        self.session.save()
        log = self.folder / "ffmpeg.log"
        if log.exists() and log.stat().st_size == 0:
            log.unlink()
        if problems:
            raise CaptureError("\n".join(problems))
        return self.session

