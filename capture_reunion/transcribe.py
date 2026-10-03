"""Transcription sur ce PC, sans Plaud ni service en ligne (faster-whisper).

Le modèle de reconnaissance vocale (Whisper, libre) tourne sur le processeur :
aucun envoi de données, aucun abonnement. Il est téléchargé une seule fois
(dossier ``modeles``), ensuite tout fonctionne hors connexion.

Quand l'enregistrement a deux pistes (son de l'ordinateur et micro), chacune
est transcrite séparément : on sait donc qui parle (« Participants » / « Moi »)
sans aucune analyse supplémentaire.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

from .session import Session
from .transcript import Segment

# Libellé affiché → modèle faster-whisper. Les durées sont des ordres de grandeur
# pour un portable récent sans carte graphique dédiée.
MODELS = {
    "Rapide (small, ~0,5 Go)": "small",
    "Précis (medium, ~1,5 Go)": "medium",
    "Très précis (large-v3-turbo, ~1,6 Go)": "large-v3-turbo",
}
DEFAULT_MODEL = "small"
SPEAKERS = {"participants": "Participants", "moi": "Moi"}
TRANSCRIPT_FILE = "transcription.srt"


def models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    root = Path(base) if base else Path.home() / ".cache"
    return root / "CaptureReunion" / "modeles"


@dataclass
class Track:
    path: Path
    speaker: str = ""


def session_tracks(session: Session) -> list[Track]:
    """Pistes à transcrire : séparées si possible, sinon le son de la vidéo."""
    tracks = [
        Track(session.folder / rel, SPEAKERS.get(name, name.capitalize()))
        for name, rel in sorted(session.tracks.items())
        if (session.folder / rel).exists()
    ]
    if tracks:
        return tracks
    for candidate in (session.audio_path, session.video_path):
        if candidate and candidate.exists():
            return [Track(candidate)]
    raise FileNotFoundError("Aucun son à transcrire dans cet enregistrement.")


def load_model(model: str = DEFAULT_MODEL):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Module faster-whisper absent : pip install faster-whisper") from exc
    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)
    try:
        return WhisperModel(model, device="cpu", compute_type="int8",
                            cpu_threads=os.cpu_count() or 4, download_root=str(target))
    except Exception as exc:
        raise RuntimeError(
            f"Impossible de charger le modèle « {model} ». La première fois, une connexion "
            f"Internet est nécessaire pour le télécharger.\nDétail : {exc}"
        ) from exc


def transcribe_tracks(
    tracks: list[Track],
    model: str = DEFAULT_MODEL,
    language: str = "fr",
    vocabulary: str = "",
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    whisper=None,
) -> list[Segment]:
    """Transcrit chaque piste ; renvoie les passages triés par heure."""
    whisper = whisper or load_model(model)
    per_track: list[list[Segment]] = []
    for i, track in enumerate(tracks):
        segments, info = whisper.transcribe(
            str(track.path),
            language=language or None,
            vad_filter=True,  # saute les silences : plus rapide, moins d'inventions
            initial_prompt=vocabulary or None,
            hotwords=vocabulary or None,
            condition_on_previous_text=False,
        )
        found: list[Segment] = []
        duration = getattr(info, "duration", 0) or 0
        for seg in segments:
            text = seg.text.strip()
            if text:
                found.append(Segment(round(seg.start, 2), text, track.speaker, round(seg.end, 2)))
            if progress and duration:
                progress(min(1.0, (i + seg.end / duration) / len(tracks)))
            if cancelled and cancelled():
                raise RuntimeError("Transcription annulée.")
        per_track.append(found)
        if progress:
            progress((i + 1) / len(tracks))

    speakers = [t.speaker for t in tracks]
    if "Moi" in speakers and "Participants" in speakers:
        mine, others = speakers.index("Moi"), speakers.index("Participants")
        per_track[mine] = remove_echo(per_track[mine], per_track[others])
    merged = [s for segs in per_track for s in segs]
    merged.sort(key=lambda s: s.start)
    return merged


def remove_echo(mine: list[Segment], others: list[Segment]) -> list[Segment]:
    """Retire du micro ce qui est en fait le son des haut-parleurs capté par le micro
    (même texte, au même moment, que la piste des participants)."""
    kept = []
    for seg in mine:
        end = seg.end if seg.end is not None else seg.start + 2
        echo = False
        for other in others:
            o_end = other.end if other.end is not None else other.start + 2
            overlap = min(end, o_end) - max(seg.start, other.start)
            if overlap <= 0.5 * max(0.1, end - seg.start):
                continue
            if SequenceMatcher(None, seg.text.lower(), other.text.lower()).ratio() > 0.6:
                echo = True
                break
        if not echo:
            kept.append(seg)
    return kept


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[Segment], path: Path) -> Path:
    blocks = []
    for n, seg in enumerate(segments, start=1):
        end = seg.end if seg.end is not None else seg.start + 2
        who = f"{seg.speaker}: " if seg.speaker else ""
        blocks.append(f"{n}\n{_srt_time(seg.start)} --> {_srt_time(end)}\n{who}{seg.text}\n")
    Path(path).write_text("\n".join(blocks), encoding="utf-8")
    return Path(path)


def write_text(segments: list[Segment], path: Path) -> Path:
    """Version lisible : « [00:01:23] Moi : … »."""
    Path(path).write_text("\n".join(s.line() for s in segments) + "\n", encoding="utf-8")
    return Path(path)


def transcribe_session(
    session: Session,
    model: str = DEFAULT_MODEL,
    vocabulary: str = "",
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    whisper=None,
) -> Path:
    """Transcrit l'enregistrement ; écrit transcription.srt et transcription.txt."""
    segments = transcribe_tracks(session_tracks(session), model, "fr", vocabulary,
                                 progress, cancelled, whisper)
    srt = write_srt(segments, session.folder / TRANSCRIPT_FILE)
    write_text(segments, session.folder / "transcription.txt")
    session.transcript = TRANSCRIPT_FILE
    session.save()
    return srt
