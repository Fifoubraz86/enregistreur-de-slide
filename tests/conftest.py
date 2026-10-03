import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

W, H = 640, 360


def make_slide(n: int, bullets: int = 3) -> np.ndarray:
    """Diapo synthétique : titre, bloc coloré et puces différents selon n."""
    img = np.full((H, W, 3), 255, np.uint8)
    rng = np.random.default_rng(n)
    color = tuple(int(c) for c in rng.integers(0, 200, 3))
    cv2.rectangle(img, (0, 0), (W, 50), color, -1)
    cv2.putText(img, f"Diapo numero {n}", (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    x = int(rng.integers(300, 480))
    cv2.circle(img, (x, 200), 60 + 10 * (n % 4), color, -1)
    for i in range(bullets):
        cv2.putText(img, f"- point {n}.{i} texte", (30, 110 + 40 * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    return img


def with_webcam(frame: np.ndarray, t: float) -> np.ndarray:
    """Ajoute une vignette « webcam » animée dans le coin bas droit (hors zone diapo)."""
    out = frame.copy()
    rng = np.random.default_rng(int(t * 10))
    out[H - 80:, W - 120:] = rng.integers(0, 255, (80, 120, 3), dtype=np.uint8)
    return out


def scenario():
    """(durée en s, générateur d'image) : diapo 1, 2, vidéo, 3 (puces animées), retour à 1, 4."""
    rng = np.random.default_rng(0)
    return [
        (6, lambda t: make_slide(1)),
        (6, lambda t: make_slide(2)),
        (5, lambda t: rng.integers(0, 255, (H, W, 3), dtype=np.uint8)),  # passage vidéo
        (4, lambda t: make_slide(3, bullets=2)),
        (5, lambda t: make_slide(3, bullets=3)),  # puce supplémentaire
        (5, lambda t: make_slide(1)),  # retour à la diapo 1
        (6, lambda t: make_slide(4)),
    ]


def frames(fps: float, webcam: bool = False):
    t = 0.0
    for duration, gen in scenario():
        for _ in range(int(duration * fps)):
            frame = gen(t)
            yield t, with_webcam(frame, t) if webcam else frame
            t += 1.0 / fps


@pytest.fixture
def sample_video(tmp_path) -> Path:
    path = tmp_path / "reunion.mp4"
    fps = 5
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    for _, frame in frames(fps, webcam=True):
        writer.write(frame)
    writer.release()
    return path
