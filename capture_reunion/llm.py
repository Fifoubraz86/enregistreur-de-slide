"""IA locale via LM Studio : traduction de qualité et résumé structuré.

LM Studio fait tourner un modèle (ex. Gemma) sur le PC et expose un serveur
local compatible OpenAI (onglet « Developer », http://localhost:1234). Rien ne
quitte l'ordinateur. Si LM Studio n'est pas lancé, le logiciel se rabat sur le
traducteur Argos et sur le résumé par phrases clés.
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
_MARKER = re.compile(r"^\s*\[(\d+)\]\s?(.*)$")


class LLMError(RuntimeError):
    pass


def load_glossary(path: Optional[Path], max_lines: int = 300) -> str:
    """Glossaire « terme = traduction », une ligne par terme (lignes # ignorées)."""
    if not path or not Path(path).exists():
        return ""
    lines = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return "\n".join(lines[:max_lines])


class LMStudio:
    def __init__(self, url: str = DEFAULT_URL, model: Optional[str] = None, timeout: float = 900) -> None:
        self.url = url.rstrip("/")
        if self.url.endswith("/v1"):
            self.url = self.url[:-3]
        self.model = model
        self.timeout = timeout

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

    def models(self) -> list[str]:
        """Modèles disponibles, ceux déjà chargés en premier."""
        try:  # API native : indique l'état chargé / non chargé
            data = self._request("/api/v0/models", timeout=10)["data"]
            llms = [m for m in data if m.get("type", "llm") in ("llm", "vlm")]
            llms.sort(key=lambda m: m.get("state") != "loaded")
            return [m["id"] for m in llms]
        except (LLMError, KeyError, TypeError):
            data = self._request("/v1/models", timeout=10).get("data", [])
            return [m["id"] for m in data if "embed" not in m["id"].lower()]

    def connect(self) -> str:
        """Vérifie la connexion et choisit le modèle ; renvoie son nom."""
        models = self.models()
        if not models:
            raise LLMError("LM Studio est lancé mais aucun modèle n'est disponible.")
        if not self.model or self.model not in models:
            self.model = models[0]
        return self.model

    def chat(self, system: str, user: str, max_tokens: int = 4096, temperature: float = 0.2) -> str:
        if not self.model:
            self.connect()
        data = self._request("/v1/chat/completions", {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        })
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Réponse inattendue de LM Studio : {str(data)[:200]}") from exc
        return _THINK.sub("", text).strip()

    # -- Traduction ------------------------------------------------------
    def translate(
        self,
        texts: list[str],
        source: str,
        target: str,
        glossary: str = "",
        progress: Optional[Callable[[float], None]] = None,
        batch_chars: int = 3000,
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
        if glossary:
            system += f"\nGlossaire à respecter (terme = traduction) :\n{glossary}"

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
                parsed = _parse_markers(self.chat(system, user, max_tokens=4 * len(user) // 3 + 512))
                for k, i in enumerate(batch):
                    if parsed.get(k + 1):
                        out[i] = parsed[k + 1]
                # Paragraphes perdus par le modèle : on les refait un par un.
                for i in batch:
                    if out[i] is None and texts[i].strip():
                        single = _parse_markers(self.chat(system, f"[1] {texts[i]}"))
                        out[i] = single.get(1) or texts[i]
            for i in batch:
                if out[i] is None:
                    out[i] = texts[i]
            if progress:
                progress((n + 1) / len(batches))
        return [t or "" for t in out]

    def translate_document(self, text: str, source: str, target: str, glossary: str = "") -> str:
        """Traduit un texte mis en forme (titres « ## », listes « - ») en gardant la forme."""
        system = (
            f"Tu es traducteur médical professionnel. Traduis du {LANGUAGE_NAMES.get(source, source)} "
            f"vers le {LANGUAGE_NAMES.get(target, target)}, fidèlement, en conservant exactement la "
            "mise en forme (lignes « ## », puces « - »). Réponds uniquement par la traduction."
        )
        if glossary:
            system += f"\nGlossaire à respecter (terme = traduction) :\n{glossary}"
        return self.chat(system, text, max_tokens=2 * len(text) + 512)

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


def _parse_markers(text: str) -> dict[int, str]:
    result: dict[int, list[str]] = {}
    current: Optional[int] = None
    for line in text.splitlines():
        m = _MARKER.match(line)
        if m:
            current = int(m.group(1))
            result[current] = [m.group(2).strip()]
        elif current is not None and line.strip():
            result[current].append(line.strip())
    return {k: " ".join(v).strip() for k, v in result.items()}
