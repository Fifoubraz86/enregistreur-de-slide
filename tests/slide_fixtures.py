"""Diapos synthétiques réalistes (police lisible) pour tester la reconstruction."""

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _font(size):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def chart_slide(width=1280, height=720) -> np.ndarray:
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 40), "Neuroendocrine tumours: treatment", fill=(20, 40, 90), font=_font(48))
    y = 170
    for text, indent in (("Somatostatin analogues first line", 0), ("Lanreotide or octreotide", 1),
                         ("PRRT after progression", 0), ("Liver-directed therapy", 0)):
        d.text((80 + indent * 50, y), f"• {text}", fill="black", font=_font(30))
        y += 60
    # Graphique à droite : axes, barres, légende d'axe.
    x0, y0, x1, y1 = 760, 180, 1220, 620
    d.rectangle([x0, y0, x1, y1], outline=(180, 180, 180), width=2)
    d.line([x0 + 40, y1 - 40, x1 - 20, y1 - 40], fill="black", width=3)
    d.line([x0 + 40, y0 + 20, x0 + 40, y1 - 40], fill="black", width=3)
    for k, hgt in enumerate((120, 220, 300, 180)):
        bx = x0 + 80 + k * 90
        d.rectangle([bx, y1 - 40 - hgt, bx + 50, y1 - 40], fill=(40 + 50 * k, 90, 180))
    d.text((x0 + 150, y1 - 32), "Months", fill="black", font=_font(20))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
