"""Ligne de commande : python -m capture_reunion <commande> ...

Sans argument, l'interface graphique s'ouvre.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from .session import Session
from .slides import DetectorSettings


def _zone(value: str):
    parts = [float(p) for p in value.replace(";", ",").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("format attendu : x,y,largeur,hauteur (fractions 0-1)")
    return tuple(parts)


def _clock(session: Session, value: str) -> datetime:
    t = datetime.strptime(value, "%H:%M:%S" if value.count(":") == 2 else "%H:%M").time()
    day = session.start_datetime.date() if session.start_datetime else datetime.now().date()
    return datetime.combine(day, t)


def _load_session(path: Path) -> Session:
    path = Path(path)
    if path.is_file():
        return Session.for_video(path)
    return Session.load(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capture_reunion", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fenetres", help="liste les fenêtres et écrans capturables (Windows)")

    rec = sub.add_parser("enregistrer", help="enregistre une fenêtre (Windows)")
    rec.add_argument("fenetre", help="partie du titre ou de l'application (ex. Zoom), "
                     "ou ecran1, ecran2… pour un écran entier")
    rec.add_argument("--dossier", type=Path, default=Path.home() / "Videos" / "Capture reunion")
    rec.add_argument("--ips", type=int, default=15, help="images par seconde (défaut 15)")
    rec.add_argument("--sans-son", action="store_true")
    rec.add_argument("--sans-diapos", action="store_true")
    rec.add_argument("--zone", type=_zone)
    rec.add_argument("--duree", type=float, help="arrêt automatique après N minutes")

    ext = sub.add_parser("extraire", help="extrait les diapos d'une vidéo")
    ext.add_argument("video", type=Path, help="fichier vidéo ou dossier de session")
    ext.add_argument("--zone", type=_zone, help="x,y,largeur,hauteur en fractions (0-1)")
    ext.add_argument("--stabilite", type=float, default=2.0, help="secondes de stabilité (défaut 2)")
    ext.add_argument("--seuil", type=float, default=0.97, help="seuil SSIM de changement (défaut 0.97)")

    tr = sub.add_parser("transcrire", help="transcrit le son sur ce PC (sans Plaud)")
    tr.add_argument("session", type=Path, help="dossier de session ou fichier vidéo")
    tr.add_argument("--modele", default="small",
                    help="small (rapide), medium (précis) ou large-v3-turbo (très précis)")
    tr.add_argument("--langue", choices=["auto", "fr", "en"], default="auto",
                    help="langue parlée (défaut : détection automatique)")
    tr.add_argument("--traduire", action="store_true",
                    help="crée aussi une version traduite (anglais ↔ français)")
    tr.add_argument("--sans-ia", action="store_true",
                    help="ne pas utiliser LM Studio (traduction Argos, résumé par phrases clés)")
    tr.add_argument("--lmstudio", default="http://localhost:1234", help="adresse du serveur LM Studio")
    tr.add_argument("--modele-ia", help="modèle LM Studio à utiliser (défaut : celui qui est chargé)")
    tr.add_argument("--glossaire", type=Path, help="fichier « terme = traduction », une ligne par terme")
    tr.add_argument("--vocabulaire", default="",
                    help="mots difficiles à reconnaître (noms, termes médicaux), séparés par des virgules")

    for name, help_text in (
        ("pptx", "crée le PowerPoint des diapos"),
        ("compte-rendu", "crée le document Word diapo + transcription"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("session", type=Path, help="dossier de session")
        p.add_argument("--transcription", type=Path,
                       help="export Plaud (.txt, .srt, .vtt, .docx) ; par défaut, celle du logiciel")
        p.add_argument("--debut-plaud", help="heure de début de l'enregistrement Plaud (HH:MM:SS)")
        p.add_argument("--decalage", type=float,
                       help="secondes à ajouter aux horodatages Plaud (prioritaire sur --debut-plaud)")
        if name == "pptx":
            p.add_argument("--modele", type=Path, help="modèle .potx ou .pptx")

    args = parser.parse_args(argv)

    if args.cmd == "fenetres":
        from .windows import list_monitors, list_windows

        windows = list_windows()
        if not windows:
            print("Aucune fenêtre trouvée (cette commande ne fonctionne que sous Windows).")
        for m in list_monitors():
            print(f"{'ecran' + str(m.index):>10}  {m.label}")
        for w in windows:
            print(f"{w.hwnd:>10}  {w.process:<18} {w.title}")
        return 0

    if args.cmd == "enregistrer":
        return _record(args)

    if args.cmd == "extraire":
        from .workflow import extract_slides

        session = _load_session(args.video)
        settings = DetectorSettings(
            zone=args.zone, stable_duration=args.stabilite, change_threshold=args.seuil
        )
        last = [-1]

        def progress(p: float) -> None:
            pct = int(p * 100)
            if pct != last[0]:
                last[0] = pct
                print(f"\rAnalyse : {pct:3d} %", end="", flush=True)

        extract_slides(session, settings, progress)
        print(f"\n{len(session.slides)} diapos dans {session.slides_dir}")
        return 0

    if args.cmd == "transcrire":
        from .llm import load_glossary
        from .transcribe import transcribe_session

        session = _load_session(args.session)
        last = [-1]

        def progress(p: float) -> None:
            pct = int(p * 100)
            if pct != last[0]:
                last[0] = pct
                print(f"\rTranscription : {pct:3d} %", end="", flush=True)

        out = transcribe_session(session, args.modele, args.vocabulaire, progress,
                                 language=None if args.langue == "auto" else args.langue,
                                 translate=args.traduire, use_llm=not args.sans_ia,
                                 llm_url=args.lmstudio, llm_model=args.modele_ia,
                                 glossary=load_glossary(args.glossaire))
        print(f"\nCréé : {out}")
        if session.ai.get("model"):
            print(f"Résumé rédigé par l'IA locale : {session.ai['model']}")
        for warning in session.ai.get("warnings", []):
            print(f"Attention : {warning}")
        return 0

    from .workflow import compute_offset, make_pptx, make_reports

    session = _load_session(args.session)
    plaud = _clock(session, args.debut_plaud) if args.debut_plaud else None
    offset = compute_offset(session, plaud, args.decalage)
    if args.cmd == "pptx":
        out = make_pptx(session, args.modele, args.transcription, offset)
    else:
        for out in make_reports(session, args.transcription, offset):
            print(f"Créé : {out}")
        return 0
    print(f"Créé : {out}")
    return 0


def _record(args) -> int:
    import re

    from .recorder import Recorder
    from .windows import list_monitors, list_windows

    needle = args.fenetre.lower()
    screen = re.fullmatch(r"[ée]cran\s*(\d+)", needle)
    if screen:
        monitors = [m for m in list_monitors() if m.index == int(screen.group(1))]
        if not monitors:
            print(f"Pas d'écran n°{screen.group(1)}. Voir : capture_reunion fenetres")
            return 1
        target, hwnd, monitor_index = monitors[0], None, monitors[0].index
    else:
        matches = [w for w in list_windows() if needle in w.title.lower() or needle in w.label.lower()]
        if not matches:
            print(f"Aucune fenêtre ne correspond à « {args.fenetre} ». Voir : capture_reunion fenetres")
            return 1
        target, hwnd, monitor_index = matches[0], matches[0].hwnd, None
        if target.is_visio:
            print("Rappel : si c'est vous qui partagez votre écran, la fenêtre Zoom/Teams ne "
                  "montre que votre vidéo. Utilisez alors ecran1 ou la fenêtre partagée.")
    recorder = Recorder(
        hwnd,
        args.dossier,
        window_title=target.title,
        monitor_index=monitor_index,
        fps=args.ips,
        record_audio=not args.sans_son,
        detect_slides=not args.sans_diapos,
        detector_settings=DetectorSettings(zone=args.zone),
        on_slide=lambda s: print(f"\nNouvelle diapo n°{s.index}"),
    )
    recorder.start()
    if recorder.audio_warning:
        print(recorder.audio_warning)
    print(f"Enregistrement de « {target.title} » — Ctrl+C pour arrêter.")
    try:
        while True:
            time.sleep(1)
            print(f"\r{int(recorder.elapsed // 60):02d}:{int(recorder.elapsed % 60):02d}  "
                  f"diapos : {recorder.slide_count}", end="", flush=True)
            if args.duree and recorder.elapsed >= args.duree * 60:
                break
            if recorder.window_closed:
                print("\nLa fenêtre a été fermée.")
                break
    except KeyboardInterrupt:
        pass
    print("\nFinalisation…")
    session = recorder.stop()
    print(f"Enregistré dans {session.folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
