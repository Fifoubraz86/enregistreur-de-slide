"""Détection des diapositives dans un flux d'images.

Principe : on échantillonne une image par seconde (réglable). Quand l'image
reste stable pendant ``stable_duration`` secondes et qu'elle diffère de la
dernière diapo retenue, on regarde si le contenu précédent a disparu (nouvelle
diapo) ou s'il est toujours là avec des éléments en plus (diapo qui se complète).

- Les passages vidéo (contenu qui bouge sans cesse) ne sont jamais retenus.
- Un retour à une diapo déjà vue n'est pas réenregistré,
  mais il est noté dans la chronologie, ce qui sert à la synchronisation
  avec la transcription Plaud.
- Si une diapo se complète (puces animées), l'image enregistrée est
  remplacée par la version la plus complète.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

Zone = tuple[float, float, float, float]  # x, y, largeur, hauteur en fractions (0-1)


@dataclass
class DetectorSettings:
    sample_interval: float = 1.0
    """Secondes entre deux images analysées."""
    change_threshold: float = 0.97
    """SSIM avec la diapo précédente en dessous duquel il peut s'agir d'une nouvelle diapo."""
    additive_tolerance: float = 0.05
    """Part maximale du contenu précédent qui peut disparaître quand une diapo se complète."""
    stable_threshold: float = 0.985
    """SSIM entre deux images successives au-dessus duquel l'image est « stable »."""
    stable_duration: float = 2.0
    """Durée de stabilité exigée avant de retenir une diapo (secondes)."""
    duplicate_threshold: float = 0.96
    """SSIM au-dessus duquel une diapo peut être considérée comme déjà vue…"""
    duplicate_max_change: float = 0.01
    """…si au plus cette part des pixels diffère."""
    analysis_width: int = 480
    zone: Optional[Zone] = None
    """Partie de l'image où se trouve la diapo (None = image entière)."""


@dataclass
class Slide:
    index: int
    timestamp: float
    """Secondes depuis le début de l'enregistrement."""
    image_path: Optional[Path] = None
    image: Optional[np.ndarray] = field(default=None, repr=False)
    small: Optional[np.ndarray] = field(default=None, repr=False)
    """Version réduite en niveaux de gris, pour reconnaître un retour à cette diapo."""


@dataclass
class TimelineEvent:
    timestamp: float
    slide_index: int


def crop_zone(frame: np.ndarray, zone: Optional[Zone]) -> np.ndarray:
    if zone is None:
        return frame
    h, w = frame.shape[:2]
    x, y, zw, zh = zone
    x0 = int(round(max(0.0, min(1.0, x)) * w))
    y0 = int(round(max(0.0, min(1.0, y)) * h))
    x1 = int(round(max(0.0, min(1.0, x + zw)) * w))
    y1 = int(round(max(0.0, min(1.0, y + zh)) * h))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return frame
    return frame[y0:y1, x0:x1]


def to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    if frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """SSIM moyen entre deux images en niveaux de gris de même taille."""
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    blur = lambda img: cv2.GaussianBlur(img, (11, 11), 1.5)  # noqa: E731
    mu_a, mu_b = blur(a), blur(b)
    mu_a2, mu_b2, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b
    sigma_a2 = blur(a * a) - mu_a2
    sigma_b2 = blur(b * b) - mu_b2
    sigma_ab = blur(a * b) - mu_ab
    num = (2 * mu_ab + c1) * (2 * sigma_ab + c2)
    den = (mu_a2 + mu_b2 + c1) * (sigma_a2 + sigma_b2 + c2)
    return float(np.mean(num / den))


def change_ratios(new: np.ndarray, ref: np.ndarray) -> tuple[float, float]:
    """(part des pixels modifiés, part du contenu de ``ref`` qui a disparu).

    Le « contenu » est repéré par les contours (texte, traits, images). Quand
    une diapo se complète, ses contours existants restent en place.
    """
    if new.shape != ref.shape:
        new = cv2.resize(new, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)
    changed = cv2.absdiff(new, ref) > 40
    ink = cv2.morphologyEx(ref, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 40
    ink_count = int(ink.sum())
    removed = float((changed & ink).sum()) / ink_count if ink_count else float(changed.mean())
    return float(changed.mean()), removed


def format_timestamp(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class SlideDetector:
    """Reçoit des images horodatées et repère les nouvelles diapos.

    ``on_slide(slide)`` est appelé pour chaque nouvelle diapo,
    ``on_slide_updated(slide)`` quand la dernière diapo est remplacée par une
    version plus complète.
    """

    def __init__(
        self,
        settings: Optional[DetectorSettings] = None,
        output_dir: Optional[Path] = None,
        on_slide: Optional[Callable[[Slide], None]] = None,
        on_slide_updated: Optional[Callable[[Slide], None]] = None,
        keep_images: bool = False,
    ) -> None:
        self.settings = settings or DetectorSettings()
        self.output_dir = Path(output_dir) if output_dir else None
        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        self.on_slide = on_slide
        self.on_slide_updated = on_slide_updated
        self.keep_images = keep_images

        self.slides: list[Slide] = []
        self.timeline: list[TimelineEvent] = []

        self._prev_small: Optional[np.ndarray] = None
        self._stable_since: float = 0.0
        self._ref_small: Optional[np.ndarray] = None
        self._current_index: Optional[int] = None
        self._last_sample_t: Optional[float] = None

    # -- API -------------------------------------------------------------
    def wants_sample(self, t: float) -> bool:
        """Indique si une image à l'instant ``t`` doit être analysée."""
        if self._last_sample_t is None:
            return True
        return t - self._last_sample_t >= self.settings.sample_interval - 1e-6

    def feed(self, t: float, frame: np.ndarray) -> Optional[Slide]:
        """Analyse une image (BGR ou BGRA). Renvoie la diapo si elle est nouvelle."""
        self._last_sample_t = t
        cropped = crop_zone(frame, self.settings.zone)
        gray = to_gray(cropped)
        small = self._shrink(gray)

        if self._prev_small is None or self._prev_small.shape != small.shape:
            self._prev_small = small
            self._stable_since = t
            return None

        stable = ssim(small, self._prev_small) >= self.settings.stable_threshold
        self._prev_small = small
        if not stable:
            self._stable_since = t
            return None
        if t - self._stable_since < self.settings.stable_duration - 1e-6:
            return None

        if self._ref_small is None:
            return self._new_slide(self._stable_since, cropped, small)

        score = ssim(small, self._ref_small)
        if score >= self.settings.stable_threshold:
            return None
        _, removed = change_ratios(small, self._ref_small)
        if score < self.settings.change_threshold and removed > self.settings.additive_tolerance:
            return self._new_slide(self._stable_since, cropped, small)
        if self._current_index is not None:
            self._update_current(cropped, small)
        return None

    # -- interne ---------------------------------------------------------
    def _shrink(self, gray: np.ndarray) -> np.ndarray:
        h, w = gray.shape[:2]
        target_w = min(self.settings.analysis_width, w)
        target_h = max(8, int(round(h * target_w / w)))
        small = cv2.resize(gray, (target_w, target_h), interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(small, (3, 3), 0)

    def _find_duplicate(self, small: np.ndarray) -> Optional[Slide]:
        best, best_score = None, self.settings.duplicate_threshold
        for slide in self.slides:
            if slide.small is None or slide.small.shape != small.shape:
                continue
            score = ssim(small, slide.small)
            if score >= best_score:
                changed, _ = change_ratios(small, slide.small)
                if changed <= self.settings.duplicate_max_change:
                    best, best_score = slide, score
        return best

    def _new_slide(self, t: float, cropped, small) -> Optional[Slide]:
        self._ref_small = small
        duplicate = self._find_duplicate(small)
        if duplicate is not None:
            # Retour à une diapo déjà vue : on le note sans la réenregistrer.
            if duplicate.index != self._current_index:
                self.timeline.append(TimelineEvent(t, duplicate.index))
            self._current_index = duplicate.index
            return None

        slide = Slide(index=len(self.slides) + 1, timestamp=t, small=small)
        self._store_image(slide, cropped)
        self.slides.append(slide)
        self.timeline.append(TimelineEvent(t, slide.index))
        self._current_index = slide.index
        if self.on_slide:
            self.on_slide(slide)
        return slide

    def _update_current(self, cropped, small) -> None:
        slide = self.slides[self._current_index - 1]
        self._ref_small = small
        slide.small = small
        self._store_image(slide, cropped)
        if self.on_slide_updated:
            self.on_slide_updated(slide)

    def _store_image(self, slide: Slide, cropped: np.ndarray) -> None:
        image = cropped[:, :, :3] if cropped.ndim == 3 and cropped.shape[2] == 4 else cropped
        if self.keep_images or self.output_dir is None:
            slide.image = np.ascontiguousarray(image).copy()
        if self.output_dir is not None:
            if slide.image_path is None:
                h, m, sec = format_timestamp(slide.timestamp).split(":")
                name = f"diapo_{slide.index:03d}_{h}h{m}m{sec}s.png"
                slide.image_path = self.output_dir / name
            ok, buf = cv2.imencode(".png", image)
            if ok:
                # tofile gère les chemins avec accents sous Windows (imwrite non).
                buf.tofile(str(slide.image_path))


def extract_from_video(
    video_path: Path,
    output_dir: Path,
    settings: Optional[DetectorSettings] = None,
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> SlideDetector:
    """Extrait les diapos d'un fichier vidéo existant."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Impossible d'ouvrir la vidéo : {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0 or fps > 240:
        fps = 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    detector = SlideDetector(settings, output_dir=output_dir)
    index = 0
    try:
        while True:
            if not cap.grab():
                break
            t = index / fps
            if detector.wants_sample(t):
                ok, frame = cap.retrieve()
                if ok:
                    detector.feed(t, frame)
                if progress and total:
                    progress(min(1.0, index / total))
                if cancelled and cancelled():
                    break
            index += 1
    finally:
        cap.release()
    if progress:
        progress(1.0)
    return detector
