"""Dossier de session : vidéo, audio, diapos et un fichier session.json qui relie tout."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .slides import SlideDetector, Zone

SESSION_FILE = "session.json"
SLIDES_DIR = "diapos"


@dataclass
class SlideRecord:
    index: int
    timestamp: float
    file: str
    """Chemin relatif au dossier de session."""


@dataclass
class Session:
    folder: Path
    started_at: Optional[str] = None
    """Heure de début (ISO 8601, heure locale)."""
    window_title: str = ""
    video: Optional[str] = None
    audio: Optional[str] = None
    tracks: dict[str, str] = field(default_factory=dict)
    """Pistes séparées pour la transcription : {"participants": ..., "moi": ...}."""
    transcript: Optional[str] = None
    """Transcription faite par le logiciel (horodatée sur la vidéo)."""
    language: Optional[str] = None
    """Langue détectée de la transcription (« fr », « en »…)."""
    summary: dict = field(default_factory=dict)
    """Résumé : {"sentences": [...], "keywords": [...]}."""
    translations: dict = field(default_factory=dict)
    """Versions traduites : {"fr": {"transcript": "transcription_fr.srt", "summary": {...}}}."""
    duration: float = 0.0
    zone: Optional[Zone] = None
    slides: list[SlideRecord] = field(default_factory=list)
    timeline: list[tuple[float, int]] = field(default_factory=list)

    # -- chemins ---------------------------------------------------------
    @property
    def slides_dir(self) -> Path:
        return self.folder / SLIDES_DIR

    @property
    def video_path(self) -> Optional[Path]:
        return self.folder / self.video if self.video else None

    @property
    def audio_path(self) -> Optional[Path]:
        return self.folder / self.audio if self.audio else None

    @property
    def start_datetime(self) -> Optional[datetime]:
        return datetime.fromisoformat(self.started_at) if self.started_at else None

    def slide_paths(self) -> list[Path]:
        return [self.folder / s.file for s in self.slides]

    # -- persistance -----------------------------------------------------
    def save(self) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("folder")
        path = self.folder / SESSION_FILE
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, folder: Path) -> "Session":
        folder = Path(folder)
        path = folder / SESSION_FILE
        if not path.exists():
            raise FileNotFoundError(f"Pas de {SESSION_FILE} dans {folder}")
        data = json.loads(path.read_text(encoding="utf-8"))
        session = cls(folder=folder)
        session.started_at = data.get("started_at")
        session.window_title = data.get("window_title", "")
        session.video = data.get("video")
        session.audio = data.get("audio")
        session.tracks = dict(data.get("tracks") or {})
        session.transcript = data.get("transcript")
        session.language = data.get("language")
        session.summary = dict(data.get("summary") or {})
        session.translations = dict(data.get("translations") or {})
        session.duration = float(data.get("duration") or 0.0)
        zone = data.get("zone")
        session.zone = tuple(zone) if zone else None
        session.slides = [SlideRecord(**s) for s in data.get("slides", [])]
        session.timeline = [(float(t), int(i)) for t, i in data.get("timeline", [])]
        return session

    @classmethod
    def for_video(cls, video: Path) -> "Session":
        """Session existante à côté de la vidéo, ou nouvelle session pour cette vidéo."""
        video = Path(video)
        folder = video.parent
        if (folder / SESSION_FILE).exists():
            session = cls.load(folder)
            if session.video_path and session.video_path.resolve() == video.resolve():
                return session
            folder = folder / f"{video.stem} - diapos"
            if (folder / SESSION_FILE).exists():
                return cls.load(folder)
        session = cls(folder=folder)
        try:
            session.video = str(video.relative_to(folder))
        except ValueError:
            session.video = str(video)
        return session

    def set_slides_from(self, detector: SlideDetector) -> None:
        self.slides = []
        for slide in detector.slides:
            if slide.image_path is None:
                continue
            try:
                rel = Path(slide.image_path).relative_to(self.folder)
            except ValueError:
                rel = Path(slide.image_path)
            self.slides.append(SlideRecord(slide.index, round(slide.timestamp, 2), rel.as_posix()))
        self.timeline = [(round(e.timestamp, 2), e.slide_index) for e in detector.timeline]


def safe_name(text: str, max_len: int = 40) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:max_len].strip() or "Reunion"


def new_session_folder(base: Path, window_title: str, when: Optional[datetime] = None) -> Path:
    when = when or datetime.now()
    name = f"{when:%Y-%m-%d_%Hh%M} - {safe_name(window_title)}"
    folder = Path(base) / name
    n = 2
    while folder.exists():
        folder = Path(base) / f"{name} ({n})"
        n += 1
    return folder
