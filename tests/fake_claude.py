#!/usr/bin/env python3
"""Faux Claude Code pour les tests : imite `claude -p --output-format json`."""
import json
import os
import re
import sys

args = sys.argv[1:]
log = os.environ.get("FAKE_CLAUDE_LOG")
if "--version" in args:
    print("2.1.0 (Claude Code)")
    sys.exit(0)
if os.environ.get("FAKE_CLAUDE_REJECT_SYSTEM") and "--system-prompt" in args:
    print("error: unknown option '--system-prompt'", file=sys.stderr)
    sys.exit(1)
prompt = sys.stdin.read()
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "cwd": os.getcwd(), "chars": len(prompt)}) + "\n")


def fr(text):
    return f"Traduction française de « {' '.join(text.split()[:3])} » pour les médecins."


if "marqueur [n]" in prompt:
    items = re.findall(r"^\[(\d+)\] (.*)$", prompt, re.M)
    result = "\n\n".join(f"[{n}] {fr(t)}" for n, t in items)
elif "mise en forme" in prompt:
    body = prompt.split("</consignes>", 1)[-1].strip()
    result = "\n".join(l if l.startswith("##") else "- " + fr(l) for l in body.splitlines() if l.strip())
elif '"parts"' in prompt:
    labels = re.findall(r"^### (.+)$", prompt, re.M)
    result = "```json\n" + json.dumps({
        "parts": {label: f"Résumé de {label}." for label in labels},
        "global": "## Sujet\n- TNE hépatiques\n## Messages clés\n- **Survie** prolongée",
    }, ensure_ascii=False) + "\n```"
elif "outil Read" in prompt:
    slides = []
    for n, path in re.findall(r"^- diapo (\d+) : (.+)$", prompt, re.M):
        slides.append({"index": int(n), "title": f"Titre lu par Claude {n}",
                       "bullets": [{"text": "Première puce", "level": 0},
                                   {"text": "Sous-puce", "level": 1}],
                       "figures": [{"box": [0.6, 0.25, 0.35, 0.6], "caption": "graphique"}]})
    result = "Voici le résultat :\n" + json.dumps({"slides": slides}, ensure_ascii=False)
else:
    result = "prêt"
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result,
                  "usage": {"input_tokens": len(prompt) // 4}}))
