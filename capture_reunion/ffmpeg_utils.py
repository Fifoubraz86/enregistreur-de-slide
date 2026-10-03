"""Localisation et appel de ffmpeg."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Évite l'ouverture d'une console noire à chaque appel quand l'appli est un .exe fenêtré.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def find_ffmpeg() -> str:
    candidates: list[Optional[str]] = [os.environ.get("CAPTURE_REUNION_FFMPEG")]
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
    candidates.append(str(exe_dir / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")))
    candidates.append(shutil.which("ffmpeg"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise FileNotFoundError(
        "ffmpeg introuvable. Installez-le (winget install ffmpeg) ou placez ffmpeg.exe "
        "à côté du logiciel."
    )


def run_ffmpeg(args: list[str]) -> None:
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip().splitlines()[-5:]
        raise RuntimeError("ffmpeg a échoué :\n" + "\n".join(err))


def open_video_encoder(
    output: Path, width: int, height: int, fps: int, log, crf: int = 28
) -> subprocess.Popen:
    """Lance ffmpeg qui lit des images BGRA brutes sur son entrée standard."""
    cmd = [
        find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ]
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stderr=log, creationflags=NO_WINDOW
    )


def mux(video: Path, audio: Path, output: Path, audio_delay: float = 0.0) -> None:
    """Assemble vidéo et son. ``audio_delay`` > 0 : le son a démarré avant l'image."""
    audio_in = ["-ss", f"{audio_delay:.3f}", "-i", str(audio)] if audio_delay > 0 else ["-i", str(audio)]
    run_ffmpeg(["-i", str(video), *audio_in, "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(output)])


def to_mp3(audio: Path, output: Path, delay: float = 0.0) -> None:
    pre = ["-ss", f"{delay:.3f}"] if delay > 0 else []
    run_ffmpeg([*pre, "-i", str(audio), "-c:a", "libmp3lame", "-b:a", "128k", str(output)])


def mix_audio(inputs: list[tuple[Path, float]], output: Path) -> None:
    """Mélange plusieurs pistes (chemin, secondes à couper au début) en un WAV stéréo."""
    args: list[str] = []
    filters: list[str] = []
    for i, (path, delay) in enumerate(inputs):
        args += ["-i", str(path)]
        filters.append(
            f"[{i}:a]atrim=start={delay:.3f},asetpts=PTS-STARTPTS,"
            f"aresample=48000,aformat=channel_layouts=stereo[a{i}]"
        )
    if len(inputs) == 1:
        out = "[a0]"
    else:
        labels = "".join(f"[a{i}]" for i in range(len(inputs)))
        filters.append(f"{labels}amix=inputs={len(inputs)}:duration=longest:normalize=0[out]")
        out = "[out]"
    run_ffmpeg([*args, "-filter_complex", ";".join(filters), "-map", out,
                "-c:a", "pcm_s16le", str(output)])


def export_track(audio: Path, output: Path, delay: float = 0.0) -> None:
    """Piste légère (mono 16 kHz), suffisante pour la transcription."""
    pre = ["-ss", f"{delay:.3f}"] if delay > 0 else []
    run_ffmpeg([*pre, "-i", str(audio), "-ac", "1", "-ar", "16000",
                "-c:a", "aac", "-b:a", "48k", str(output)])
