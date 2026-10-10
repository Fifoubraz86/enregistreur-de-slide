"""Opérations « après la réunion », partagées par l'interface et la ligne de commande."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .export import build_pptx, build_report
from .session import Session
from .slides import DetectorSettings, extract_from_video
from .transcript import (
    SlideSection,
    assign_segments,
    load_transcript,
    merge_segments,
    notes_by_slide,
    offset_from_clock,
)


def extract_slides(
    session: Session,
    settings: Optional[DetectorSettings] = None,
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Session:
    """(Ré)extrait les diapos de la vidéo de la session. Les anciennes diapos sont remplacées."""
    video = session.video_path
    if video is None or not video.exists():
        raise FileNotFoundError("Vidéo introuvable pour cette session.")
    settings = settings or DetectorSettings()
    if settings.zone is None:
        settings.zone = session.zone
    session.zone = settings.zone
    if session.slides_dir.exists():
        shutil.rmtree(session.slides_dir)
    detector = extract_from_video(video, session.slides_dir, settings, progress, cancelled)
    session.set_slides_from(detector)
    if not session.duration:
        session.duration = detector._last_sample_t or 0.0
    session.save()
    return session


def compute_offset(
    session: Session, plaud_start: Optional[datetime] = None, offset: Optional[float] = None
) -> float:
    if offset is not None:
        return offset
    if plaud_start is not None and session.start_datetime is not None:
        return offset_from_clock(session.start_datetime, plaud_start)
    return 0.0


def resolve_transcript(
    session: Session, transcript: Optional[Path], offset: float
) -> tuple[Optional[Path], float]:
    """Sans fichier indiqué, prend la transcription faite par le logiciel.

    Celle-ci est déjà horodatée sur la vidéo : le décalage est alors nul.
    """
    own = session.folder / session.transcript if session.transcript else None
    if transcript is None:
        return (own, 0.0) if own and own.exists() else (None, offset)
    if own and Path(transcript).resolve() == own.resolve():
        return Path(transcript), 0.0
    return Path(transcript), offset


def sections_for(session: Session, transcript: Path, offset: float) -> list[SlideSection]:
    segments = load_transcript(transcript)
    sections = assign_segments(session.timeline, segments, offset, session.duration or None)
    for section in sections:
        section.segments = merge_segments(section.segments)
    return sections


def make_pptx(
    session: Session,
    template: Optional[Path] = None,
    transcript: Optional[Path] = None,
    offset: float = 0.0,
    output: Optional[Path] = None,
) -> Path:
    images = session.slide_paths()
    transcript, offset = resolve_transcript(session, transcript, offset)
    notes = None
    if transcript:
        notes = notes_by_slide(sections_for(session, transcript, offset), offset)
        lang = _transcript_language(session, transcript)
        briefs = session.ai.get("slide_summaries", {}).get(lang, {}) if lang else {}
        for index, brief in briefs.items():
            label = "In brief: " if lang == "en" else "En bref : "
            key = int(index)
            notes[key] = f"{label}{brief}\n\n{notes.get(key, '')}".strip()
    else:
        notes = {}
        for t, index in session.timeline:
            line = f"Affichée à {_fmt(t)}"
            notes[index] = f"{notes[index]}\n{line}" if index in notes else line
    output = output or session.folder / "diapos.pptx"
    return build_pptx(images, output, template=template, notes=notes)


def _transcript_language(session: Session, transcript: Path) -> Optional[str]:
    """Langue d'une transcription produite par le logiciel (None : fichier externe)."""
    own = session.folder / session.transcript if session.transcript else None
    if own and own.exists() and Path(transcript).resolve() == own.resolve():
        return session.language or "fr"
    for lang, info in session.translations.items():
        if Path(transcript).resolve() == (session.folder / info["transcript"]).resolve():
            return lang
    return None


def _summary_for(session: Session, transcript: Path, sections: list[SlideSection], language: str):
    """(phrases clés, mots-clés, résumé IA, résumés par diapo) pour cette transcription."""
    lang = _transcript_language(session, transcript)
    ai_summary = session.ai.get("summary", {}).get(lang, "") if lang else ""
    slides = session.ai.get("slide_summaries", {}).get(lang, {}) if lang else {}
    if lang == (session.language or "fr") and session.summary:
        return (session.summary.get("sentences", []), session.summary.get("keywords", []),
                ai_summary, slides)
    if lang in session.translations:
        summary = session.translations[lang].get("summary", {})
        return summary.get("sentences", []), summary.get("keywords", []), ai_summary, slides
    from .summarize import summarize

    computed = summarize([seg.text for sec in sections for seg in sec.segments], language)
    return computed.sentences, computed.keywords, "", {}


def make_report(
    session: Session,
    transcript: Optional[Path] = None,
    offset: float = 0.0,
    output: Optional[Path] = None,
    language: Optional[str] = None,
) -> Path:
    transcript, offset = resolve_transcript(session, transcript, offset)
    if transcript is None:
        raise ValueError("Aucune transcription : lancez « Transcrire » ou choisissez un export Plaud.")
    language = language or session.language or "fr"
    sections = sections_for(session, transcript, offset)
    images = {s.index: session.folder / s.file for s in session.slides}
    start = session.start_datetime
    count = f"{len(session.slides)} {'slides' if language == 'en' else 'diapos'}"
    date = (f"{start:%d/%m/%Y, %H:%M}" if language == "en" else f"{start:%d/%m/%Y à %Hh%M}") if start else ""
    subtitle = " · ".join(part for part in (session.window_title, date, count) if part)
    sentences, keywords, ai_summary, slide_summaries = _summary_for(session, transcript, sections, language)
    output = output or session.folder / "compte-rendu.docx"
    return build_report(sections, images, output, subtitle=subtitle, offset=offset,
                        summary_sentences=sentences, keywords=keywords, language=language,
                        ai_summary=ai_summary, ai_model=session.ai.get("model", ""),
                        slide_summaries=slide_summaries)


def make_reports(
    session: Session, transcript: Optional[Path] = None, offset: float = 0.0
) -> list[Path]:
    """Compte-rendu, plus une version par traduction faite à la transcription."""
    paths = [make_report(session, transcript, offset)]
    own = session.folder / session.transcript if session.transcript else None
    using_own = transcript is None or (own is not None and Path(transcript).resolve() == own.resolve())
    if using_own:
        for lang, info in session.translations.items():
            path = session.folder / info["transcript"]
            if path.exists():
                paths.append(make_report(session, path, 0.0, session.folder / f"compte-rendu_{lang}.docx",
                                         language=lang))
    return paths


def _fmt(t: float) -> str:
    from .slides import format_timestamp

    return format_timestamp(t)


def rebuild_presentation(
    session: Session,
    template: Optional[Path] = None,
    engine: str = "aucun",
    claude_model: str = "sonnet",
    allow_online: bool = True,
    llm_url: Optional[str] = None,
    llm_model: Optional[str] = None,
    transcript: Optional[Path] = None,
    offset: float = 0.0,
    progress: Optional[Callable[[float], None]] = None,
    llm=None,
    glossary_path: Optional[Path] = None,
) -> tuple[Path, list[str]]:
    """PowerPoint reconstruit (texte modifiable + figures) : (chemin, avertissements)."""
    from .rebuild import build_rebuilt_pptx, read_slides
    from .transcribe import make_engine

    images = session.slide_paths()
    if not images:
        raise ValueError("Aucune diapo : lancez d'abord l'extraction.")
    warnings: list[str] = []
    client = llm if llm is not None else make_engine(engine, llm_url, llm_model, claude_model,
                                                     allow_online, warnings)
    from .glossary import default_path, load

    contents, read_warnings = read_slides(images, session.folder, client, progress,
                                          glossary=load(glossary_path or default_path()))
    warnings += read_warnings

    notes: dict[int, str] = {}
    transcript, offset = resolve_transcript(session, transcript, offset)
    if transcript:
        notes = notes_by_slide(sections_for(session, transcript, offset), offset)
    output = build_rebuilt_pptx(contents, session.folder, session.folder / "diapos_reconstruites.pptx",
                                template=template, notes=notes)
    return output, warnings
