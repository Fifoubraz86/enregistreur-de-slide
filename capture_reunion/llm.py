"""Moteurs d'IA : traduction de qualité, résumés et lecture des diapos.

- ``LMStudio`` : modèle local (ex. Gemma) servi par LM Studio sur le PC
  (onglet « Developer », http://localhost:1234). Rien ne quitte l'ordinateur.
- ``ClaudeCode`` (claude_code.py) : Claude, via Claude Code installé sur le PC et
  l'abonnement Claude de l'utilisateur. Le texte part chez Anthropic.

Sans moteur, le logiciel se rabat sur le traducteur Argos et sur le résumé par
phrases clés.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

DEFAULT_URL = "http://localhost:1234"
LANGUAGE_NAMES = {"fr": "français", "en": "anglais"}
_THINK = re.compile(r"<think>.*?</think>", re.S)
# Marqueurs de paragraphe tels que les modèles les recopient : [1], **[1]**, [1]:, 1. ou 1)
_MARKER = re.compile(
    r"^\s*[*_]*\s*(?:\[(\d+)\]|(\d+)[.)](?=\s))[*_]*\s*[:.\-–]?\s*(.*)$"
)
_FENCE = re.compile(r"^\s*```\w*\s*$")


class LLMError(RuntimeError):
    pass


def load_glossary(path: Optional[Path]):
    """Glossaire (voir glossary.py pour les formats acceptés)."""
    from .glossary import load

    return load(path)


def _glossary_block(glossary, text: str, source: str, target: str) -> str:
    """Partie du glossaire utile pour ``text`` (tout, si c'est un simple texte)."""
    if not glossary:
        return ""
    lines = glossary if isinstance(glossary, str) else glossary.prompt_for(text, source, target)
    if not lines:
        return ""
    return ("\nTerminologie à respecter (terme source = terme à employer ; les sigles entre "
            f"parenthèses sont ceux de chaque langue) :\n{lines}")


SLIDE_JSON_SPEC = (
    "Pour chaque diapo, rends un objet JSON : "
    '{"index": n, "title": "titre", "bullets": [{"text": "puce", "level": 0}], '
    '"figures": [{"box": [x, y, largeur, hauteur], "caption": "légende"}]}. '
    "« level » vaut 0 pour une puce principale, 1 ou 2 pour une sous-puce. "
    "« figures » liste les graphiques, photos, schémas et tableaux à recopier comme image, "
    "avec leur rectangle en fractions de l'image (0 à 1, origine en haut à gauche). "
    "Recopie le texte exactement, sans le traduire ni le résumer ; ignore les logos, "
    "numéros de page et bandeaux décoratifs."
)


class Engine:
    """Base commune : tout repose sur ``chat(system, user)``."""

    label = "IA"
    batch_chars = 3000
    online = False

    def __init__(self) -> None:
        self.model: Optional[str] = None
        self.last_failures = 0  # paragraphes que la dernière traduction n'a pas pu traduire
        self.needs_loading = False

    @property
    def display_name(self) -> str:
        return self.model or self.label

    def connect(self) -> str:
        raise NotImplementedError

    def chat(self, system: str, user: str, max_tokens: int = 4096, temperature: float = 0.2) -> str:
        raise NotImplementedError

    def read_slides(self, images: list[Path]) -> list[dict]:
        raise LLMError(f"{self.label} ne sait pas lire les images.")

    def summarize_all(
        self, parts: list[tuple[str, str]], language: str, title: str = "",
        progress: Optional[Callable[[float], None]] = None,
    ) -> tuple[list[tuple[str, str]], str]:
        """Résumé de chaque partie (diapo) puis résumé global : (résumés, résumé global)."""
        summarized = []
        for i, (label, text) in enumerate(parts):
            summarized.append((label, self.summarize_part(text[:12000], language, label)))
            if progress:
                progress((i + 1) / (len(parts) + 1))
        return summarized, self.summarize_global(summarized, language, title)


class LMStudio(Engine):
    label = "LM Studio"

    def __init__(self, url: str = DEFAULT_URL, model: Optional[str] = None, timeout: float = 900) -> None:
        super().__init__()
        self.url = url.rstrip("/")
        if self.url.endswith("/v1"):
            self.url = self.url[:-3]
        self.model = model
        self.timeout = timeout
        self.vision = False

    # -- HTTP ------------------------------------------------------------
    def _request(self, path: str, payload: Optional[dict] = None, timeout: Optional[float] = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.url + path, data=data, headers={"Content-Type": "application/json"},
            method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise LLMError(f"LM Studio a répondu {exc.code} : {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise LLMError(
                f"LM Studio injoignable à {self.url}. Lancez LM Studio, chargez un modèle et "
                f"démarrez le serveur (onglet Developer)."
            ) from exc

    def model_infos(self) -> list[dict]:
        """Modèles de langage disponibles : [{"id", "loaded"}], les chargés en premier."""
        try:  # API native : indique l'état chargé / non chargé
            data = self._request("/api/v0/models", timeout=10)["data"]
            infos = [{"id": m["id"], "loaded": m.get("state") == "loaded", "vision": m.get("type") == "vlm"}
                     for m in data if m.get("type", "llm") in ("llm", "vlm")]
        except (LLMError, KeyError, TypeError):
            data = self._request("/v1/models", timeout=10).get("data", [])
            infos = [{"id": m["id"], "loaded": None, "vision": None}
                     for m in data if "embed" not in m["id"].lower()]
        infos.sort(key=lambda m: m["loaded"] is not True)
        return infos

    def models(self) -> list[str]:
        return [m["id"] for m in self.model_infos()]

    def connect(self) -> str:
        """Vérifie la connexion et le modèle ; renvoie son nom.

        Sans modèle imposé, on prend celui qui est chargé. Un modèle imposé mais pas
        encore chargé sera chargé par LM Studio à la première demande (chargement
        « Just-In-Time »), ce qui peut prendre une à deux minutes.
        """
        infos = self.model_infos()
        if not infos:
            raise LLMError("LM Studio est lancé mais aucun modèle n'est disponible.")
        if self.model:
            match = next((m for m in infos if m["id"] == self.model), None)
            if match is None:
                raise LLMError(
                    f"Le modèle « {self.model} » n'existe pas dans LM Studio. "
                    f"Modèles disponibles : {', '.join(m['id'] for m in infos)}")
        else:
            match = infos[0]
            self.model = match["id"]
        self.needs_loading = match["loaded"] is False
        self.vision = match.get("vision") is not False  # inconnu : on essaiera
        return self.model

    def chat(self, system: str, user, max_tokens: int = 4096, temperature: float = 0.2) -> str:
        """``user`` : texte, ou liste de contenus (texte + images) pour les modèles de vision."""
        if not self.model:
            self.connect()
        try:
            data = self._request("/v1/chat/completions", {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            })
        except LLMError as exc:
            if self.needs_loading and "injoignable" not in str(exc):
                raise LLMError(
                    f"LM Studio n'a pas pu charger « {self.model} » ({exc}).\n"
                    "Vérifiez dans LM Studio, onglet Developer, que le chargement à la demande "
                    "(« Just-In-Time model loading ») est activé, ou chargez le modèle vous-même. "
                    "Si un autre modèle occupe déjà la carte graphique, déchargez-le d'abord."
                ) from exc
            raise
        self.needs_loading = False
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Réponse inattendue de LM Studio : {str(data)[:200]}") from exc
        return _THINK.sub("", text).strip()

    def read_slides(self, images: list[Path]) -> list[dict]:
        """Lit chaque diapo avec un modèle de vision local (une requête par diapo)."""
        import base64

        if not self.model:
            self.connect()
        if not self.vision:
            raise LLMError(f"Le modèle « {self.model} » ne lit pas les images.")
        system = "Tu recopies fidèlement le contenu de diapositives. " + SLIDE_JSON_SPEC
        out = []
        for n, path in enumerate(images, start=1):
            data = base64.b64encode(Path(path).read_bytes()).decode()
            content = [
                {"type": "text", "text": f"Diapo {n}. Réponds uniquement par l'objet JSON de cette diapo."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
            ]
            parsed = parse_json(self.chat(system, content, max_tokens=2000))
            if isinstance(parsed, dict) and "slides" in parsed:
                parsed = (parsed["slides"] or [None])[0]
            if not isinstance(parsed, dict):
                raise LLMError(f"Réponse illisible pour la diapo {n}.")
            parsed["index"] = n
            out.append(parsed)
        return out


class _Translation:
    """Traduction et résumés, communs à tous les moteurs (mélangé dans Engine)."""

    # -- Traduction ------------------------------------------------------
    def translate(
        self,
        texts: list[str],
        source: str,
        target: str,
        glossary=None,
        progress: Optional[Callable[[float], None]] = None,
        batch_chars: Optional[int] = None,
    ) -> list[str]:
        """Traduit des paragraphes en gardant leur découpage (donc leurs horodatages)."""
        system = (
            f"Tu es traducteur médical professionnel, spécialisé en endocrinologie et oncologie. "
            f"Traduis du {LANGUAGE_NAMES.get(source, source)} vers le "
            f"{LANGUAGE_NAMES.get(target, target)} la transcription orale d'une présentation.\n"
            "Règles :\n"
            "- Traduction fidèle et complète : n'ajoute rien, ne résume pas, n'explique pas.\n"
            "- Style naturel et correct ; tu peux omettre les hésitations (euh, um) et les "
            "répétitions évidentes de l'oral.\n"
            "- Garde tels quels les sigles, noms de molécules, de gènes et d'essais, les "
            "chiffres et unités ; utilise la terminologie médicale usuelle de la langue cible.\n"
            "- Chaque paragraphe commence par un marqueur [n] : renvoie exactement les mêmes "
            "marqueurs, dans le même ordre, un paragraphe traduit par marqueur, sans aucun "
            "autre texte."
        )
        batch_chars = batch_chars or self.batch_chars
        self.last_failures = 0
        out: list[Optional[str]] = [None] * len(texts)
        batches, current, size = [], [], 0
        for i, text in enumerate(texts):
            if current and size + len(text) > batch_chars:
                batches.append(current)
                current, size = [], 0
            current.append(i)
            size += len(text)
        if current:
            batches.append(current)

        for n, batch in enumerate(batches):
            if any(texts[i].strip() for i in batch):
                user = "\n\n".join(f"[{k + 1}] {texts[i]}" for k, i in enumerate(batch))
                prompt = system + _glossary_block(glossary, user, source, target)
                parsed = _parse_markers(self.chat(prompt, user, max_tokens=3 * len(user) // 2 + 2048))
                for k, i in enumerate(batch):
                    candidate = parsed.get(k + 1)
                    if candidate and not looks_untranslated(texts[i], candidate, source, target):
                        out[i] = candidate
                # Paragraphes perdus ou restés dans la langue d'origine : un par un, sans marqueur.
                for i in batch:
                    if out[i] is None and texts[i].strip():
                        single = self.translate_document(texts[i], source, target, glossary)
                        if single and not looks_untranslated(texts[i], single, source, target):
                            out[i] = single
                        else:
                            self.last_failures += 1
            for i in batch:
                if out[i] is None:
                    out[i] = texts[i]
            if progress:
                progress((n + 1) / len(batches))
        return [t or "" for t in out]

    def translate_document(self, text: str, source: str, target: str, glossary=None) -> str:
        """Traduit un texte mis en forme (titres « ## », listes « - ») en gardant la forme."""
        system = (
            f"Tu es traducteur médical professionnel. Traduis du {LANGUAGE_NAMES.get(source, source)} "
            f"vers le {LANGUAGE_NAMES.get(target, target)}, fidèlement, en conservant exactement la "
            "mise en forme (lignes « ## », puces « - »). Réponds uniquement par la traduction."
        )
        system += _glossary_block(glossary, text, source, target)
        return self.chat(system, text, max_tokens=2 * len(text) + 2048)

    # -- Résumé ----------------------------------------------------------
    def summarize_part(self, text: str, language: str, label: str) -> str:
        lang = LANGUAGE_NAMES.get(language, language)
        system = (
            "Tu aides un médecin à garder une trace fidèle d'une présentation. "
            f"Réponds en {lang}. N'invente rien : uniquement ce qui est dit dans le passage."
        )
        user = (
            f"Passage ({label}) :\n\n{text}\n\n"
            "Résume ce passage en 1 à 3 phrases (idée principale, données chiffrées s'il y "
            "en a). S'il ne contient rien de substantiel, réponds seulement : —"
        )
        answer = self.chat(system, user, max_tokens=400)
        return "" if answer.strip(" .—-") == "" else answer

    def summarize_global(self, parts: list[tuple[str, str]], language: str, title: str = "") -> str:
        lang = LANGUAGE_NAMES.get(language, language)
        system = (
            "Tu rédiges le compte-rendu d'une présentation médicale pour un médecin "
            f"endocrinologue. Réponds en {lang}. N'invente rien : appuie-toi uniquement sur les "
            "résumés fournis ; si une information manque, ne la suppose pas."
        )
        listing = "\n".join(f"- {label} : {text}" for label, text in parts if text)
        user = (
            (f"Titre : {title}\n\n" if title else "")
            + f"Résumés successifs de la présentation :\n{listing}\n\n"
            "Rédige un résumé structuré avec exactement ces rubriques (titres précédés de "
            "« ## », éléments en liste précédés de « - ») :\n"
            "## Sujet\n## Messages clés\n## Données chiffrées\n## Conclusions et implications "
            "pratiques\nOmets « Données chiffrées » s'il n'y en a pas. Sois concis et précis."
        )
        return self.chat(system, user, max_tokens=1500)


for _name, _member in list(vars(_Translation).items()):
    if callable(_member) and not _name.startswith("__"):
        setattr(Engine, _name, _member)


def parse_json(text: str):
    """Premier objet ou tableau JSON d'une réponse (tolère ```json … ``` et du texte autour)."""
    text = _THINK.sub("", text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    for end in range(len(text), start, -1):
        if text[end - 1] in "}]":
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
    return None


def looks_untranslated(source_text: str, output: str, source: str, target: str) -> bool:
    """Vrai si la « traduction » est identique à l'original ou visiblement restée
    dans la langue d'origine (mots courants de chaque langue)."""
    from .summarize import STOPWORDS

    if output.strip().lower() == source_text.strip().lower():
        return len(source_text.split()) > 3
    src_only = STOPWORDS.get(source, set()) - STOPWORDS.get(target, set())
    tgt_only = STOPWORDS.get(target, set()) - STOPWORDS.get(source, set())
    words = re.findall(r"[a-zà-ÿ']+", output.lower())
    if len(words) < 8 or not src_only or not tgt_only:
        return False
    in_source = sum(w in src_only for w in words)
    in_target = sum(w in tgt_only for w in words)
    return in_source > 2 * in_target


def _parse_markers(text: str) -> dict[int, str]:
    result: dict[int, list[str]] = {}
    current: Optional[int] = None
    for line in text.splitlines():
        if _FENCE.match(line):
            continue
        m = _MARKER.match(line)
        if m:
            current = int(m.group(1) or m.group(2))
            result[current] = [m.group(3).strip()]
        elif current is not None and line.strip():
            result[current].append(line.strip())
    return {k: " ".join(v).strip() for k, v in result.items()}
