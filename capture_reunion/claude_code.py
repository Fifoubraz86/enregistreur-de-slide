"""Claude via Claude Code : utilise l'abonnement Claude (Pro / Max) de la personne,
sans clé API.

Claude Code (l'outil en ligne de commande d'Anthropic) doit être installé sur le PC
et connecté au compte Claude une fois (taper ``claude`` dans un terminal). Le
logiciel l'appelle ensuite en mode non interactif (``claude -p``).

Chaque appel consomme le quota de l'abonnement, et Claude Code y ajoute ses propres
instructions : on regroupe donc le travail en très peu d'appels (traduction
complète d'un coup, tous les résumés d'un coup, diapos par paquets).

Le texte et les images envoyés partent chez Anthropic : ne pas utiliser pour une
réunion contenant des données de patients.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .ffmpeg_utils import NO_WINDOW
from .llm import LANGUAGE_NAMES, SLIDE_JSON_SPEC, Engine, LLMError, parse_json

MODELS = {
    "Sonnet (recommandé)": "sonnet",
    "Opus (le plus fin, consomme plus)": "opus",
    "Haiku (le plus économe)": "haiku",
}
DEFAULT_MODEL = "sonnet"
SLIDES_PER_CALL = 6
# Options qui réduisent la consommation mais n'existent pas dans toutes les versions de
# Claude Code : si l'une est refusée, on relance sans elle.
OPTIONAL_FLAGS = {
    "--system-prompt": "Tu exécutes une tâche précise pour un logiciel de compte-rendu médical. "
                       "Réponds directement, en suivant exactement le format demandé.",
}


INSTALL_HELP = (
    "Claude Code est un outil distinct de l'application Claude (claude.ai / Claude Desktop). "
    "Pour l'installer : ouvrez PowerShell et tapez\n"
    "    irm https://claude.ai/install.ps1 | iex\n"
    "puis tapez « claude » dans un nouveau terminal pour vous connecter à votre compte. "
    "Si Claude Code est déjà installé ailleurs, indiquez son chemin dans le champ « Claude Code »."
)


def _registry_path_dirs() -> list[str]:
    """Dossiers du PATH enregistrés dans Windows (à jour même si le logiciel a été lancé
    avant l'installation de Claude Code)."""
    if sys.platform != "win32":
        return []
    import winreg

    dirs: list[str] = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                      (winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, key) as k:
                value, _ = winreg.QueryValueEx(k, "Path")
            dirs += [os.path.expandvars(d) for d in str(value).split(";") if d]
        except OSError:
            continue
    return dirs


def _is_claude_code(path: str) -> bool:
    """Vérifie que c'est bien l'outil en ligne de commande (et pas l'application de bureau)."""
    lowered = path.lower()
    if "anthropicclaude" in lowered or lowered.endswith(("claude desktop.exe",)):
        return False  # application Claude Desktop : ne surtout pas la lancer
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "claude code" in (proc.stdout + proc.stderr).lower()


def find_claude() -> Optional[str]:
    """Chemin de Claude Code : réglage manuel, PATH, PATH enregistré dans Windows,
    installateur officiel, npm, ou version fournie avec l'application de bureau."""
    env = os.environ.get("CAPTURE_REUNION_CLAUDE")
    if env and Path(env).is_file():
        return env  # choisi par l'utilisateur : on lui fait confiance
    candidates = [shutil.which("claude")]
    if sys.platform == "win32":
        home = Path.home()
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        for folder in _registry_path_dirs():
            candidates += [str(Path(folder) / "claude.exe"), str(Path(folder) / "claude.cmd")]
        candidates += [
            str(home / ".local" / "bin" / "claude.exe"),
            str(home / ".claude" / "local" / "claude.exe"),
            str(home / ".claude" / "local" / "claude.cmd"),
            str(appdata / "npm" / "claude.cmd"),
            str(local / "Programs" / "claude" / "claude.exe"),
        ]
        # Claude Code fourni avec l'application de bureau (dossier versionné).
        for root in (appdata / "Claude" / "claude-code", local / "Claude" / "claude-code"):
            if root.is_dir():
                found = sorted(root.glob("*/claude.exe"), reverse=True) + sorted(root.glob("*/*/claude.exe"), reverse=True)
                candidates += [str(p) for p in found]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate.lower() in seen or not Path(candidate).is_file():
            continue
        seen.add(candidate.lower())
        if _is_claude_code(candidate):
            return candidate
    return None


class ClaudeCode(Engine):
    label = "Claude"
    batch_chars = 40000  # ~30 min de parole en un seul appel
    online = True

    def __init__(self, model: str = DEFAULT_MODEL, executable: Optional[str] = None,
                 timeout: float = 1800) -> None:
        super().__init__()
        self.claude_model = model or DEFAULT_MODEL
        self.executable = executable
        self.timeout = timeout
        self.calls = 0
        self._unsupported: set[str] = set()

    @property
    def display_name(self) -> str:
        return f"Claude {self.claude_model} (abonnement, via Claude Code)"

    def connect(self) -> str:
        self.executable = self.executable or find_claude()
        if not self.executable:
            raise LLMError("Claude Code est introuvable.\n" + INSTALL_HELP)
        try:
            proc = subprocess.run([self.executable, "--version"], capture_output=True, text=True,
                                  timeout=60, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LLMError(f"Claude Code ne répond pas : {exc}") from exc
        if proc.returncode != 0:
            raise LLMError(f"Claude Code ne démarre pas : {(proc.stderr or proc.stdout).strip()[:300]}")
        self.model = self.display_name
        return self.model

    # -- appel -----------------------------------------------------------
    def run(self, prompt: str, tools: Optional[list[str]] = None, cwd: Optional[Path] = None,
            max_turns: int = 1) -> str:
        """Un appel non interactif ; renvoie le texte de la réponse."""
        if not self.executable:
            self.connect()
        base = [self.executable, "-p", "Réalise la tâche décrite dans le texte transmis.",
                "--output-format", "json", "--model", self.claude_model,
                "--max-turns", str(max_turns)]
        if tools:
            base += ["--allowedTools", ",".join(tools)]
        while True:
            optional = [x for flag, value in OPTIONAL_FLAGS.items()
                        if flag not in self._unsupported for x in (flag, value)]
            try:
                proc = subprocess.run(
                    base + optional, input=prompt, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=self.timeout, cwd=str(cwd) if cwd else None,
                    creationflags=NO_WINDOW)
            except subprocess.TimeoutExpired as exc:
                raise LLMError("Claude Code n'a pas répondu à temps.") from exc
            except OSError as exc:
                raise LLMError(f"Impossible de lancer Claude Code : {exc}") from exc
            err = (proc.stderr or "").lower()
            rejected = [f for f in OPTIONAL_FLAGS if f not in self._unsupported and f in err
                        and ("unknown" in err or "invalid" in err)]
            if proc.returncode != 0 and rejected:
                self._unsupported.update(rejected)
                continue
            break
        self.calls += 1
        out = (proc.stdout or "").strip()
        data = None
        try:
            data = json.loads(out) if out else None
        except json.JSONDecodeError:
            pass
        if isinstance(data, dict):
            if data.get("is_error") or data.get("subtype", "success") != "success":
                raise LLMError(f"Claude a renvoyé une erreur : {str(data.get('result') or data)[:300]}")
            return str(data.get("result") or "").strip()
        if proc.returncode != 0:
            detail = (proc.stderr or out).strip()[:400]
            if "login" in detail.lower() or "auth" in detail.lower():
                detail += "\nConnectez-vous : tapez « claude » dans un terminal, puis /login."
            raise LLMError(f"Claude Code a échoué : {detail}")
        return out

    def chat(self, system: str, user: str, max_tokens: int = 4096, temperature: float = 0.2) -> str:
        return self.run(f"<consignes>\n{system}\n</consignes>\n\n{user}")

    # -- regroupements pour économiser le quota ----------------------------
    def summarize_all(
        self, parts: list[tuple[str, str]], language: str, title: str = "",
        progress: Optional[Callable[[float], None]] = None,
    ) -> tuple[list[tuple[str, str]], str]:
        """Tous les résumés (par diapo + global) en un seul appel."""
        lang = LANGUAGE_NAMES.get(language, language)
        listing = "\n\n".join(f"### {label}\n{text[:12000]}" for label, text in parts)
        prompt = (
            "<consignes>\nTu rédiges le compte-rendu fidèle d'une présentation médicale pour un "
            f"médecin endocrinologue. Réponds en {lang}. N'invente rien : uniquement ce qui est dit.\n"
            "</consignes>\n\n"
            + (f"Titre : {title}\n\n" if title else "")
            + f"Transcription, découpée par diapo :\n\n{listing}\n\n"
            "Réponds uniquement par un objet JSON :\n"
            '{"parts": {"<libellé exact de chaque partie>": "résumé en 1 à 3 phrases, ou \\"\\" si '
            'rien de substantiel"}, "global": "résumé structuré"}\n'
            "Le résumé global utilise exactement ces rubriques, titres précédés de « ## » et éléments "
            "en liste précédés de « - » : ## Sujet / ## Messages clés / ## Données chiffrées / "
            "## Conclusions et implications pratiques (omets « Données chiffrées » s'il n'y en a pas)."
        )
        data = parse_json(self.run(prompt))
        if progress:
            progress(1.0)
        if not isinstance(data, dict) or "global" not in data:
            raise LLMError("Réponse de Claude illisible pour les résumés.")
        given = data.get("parts") or {}
        summarized = [(label, str(given.get(label, "")).strip()) for label, _ in parts]
        return summarized, str(data["global"]).strip()

    def read_slides(self, images: list[Path]) -> list[dict]:
        """Lit les diapos par paquets (Claude Code les ouvre avec son outil de lecture)."""
        results: list[dict] = []
        for start in range(0, len(images), SLIDES_PER_CALL):
            batch = images[start:start + SLIDES_PER_CALL]
            listing = "\n".join(f"- diapo {start + i + 1} : {Path(p).resolve()}" for i, p in enumerate(batch))
            prompt = (
                "Ouvre chacune de ces images de diapositives avec l'outil Read et recopie leur contenu.\n"
                f"{listing}\n\n{SLIDE_JSON_SPEC}\n"
                'Réponds uniquement par un objet JSON : {"slides": [ ... ]}, une entrée par diapo, '
                "avec « index » égal au numéro indiqué."
            )
            data = parse_json(self.run(prompt, tools=["Read"], cwd=Path(batch[0]).parent,
                                       max_turns=len(batch) + 4))
            slides = data.get("slides") if isinstance(data, dict) else data
            if not isinstance(slides, list):
                raise LLMError("Réponse de Claude illisible pour la lecture des diapos.")
            results += [s for s in slides if isinstance(s, dict)]
        return results
