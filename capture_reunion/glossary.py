"""Glossaire médical français ↔ anglais.

Formats acceptés (une entrée par ligne) :

- ``ABRÉV — Terme en français — English term (ENGLISH ABBR)``, « – » s'il n'y a pas
  d'abréviation ; les chapitres sont annoncés par une ligne « 8. TITRE » encadrée de
  « ===== » (format du glossaire d'endocrinologie) ;
- ``terme anglais = terme français`` (format simple).

Le glossaire complet est trop long pour être envoyé à l'IA à chaque fois : on ne lui
transmet que les entrées dont un terme ou une abréviation apparaît dans le passage
à traduire. Pour la reconnaissance vocale, on peut fournir les termes d'un chapitre
(le thème de la réunion).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_SECTION = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
_RULE = re.compile(r"^\s*[=\-_]{5,}\s*$")
_SUBSECTION = re.compile(r"^\s*-{2,}\s*(.+?)\s*-{2,}\s*$")
_PAREN = re.compile(r"\(([^()]*)\)")
_DASHES = (" — ", " – ")


@dataclass
class Entry:
    fr: str
    en: str
    abbr_fr: list[str] = field(default_factory=list)
    abbr_en: list[str] = field(default_factory=list)
    section: str = ""

    def label(self, language: str) -> str:
        term, abbr = (self.fr, self.abbr_fr) if language == "fr" else (self.en, self.abbr_en)
        term = term.strip()
        extra = [a for a in abbr if a not in term]
        return f"{term} ({' / '.join(extra)})" if extra else term


_ACRONYMS = {"nem": "NEM", "tne": "TNE", "turner": "Turner"}


def _sentence_case(title: str) -> str:
    """« TUMEURS NEUROENDOCRINES ET NEM » → « Tumeurs neuroendocrines et NEM »."""
    words = [_ACRONYMS.get(w.lower(), w.lower()) for w in title.split(" ")]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _flexible(form: str) -> str:
    """Motif tolérant : pluriels, orthographes britannique/américaine (tumour/tumor…)."""
    pattern = re.escape(form)
    for uk, regex in (("our", "ou?r"), ("ae", "a?e"), ("oe", "o?e"), ("ise", "i[sz]e")):
        pattern = pattern.replace(uk, regex)
    return r"(?<!\w)" + pattern + r"(?:s|es|x)?(?!\w)"


def _split_abbr(text: str) -> list[str]:
    text = text.strip()
    if not text or text in ("–", "-", "—"):
        return []
    parts = re.split(r"\s*/\s*|\s*,\s*|\s*;\s*|[()]", text)
    return [p.strip() for p in parts if p and p.strip() and p.strip() not in ("–", "-")]


def _looks_like_abbr(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9α-ωβ\-+./ ]{1,14}", text)) and any(c.isupper() for c in text)


def parse(text: str) -> list[Entry]:
    entries: list[Entry] = []
    section = ""
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or _RULE.match(line):
            continue
        sub = _SUBSECTION.match(line)
        if sub and " — " not in line:
            continue
        m = _SECTION.match(line)
        prev = lines[i - 1].strip() if i else ""
        if m and _RULE.match(prev or "x"):
            section = _sentence_case(m.group(2).strip())
            continue
        for dash in _DASHES:
            if line.count(dash) >= 2:
                abbr, fr, en = [p.strip() for p in line.split(dash, 2)]
                abbr_en = [a for p in _PAREN.findall(en) for a in _split_abbr(p) if _looks_like_abbr(a)]
                entries.append(Entry(fr, en, _split_abbr(abbr), abbr_en, section))
                break
        else:
            if "=" in line and line.count("=") == 1:
                en, fr = [p.strip() for p in line.split("=")]
                if en and fr:
                    entries.append(Entry(fr, en, section=section))
    return entries


def _terms(text: str) -> list[str]:
    """Formes d'un terme à rechercher : complet sans parenthèses, et ses variantes « / »."""
    base = _PAREN.sub("", text)
    base = re.sub(r"\s+", " ", base).strip(" ;,.")
    forms = {base.lower()}
    for alt in re.split(r"\s*/\s*|\s*;\s*", base):
        alt = alt.strip(" ,.")
        if len(alt) >= 8 or len(alt.split()) >= 2:
            forms.add(alt.lower())
    return [f for f in forms if len(f) >= 4]


class Glossary:
    def __init__(self, entries: list[Entry]) -> None:
        self.entries = entries
        self._keys: dict[str, list[tuple[re.Pattern, ...]]] = {}

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def sections(self) -> list[str]:
        seen: list[str] = []
        for e in self.entries:
            if e.section and e.section not in seen:
                seen.append(e.section)
        return seen

    def _patterns(self, language: str) -> list[tuple[Entry, list[re.Pattern]]]:
        if language not in self._keys:
            compiled = []
            for e in self.entries:
                pats = []
                term = e.fr if language == "fr" else e.en
                for form in _terms(term):
                    pats.append(re.compile(_flexible(form), re.I))
                for abbr in set(e.abbr_fr) | set(e.abbr_en):
                    if len(abbr) >= 2 and any(c.isupper() for c in abbr):
                        pats.append(re.compile(r"(?<!\w)" + re.escape(abbr) + r"(?!\w)"))
                compiled.append(pats)
            self._keys[language] = compiled
        return list(zip(self.entries, self._keys[language]))

    def relevant(self, text: str, source: str, limit: int = 80) -> list[Entry]:
        found = [e for e, pats in self._patterns(source) if any(p.search(text) for p in pats)]
        return found[:limit]

    def prompt_for(self, text: str, source: str, target: str, limit: int = 80) -> str:
        """Lignes « terme source = terme cible » des entrées présentes dans ``text``."""
        return "\n".join(f"{e.label(source)} = {e.label(target)}" for e in self.relevant(text, source, limit))

    def section_terms(self, section: str, max_chars: int = 700) -> str:
        """Termes d'un chapitre (abréviations et termes, deux langues), pour la
        reconnaissance vocale, dans la limite de ``max_chars``."""
        words: list[str] = []
        for e in self.entries:
            if e.section != section:
                continue
            for item in [*e.abbr_fr, *e.abbr_en, _PAREN.sub("", e.en).strip(), _PAREN.sub("", e.fr).strip()]:
                item = item.strip(" ;,.")
                if item and item not in words and len(item) <= 40:
                    words.append(item)
        out, size = [], 0
        for w in words:
            if size + len(w) + 2 > max_chars:
                break
            out.append(w)
            size += len(w) + 2
        return ", ".join(out)


def load(path: Optional[Path]) -> Glossary:
    if not path or not Path(path).exists():
        return Glossary([])
    return Glossary(parse(Path(path).read_text(encoding="utf-8-sig", errors="replace")))


def default_path() -> Optional[Path]:
    """Glossaire fourni avec le logiciel (dossier glossaires/)."""
    import sys

    roots = [Path(sys._MEIPASS)] if getattr(sys, "_MEIPASS", None) else []  # .exe PyInstaller
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        folder = root / "glossaires"
        if folder.is_dir():
            files = sorted(folder.glob("*.txt"))
            if files:
                return files[0]
    return None
