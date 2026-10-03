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


def sections_for(session: Session, transcript: Path, offset: float) -> list[SlideSection]:
    segments = load_transcript(transcript)
    return assign_segments(session.timeline, segments, offset, session.duration or None)


def make_pptx(
    session: Session,
    template: Optional[Path] = None,
    transcript: Optional[Path] = None,
    offset: float = 0.0,
    output: Optional[Path] = None,
) -> Path:
    images = session.slide_paths()
    notes = None
    if transcript:
        notes = notes_by_slide(sections_for(session, transcript, offset), offset)
    else:
        notes = {}
        for t, index in session.timeline:
            line = f"Affichée à {_fmt(t)}"
            notes[index] = f"{notes[index]}\n{line}" if index in notes else line
    output = output or session.folder / "diapos.pptx"
    return build_pptx(images, output, template=template, notes=notes)


def make_report(
    session: Session,
    transcript: Path,
    offset: float = 0.0,
    output: Optional[Path] = None,
) -> Path:
    sections = sections_for(session, transcript, offset)
    images = {s.index: session.folder / s.file for s in session.slides}
    start = session.start_datetime
    subtitle = " · ".join(
        part
        for part in (
            session.window_title,
            f"{start:%d/%m/%Y à %Hh%M}" if start else "",
            f"{len(session.slides)} diapos",
        )
        if part
    )
    output = output or session.folder / "compte-rendu.docx"
    return build_report(sections, images, output, subtitle=subtitle, offset=offset)


def _fmt(t: float) -> str:
    from .slides import format_timestamp

    return format_timestamp(t)
