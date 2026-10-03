"""Résumé automatique, sans IA : on garde les phrases les plus représentatives.

Méthode TextRank : chaque phrase est comparée à toutes les autres selon les mots
qu'elles partagent (pondérés par leur rareté, TF-IDF). Les phrases qui
ressemblent au plus grand nombre d'autres résument le mieux l'ensemble. Elles
sont rendues dans l'ordre où elles ont été prononcées. Le résumé est donc
fait de phrases réellement dites, pas reformulées.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

STOPWORDS = {
    "fr": set("""
a à afin ai aie aient aies ait alors as au aucun aucune aujourd auquel aussi autre autres aux
auxquelles auxquels avaient avais avait avant avec avez aviez avions avoir avons ayant bah bien
bon c ça car ce ceci cela celle celles celui cependant certain certaine certains ces cet cette
ceux chaque chez ci comme comment d dans de des déjà depuis donc dont du elle elles en encore
entre est et étaient étais était étant été être eu eue eux fait faire faut fois font hein il
ils j je jusqu l la là laquelle le lequel les lesquelles lesquels leur leurs lors lui m ma mais
me même mes moi moins mon n ne ni non nos notre nous on ont or ou où oui par parce pas peu peut
peuvent plus pour pourquoi puis qu quand que quel quelle quelles quels qui quoi s sa sans se
sera seront ses si sien soi soit sommes son sont sous suis sur t ta te tes toi ton tous tout
toute toutes très tu un une va vais voilà vos votre vous vu y donc euh voici ainsi alors
aller avez beaucoup chose choses dire dit enfin grand grande grands petit petite parfois
souvent toujours vraiment voir vais veux peut-être plutôt quelque quelques autre fois
""".split()),
    "en": set("""
a about above after again against all also am an and any are aren as at be because been before
being below between both but by can cannot could couldn did didn do does doesn doing don down
during each few for from further get gets got had hadn has hasn have haven having he her here
hers herself him himself his how i if in into is isn it its itself just know let like ll m me
might more most much must my myself no nor not now of off on once only or other ought our ours
ourselves out over own re really right s said same say says she should shouldn so some sort
such t than that the their theirs them themselves then there these they thing things think
this those through to too uh um under until up us ve very was wasn way we well were weren what
when where which while who whom why will with won would wouldn yeah yes you your yours yourself
yourselves going actually basically okay ok also look looking looked sometimes often
different many lot lots maybe quite still even back mean meaning means trying try want need
see use used using make made take give come kind part probably perhaps already another every
""".split()),
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[\"«(]?[A-ZÀ-ÖØ-Þ0-9])")
_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+")


@dataclass
class Summary:
    sentences: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def _words(sentence: str, stop: set[str]) -> list[str]:
    words = []
    for w in _WORD.findall(sentence.lower()):
        w = w.strip("'’-")
        if len(w) > 2 and w not in stop and "'" not in w and "’" not in w:
            words.append(w)
    return words


def summarize(texts: list[str], language: str = "fr", max_sentences: int = 0) -> Summary:
    """Résumé d'une liste de paragraphes (``max_sentences`` = 0 : longueur automatique)."""
    stop = STOPWORDS.get(language, set()) | STOPWORDS["fr"] | STOPWORDS["en"]
    sentences = [s for t in texts for s in split_sentences(t)]
    candidates = [(i, s, _words(s, stop)) for i, s in enumerate(sentences)]
    candidates = [c for c in candidates if len(c[2]) >= 4 and len(c[1].split()) >= 6]
    if not candidates:
        return Summary()

    vocab: dict[str, int] = {}
    for _, _, words in candidates:
        for w in words:
            vocab.setdefault(w, len(vocab))
    doc_freq = Counter(w for _, _, words in candidates for w in set(words))
    n = len(candidates)
    matrix = np.zeros((n, len(vocab)))
    for row, (_, _, words) in enumerate(candidates):
        for w, count in Counter(words).items():
            matrix[row, vocab[w]] = count * math.log((1 + n) / (1 + doc_freq[w]))
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms == 0, 1, norms)

    similarity = matrix @ matrix.T
    np.fill_diagonal(similarity, 0)
    scores = _pagerank(similarity)

    k = max_sentences or max(3, min(10, round(n * 0.12)))
    best = sorted(np.argsort(-scores)[:k])
    chosen = [candidates[i][1] for i in best]

    # Mots-clés : les termes (hors mots courants) les plus répétés.
    counts = Counter(w for _, _, words in candidates for w in words if len(w) > 3)
    keywords = [w for w, c in counts.most_common(10) if c >= 2]
    return Summary(chosen, keywords)


def _pagerank(similarity: np.ndarray, damping: float = 0.85, iterations: int = 100) -> np.ndarray:
    n = similarity.shape[0]
    rows = similarity.sum(axis=1, keepdims=True)
    transition = np.where(rows > 0, similarity / np.where(rows == 0, 1, rows), 1.0 / n)
    scores = np.full(n, 1.0 / n)
    for _ in range(iterations):
        new = (1 - damping) / n + damping * transition.T @ scores
        if np.abs(new - scores).sum() < 1e-9:
            return new
        scores = new
    return scores
