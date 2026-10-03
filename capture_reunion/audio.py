"""Enregistrement du son de l'ordinateur (WASAPI loopback) : ce que vous entendez
dans le casque ou les haut-parleurs, donc le son de la réunion."""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Optional


class LoopbackRecorder:
    """Enregistre la sortie audio par défaut dans un fichier WAV.

    WASAPI ne fournit aucune donnée quand rien n'est joué : on comble ces
    silences avec des zéros pour que le son reste synchronisé avec la vidéo.
    """

    GAP_TOLERANCE = 0.2  # secondes

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.start_time: Optional[float] = None
        self.device_name = ""
        self._pa = None
        self._stream = None
        self._wav: Optional[wave.Wave_write] = None
        self._lock = threading.Lock()
        self._written = 0
        self._rate = 48000
        self._channels = 2
        self._frame_bytes = 4

    def start(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:
            raise RuntimeError("Module pyaudiowpatch absent : pip install pyaudiowpatch") from exc

        self._pa = pyaudio.PyAudio()
        try:
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError as exc:
            self._pa.terminate()
            raise RuntimeError("WASAPI indisponible sur ce système.") from exc
        speakers = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not speakers.get("isLoopbackDevice"):
            for loopback in self._pa.get_loopback_device_info_generator():
                if speakers["name"] in loopback["name"]:
                    speakers = loopback
                    break
            else:
                self._pa.terminate()
                raise RuntimeError("Aucun périphérique de capture du son système trouvé.")

        self.device_name = speakers["name"]
        self._rate = int(speakers["defaultSampleRate"])
        self._channels = max(1, int(speakers["maxInputChannels"]))
        self._frame_bytes = 2 * self._channels

        self._wav = wave.open(str(self.path), "wb")
        self._wav.setnchannels(self._channels)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self._rate)

        self.start_time = time.monotonic()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self._channels,
            rate=self._rate,
            input=True,
            input_device_index=speakers["index"],
            frames_per_buffer=int(self._rate * 0.05),
            stream_callback=self._callback,
        )
        self._stream.start_stream()

    def _pad_until(self, now: float, upcoming: int = 0) -> None:
        expected = int((now - self.start_time) * self._rate)
        missing = expected - (self._written + upcoming)
        if missing > self._rate * self.GAP_TOLERANCE:
            self._wav.writeframes(b"\x00" * (missing * self._frame_bytes))
            self._written += missing

    def _callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio

        with self._lock:
            if self._wav is not None:
                self._pad_until(time.monotonic(), frame_count)
                self._wav.writeframes(in_data)
                self._written += frame_count
        return (None, pyaudio.paContinue)

    def stop(self) -> None:
        stop_time = time.monotonic()
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            if self._wav is not None:
                self._pad_until(stop_time)
                self._wav.close()
                self._wav = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
