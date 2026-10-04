"""Traduction hors ligne anglais ↔ français.

Modèles libres d'Argos Translate (OPUS-MT, ~100 Mo par sens), exécutés
localement avec CTranslate2 (déjà utilisé par la transcription) et
SentencePiece. Le modèle est téléchargé une seule fois ; ensuite rien ne
quitte le PC.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .summarize import split_sentences
from .transcribe import models_dir

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
LANGUAGE_NAMES = {"fr": "français", "en": "anglais"}


def target_language(source: str) -> str:
    """Langue de la version traduite : le français, ou l'anglais si l'original est en français."""
    return "en" if source == "fr" else "fr"


# Certains serveurs refusent (403) les requêtes qui se présentent comme « Python-urllib ».
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 CaptureReunion",
    "Accept": "*/*",
}


def _open(url: str, timeout: float):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _open(url, 120) as response, open(tmp, "wb") as out:
        shutil.copyfileobj(response, out)
    tmp.replace(dest)


def _extract_dropped(root: Path) -> Optional[Path]:
    """Modèle .argosmodel téléchargé à la main et déposé dans le dossier."""
    for archive in sorted(root.glob("*.argosmodel")):
        with zipfile.ZipFile(archive) as z:
            z.extractall(root)
        archive.unlink()
    return _find_model_dir(root)


def _find_model_dir(root: Path) -> Optional[Path]:
    for spm in root.rglob("sentencepiece.model"):
        if (spm.parent / "model" / "model.bin").exists():
            return spm.parent
    return None


def ensure_model(source: str, target: str, root: Optional[Path] = None) -> Path:
    """Dossier du modèle source→cible, téléchargé si besoin."""
    root = (root or models_dir()) / f"traduction-{source}_{target}"
    root.mkdir(parents=True, exist_ok=True)
    found = _find_model_dir(root) or _extract_dropped(root)
    if found:
        return found
    link = f"https://argos-net.com/v1/translate-{source}_{target}-1_9.argosmodel"
    try:
        with _open(INDEX_URL, 30) as response:
            index = json.load(response)
        package = next(p for p in index if p["from_code"] == source and p["to_code"] == target)
        link = package["links"][0]
        archive = root / "modele.argosmodel"
        _download(link, archive)
    except StopIteration:
        raise RuntimeError(f"Pas de modèle de traduction {source} → {target}.") from None
    except Exception as exc:
        raise RuntimeError(
            f"Téléchargement du modèle de traduction impossible ({exc}).\n\n"
            f"Solution : ouvrez ce lien dans votre navigateur :\n{link}\n"
            f"puis déposez le fichier téléchargé (.argosmodel) dans :\n{root}\n"
            "et relancez la transcription.\n\nOu utilisez LM Studio (meilleure traduction)."
        ) from exc
    with zipfile.ZipFile(archive) as z:
        z.extractall(root)
    archive.unlink()
    found = _find_model_dir(root)
    if not found:
        raise RuntimeError("Modèle de traduction téléchargé mais illisible.")
    return found


class Translator:
    def __init__(self, source: str, target: str, model_dir: Optional[Path] = None,
                 translator=None, tokenizer=None) -> None:
        self.source, self.target = source, target
        if translator is None or tokenizer is None:
            import ctranslate2
            import sentencepiece

            model_dir = model_dir or ensure_model(source, target)
            translator = translator or ctranslate2.Translator(
                str(model_dir / "model"), device="cpu", compute_type="int8")
            tokenizer = tokenizer or sentencepiece.SentencePieceProcessor(
                model_file=str(model_dir / "sentencepiece.model"))
        self._translator = translator
        self._sp = tokenizer

    def translate_sentences(self, sentences: list[str]) -> list[str]:
        if not sentences:
            return []
        tokens = [self._sp.encode(s, out_type=str) for s in sentences]
        results = self._translator.translate_batch(
            tokens, beam_size=4, max_batch_size=16, replace_unknowns=True)
        return [self._sp.decode(r.hypotheses[0]).strip() for r in results]

    def translate(
        self,
        texts: list[str],
        progress: Optional[Callable[[float], None]] = None,
    ) -> list[str]:
        """Traduit des paragraphes, phrase par phrase (les modèles gèrent mal les textes longs)."""
        out = []
        for i, text in enumerate(texts):
            out.append(" ".join(self.translate_sentences(split_sentences(text))))
            if progress:
                progress((i + 1) / len(texts))
        return out
