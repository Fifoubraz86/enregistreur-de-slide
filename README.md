# Capture réunion

Logiciel Windows qui :

1. **enregistre une fenêtre précise** (Zoom, Teams, onglet Chrome…) **même si vous travaillez sur autre chose** : la fenêtre peut rester cachée derrière les autres ;
2. **enregistre le son de l'ordinateur** (le son de la réunion, propre, sans micro) ;
3. **photographie automatiquement chaque diapo** du diaporama qui défile ;
4. **crée un PowerPoint « maison »** à partir de ces photos, avec votre modèle (.potx : logo, bandeau CHU…) ;
5. **transcrit la réunion sur votre PC**, sans Plaud, sans abonnement et sans rien envoyer sur Internet :
   - le logiciel sait qui parle : « Moi » (votre micro) ou « Participants » (le son de l'ordinateur) ;
   - la langue est détectée automatiquement ;
   - le texte est rendu en paragraphes et se termine par un **résumé** (phrases clés et mots-clés) ;
   - sur option, une **version traduite** anglais ↔ français est produite ;
6. **ou se couple au Plaud** : chaque phrase de la transcription est rattachée à la diapo affichée à ce moment-là (dans les notes du PowerPoint et dans un compte-rendu Word « diapo + ce qui a été dit »).

## Installation (une seule fois)

1. Installez **Python 3.11 ou plus récent** depuis <https://www.python.org/downloads/> en cochant **« Add python.exe to PATH »**.
2. Téléchargez ce dossier (bouton vert *Code* → *Download ZIP* sur GitHub), puis décompressez-le.
3. Double-cliquez sur **`installer.bat`** (quelques minutes).

Ensuite, double-cliquez sur **`lancer.bat`** pour démarrer le logiciel.

ffmpeg est fourni automatiquement (paquet `imageio-ffmpeg`), vous n'avez rien à installer de plus.

> Pour obtenir un vrai `CaptureReunion.exe` (à copier sur un autre PC sans Python) : double-cliquez sur `construire_exe.bat`. Le résultat se trouve dans `dist\CaptureReunion\`.

## Utilisation

### 1. Enregistrer

- Choisissez quoi enregistrer dans la liste (Zoom, Teams, navigateurs et PowerPoint en premier, puis les écrans entiers) :
  - **quelqu'un d'autre partage son écran** → la fenêtre Zoom / Teams ;
  - **c'est vous qui partagez** → **« Écran entier »** ou la fenêtre que vous partagez (ex. « Diaporama PowerPoint »). Pendant votre partage, Zoom cache sa fenêtre de réunion et n'affiche plus qu'une vignette avec votre vidéo : l'enregistrer ne montrerait que votre visage.
- Facultatif, mais conseillé : **« Définir la zone des diapos… »**, puis tracez un rectangle autour de la diapo, sans les vignettes des participants. Les visages qui bougent ne déclenchent alors plus de fausses détections.
- Cliquez sur **Démarrer**. Vous pouvez passer à autre chose : la dernière diapo détectée s'affiche en miniature.

Deux règles à respecter :

| Situation | Que faire |
|---|---|
| Fenêtre **réduite** dans la barre des tâches | Windows ne la dessine plus, donc l'image se fige. Laissez-la ouverte derrière les autres fenêtres, ou sur un 2ᵉ écran. Le logiciel affiche un avertissement si cela arrive. |
| **Vous partagez votre écran dans Zoom/Teams** | Choisissez « Écran entier » ou la fenêtre partagée, pas la fenêtre Zoom. Avec « Écran entier », tout ce qui s'affiche sur cet écran est enregistré. |
| **Onglet Chrome** | Seul l'onglet actif d'une fenêtre est dessiné. Détachez l'onglet de la réunion dans sa propre fenêtre (glissez l'onglet hors de la barre d'onglets). |

**Arrêt automatique** (activé par défaut, réglable) : après 3 minutes sans activité, c'est-à-dire sans image qui change notablement ni son (participants ou micro), l'enregistrement s'arrête tout seul et la fin morte est coupée (10 secondes de marge gardées). Pendant l'enregistrement, des indicateurs montrent le niveau du son de l'ordinateur et du micro. Si aucun son de l'ordinateur n'arrive après 30 secondes, une alerte suggère que le son est coupé.

**Mise en veille :** pendant l'enregistrement, le logiciel empêche Windows de se mettre en veille et d'éteindre l'écran. Il rend la main à l'arrêt. Attention : refermer l'écran d'un portable déclenche quand même la mise en veille si Windows est réglé ainsi (Paramètres → Système → Alimentation → « Action à la fermeture du capot »).

**Volume à 0 ou muet :** le logiciel enregistre le son que Windows envoie à la sortie audio. Il ne contourne pas un réglage qui coupe le son *avant* : le volume de Zoom/Teams au minimum, ou le volume de l'application dans le mélangeur de Windows. Avec le volume général de Windows à 0 ou en muet, le résultat dépend du pilote audio : faites un essai de 30 secondes (l'indicateur et l'alerte vous le diront). Pour ne rien entendre tout en enregistrant à coup sûr, le plus fiable est de garder le volume normal et de brancher un casque sans le porter.

À l'arrêt, un dossier est créé dans `Vidéos\Capture reunion\` :

```
2026-10-03_14h30 - Zoom Meeting\
├── video.mp4          vidéo + son
├── son.mp3            son seul (mélange micro + ordinateur)
├── piste_moi.m4a, piste_participants.m4a   pistes séparées pour la transcription
├── diapos\            diapo_001_00h00m12s.png, diapo_002_…
└── session.json       heure de début, horodatage des diapos
```

Cochez **« Enregistrer mon micro »** pour que votre propre voix soit enregistrée : le son de l'ordinateur ne contient que les autres participants. Un **casque** est conseillé, sinon le micro capte aussi les haut-parleurs. Le logiciel retire alors ces doublons de la transcription.

### 2. Diapos et compte-rendu

Ouvrez l'onglet **« 2. Diapos et compte-rendu »** :

- **Extraire les diapos de la vidéo** : refait la détection, par exemple avec une autre zone, ou sur une vidéo enregistrée ailleurs (bouton « Ouvrir une autre vidéo… »).
- **Transcrire** (transcription sur ce PC) :
  - **Qualité** : *Rapide* (small) suffit souvent. *Précis* (medium) ou *Très précis* (large-v3-turbo) reconnaissent mieux le vocabulaire médical, mais sont plus lents. Pour 1 h de réunion, comptez de l'ordre de 10 à 20 min en Rapide sur un portable récent, nettement plus en Précis.
  - **Langue parlée** : laissez « Détection automatique ». Imposer « Français » sur une conférence en anglais ferait *traduire* la reconnaissance vocale, et mal.
  - **Version traduite** : conférence en anglais → version française, conférence en français → version anglaise. La traduction tourne aussi sur le PC (modèles libres Argos/OPUS-MT, environ 100 Mo, téléchargés une fois). C'est une traduction automatique : elle est fidèle sur le fond, mais pas toujours élégante.
  - **IA locale LM Studio** (recommandé avec une carte graphique) : si LM Studio est lancé avec un modèle chargé et son serveur démarré (onglet *Developer*), il fournit :
    - un **résumé rédigé et structuré** (sujet, messages clés, données chiffrées, conclusions) ;
    - un **« En bref » pour chaque diapo** ;
    - une **traduction soignée**.

    **Modèle IA** : cliquez sur **Actualiser** pour lister les modèles de LM Studio (« ● chargé » indique ceux déjà en mémoire), puis choisissez-en un. S'il n'est pas chargé, LM Studio le charge à la première demande ; il faut pour cela que le chargement à la demande (« Just-In-Time model loading ») soit activé dans l'onglet Developer. Le premier traitement attend alors une à deux minutes. Si un autre modèle occupe déjà la carte graphique, déchargez-le d'abord.

    Le bouton **Tester** vérifie la connexion et charge le modèle choisi. Si LM Studio n'est pas lancé, le logiciel utilise automatiquement le traducteur Argos et le résumé par phrases clés. Les phrases réellement prononcées restent toujours en annexe, pour vérifier ce que l'IA affirme.
  - **Moteur IA** : trois choix.
    - **LM Studio** : IA locale, rien ne quitte le PC, gratuit.
    - **Claude — votre abonnement** : passe par Claude Code, l'outil en ligne de commande d'Anthropic, et utilise votre abonnement Claude (Pro ou Max), sans clé API. Il faut l'installer une fois (<https://claude.com/claude-code>), puis taper `claude` dans un terminal pour vous connecter. Le texte et les images partent chez Anthropic et consomment le quota de l'abonnement : le logiciel regroupe le travail en très peu d'appels, avec Sonnet par défaut, plus économe qu'Opus. Votre consommation réelle s'affiche dans Claude Code avec `/usage`.
    - **Aucune** : traduction Argos et résumé par phrases clés.

    Cochez **« Réunion avec données de patients »** pour qu'aucun texte ni aucune image ne parte en ligne : Claude est alors désactivé, même s'il est choisi.
  - **Glossaire** : le glossaire d'endocrinologie, diabétologie et TNE fourni dans `glossaires/` est utilisé par défaut. Il comporte environ 460 termes, au format `ABRÉV — Terme français — English term (ABBR)`. À chaque traduction, seuls les termes présents dans le passage sont transmis à l'IA, pluriels et orthographes britannique ou américaine compris. Le format simple `terme anglais = terme français` est aussi accepté.
  - **Thème de la réunion** : un chapitre du glossaire (ex. « Tumeurs neuroendocrines… »). Ses termes sont soufflés à la reconnaissance vocale, pour mieux reconnaître les abréviations et les noms de molécules.
  - **Vocabulaire** : mots difficiles (noms propres, molécules, sigles) pour aider la reconnaissance.
  - Si le téléchargement du traducteur Argos est refusé (erreur 403), le message indique un lien à ouvrir dans le navigateur et le dossier où déposer le fichier.
  - Le modèle est téléchargé **une seule fois** (0,5 à 1,6 Go, dans `%LOCALAPPDATA%\CaptureReunion\modeles`). Ensuite, tout fonctionne hors ligne.
  - Résultat :
    - `transcription.txt` : paragraphes et résumé ;
    - `transcription.srt` : horodatage fin, pour rattacher le texte aux diapos ;
    - si la traduction est demandée, `transcription_fr.txt` (ou `_en`).

    Ces fichiers sont utilisés automatiquement pour le PowerPoint et le compte-rendu.
  - **Le résumé est calculé sans IA** (méthode TextRank) : il retient les phrases réellement prononcées qui représentent le mieux l'ensemble. Il ne les reformule pas.
- **Modèle PowerPoint** : votre .potx ou .pptx. Chaque photo est centrée sur la diapo. Si votre modèle comporte une disposition avec un espace réservé « Image », la photo est placée dans ce cadre.
- **Transcription** : celle du logiciel est remplie automatiquement. Vous pouvez aussi choisir un export Plaud : exportez la transcription depuis l'appli Plaud **avec les horodatages** (TXT, SRT ou DOCX).
- **Heure de début du Plaud** (export Plaud uniquement) : l'heure à laquelle vous avez lancé l'enregistrement Plaud (visible dans l'appli). Le logiciel connaît l'heure de début de la vidéo et calcule le décalage. « Ajustement fin » permet de corriger de quelques secondes.
- **Créer le PowerPoint** : une diapo par photo. Les notes du présentateur contiennent l'heure d'affichage et, si une transcription est fournie, ce qui a été dit.
- **PowerPoint reconstruit (texte modifiable)** : recrée de vraies diapos à partir des captures. Le titre et les puces (avec leurs niveaux) deviennent du texte modifiable, et les graphiques, photos et schémas sont découpés et placés comme images.
  - **Masque** : celui du champ « Modèle PowerPoint », ou à défaut le masque sobre fourni (`modeles/masque_par_defaut.pptx`, modifiable dans PowerPoint).
  - **Lecture des diapos** : par le moteur IA s'il voit les images (Claude, ou un modèle de vision dans LM Studio). Sinon par OCR sur le PC (hors ligne), qui lit bien le texte mais devine la mise en forme plus grossièrement.
  - **Lectures mémorisées** dans `diapos_lues.json` : refaire le PowerPoint avec un autre masque ne relit pas les diapos et ne reconsomme pas de quota.
  - Les notes de chaque diapo contiennent la transcription et le nom de la capture d'origine. Relisez toujours le résultat : une lecture automatique peut se tromper.
- **Créer le compte-rendu Word** : chaque diapo suivie de la transcription correspondante, puis le résumé. Si une traduction existe, `compte-rendu_fr.docx` (ou `_en`) est créé en plus.

## Comment la détection des diapos fonctionne

Une image est analysée chaque seconde :

- quand l'image reste **stable environ 2 s** et que le contenu précédent a **disparu**, c'est une nouvelle diapo ;
- quand le contenu précédent est **toujours là avec des éléments en plus** (puces animées), la photo de la diapo est remplacée par la version la plus complète ;
- un **passage vidéo** (image qui bouge en permanence) n'est jamais photographié ;
- un **retour à une diapo déjà vue** n'est pas photographié une 2ᵉ fois, mais il est noté pour la synchronisation avec la transcription.

## Ligne de commande (facultatif)

```
.venv\Scripts\python -m capture_reunion fenetres
.venv\Scripts\python -m capture_reunion enregistrer Zoom --zone 0,0,0.8,1
.venv\Scripts\python -m capture_reunion enregistrer ecran1 --arret-auto 5
.venv\Scripts\python -m capture_reunion extraire "chemin\video.mp4"
.venv\Scripts\python -m capture_reunion reconstruire "dossier de session" --moteur claude --modele masque.potx
.venv\Scripts\python -m capture_reunion transcrire "dossier de session" --modele medium --traduire --theme neuroendocrines --vocabulaire "RCP, pembrolizumab"
.venv\Scripts\python -m capture_reunion pptx "dossier de session" --modele modele.potx --transcription plaud.txt --debut-plaud 14:29:50
.venv\Scripts\python -m capture_reunion compte-rendu "dossier de session" --transcription plaud.txt --debut-plaud 14:29:50
```

## Cadre légal

Prévenez les participants avant d'enregistrer (RGPD). En RCP, les données de santé imposent un stockage sécurisé (pas de cloud grand public) et une durée de conservation limitée.

## Pour les développeurs

- Code dans `capture_reunion/` : `recorder.py` (capture Windows Graphics Capture + ffmpeg), `audio.py` (WASAPI loopback), `slides.py` (détection), `export.py` (PowerPoint/Word), `transcript.py` (lecture des transcriptions), `transcribe.py` (transcription locale faster-whisper), `summarize.py` (résumé TextRank), `translate.py` (traduction hors ligne CTranslate2 + modèles Argos), `llm.py` (moteurs IA, LM Studio), `claude_code.py` (Claude via Claude Code), `rebuild.py` (reconstruction des diapos : lecture IA/OCR, figures, masque), `gui.py` (PySide6).
- Tests : `python -m pytest`. Ils tournent aussi hors Windows : la capture est simulée par une fausse fenêtre.
