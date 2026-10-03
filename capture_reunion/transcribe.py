"""Transcription sur ce PC, sans Plaud ni service en ligne (faster-whisper).

Le modèle de reconnaissance vocale (Whisper, libre) tourne sur le processeur :
aucun envoi de données, aucun abonnement. Il est téléchargé une seule fois
(dossier ``modeles``), ensuite tout fonctionne hors connexion.

La langue est détectée automatiquement (une conférence en anglais reste en
anglais). Le texte est regroupé en paragraphes, suivi d'un résumé (phrases clés,
sans IA) et, sur demande, d'une version traduite anglais ↔ français.

Quand l'enregistrement a deux pistes (son de l'ordinateur et micro), chacune
est transcrite séparément : on sait donc qui parle (« Participants » / « Moi »)
sans aucune analyse supplémentaire.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .ffmpeg_utils import NO_WINDOW, find_ffmpeg
from .session import Session
from .summarize import Summary, summarize
from .transcript import MIN_INTRO, Segment, merge_segments

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


SAMPLE_RATE = 16000


def load_audio(path: Path) -> np.ndarray:
    """Décode le son en 16 kHz mono (float32) avec ffmpeg.

    On ne laisse pas faster-whisper décoder lui-même : il passe par PyAV, dont
    certaines versions refusent ses options (« unexpected keyword argument
    'metadata_errors' »).
    """
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", str(path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip().splitlines()[-3:]
        raise RuntimeError(f"Lecture du son impossible ({Path(path).name}) :\n" + "\n".join(err))
    return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0


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
    language: Optional[str] = None,
    vocabulary: str = "",
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    whisper=None,
) -> tuple[list[Segment], str]:
    """Transcrit chaque piste ; renvoie les passages triés par heure et la langue.

    ``language=None`` : détection automatique sur le début de chaque piste.
    Imposer une langue qui n'est pas celle parlée fait *traduire* Whisper, et mal.
    """
    whisper = whisper or load_model(model)
    per_track: list[list[Segment]] = []
    spoken: Counter = Counter()  # secondes de parole par langue
    for i, track in enumerate(tracks):
        audio = load_audio(track.path)
        if audio.size == 0:
            per_track.append([])
            continue
        segments, info = whisper.transcribe(
            audio,
            language=language or None,
            vad_filter=True,  # saute les silences : plus rapide, moins d'inventions
            initial_prompt=vocabulary or None,
            hotwords=vocabulary or None,
            condition_on_previous_text=False,
        )
        found: list[Segment] = []
        duration = audio.size / SAMPLE_RATE
        track_language = language or getattr(info, "language", None) or "fr"
        for seg in segments:
            text = seg.text.strip()
            if text:
                found.append(Segment(round(seg.start, 2), text, track.speaker, round(seg.end, 2)))
            if progress and duration:
                progress(min(1.0, (i + seg.end / duration) / len(tracks)))
            if cancelled and cancelled():
                raise RuntimeError("Transcription annulée.")
        per_track.append(found)
        spoken[track_language] += sum((s.end or s.start) - s.start for s in found)
        if progress:
            progress((i + 1) / len(tracks))

    speakers = [t.speaker for t in tracks]
    if "Moi" in speakers and "Participants" in speakers:
        mine, others = speakers.index("Moi"), speakers.index("Participants")
        per_track[mine] = remove_echo(per_track[mine], per_track[others])
    merged = [s for segs in per_track for s in segs]
    merged.sort(key=lambda s: s.start)
    detected = language or (spoken.most_common(1)[0][0] if spoken else "fr")
    return merged, detected


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


SUMMARY_TITLES = {
    "fr": ("RÉSUMÉ (phrases clés extraites automatiquement)", "Mots-clés"),
    "en": ("SUMMARY (key sentences, automatically extracted)", "Keywords"),
}
SPEAKER_TRANSLATIONS = {"en": {"Moi": "Me"}}


def write_text(
    segments: list[Segment], path: Path, summary: Optional[Summary] = None, language: str = "fr"
) -> Path:
    """Version lisible : un paragraphe par prise de parole, puis le résumé."""
    parts = [s.line() for s in segments]
    if summary and summary.sentences:
        title, kw_title = SUMMARY_TITLES.get(language, SUMMARY_TITLES["fr"])
        parts += ["", title, ""] + [f"• {s}" for s in summary.sentences]
        if summary.keywords:
            parts += ["", f"{kw_title} : {', '.join(summary.keywords)}"]
    Path(path).write_text("\n\n".join(parts).replace("\n\n\n\n", "\n\n") + "\n", encoding="utf-8")
    return Path(path)


def _stage(progress, start: float, end: float):
    return (lambda p: progress(start + (end - start) * p)) if progress else None


def transcribe_session(
    session: Session,
    model: str = DEFAULT_MODEL,
    vocabulary: str = "",
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    whisper=None,
    language: Optional[str] = None,
    translate: bool = False,
    translator=None,
) -> Path:
    """Transcrit l'enregistrement.

    Écrit transcription.srt (horodatage fin, pour les diapos) et transcription.txt
    (paragraphes + résumé) ; avec ``translate``, aussi transcription_<langue>.srt/.txt.
    """
    split = 0.85 if translate else 1.0
    segments, lang = transcribe_tracks(session_tracks(session), model, language, vocabulary,
                                       _stage(progress, 0, split), cancelled, whisper)
    srt = write_srt(segments, session.folder / TRANSCRIPT_FILE)
    paragraphs = merge_segments(segments, [t for t, _ in session.timeline if t > MIN_INTRO])
    summary = summarize([p.text for p in paragraphs], lang)
    write_text(paragraphs, session.folder / "transcription.txt", summary, lang)
    session.transcript = TRANSCRIPT_FILE
    session.language = lang
    session.summary = {"sentences": summary.sentences, "keywords": summary.keywords}
    session.translations = {}

    if translate:
        from .translate import Translator, target_language

        target = target_language(lang)
        tr = translator or Translator(lang, target)
        texts = tr.translate([p.text for p in paragraphs], _stage(progress, split, 1.0))
        names = SPEAKER_TRANSLATIONS.get(target, {})
        translated = [Segment(p.start, t, names.get(p.speaker, p.speaker), p.end)
                      for p, t in zip(paragraphs, texts)]
        t_summary = Summary(tr.translate_sentences(summary.sentences),
                            tr.translate_sentences(summary.keywords))
        t_srt = f"transcription_{target}.srt"
        write_srt(translated, session.folder / t_srt)
        write_text(translated, session.folder / f"transcription_{target}.txt", t_summary, target)
        session.translations[target] = {
            "transcript": t_srt,
            "summary": {"sentences": t_summary.sentences, "keywords": t_summary.keywords},
        }
    session.save()
    if progress:
        progress(1.0)
    return srt
